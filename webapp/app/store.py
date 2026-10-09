"""
SQLite helper for orders and stock.

Thread-safe: uses WAL mode and proper locking for concurrent access.
"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import ensure_inventory, load_inventory

# Database file path
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "orders.db"

# Thread-local storage for connections
_local = threading.local()

# Lock for write operations
_write_lock = threading.Lock()

# True after we checked inventory.json once (see init_db)
_inventory_checked = False


def _get_connection():
    """Get a thread-local database connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for better concurrent access
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return _local.conn


@contextmanager
def _transaction():
    """Context manager for write transactions with locking."""
    conn = _get_connection()
    with _write_lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def init_db():
    """Create data/, the SQLite file and the orders table if missing.
    Also writes the starting inventory if inventory.json is missing or empty."""
    global _inventory_checked
    if not _inventory_checked:  # only once: init_db() is called on every page load
        _inventory_checked = True
        if ensure_inventory():
            print("[Store] Inventory was empty, wrote the starting stock.")
    conn = _get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            items TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            step_log TEXT NOT NULL DEFAULT '[]',
            bill TEXT NOT NULL DEFAULT '{}',
            problems TEXT NOT NULL DEFAULT '[]',
            current_task TEXT,
            station TEXT
        )
    """)
    conn.commit()


def _row_to_dict(row):
    """Convert a sqlite3.Row to a dict with JSON fields parsed."""
    if row is None:
        return None
    d = dict(row)
    # Parse JSON fields
    for field in ["items", "step_log", "bill", "problems"]:
        if field in d and d[field]:
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


# --- Stock functions ---

def get_stock():
    """Get current stock levels from inventory.json."""
    inventory = load_inventory()
    return {name: info.get("stock", 0) for name, info in inventory.items()}


def check_stock(items):
    """
    Check if there is enough stock for the order.
    Returns (ok, problems) where problems is a list of error messages.
    """
    inventory = load_inventory()
    problems = []

    for entry in items:
        item = entry["item"]
        qty = entry["qty"]
        if item not in inventory:
            problems.append(f"'{item}' is not in inventory.")
        else:
            stock = inventory[item].get("stock", 0)
            if stock < qty:
                problems.append(f"Not enough '{item}': need {qty}, have {stock}.")

    return len(problems) == 0, problems


def reduce_stock(item, qty=1):
    """
    Reduce stock for an item. Called ONLY after item is placed in basket.
    Updates inventory.json file.
    Returns True if successful, False if not enough stock.
    """
    inventory_path = Path(__file__).resolve().parent / "inventory.json"

    with _write_lock:
        # Read current inventory
        inventory = json.loads(inventory_path.read_text())

        if item not in inventory:
            return False

        current_stock = inventory[item].get("stock", 0)
        if current_stock < qty:
            return False

        # Reduce stock
        inventory[item]["stock"] = current_stock - qty

        # Write back
        inventory_path.write_text(json.dumps(inventory, indent=2) + "\n")

    return True


def restore_stock(item, qty=1):
    """Restore stock for an item (e.g., when order is cancelled)."""
    inventory_path = Path(__file__).resolve().parent / "inventory.json"

    with _write_lock:
        inventory = json.loads(inventory_path.read_text())
        if item not in inventory:
            return False
        inventory[item]["stock"] = inventory[item].get("stock", 0) + qty
        inventory_path.write_text(json.dumps(inventory, indent=2) + "\n")

    return True


# --- Order functions ---

def create_order(items):
    """
    Create a new order. Returns (order_id, None) on success,
    or (None, problems) if stock check fails.
    """
    # Check stock first
    ok, problems = check_stock(items)
    if not ok:
        return None, problems

    init_db()

    with _transaction() as conn:
        cursor = conn.execute(
            """
            INSERT INTO orders (created_at, items, status)
            VALUES (?, ?, 'queued')
            """,
            (datetime.now().isoformat(), json.dumps(items))
        )
        order_id = cursor.lastrowid

    return order_id, None


def get_order(order_id):
    """Get an order by ID. Returns None if not found."""
    init_db()
    conn = _get_connection()
    row = conn.execute(
        "SELECT * FROM orders WHERE id = ?", (order_id,)
    ).fetchone()
    return _row_to_dict(row)


def get_orders(status=None, limit=50):
    """Get orders, optionally filtered by status."""
    init_db()
    conn = _get_connection()

    if status:
        rows = conn.execute(
            "SELECT * FROM orders WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()

    return [_row_to_dict(row) for row in rows]


def get_next_queued_order():
    """Get the oldest queued order. Returns None if no queued orders."""
    init_db()
    conn = _get_connection()
    row = conn.execute(
        "SELECT * FROM orders WHERE status = 'queued' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    return _row_to_dict(row)


def update_order_status(order_id, status, current_task=None, station=None):
    """Update order status and optionally current_task and station."""
    init_db()
    with _transaction() as conn:
        conn.execute(
            """
            UPDATE orders
            SET status = ?, current_task = ?, station = ?
            WHERE id = ?
            """,
            (status, current_task, station, order_id)
        )


def update_order_task(order_id, current_task, station=None):
    """Update the current task and station for an order."""
    init_db()
    with _transaction() as conn:
        if station is not None:
            conn.execute(
                "UPDATE orders SET current_task = ?, station = ? WHERE id = ?",
                (current_task, station, order_id)
            )
        else:
            conn.execute(
                "UPDATE orders SET current_task = ? WHERE id = ?",
                (current_task, order_id)
            )


def append_step_log(order_id, step_entry):
    """Append a step to the order's step_log."""
    init_db()
    with _transaction() as conn:
        row = conn.execute(
            "SELECT step_log FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if row:
            try:
                log = json.loads(row["step_log"])
            except (json.JSONDecodeError, TypeError):
                log = []
            log.append(step_entry)
            conn.execute(
                "UPDATE orders SET step_log = ? WHERE id = ?",
                (json.dumps(log), order_id)
            )


def complete_order(order_id, bill, problems=None):
    """Mark an order as done with the final bill."""
    init_db()
    status = "done" if not problems else "failed"
    with _transaction() as conn:
        conn.execute(
            """
            UPDATE orders
            SET status = ?, bill = ?, problems = ?, current_task = NULL
            WHERE id = ?
            """,
            (status, json.dumps(bill), json.dumps(problems or []), order_id)
        )


def fail_order(order_id, problems):
    """Mark an order as failed with error messages."""
    init_db()
    with _transaction() as conn:
        conn.execute(
            """
            UPDATE orders
            SET status = 'failed', problems = ?, current_task = NULL
            WHERE id = ?
            """,
            (json.dumps(problems), order_id)
        )


def delete_order(order_id):
    """Delete an order (for testing)."""
    init_db()
    with _transaction() as conn:
        conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))


def clear_all_orders():
    """Delete all orders (for testing)."""
    init_db()
    with _transaction() as conn:
        conn.execute("DELETE FROM orders")


# --- Dashboard helper functions ---

def get_orders_today():
    """Get all orders created today."""
    init_db()
    conn = _get_connection()
    today = datetime.now().strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT * FROM orders WHERE created_at LIKE ? ORDER BY id DESC",
        (f"{today}%",)
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_stats_today():
    """Get today's statistics: total orders, done orders, queued orders, revenue."""
    orders = get_orders_today()

    total = len(orders)
    done = sum(1 for o in orders if o["status"] == "done")
    queued = sum(1 for o in orders if o["status"] == "queued")
    packing = sum(1 for o in orders if o["status"] == "packing")

    # Calculate revenue from completed orders
    revenue = 0
    for o in orders:
        if o["status"] == "done":
            bill = o.get("bill", {})
            if isinstance(bill, dict):
                revenue += bill.get("total", 0)

    return {
        "total": total,
        "done": done,
        "queued": queued,
        "packing": packing,
        "revenue": revenue,
    }


def get_current_packing_order():
    """Get the order currently being packed (status='packing')."""
    init_db()
    conn = _get_connection()
    row = conn.execute(
        "SELECT * FROM orders WHERE status = 'packing' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    return _row_to_dict(row)


def add_stock(item, qty=10):
    """Add stock to an item (for restocking)."""
    inventory_path = Path(__file__).resolve().parent / "inventory.json"

    with _write_lock:
        inventory = json.loads(inventory_path.read_text())
        if item not in inventory:
            return False
        inventory[item]["stock"] = inventory[item].get("stock", 0) + qty
        inventory_path.write_text(json.dumps(inventory, indent=2) + "\n")

    return True
