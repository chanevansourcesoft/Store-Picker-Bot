"""
Tests for quickpick/worker.py

The agent is replaced with a fake one, so these tests need no Nemotron key
and no robot. The fake agent sends the same steps and robot messages as the
mock robot and the Jetson server ("placed chips_packet in basket").

Run:
    python -m quickpick.test_worker
"""

from unittest.mock import patch

from quickpick import store, worker
from store_agent.inventory import DEFAULT_INVENTORY
from quickpick.test_store import reset_inventory, setup_test_db, teardown_test_db
from store_agent.testing_fakes import BASKET, RETURN, SCAN, FakeRobot, ScriptedModel, nav, pick

# The exact tool calls the model made in demo order #3 (it lost count
# after scan failures and packed 3 chips packets for an order of 2).
ORDER_3_SCRIPT = [
    nav("shelf_A"), nav("shelf_A"), pick("chips_packet"), SCAN, RETURN,
    pick("chips_packet"), SCAN, BASKET,
    pick("chips_packet"), SCAN, RETURN, pick("chips_packet"), pick("chips_packet"), SCAN,
    nav("shelf_B"), pick("chips_packet"), RETURN, RETURN, pick("chips_packet"),
    nav("shelf_A"), pick("chips_packet"), SCAN, nav("shelf_A"), SCAN, BASKET,
    pick("chips_packet"), SCAN, BASKET,
    nav("shelf_B"), pick("hide_and_seek"), SCAN, BASKET,
    nav("shelf_B"), pick("frooti"), SCAN, BASKET,
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
    """2 chips packets + 1 hide and seek packed in the basket -> stock goes down by 2 and 1."""
    db_path = setup_test_db()
    reset_inventory()
    try:
        before = store.get_stock()
        items = [{"item": "chips_packet", "qty": 2}, {"item": "hide_and_seek", "qty": 1}]
        order_id, problems = store.create_order(items)
        assert order_id, problems

        steps = fake_steps("chips_packet") + fake_steps("chips_packet") + fake_steps("hide_and_seek")
        with patch("quickpick.worker.run_agent", make_fake_agent(steps)):
            worker.process_single_order(order_id)

        after = store.get_stock()
        assert after["chips_packet"] == before["chips_packet"] - 2, after
        assert after["hide_and_seek"] == before["hide_and_seek"] - 1, after
        assert after["frooti"] == before["frooti"], after
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
        order_id, problems = store.create_order([{"item": "chips_packet", "qty": 1}])
        assert order_id, problems

        steps = fake_steps("chips_packet", target="return")
        with patch("quickpick.worker.run_agent", make_fake_agent(steps)):
            worker.process_single_order(order_id)

        assert store.get_stock()["chips_packet"] == before["chips_packet"]

        print("PASS: test_returned_item_keeps_stock")

    finally:
        teardown_test_db(db_path)
        reset_inventory()


def test_replay_order_3_is_not_overpacked():
    """Replay order #3 (several scan failures, model loses count). The code must stop
    the mistakes: exactly 2 chips packets, bill Rs 85 or less, stock down by exactly 2."""
    db_path = setup_test_db()
    reset_inventory()
    try:
        before = store.get_stock()
        items = [{"item": "chips_packet", "qty": 2}, {"item": "hide_and_seek", "qty": 1}, {"item": "frooti", "qty": 1}]
        order_id, problems = store.create_order(items)
        assert order_id, problems

        # Same failures as order #3: 1st navigation, 4th grasp, scans 1, 3 and 4
        robot = FakeRobot(DEFAULT_INVENTORY, nav_fail=[1], pick_fail=[4], scan_fail=[1, 3, 4])
        model = ScriptedModel(ORDER_3_SCRIPT)
        with patch("store_agent.tools.robot_client", robot), patch("store_agent.agent.robot_client", robot), \
                patch("store_agent.agent.get_client", return_value=model):
            worker.process_single_order(order_id)

        order = store.get_order(order_id)
        bill = order["bill"]
        after = store.get_stock()
        chips_in_basket = sum(1 for e in order["step_log"]
                              if e["result"].get("message") == "placed chips_packet in basket")

        assert chips_in_basket == 2, f"basket has {chips_in_basket} chips packets"
        assert bill["total"] <= 85, bill
        assert bill["total"] == bill["paid"] == 85, bill
        assert before["chips_packet"] - after["chips_packet"] == 2, (before, after)
        assert before["hide_and_seek"] - after["hide_and_seek"] == 1
        assert before["frooti"] - after["frooti"] == 1
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
    """If an extra chips packet still ends in the basket, stock goes down only by the ordered qty."""
    db_path = setup_test_db()
    reset_inventory()
    try:
        before = store.get_stock()
        order_id, problems = store.create_order([{"item": "chips_packet", "qty": 2}])
        assert order_id, problems

        steps = fake_steps("chips_packet") * 3  # 3 chips packets placed, 2 ordered
        with patch("quickpick.worker.run_agent", make_fake_agent(steps)):
            worker.process_single_order(order_id)

        assert before["chips_packet"] - store.get_stock()["chips_packet"] == 2
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
