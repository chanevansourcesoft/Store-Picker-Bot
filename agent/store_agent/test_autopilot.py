"""
Tests for store_agent/autopilot.py (the offline planner used without NEBIUS_API_KEY)
and the offline order parser.

Run:
    python -m store_agent.test_autopilot
"""

from unittest.mock import patch

from store_agent.agent import run_agent
from store_agent.autopilot import Autopilot
from store_agent.inventory import DEFAULT_INVENTORY
from store_agent.order_parser import parse_order_offline
from store_agent.testing_fakes import FakeRobot


def run_autopilot(order_items, **robot_failures):
    """Run the real agent loop with the autopilot and a fake robot."""
    robot = FakeRobot(DEFAULT_INVENTORY, **robot_failures)
    order = {"items": order_items, "unknown": []}
    with patch("store_agent.tools.robot_client", robot), patch("store_agent.agent.robot_client", robot), \
            patch("store_agent.tools.load_inventory", return_value=DEFAULT_INVENTORY), \
            patch("store_agent.agent.load_inventory", return_value=DEFAULT_INVENTORY):
        bill, log = run_agent(order, client=Autopilot())
    return bill, log, robot


def test_packs_whole_order():
    bill, log, robot = run_autopilot([{"item": "chips_packet", "qty": 2}, {"item": "hide_and_seek", "qty": 1}])
    assert bill["status"] == "done", bill
    assert bill["total"] == 2 * 20 + 30, bill
    assert not any(e["result"].get("refused") for e in log), "autopilot must never be refused"
    stations = [arg for name, arg in robot.commands if name == "navigate"]
    assert stations == ["shelf_A", "shelf_B", "customer", "home"], stations
    print("PASS: test_packs_whole_order")


def test_retries_grasp_failures():
    bill, log, robot = run_autopilot([{"item": "frooti", "qty": 1}], pick_fail=(1,))
    assert bill["status"] == "done", bill
    assert robot.picks_sent("frooti") == 2, robot.commands
    assert robot.basket == ["frooti"]
    assert all(set(e["input"]) == {"item"} for e in log if e["tool"] == "pick_item"), "item id only"
    print("PASS: test_retries_grasp_failures")


def test_gives_up_and_still_delivers():
    """Every pick fails: skip the item after 3 tries, deliver what was packed, go home."""
    bill, log, robot = run_autopilot([{"item": "chips_packet", "qty": 1}, {"item": "hide_and_seek", "qty": 1}],
                                     pick_fail=(1, 2, 3))
    assert bill["status"] == "done_with_problems", bill
    assert bill["items"] == [{"item": "hide_and_seek", "qty": 1, "price": 30}], bill
    assert robot.picks_sent("chips_packet") == 3, robot.commands
    assert robot.station == "home"
    print("PASS: test_gives_up_and_still_delivers")


def test_navigation_retry():
    bill, log, robot = run_autopilot([{"item": "chips_packet", "qty": 1}], nav_fail=(1,))
    assert bill["status"] == "done", bill
    print("PASS: test_navigation_retry")


def test_offline_parser():
    inv = DEFAULT_INVENTORY
    assert parse_order_offline("2 chips packets and one hide and seek", inv) == {
        "items": [{"item": "chips_packet", "qty": 2}, {"item": "hide_and_seek", "qty": 1}], "unknown": []}
    assert parse_order_offline("a Hide & Seek, 2 mentos", inv)["items"] == [
        {"item": "hide_and_seek", "qty": 1}, {"item": "mentos", "qty": 2}]
    assert parse_order_offline("biscuits and a mango drink", inv)["items"] == [
        {"item": "hide_and_seek", "qty": 1}, {"item": "frooti", "qty": 1}]
    parsed = parse_order_offline("three frootis, a pizza please", inv)
    assert parsed["items"] == [{"item": "frooti", "qty": 3}], parsed
    assert parsed["unknown"] == ["pizza"], parsed
    assert parse_order_offline("chips and chips", inv)["items"] == [{"item": "chips_packet", "qty": 2}]
    print("PASS: test_offline_parser")


def run_all_tests():
    print("Running autopilot tests...\n")
    test_packs_whole_order()
    test_retries_grasp_failures()
    test_gives_up_and_still_delivers()
    test_navigation_retry()
    test_offline_parser()
    print("\nAll autopilot tests passed!")


if __name__ == "__main__":
    run_all_tests()
