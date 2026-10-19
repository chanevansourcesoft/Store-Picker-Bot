"""
Helpers to read an order's step log (used by the owner dashboard).

A failed step is "recovered" when the same action on the same target
(same item, or same station) worked later, for example:
    pick soup_can -> grasp failed, then pick soup_can -> picked
"""


def held_item(log, index):
    """Item the robot holds at step `index` (from the last successful pick before it)."""
    for entry in reversed(log[:index]):
        if entry.get("tool") == "pick_item" and entry.get("result", {}).get("success"):
            return entry.get("input", {}).get("item")
    return None


def step_target(log, index):
    """What a step works on: the station for navigate, the item for pick/scan/place."""
    entry = log[index]
    data = entry.get("input", {}) or {}
    tool = entry.get("tool")
    if tool == "navigate_to":
        return data.get("station")
    if tool == "pick_item":
        return data.get("item")
    return held_item(log, index)


def is_recovered(log, index):
    """True if this failed step was done again later (same action, same target) and worked."""
    tool = log[index].get("tool")
    target = step_target(log, index)
    for later in range(index + 1, len(log)):
        entry = log[later]
        if entry.get("tool") == tool and step_target(log, later) == target \
                and entry.get("result", {}).get("success"):
            return True
    return False
