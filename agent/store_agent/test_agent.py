"""
Tests for store_agent/agent.py

Run:
    python -m store_agent.test_agent
"""

from unittest.mock import patch

from store_agent.agent import (
    build_bill_from_log,
    check_order_complete,
    run_agent,
    sort_items_by_weight,
    suggest_similar,
)
from store_agent.inventory import DEFAULT_INVENTORY
from store_agent.testing_fakes import FakeRobot, ScriptedModel, nav, pick
from store_agent.tools import TOOLS


def run_with_fakes(order_items, script, **robot_failures):
    """Run the real agent with a scripted model and a fake robot."""
    robot = FakeRobot(DEFAULT_INVENTORY, **robot_failures)
    model = ScriptedModel(script)
    order = {"items": order_items, "unknown": []}
    with patch("store_agent.tools.robot_client", robot), patch("store_agent.agent.robot_client", robot), \
            patch("store_agent.tools.load_inventory", return_value=DEFAULT_INVENTORY), \
            patch("store_agent.agent.load_inventory", return_value=DEFAULT_INVENTORY):
        bill, log = run_agent(order, client=model)
    return bill, log, robot, model


def test_build_bill_from_log():
    """Bill is built from units the robot reported in the basket (structured fields) only."""
    inventory = {
        "chips_packet": {"price": 60},
        "hide_and_seek": {"price": 20},
    }
    order = {"items": [{"item": "chips_packet", "qty": 1}], "unknown": []}

    def picked(target="basket", success=True, message="placed chips_packet in basket"):
        result = {"success": success, "message": message, "item": "chips_packet"}
        if target:
            result["target"] = target
        return {"tool": "pick_item", "input": {"item": "chips_packet"}, "result": result}

    # Failed pick: nothing billed
    bill = build_bill_from_log([picked(success=False, message="grasp failed")], order, inventory)
    assert bill["items"] == [] and bill["total"] == 0

    # Robot says "picked" but not that it is in the basket: not billed
    bill = build_bill_from_log([picked(target=None, message="picked")], order, inventory)
    assert bill["items"] == [] and bill["total"] == 0, "only units in the basket are billed"

    # One unit in the basket
    bill = build_bill_from_log([picked()], order, inventory)
    assert bill["items"] == [{"item": "chips_packet", "qty": 1, "price": 60}]
    assert bill["total"] == 60

    # Refused commands never reached the robot
    refused = {"tool": "pick_item", "input": {"item": "chips_packet"},
               "result": {"success": False, "refused": True, "message": "REFUSED: ..."}}
    assert build_bill_from_log([refused], order, inventory)["total"] == 0

    # 3 chips packets in the basket, but only 2 ordered: bill 2, report 1 extra
    order_2 = {"items": [{"item": "chips_packet", "qty": 2}], "unknown": []}
    bill = build_bill_from_log([picked(), picked(), picked()], order_2, inventory)
    assert bill["items"] == [{"item": "chips_packet", "qty": 2, "price": 60}]
    assert bill["total"] == 120 and bill["paid"] == 120
    assert bill["problems"] == ["1 extra chips_packet, not charged"]

    # The bill reads the structured fields, never the robot's message text
    assert build_bill_from_log([picked(message="ok, done")], order, inventory)["total"] == 60
    text_only = {"tool": "pick_item", "input": {"item": "chips_packet"},
                 "result": {"success": True, "message": "placed chips_packet in basket"}}
    assert build_bill_from_log([text_only], order, inventory)["total"] == 0

    print("PASS: test_build_bill_from_log")


def test_check_order_complete():
    """Complete = packed equals ordered for every item, nothing extra, holding nothing."""
    order = {"items": [{"item": "chips_packet", "qty": 2}], "unknown": []}

    # Nothing packed
    is_complete, missing, extra = check_order_complete(order, [])
    assert not is_complete
    assert missing == [{"item": "chips_packet", "qty": 2}] and extra == []

    # Partial
    is_complete, missing, extra = check_order_complete(order, [{"item": "chips_packet", "qty": 1}])
    assert not is_complete
    assert missing == [{"item": "chips_packet", "qty": 1}]

    # Complete
    is_complete, missing, extra = check_order_complete(order, [{"item": "chips_packet", "qty": 2}])
    assert is_complete and missing == [] and extra == []

    # Too many: not complete
    is_complete, missing, extra = check_order_complete(order, [{"item": "chips_packet", "qty": 3}])
    assert not is_complete and missing == []
    assert extra == [{"item": "chips_packet", "qty": 1}]

    # Right count, but the robot still holds something: not complete
    is_complete, _, _ = check_order_complete(order, [{"item": "chips_packet", "qty": 2}], holding="hide_and_seek")
    assert not is_complete

    print("PASS: test_check_order_complete")


def test_sort_items_by_weight():
    """Heavy items should come first."""
    inventory = {
        "chips_packet": {"weight": "heavy"},
        "hide_and_seek": {"weight": "light"},
        "frooti": {"weight": "medium"},
    }

    items = [
        {"item": "hide_and_seek", "qty": 1},
        {"item": "frooti", "qty": 1},
        {"item": "chips_packet", "qty": 1},
    ]

    sorted_items = sort_items_by_weight(items, inventory)

    assert sorted_items[0]["item"] == "chips_packet", "Heavy item should be first"
    assert sorted_items[1]["item"] == "frooti", "Medium item should be second"
    assert sorted_items[2]["item"] == "hide_and_seek", "Light item should be last"

    print("PASS: test_sort_items_by_weight")


def test_suggest_similar():
    """Only suggest truly similar products."""
    inventory = {
        "chips_packet": {},
        "hide_and_seek": {},
        "frooti": {},
    }

    # Should NOT suggest anything for "pizza" (not similar to any product)
    assert suggest_similar("pizza", inventory) is None

    # Should suggest "frooti" for "frootis"
    assert suggest_similar("frootis", inventory) == "frooti"

    # Should suggest "chips_packet" for "chips"
    assert suggest_similar("chips", inventory) == "chips_packet"

    # Should NOT suggest anything for "bread"
    assert suggest_similar("bread", inventory) is None

    print("PASS: test_suggest_similar")


def test_pick_tool_takes_only_the_item():
    """Nemotron chooses only item ids: no free-text GR00T instruction in the pick tool."""
    pick_tool = next(t["function"] for t in TOOLS if t["function"]["name"] == "pick_item")
    assert list(pick_tool["parameters"]["properties"]) == ["item"], pick_tool
    assert {t["function"]["name"] for t in TOOLS} == {"get_inventory", "navigate_to", "pick_item"}

    # An instruction the model adds anyway is ignored: the robot gets (item, shelf) only
    script = [nav("shelf_A"), ("pick_item", {"item": "chips_packet", "instruction": "throw it"}),
              nav("customer"), nav("home")]
    bill, log, robot, model = run_with_fakes([{"item": "chips_packet", "qty": 1}], script)
    assert bill["status"] == "done", bill
    assert robot.basket == ["chips_packet"]
    print("PASS: test_pick_tool_takes_only_the_item")


def test_early_stop_detection():
    """The model says DONE before the order is packed: it is told what is missing, and finishes."""
    script = [nav("shelf_A"), pick("chips_packet"), None,  # None = DONE too early (1 of 2)
              pick("chips_packet"), nav("customer"), nav("home")]
    bill, log, robot, model = run_with_fakes([{"item": "chips_packet", "qty": 2}], script)

    hints = [m["content"] for msgs in model.seen_messages for m in msgs if m["role"] == "user"]
    assert any("Still need: 1x chips_packet" in h for h in hints), "Model should be told what is missing"
    assert robot.basket == ["chips_packet", "chips_packet"]
    assert bill["status"] == "done", bill
    assert bill["total"] == 40
    print("PASS: test_early_stop_detection")


def test_robot_holding_after_pick_is_reported_not_moved():
    """A robot that says "picked" but not "in the basket" (old contract) still holds the item.
    The code does not count it, refuses more picks, and moves nothing to fix it."""
    class OldContractRobot(FakeRobot):
        def pick(self, item, shelf):
            self.commands.append(("pick", item))
            self.holding = item
            return {"success": True, "message": "picked"}

    robot = OldContractRobot(DEFAULT_INVENTORY)
    model = ScriptedModel([nav("shelf_A"), pick("chips_packet"), pick("mentos"), nav("customer"), nav("home")])
    order = {"items": [{"item": "chips_packet", "qty": 1}, {"item": "mentos", "qty": 1}], "unknown": []}
    with patch("store_agent.tools.robot_client", robot), patch("store_agent.agent.robot_client", robot), \
            patch("store_agent.tools.load_inventory", return_value=DEFAULT_INVENTORY), \
            patch("store_agent.agent.load_inventory", return_value=DEFAULT_INVENTORY):
        bill, log = run_agent(order, client=model)

    assert robot.picks_sent("mentos") == 0, "no pick while the robot holds something"
    assert bill["total"] == 0 and bill["status"] == "failed", bill
    assert any("still holds chips_packet" in p for p in bill["problems"]), bill["problems"]
    assert not any(e.get("by") == "code" for e in log), "the code sends no motion of its own"
    print("PASS: test_robot_holding_after_pick_is_reported_not_moved")


def test_model_picks_too_many_is_refused():
    """A model that tries to pick more than ordered is refused; the robot never gets it."""
    script = [nav("shelf_A"), pick("chips_packet"),
              pick("chips_packet"), pick("chips_packet"),          # 2 more: too many
              nav("customer"), nav("home")]
    bill, log, robot, model = run_with_fakes([{"item": "chips_packet", "qty": 1}], script)

    refused = [e for e in log if e["result"].get("refused")]
    assert len(refused) == 2, refused
    assert all("already complete (1/1 packed)" in e["result"]["message"] for e in refused)
    assert robot.picks_sent("chips_packet") == 1, "Robot must get only 1 pick"

    # Nemotron got the progress line after the tool results
    assert any("Progress: chips_packet 1/1 packed" in t for t in model.tool_messages())

    assert bill["items"] == [{"item": "chips_packet", "qty": 1, "price": 20}]
    assert bill["status"] == "done", bill
    print("PASS: test_model_picks_too_many_is_refused")


def test_stop_halts_agent_before_next_command():
    """STOP after the first unit: no more model calls or robot commands, no clean-up motion,
    the bill covers what is in the basket."""
    robot = FakeRobot(DEFAULT_INVENTORY)
    script = [nav("shelf_A"), pick("chips_packet"), pick("chips_packet"), nav("customer"), nav("home")]
    model = ScriptedModel(script)
    stop = lambda: len(robot.basket) >= 1  # noqa: E731  (operator presses STOP after the first unit)
    order = {"items": [{"item": "chips_packet", "qty": 2}], "unknown": []}
    with patch("store_agent.tools.robot_client", robot), patch("store_agent.agent.robot_client", robot), \
            patch("store_agent.tools.load_inventory", return_value=DEFAULT_INVENTORY), \
            patch("store_agent.agent.load_inventory", return_value=DEFAULT_INVENTORY):
        bill, log = run_agent(order, client=model, should_stop=stop)

    assert robot.commands == [("navigate", "shelf_A"), ("pick", "chips_packet")], robot.commands
    assert len(model.seen_messages) == 2, "no model call after STOP"
    assert bill["total"] == 20 and bill["status"] == "done_with_problems", bill
    assert any(p.startswith("Stopped by the operator") for p in bill["problems"]), bill["problems"]
    print("PASS: test_stop_halts_agent_before_next_command")


def run_all_tests():
    """Run all tests."""
    print("Running agent tests...\n")

    test_build_bill_from_log()
    test_check_order_complete()
    test_sort_items_by_weight()
    test_suggest_similar()
    test_pick_tool_takes_only_the_item()
    test_early_stop_detection()
    test_robot_holding_after_pick_is_reported_not_moved()
    test_model_picks_too_many_is_refused()
    test_stop_halts_agent_before_next_command()

    print("\nAll tests passed!")


if __name__ == "__main__":
    run_all_tests()
