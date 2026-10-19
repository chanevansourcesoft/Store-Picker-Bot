"""
Tests for quickpick/worker.py

The agent is replaced with a fake one, so these tests need no Nemotron key
and no robot. The fake agent sends the same steps and robot messages as the
mock robot and the Jetson server (structured "item" and "target" fields).

Run:
    python -m quickpick.test_worker
"""

from unittest.mock import patch

from quickpick import store, worker
from store_agent.inventory import DEFAULT_INVENTORY
from quickpick.test_store import reset_inventory, setup_test_db, teardown_test_db
from store_agent.testing_fakes import FakeRobot, ScriptedModel, nav, pick

# Demo order #3, replayed with one pick = pick AND place in the basket: the model
# loses count after failures and keeps asking for more chips packets than ordered.
ORDER_3_SCRIPT = [
    nav("shelf_A"), nav("shelf_A"), pick("chips_packet"),
    pick("chips_packet"), pick("chips_packet"), pick("chips_packet"), pick("chips_packet"),
    nav("shelf_B"), pick("chips_packet"), pick("hide_and_seek"), pick("hide_and_seek"),
    nav("shelf_B"), pick("frooti"),
    nav("customer"), nav("home"),
]


def fake_steps(item, ok=True):
    """Step the agent logs for one unit: one pick that places it in the basket (or fails)."""
    if ok:
        result = {"success": True, "message": f"placed {item} in basket", "item": item, "target": "basket"}
    else:
        result = {"success": False, "message": "grasp failed", "item": item}
    return [{"tool": "pick_item", "input": {"item": item}, "result": result}]


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


def test_failed_pick_keeps_stock():
    """A failed pick put nothing in the basket: stock must not go down."""
    db_path = setup_test_db()
    reset_inventory()
    try:
        before = store.get_stock()
        order_id, problems = store.create_order([{"item": "chips_packet", "qty": 1}])
        assert order_id, problems

        steps = fake_steps("chips_packet", ok=False)
        with patch("quickpick.worker.run_agent", make_fake_agent(steps)):
            worker.process_single_order(order_id)

        assert store.get_stock()["chips_packet"] == before["chips_packet"]

        print("PASS: test_failed_pick_keeps_stock")

    finally:
        teardown_test_db(db_path)
        reset_inventory()


def test_replay_order_3_is_not_overpacked():
    """Replay order #3 (navigation and grasp failures, model loses count). The code must stop
    the mistakes: exactly 2 chips packets, bill Rs 85 or less, stock down by exactly 2."""
    db_path = setup_test_db()
    reset_inventory()
    try:
        before = store.get_stock()
        items = [{"item": "chips_packet", "qty": 2}, {"item": "hide_and_seek", "qty": 1}, {"item": "frooti", "qty": 1}]
        order_id, problems = store.create_order(items)
        assert order_id, problems

        # Failures: 1st navigation, 1st grasp
        robot = FakeRobot(DEFAULT_INVENTORY, nav_fail=[1], pick_fail=[1])
        model = ScriptedModel(ORDER_3_SCRIPT)
        with patch("store_agent.tools.robot_client", robot), patch("store_agent.agent.robot_client", robot), \
                patch("store_agent.agent.get_client", return_value=model):
            worker.process_single_order(order_id)

        order = store.get_order(order_id)
        bill = order["bill"]
        after = store.get_stock()
        chips_in_basket = robot.basket.count("chips_packet")

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
        assert robot.picks_sent("chips_packet") == 3, "1 failed + 2 good: the extra picks were refused"
        assert sorted(robot.basket) == ["chips_packet", "chips_packet", "frooti", "hide_and_seek"], robot.basket

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


def test_stock_uses_item_field_not_message():
    """Stock follows the structured "item" field, whatever the robot's message says."""
    db_path = setup_test_db()
    reset_inventory()
    try:
        before = store.get_stock()
        order_id, problems = store.create_order([{"item": "frooti", "qty": 1}])
        assert order_id, problems
        steps = fake_steps("frooti")
        steps[-1] = dict(steps[-1], result=dict(steps[-1]["result"], message="done"))
        with patch("quickpick.worker.run_agent", make_fake_agent(steps)):
            worker.process_single_order(order_id)
        assert before["frooti"] - store.get_stock()["frooti"] == 1
        print("PASS: test_stock_uses_item_field_not_message")
    finally:
        teardown_test_db(db_path)
        reset_inventory()


def run_all_tests():
    """Run all worker tests."""
    print("Running worker tests...\n")

    test_stock_goes_down_after_order()
    test_failed_pick_keeps_stock()
    test_replay_order_3_is_not_overpacked()
    test_extra_item_does_not_reduce_stock()
    test_stock_uses_item_field_not_message()

    print("\nAll worker tests passed!")


if __name__ == "__main__":
    run_all_tests()
