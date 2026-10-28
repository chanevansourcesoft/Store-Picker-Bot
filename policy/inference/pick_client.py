"""
Calls the policy worker (pick_worker.py) from the robot server. Plain Python, no LeRobot or GR00T
needed, so agent/robot_server/arm.py can use it as it is:

    from pick_client import run_pick, stop_arm, arm_ready

    run_pick(item, instruction=None)  -> (ok, message)   one pick; blocks until the arm is back at rest
    stop_arm()                        -> (ok, message)   stop the arm where it is (during a pick)
    arm_ready()                       -> bool            the worker is running, its model loaded
    status()                                             the worker's full status (dict), or None

The worker's address is PICK_WORKER_URL (default http://127.0.0.1:8790). The instruction given to
GR00T always comes from policy/instructions.json, by item id; run_pick's instruction argument is
only there to match arm.run_pick(item, instruction) and is not used.
"""

import json
import os
import urllib.error
import urllib.request

URL = os.environ.get("PICK_WORKER_URL", "http://127.0.0.1:8790").rstrip("/")
PICK_TIMEOUT = 300  # seconds: longer than an episode (--max_s) plus the move back to rest


def _call(method, path, body=None, timeout=5):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(URL + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def run_pick(item, instruction=None):
    """Pick one unit of item and put it in the basket. (ok, message); the arm ends at rest unless stopped."""
    try:
        result = _call("POST", "/pick", {"item": item}, timeout=PICK_TIMEOUT)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, f"policy worker not reachable at {URL}: {e}"
    return bool(result.get("ok")), result.get("message", "")


def stop_arm():
    """Stop the arm where it is. (ok, message)."""
    try:
        result = _call("POST", "/stop", {}, timeout=2)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, f"policy worker not reachable at {URL}: {e}"
    return bool(result.get("ok")), result.get("message", "")


def status():
    try:
        return _call("GET", "/status", timeout=2)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def arm_ready():
    s = status()
    return bool(s and s.get("ready"))
