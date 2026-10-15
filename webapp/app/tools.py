"""
Step 2.3: Tools that Nemotron can call.

Contract v2: multiple shelves, basket instead of bag.

TOOLS     -> the list we send to Nemotron (name, what it does, inputs)
run_tool  -> runs the tool Nemotron picked and returns the result as text

With an OrderTracker, run_tool first checks the guards (no picking too many,
no basket for a complete item, return only at the item's own shelf).
A refused command is NOT sent to the robot: Nemotron gets "REFUSED: ..." back.
"""

import json

from app import robot_client
from app.config import load_inventory

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
            "description": "Tell the robot arm to pick ONE unit of a product from its shelf. The arm can hold only one item at a time. Write a short instruction for the GR00T pick policy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "Product name from the inventory"},
                    "instruction": {"type": "string", "description": "Short text instruction for the pick policy, e.g. 'pick up the soup can'"}
                },
                "required": ["item"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scan_item",
            "description": "Scan the barcode of the item the arm is holding. Returns the barcode and which product it belongs to.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "place_item",
            "description": "Put the held item in the customer basket ('basket') or back on its shelf ('return').",
            "parameters": {
                "type": "object",
                "properties": {"target": {"type": "string", "enum": ["basket", "return"]}},
                "required": ["target"],
            },
        },
    },
]


def _product_for_barcode(barcode, inventory):
    for name, info in inventory.items():
        if info["barcode"] == barcode:
            return name
    return None


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
    if tracker is not None:
        if name == "pick_item":
            reason = tracker.check_pick(args.get("item", ""))
        elif name == "place_item":
            reason = tracker.check_place(args.get("target", ""))
        else:
            reason = None
        if reason:
            return json.dumps(refused(reason))

    if name == "get_inventory":
        result = inventory

    elif name == "navigate_to":
        station = args.get("station", "")
        result = robot_client.navigate(station)

    elif name == "pick_item":
        item = args.get("item", "")
        instruction = args.get("instruction")
        if item not in inventory:
            result = {"success": False, "message": f"{item} is not in the inventory"}
        else:
            shelf = inventory[item]["shelf"]
            result = robot_client.pick(item, shelf, instruction)

    elif name == "scan_item":
        result = robot_client.scan()
        if result.get("barcode"):
            result["product"] = _product_for_barcode(result["barcode"], inventory)

    elif name == "place_item":
        result = robot_client.place(args.get("target", ""))

    else:
        result = {"success": False, "message": f"unknown tool {name}"}

    if tracker is not None:
        tracker.record(name, args, result)
    return json.dumps(result)


if __name__ == "__main__":
    # Quick test without Nemotron: run one full pick by hand.
    print("health   :", robot_client.health())
    print("status   :", robot_client.status())
    print("navigate :", run_tool("navigate_to", {"station": "shelf_A"}))
    print("pick     :", run_tool("pick_item", {"item": "soup_can", "instruction": "pick up the soup can"}))
    print("scan     :", run_tool("scan_item", {}))
    print("place    :", run_tool("place_item", {"target": "basket"}))
    print("navigate :", run_tool("navigate_to", {"station": "customer"}))
    print("navigate :", run_tool("navigate_to", {"station": "home"}))
