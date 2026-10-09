"""
Talks to the robot server over HTTP (mock robot now, real Jetson later).
Only ROBOT_URL in .env changes when we switch to the real robot.

Demo mode: if DEMO_MODE=1, or ROBOT_URL does not answer at startup, we use
the mock robot inside this Python process (no robot server needed).
Good for Streamlit Community Cloud. The choice is made once per process.

Contract v2: supports multiple shelves, basket, emergency stop, pose, battery.
"""

import threading

import requests

from app.config import DEMO_MODE, ROBOT_URL

TIMEOUT_SEC = 120  # a real pick or navigation can take time
STARTUP_CHECK_SEC = 3  # how long we wait for the real robot at startup

_demo_mode = None  # None = not decided yet
_demo_lock = threading.Lock()


def is_demo_mode():
    """True if we use the simulated robot. Decided on the first call, then fixed."""
    global _demo_mode
    with _demo_lock:
        if _demo_mode is None:
            if DEMO_MODE:
                _demo_mode = True
                print("[Robot] Demo mode: simulated robot (DEMO_MODE=1)")
            elif _robot_reachable():
                _demo_mode = False
                print(f"[Robot] Using real robot at {ROBOT_URL}")
            else:
                _demo_mode = True
                print(f"[Robot] Demo mode: simulated robot ({ROBOT_URL} not reachable)")
        return _demo_mode


def _robot_reachable():
    try:
        r = requests.get(f"{ROBOT_URL}/health", timeout=STARTUP_CHECK_SEC)
        return r.ok and r.json().get("status") == "ok"
    except Exception:
        return False


def _call_mock(method, path, body):
    """Run the request on the in-process mock robot (same contract v2)."""
    from robot_server import mock_robot as mock

    body = body or {}
    routes = {
        ("GET", "/health"): lambda: mock.health(),
        ("GET", "/status"): lambda: mock.status(),
        ("GET", "/map"): lambda: mock.get_map(),
        ("POST", "/navigate"): lambda: mock.navigate(mock.NavigateRequest(**body)),
        ("POST", "/pick"): lambda: mock.pick(mock.PickRequest(**body)),
        ("POST", "/scan"): lambda: mock.scan(),
        ("POST", "/place"): lambda: mock.place(mock.PlaceRequest(**body)),
        ("POST", "/stop"): lambda: mock.stop(),
        ("POST", "/resume"): lambda: mock.resume(),
    }
    handler = routes.get((method, path))
    if handler is None:
        return {"success": False, "message": f"unknown robot call {method} {path}"}
    try:
        return handler()
    except Exception as e:
        return {"success": False, "message": f"robot error: {e}"}


def _call(method, path, body=None):
    if is_demo_mode():
        return _call_mock(method, path, body)

    url = f"{ROBOT_URL}{path}"
    try:
        r = requests.request(method, url, json=body, timeout=TIMEOUT_SEC)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        return {"success": False, "message": f"cannot reach robot at {ROBOT_URL}"}
    except requests.exceptions.Timeout:
        return {"success": False, "message": "robot timeout"}
    except Exception as e:
        return {"success": False, "message": f"robot error: {e}"}


def health():
    """Check if robot is online."""
    return _call("GET", "/health")


def status():
    """Get robot status: busy, holding, station, pose, battery, speed, nav_state, obstacle, last_error."""
    return _call("GET", "/status")


def get_map():
    """Get station positions for the dashboard."""
    return _call("GET", "/map")


def navigate(station):
    """Navigate robot to a station: 'home', 'shelf_A', 'shelf_B', 'shelf_C', or 'customer'."""
    return _call("POST", "/navigate", {"station": station})


def pick(item, shelf, instruction=None):
    """Pick an item from a shelf. The instruction is passed to GR00T policy."""
    body = {"item": item, "shelf": shelf}
    if instruction:
        body["instruction"] = instruction
    return _call("POST", "/pick", body)


def scan():
    """Scan the barcode of the held item."""
    return _call("POST", "/scan")


def place(target):
    """Place the held item in basket or return it to shelf."""
    return _call("POST", "/place", {"target": target})


def stop():
    """Emergency stop the robot."""
    return _call("POST", "/stop")


def resume():
    """Resume after emergency stop."""
    return _call("POST", "/resume")


def camera_url():
    """Get the camera stream URL (for dashboard). None in demo mode (no camera)."""
    if is_demo_mode():
        return None
    return f"{ROBOT_URL}/camera"


if __name__ == "__main__":
    # Quick test of all endpoints
    print("Testing robot client...")
    print(f"ROBOT_URL: {ROBOT_URL}")
    print(f"Demo mode: {is_demo_mode()}")

    print("\n/health:", health())
    print("/status:", status())
    print("/map:", get_map())

    print("\nNavigate to shelf_A:", navigate("shelf_A"))
    print("Pick soup_can:", pick("soup_can", "shelf_A", "pick up the soup can"))
    print("Scan:", scan())
    print("Place in basket:", place("basket"))

    print("\nNavigate to customer:", navigate("customer"))
    print("Navigate to home:", navigate("home"))

    print("\n/status:", status())
    print("\nDone.")
