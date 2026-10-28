"""
Jetson Robot Server - Contract v2

FastAPI server for the hardware robot running on NVIDIA Jetson with ROS 2 Humble.
Uses Nav2 for navigation. The arm runs one GR00T episode per /pick (pick the item
AND place it in the basket) through the policy worker; see arm.py, the single integration
point for the policy. The GR00T instruction comes from policy/instructions.json by item id
(instructions.py); which station (rack) an item is at comes from the catalog (catalog.py).

GR00T only works close to where it was recorded, so a rack station (shelf_*) is reached
with a tight Nav2 goal tolerance, the parked pose is checked, and the base stays still for
PICK_SETTLE_SEC before and during a pick. Nothing drives while a pick runs.

Endpoints match mock_robot.py exactly so the AI agent works with both.

Run (from agent/):
    # First, source ROS 2 and Nav2
    source /opt/ros/humble/setup.bash
    source ~/nav2_ws/install/setup.bash

    # Then run the server
    uvicorn robot_server.jetson_server:app --host 0.0.0.0 --port 8000

Station positions:
    Read from robot_server/stations.yaml (copy stations.example.yaml to start).
    If the file is missing or wrong, the server uses STATION_MAP below.

Dependencies:
    pip install fastapi uvicorn pyyaml
    # ROS 2 Humble with Nav2 must be installed separately
"""

# Type hints are not run at import, so the file loads without ROS (stub mode)
from __future__ import annotations

import asyncio
import math
import os
import threading
import time
from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from robot_server import arm, catalog, instructions

# -----------------------------------------------------------------------------
# ROS 2 / Nav2 imports (only import if available)
# -----------------------------------------------------------------------------
try:
    import rclpy
    from rclpy.action import ActionClient
    from rclpy.node import Node
    from geometry_msgs.msg import PoseStamped, Twist
    from nav2_msgs.action import NavigateToPose
    from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue
    from rcl_interfaces.srv import GetParameters, SetParameters
    from sensor_msgs.msg import BatteryState
    from std_msgs.msg import String

    ROS_AVAILABLE = True
except ImportError:
    ROS_AVAILABLE = False
    print("[WARN] ROS 2 not available. Running in stub mode.")
    if "Node" not in globals():
        Node = object  # stand-in so the Nav2Client class can be defined (never used)


# -----------------------------------------------------------------------------
# Station positions (in meters, relative to map origin)
# The real positions come from stations.yaml (set by the navigation team).
# STATION_MAP is the fallback when stations.yaml is missing or wrong.
# -----------------------------------------------------------------------------
STATION_MAP = {
    "home": {"x": 0.0, "y": 0.0, "theta": 0.0},
    "shelf_A": {"x": 1.5, "y": 0.0, "theta": 1.57},   # Facing shelf
    "shelf_B": {"x": 1.5, "y": 1.0, "theta": 1.57},
    "shelf_C": {"x": 1.5, "y": 2.0, "theta": 1.57},
    "customer": {"x": 0.0, "y": 2.0, "theta": 3.14},  # Facing customer
}

STATIONS_FILE = Path(__file__).resolve().parent / "stations.yaml"
NAV_TIMEOUT_SEC = 120.0  # a navigation that takes longer is cancelled and counts as failed
STUB_DRIVE_SEC = 2.0     # stub mode (no ROS): simulated travel time


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        print(f"[WARN] {name} must be a number; using {default}")
        return float(default)


# Parking at a rack (shelf_* stations): GR00T was recorded with the base a few cm in front of
# the rack, straight or turned slightly. Nav2 gets these goal tolerances for a rack goal; the
# parked pose is checked against them, with PARK_ATTEMPTS goals at most.
PARK_XY_TOLERANCE = _env_float("PARK_XY_TOLERANCE", "0.03")     # metres
PARK_YAW_TOLERANCE = _env_float("PARK_YAW_TOLERANCE", "0.05")   # radians (about 3 degrees)
PARK_ATTEMPTS = max(1, int(_env_float("PARK_ATTEMPTS", "2")))
PICK_SETTLE_SEC = _env_float("PICK_SETTLE_SEC", "1.0")          # base still this long after arriving
# Nav2 parameters set for rack goals (and restored for the others), see ros2_ws mecanum_nav config
NAV2_CONTROLLER = os.environ.get("NAV2_CONTROLLER", "/controller_server")
NAV2_GOAL_CHECKER = os.environ.get("NAV2_GOAL_CHECKER", "general_goal_checker")


def is_rack(station):
    """Rack stations are where the arm picks: shelf_A, shelf_B, shelf_C."""
    return str(station).startswith("shelf_")


def parking_error(pose, target):
    """(distance in m, |heading difference| in rad) between a pose and a station."""
    distance = math.hypot(pose["x"] - target["x"], pose["y"] - target["y"])
    turn = pose["theta"] - target.get("theta", 0.0)
    return distance, abs(math.atan2(math.sin(turn), math.cos(turn)))


def _read_station(name, pos):
    """Check one station from the yaml file. Returns {"x", "y", "theta"} or raises ValueError."""
    if not isinstance(pos, dict):
        raise ValueError(f"'{name}' must have x, y and theta")
    result = {}
    for key in ("x", "y", "theta"):
        value = pos.get(key, 0.0 if key == "theta" else None)  # theta is optional
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"'{name}' needs a number for {key}, got {value!r}")
        result[key] = float(value)
    return result


def load_stations(path=STATIONS_FILE):
    """Load station positions from stations.yaml.

    All 5 stations must be in the file. If the file is missing or has any
    problem, we use the whole STATION_MAP (never a mix of the two).
    Returns (stations, source_text).
    """
    path = Path(path)
    if not path.exists():
        return STATION_MAP, f"STATION_MAP fallback ({path.name} not found)"
    try:
        import yaml
    except ImportError:
        print(f"[WARN] PyYAML not installed, cannot read {path.name}. Run: pip install pyyaml")
        return STATION_MAP, "STATION_MAP fallback (PyYAML not installed)"

    try:
        data = yaml.safe_load(path.read_text()) or {}
        raw = data.get("stations") if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            raise ValueError("the file needs a 'stations:' section")

        missing = [name for name in STATION_MAP if name not in raw]
        if missing:
            raise ValueError(f"missing stations: {', '.join(missing)}")
        extra = [name for name in raw if name not in STATION_MAP]
        if extra:
            print(f"[WARN] {path.name}: unknown stations ignored: {', '.join(extra)}")

        stations = {name: _read_station(name, raw[name]) for name in STATION_MAP}
        return stations, f"{path.name}"
    except Exception as e:
        print(f"[WARN] {path.name} has a problem: {e}. Using STATION_MAP instead.")
        return STATION_MAP, f"STATION_MAP fallback ({path.name} has a problem)"


# Positions the server uses (from stations.yaml, or STATION_MAP as fallback)
STATIONS, STATIONS_SOURCE = load_stations()
print(f"[Stations] Using {STATIONS_SOURCE}")


# -----------------------------------------------------------------------------
# Pydantic models (same as mock_robot.py)
# -----------------------------------------------------------------------------
class Station(str, Enum):
    home = "home"
    shelf_A = "shelf_A"
    shelf_B = "shelf_B"
    shelf_C = "shelf_C"
    customer = "customer"


class NavigateRequest(BaseModel):
    station: Station


class PickRequest(BaseModel):
    item: str
    shelf: str
    instruction: Optional[str] = None  # ignored: the instruction comes from policy/instructions.json


class PlaceRequest(BaseModel):
    target: str  # only "return": take one unit of `item` from the basket back to its shelf
    item: Optional[str] = None


# -----------------------------------------------------------------------------
# Robot State
# -----------------------------------------------------------------------------
class RobotState:
    def __init__(self):
        self.busy = False
        self.holding: Optional[str] = None
        self.station = "home"
        self.pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
        self.battery = 100.0
        self.speed = 0.0
        self.nav_state = "idle"  # idle, navigating, arrived, failed
        self.obstacle = False
        self.last_error: Optional[str] = None
        # GR00T pick tries for the current unit (reset after it is placed in the basket,
        # after driving to another station, and on /resume)
        self.pick_item: Optional[str] = None
        self.pick_try = 0
        self.stopped = False
        # True from the start of a /pick until the arm is back: nothing may drive meanwhile.
        # Separate from busy, which /stop clears while the episode may still be ending.
        self.picking = False
        self.arrived_at: Optional[float] = None   # time.monotonic() when the last goal ended here
        self.parked: Optional[dict] = None        # measured parking error at a rack {"xy_cm", "yaw_deg"}
        # Set when the gripper still holds something after a pick: no pick and no driving
        # until a person has checked the arm and pressed Resume.
        self.needs_operator: Optional[str] = None
        self.lock = threading.Lock()

    def to_dict(self):
        with self.lock:
            return {
                "busy": self.busy,
                "holding": self.holding,
                "station": self.station,
                "pose": self.pose.copy(),
                "battery": round(self.battery, 1),
                "speed": round(self.speed, 2),
                "nav_state": self.nav_state,
                "obstacle": self.obstacle,
                "last_error": self.last_error,
                "stopped": self.stopped,
                "picking": self.picking,
                "pick_try": self.pick_try,
                "parked": dict(self.parked) if self.parked else None,
                "needs_operator": self.needs_operator,
            }


robot_state = RobotState()


def succeeded(result):
    """A command worked, so the old error is not true any more: clear it."""
    with robot_state.lock:
        robot_state.last_error = None
    return result


def log_groot(text, attempt, message):
    """One line per pick, e.g. [GR00T] pick up the chips packet (try 2) -> picked"""
    print(f"[GR00T] {text} (try {attempt}) -> {message}", flush=True)


# -----------------------------------------------------------------------------
# Nav2 Action Client (ROS 2)
# -----------------------------------------------------------------------------
class Nav2Client(Node):
    """ROS 2 node that sends goals to Nav2 and tracks robot state."""

    def __init__(self):
        super().__init__("jetson_robot_server")

        # Nav2 action client
        self._action_client = ActionClient(self, NavigateToPose, "navigate_to_pose")

        # Subscribers for robot state
        self._battery_sub = self.create_subscription(
            BatteryState, "/battery_state", self._battery_callback, 10
        )

        # Publisher for emergency stop (send zero velocity)
        self._cmd_vel_pub = self.create_publisher(Twist, "/cmd_vel", 10)

        # Goal handle for cancellation
        self._goal_handle = None
        # Every goal gets a number. Callbacks of an older goal (a late result after a
        # timeout or a cancel) are ignored, so they cannot finish or fail the new goal.
        self._goal_lock = threading.Lock()
        self._goal_seq = 0
        self._navigation_complete = threading.Event()
        self._navigation_result = None

        # Goal tolerance of the Nav2 goal checker: tight for a rack, Nav2's own for the rest
        self._set_params = self.create_client(SetParameters, f"{NAV2_CONTROLLER}/set_parameters")
        self._get_params = self.create_client(GetParameters, f"{NAV2_CONTROLLER}/get_parameters")
        self._normal_tolerance = None   # (xy, yaw) Nav2 had before our first change
        self._tolerance = None          # (xy, yaw) set by us now
        # Measured pose from Nav2 feedback: of the current goal, and the latest one of any goal
        self._goal_pose = None
        self.last_feedback_pose = None

        self.get_logger().info("Nav2Client initialized")

    def _battery_callback(self, msg: BatteryState):
        """Update battery state from ROS topic."""
        with robot_state.lock:
            robot_state.battery = msg.percentage * 100.0

    def send_goal(self, station: str) -> bool:
        """Send a navigation goal to Nav2. Blocks until complete."""
        if station not in STATIONS:
            return False

        target = STATIONS[station]
        try:
            self.apply_tolerance(station)
        except Exception as e:  # the goal is still sent; the parking is checked after it
            self.get_logger().warn(f"Could not set the Nav2 goal tolerance: {e}")

        # Wait for action server
        if not self._action_client.wait_for_server(timeout_sec=5.0):
            self.get_logger().error("Nav2 action server not available")
            with robot_state.lock:
                robot_state.last_error = "Nav2 not available"
            return False

        # Build goal
        goal_msg = NavigateToPose.Goal()
        goal_msg.pose = PoseStamped()
        goal_msg.pose.header.frame_id = "map"
        goal_msg.pose.header.stamp = self.get_clock().now().to_msg()
        goal_msg.pose.pose.position.x = target["x"]
        goal_msg.pose.pose.position.y = target["y"]
        goal_msg.pose.pose.position.z = 0.0

        # Convert theta to quaternion (yaw only)
        theta = target["theta"]
        goal_msg.pose.pose.orientation.z = math.sin(theta / 2.0)
        goal_msg.pose.pose.orientation.w = math.cos(theta / 2.0)

        # Send goal: a fresh result and done-event for this goal only
        with self._goal_lock:
            self._goal_seq += 1
            seq = self._goal_seq
            self._goal_handle = None
            self._navigation_result = None
            self._goal_pose = None
            done = self._navigation_complete = threading.Event()
        with robot_state.lock:
            robot_state.busy = True
            robot_state.nav_state = "navigating"
            robot_state.speed = 0.3  # Will be updated by odometry

        self.get_logger().info(f"Navigating to {station} at ({target['x']}, {target['y']})")

        send_goal_future = self._action_client.send_goal_async(
            goal_msg, feedback_callback=self._feedback_callback
        )
        send_goal_future.add_done_callback(lambda future: self._goal_response_callback(future, seq))

        # Wait for THIS goal to finish
        finished = done.wait(timeout=NAV_TIMEOUT_SEC)
        if not finished:
            self.get_logger().warn(f"Navigation to {station} timed out, cancelling the goal")
            self.cancel_navigation()
            with self._goal_lock:
                if self._goal_seq == seq:
                    self._goal_seq += 1  # a late result of this goal is ignored now
            with robot_state.lock:
                robot_state.nav_state = "failed"
                robot_state.last_error = "Navigation timed out"

        with robot_state.lock:
            robot_state.busy = False
            robot_state.speed = 0.0

        return finished and self._navigation_result is True

    def _is_current(self, seq):
        with self._goal_lock:
            return seq == self._goal_seq

    def _finish(self, seq, ok):
        """Give goal `seq` its result and wake up send_goal (only if it is still the current goal)."""
        with self._goal_lock:
            if seq != self._goal_seq:
                return
            self._navigation_result = ok
            self._navigation_complete.set()

    def _goal_response_callback(self, future, seq):
        """Called when goal is accepted or rejected."""
        if not self._is_current(seq):
            return  # answer for an older goal
        goal_handle = future.result()
        if not goal_handle.accepted:
            self.get_logger().warn("Goal rejected by Nav2")
            with robot_state.lock:
                robot_state.nav_state = "failed"
                robot_state.last_error = "Goal rejected"
            self._finish(seq, False)
            return

        self._goal_handle = goal_handle
        self.get_logger().info("Goal accepted")

        # Get result
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(lambda future: self._get_result_callback(future, seq))

    def _get_result_callback(self, future, seq):
        """Called when navigation completes."""
        if not self._is_current(seq):
            return  # result of an older goal: it must not finish the current one
        status = future.result().status

        if status == 4:  # SUCCEEDED
            self.get_logger().info("Navigation succeeded")
            with robot_state.lock:
                robot_state.nav_state = "arrived"
            self._finish(seq, True)
        else:
            self.get_logger().warn(f"Navigation failed with status {status}")
            with robot_state.lock:
                robot_state.nav_state = "failed"
                robot_state.last_error = f"Nav failed (status {status})"
            self._finish(seq, False)

    def _feedback_callback(self, feedback_msg):
        """Update robot pose from Nav2 feedback (map frame)."""
        feedback = feedback_msg.feedback
        pose = feedback.current_pose.pose
        # Extract yaw from quaternion
        siny_cosp = 2.0 * (pose.orientation.w * pose.orientation.z)
        cosy_cosp = 1.0 - 2.0 * (pose.orientation.z * pose.orientation.z)
        measured = {"x": pose.position.x, "y": pose.position.y, "theta": math.atan2(siny_cosp, cosy_cosp)}
        self._goal_pose = self.last_feedback_pose = measured

        with robot_state.lock:
            robot_state.pose = dict(measured)

    def measured_pose(self):
        """Where Nav2 last saw the robot: during the last goal, else during any goal. None if never."""
        return self._goal_pose or self.last_feedback_pose

    # ---------- goal tolerance (rack parking) ----------

    def _call_service(self, client, request, timeout=2.0):
        """Call a ROS service from a non-ROS thread (the executor thread answers). None on failure."""
        if client is None or not client.wait_for_service(timeout_sec=timeout):
            return None
        done = threading.Event()
        future = client.call_async(request)
        future.add_done_callback(lambda _: done.set())
        if not done.wait(timeout):
            return None
        return future.result()

    def _tolerance_names(self):
        return [f"{NAV2_GOAL_CHECKER}.xy_goal_tolerance", f"{NAV2_GOAL_CHECKER}.yaw_goal_tolerance"]

    def _read_tolerance(self):
        request = GetParameters.Request()
        request.names = self._tolerance_names()
        response = self._call_service(self._get_params, request)
        if response is None or len(response.values) != 2:
            return None
        return (response.values[0].double_value, response.values[1].double_value)

    def _write_tolerance(self, xy, yaw):
        request = SetParameters.Request()
        request.parameters = [
            Parameter(name=name, value=ParameterValue(type=ParameterType.PARAMETER_DOUBLE, double_value=float(v)))
            for name, v in zip(self._tolerance_names(), (xy, yaw))]
        response = self._call_service(self._set_params, request)
        return response is not None and all(r.successful for r in response.results)

    def apply_tolerance(self, station):
        """Set the Nav2 goal tolerance for a goal to `station`: PARK_*_TOLERANCE for a rack,
        Nav2's own values otherwise. Returns False if it could not be set (parking is still checked)."""
        if self._normal_tolerance is None:
            self._normal_tolerance = self._read_tolerance()
        want = (PARK_XY_TOLERANCE, PARK_YAW_TOLERANCE) if is_rack(station) else self._normal_tolerance
        if want is None:
            self.get_logger().warn(f"Cannot read the Nav2 goal tolerance from {NAV2_CONTROLLER}: left unchanged")
            return False
        if want == self._tolerance:
            return True
        if self._write_tolerance(*want):
            self._tolerance = want
            self.get_logger().info(f"Nav2 goal tolerance {want[0]:.3f} m / {want[1]:.3f} rad for {station}")
            return True
        self.get_logger().warn(f"Could not set the Nav2 goal tolerance on {NAV2_CONTROLLER} "
                               f"({NAV2_GOAL_CHECKER}): parking may not be precise enough")
        return False

    def hold_still(self):
        """Publish one zero velocity: the base must not move during a pick."""
        self._cmd_vel_pub.publish(Twist())

    def cancel_navigation(self):
        """Cancel current navigation goal."""
        if self._goal_handle is not None:
            self.get_logger().info("Cancelling navigation")
            self._goal_handle.cancel_goal_async()
            self._goal_handle = None

    def emergency_stop(self):
        """Send zero velocity command."""
        self.get_logger().warn("EMERGENCY STOP")
        twist = Twist()
        twist.linear.x = 0.0
        twist.linear.y = 0.0
        twist.angular.z = 0.0
        self._cmd_vel_pub.publish(twist)


# Global Nav2 client (initialized on startup)
nav2_client: Optional[Nav2Client] = None
ros_executor = None
ros_thread = None


def ros_spin_thread():
    """Thread that spins the ROS 2 executor."""
    global ros_executor
    if ros_executor:
        ros_executor.spin()


# -----------------------------------------------------------------------------
# FastAPI app
# -----------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize ROS 2 on startup, shutdown on exit."""
    global nav2_client, ros_executor, ros_thread

    if ROS_AVAILABLE:
        try:
            rclpy.init()
            nav2_client = Nav2Client()
            ros_executor = rclpy.executors.SingleThreadedExecutor()
            ros_executor.add_node(nav2_client)
            ros_thread = threading.Thread(target=ros_spin_thread, daemon=True)
            ros_thread.start()
            print("[INFO] ROS 2 initialized successfully")
        except Exception as e:
            print(f"[ERROR] Failed to initialize ROS 2: {e}")
            nav2_client = None

    yield

    # Shutdown
    if ROS_AVAILABLE and nav2_client:
        nav2_client.destroy_node()
        rclpy.shutdown()


app = FastAPI(
    title="Jetson Robot Server",
    description="Contract v2 robot API for NVIDIA Jetson with Nav2",
    version="2.0.0",
    lifespan=lifespan,
)


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
@app.get("/status")
def get_status():
    """Get current robot status."""
    return robot_state.to_dict()


@app.get("/map")
def get_map():
    """Get station positions for the store map."""
    return {"stations": STATIONS}


def fail(message, **fields):
    """A /navigate or /pick that did not happen. The error stays in last_error."""
    with robot_state.lock:
        robot_state.last_error = message
    return {"success": False, "message": message, **fields}


def _arrived(station, parked=None):
    with robot_state.lock:
        if robot_state.station != station:
            robot_state.pick_item, robot_state.pick_try = None, 0   # tries count per visit
        robot_state.station = station
        robot_state.nav_state = "arrived"
        robot_state.arrived_at = time.monotonic()
        robot_state.parked = parked


async def _drive(station):
    """One Nav2 goal (real), or a simulated drive (stub). True if Nav2 reports success."""
    if nav2_client and ROS_AVAILABLE:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, nav2_client.send_goal, station)
    with robot_state.lock:
        robot_state.busy = True
        robot_state.nav_state = "navigating"
    await asyncio.sleep(STUB_DRIVE_SEC)  # Simulate travel time
    with robot_state.lock:
        robot_state.busy = False
        robot_state.pose = STATIONS[station].copy()
        robot_state.battery = max(0, robot_state.battery - 1.0)
    return True


@app.post("/navigate")
async def navigate(req: NavigateRequest):
    """Navigate to a station using Nav2. A rack (shelf_*) counts as reached only when the base
    is parked within PARK_XY_TOLERANCE / PARK_YAW_TOLERANCE (up to PARK_ATTEMPTS goals)."""
    station = req.station.value
    with robot_state.lock:
        if robot_state.stopped:
            return {"success": False, "message": "Robot is stopped. Call /resume first."}
        if robot_state.picking:
            return {"success": False, "message": "A pick is running: the base does not move until it ends"}
        if robot_state.busy:
            return {"success": False, "message": "Robot is busy"}
        if robot_state.needs_operator:
            return {"success": False, "message": f"Not driving: {robot_state.needs_operator}. "
                                                 "A person must check the arm, then press Resume."}
        if robot_state.station == station and robot_state.nav_state == "arrived":
            return succeeded({"success": True, "message": f"already at {station}"})   # no driving at all
        robot_state.station = None   # on the way: no pick is allowed until we arrive again

    real = nav2_client is not None and ROS_AVAILABLE
    if not (real and is_rack(station)):
        if not await _drive(station):
            return fail(f"Navigation to {station} failed")
        _arrived(station)
        return succeeded({"success": True, "message": f"Arrived at {station}" + ("" if real else " (stub mode)")})

    target = STATIONS[station]
    parked, attempt = None, 0
    for attempt in range(1, PARK_ATTEMPTS + 1):
        if not await _drive(station):
            return fail(f"Navigation to {station} failed")
        pose = nav2_client.measured_pose()
        if pose is None:
            return fail(f"Arrived at {station}, but Nav2 sent no pose: the parking cannot be checked")
        distance, turn = parking_error(pose, target)
        parked = {"xy_cm": round(distance * 100, 1), "yaw_deg": round(math.degrees(turn), 1), "attempt": attempt}
        print(f"[Park] {station} try {attempt}: {parked['xy_cm']} cm, {parked['yaw_deg']} deg", flush=True)
        if distance <= PARK_XY_TOLERANCE and turn <= PARK_YAW_TOLERANCE:
            _arrived(station, parked)
            return succeeded({"success": True, "message": f"Arrived at {station}", "parked": parked})
        if robot_state.stopped:
            break
    with robot_state.lock:
        robot_state.parked = parked
    return fail(f"Parked {parked['xy_cm']} cm / {parked['yaw_deg']} deg from the {station} position after "
                f"{attempt} goal(s); GR00T needs {PARK_XY_TOLERANCE * 100:.0f} cm / "
                f"{math.degrees(PARK_YAW_TOLERANCE):.0f} deg", parked=parked)


@app.post("/pick")
async def pick_item(req: PickRequest):
    """Pick one unit of an item and place it in the basket: ONE GR00T episode
    (rest pose -> pick -> basket -> rest pose) on the policy worker.

    Before the arm moves: the item id must be a product id with a GR00T instruction in
    policy/instructions.json, the catalog must put it at req.shelf (a station), the robot must
    be parked there, and the arm must be ready. Then the base stays still for PICK_SETTLE_SEC
    and the episode runs. Failures say whether a retry makes sense ("retryable") and whether
    a person must check the arm ("needs_operator").
    """
    entry, problem = instructions.lookup(req.item)
    station = catalog.station_of(req.item) if not problem else None
    with robot_state.lock:
        if robot_state.stopped:
            return {"success": False, "message": "Robot is stopped. Call /resume first.", "retryable": False}
        if robot_state.busy or robot_state.picking:
            return {"success": False, "message": "Robot is busy", "retryable": False}
        if robot_state.needs_operator:
            return {"success": False, "message": f"{robot_state.needs_operator}. A person must check the arm, "
                                                 "then press Resume.", "retryable": False, "needs_operator": True}
        if robot_state.holding:
            return {"success": False, "message": f"Already holding {robot_state.holding}", "retryable": False}
    if problem:
        return fail(problem, item=req.item, retryable=False)
    if station is None:
        return fail(f"{req.item} is not in the store catalog", item=req.item, retryable=False)
    if req.shelf != station:
        return fail(f"{req.item} is at {station}, not {req.shelf}", item=req.item, retryable=False)
    with robot_state.lock:
        here, nav_state, arrived_at = robot_state.station, robot_state.nav_state, robot_state.arrived_at
    if here != station or nav_state != "arrived":
        return fail(f"Not parked at {station}. Currently at {here or 'no station'}", item=req.item, retryable=False)

    loop = asyncio.get_event_loop()
    if not await loop.run_in_executor(None, arm.arm_ready):
        return fail("Arm not ready: the policy worker is not running or its model is still loading",
                    item=req.item, retryable=False)

    with robot_state.lock:
        if robot_state.stopped or robot_state.busy or robot_state.picking or robot_state.station != station:
            return {"success": False, "message": "Robot state changed, pick not started", "retryable": False}
        robot_state.busy = robot_state.picking = True
        robot_state.holding = req.item  # in the gripper while the episode runs
        if robot_state.pick_item != req.item:
            robot_state.pick_item = req.item
            robot_state.pick_try = 0
        robot_state.pick_try += 1
        attempt = robot_state.pick_try

    instruction = entry["instruction"]
    try:
        if nav2_client and ROS_AVAILABLE:
            nav2_client.hold_still()
        wait = PICK_SETTLE_SEC - (time.monotonic() - (arrived_at or 0.0))
        if wait > 0:
            await asyncio.sleep(wait)   # let the base settle after parking
        if robot_state.stopped:
            ok, message = False, "stopped before the arm moved"
        else:
            ok, message = await loop.run_in_executor(None, arm.run_pick, req.item, instruction)
    except Exception as e:  # the policy client crashed: the pick failed, the server keeps running
        ok, message = False, f"GR00T error: {e}"
    outcome = arm.classify(ok, message)

    with robot_state.lock:
        robot_state.busy = robot_state.picking = False
        robot_state.battery = max(0, robot_state.battery - 0.5)
        if outcome == arm.HOLDING:
            robot_state.holding = req.item   # still in the gripper: a person checks the arm
            robot_state.needs_operator = message
        else:
            robot_state.holding = None       # in the basket, not picked, or held by a stop
        if outcome == arm.PLACED:
            robot_state.pick_item = None     # this unit is done, next pick is try 1 again
            robot_state.pick_try = 0
        else:
            robot_state.last_error = message

    log_groot(instruction, attempt, message)
    if outcome != arm.PLACED:
        return {"success": False, "message": message, "item": req.item, "instruction": instruction,
                "pick_try": attempt, "outcome": outcome, "retryable": outcome == arm.RETRY,
                "needs_operator": outcome == arm.HOLDING}
    return succeeded({"success": True, "message": message, "item": req.item, "shelf": req.shelf,
                      "level": entry["level"], "target": "basket", "instruction": instruction,
                      "barcode": None, "pick_try": attempt, "outcome": outcome})


@app.post("/place")
async def place_item(req: PlaceRequest):
    """Return one unit of an item from the basket to its shelf.

    There is no GR00T skill for this yet, so the real robot refuses it (honestly)
    instead of pretending. Putting an item IN the basket is part of /pick.
    """
    target = req.target.lower()
    if target == "basket":
        return {"success": False, "message": "/pick already places the item in the basket"}
    if target != "return":
        return {"success": False, "message": "Target must be 'return'"}
    return {"success": False, "message": "Returning an item to its shelf is not available on the robot yet"}


@app.post("/stop")
async def emergency_stop():
    """Emergency stop: cancel the Nav2 goal, stop the base AND halt the arm where it is
    (mid-episode). After a stop the arm holds where it is; the next pick first returns to rest."""
    with robot_state.lock:
        robot_state.stopped = True
        robot_state.busy = False
        robot_state.speed = 0.0
        robot_state.nav_state = "idle"
        picking = robot_state.picking

    if nav2_client and ROS_AVAILABLE:
        nav2_client.cancel_navigation()
        nav2_client.emergency_stop()

    loop = asyncio.get_event_loop()
    arm_ok, arm_message = await loop.run_in_executor(None, arm.stop_arm)
    # The base stop is always sent. If a pick runs and the arm did not confirm, say so.
    message = "Robot stopped" + ("" if arm_ok else f"; ARM STOP NOT CONFIRMED: {arm_message}")
    return {"success": arm_ok or not picking, "message": message, "arm": {"ok": arm_ok, "message": arm_message}}


@app.post("/resume")
async def resume():
    """Resume robot operation after emergency stop. Also clears "needs operator" (the gripper
    held something): press Resume only after a person checked the arm."""
    with robot_state.lock:
        robot_state.stopped = False
        robot_state.last_error = None
        if not robot_state.picking:
            robot_state.needs_operator = None
            robot_state.holding = None
        robot_state.pick_item, robot_state.pick_try = None, 0

    return {"success": True, "message": "Robot resumed"}


# -----------------------------------------------------------------------------
# Health check
# -----------------------------------------------------------------------------
@app.get("/health")
def health_check():
    """Health check endpoint. "ok" like mock_robot.py (the contract).
    arm_ready: the policy worker runs and its model is loaded. Missions wait until it is True."""
    worker = arm.arm_status()
    return {
        "status": "ok",
        "ros_available": ROS_AVAILABLE,
        "ros_connected": nav2_client is not None,
        "arm_ready": bool(worker and worker.get("ready")),
        "arm": {"state": worker.get("state"), "dry_run": worker.get("dry_run"), "model": worker.get("model")}
               if worker else {"state": "unreachable", "detail": arm.IMPORT_ERROR},
    }


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("Jetson Robot Server - Contract v2")
    print("=" * 60)
    print(f"ROS 2 Available: {ROS_AVAILABLE}")
    print(f"Stations from: {STATIONS_SOURCE}")
    print()
    print("Endpoints:")
    print("  GET  /status   - Robot status (pose, battery, etc.)")
    print("  GET  /map      - Station positions")
    print("  POST /navigate - Navigate to station")
    print("  POST /pick     - Pick item and place it in the basket (one GR00T run)")
    print("  POST /place    - Return an item to its shelf (not available yet)")
    print("  POST /stop     - Emergency stop")
    print("  POST /resume   - Resume after stop")
    print("  GET  /health   - Health check")
    print()
    print("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=8000)
