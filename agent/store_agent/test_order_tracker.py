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
    """Real robot results for: pick -> place in basket."""
    tracker.record("pick_item", {"item": item}, {"success": True, "message": "picked"})
    return tracker.record("place_item", {"target": "basket"}, {"success": True, "message": f"placed {item} in basket"})


def test_pick_guards():
    t = new_tracker()
    t.record("navigate_to", {"station": "shelf_A"}, OK)
    assert t.check_pick("chips_packet") is None
    t.record("pick_item", {"item": "chips_packet"}, OK)
    assert "already holding chips_packet" in t.check_pick("chips_packet")
    t.record("place_item", {"target": "basket"}, {"success": True, "message": "placed chips_packet in basket"})
    pack(t, "chips_packet")
    assert "already complete (2/2 packed)" in t.check_pick("chips_packet")
    assert "not in this order" in t.check_pick("milk")
    print("PASS: test_pick_guards")


def test_failed_pick_changes_nothing():
    t = new_tracker()
    t.record("pick_item", {"item": "chips_packet"}, {"success": False, "message": "grasp failed"})
    assert t.holding is None and t.check_pick("chips_packet") is None
    print("PASS: test_failed_pick_changes_nothing")


def test_place_guards():
    t = new_tracker()
    t.record("navigate_to", {"station": "shelf_A"}, OK)
    t.record("pick_item", {"item": "chips_packet"}, OK)
    assert t.check_place("basket") is None
    assert t.check_place("return") is None  # at its own shelf

    t.record("navigate_to", {"station": "shelf_B"}, OK)
    reason = t.check_place("return")
    assert "belongs on shelf_A" in reason and "navigate_to('shelf_A')" in reason

    # Basket refused when the item is already complete
    t2 = new_tracker()
    pack(t2, "hide_and_seek")
    t2.record("pick_item", {"item": "hide_and_seek"}, OK)  # e.g. robot picked it before the guard existed
    assert "already complete (1/1 packed)" in t2.check_place("basket")
    print("PASS: test_place_guards")


def test_extra_and_complete():
    t = new_tracker()
    assert pack(t, "chips_packet") == "packed"
    assert pack(t, "chips_packet") == "packed"
    assert pack(t, "chips_packet") == "extra"
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
    t.record("pick_item", {"item": "chips_packet"}, OK)
    line = t.progress_line()
    assert line.startswith("Progress: chips_packet 1/2 packed, hide_and_seek 0/1 packed, frooti 0/1 packed."), line
    assert "Holding: chips_packet. Robot at: shelf_A." in line, line

    t.record("place_item", {"target": "basket"}, {"success": True, "message": "placed chips_packet in basket"})
    pack(t, "hide_and_seek")
    pack(t, "frooti")
    assert "All items are packed" in t.progress_line()
    assert t.is_complete()
    print("PASS: test_progress_line")


def run_all_tests():
    print("Running order tracker tests...\n")
    test_pick_guards()
    test_failed_pick_changes_nothing()
    test_place_guards()
    test_extra_and_complete()
    test_progress_line()
    print("\nAll order tracker tests passed!")


if __name__ == "__main__":
    run_all_tests()
