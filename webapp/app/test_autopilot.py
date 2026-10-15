"""
Tests for app/autopilot.py (the offline planner used without NEBIUS_API_KEY)
and the offline order parser.

Run:
    python -m app.test_autopilot
"""

from unittest.mock import patch

from app.agent import run_agent
from app.autopilot import Autopilot
from app.config import DEFAULT_INVENTORY
from app.order_parser import parse_order_offline
from app.testing_fakes import FakeRobot


def run_autopilot(order_items, **robot_failures):
    """Run the real agent loop with the autopilot and a fake robot."""
    robot = FakeRobot(DEFAULT_INVENTORY, **robot_failures)
    order = {"items": order_items, "unknown": []}
    with patch("app.tools.robot_client", robot), patch("app.agent.robot_client", robot), \
            patch("app.tools.load_inventory", return_value=DEFAULT_INVENTORY), \
            patch("app.agent.load_inventory", return_value=DEFAULT_INVENTORY):
        bill, log = run_agent(order, client=Autopilot())
    return bill, log, robot


def test_packs_whole_order():
    bill, log, robot = run_autopilot([{"item": "soup_can", "qty": 2}, {"item": "chips_pack", "qty": 1}])
    assert bill["status"] == "done", bill
    assert bill["total"] == 2 * 60 + 20, bill
    assert not any(e["result"].get("refused") for e in log), "autopilot must never be refused"
    stations = [arg for name, arg in robot.commands if name == "navigate"]
    assert stations == ["shelf_A", "shelf_B", "customer", "home"], stations
    print("PASS: test_packs_whole_order")


def test_retries_grasp_and_scan_failures():
    bill, log, robot = run_autopilot([{"item": "juice_box", "qty": 1}], pick_fail=(1,), scan_fail=(1,))
    assert bill["status"] == "done", bill
    assert robot.picks_sent("juice_box") == 2, robot.commands
    assert robot.scan_count == 2, robot.commands
    print("PASS: test_retries_grasp_and_scan_failures")


def test_gives_up_and_still_delivers():
    """Every pick fails: skip the item after 3 tries, deliver what was packed, go home."""
    bill, log, robot = run_autopilot([{"item": "soup_can", "qty": 1}, {"item": "chips_pack", "qty": 1}],
                                     pick_fail=(1, 2, 3))
    assert bill["status"] == "done_with_problems", bill
    assert bill["items"] == [{"item": "chips_pack", "qty": 1, "price": 20}], bill
    assert robot.picks_sent("soup_can") == 3, robot.commands
    assert robot.station == "home"
    print("PASS: test_gives_up_and_still_delivers")


def test_navigation_retry():
    bill, log, robot = run_autopilot([{"item": "soup_can", "qty": 1}], nav_fail=(1,))
    assert bill["status"] == "done", bill
    print("PASS: test_navigation_retry")


def test_offline_parser():
    inv = DEFAULT_INVENTORY
    assert parse_order_offline("2 soup cans and one chips", inv) == {
        "items": [{"item": "soup_can", "qty": 2}, {"item": "chips_pack", "qty": 1}], "unknown": []}
    parsed = parse_order_offline("three juices, a pizza please", inv)
    assert parsed["items"] == [{"item": "juice_box", "qty": 3}], parsed
    assert parsed["unknown"] == ["pizza"], parsed
    assert parse_order_offline("soup and soup", inv)["items"] == [{"item": "soup_can", "qty": 2}]
    print("PASS: test_offline_parser")


def run_all_tests():
    print("Running autopilot tests...\n")
    test_packs_whole_order()
    test_retries_grasp_and_scan_failures()
    test_gives_up_and_still_delivers()
    test_navigation_retry()
    test_offline_parser()
    print("\nAll autopilot tests passed!")


if __name__ == "__main__":
    run_all_tests()
