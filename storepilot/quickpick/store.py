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

from store_agent import inventory as stock_store
from store_agent.inventory import ensure_stock, load_inventory
from store_agent.order_tracker import unpacked_units

# Order statuses: queued -> packing -> one of FINISHED_STATUSES (or queued -> cancelled)
FINISHED_STATUSES = ("done", "done_with_problems", "failed", "cancelled")
CHARGED_STATUSES = ("done", "done_with_problems")  # the bill counts as revenue

# Database file path
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "orders.db"

# Thread-local storage for connections
_local = threading.local()

# Lock for write operations
_write_lock = threading.Lock()

# True after we checked the stock file once (see init_db)
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
    Also writes the starting stock if the stock file is missing."""
    global _inventory_checked
    if not _inventory_checked:  # only once: init_db() is called on every page load
        _inventory_checked = True
        if ensure_stock():
            print("[Store] No stock file yet, wrote the starting stock.")
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
            station TEXT,
            stock_reserved INTEGER NOT NULL DEFAULT 0
        )
    """)
    # Databases from before stock reservation: add the column (old orders = not reserved)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(orders)")]
    if "stock_reserved" not in columns:
        conn.execute("ALTER TABLE orders ADD COLUMN stock_reserved INTEGER NOT NULL DEFAULT 0")
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
    """Current stock levels (live stock file, see store_agent.inventory)."""
    return stock_store.get_stock()


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
    Returns True if successful, False if not enough stock.
    """
    return stock_store.take_stock(item, qty)


def restore_stock(item, qty=1):
    """Restore stock for an item (e.g., when order is cancelled)."""
    return stock_store.add_stock(item, qty)


# --- Order functions ---

def create_order(items):
    """
    Create a new order and reserve its stock at once (all or nothing), so two
    orders can never both get the last unit. Returns (order_id, None) on success,
    or (None, problems) if there is not enough stock.
    Units that are not packed go back to stock when the order ends (release_unpacked).
    """
    ok, problems = stock_store.reserve(items)
    if not ok:
        return None, problems

    init_db()

    try:
        with _transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO orders (created_at, items, status, stock_reserved)
                VALUES (?, ?, 'queued', 1)
                """,
                (datetime.now().isoformat(), json.dumps(items))
            )
            order_id = cursor.lastrowid
    except Exception:
        stock_store.release(items)
        raise

    return order_id, None


def release_unpacked(order):
    """Give back the reserved stock of every unit this order did not put in the basket.
    Only once per order, and only for orders that reserved stock. Returns {item: qty}."""
    init_db()
    with _transaction() as conn:
        cursor = conn.execute(
            "UPDATE orders SET stock_reserved = 0 WHERE id = ? AND stock_reserved = 1", (order["id"],))
        if cursor.rowcount != 1:
            return {}
    back = unpacked_units(order["items"], order.get("step_log") or [])
    stock_store.release(back)
    return back


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


def complete_order(order_id, bill, problems=None, status=None):
    """Mark an order as finished with the final bill.

    status: "done", "done_with_problems" or "failed".
    If not given: "done" without problems, else "failed".
    """
    init_db()
    if status not in FINISHED_STATUSES:
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


def cancel_order(order_id):
    """Cancel an order that is still queued (the robot has not started it).
    Returns True if it was cancelled. Its reserved stock goes back."""
    init_db()
    with _transaction() as conn:
        cursor = conn.execute(
            "UPDATE orders SET status = 'cancelled', current_task = NULL, problems = ? "
            "WHERE id = ? AND status = 'queued'",
            (json.dumps(["Cancelled by the store before packing."]), order_id)
        )
        cancelled = cursor.rowcount == 1
    if cancelled:
        release_unpacked(get_order(order_id))
    return cancelled


def fail_interrupted_orders():
    """Orders still 'packing' when the app starts were cut off by a restart or crash.
    They are marked failed (never resumed: the robot and the basket must be checked first)
    and the units they did not pack go back to stock. Returns the order ids."""
    init_db()
    conn = _get_connection()
    ids = [row["id"] for row in conn.execute("SELECT id FROM orders WHERE status = 'packing'").fetchall()]
    for order_id in ids:
        order = get_order(order_id)
        problems = list(order.get("problems") or []) + [
            "Interrupted by an app restart while packing. Not resumed: check the robot and the basket."]
        with _transaction() as conn:
            conn.execute(
                "UPDATE orders SET status = 'failed', problems = ?, current_task = NULL "
                "WHERE id = ? AND status = 'packing'", (json.dumps(problems), order_id))
        release_unpacked(order)
    return ids


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
    with_problems = sum(1 for o in orders if o["status"] == "done_with_problems")
    queued = sum(1 for o in orders if o["status"] == "queued")
    packing = sum(1 for o in orders if o["status"] == "packing")
    failed = sum(1 for o in orders if o["status"] == "failed")
    cancelled = sum(1 for o in orders if o["status"] == "cancelled")

    # Revenue: what we charged (done orders, and partly packed orders)
    revenue = 0
    for o in orders:
        if o["status"] in CHARGED_STATUSES:
            bill = o.get("bill", {})
            if isinstance(bill, dict):
                revenue += bill.get("total", 0)

    return {
        "total": total,
        "done": done,
        "done_with_problems": with_problems,
        "queued": queued,
        "packing": packing,
        "failed": failed,
        "cancelled": cancelled,
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
    return stock_store.add_stock(item, qty)
