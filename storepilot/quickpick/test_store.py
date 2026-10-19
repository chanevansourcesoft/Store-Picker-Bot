"""
Tests for quickpick/store.py

Run:
    python -m quickpick.test_store
"""

import os
import tempfile
from pathlib import Path

from quickpick import store
from store_agent import inventory as stock_store

TEST_STOCK_FILE = Path(tempfile.mkdtemp()) / "stock.json"


def setup_test_db():
    """Use a temporary database for testing."""
    # Create a temporary file for the test database
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store.DB_PATH = Path(path)
    store._local = type(store._local)()  # Reset thread-local storage
    store.init_db()
    return path


def teardown_test_db(path):
    """Clean up test database."""
    try:
        os.unlink(path)
    except OSError:
        pass


def reset_inventory():
    """Fresh stock (the catalog's initial_stock) in a temporary stock file: the real one is never touched."""
    stock_store.STOCK_FILE = TEST_STOCK_FILE
    TEST_STOCK_FILE.unlink(missing_ok=True)


def test_check_stock():
    """Test stock checking."""
    reset_inventory()

    # Should pass: enough stock
    items = [{"item": "chips_packet", "qty": 2}]
    ok, problems = store.check_stock(items)
    assert ok, f"Should have enough stock: {problems}"
    assert len(problems) == 0

    # Should fail: not enough stock
    items = [{"item": "chips_packet", "qty": 100}]
    ok, problems = store.check_stock(items)
    assert not ok, "Should not have enough stock"
    assert len(problems) == 1
    assert "Not enough" in problems[0]

    # Should fail: item not in inventory
    items = [{"item": "pizza", "qty": 1}]
    ok, problems = store.check_stock(items)
    assert not ok, "Should fail for unknown item"
    assert len(problems) == 1
    assert "not in inventory" in problems[0]

    print("PASS: test_check_stock")


def test_reduce_stock():
    """Test stock reduction."""
    reset_inventory()

    # Get initial stock
    initial = store.get_stock()
    assert initial["chips_packet"] == 10

    # Reduce by 1
    ok = store.reduce_stock("chips_packet", 1)
    assert ok, "Should reduce stock"

    # Check new stock
    after = store.get_stock()
    assert after["chips_packet"] == 9, f"Stock should be 9, got {after['chips_packet']}"

    # Try to reduce more than available
    ok = store.reduce_stock("chips_packet", 100)
    assert not ok, "Should not reduce stock below 0"

    # Stock should be unchanged
    after2 = store.get_stock()
    assert after2["chips_packet"] == 9, f"Stock should still be 9, got {after2['chips_packet']}"

    # Reset for other tests
    reset_inventory()

    print("PASS: test_reduce_stock")


def test_create_order():
    """Test order creation with stock check."""
    db_path = setup_test_db()
    reset_inventory()

    try:
        # Should succeed: enough stock
        items = [{"item": "chips_packet", "qty": 2}]
        order_id, problems = store.create_order(items)
        assert order_id is not None, f"Should create order: {problems}"
        assert problems is None

        # Verify order in database
        order = store.get_order(order_id)
        assert order is not None
        assert order["status"] == "queued"
        assert order["items"] == items

        # Should fail: not enough stock
        items = [{"item": "chips_packet", "qty": 1000}]
        order_id, problems = store.create_order(items)
        assert order_id is None, "Should not create order"
        assert problems is not None
        assert len(problems) > 0

        print("PASS: test_create_order")

    finally:
        teardown_test_db(db_path)
        reset_inventory()


def test_order_status_flow():
    """Test order status changes: queued -> packing -> done/failed."""
    db_path = setup_test_db()
    reset_inventory()

    try:
        # Create an order
        items = [{"item": "hide_and_seek", "qty": 1}]
        order_id, _ = store.create_order(items)
        assert order_id is not None

        # Check initial status
        order = store.get_order(order_id)
        assert order["status"] == "queued"

        # Change to packing
        store.update_order_status(order_id, "packing", current_task="Picking", station="shelf")
        order = store.get_order(order_id)
        assert order["status"] == "packing"
        assert order["current_task"] == "Picking"
        assert order["station"] == "shelf"

        # Append step log
        step = {"tool": "pick_item", "result": {"success": True}}
        store.append_step_log(order_id, step)
        order = store.get_order(order_id)
        assert len(order["step_log"]) == 1
        assert order["step_log"][0]["tool"] == "pick_item"

        # Complete the order
        bill = {"items": [{"item": "hide_and_seek", "qty": 1, "price": 20}], "total": 20}
        store.complete_order(order_id, bill)
        order = store.get_order(order_id)
        assert order["status"] == "done"
        assert order["bill"]["total"] == 20
        assert order["current_task"] is None

        print("PASS: test_order_status_flow")

    finally:
        teardown_test_db(db_path)
        reset_inventory()


def test_fail_order():
    """Test failing an order."""
    db_path = setup_test_db()
    reset_inventory()

    try:
        # Create an order
        items = [{"item": "frooti", "qty": 1}]
        order_id, _ = store.create_order(items)

        # Fail the order
        store.fail_order(order_id, ["Robot error", "Could not pick item"])
        order = store.get_order(order_id)
        assert order["status"] == "failed"
        assert len(order["problems"]) == 2
        assert "Robot error" in order["problems"]

        print("PASS: test_fail_order")

    finally:
        teardown_test_db(db_path)
        reset_inventory()


def test_get_orders():
    """Test getting orders with filters."""
    db_path = setup_test_db()
    reset_inventory()

    try:
        # Create multiple orders
        store.create_order([{"item": "chips_packet", "qty": 1}])
        store.create_order([{"item": "hide_and_seek", "qty": 1}])
        order_id3, _ = store.create_order([{"item": "frooti", "qty": 1}])

        # Mark one as packing
        store.update_order_status(order_id3, "packing")

        # Get all orders
        all_orders = store.get_orders()
        assert len(all_orders) == 3

        # Get only queued orders
        queued = store.get_orders(status="queued")
        assert len(queued) == 2

        # Get only packing orders
        packing = store.get_orders(status="packing")
        assert len(packing) == 1
        assert packing[0]["id"] == order_id3

        print("PASS: test_get_orders")

    finally:
        teardown_test_db(db_path)
        reset_inventory()


def test_get_next_queued_order():
    """Test getting the next queued order (FIFO)."""
    db_path = setup_test_db()
    reset_inventory()

    try:
        # Create orders in sequence
        id1, _ = store.create_order([{"item": "chips_packet", "qty": 1}])
        id2, _ = store.create_order([{"item": "hide_and_seek", "qty": 1}])
        id3, _ = store.create_order([{"item": "frooti", "qty": 1}])

        # Should get the first one
        next_order = store.get_next_queued_order()
        assert next_order["id"] == id1

        # Mark it as packing
        store.update_order_status(id1, "packing")

        # Should get the second one now
        next_order = store.get_next_queued_order()
        assert next_order["id"] == id2

        print("PASS: test_get_next_queued_order")

    finally:
        teardown_test_db(db_path)
        reset_inventory()


def test_cancel_order():
    """Only a queued order can be cancelled; stock is not touched."""
    db_path = setup_test_db()
    try:
        reset_inventory()
        first, _ = store.create_order([{"item": "chips_packet", "qty": 1}])
        second, _ = store.create_order([{"item": "hide_and_seek", "qty": 1}])
        store.update_order_status(first, "packing")

        assert not store.cancel_order(first), "a packing order cannot be cancelled"
        assert store.cancel_order(second)
        assert store.get_order(second)["status"] == "cancelled"
        assert store.get_next_queued_order() is None, "the worker must not pick a cancelled order"
        assert store.get_stock()["hide_and_seek"] == 10
        assert store.get_stats_today()["cancelled"] == 1
        print("PASS: test_cancel_order")
    finally:
        teardown_test_db(db_path)
        reset_inventory()


def run_all_tests():
    """Run all store tests."""
    print("Running store tests...\n")

    test_check_stock()
    test_reduce_stock()
    test_create_order()
    test_order_status_flow()
    test_fail_order()
    test_get_orders()
    test_get_next_queued_order()
    test_cancel_order()

    print("\nAll store tests passed!")


if __name__ == "__main__":
    run_all_tests()
