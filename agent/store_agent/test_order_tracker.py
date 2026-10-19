"""
Tests for store_agent/order_tracker.py

Run:
    python -m store_agent.test_order_tracker
"""

from store_agent.inventory import DEFAULT_INVENTORY
from store_agent.order_tracker import OrderTracker

OK = {"success": True}


def new_tracker():
    items = [{"item": "chips_packet", "qty": 2}, {"item": "hide_and_seek", "qty": 1}, {"item": "frooti", "qty": 1}]
    tracker = OrderTracker(items, DEFAULT_INVENTORY)
    tracker.sync_from_status({"holding": None, "station": "home"})
    return tracker


def pack(tracker, item):
    """Real robot result for one pick: one GR00T run picks the item AND places it in the basket."""
    return tracker.record("pick_item", {"item": item},
                          {"success": True, "message": "done", "item": item, "target": "basket"})


def test_pick_guards():
    t = new_tracker()
    t.record("navigate_to", {"station": "shelf_A"}, OK)
    assert t.check_pick("chips_packet") is None
    assert pack(t, "chips_packet") == "packed"
    assert t.holding is None, "after a pick the arm is empty again"
    assert t.check_pick("chips_packet") is None, "second unit still allowed"
    pack(t, "chips_packet")
    assert "already complete (2/2 packed)" in t.check_pick("chips_packet")
    assert "not in this order" in t.check_pick("milk")
    print("PASS: test_pick_guards")


def test_failed_pick_changes_nothing():
    t = new_tracker()
    t.record("pick_item", {"item": "chips_packet"}, {"success": False, "message": "grasp failed"})
    assert t.holding is None and t.check_pick("chips_packet") is None
    assert t.packed_count() == 0
    print("PASS: test_failed_pick_changes_nothing")


def test_pick_not_in_basket_is_held():
    """A robot that only says "picked" (no target "basket") still holds the item:
    nothing is counted, and no further pick is allowed until the arm is checked."""
    t = new_tracker()
    assert t.record("pick_item", {"item": "chips_packet"}, {"success": True, "message": "picked"}) is None
    assert t.packed_count() == 0 and t.holding == "chips_packet"
    assert "operator must check the arm" in t.check_pick("frooti")
    assert "HOLDS chips_packet" in t.progress_line()
    assert not t.is_complete()
    print("PASS: test_pick_not_in_basket_is_held")


def test_extra_and_complete():
    t = new_tracker()
    assert pack(t, "chips_packet") == "packed"
    assert pack(t, "chips_packet") == "packed"
    assert pack(t, "chips_packet") == "extra"   # e.g. a robot that ignores the guard
    assert t.extras() == [{"item": "chips_packet", "qty": 1}]
    pack(t, "hide_and_seek")
    pack(t, "frooti")
    assert t.missing() == []
    assert not t.is_complete(), "extra item -> not complete"
    print("PASS: test_extra_and_complete")


def test_progress_line():
    t = new_tracker()
    t.record("navigate_to", {"station": "shelf_A"}, OK)
    pack(t, "chips_packet")
    line = t.progress_line()
    assert line.startswith("Progress: chips_packet 1/2 packed, hide_and_seek 0/1 packed, frooti 0/1 packed."), line
    assert "Robot at: shelf_A." in line and "HOLDS" not in line, line

    pack(t, "chips_packet")
    pack(t, "hide_and_seek")
    pack(t, "frooti")
    assert "All items are packed" in t.progress_line()
    assert t.is_complete()
    print("PASS: test_progress_line")


def run_all_tests():
    print("Running order tracker tests...\n")
    test_pick_guards()
    test_failed_pick_changes_nothing()
    test_pick_not_in_basket_is_held()
    test_extra_and_complete()
    test_progress_line()
    print("\nAll order tracker tests passed!")


if __name__ == "__main__":
    run_all_tests()
