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
from app.config import DEFAULT_INVENTORY
from app.test_store import reset_inventory, setup_test_db, teardown_test_db
from app.testing_fakes import BASKET, RETURN, SCAN, FakeRobot, ScriptedModel, nav, pick

# The exact tool calls the model made in demo order #3 (it lost count
# after scan failures and packed 3 soups for an order of 2).
ORDER_3_SCRIPT = [
    nav("shelf_A"), nav("shelf_A"), pick("soup_can"), SCAN, RETURN,
    pick("soup_can"), SCAN, BASKET,
    pick("soup_can"), SCAN, RETURN, pick("soup_can"), pick("soup_can"), SCAN,
    nav("shelf_B"), pick("soup_can"), RETURN, RETURN, pick("soup_can"),
    nav("shelf_A"), pick("soup_can"), SCAN, nav("shelf_A"), SCAN, BASKET,
    pick("soup_can"), SCAN, BASKET,
    nav("shelf_B"), pick("chips_pack"), SCAN, BASKET,
    nav("shelf_C"), pick("juice_box"), SCAN, BASKET,
    nav("customer"), nav("home"),
]


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


def test_replay_order_3_is_not_overpacked():
    """Replay order #3 (several scan failures, model loses count). The code must stop
    the mistakes: exactly 2 soups, bill Rs 175 or less, stock down by exactly 2."""
    db_path = setup_test_db()
    reset_inventory()
    try:
        before = store.get_stock()
        items = [{"item": "soup_can", "qty": 2}, {"item": "chips_pack", "qty": 1}, {"item": "juice_box", "qty": 1}]
        order_id, problems = store.create_order(items)
        assert order_id, problems

        # Same failures as order #3: 1st navigation, 4th grasp, scans 1, 3 and 4
        robot = FakeRobot(DEFAULT_INVENTORY, nav_fail=[1], pick_fail=[4], scan_fail=[1, 3, 4])
        model = ScriptedModel(ORDER_3_SCRIPT)
        with patch("app.tools.robot_client", robot), patch("app.agent.robot_client", robot), \
                patch("app.agent.get_client", return_value=model):
            worker.process_single_order(order_id)

        order = store.get_order(order_id)
        bill = order["bill"]
        after = store.get_stock()
        soups_in_basket = sum(1 for e in order["step_log"]
                              if e["result"].get("message") == "placed soup_can in basket")

        assert soups_in_basket == 2, f"basket has {soups_in_basket} soups"
        assert bill["total"] <= 175, bill
        assert bill["total"] == bill["paid"] == 175, bill
        assert before["soup_can"] - after["soup_can"] == 2, (before, after)
        assert before["chips_pack"] - after["chips_pack"] == 1
        assert before["juice_box"] - after["juice_box"] == 1
        assert order["status"] == "done", (order["status"], order["problems"])

        # The mistakes were refused by the code and never reached the robot
        refused = [e for e in order["step_log"] if e["result"].get("refused")]
        assert refused, "Some commands should be refused"
        assert not any("already holding" in e for e in robot.errors), robot.errors
        assert not any("cannot return" in e for e in robot.errors), robot.errors
        assert robot.holding is None

        print(f"PASS: test_replay_order_3_is_not_overpacked ({len(refused)} commands refused)")

    finally:
        teardown_test_db(db_path)
        reset_inventory()


def test_extra_item_does_not_reduce_stock():
    """If an extra soup still ends in the basket, stock goes down only by the ordered qty."""
    db_path = setup_test_db()
    reset_inventory()
    try:
        before = store.get_stock()
        order_id, problems = store.create_order([{"item": "soup_can", "qty": 2}])
        assert order_id, problems

        steps = fake_steps("soup_can") * 3  # 3 soups placed, 2 ordered
        with patch("app.worker.run_agent", make_fake_agent(steps)):
            worker.process_single_order(order_id)

        assert before["soup_can"] - store.get_stock()["soup_can"] == 2
        print("PASS: test_extra_item_does_not_reduce_stock")

    finally:
        teardown_test_db(db_path)
        reset_inventory()


def run_all_tests():
    """Run all worker tests."""
    print("Running worker tests...\n")

    test_stock_goes_down_after_order()
    test_returned_item_keeps_stock()
    test_replay_order_3_is_not_overpacked()
    test_extra_item_does_not_reduce_stock()

    print("\nAll worker tests passed!")


if __name__ == "__main__":
    run_all_tests()
