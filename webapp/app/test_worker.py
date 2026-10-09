"""
Tests for app/worker.py

The agent is replaced with a fake one, so these tests need no Nemotron key
and no robot. The fake agent sends the same steps and robot messages as the
mock robot and the Jetson server ("placed soup_can in basket").

Run:
    python -m app.test_worker
"""

from unittest.mock import patch

from app import store, worker
from app.test_store import reset_inventory, setup_test_db, teardown_test_db


def fake_steps(item, target="basket"):
    """Steps the agent logs for one item: pick, scan, place."""
    if target == "basket":
        place_msg = f"placed {item} in basket"
    else:
        place_msg = f"returned {item} to shelf"
    return [
        {"tool": "pick_item", "input": {"item": item, "shelf": "shelf_A"},
         "result": {"success": True, "message": "picked"}},
        {"tool": "scan_item", "input": {},
         "result": {"success": True, "barcode": "123"}},
        {"tool": "place_item", "input": {"target": target},
         "result": {"success": True, "message": place_msg}},
    ]


def make_fake_agent(steps):
    """A fake run_agent() that sends the given steps to on_step."""
    def fake_run_agent(order, on_step=None):
        for i, entry in enumerate(steps):
            entry = dict(entry, step=i + 1)
            if on_step:
                on_step(entry)
        return {"items": [], "total": 0, "problems": []}, steps
    return fake_run_agent


def test_stock_goes_down_after_order():
    """2 soups + 1 chips packed in the basket -> stock goes down by 2 and 1."""
    db_path = setup_test_db()
    reset_inventory()
    try:
        before = store.get_stock()
        items = [{"item": "soup_can", "qty": 2}, {"item": "chips_pack", "qty": 1}]
        order_id, problems = store.create_order(items)
        assert order_id, problems

        steps = fake_steps("soup_can") + fake_steps("soup_can") + fake_steps("chips_pack")
        with patch("app.worker.run_agent", make_fake_agent(steps)):
            worker.process_single_order(order_id)

        after = store.get_stock()
        assert after["soup_can"] == before["soup_can"] - 2, after
        assert after["chips_pack"] == before["chips_pack"] - 1, after
        assert after["juice_box"] == before["juice_box"], after
        assert store.get_order(order_id)["status"] == "done"

        print("PASS: test_stock_goes_down_after_order")

    finally:
        teardown_test_db(db_path)
        reset_inventory()


def test_returned_item_keeps_stock():
    """An item put back on the shelf ("return") must not reduce stock."""
    db_path = setup_test_db()
    reset_inventory()
    try:
        before = store.get_stock()
        order_id, problems = store.create_order([{"item": "soup_can", "qty": 1}])
        assert order_id, problems

        steps = fake_steps("soup_can", target="return")
        with patch("app.worker.run_agent", make_fake_agent(steps)):
            worker.process_single_order(order_id)

        assert store.get_stock()["soup_can"] == before["soup_can"]

        print("PASS: test_returned_item_keeps_stock")

    finally:
        teardown_test_db(db_path)
        reset_inventory()


def run_all_tests():
    """Run all worker tests."""
    print("Running worker tests...\n")

    test_stock_goes_down_after_order()
    test_returned_item_keeps_stock()

    print("\nAll worker tests passed!")


if __name__ == "__main__":
    run_all_tests()
