"""
The one place where the robot server moves the arm, through the policy worker.

The policy worker (policy/inference/pick_worker.py, owned by the policy team) runs on the
Jetson, keeps GR00T loaded and answers on PICK_WORKER_URL (default http://127.0.0.1:8790).
We call it with policy/inference/pick_client.py (standard library only), imported from
that folder as it is, never copied:

    run_pick(item, instruction) -> (ok, message)   ONE episode: from the rest pose, pick one
                                                   unit, put it in the basket, back to rest.
                                                   Blocks up to ~1 min. Never raises.
    stop_arm()                  -> (ok, message)   halt the arm where it is (mid-episode)
    arm_ready()                 -> bool            worker running and its model loaded
    arm_status()                -> dict or None    the worker's full /status

The client only returns (ok, message), so classify() turns the worker's message into an
outcome. It is the only place that reads the message text.

Where pick_client.py is found: POLICY_INFERENCE_DIR, else <repo>/policy/inference.
If it is missing, every pick fails with a clear message and the arm is "not ready".
"""

import os
import sys
from pathlib import Path

INFERENCE_DIR = Path(os.environ.get(
    "POLICY_INFERENCE_DIR", Path(__file__).resolve().parents[2] / "policy" / "inference"))

if str(INFERENCE_DIR) not in sys.path:
    sys.path.insert(0, str(INFERENCE_DIR))
try:
    import pick_client
    IMPORT_ERROR = None
except ImportError as e:  # the policy folder is not mounted / not on this machine
    pick_client = None
    IMPORT_ERROR = f"pick_client not found in {INFERENCE_DIR} ({e})"


def run_pick(item, instruction=None):
    """One GR00T episode for one unit of `item`. Returns (ok, message). Blocking.
    The worker takes the instruction from policy/instructions.json by item id; `instruction`
    is only logged by the caller."""
    if pick_client is None:
        return False, IMPORT_ERROR
    return pick_client.run_pick(item, instruction)


def stop_arm():
    """Halt the arm where it is. Returns (ok, message). Never raises."""
    if pick_client is None:
        return False, IMPORT_ERROR
    try:
        return pick_client.stop_arm()
    except Exception as e:
        return False, f"stop_arm failed: {e}"


def arm_ready():
    """True when the policy worker answers and its model is loaded."""
    if pick_client is None:
        return False
    try:
        return bool(pick_client.arm_ready())
    except Exception:
        return False


def arm_status():
    """The worker's /status (state, dry_run, model, last result...), or None."""
    if pick_client is None:
        return None
    try:
        return pick_client.status()
    except Exception:
        return None


# ---------- what a result means ----------

PLACED = "placed"          # the unit is in the basket, the gripper is empty
RETRY = "retry"            # an episode ran and failed (no grasp seen, time limit): a retry is allowed
HOLDING = "holding"        # the gripper still holds something: no retry, a person must check the arm
STOPPED = "stopped"        # /stop halted the episode: the arm holds where it is
NOT_READY = "not_ready"    # the worker is starting, unreachable or broken: no episode ran
REFUSED = "refused"        # the worker refused the request (unknown item, busy): no episode ran


def classify(ok, message):
    """Outcome of run_pick from its (ok, message). Matches policy/inference/pick_worker.py."""
    text = (message or "").lower()
    if "still holds something" in text:   # can come with ok=False only, after any ending
        return HOLDING
    if ok:
        return PLACED
    if text.startswith("stopped"):
        return STOPPED
    if text.startswith("no grasp seen") or text.startswith("time limit"):
        return RETRY
    if text.startswith("unknown item") or text.startswith("busy"):
        return REFUSED
    # "still starting", "not reachable", "cannot read instructions.json", "policy error",
    # pick_client missing, or a message we do not know: do not run the arm again blindly
    return NOT_READY
