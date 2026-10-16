"""
Drive routes over rosbridge: one goal or many waypoints, sent to Nav2 one after another.

The Pi only sends Nav2 goals (/goal_pose), cancels them and sets AMCL's pose (/initialpose).
On top of Nav2 it adds three things, all from the Pi:

1. Arrival: Nav2 normally reports "succeeded". If the robot is already inside the goal
   tolerance but keeps wiggling for ARRIVE_HOLD_SEC, the Pi cancels the goal so it stops.
   After every stop the Pi waits until the robot is really standing still.
2. Auto-align at stops: when the robot stands still, fit the LiDAR to the map and send the
   refined pose to AMCL (option `auto_align`, also every IDLE_ALIGN_SEC when quality is low).
3. Localization guard while driving (option `guard`): if the share of LiDAR points on map walls
   falls below GUARD_QUALITY twice in a row, cancel the goal, wait for standstill, auto-align and
   re-send the same waypoint (at most MAX_REALIGNS times per waypoint).
"""

import json
import math
import threading
import time
from pathlib import Path

from .config import station_label

XY_TOL = 0.15            # m, same as the robot's Nav2 goal checker
YAW_TOL = 0.20           # rad
ARRIVE_HOLD_SEC = 3.0    # inside tolerance this long while Nav2 still drives -> cancel
STILL_SPEED = 0.02       # m/s
STILL_SEC = 0.6
WAYPOINT_TIMEOUT = 240   # s
GUARD_QUALITY = 0.35
GOOD_QUALITY = 0.55
MAX_REALIGNS = 3
IDLE_ALIGN_SEC = 20.0


def wrap(a):
    return math.atan2(math.sin(a), math.cos(a))


class RouteAborted(Exception):
    pass


class DriveController:
    def __init__(self, robot, stations, event, settings_path):
        self.robot = robot                    # RosbridgeRobot
        self.stations = stations
        self.event = event                    # event(source, level, text)
        self.settings_path = Path(settings_path)
        self.settings = {"auto_align": True, "guard": True}
        try:
            self.settings.update(json.loads(self.settings_path.read_text()))
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        self.lock = threading.Lock()
        self.route = None                     # dict while a route runs or just finished
        self.abort = threading.Event()
        self.thread = None
        self.last_align = 0.0
        threading.Thread(target=self._idle_loop, name="idle-align", daemon=True).start()

    # ---------- settings ----------

    def set_settings(self, **values):
        for key in ("auto_align", "guard"):
            if key in values and values[key] is not None:
                self.settings[key] = bool(values[key])
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(self.settings))
        return dict(self.settings)

    # ---------- route ----------

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def resolve(self, waypoints):
        """[{x, y, theta} | {station}] -> [{x, y, theta, label}] for the current map."""
        saved = self.stations.get(self.robot.map_name)
        out = []
        for i, w in enumerate(waypoints, 1):
            if w.get("station"):
                s = saved.get(w["station"])
                if not s:
                    raise ValueError(f"Station {w['station']} is not saved for map {self.robot.map_name}.")
                out.append({"x": s["x"], "y": s["y"], "theta": s["theta"], "label": station_label(w["station"]),
                            "station": w["station"]})
            else:
                out.append({"x": float(w["x"]), "y": float(w["y"]), "theta": float(w.get("theta", 0.0)),
                            "label": w.get("label") or f"point {i}", "station": None})
        if not out:
            raise ValueError("The route has no waypoints.")
        if len(out) > 20:
            raise ValueError("At most 20 waypoints per route.")
        return out

    def start(self, waypoints):
        if self.busy():
            raise ValueError("A route is already running: cancel it first.")
        points = self.resolve(waypoints)
        self.abort.clear()
        with self.lock:
            self.route = {"waypoints": points, "index": 0, "state": "starting", "message": "", "realigns": 0,
                          "started_at": time.time(), "finished": False}
        self.thread = threading.Thread(target=self._run, name="drive-route", daemon=True)
        self.thread.start()
        return self.snapshot()

    def cancel(self, reason="Route cancelled by operator"):
        """Stop following the route (the STOP button pauses Nav2 separately)."""
        if not self.busy():
            return False
        self.abort.set()
        self.robot.cancel_goal()
        self._set(state="canceled", message=reason)
        return True

    def snapshot(self):
        with self.lock:
            r = None if self.route is None else {k: (list(v) if isinstance(v, list) else v) for k, v in self.route.items()}
        return {"route": r, "settings": dict(self.settings), "busy": self.busy()}

    def _set(self, **fields):
        with self.lock:
            if self.route is not None:
                self.route.update(fields)

    # ---------- helpers ----------

    def _check_abort(self):
        if self.abort.is_set() or self.robot.stopped:
            raise RouteAborted()

    def _speed(self):
        s = self.robot.odom_speed
        return 0.0 if s is None else s

    def _wait_still(self, timeout=6.0):
        still_since, end = None, time.monotonic() + timeout
        while time.monotonic() < end:
            self._check_abort()
            if self._speed() < STILL_SPEED:
                still_since = still_since or time.monotonic()
                if time.monotonic() - still_since >= STILL_SEC:
                    return True
            else:
                still_since = None
            time.sleep(0.1)
        return False

    def _align(self, why):
        before = self.robot.localization_quality()
        result = self.robot.auto_align()
        self.last_align = time.monotonic()
        if result.get("success"):
            time.sleep(1.5)                       # AMCL nomotion updates settle
            self.robot.quality_cache = (None, 0.0)
            after = self.robot.localization_quality()
            self.event("robot", "info", f"Auto-align ({why}): {result['message'].split('(')[-1].rstrip(')')}"
                       if "auto-aligned" in result["message"] else f"Auto-align ({why}): no better fit found")
            return after
        self.event("robot", "warning", f"Auto-align ({why}) failed: {result.get('message')}")
        return before

    # ---------- executor ----------

    def _run(self):
        route = self.route
        total = len(route["waypoints"])
        try:
            for i, wp in enumerate(route["waypoints"]):
                self._set(index=i, realigns=0)
                self._drive_to(wp, i, total)
                if self.settings["auto_align"] and self.robot.localized():
                    self._set(state="aligning", message=f"Aligning at {wp['label']}")
                    self._align(f"at {wp['label']}")
            self._set(state="done", message=f"Route finished: {total} stop{'s' if total > 1 else ''} reached",
                      finished=True)
            self.event("robot", "success", f"Route finished: reached all {total} waypoint{'s' if total > 1 else ''}")
        except RouteAborted:
            self._set(state="canceled", finished=True,
                      message="Route stopped" + (" (STOP pressed)" if self.robot.stopped else ""))
        except Exception as e:     # never leave a goal running after an error
            self.robot.cancel_goal()
            self._set(state="failed", message=str(e), finished=True)
            self.event("robot", "error", f"Route stopped: {e}")

    def _drive_to(self, wp, i, total):
        name = f"{wp['label']} ({i + 1}/{total})"
        realigns = 0
        while True:
            self._check_abort()
            result = self.robot.send_goal(wp["x"], wp["y"], wp["theta"], station=wp.get("station"))
            if not result.get("success"):
                raise RuntimeError(f"Goal {name} refused: {result.get('message')}")
            self._set(state="driving", message=f"Driving to {name}")
            self.event("robot", "info", f"Driving to {name}")
            outcome = self._watch_goal(wp)
            if outcome == "arrived":
                self._wait_still()
                self.event("robot", "success", f"Arrived at {name}")
                return
            if outcome == "lost":
                realigns += 1
                self._set(realigns=realigns)
                if realigns > MAX_REALIGNS:
                    raise RuntimeError(f"Localization keeps dropping on the way to {name}")
                self._set(state="aligning", message=f"Localization weak: stopped to re-align ({realigns}/{MAX_REALIGNS})")
                self._wait_still()
                quality = self._align("localization guard")
                if quality is None or quality < GUARD_QUALITY:
                    raise RuntimeError(f"Could not re-localize near {name} "
                                       f"(LiDAR on walls {0 if quality is None else quality:.0%})")
                continue                     # re-send the same waypoint
            raise RuntimeError(f"Nav2 {outcome} the goal to {name}")

    def _watch_goal(self, wp):
        """Returns 'arrived', 'lost', 'aborted' or 'canceled'."""
        start = time.monotonic()
        inside_since = None
        low = 0
        last_guard = 0.0
        time.sleep(0.4)
        while True:
            self._check_abort()
            status = self.robot.goal_status
            if status == "succeeded":
                return "arrived"
            if status in ("aborted", "canceled"):
                return status
            pose = self.robot.pose()
            if pose:
                near = math.hypot(wp["x"] - pose["x"], wp["y"] - pose["y"]) <= XY_TOL and \
                    abs(wrap(wp["theta"] - pose["theta"])) <= YAW_TOL
                if near:
                    inside_since = inside_since or time.monotonic()
                    if time.monotonic() - inside_since > ARRIVE_HOLD_SEC:
                        self.robot.cancel_goal()     # Nav2 keeps correcting inside the tolerance: stop it
                        self.event("robot", "info", "Inside the goal tolerance: goal closed by the Pi")
                        return "arrived"
                else:
                    inside_since = None
            if self.settings["guard"] and time.monotonic() - last_guard >= 1.0:
                last_guard = time.monotonic()
                q = self.robot.localization_quality()
                low = low + 1 if (q is not None and q < GUARD_QUALITY) else 0
                if low >= 2:
                    self.robot.cancel_goal()
                    self.event("robot", "warning", f"Localization guard: only {q:.0%} of LiDAR points on walls, "
                                                   "stopping to re-align")
                    return "lost"
            if time.monotonic() - start > WAYPOINT_TIMEOUT:
                self.robot.cancel_goal()
                return "aborted"
            time.sleep(0.1)

    # ---------- idle alignment ----------

    def _idle_loop(self):
        while True:
            time.sleep(2.0)
            try:
                if not self.settings["auto_align"] or self.busy() or not self.robot.link.connected:
                    continue
                if not self.robot.localized() or self.robot.loc_active is False:
                    continue
                if (self.robot.goal_status or "") in ("accepted", "executing") or self._speed() >= STILL_SPEED:
                    continue
                if time.monotonic() - self.last_align < IDLE_ALIGN_SEC:
                    continue
                q = self.robot.localization_quality()
                if q is not None and q < GOOD_QUALITY:
                    self._align("robot standing, fit was weak")
            except Exception:
                pass
