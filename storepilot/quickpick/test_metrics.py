"""
Tests for quickpick/metrics.py (owner dashboard numbers).

Run:
    python -m quickpick.test_metrics
"""

from datetime import datetime

from quickpick import metrics


def step(tool, ok=True, time="2026-10-15T10:00:10", message="", refused=False, **inp):
    result = {"success": ok, "message": message}
    if refused:
        result["refused"] = True
    return {"tool": tool, "input": inp, "result": result, "time": time}


ORDER = {
    "id": 1, "status": "done", "created_at": "2026-10-15T10:00:00",
    "items": [{"item": "soup_can", "qty": 2}],
    "bill": {"total": 120}, "problems": [],
    "step_log": [
        step("navigate_to", time="2026-10-15T10:00:05", station="shelf_A"),
        step("pick_item", ok=False, time="2026-10-15T10:00:20", item="soup_can"),
        step("pick_item", time="2026-10-15T10:00:30", item="soup_can"),
        step("pick_item", refused=True, ok=False, time="2026-10-15T10:00:31", item="soup_can"),
        step("place_item", message="placed soup_can in basket", time="2026-10-15T10:00:40", target="basket"),
        step("place_item", message="placed soup_can in basket", time="2026-10-15T10:01:05", target="basket"),
    ],
}


def test_order_times():
    wait, pack = metrics.order_times(ORDER)
    assert (wait, pack) == (5, 60), (wait, pack)
    assert metrics.order_times({"created_at": "x", "step_log": []}) == (None, None)
    print("PASS: test_order_times")


def test_pick_stats_skips_refused():
    assert metrics.pick_stats([ORDER]) == (1, 2)
    print("PASS: test_pick_stats_skips_refused")


def test_order_progress():
    assert metrics.order_progress(ORDER) == (2, 2)
    assert metrics.order_progress({"items": [{"item": "soup_can", "qty": 3}], "step_log": []}) == (0, 3)
    print("PASS: test_order_progress")


def test_orders_by_hour():
    now = datetime(2026, 10, 15, 12, 30)
    orders = [{"created_at": "2026-10-15T03:10:00"}, {"created_at": "2026-10-15T12:01:00"},
              {"created_at": "2026-10-15T12:20:00"}, {"created_at": "2026-10-14T12:00:00"}]
    hours = metrics.orders_by_hour(orders, now=now)
    assert hours[0] == (3, 1) and hours[-1] == (12, 2), hours
    assert len(metrics.orders_by_hour([], now=now)) == 8
    print("PASS: test_orders_by_hour")


def test_format_duration():
    assert metrics.format_duration(None) == "-"
    assert metrics.format_duration(42) == "42s"
    assert metrics.format_duration(75) == "1m 15s"
    print("PASS: test_format_duration")


def test_attention_items():
    robot = {"online": True, "stopped": False, "battery": 15, "obstacle": False}
    items = metrics.attention_items(robot, {"failed": 1, "queued": 0}, {"Soup": 0, "Chips": 2, "Juice": 9}, 3, True)
    texts = " | ".join(t for _, _, t in items)
    assert items[0][0] == "critical" and "Battery 15%" in texts, items
    assert "Out of stock: Soup" in texts and "Low stock: Chips (2)" in texts and "1 order failed" in texts
    stopped = metrics.attention_items(dict(robot, stopped=True), {}, {}, 3, False)
    assert stopped[0][2].startswith("Emergency stop"), stopped
    assert stopped[-1][0] == "info", "missing API key is a note"
    assert metrics.attention_items(dict(robot, battery=90), {}, {"Soup": 9}, 3, True) == []
    print("PASS: test_attention_items")


def test_orders_csv():
    text = metrics.orders_csv([ORDER])
    lines = text.strip().splitlines()
    assert lines[0].startswith("order_id,created_at,status")
    assert lines[1].startswith("1,2026-10-15T10:00:00,done,2x soup_can,120,5,60"), lines[1]
    print("PASS: test_orders_csv")


def run_all_tests():
    print("Running metrics tests...\n")
    test_order_times()
    test_pick_stats_skips_refused()
    test_order_progress()
    test_orders_by_hour()
    test_format_duration()
    test_attention_items()
    test_orders_csv()
    print("\nAll metrics tests passed!")


if __name__ == "__main__":
    run_all_tests()
