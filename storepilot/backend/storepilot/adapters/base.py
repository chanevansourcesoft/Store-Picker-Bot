"""
The one boundary between the dashboard and a robot.

Only HIGH-LEVEL commands exist here: go to a named station, pick a named product
(one GR00T run: pick it AND place it in the basket), return a product to its shelf,
stop, resume. There is no way to send wheel
velocities, PWM or joint targets from the dashboard.

Two methods are used by the telemetry poller and RAISE on any problem, so a
failure is visible as "disconnected" instead of being hidden:
    fetch_health() -> dict
    fetch_status() -> dict
    fetch_map()    -> {"stations": {...}}

The command methods follow the robot contract (agent/robot_server/README.md):
they return {"success": bool, "message": str, ...} and never raise.
"""

VALID_STATIONS = ("home", "shelf_A", "shelf_B", "shelf_C", "customer")


class AdapterError(Exception):
    """The robot could not be reached or answered something unexpected."""


class RobotAdapter:
    source = "unknown"      # "simulated" or "jetson"

    # ---------- telemetry (raise on failure) ----------
    def fetch_health(self):
        raise NotImplementedError

    def fetch_status(self):
        raise NotImplementedError

    def fetch_map(self):
        raise NotImplementedError

    # ---------- high-level commands (never raise) ----------
    def navigate(self, station):
        raise NotImplementedError

    def pick(self, item, shelf):
        """Pick one unit and place it in the basket. Success result: {"item", "target": "basket", ...}."""
        raise NotImplementedError

    def return_item(self, item):
        """Take one unit of an item from the basket back to its shelf."""
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def resume(self):
        raise NotImplementedError

    # The shared agent calls status() to sync its order tracker: never raise there.
    def status(self):
        try:
            return self.fetch_status()
        except Exception:
            return {}
