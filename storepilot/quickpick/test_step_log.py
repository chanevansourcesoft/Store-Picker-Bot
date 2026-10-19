"""
Tests for quickpick/step_log.py

Run:
    python -m quickpick.test_step_log
"""

from quickpick.step_log import is_recovered, step_target


def step(tool, data, success, message=""):
    return {"tool": tool, "input": data, "result": {"success": success, "message": message}}


# Same steps as order #17 (hide and seek pick failed once, frooti scan failed once)
ORDER_17 = [
    step("navigate_to", {"station": "shelf_B"}, True, "arrived at shelf_B"),          # 0
    step("pick_item", {"item": "hide_and_seek"}, False, "grasp failed"),                  # 1
    step("pick_item", {"item": "hide_and_seek"}, True, "picked"),                         # 2
    step("scan_item", {}, True),                                                       # 3
    step("place_item", {"target": "basket"}, True, "placed hide_and_seek in basket"),     # 4
    step("navigate_to", {"station": "shelf_C"}, True, "arrived at shelf_C"),          # 5
    step("pick_item", {"item": "frooti"}, True, "picked"),                          # 6
    step("scan_item", {}, False, "barcode not readable"),                              # 7
    step("place_item", {"target": "return"}, True, "returned frooti to shelf"),     # 8
    step("pick_item", {"item": "frooti"}, True, "picked"),                          # 9
    step("scan_item", {}, True),                                                       # 10
    step("place_item", {"target": "basket"}, True, "placed frooti in basket"),      # 11
]


def test_retried_failures_are_recovered():
    assert is_recovered(ORDER_17, 1)  # grasp failed on chips, retry worked
    assert is_recovered(ORDER_17, 7)  # scan failed on frooti, retry worked
    print("PASS: test_retried_failures_are_recovered")


def test_scan_target_is_held_item():
    assert step_target(ORDER_17, 7) == "frooti"
    assert step_target(ORDER_17, 3) == "hide_and_seek"
    print("PASS: test_scan_target_is_held_item")


def test_failure_without_retry_stays_failed():
    log = [
        step("pick_item", {"item": "chips_packet"}, False, "grasp failed"),
        step("pick_item", {"item": "hide_and_seek"}, True, "picked"),  # different item
    ]
    assert not is_recovered(log, 0)
    print("PASS: test_failure_without_retry_stays_failed")


def test_navigation_retry_is_recovered():
    log = [
        step("navigate_to", {"station": "shelf_A"}, False, "navigation failed, obstacle detected"),
        step("navigate_to", {"station": "shelf_A"}, True, "arrived at shelf_A"),
    ]
    assert is_recovered(log, 0)
    print("PASS: test_navigation_retry_is_recovered")


def run_all_tests():
    print("Running step log tests...\n")
    test_retried_failures_are_recovered()
    test_scan_target_is_held_item()
    test_failure_without_retry_stays_failed()
    test_navigation_retry_is_recovered()
    print("\nAll step log tests passed!")


if __name__ == "__main__":
    run_all_tests()
