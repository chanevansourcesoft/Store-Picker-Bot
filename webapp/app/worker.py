"""
Background worker that processes queued orders one at a time.

Start the worker with init_worker() - it runs in a background thread.
Use st.cache_resource in Streamlit so it only starts once per server.
"""

import threading
import time
import traceback
from typing import Callable, Optional

from app import robot_client, store
from app.agent import run_agent
from app.config import load_inventory

# Worker state
_worker_thread: Optional[threading.Thread] = None
_worker_running = False
_worker_lock = threading.Lock()


def process_single_order(order_id: int, on_step: Optional[Callable] = None):
    """
    Process one order. Updates the database with status, step_log, etc.
    Reduces stock only after items are successfully placed in basket,
    and never more than the ordered qty (an extra item is not sold).
    """
    order = store.get_order(order_id)
    if not order:
        return

    items = order["items"]
    inventory = load_inventory()

    # Ordered qty per item, and how many we already took from stock
    ordered = {}
    for entry in items:
        ordered[entry["item"]] = ordered.get(entry["item"], 0) + entry["qty"]
    taken = {}

    # Mark as packing
    store.update_order_status(order_id, "packing", current_task="Starting", station="home")

    def step_callback(entry):
        """Called after each tool call."""
        # Update the database with the step
        store.append_step_log(order_id, entry)

        # Update current task based on the tool
        tool = entry["tool"]
        result = entry["result"]

        if result.get("refused"):
            store.update_order_task(order_id, "Blocked a wrong command")
            if on_step:
                on_step(entry)
            return

        if tool == "navigate_to":
            station = entry["input"].get("station", "")
            if result.get("success"):
                store.update_order_task(order_id, f"At {station}", station=station)
            else:
                store.update_order_task(order_id, f"Navigation failed")

        elif tool == "pick_item":
            item = entry["input"].get("item", "")
            if result.get("success"):
                store.update_order_task(order_id, f"Picked {item}")
            else:
                store.update_order_task(order_id, f"Pick failed: {item}")

        elif tool == "scan_item":
            if result.get("success"):
                product = result.get("product", "?")
                store.update_order_task(order_id, f"Scanned {product}")
            else:
                store.update_order_task(order_id, "Scan failed")

        elif tool == "place_item":
            target = entry["input"].get("target", "")
            if result.get("success"):
                if target == "basket":
                    # Extract item name and reduce stock
                    msg = result.get("message", "")
                    import re
                    match = re.search(r"placed (\S+) in basket", msg)
                    if match:
                        item_name = match.group(1)
                        if taken.get(item_name, 0) < ordered.get(item_name, 0):
                            taken[item_name] = taken.get(item_name, 0) + 1
                            store.reduce_stock(item_name, 1)
                            store.update_order_task(order_id, f"Packed {item_name}")
                        else:
                            store.update_order_task(order_id, f"Extra {item_name} (not charged)")
                else:
                    store.update_order_task(order_id, "Returned item")
            else:
                store.update_order_task(order_id, "Place failed")

        # Call the external callback if provided
        if on_step:
            on_step(entry)

    try:
        # Build the order dict for the agent
        order_dict = {
            "items": items,
            "unknown": [],  # Already validated when order was created
        }

        # Run the agent
        bill, log = run_agent(order_dict, on_step=step_callback)

        # Complete the order (the agent decides done / done_with_problems / failed)
        store.complete_order(order_id, bill, bill.get("problems", []), status=bill.get("status"))

    except Exception as e:
        # Mark as failed
        error_msg = f"Worker error: {str(e)}"
        traceback.print_exc()
        store.fail_order(order_id, [error_msg])


def worker_loop():
    """Main worker loop. Processes queued orders one at a time."""
    global _worker_running

    while _worker_running:
        try:
            # Get next queued order
            order = store.get_next_queued_order()

            if order:
                print(f"[Worker] Processing order #{order['id']}")
                process_single_order(order["id"])
                print(f"[Worker] Finished order #{order['id']}")
            else:
                # No orders, wait a bit
                time.sleep(1)

        except Exception as e:
            print(f"[Worker] Error in main loop: {e}")
            traceback.print_exc()
            time.sleep(5)  # Wait before retrying


def start_worker():
    """Start the background worker thread."""
    global _worker_thread, _worker_running

    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return  # Already running

        _worker_running = True
        _worker_thread = threading.Thread(target=worker_loop, daemon=True, name="OrderWorker")
        _worker_thread.start()
        print("[Worker] Started background worker")


def stop_worker():
    """Stop the background worker thread."""
    global _worker_thread, _worker_running

    with _worker_lock:
        _worker_running = False
        if _worker_thread is not None:
            _worker_thread.join(timeout=5)
            _worker_thread = None
        print("[Worker] Stopped background worker")


def is_worker_running():
    """Check if the worker is running."""
    return _worker_running and _worker_thread is not None and _worker_thread.is_alive()


def init_worker():
    """
    Initialize the worker. Safe to call multiple times.
    Use with st.cache_resource in Streamlit:

        @st.cache_resource
        def get_worker():
            from app.worker import init_worker
            return init_worker()
    """
    store.init_db()                # creates data/ and the SQLite file, seeds inventory if empty
    robot_client.is_demo_mode()    # decide real robot or simulated robot now, at startup
    start_worker()
    return True


if __name__ == "__main__":
    # Test: start worker and create a test order
    import json

    print("Starting worker test...")

    # Initialize database
    store.init_db()

    # Start worker
    start_worker()

    # Create a test order
    items = [{"item": "soup_can", "qty": 1}]
    order_id, problems = store.create_order(items)

    if order_id:
        print(f"Created order #{order_id}")

        # Wait for it to complete
        for _ in range(120):  # Max 2 minutes
            time.sleep(1)
            order = store.get_order(order_id)
            print(f"  Status: {order['status']}, Task: {order.get('current_task')}")
            if order["status"] in store.FINISHED_STATUSES:
                print(f"\nFinal order: {json.dumps(order, indent=2)}")
                break
    else:
        print(f"Failed to create order: {problems}")

    stop_worker()
    print("Worker test done.")
