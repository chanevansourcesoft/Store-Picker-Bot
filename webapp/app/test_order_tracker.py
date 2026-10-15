"""
Tests for app/order_tracker.py

Run:
    python -m app.test_order_tracker
"""

from app.config import DEFAULT_INVENTORY
from app.order_tracker import OrderTracker

OK = {"success": True}


def new_tracker():
    items = [{"item": "soup_can", "qty": 2}, {"item": "chips_pack", "qty": 1}, {"item": "juice_box", "qty": 1}]
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
    assert t.check_pick("soup_can") is None
    t.record("pick_item", {"item": "soup_can"}, OK)
    assert "already holding soup_can" in t.check_pick("soup_can")
    t.record("place_item", {"target": "basket"}, {"success": True, "message": "placed soup_can in basket"})
    pack(t, "soup_can")
    assert "already complete (2/2 packed)" in t.check_pick("soup_can")
    assert "not in this order" in t.check_pick("milk")
    print("PASS: test_pick_guards")


def test_failed_pick_changes_nothing():
    t = new_tracker()
    t.record("pick_item", {"item": "soup_can"}, {"success": False, "message": "grasp failed"})
    assert t.holding is None and t.check_pick("soup_can") is None
    print("PASS: test_failed_pick_changes_nothing")


def test_place_guards():
    t = new_tracker()
    t.record("navigate_to", {"station": "shelf_A"}, OK)
    t.record("pick_item", {"item": "soup_can"}, OK)
    assert t.check_place("basket") is None
    assert t.check_place("return") is None  # at its own shelf

    t.record("navigate_to", {"station": "shelf_B"}, OK)
    reason = t.check_place("return")
    assert "belongs on shelf_A" in reason and "navigate_to('shelf_A')" in reason

    # Basket refused when the item is already complete
    t2 = new_tracker()
    pack(t2, "chips_pack")
    t2.record("pick_item", {"item": "chips_pack"}, OK)  # e.g. robot picked it before the guard existed
    assert "already complete (1/1 packed)" in t2.check_place("basket")
    print("PASS: test_place_guards")


def test_extra_and_complete():
    t = new_tracker()
    assert pack(t, "soup_can") == "packed"
    assert pack(t, "soup_can") == "packed"
    assert pack(t, "soup_can") == "extra"
    assert t.extras() == [{"item": "soup_can", "qty": 1}]
    pack(t, "chips_pack")
    pack(t, "juice_box")
    assert t.missing() == []
    assert not t.is_complete(), "extra item -> not complete"
    print("PASS: test_extra_and_complete")


def test_progress_line():
    t = new_tracker()
    t.record("navigate_to", {"station": "shelf_A"}, OK)
    pack(t, "soup_can")
    t.record("pick_item", {"item": "soup_can"}, OK)
    line = t.progress_line()
    assert line.startswith("Progress: soup_can 1/2 packed, chips_pack 0/1 packed, juice_box 0/1 packed."), line
    assert "Holding: soup_can. Robot at: shelf_A." in line, line

    t.record("place_item", {"target": "basket"}, {"success": True, "message": "placed soup_can in basket"})
    pack(t, "chips_pack")
    pack(t, "juice_box")
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
