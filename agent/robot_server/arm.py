"""
The one place where the robot server moves the arm.

run_pick(item, instruction) runs ONE GR00T episode: from the arm's rest pose, pick
one unit of the item from its shelf, place it in the basket, and end in the rest
pose again. It returns (ok, message). After it returns, the arm holds nothing,
whether it worked or not.

INTEGRATION POINT (policy team): replace the stub body with the pick function from
policy/ (item id + instruction -> run GR00T -> success or failure), for example:

    from policy.pick import pick_item          # name to be agreed with the policy team
    return pick_item(item, instruction)

Until then this is a timed stub that moves nothing and always succeeds. That is why
StorePilot keeps live missions blocked (STOREPILOT_MANIPULATION_VERIFIED=0).
"""

import time

STUB_SECONDS = 1.5


def run_pick(item, instruction):
    """Pick `item` and place it in the basket with GR00T. Returns (ok, message). Blocking."""
    time.sleep(STUB_SECONDS)
    return True, f"placed {item} in basket (stub: no arm motion)"
