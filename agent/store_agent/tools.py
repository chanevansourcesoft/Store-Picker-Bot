"""
Step 2.3: Tools that Nemotron can call.

Contract v2: multiple shelves, basket.

TOOLS     -> the list we send to Nemotron (name, what it does, inputs)
run_tool  -> runs the tool Nemotron picked and returns the result as text

pick_item runs ONE GR00T episode on the robot: pick the item and place it in the
basket. Nemotron only chooses the item id; the robot looks up the GR00T instruction
in policy/instructions.json. There is no barcode scan.

With an OrderTracker, run_tool first checks the guards (no picking too many,
no picking while the robot holds something). A refused command is NOT sent to
the robot: Nemotron gets "REFUSED: ..." back.
"""

import json

from store_agent import robot_client
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
                           "On success the unit is in the basket. On failure nothing is in the basket and you may retry.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "Product id from the inventory, e.g. 'chips_packet'"},
                },
                "required": ["item"],
            },
        },
    },
]


def refused(message):
    """Result for a command the code did not send to the robot."""
    return {"success": False, "refused": True, "message": message}


def run_tool(name, args, tracker=None):
    """Run one tool and return the result as a JSON string (for the model).

    tracker: optional OrderTracker. It can refuse the command, and it is
    updated with the real robot result.
    """
    inventory = load_inventory()

    # Guards: stop mistakes before they reach the robot
    if tracker is not None and name == "pick_item":
        reason = tracker.check_pick(args.get("item", ""))
        if reason:
            return json.dumps(refused(reason))

    if name == "get_inventory":
        result = inventory

    elif name == "navigate_to":
        station = args.get("station", "")
        result = robot_client.navigate(station)

    elif name == "pick_item":
        item = args.get("item", "")
        if item not in inventory:
            result = {"success": False, "message": f"{item} is not in the inventory"}
        else:
            result = robot_client.pick(item, inventory[item]["shelf"])

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
    print("pick     :", run_tool("pick_item", {"item": "chips_packet"}))
    print("navigate :", run_tool("navigate_to", {"station": "customer"}))
    print("navigate :", run_tool("navigate_to", {"station": "home"}))
