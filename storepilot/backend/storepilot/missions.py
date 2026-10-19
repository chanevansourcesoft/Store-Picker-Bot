"""
Missions: order text -> parsed items -> validated plan -> operator confirmation -> run.

Planning reuses the shared agent in agent/store_agent (NVIDIA Nemotron on Nebius Token
Factory, or the offline autopilot without a key). Its OrderTracker guards refuse
wrong commands before they reach the robot.

Safety rules enforced here:
- Nothing runs without an explicit confirm() call from the operator.
- In live mode, confirm() also needs STOREPILOT_ENABLE_MOTION=1 and the
  operator's acknowledgement that the area is clear.
- The planner can only call high-level robot commands, and every one of them
  goes through RobotProxy, which stops the mission as soon as STOP is pressed.
- After a restart, nothing resumes by itself (see Store.close_unfinished).
"""

import threading
import time
from datetime import datetime

from . import config
from .db import now_iso

# Shared agent code (agent/store_agent, installed with pip install -e ../../agent)
import store_agent.agent as shared_agent
import store_agent.tools as shared_tools
from store_agent.agent import group_items_by_shelf, plan_shelf_route, run_agent
from store_agent.config import MODEL_AGENT, MODEL_FAST, has_llm
from store_agent.inventory import load_inventory
from store_agent.order_parser import parse_order, parse_order_offline
from store_agent.order_tracker import basket_item

RESULT_STATUS = {"done": "completed", "done_with_problems": "completed_with_issues", "failed": "failed"}
FINISHED = ("completed", "completed_with_issues", "failed", "aborted", "interrupted", "rejected",
            "discarded", "expired")


class MissionAborted(Exception):
    """Raised inside the planner thread when the operator pressed STOP."""


class MissionError(Exception):
    """A request the operator made cannot be done now (shown as a message)."""


def mentioned(item, text):
    """True if the order text names this product (id word or display-name word, singular or plural).
    A parsed item that is not mentioned was inferred by the model and needs the operator's eye."""
    words = set(item.lower().split("_")) | set(config.product_label(item).lower().split())
    words -= {"can", "pack", "box", "packet", "bottle", "and", "the"}   # container and filler words prove nothing
    text = text.lower()
    return any(w in text or w + "s" in text or w.rstrip("s") in text.split() for w in words if len(w) > 2)


def model_name(model):
    return model.split("/")[-1]


class RobotProxy:
    """What the shared agent sees as `robot_client`. It only exposes the high-level
    commands the planner may use (navigate, pick), plus status for its tracker."""

    def __init__(self, manager):
        self.m = manager

    def status(self):
        return self.m.adapter.status()

    def health(self):
        return {"status": "ok"}

    def _command(self, tool, args, call):
        self.m.before_command(tool, args)
        try:
            return call()
        finally:
            self.m.after_command()

    def navigate(self, station):
        return self._command("navigate_to", {"station": station}, lambda: self.m.adapter.navigate(station))

    def pick(self, item, shelf):
        return self._command("pick_item", {"item": item}, lambda: self.m.adapter.pick(item, shelf))


class MissionManager:
    def __init__(self, store, adapter, telemetry, ai_status):
        self.store = store
        self.adapter = adapter
        self.telemetry = telemetry
        self.ai = ai_status
        self.simulated = adapter.source == "simulated"
        self.lock = threading.Lock()
        self.current_id = None          # running mission
        self.action = None              # {"tool", "args", "started"} while a robot command runs
        self.abort = threading.Event()
        self.thread = None
        self.last_finished = None       # (mission_id, status, monotonic time)
        self.stop_pending = False       # STOP sent, not yet confirmed by telemetry

        # The shared agent talks to "robot_client": route it through our proxy.
        proxy = RobotProxy(self)
        shared_agent.robot_client = proxy
        shared_tools.robot_client = proxy

        interrupted, expired = store.close_unfinished()
        for mission_id in interrupted:
            self.event("system", "warning", "Mission was running when the dashboard restarted. "
                       "Marked interrupted; it will NOT resume by itself.", mission_id)
        for mission_id in expired:
            self.event("system", "info", "Unconfirmed mission expired after restart.", mission_id)

    # ---------- events ----------

    def event(self, source, level, text, mission_id=None):
        return self.store.add_event(source, level, text, mission_id=mission_id, simulated=self.simulated)

    # ---------- catalog ----------

    def catalog(self):
        inventory = load_inventory()
        return [{"id": name, "name": config.product_label(name), "shelf": info.get("shelf"),
                 "shelf_name": config.station_label(info.get("shelf")), "stock": info.get("stock", 0),
                 "price": info.get("price", 0)} for name, info in inventory.items()]

    # ---------- 1. order -> draft ----------

    def submit_order(self, text):
        text = (text or "").strip()
        if not text:
            raise MissionError("Type or build an order first.")
        if len(text) > 500:
            raise MissionError("Order text is too long (500 characters max).")
        self.event("operator", "info", f"Order received: “{text}”")

        inventory = load_inventory()
        if has_llm():
            try:
                parsed = parse_order(text)
                parser = f"NVIDIA Nemotron ({model_name(MODEL_FAST)})"
                self.ai.record(True)
            except Exception as e:
                self.ai.record(False, str(e))
                parsed = parse_order_offline(text, inventory)
                parser = "offline keyword parser (Nemotron unavailable)"
                self.event("ai", "warning", f"Nemotron could not parse the order ({e.__class__.__name__}). "
                           "Used the offline keyword parser instead.")
        else:
            parsed = parse_order_offline(text, inventory)
            parser = "offline keyword parser (no API key)"

        items = [{"item": e["item"], "name": config.product_label(e["item"]), "qty": e["qty"],
                  "shelf": inventory.get(e["item"], {}).get("shelf"), "inferred": not mentioned(e["item"], text)}
                 for e in parsed.get("items", [])]
        unknown = list(dict.fromkeys(parsed.get("unknown", [])))
        route = [shelf for shelf, _ in plan_shelf_route(group_items_by_shelf(parsed.get("items", []), inventory))]
        problems = self.validate(items, route, inventory)
        total = sum(i["qty"] for i in items)
        status = "rejected" if problems else "awaiting_confirmation"

        mission_id = self.store.create_mission(
            text=text, mode=config.MODE, status=status, items=items, unknown=unknown,
            route=route, parser=parser, total=total, problems=problems)

        summary = ", ".join(f"{i['qty']}× {i['name']}" for i in items) or "no stocked products"
        self.event("ai", "info", f"Order parsed by {parser}: {summary}", mission_id)
        if unknown:
            self.event("ai", "warning", "Not stocked in this store: " + ", ".join(unknown), mission_id)
        inferred = [i["name"] for i in items if i["inferred"]]
        if inferred:
            self.event("ai", "warning", "Not named in the order, please check before starting: "
                       + ", ".join(inferred), mission_id)
        if problems:
            self.event("system", "error", "Mission plan rejected: " + " ".join(problems), mission_id)
        else:
            path = " → ".join(config.station_label(s) for s in route + ["customer", "home"])
            self.event("system", "success", f"Mission plan validated: {path}. Waiting for operator confirmation.",
                       mission_id)
        return self.store.get_mission(mission_id)

    def validate(self, items, route, inventory):
        """Problems that block the mission (empty list = OK)."""
        problems = []
        if not items:
            problems.append("No products from this store were found in the order.")
        for entry in items:
            stock = inventory.get(entry["item"], {}).get("stock", 0)
            if entry["qty"] > stock:
                problems.append(f"Only {stock} {entry['name']} in stock (asked for {entry['qty']}).")
        if sum(i["qty"] for i in items) > config.MAX_UNITS_PER_MISSION:
            problems.append(f"At most {config.MAX_UNITS_PER_MISSION} items per mission.")
        known = (self.telemetry.snapshot().get("map") or {}).get("stations")
        if known:
            missing = [s for s in route + ["customer", "home"] if s not in known]
            if missing:
                problems.append("The robot map has no station for: " + ", ".join(missing) + ".")
        return problems

    # ---------- 2. operator confirms ----------

    def readiness(self):
        """(ok, reason) for starting a mission right now."""
        tel = self.telemetry.snapshot()
        status = tel.get("status") or {}
        if self.current_id is not None:
            return False, "Another mission is running."
        if not tel["connected"]:
            return False, "Robot is disconnected."
        if tel["age"] is None or tel["age"] > config.STALE_AFTER_SEC:
            return False, "Robot telemetry is stale."
        if status.get("stopped"):
            return False, "Robot is stopped. Resume it first."
        if status.get("busy"):
            return False, "Robot reports it is busy."
        if config.MODE == "live" and not config.ENABLE_MOTION:
            return False, "Physical motion is disabled (STOREPILOT_ENABLE_MOTION=0)."
        if config.MODE == "live" and not config.MANIPULATION_VERIFIED:
            return False, ("The GR00T pick (pick and place in basket) is not verified on the Jetson yet "
                           "(STOREPILOT_MANIPULATION_VERIFIED=0).")
        return True, ""

    def confirm(self, mission_id, area_clear=False):
        with self.lock:
            mission = self.store.get_mission(mission_id)
            if not mission:
                raise MissionError("Mission not found.")
            if mission["status"] != "awaiting_confirmation":
                raise MissionError(f"Mission is {mission['status']}, it cannot be started.")
            ok, reason = self.readiness()
            if not ok:
                raise MissionError(reason)
            if config.MODE == "live" and not area_clear:
                raise MissionError("Confirm that the robot's path is clear before starting a real mission.")
            if load_inventory() and self.validate(mission["items"], mission["route"], load_inventory()):
                raise MissionError("Stock changed since the order was checked. Submit it again.")

            planner = f"NVIDIA Nemotron ({model_name(MODEL_AGENT)})" if has_llm() else "offline autopilot (no API key)"
            self.store.update_mission(mission_id, status="running", confirmed_at=now_iso(), planner=planner)
            self.current_id = mission_id
            self.abort.clear()
            self.event("operator", "info", "Mission confirmed by operator" +
                       (" (path reported clear)" if config.MODE == "live" else ""), mission_id)
            self.event("ai", "info", f"Planner started: {planner}", mission_id)
            self.thread = threading.Thread(target=self._run, args=(mission_id,), name=f"mission-{mission_id}",
                                           daemon=True)
            self.thread.start()
        return self.store.get_mission(mission_id)

    def discard(self, mission_id):
        mission = self.store.get_mission(mission_id)
        if not mission or mission["status"] != "awaiting_confirmation":
            raise MissionError("Only a mission waiting for confirmation can be discarded.")
        self.store.update_mission(mission_id, status="discarded", finished_at=now_iso())
        self.event("operator", "info", "Mission discarded by operator", mission_id)

    # ---------- 3. run (planner thread) ----------

    def _run(self, mission_id):
        mission = self.store.get_mission(mission_id)
        status, problems, bill, log = "failed", [], {}, []
        try:
            bill, log = run_agent({"items": [{"item": i["item"], "qty": i["qty"]} for i in mission["items"]],
                                   "unknown": []}, on_step=lambda entry: self.on_step(mission_id, entry))
            status = RESULT_STATUS.get(bill.get("status"), "failed")
            problems = bill.get("problems", [])
            if has_llm():
                self.ai.record(True)
        except MissionAborted:
            status, problems = "aborted", ["Stopped by the operator."]
        except Exception as e:
            problems = [f"Planner error: {e}"]
            if has_llm():
                self.ai.record(False, str(e))
        finally:
            packed = self.store.get_mission(mission_id)["packed"]
            self.store.update_mission(mission_id, status=status, finished_at=now_iso(), bill=bill,
                                      problems=problems, packed=packed)
            with self.lock:
                self.current_id, self.action = None, None
                self.last_finished = (mission_id, status, time.monotonic())
            total = mission["total"]
            text = {
                "completed": f"Mission completed: {packed}/{total} items delivered to the packing area",
                "completed_with_issues": f"Mission finished with issues: {packed}/{total} items delivered",
                "failed": "Mission failed",
                "aborted": f"Mission stopped by operator after {packed}/{total} items",
            }[status]
            level = {"completed": "success", "completed_with_issues": "warning"}.get(status, "error")
            detail = (" · " + "; ".join(problems)) if problems and status != "completed" else ""
            self.event("system", level, text + detail, mission_id)

    def before_command(self, tool, args):
        """Called by RobotProxy just before a robot command."""
        if self.abort.is_set():
            raise MissionAborted()
        mission_id = self.current_id
        with self.lock:
            self.action = {"tool": tool, "args": args, "started": time.monotonic()}
        if tool == "navigate_to":
            station = args["station"]
            if station == "customer":
                text = "Delivering basket to the packing area"
            elif station == "home":
                text = "Returning to home position"
            else:
                text = f"Navigating to {config.station_label(station)}"
            self.event("robot", "info", text, mission_id)
        elif tool == "pick_item":
            self.event("robot", "info", f"Pick requested: {config.product_label(args['item'])} "
                       "(one GR00T run: pick and place in basket)", mission_id)

    def after_command(self):
        with self.lock:
            self.action = None
        if self.abort.is_set():
            raise MissionAborted()

    def on_step(self, mission_id, entry):
        """Called by the agent after every tool call with the robot's answer."""
        tool, args = entry.get("tool"), entry.get("input") or {}
        result = entry.get("result") or {}
        ok = bool(result.get("success"))
        message = result.get("message") or ""
        mission = self.store.get_mission(mission_id)
        log = (mission.get("step_log") or []) + [entry]
        updates = {"step_log": log}

        if result.get("refused"):
            self.event("ai", "warning", f"Planner command blocked by safety check: {message}", mission_id)
        elif tool == "navigate_to":
            station = config.station_label(args.get("station"))
            self.event("robot", "success" if ok else "warning",
                       f"Arrived at {station}" if ok else f"Navigation to {station} failed: {message}", mission_id)
        elif tool == "pick_item":
            name = config.product_label(args.get("item"))
            if basket_item(entry):
                packed = min(mission["packed"] + 1, mission["total"])
                updates["packed"] = packed
                instruction = result.get("instruction")
                self.event("robot", "success", f"{name} placed in basket ({packed} of {mission['total']} picked)"
                           + (f" · GR00T instruction “{instruction}”" if instruction else ""), mission_id)
            elif ok:
                self.event("robot", "warning", f"Robot picked {name} but did not report it in the basket",
                           mission_id)
            else:
                self.event("robot", "warning", f"Pick of {name} failed: {message}", mission_id)
        self.store.update_mission(mission_id, **updates)

    # ---------- STOP / resume ----------

    def stop(self):
        """Stop the mission and send STOP to the robot. Returns the robot's answer.
        The dashboard says 'stopped' only when telemetry confirms it."""
        self.abort.set()
        mission_id = self.current_id
        rosbridge = getattr(self.adapter, "link_kind", "") == "rosbridge"
        if config.MODE == "live" and not config.JETSON_STOP_CONFIRMED and not rosbridge:
            self.event("operator", "error", "STOP pressed: mission planner halted, but the Jetson stop endpoint is "
                       "not confirmed (JETSON_STOP_CONFIRMED=0). Use the hardware E-stop.", mission_id)
            return {"success": False, "message": "Jetson stop not connected. Use the hardware emergency stop.",
                    "planner_halted": True}
        self.event("operator", "warning", "STOP requested by operator" +
                   (" (cancel goal + pause Nav2; physical stop not yet verified, keep the hardware E-stop at hand)"
                    if rosbridge and not config.JETSON_STOP_CONFIRMED else ""), mission_id)
        result = self.adapter.stop()
        if result.get("success"):
            self.stop_pending = True
            self.event("robot", "warning", f"Robot acknowledged STOP: {result.get('message', '')}", mission_id)
        else:
            self.event("robot", "error", f"STOP NOT acknowledged: {result.get('message', 'no answer')}. "
                       "Use the hardware emergency stop.", mission_id)
        return result

    def resume(self):
        """Clear the robot's stop state. Never restarts a mission."""
        if config.MODE == "live" and not config.JETSON_STOP_CONFIRMED and \
                getattr(self.adapter, "link_kind", "") != "rosbridge":
            raise MissionError("Jetson stop/resume endpoint is not confirmed.")
        result = self.adapter.resume()
        if result.get("success"):
            self.stop_pending = False
            with self.lock:
                if self.last_finished and self.last_finished[1] == "aborted":
                    self.last_finished = None   # back to Idle, not "Paused"
            self.event("operator", "info", "Robot resumed by operator (no mission restarted)")
        else:
            self.event("robot", "error", f"Resume failed: {result.get('message', 'no answer')}")
        return result

    # ---------- state for the dashboard ----------

    def eta(self, mission):
        """Estimate from past completed missions in the same mode (needs 3+). None if not reliable."""
        samples = []
        for m in self.store.finished_missions(config.MODE, limit=20):
            try:
                secs = (datetime.fromisoformat(m["finished_at"]) - datetime.fromisoformat(m["confirmed_at"])).total_seconds()
            except (TypeError, ValueError):
                continue
            if m["total"]:
                samples.append(secs / m["total"])
        if len(samples) < 3 or not mission or not mission.get("total"):
            return None
        estimate = sum(samples) / len(samples) * mission["total"]
        result = {"total_sec": round(estimate), "samples": len(samples)}
        if mission.get("status") == "running" and mission.get("confirmed_at"):
            elapsed = (datetime.now() - datetime.fromisoformat(mission["confirmed_at"])).total_seconds()
            result["remaining_sec"] = round(estimate - elapsed) if estimate > elapsed else None
        return result

    def robot_state(self, tel):
        """(state, detail) — idle / planning / navigating / picking / returning / completed / paused / error / offline."""
        status = tel.get("status") or {}
        if not tel["connected"]:
            return "offline", "No telemetry from the robot"
        if status.get("stopped"):
            self.stop_pending = False
            return "paused", "Robot stopped — press Resume when the area is safe"
        action = self.action
        if self.current_id is not None:
            if self.abort.is_set():
                return "paused", "Stopping…"
            if action is None:
                return "planning", "Planner is deciding the next step"
            tool, args = action["tool"], action["args"]
            if tool == "navigate_to":
                station = args["station"]
                if station in ("customer", "home"):
                    return "returning", f"To {config.station_label(station)}"
                return "navigating", f"To {config.station_label(station)}"
            return "picking", f"Picking {config.product_label(args.get('item'))} into the basket"
        if status.get("nav_state") == "navigating":
            goal = status.get("goal") or {}
            where = config.station_label(status.get("goal_station")) if status.get("goal_station") else \
                f"point ({goal.get('x', 0):.2f}, {goal.get('y', 0):.2f})"
            return "navigating", f"Driving to {where}"
        if "scan_age" in status and (status["scan_age"] is None or status["scan_age"] > 2.0):
            return "error", "No LiDAR scan: check the LiDAR (power / battery)"
        if status.get("odom_bad"):
            return "error", f"Odometry broken ({status['odom_bad']}): restart navigation"
        if "nav2_active" in status and status.get("nav2_active") is None:
            return "idle", "Nav2 is not running on the Jetson: start navigation.launch.py"
        if status.get("nav2_active") is False:
            return "idle", "Nav2 is paused: press Start Nav2"
        if "localized" in status and not status.get("localized"):
            return "idle", "Not localized: set the robot position on the map"
        if self.last_finished and time.monotonic() - self.last_finished[2] < 30:
            _, status_done, _ = self.last_finished
            if status_done == "completed":
                return "completed", "Mission completed"
            if status_done == "completed_with_issues":
                return "completed", "Completed with issues"
            if status_done == "aborted":
                return "paused", "Mission stopped by operator"
            return "error", "Mission failed"
        if status.get("last_error"):
            return "idle", f"Last robot error: {status['last_error']}"
        if "localized" in status:   # live over rosbridge: driving only
            return "idle", "Ready to drive"
        return "idle", "Ready for a mission"

    def target(self):
        action = self.action
        if action and action["tool"] == "navigate_to":
            return action["args"]["station"]
        return None
