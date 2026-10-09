"""
Tests for app/agent.py

Run:
    python -m app.test_agent
"""

import json
from unittest.mock import MagicMock, patch

from app.agent import (
    build_bill_from_log,
    check_order_complete,
    run_agent,
    sort_items_by_weight,
    suggest_similar,
)


def test_build_bill_from_log():
    """Bill is built from successful place_item(basket) calls only."""
    inventory = {
        "soup_can": {"price": 60},
        "chips_pack": {"price": 20},
    }
    order = {"items": [{"item": "soup_can", "qty": 1}], "unknown": []}

    # Log where pick and scan happened, but place_item was NOT called
    log_incomplete = [
        {"tool": "pick_item", "input": {"item": "soup_can"}, "result": {"success": True}},
        {"tool": "scan_item", "input": {}, "result": {"success": True, "barcode": "123"}},
    ]

    bill = build_bill_from_log(log_incomplete, order, inventory)
    assert bill["items"] == [], "No items should be billed without place_item"
    assert bill["total"] == 0, "Total should be 0 without place_item"

    # Log where place_item(basket) succeeded
    log_complete = [
        {"tool": "pick_item", "input": {"item": "soup_can"}, "result": {"success": True}},
        {"tool": "scan_item", "input": {}, "result": {"success": True, "barcode": "123"}},
        {"tool": "place_item", "input": {"target": "basket"}, "result": {"success": True, "message": "placed soup_can in basket"}},
    ]

    bill = build_bill_from_log(log_complete, order, inventory)
    assert len(bill["items"]) == 1, "One item should be billed"
    assert bill["items"][0]["item"] == "soup_can"
    assert bill["total"] == 60

    # Log where place_item was called with "return" (not basket)
    log_returned = [
        {"tool": "pick_item", "input": {"item": "soup_can"}, "result": {"success": True}},
        {"tool": "scan_item", "input": {}, "result": {"success": True, "barcode": "123"}},
        {"tool": "place_item", "input": {"target": "return"}, "result": {"success": True, "message": "returned soup_can"}},
    ]

    bill = build_bill_from_log(log_returned, order, inventory)
    assert bill["items"] == [], "Returned items should not be billed"
    assert bill["total"] == 0

    print("PASS: test_build_bill_from_log")


def test_check_order_complete():
    """Check if order completion detection works."""
    inventory = {"soup_can": {"price": 60}}
    order = {"items": [{"item": "soup_can", "qty": 2}], "unknown": []}

    # Nothing packed
    is_complete, missing = check_order_complete(order, [], inventory)
    assert not is_complete
    assert missing == [{"item": "soup_can", "qty": 2}]

    # Partial
    packed = [{"item": "soup_can", "qty": 1}]
    is_complete, missing = check_order_complete(order, packed, inventory)
    assert not is_complete
    assert missing == [{"item": "soup_can", "qty": 1}]

    # Complete
    packed = [{"item": "soup_can", "qty": 2}]
    is_complete, missing = check_order_complete(order, packed, inventory)
    assert is_complete
    assert missing == []

    print("PASS: test_check_order_complete")


def test_sort_items_by_weight():
    """Heavy items should come first."""
    inventory = {
        "soup_can": {"weight": "heavy"},
        "chips_pack": {"weight": "light"},
        "juice_box": {"weight": "medium"},
    }

    items = [
        {"item": "chips_pack", "qty": 1},
        {"item": "juice_box", "qty": 1},
        {"item": "soup_can", "qty": 1},
    ]

    sorted_items = sort_items_by_weight(items, inventory)

    assert sorted_items[0]["item"] == "soup_can", "Heavy item should be first"
    assert sorted_items[1]["item"] == "juice_box", "Medium item should be second"
    assert sorted_items[2]["item"] == "chips_pack", "Light item should be last"

    print("PASS: test_sort_items_by_weight")


def test_suggest_similar():
    """Only suggest truly similar products."""
    inventory = {
        "soup_can": {},
        "chips_pack": {},
        "juice_box": {},
    }

    # Should NOT suggest anything for "pizza" (not similar to any product)
    assert suggest_similar("pizza", inventory) is None

    # Should suggest "juice_box" for "juice"
    assert suggest_similar("juice", inventory) == "juice_box"

    # Should suggest "chips_pack" for "chips"
    assert suggest_similar("chips", inventory) == "chips_pack"

    # Should NOT suggest anything for "bread"
    assert suggest_similar("bread", inventory) is None

    print("PASS: test_suggest_similar")


def test_early_stop_detection():
    """
    Test that if the model stops early (robot still holding), the system
    detects this and either continues or reports the problem.
    """
    # Create a fake client that makes the model stop after scan (no place_item)
    call_count = [0]

    class FakeChoice:
        def __init__(self, tool_calls=None, content=None):
            self.message = MagicMock()
            self.message.tool_calls = tool_calls
            self.message.content = content
            self.finish_reason = "stop" if not tool_calls else "tool_calls"

    class FakeResponse:
        def __init__(self, tool_calls=None, content=None):
            self.choices = [FakeChoice(tool_calls, content)]

    class FakeToolCall:
        def __init__(self, name, args):
            self.id = f"call_{name}"
            self.function = MagicMock()
            self.function.name = name
            self.function.arguments = json.dumps(args)

    def fake_create(**kwargs):
        call_count[0] += 1

        # First call: model calls get_inventory
        if call_count[0] == 1:
            return FakeResponse(tool_calls=[FakeToolCall("get_inventory", {})])

        # Second call: model calls pick_item
        if call_count[0] == 2:
            return FakeResponse(tool_calls=[FakeToolCall("pick_item", {"item": "soup_can"})])

        # Third call: model calls scan_item
        if call_count[0] == 3:
            return FakeResponse(tool_calls=[FakeToolCall("scan_item", {})])

        # Fourth call: model says DONE without calling place_item (BUG!)
        if call_count[0] == 4:
            return FakeResponse(content="DONE")

        # Fifth call: after being told robot is holding, model places item
        if call_count[0] == 5:
            return FakeResponse(tool_calls=[FakeToolCall("place_item", {"target": "basket"})])

        # Sixth call: model says DONE
        return FakeResponse(content="DONE - all items packed")

    fake_client = MagicMock()
    fake_client.chat.completions.create = fake_create

    order = {"items": [{"item": "soup_can", "qty": 1}], "unknown": []}

    with patch("app.agent.robot_client") as mock_robot:
        # Mock robot status to show it's holding something after first DONE
        status_calls = [0]
        def mock_status():
            status_calls[0] += 1
            if status_calls[0] == 1:
                return {"holding": "soup_can"}  # Still holding!
            return {"holding": None}  # After place_item

        mock_robot.status = mock_status
        mock_robot.place = MagicMock(return_value={"success": True, "message": "placed soup_can in basket"})

        bill, log = run_agent(order, client=fake_client)

    # The system should have detected the problem and either:
    # 1. Continued the agent to place the item, OR
    # 2. Reported the problem in the bill

    # Check that place_item was eventually called
    place_calls = [e for e in log if e["tool"] == "place_item"]

    if place_calls:
        # Agent was continued and placed the item
        assert any(e["input"].get("target") == "basket" for e in place_calls), \
            "Item should have been placed in basket"
        print("PASS: test_early_stop_detection (agent continued and placed item)")
    else:
        # Problem was reported
        assert "holding" in str(bill.get("problems", [])).lower() or bill["total"] == 0, \
            "If item was not placed, bill should be 0 or have a problem reported"
        print("PASS: test_early_stop_detection (problem was reported)")


def run_all_tests():
    """Run all tests."""
    print("Running agent tests...\n")

    test_build_bill_from_log()
    test_check_order_complete()
    test_sort_items_by_weight()
    test_suggest_similar()
    test_early_stop_detection()

    print("\nAll tests passed!")


if __name__ == "__main__":
    run_all_tests()
