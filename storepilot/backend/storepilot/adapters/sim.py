"""
Simulated robot for development and demos (STOREPILOT_MODE=mock).

It follows the same contract as the Jetson server, and it moves smoothly so the
map can be tested. Everything it reports is labelled SIMULATED by the dashboard.
It never talks to real hardware.
"""

import math
import random
import threading
import time

from robot_server import instructions

from .base import VALID_STATIONS, RobotAdapter

# Simulated store layout (meters). Not the real store.
SIM_STATIONS = {
    "home":     {"x": 0.6, "y": 0.6, "theta": 0.0},
    "shelf_A":  {"x": 3.4, "y": 1.0, "theta": 0.0},
    "shelf_B":  {"x": 3.4, "y": 2.3, "theta": 0.0},
    "shelf_C":  {"x": 3.4, "y": 3.6, "theta": 0.0},
    "customer": {"x": 0.7, "y": 3.8, "theta": math.pi},
}
SPEED = 0.45          # m/s
TURN_SPEED = 3.0      # rad/s
TICK = 0.05


class SimRobot(RobotAdapter):
    source = "simulated"

    def __init__(self, inventory_loader, pick_fail=0.10, nav_fail=0.04, time_scale=1.0, seed=None):
        self.inventory = inventory_loader
        self.pick_fail, self.nav_fail = pick_fail, nav_fail
        self.time_scale = time_scale      # tests use a small value to run fast
        self.rng = random.Random(seed)
        self.lock = threading.Lock()
        home = SIM_STATIONS["home"]
        self.state = {
            "busy": False, "holding": None, "station": "home",
            "pose": {"x": home["x"], "y": home["y"], "theta": 0.0},
            "battery": 92.0, "speed": 0.0, "nav_state": "idle",
            "obstacle": False, "last_error": None, "stopped": False,
        }

    def _sleep(self, seconds):
        """Sleep in small steps; returns False if the robot was stopped meanwhile."""
        end = time.monotonic() + seconds * self.time_scale
        while time.monotonic() < end:
            if self.state["stopped"]:
                return False
            time.sleep(min(TICK, max(end - time.monotonic(), 0)))
        return not self.state["stopped"]

    def _fail(self, message):
        with self.lock:
            self.state["last_error"] = message
            self.state["busy"] = False
        return {"success": False, "message": message}

    def _ok(self, result):
        with self.lock:
            self.state["last_error"] = None
            self.state["busy"] = False
        return result

    # ---------- telemetry ----------

    def fetch_health(self):
        # no ROS here, so no ros_available claim; the simulated arm is always ready
        return {"status": "ok", "simulated": True, "arm_ready": True}

    def fetch_status(self):
        with self.lock:
            s = dict(self.state)
            s["pose"] = dict(self.state["pose"])
        s["battery"] = round(s["battery"], 1)
        s["speed"] = round(s["speed"], 2)
        return s

    def fetch_map(self):
        return {"stations": {k: dict(v) for k, v in SIM_STATIONS.items()}}

    # ---------- commands ----------

    def _check_ready(self):
        if self.state["stopped"]:
            return "robot is stopped"
        if self.state["busy"]:
            return "busy"
        return None

    def navigate(self, station):
        problem = self._check_ready()
        if problem:
            return self._fail(problem)
        if station not in VALID_STATIONS:
            return self._fail(f"unknown station {station!r}")
        if self.state["station"] == station:
            return self._ok({"success": True, "message": f"already at {station}"})

        target = SIM_STATIONS[station]
        with self.lock:
            self.state.update(busy=True, nav_state="navigating", obstacle=False, station="")
        fail_at = self.rng.random() if self.rng.random() < self.nav_fail else None
        pose = self.state["pose"]
        start = (pose["x"], pose["y"])
        total = math.dist(start, (target["x"], target["y"]))

        # turn toward the goal, drive, then turn to the station heading
        heading = math.atan2(target["y"] - pose["y"], target["x"] - pose["x"])
        for goal_theta in (heading, None, target["theta"]):
            if goal_theta is None:
                while True:
                    left = math.dist((pose["x"], pose["y"]), (target["x"], target["y"]))
                    done = 1 - left / total if total else 1
                    if fail_at is not None and done >= fail_at:
                        with self.lock:
                            self.state.update(speed=0.0, nav_state="failed", obstacle=True)
                        return self._fail("navigation failed, obstacle detected")
                    if left < 0.02:
                        break
                    step = min(SPEED * TICK, left)
                    with self.lock:
                        pose["x"] += (target["x"] - pose["x"]) / left * step
                        pose["y"] += (target["y"] - pose["y"]) / left * step
                        self.state["speed"] = SPEED
                        self.state["battery"] = max(0.0, self.state["battery"] - 0.004)
                    if not self._sleep(TICK):
                        with self.lock:
                            self.state.update(speed=0.0, nav_state="idle")
                        return self._fail("robot is stopped")
                with self.lock:
                    self.state["speed"] = 0.0
                continue
            while True:
                diff = (goal_theta - pose["theta"] + math.pi) % (2 * math.pi) - math.pi
                if abs(diff) < 0.03:
                    break
                with self.lock:
                    pose["theta"] += max(-TURN_SPEED * TICK, min(TURN_SPEED * TICK, diff))
                if not self._sleep(TICK):
                    with self.lock:
                        self.state.update(speed=0.0, nav_state="idle")
                    return self._fail("robot is stopped")

        with self.lock:
            pose.update(x=target["x"], y=target["y"], theta=target["theta"])
            self.state.update(station=station, nav_state="arrived", speed=0.0)
        return self._ok({"success": True, "message": f"arrived at {station}"})

    def pick(self, item, shelf):
        """One simulated GR00T run: pick the item and place it in the basket."""
        problem = self._check_ready()
        if problem:
            return self._fail(problem)
        inventory = self.inventory()
        if self.state["holding"]:
            return self._fail(f"already holding {self.state['holding']}")
        if item not in inventory:
            return self._fail(f"unknown item {item}")
        if inventory[item]["shelf"] != shelf:
            return self._fail(f"{item} is not on {shelf}")
        if self.state["station"] != shelf:
            return self._fail(f"not at {shelf}, currently at {self.state['station'] or 'moving'}")
        entry, problem = instructions.lookup(item)
        if problem:
            return self._fail(problem)
        instruction = entry["instruction"]
        with self.lock:
            self.state["busy"] = True
            self.state["holding"] = item      # in the gripper during the run
        if not self._sleep(2.5):
            with self.lock:
                self.state["holding"] = None
            return self._fail("robot is stopped")
        with self.lock:
            self.state["holding"] = None      # the run ends in the rest pose
            self.state["battery"] = max(0.0, self.state["battery"] - 0.05)
        if self.rng.random() < self.pick_fail:
            return self._fail("grasp failed")
        return self._ok({"success": True, "message": f"placed {item} in basket", "item": item, "shelf": shelf,
                         "target": "basket", "instruction": instruction, "barcode": None})

    def return_item(self, item):
        problem = self._check_ready()
        if problem:
            return self._fail(problem)
        shelf = self.inventory().get(item, {}).get("shelf")
        if not shelf:
            return self._fail(f"unknown item {item}")
        if self.state["station"] != shelf:
            return self._fail(f"cannot return {item} here")
        with self.lock:
            self.state["busy"] = True
        if not self._sleep(1.0):
            return self._fail("robot is stopped")
        return self._ok({"success": True, "message": f"returned {item} to shelf", "item": item, "target": "return"})

    def stop(self):
        with self.lock:
            self.state.update(stopped=True, speed=0.0, nav_state="idle")
        return {"success": True, "message": "robot stopped (simulated)"}

    def resume(self):
        with self.lock:
            self.state.update(stopped=False, last_error=None, busy=False)
        return {"success": True, "message": "robot resumed (simulated)"}
