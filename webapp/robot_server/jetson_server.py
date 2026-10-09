"""
Jetson Robot Server - Contract v2

FastAPI server for the hardware robot running on NVIDIA Jetson with ROS 2 Humble.
Uses Nav2 for navigation and stubs for arm/barcode operations.

Endpoints match mock_robot.py exactly so the AI agent works with both.

Run:
    # First, source ROS 2 and Nav2
    source /opt/ros/humble/setup.bash
    source ~/nav2_ws/install/setup.bash

    # Then run the server
    uvicorn jetson_server:app --host 0.0.0.0 --port 8000

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
import threading
import time
from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# -----------------------------------------------------------------------------
# ROS 2 / Nav2 imports (only import if available)
# -----------------------------------------------------------------------------
try:
    import rclpy
    from rclpy.action import ActionClient
    from rclpy.node import Node
    from geometry_msgs.msg import PoseStamped, Twist
    from nav2_msgs.action import NavigateToPose
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
    instruction: str = ""


class PlaceRequest(BaseModel):
    target: str  # "basket" or "return"


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
        self.stopped = False
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
            }


robot_state = RobotState()


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
        self._navigation_complete = threading.Event()
        self._navigation_result = None

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

        # Send goal
        self._navigation_complete.clear()
        with robot_state.lock:
            robot_state.busy = True
            robot_state.nav_state = "navigating"
            robot_state.speed = 0.3  # Will be updated by odometry

        self.get_logger().info(f"Navigating to {station} at ({target['x']}, {target['y']})")

        send_goal_future = self._action_client.send_goal_async(
            goal_msg, feedback_callback=self._feedback_callback
        )
        send_goal_future.add_done_callback(self._goal_response_callback)

        # Wait for navigation to complete
        self._navigation_complete.wait(timeout=120.0)  # 2 minute timeout

        with robot_state.lock:
            robot_state.busy = False
            robot_state.speed = 0.0

        return self._navigation_result

    def _goal_response_callback(self, future):
        """Called when goal is accepted or rejected."""
        goal_handle = future.result()
        if not goal_handle.accepted:
            self.get_logger().warn("Goal rejected by Nav2")
            with robot_state.lock:
                robot_state.nav_state = "failed"
                robot_state.last_error = "Goal rejected"
            self._navigation_result = False
            self._navigation_complete.set()
            return

        self._goal_handle = goal_handle
        self.get_logger().info("Goal accepted")

        # Get result
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(self._get_result_callback)

    def _get_result_callback(self, future):
        """Called when navigation completes."""
        result = future.result().result
        status = future.result().status

        if status == 4:  # SUCCEEDED
            self.get_logger().info("Navigation succeeded")
            with robot_state.lock:
                robot_state.nav_state = "arrived"
            self._navigation_result = True
        else:
            self.get_logger().warn(f"Navigation failed with status {status}")
            with robot_state.lock:
                robot_state.nav_state = "failed"
                robot_state.last_error = f"Nav failed (status {status})"
            self._navigation_result = False

        self._navigation_complete.set()

    def _feedback_callback(self, feedback_msg):
        """Update robot pose from Nav2 feedback."""
        feedback = feedback_msg.feedback
        pose = feedback.current_pose.pose

        with robot_state.lock:
            robot_state.pose["x"] = pose.position.x
            robot_state.pose["y"] = pose.position.y
            # Extract yaw from quaternion
            siny_cosp = 2.0 * (pose.orientation.w * pose.orientation.z)
            cosy_cosp = 1.0 - 2.0 * (pose.orientation.z * pose.orientation.z)
            robot_state.pose["theta"] = math.atan2(siny_cosp, cosy_cosp)

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


@app.post("/navigate")
async def navigate(req: NavigateRequest):
    """Navigate to a station using Nav2."""
    with robot_state.lock:
        if robot_state.stopped:
            return {"success": False, "message": "Robot is stopped. Call /resume first."}
        if robot_state.busy:
            return {"success": False, "message": "Robot is busy"}

    station = req.station.value

    if nav2_client and ROS_AVAILABLE:
        # Use real Nav2 navigation
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, nav2_client.send_goal, station)

        if success:
            with robot_state.lock:
                robot_state.station = station
                robot_state.pose = STATIONS[station].copy()
            return {"success": True, "message": f"Arrived at {station}"}
        else:
            return {"success": False, "message": f"Navigation to {station} failed"}
    else:
        # Stub mode: simulate navigation
        with robot_state.lock:
            robot_state.busy = True
            robot_state.nav_state = "navigating"

        await asyncio.sleep(2.0)  # Simulate travel time

        with robot_state.lock:
            robot_state.busy = False
            robot_state.station = station
            robot_state.pose = STATIONS[station].copy()
            robot_state.nav_state = "arrived"
            robot_state.battery = max(0, robot_state.battery - 1.0)

        return {"success": True, "message": f"Arrived at {station} (stub mode)"}


@app.post("/pick")
async def pick_item(req: PickRequest):
    """Pick an item from a shelf.

    This is a stub - implement with your GR00T policy or arm controller.
    The 'instruction' field can be passed to the policy for context.
    """
    with robot_state.lock:
        if robot_state.stopped:
            return {"success": False, "message": "Robot is stopped. Call /resume first."}
        if robot_state.busy:
            return {"success": False, "message": "Robot is busy"}
        if robot_state.holding:
            return {"success": False, "message": f"Already holding {robot_state.holding}"}

        # Check we're at the right shelf
        if robot_state.station != req.shelf:
            return {
                "success": False,
                "message": f"Not at {req.shelf}. Currently at {robot_state.station}",
            }

        robot_state.busy = True

    # TODO: Implement actual pick using GR00T policy or arm controller
    # Example integration point:
    # from groot_client import pick_with_policy
    # success = await pick_with_policy(req.item, req.instruction)

    await asyncio.sleep(1.5)  # Simulate pick time

    with robot_state.lock:
        robot_state.busy = False
        robot_state.holding = req.item
        robot_state.battery = max(0, robot_state.battery - 0.5)

    return {
        "success": True,
        "message": f"Picked {req.item} from {req.shelf}",
        "item": req.item,
    }


@app.post("/scan")
async def scan_item():
    """Scan the barcode of the held item.

    This is a stub - implement with your barcode scanner.
    """
    with robot_state.lock:
        if robot_state.stopped:
            return {"success": False, "message": "Robot is stopped. Call /resume first."}
        if not robot_state.holding:
            return {"success": False, "message": "Not holding any item"}

        item = robot_state.holding

    # TODO: Implement actual barcode scanning
    # Example integration point:
    # from barcode_scanner import scan_barcode
    # barcode = await scan_barcode()

    # For now, return a fake barcode based on item name
    fake_barcodes = {
        "soup_can": "8901000000011",
        "chips_pack": "8901000000028",
        "juice_box": "8901000000035",
    }

    await asyncio.sleep(0.5)  # Simulate scan time

    barcode = fake_barcodes.get(item, "0000000000000")

    return {
        "success": True,
        "barcode": barcode,
        "product": item,
    }


@app.post("/place")
async def place_item(req: PlaceRequest):
    """Place the held item in basket or return it.

    This is a stub - implement with your arm controller.
    """
    with robot_state.lock:
        if robot_state.stopped:
            return {"success": False, "message": "Robot is stopped. Call /resume first."}
        if not robot_state.holding:
            return {"success": False, "message": "Not holding any item"}

        item = robot_state.holding
        robot_state.busy = True

    target = req.target.lower()
    if target not in ["basket", "return"]:
        with robot_state.lock:
            robot_state.busy = False
        return {"success": False, "message": "Target must be 'basket' or 'return'"}

    # TODO: Implement actual place using arm controller
    # Example integration point:
    # from arm_controller import place_in_basket, return_to_shelf
    # if target == "basket":
    #     success = await place_in_basket()
    # else:
    #     success = await return_to_shelf()

    await asyncio.sleep(1.0)  # Simulate place time

    with robot_state.lock:
        robot_state.busy = False
        robot_state.holding = None
        robot_state.battery = max(0, robot_state.battery - 0.3)

    if target == "basket":
        return {"success": True, "message": f"placed {item} in basket"}
    else:
        return {"success": True, "message": f"returned {item} to shelf"}


@app.post("/stop")
async def emergency_stop():
    """Emergency stop - immediately halt all robot movement."""
    with robot_state.lock:
        robot_state.stopped = True
        robot_state.busy = False
        robot_state.speed = 0.0
        robot_state.nav_state = "idle"

    if nav2_client and ROS_AVAILABLE:
        nav2_client.cancel_navigation()
        nav2_client.emergency_stop()

    return {"success": True, "message": "Robot stopped"}


@app.post("/resume")
async def resume():
    """Resume robot operation after emergency stop."""
    with robot_state.lock:
        robot_state.stopped = False
        robot_state.last_error = None

    return {"success": True, "message": "Robot resumed"}


# -----------------------------------------------------------------------------
# Health check
# -----------------------------------------------------------------------------
@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "ros_available": ROS_AVAILABLE,
        "ros_connected": nav2_client is not None,
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
    print("  POST /pick     - Pick item from shelf")
    print("  POST /scan     - Scan barcode")
    print("  POST /place    - Place in basket or return")
    print("  POST /stop     - Emergency stop")
    print("  POST /resume   - Resume after stop")
    print("  GET  /health   - Health check")
    print()
    print("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=8000)
