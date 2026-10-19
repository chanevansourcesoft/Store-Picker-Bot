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
from store_agent.testing_fakes import BASKET, SCAN, FakeRobot, ScriptedModel, nav, pick


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
    """Bill is built from successful place_item(basket) calls only."""
    inventory = {
        "chips_packet": {"price": 60},
        "hide_and_seek": {"price": 20},
    }
    order = {"items": [{"item": "chips_packet", "qty": 1}], "unknown": []}

    # Log where pick and scan happened, but place_item was NOT called
    log_incomplete = [
        {"tool": "pick_item", "input": {"item": "chips_packet"}, "result": {"success": True}},
        {"tool": "scan_item", "input": {}, "result": {"success": True, "barcode": "123"}},
    ]

    bill = build_bill_from_log(log_incomplete, order, inventory)
    assert bill["items"] == [], "No items should be billed without place_item"
    assert bill["total"] == 0, "Total should be 0 without place_item"

    # Log where place_item(basket) succeeded
    log_complete = [
        {"tool": "pick_item", "input": {"item": "chips_packet"}, "result": {"success": True}},
        {"tool": "scan_item", "input": {}, "result": {"success": True, "barcode": "123"}},
        {"tool": "place_item", "input": {"target": "basket"},
         "result": {"success": True, "message": "placed chips_packet in basket", "item": "chips_packet"}},
    ]

    bill = build_bill_from_log(log_complete, order, inventory)
    assert len(bill["items"]) == 1, "One item should be billed"
    assert bill["items"][0]["item"] == "chips_packet"
    assert bill["total"] == 60

    # Log where place_item was called with "return" (not basket)
    log_returned = [
        {"tool": "pick_item", "input": {"item": "chips_packet"}, "result": {"success": True}},
        {"tool": "scan_item", "input": {}, "result": {"success": True, "barcode": "123"}},
        {"tool": "place_item", "input": {"target": "return"},
         "result": {"success": True, "message": "returned chips_packet", "item": "chips_packet"}},
    ]

    bill = build_bill_from_log(log_returned, order, inventory)
    assert bill["items"] == [], "Returned items should not be billed"
    assert bill["total"] == 0

    # 3 chips packets in the basket, but only 2 ordered: bill 2, report 1 extra
    order_2 = {"items": [{"item": "chips_packet", "qty": 2}], "unknown": []}
    placed = {"tool": "place_item", "input": {"target": "basket"},
              "result": {"success": True, "message": "placed chips_packet in basket", "item": "chips_packet"}}
    bill = build_bill_from_log([placed, placed, placed], order_2, inventory)
    assert bill["items"] == [{"item": "chips_packet", "qty": 2, "price": 60}]
    assert bill["total"] == 120 and bill["paid"] == 120
    assert bill["problems"] == ["1 extra chips_packet, not charged"]

    # The bill reads the structured "item" field, never the robot's message text
    wording = {"tool": "place_item", "input": {"target": "basket"},
               "result": {"success": True, "message": "ok, done", "item": "chips_packet"}}
    assert build_bill_from_log([wording], order, inventory)["total"] == 60
    text_only = {"tool": "place_item", "input": {"target": "basket"},
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


def test_early_stop_detection():
    """The model says DONE while the robot still holds an item: it is told, and it places it."""
    script = [nav("shelf_A"), pick("chips_packet"), SCAN, None,  # None = DONE too early
              BASKET, nav("customer"), nav("home")]
    bill, log, robot, model = run_with_fakes([{"item": "chips_packet", "qty": 1}], script)

    hints = [m["content"] for msgs in model.seen_messages for m in msgs if m["role"] == "user"]
    assert any("still holding chips_packet" in h for h in hints), "Model should be told it still holds the item"
    assert robot.holding is None
    assert bill["status"] == "done", bill
    assert bill["total"] == 20
    print("PASS: test_early_stop_detection")


def test_still_holding_at_end_is_put_back():
    """The model never places the item: the CODE puts it back on its own shelf."""
    script = [nav("shelf_A"), pick("chips_packet"), nav("customer")]  # then only DONE
    bill, log, robot, model = run_with_fakes([{"item": "chips_packet", "qty": 1}], script)

    assert robot.holding is None, "Item must be back on the shelf"
    assert robot.station == "shelf_A", "Code drove to the item's own shelf to return it"
    assert any(e.get("by") == "code" for e in log)
    assert bill["status"] == "failed" and bill["total"] == 0
    assert any("still holding chips_packet" in p for p in bill["problems"])
    print("PASS: test_still_holding_at_end_is_put_back")


def test_model_picks_too_many_is_refused():
    """A model that tries to pick more than ordered is refused; the robot never gets it."""
    script = [nav("shelf_A"), pick("chips_packet"), SCAN, BASKET,
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


def run_all_tests():
    """Run all tests."""
    print("Running agent tests...\n")

    test_build_bill_from_log()
    test_check_order_complete()
    test_sort_items_by_weight()
    test_suggest_similar()
    test_early_stop_detection()
    test_still_holding_at_end_is_put_back()
    test_model_picks_too_many_is_refused()

    print("\nAll tests passed!")


if __name__ == "__main__":
    run_all_tests()
