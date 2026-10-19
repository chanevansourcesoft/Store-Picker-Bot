"""
Step 2.4: Agent loop — Nemotron picks and packs the order.

Contract v2: multiple shelves, basket instead of bag, route planning by shelf.

The agent:
1. Plans a route: visit each shelf once (in optimal order).
2. At each shelf: pick all items from that shelf (heavy first).
3. For each unit: pick_item = ONE GR00T run that picks it AND places it in the basket.
   A failed pick is retried; after too many failures the unit is skipped.
4. After all items: navigate to customer, then home.
5. Returns a bill with items packed, total, and any problems.

Nemotron only chooses item ids. The robot looks up the GR00T instruction in
policy/instructions.json. There is no barcode scan.

The bill is built by CODE from the units the robot reported in the basket, not by the model.
The CODE keeps the order truth (OrderTracker): it refuses wrong robot commands and
sends Nemotron a progress line after every tool call, so the model cannot lose count.

Final status: "done" (exactly as ordered), "done_with_problems" (some items packed,
but missing/extra items), or "failed" (nothing packed).

Run:
    python -m store_agent.agent "2 chips and one hide and seek"
"""

import json
import re
import sys
from collections import defaultdict
from datetime import datetime

from store_agent import robot_client
from store_agent.autopilot import Autopilot
from store_agent.config import MODEL_AGENT, get_client
from store_agent.inventory import load_inventory
from store_agent.order_parser import parse_order
from store_agent.order_tracker import OrderTracker, basket_item
from store_agent.tools import TOOLS, run_tool

MAX_STEPS = 60  # Stop the loop after this many tool calls.
MAX_RETRIES = 2  # Retry a failed pick this many times per unit.
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
   c. For each unit: call pick_item with the item id. One pick_item picks ONE unit and
      places it in the basket. Call it once per unit (2x chips_packet = 2 calls).
      If it fails, call it again (max {MAX_RETRIES} retries per unit). If retries are
      exhausted, skip that unit (the system notes the problem).
2. For unknown items, do NOT pick anything.
3. After all items are packed, call navigate_to with station "customer" to deliver.
4. Then call navigate_to with station "home" to return.
5. When everything is done, say "DONE" (the system will build the bill).

RULES:
- pick_item takes only the item id. The robot knows how to pick each product.
- The robot must be at the item's shelf before pick_item.
- If a tool fails, read the error message and decide what to do.
- Pack heavy items first at each shelf, then medium, then light.
- After every tool result the system adds a "Progress:" line. It is the truth.
  Trust it more than your memory: it says what is already in the basket.
- If a command result says "REFUSED", the robot did NOT do it. Read why and follow the advice."""

    if packed_so_far:
        prompt += f"\n\nALREADY PACKED: {packed_so_far}"

    if continue_msg:
        prompt += f"\n\nIMPORTANT: {continue_msg}"

    return prompt


def ordered_counts(order):
    """{"chips_packet": 2, ...} from the order items (duplicates added up)."""
    counts = {}
    for entry in order["items"]:
        counts[entry["item"]] = counts.get(entry["item"], 0) + entry["qty"]
    return counts


def build_bill_from_log(log, order, inventory):
    """Build the bill from the step log. Only count units the robot reported in the basket.

    Never charge more than the customer paid: each item is billed only up to the
    ordered qty. Extra items are listed as problems ("1 extra chips_packet, not charged").
    """
    packed = {}  # item -> count

    for entry in log:
        item = basket_item(entry)
        if item:
            packed[item] = packed.get(item, 0) + 1

    ordered = ordered_counts(order)
    bill_items = []
    problems = []
    total = 0
    for item, qty in packed.items():
        charged = min(qty, ordered.get(item, 0))
        extra = qty - charged
        price = inventory.get(item, {}).get("price", 0)
        if charged:
            bill_items.append({"item": item, "qty": charged, "price": price})
            total += price * charged
        if extra:
            problems.append(f"{extra} extra {item}, not charged")

    paid = sum(qty * inventory.get(item, {}).get("price", 0) for item, qty in ordered.items())
    return {"items": bill_items, "total": total, "paid": paid, "problems": problems}


def check_order_complete(order, packed_items, holding=None):
    """Check the order against what is in the basket.

    packed_items: [{"item", "qty"}] really in the basket (charged or not).
    Returns (is_complete, missing, extra). Complete = packed equals ordered for
    every item, nothing extra, and the robot holds nothing.
    """
    needed = ordered_counts(order)

    # Count what we packed
    packed = {}
    for entry in packed_items:
        item = entry["item"]
        packed[item] = packed.get(item, 0) + entry["qty"]

    missing = []
    for item, qty in needed.items():
        packed_qty = packed.get(item, 0)
        if packed_qty < qty:
            missing.append({"item": item, "qty": qty - packed_qty})

    extra = []
    for item, qty in packed.items():
        if qty > needed.get(item, 0):
            extra.append({"item": item, "qty": qty - needed.get(item, 0)})

    is_complete = not missing and not extra and not holding
    return is_complete, missing, extra


def run_agent_loop(messages, inventory, log, step_offset=0, client=None, on_step=None, tracker=None):
    """Run the agent loop. Returns (log, stopped_reason, step).

    on_step: optional callback(step_entry) called after each tool call.
    tracker: optional OrderTracker (guards + progress line after every tool call).
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

                tool_result = run_tool(tool_name, tool_args, tracker)

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

                # Add tool result to messages, with the progress line from the code
                content = tool_result
                if tracker is not None:
                    content += "\n" + tracker.progress_line()
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": content,
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


def final_status(tracker, problems):
    """done = exactly as ordered and no problems; done_with_problems = something packed; else failed."""
    if tracker.is_complete() and not problems:
        return "done"
    if tracker.packed_count() > 0:
        return "done_with_problems"
    return "failed"


def run_agent(order, client=None, on_step=None):
    """
    Run the agent loop. Returns:
    - bill: dict with items, total, paid, problems, status
    - log: list of steps [{tool, input, result}, ...]

    on_step: optional callback(step_entry) called after each tool call.
    """
    if client is None:
        try:
            client = get_client()
        except RuntimeError:  # no NEBIUS_API_KEY: plan with the offline autopilot
            client = Autopilot()
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

    # The code keeps the truth about this order (not the model)
    tracker = OrderTracker(sorted_items, inventory)
    tracker.sync_from_status(robot_client.status())
    if hasattr(client, "attach"):  # offline autopilot reads the tracker
        client.attach(tracker)

    # Build initial messages
    system_prompt = build_system_prompt(sorted_order, inventory, route)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Start packing the order now. Follow the planned route, visiting each shelf once.\n"
                                    + tracker.progress_line()},
    ]

    log = []
    problems = []
    continue_attempts = 0
    total_steps = 0
    stopped_reason = None

    while True:
        log, stopped_reason, total_steps = run_agent_loop(
            messages, inventory, log, step_offset=total_steps, client=client, on_step=on_step, tracker=tracker
        )
        tracker.sync_from_status(robot_client.status())

        if tracker.missing():
            hint = "Order is not complete. Still need: " + ", ".join(
                f"{m['qty']}x {m['item']}" for m in tracker.missing()) + ". Continue packing."
        else:
            break  # everything packed (extra items cannot be taken back out of the basket)

        continue_attempts += 1
        if continue_attempts >= MAX_CONTINUE_ATTEMPTS or stopped_reason == "max_steps":
            break
        messages.append({"role": "user", "content": hint + "\n" + tracker.progress_line()})

    # A pick always ends with the arm empty. If the robot still reports an item in the
    # gripper, something went wrong on the arm: report it, do not move the robot.
    if tracker.holding:
        problems.append(f"Robot reports it still holds {tracker.holding}: the operator must check the arm.")

    for m in tracker.missing():
        problems.append(f"Could not pack {m['qty']}x {m['item']}.")

    if stopped_reason == "max_steps":
        problems.append("Agent stopped: too many steps.")

    # Final bill (charged only up to the ordered qty; extras are listed as problems)
    bill = build_bill_from_log(log, sorted_order, inventory)
    bill["problems"] = bill["problems"] + problems
    bill["status"] = final_status(tracker, bill["problems"])
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
    order_text = " ".join(sys.argv[1:]) or "2 chips and one hide and seek"
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
