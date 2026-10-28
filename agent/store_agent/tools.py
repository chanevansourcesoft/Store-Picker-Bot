"""
Step 2.3: Tools that Nemotron can call.

Contract v2: multiple shelves, basket.

TOOLS     -> the list we send to Nemotron (name, what it does, inputs)
run_tool  -> runs the tool Nemotron picked and returns the result as text

pick_item runs ONE GR00T episode on the robot: pick the item and place it in the
basket. Nemotron only chooses the item id; the robot looks up the GR00T instruction
in policy/instructions.json. There is no barcode scan.

Before a pick reaches the robot, the code checks that the item is a product id from the
inventory (no empty ids, no free text). With an OrderTracker it also checks the guards
(no picking too many, not at the wrong station, not while the robot holds something).
A refused command is NOT sent to the robot: Nemotron gets "REFUSED: ..." back.

A failed pick is retried by the CODE, not the model: up to PICK_RETRIES more tries for the
unit, while the robot says the failure is retryable (an episode ran: no grasp seen, time
limit). Then the result says "not_picked": the item is reported and the order goes on.
If the gripper still holds something ("needs_operator"), there is no retry at all.
"""

import json

from store_agent import robot_client
from store_agent.config import PICK_RETRIES
from store_agent.inventory import load_inventory

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_inventory",
            "description": "List all products in the store with their shelf, bin, price and weight (heavy/medium/light).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "navigate_to",
            "description": "Move the robot to a station. Use shelf_A/B/C to go to specific shelves, 'customer' for delivery, 'home' to return.",
            "parameters": {
                "type": "object",
                "properties": {
                    "station": {
                        "type": "string",
                        "enum": ["home", "shelf_A", "shelf_B", "shelf_C", "customer"],
                        "description": "The station to go to: 'shelf_A/B/C' for picking, 'customer' for delivery, 'home' to return."
                    }
                },
                "required": ["station"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pick_item",
            "description": "The robot arm picks ONE unit of a product from its shelf and places it in the customer basket "
                           "(one complete arm motion). The robot must be at the product's shelf. "
                           "On success the unit is in the basket. A failed pick is retried automatically; "
                           "if the result says not_picked, do not pick that product again.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "Product id from the inventory, e.g. 'ink_box'"},
                },
                "required": ["item"],
            },
        },
    },
]


def refused(message):
    """Result for a command the code did not send to the robot."""
    return {"success": False, "refused": True, "message": message}


def check_item(item, inventory):
    """None if `item` is a product id from the inventory, else a REFUSED message.
    Nemotron must send ids only: never an empty id or a sentence."""
    if not isinstance(item, str) or not item.strip():
        return "REFUSED: pick_item needs a product id from the inventory, e.g. " + ", ".join(sorted(inventory)) + "."
    if item not in inventory:
        return (f"REFUSED: '{item[:60]}' is not a product id. Send exactly one id from the inventory: "
                + ", ".join(sorted(inventory)) + ".")
    return None


def pick_with_retries(item, shelf, should_stop=None):
    """Pick one unit, retrying a retryable failure up to PICK_RETRIES times.

    The try number comes from the robot ("pick_try"). The result is the robot's last answer,
    plus "tries", "attempts" (every answer's try and message) and, when the unit was given up,
    "not_picked": True. should_stop: optional callable; no new try is sent once it returns True.
    """
    tries = 1 + PICK_RETRIES
    attempts = []
    while True:
        result = robot_client.pick(item, shelf)
        attempt = result.get("pick_try") or len(attempts) + 1
        attempts.append({"pick_try": attempt, "message": result.get("message", "")})
        if result.get("success"):
            break
        if result.get("needs_operator"):          # the gripper still holds something: never retry
            result["not_picked"] = True
            break
        if result.get("retryable") is False:      # no episode ran (wrong place, not ready, stopped...)
            break
        if attempt >= tries:
            result["not_picked"] = True
            result["message"] = f"not picked after {attempt} tries: {result.get('message', '')}"
            break
        if should_stop is not None and should_stop():
            break
    result["tries"] = len(attempts)
    result["attempts"] = attempts
    return result


def run_tool(name, args, tracker=None, should_stop=None):
    """Run one tool and return the result as a JSON string (for the model).

    tracker: optional OrderTracker. It can refuse the command, and it is
    updated with the real robot result.
    should_stop: optional callable (STOP button): no further pick try once it returns True.
    """
    inventory = load_inventory()

    # Guards: stop mistakes before they reach the robot
    if name == "pick_item":
        reason = check_item(args.get("item"), inventory)
        if reason is None and tracker is not None:
            reason = tracker.check_pick(args["item"])
        if reason:
            result = refused(reason)
            if tracker is not None:
                tracker.record(name, args, result)
            return json.dumps(result)

    if name == "get_inventory":
        result = inventory

    elif name == "navigate_to":
        station = args.get("station", "")
        result = robot_client.navigate(station)

    elif name == "pick_item":
        item = args["item"]
        result = pick_with_retries(item, inventory[item]["shelf"], should_stop)

    else:
        result = {"success": False, "message": f"unknown tool {name}"}

    # Structured result: the bill, the stock and the tracker read "item" and "target"
    # (where the unit went), never the message text.
    if result.get("success") and name == "pick_item" and not result.get("item"):
        result["item"] = args.get("item")

    if tracker is not None:
        tracker.record(name, args, result)
    return json.dumps(result)


if __name__ == "__main__":
    # Quick test without Nemotron: run one full pick by hand.
    print("health   :", robot_client.health())
    print("status   :", robot_client.status())
    print("navigate :", run_tool("navigate_to", {"station": "shelf_A"}))
    print("pick     :", run_tool("pick_item", {"item": "ink_box"}))
    print("navigate :", run_tool("navigate_to", {"station": "customer"}))
    print("navigate :", run_tool("navigate_to", {"station": "home"}))
