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

import json
import threading
import time
from datetime import datetime

from . import config
from .db import now_iso

# Shared agent code (agent/store_agent, installed with pip install -e ../../agent)
import store_agent.agent as shared_agent
import store_agent.tools as shared_tools
from store_agent.agent import group_items_by_shelf, plan_shelf_route, run_agent, strip_think
from store_agent.config import MODEL_AGENT, MODEL_FAST, MODEL_ULTRA, get_client, has_llm
from store_agent.inventory import load_inventory, release, reserve
from store_agent.order_parser import parse_order, parse_order_offline
from store_agent.order_tracker import basket_item, unpacked_units

RESULT_STATUS = {"done": "completed", "done_with_problems": "completed_with_issues", "failed": "failed"}
FINISHED = ("completed", "completed_with_issues", "failed", "aborted", "interrupted", "rejected",
            "discarded", "expired")


class MissionAborted(Exception):
    """Raised inside the planner thread when the operator pressed STOP."""


class MissionError(Exception):
    """A request the operator made cannot be done now (shown as a message)."""


def mentioned(item, text, aliases=()):
    """True if the order text names this product (id word, display-name word or a catalog alias, singular
    or plural). A parsed item that is not mentioned was inferred by the model and needs the operator's eye."""
    text = text.lower()
    if any(a.lower() in text for a in aliases if len(a) > 2):
        return True
    words = set(item.lower().split("_")) | set(config.product_label(item).lower().split())
    words -= {"can", "pack", "box", "packet", "bottle", "and", "the"}   # container and filler words prove nothing
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
        self.unanswered = None          # (tool, args, tries) sent to the robot, not yet reported to the planner
        self.shift = None               # last shift report {"text", "by", "at"}
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
            self.release_stock(mission_id)
        for mission_id in expired:
            self.event("system", "info", "Unconfirmed mission expired after restart.", mission_id)

    # ---------- events ----------

    def event(self, source, level, text, mission_id=None):
        return self.store.add_event(source, level, text, mission_id=mission_id, simulated=self.simulated)

    # ---------- catalog ----------

    def catalog(self):
        inventory = load_inventory()
        return [{"id": name, "name": config.product_label(name), "shelf": info.get("shelf"),
                 "shelf_name": config.station_label(info.get("shelf")), "level": info.get("level"),
                 "stock": info.get("stock", 0), "price": info.get("price", 0)} for name, info in inventory.items()]

    def layout(self):
        """{station: {level: [{"id", "name", "stock"}]}} in catalog order: what the map draws on each table."""
        out = {}
        for name, info in load_inventory().items():
            shelf = out.setdefault(info.get("shelf"), {})
            shelf.setdefault(info.get("level") or "shelf", []).append(
                {"id": name, "name": config.product_label(name), "stock": info.get("stock", 0)})
        return out

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
                  "shelf": inventory.get(e["item"], {}).get("shelf"), "inferred": not mentioned(e["item"], text, inventory.get(e["item"], {}).get("aliases", ()))}
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
        if (tel.get("health") or {}).get("arm_ready") is False:
            return False, ("The arm is not ready: the GR00T policy worker on the Jetson is not running "
                           "or is still loading its model. Missions wait until it is ready.")
        if status.get("needs_operator"):
            return False, f"A person must check the arm first: {status['needs_operator']}. Then press Resume."
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
            # Reserve the stock now (all or nothing), so no other order can take these units
            ok, _ = reserve([{"item": i["item"], "qty": i["qty"]} for i in mission["items"]])
            if not ok:
                raise MissionError("Stock changed since the order was checked. Submit it again.")
            self.store.update_mission(mission_id, stock_reserved=1)

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

    def release_stock(self, mission_id):
        """Give back the reserved stock of every unit the mission did not put in the basket (once)."""
        if not self.store.take_reservation(mission_id):
            return
        mission = self.store.get_mission(mission_id)
        back = unpacked_units(mission["items"], mission.get("step_log") or [])
        if back:
            release(back)
            self.event("system", "info", "Back to stock (not packed): " + ", ".join(
                f"{qty}× {config.product_label(item)}" for item, qty in back.items()), mission_id)

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
            self.release_stock(mission_id)
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
            threading.Thread(target=self.summarize, args=(mission_id,), name=f"summary-{mission_id}",
                             daemon=True).start()

    # ---------- 4. mission report (Nemotron Ultra) ----------

    @staticmethod
    def mission_facts(m):
        """What happened, from the mission record and its step log (no guessing)."""
        log = m.get("step_log") or []
        picks = [e for e in log if e.get("tool") == "pick_item" and not (e.get("result") or {}).get("refused")]
        # one pick command can be several arm attempts: the code retries a missed grasp ("tries")
        arm_attempts = sum((e.get("result") or {}).get("tries") or 1 for e in picks)
        in_basket = sum(1 for e in picks if basket_item(e))
        navs = [e for e in log if e.get("tool") == "navigate_to"]
        failed_navs = [e for e in navs if not (e.get("result") or {}).get("success")]
        refused = [e for e in log if (e.get("result") or {}).get("refused")]
        seconds = None
        if m.get("confirmed_at") and m.get("finished_at"):
            seconds = round((datetime.fromisoformat(m["finished_at"]) -
                             datetime.fromisoformat(m["confirmed_at"])).total_seconds())
        return {
            "order": m["text"],
            "items": ", ".join(f"{i['qty']}x {i['name']}" for i in m["items"]),
            "result": m["status"].replace("_", " "),
            "delivered": f"{m['packed']} of {m['total']} items",
            "duration_seconds": seconds,
            "pick_attempts": arm_attempts,
            "failed_picks": arm_attempts - in_basket,
            "drives": len(navs),
            "failed_drives": len(failed_navs),
            "commands_blocked_by_safety_checks": len(refused),
            "problems": m.get("problems") or [],
            "robot": "simulated robot" if m["mode"] == "mock" else "real robot",
        }

    @staticmethod
    def rule_summary(f):
        text = f"Mission {f['result']}: {f['delivered']} delivered"
        if f["duration_seconds"] is not None:
            text += f" in {f['duration_seconds']} s"
        text += f" ({f['pick_attempts']} pick attempts, {f['failed_picks']} failed"
        text += f", {f['commands_blocked_by_safety_checks']} commands blocked)." \
            if f["commands_blocked_by_safety_checks"] else ")."
        if f["problems"]:
            text += " Problems: " + "; ".join(f["problems"])
        return text

    def summarize(self, mission_id):
        """Short report for the operator, written by Nemotron Ultra (rule-based without a key)."""
        m = self.store.get_mission(mission_id)
        if not m or m["status"] not in FINISHED:
            return
        facts = self.mission_facts(m)
        summary, by = None, None
        if has_llm():
            prompt = ("You are the operations assistant of a dark-store picking robot. Write a short mission report "
                      "for the store operator in 2 or 3 plain sentences: what was ordered, what was delivered, "
                      "how long it took, and any problem or retry worth noticing. Use only these facts, no "
                      "markdown, no lists, no greeting.\n\nFacts: " + json.dumps(facts))
            try:
                reply = get_client().with_options(timeout=90.0).chat.completions.create(
                    model=MODEL_ULTRA, messages=[{"role": "user", "content": prompt}], max_tokens=2000)
                summary = strip_think(reply.choices[0].message.content or "")
                by = f"NVIDIA Nemotron ({model_name(MODEL_ULTRA)})"
            except Exception as e:
                self.event("ai", "warning", f"Nemotron Ultra could not write the mission report "
                           f"({e.__class__.__name__}). Used a rule-based summary instead.", mission_id)
        if not summary:
            summary, by = self.rule_summary(facts), "rule-based summary (no Nemotron)"
        self.store.update_mission(mission_id, summary=summary, summary_by=by)
        self.event("ai", "success", f"Mission report by {by}: {summary}", mission_id)

    def before_command(self, tool, args):
        """Called by RobotProxy just before a robot command."""
        if self.abort.is_set():
            raise MissionAborted()
        mission_id = self.current_id
        with self.lock:
            self.action = {"tool": tool, "args": args, "started": time.monotonic()}
        # The same command again before the planner saw an answer = the code's automatic pick retry
        # (store_agent.tools.pick_with_retries), not a new planner decision
        if self.unanswered and self.unanswered[:2] == (tool, args):
            tries = self.unanswered[2] + 1
            self.unanswered = (tool, args, tries)
            self.event("robot", "warning", f"Grasp missed: retrying the {config.product_label(args.get('item'))} "
                       f"pick automatically (try {tries})", mission_id)
            return
        self.unanswered = (tool, args, 1)
        # The planner chose this command: credit it to the planner, the robot's answer follows in on_step
        who = "Nemotron Super decided" if has_llm() else "Autopilot decided"
        if tool == "navigate_to":
            station = args["station"]
            if station == "customer":
                text = "deliver the basket to the packing area"
            elif station == "home":
                text = "return to the home position"
            else:
                text = f"drive to {config.station_label(station)}"
            self.event("ai", "info", f"{who}: {text}", mission_id)
        elif tool == "pick_item":
            self.event("ai", "info", f"{who}: pick {config.product_label(args['item'])} with the arm (GR00T)",
                       mission_id)

    def after_command(self):
        with self.lock:
            self.action = None
        if self.abort.is_set():
            raise MissionAborted()

    def on_step(self, mission_id, entry):
        """Called by the agent after every tool call with the robot's answer."""
        tool, args = entry.get("tool"), entry.get("input") or {}
        result = entry.get("result") or {}
        self.unanswered = None
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

    def picking(self):
        """The product the arm is picking right now, or None."""
        action = self.action
        return action["args"].get("item") if action and action["tool"] == "pick_item" else None

    # ---------- shift report for the owner (Nemotron Ultra) ----------

    def shift_facts(self):
        """Today's missions, robot performance and stock, from the database (no guessing)."""
        today = datetime.now().date().isoformat()
        missions = [m for m in self.store.list_missions(limit=500) if (m.get("created_at") or "").startswith(today)]
        ran = [m for m in missions if m.get("confirmed_at") and m["status"] in FINISHED]
        facts = [self.mission_facts(m) for m in ran]
        times = [f["duration_seconds"] for f, m in zip(facts, ran)
                 if f["duration_seconds"] is not None and m["status"].startswith("completed")]
        ordered = {}
        for m in ran:
            for i in m["items"]:
                ordered[i["name"]] = ordered.get(i["name"], 0) + i["qty"]
        inventory = load_inventory()
        stock = {config.product_label(k): v.get("stock", 0) for k, v in inventory.items()}
        count = lambda status: sum(m["status"] == status for m in missions)  # noqa: E731
        return {
            "date": today,
            "missions_run": len(ran),
            "completed": count("completed"),
            "completed_with_issues": count("completed_with_issues"),
            "failed": count("failed"),
            "stopped_by_operator": count("aborted"),
            "orders_refused_before_start": count("rejected"),
            "items_delivered": sum(m["packed"] for m in ran),
            "items_ordered": sum(m["total"] for m in ran),
            "pick_attempts": sum(f["pick_attempts"] for f in facts),
            "failed_picks": sum(f["failed_picks"] for f in facts),
            "failed_drives": sum(f["failed_drives"] for f in facts),
            "commands_blocked_by_safety_checks": sum(f["commands_blocked_by_safety_checks"] for f in facts),
            "average_mission_seconds": round(sum(times) / len(times)) if times else None,
            "products_ordered": ordered,
            "problems": sorted({p for m in missions for p in (m.get("problems") or [])})[:8],
            "stock": stock,
            "low_stock_3_or_less": [name for name, qty in stock.items() if qty <= 3],
            "robot": "simulated robot" if self.simulated else "real robot",
        }

    @staticmethod
    def rule_shift_report(f):
        text = (f"Today: {f['missions_run']} missions run, {f['completed']} completed cleanly, "
                f"{f['completed_with_issues']} with issues, {f['failed']} failed, "
                f"{f['orders_refused_before_start']} orders refused before start. "
                f"{f['items_delivered']} of {f['items_ordered']} items delivered")
        if f["pick_attempts"]:
            ok = f["pick_attempts"] - f["failed_picks"]
            text += f"; {ok} of {f['pick_attempts']} picks worked"
        text += "."
        if f["average_mission_seconds"] is not None:
            text += f" Average mission time {f['average_mission_seconds']} s."
        text += " Restock: " + (", ".join(f["low_stock_3_or_less"]) or "nothing urgent") + "."
        if f["problems"]:
            text += " Problems: " + "; ".join(f["problems"][:3]) + "."
        return text

    def shift_report(self):
        """Owner's shift report: Nemotron Ultra analyses today's missions and stock (rule-based without a key)."""
        facts = self.shift_facts()
        text, by = None, None
        if has_llm():
            prompt = ("You are the operations assistant of a dark store that uses a picking robot. Write a short "
                      "shift report for the store owner in 4 to 6 plain sentences: how the shift went, how well the "
                      "robot picked and how long missions took, any problems worth noticing, what to restock, and "
                      "one practical suggestion. Use only these facts. Recommend restocking only the products in "
                      "low_stock_3_or_less; if that list is empty, say no restocking is needed. "
                      "No markdown, no lists, no greeting.\n\n"
                      "Facts: " + json.dumps(facts))
            try:
                reply = get_client().with_options(timeout=90.0).chat.completions.create(
                    model=MODEL_ULTRA, messages=[{"role": "user", "content": prompt}], max_tokens=2000)
                text = strip_think(reply.choices[0].message.content or "")
                by = f"NVIDIA Nemotron ({model_name(MODEL_ULTRA)})"
            except Exception as e:
                self.event("ai", "warning", f"Nemotron Ultra could not write the shift report "
                           f"({e.__class__.__name__}). Used a rule-based summary instead.")
        if not text:
            text, by = self.rule_shift_report(facts), "rule-based summary (no Nemotron)"
        self.shift = {"text": text, "by": by, "at": now_iso(), "missions": facts["missions_run"]}
        self.event("ai", "success", f"Shift report by {by}: {text}")
        return self.shift
