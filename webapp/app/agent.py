"""
Step 2.4: Agent loop — Nemotron picks and packs the order.

Contract v2: multiple shelves, basket instead of bag, route planning by shelf.

The agent:
1. Plans a route: visit each shelf once (in optimal order).
2. At each shelf: pick all items from that shelf (heavy first).
3. For each item: pick -> scan -> if barcode matches, basket; else return and retry.
4. After all items: navigate to customer, then home.
5. Returns a bill with items packed, total, and any problems.

The bill is built by CODE from successful place_item(basket) calls, not by the model.

Run:
    python -m app.agent "2 soup cans and one chips"
"""

import json
import re
import sys
from collections import defaultdict
from datetime import datetime

from app import robot_client
from app.config import MODEL_AGENT, get_client, load_inventory
from app.order_parser import parse_order
from app.tools import TOOLS, run_tool

MAX_STEPS = 60  # Stop the loop after this many tool calls.
MAX_RETRIES = 2  # Retry pick/scan this many times per item.
MAX_CONTINUE_ATTEMPTS = 3  # How many times to continue if order is incomplete.

# Weight order for sorting (heavy first, at bottom of basket).
WEIGHT_ORDER = {"heavy": 0, "medium": 1, "light": 2}


def strip_think(text):
    """Remove <think>...</think> from model output."""
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()


def suggest_similar(unknown_item, inventory):
    """Suggest a product only if it is really similar. Otherwise return None."""
    unknown_lower = unknown_item.lower().strip()

    # Only suggest if the unknown word is a substring of a product name
    # or the product name is a substring of the unknown word.
    for product in inventory:
        product_lower = product.lower()
        # Check if they share a meaningful root (at least 4 chars).
        if len(unknown_lower) >= 4 and unknown_lower in product_lower:
            return product
        if len(product_lower) >= 4 and product_lower.split("_")[0] in unknown_lower:
            return product

    return None


def sort_items_by_weight(items, inventory):
    """Sort items so heavy ones come first (go at bottom of basket)."""
    def weight_key(entry):
        item_name = entry["item"]
        weight = inventory.get(item_name, {}).get("weight", "medium")
        return WEIGHT_ORDER.get(weight, 1)

    return sorted(items, key=weight_key)


def group_items_by_shelf(items, inventory):
    """Group items by shelf and sort by weight within each shelf."""
    shelf_items = defaultdict(list)

    for entry in items:
        item_name = entry["item"]
        shelf = inventory.get(item_name, {}).get("shelf", "shelf_A")
        shelf_items[shelf].append(entry)

    # Sort each shelf's items by weight (heavy first)
    for shelf in shelf_items:
        shelf_items[shelf] = sort_items_by_weight(shelf_items[shelf], inventory)

    return dict(shelf_items)


def plan_shelf_route(shelf_items):
    """Plan the order to visit shelves. Returns list of (shelf, items) tuples."""
    # Simple order: A, B, C (could optimize for real positions later)
    shelf_order = ["shelf_A", "shelf_B", "shelf_C"]

    route = []
    for shelf in shelf_order:
        if shelf in shelf_items:
            route.append((shelf, shelf_items[shelf]))

    return route


def build_system_prompt(order, inventory, route, packed_so_far=None, continue_msg=None):
    """Create the system prompt for the agent."""
    items_str = ", ".join(f"{e['qty']}x {e['item']}" for e in order["items"])
    unknown_str = ", ".join(order["unknown"]) if order["unknown"] else "none"

    inv_lines = []
    for name, info in inventory.items():
        inv_lines.append(
            f"  {name}: shelf {info['shelf']}, bin {info['bin']}, barcode {info['barcode']}, "
            f"price {info['price']} INR, weight {info['weight']}"
        )

    # Build route description
    route_lines = []
    for shelf, items in route:
        items_str_shelf = ", ".join(f"{e['qty']}x {e['item']}" for e in items)
        route_lines.append(f"  {shelf}: {items_str_shelf}")

    prompt = f"""You are a mobile robot controller for a grocery warehouse.
The robot has a mecanum base and an SO-100 arm.

ORDER TO PACK:
- Items: {items_str or 'none'}
- Unknown (not in inventory): {unknown_str}

INVENTORY:
{chr(10).join(inv_lines)}

PLANNED ROUTE (visit shelves in this order):
{chr(10).join(route_lines) if route_lines else '  No items to pick'}

YOUR TASK:
1. Follow the planned route. For each shelf in the route:
   a. Call navigate_to with the shelf name (e.g. "shelf_A").
   b. Pick all items from that shelf (heavy first) before moving to the next shelf.
   c. For each item:
      - Call pick_item with the item name and a short instruction for the GR00T policy.
      - Call scan_item to read the barcode.
      - If the barcode matches, call place_item with target "basket".
      - If the barcode does NOT match or scan fails, call place_item with target "return" and retry (max {MAX_RETRIES} retries per unit).
      - If retries are exhausted, skip that unit and note the problem.
2. For unknown items, do NOT pick anything.
3. After all items are packed, call navigate_to with station "customer" to deliver.
4. Then call navigate_to with station "home" to return.
5. When everything is done, say "DONE" (the system will build the bill).

RULES:
- Only one pick at a time. The arm can hold only one item.
- Always scan after picking.
- ALWAYS call place_item (basket or return) before picking the next item or navigating.
- If a tool fails, read the error message and decide what to do.
- Pack heavy items first at each shelf, then medium, then light.
- Always write a short instruction for pick_item so the GR00T policy knows what to do."""

    if packed_so_far:
        prompt += f"\n\nALREADY PACKED: {packed_so_far}"

    if continue_msg:
        prompt += f"\n\nIMPORTANT: {continue_msg}"

    return prompt


def build_bill_from_log(log, order, inventory):
    """Build the bill from the step log. Only count successful place_item(basket) calls."""
    packed = {}  # item -> count

    for entry in log:
        if entry["tool"] == "place_item":
            target = entry["input"].get("target")
            result = entry["result"]
            if target == "basket" and result.get("success"):
                # Extract item name from the message "placed X in basket"
                msg = result.get("message", "")
                match = re.search(r"placed (\S+) in basket", msg)
                if match:
                    item = match.group(1)
                    packed[item] = packed.get(item, 0) + 1

    # Build bill items
    bill_items = []
    total = 0
    for item, qty in packed.items():
        price = inventory.get(item, {}).get("price", 0)
        bill_items.append({"item": item, "qty": qty, "price": price})
        total += price * qty

    return {"items": bill_items, "total": total, "problems": []}


def check_order_complete(order, packed_items, inventory):
    """Check if the order is complete. Returns (is_complete, missing_items)."""
    # Count what we need
    needed = {}
    for entry in order["items"]:
        item = entry["item"]
        needed[item] = needed.get(item, 0) + entry["qty"]

    # Count what we packed
    packed = {}
    for entry in packed_items:
        item = entry["item"]
        packed[item] = packed.get(item, 0) + entry["qty"]

    # Find missing
    missing = []
    for item, qty in needed.items():
        packed_qty = packed.get(item, 0)
        if packed_qty < qty:
            missing.append({"item": item, "qty": qty - packed_qty})

    return len(missing) == 0, missing


def run_agent_loop(messages, inventory, log, step_offset=0, client=None, on_step=None):
    """Run the agent loop. Returns (log, stopped_reason, step).

    on_step: optional callback(step_entry) called after each tool call.
    """
    if client is None:
        client = get_client()

    step = step_offset

    while step < MAX_STEPS:
        step += 1
        response = client.chat.completions.create(
            model=MODEL_AGENT,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=1000,
        )
        msg = response.choices[0].message

        # If the model wants to call tools
        if msg.tool_calls:
            # Add assistant message with tool calls to history
            messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ]
            })

            # Execute each tool call
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    tool_args = {}

                tool_result = run_tool(tool_name, tool_args)

                step_entry = {
                    "step": step,
                    "tool": tool_name,
                    "input": tool_args,
                    "result": json.loads(tool_result),
                    "time": datetime.now().isoformat(),
                }
                log.append(step_entry)

                # Call the callback if provided
                if on_step:
                    on_step(step_entry)

                # Add tool result to messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })

        # If the model is done (no tool calls)
        else:
            content = strip_think(msg.content)
            messages.append({"role": "assistant", "content": content})

            # Check if model said DONE
            if "DONE" in content.upper():
                return log, "done", step

            # Ask it to continue or finish
            messages.append({"role": "user", "content": "Continue packing or say DONE if finished."})

    return log, "max_steps", step


def run_agent(order, client=None, on_step=None):
    """
    Run the agent loop. Returns:
    - bill: dict with items, total, problems
    - log: list of steps [{tool, input, result}, ...]

    on_step: optional callback(step_entry) called after each tool call.
    """
    if client is None:
        client = get_client()
    inventory = load_inventory()

    # Group items by shelf and sort by weight within each shelf
    shelf_items = group_items_by_shelf(order["items"], inventory)

    # Plan the route
    route = plan_shelf_route(shelf_items)

    # Build sorted order (for reference)
    sorted_items = []
    for shelf, items in route:
        sorted_items.extend(items)

    sorted_order = {
        "items": sorted_items,
        "unknown": order.get("unknown", []),
    }

    # Build initial messages
    system_prompt = build_system_prompt(sorted_order, inventory, route)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Start packing the order now. Follow the planned route, visiting each shelf once."},
    ]

    log = []
    problems = []
    continue_attempts = 0
    total_steps = 0

    while continue_attempts < MAX_CONTINUE_ATTEMPTS:
        log, stopped_reason, total_steps = run_agent_loop(
            messages, inventory, log, step_offset=total_steps, client=client, on_step=on_step
        )

        # Build bill from log
        bill = build_bill_from_log(log, sorted_order, inventory)

        # Check robot status
        status = robot_client.status()
        holding = status.get("holding")

        # Check if order is complete
        is_complete, missing = check_order_complete(sorted_order, bill["items"], inventory)

        # If robot is still holding something, that's a problem
        if holding:
            continue_attempts += 1
            if continue_attempts < MAX_CONTINUE_ATTEMPTS:
                # Tell agent to place the item
                messages.append({
                    "role": "user",
                    "content": f"The robot is still holding {holding}. You must call place_item before finishing. Place it in basket if it was scanned correctly, otherwise return it."
                })
                continue
            else:
                problems.append(f"Robot still holding {holding} at end.")
                # Force return the item
                robot_client.place("return")

        # If order is not complete, try to continue
        if not is_complete and continue_attempts < MAX_CONTINUE_ATTEMPTS:
            continue_attempts += 1
            missing_str = ", ".join(f"{m['qty']}x {m['item']}" for m in missing)
            messages.append({
                "role": "user",
                "content": f"Order is not complete. Still need: {missing_str}. Continue packing."
            })
            continue

        # Add missing items to problems
        if not is_complete:
            for m in missing:
                problems.append(f"Could not pack {m['qty']}x {m['item']}.")

        break

    # Final bill
    bill = build_bill_from_log(log, sorted_order, inventory)
    bill["problems"] = problems

    if stopped_reason == "max_steps":
        bill["problems"].append("Agent stopped: too many steps.")

    return bill, log


def process_order(order_text, on_step=None, on_parsed=None):
    """Parse the order and run the agent. Returns bill and log.

    on_step: optional callback(step_entry) called after each tool call.
    on_parsed: optional callback(parsed_order) called after order is parsed.
    """
    parsed = parse_order(order_text)

    # Call on_parsed callback if provided
    if on_parsed:
        on_parsed(parsed)

    # Handle unknown items (no duplicates, smart suggestions only)
    inventory = load_inventory()
    problems = []
    seen_unknown = set()

    for item in parsed.get("unknown", []):
        item_lower = item.lower().strip()
        if item_lower in seen_unknown:
            continue
        seen_unknown.add(item_lower)

        suggestion = suggest_similar(item, inventory)
        if suggestion:
            problems.append(f"'{item}' not found. Did you mean '{suggestion}'?")
        else:
            problems.append(f"'{item}' is not available.")

    # If no items to pack, return early
    if not parsed.get("items"):
        return {
            "items": [],
            "total": 0,
            "problems": problems or ["No items to pack."],
        }, []

    bill, log = run_agent(parsed, on_step=on_step)

    # Add unknown item problems to the bill (at the end)
    if problems:
        bill["problems"] = bill.get("problems", []) + problems

    return bill, log


def main():
    order_text = " ".join(sys.argv[1:]) or "2 soup cans and one chips"
    print(f"Order: {order_text}\n")

    print("Parsing order...")
    parsed = parse_order(order_text)
    print(f"Parsed: {json.dumps(parsed, indent=2)}\n")

    print("Running agent...")
    bill, log = process_order(order_text)

    print("\n--- STEP LOG ---")
    for entry in log:
        print(f"[{entry['step']}] {entry['tool']}({entry['input']}) -> {entry['result']}")

    print("\n--- FINAL BILL ---")
    print(json.dumps(bill, indent=2))


if __name__ == "__main__":
    main()
