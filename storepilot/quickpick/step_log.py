"""
Helpers to read an order's step log (used by the owner dashboard).

A failed step is "recovered" when the same action on the same target
(same item, or same station) worked later, for example:
    pick chips_packet -> grasp failed, then pick chips_packet -> picked
"""

from store_agent.order_tracker import basket_item as shared_basket_item


def basket_item(entry):
    """Item a step put in the basket, else None: a pick_item the robot reported in the
    basket (structured fields). Orders saved before the structured fields existed only
    have the robot's message on place_item ("placed X in basket")."""
    item = shared_basket_item(entry)
    if item:
        return item
    result = entry.get("result") or {}
    if entry.get("tool") != "place_item" or not result.get("success") or result.get("item"):
        return None
    if (entry.get("input") or {}).get("target") != "basket":
        return None
    message = result.get("message") or ""
    return message.split(" ")[1] if message.startswith("placed ") and " " in message else None


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
