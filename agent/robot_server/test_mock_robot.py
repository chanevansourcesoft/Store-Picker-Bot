"""
Tests for robot_server/mock_robot.py: last_error, the GR00T try print, and the pick contract
(one pick = pick AND place in the basket, instruction from policy/instructions.json).

Run:
    python -m robot_server.test_mock_robot
"""

import contextlib
import io

from robot_server import mock_robot as mock

# No waiting and no random failures, unless a test asks for them
mock.FAKE_DELAY_SEC = 0
mock.NAV_DELAY_SEC = 0
mock.NAV_FAIL_RATE = 0

INSTRUCTION = "pick up the chips packet and place it in the basket"  # policy/instructions.json


def reset_robot():
    mock.resume()
    mock.state.update(holding=None, station="home", pick_item=None, pick_try=0)


def pick_chips(fail):
    """Pick a chips packet. fail=True forces 'grasp failed'. Returns (result, printed text)."""
    mock.PICK_FAIL_RATE = 1.0 if fail else 0.0
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = mock.pick(mock.PickRequest(item="chips_packet", shelf="shelf_A"))
    return result, out.getvalue().strip()


def test_error_cleared_after_success():
    reset_robot()
    mock.navigate(mock.NavigateRequest(station="shelf_A"))
    result, _ = pick_chips(fail=True)
    assert not result["success"]
    assert mock.status()["last_error"] == "grasp failed"

    result, _ = pick_chips(fail=False)
    assert result["success"]
    assert mock.status()["last_error"] is None
    print("PASS: test_error_cleared_after_success")


def test_groot_print_shows_try_and_result():
    reset_robot()
    mock.navigate(mock.NavigateRequest(station="shelf_A"))
    _, line1 = pick_chips(fail=True)
    _, line2 = pick_chips(fail=False)
    assert line1 == f"[GR00T] {INSTRUCTION} (try 1) -> grasp failed", line1
    assert line2 == f"[GR00T] {INSTRUCTION} (try 2) -> placed chips_packet in basket", line2

    # Next unit (the last one is in the basket) starts at try 1 again
    _, line3 = pick_chips(fail=False)
    assert line3 == f"[GR00T] {INSTRUCTION} (try 1) -> placed chips_packet in basket", line3
    print("PASS: test_groot_print_shows_try_and_result")


def test_one_print_per_pick():
    reset_robot()
    mock.navigate(mock.NavigateRequest(station="shelf_A"))
    _, text = pick_chips(fail=False)
    assert text.count("[GR00T]") == 1, text
    print("PASS: test_one_print_per_pick")


def test_pick_places_in_basket_with_policy_instruction():
    """One /pick = pick AND place in the basket. The instruction comes from the policy file,
    whatever the caller sends. There is no /scan any more."""
    reset_robot()
    mock.navigate(mock.NavigateRequest(station="shelf_A"))
    mock.PICK_FAIL_RATE = 0.0
    with contextlib.redirect_stdout(io.StringIO()) as out:
        result = mock.pick(mock.PickRequest(item="chips_packet", shelf="shelf_A", instruction="throw it away"))
    assert result["success"] and result["target"] == "basket" and result["item"] == "chips_packet", result
    assert result["instruction"] == INSTRUCTION and "throw it away" not in out.getvalue()
    assert mock.status()["holding"] is None, "the arm is empty after the run"
    assert not hasattr(mock, "scan")

    assert not mock.pick(mock.PickRequest(item="frooti", shelf="shelf_A"))["success"], "frooti is on shelf_B"
    assert not mock.pick(mock.PickRequest(item="frooti", shelf="shelf_B"))["success"], "robot is at shelf_A"
    assert "already places" in mock.place(mock.PlaceRequest(target="basket"))["message"]
    print("PASS: test_pick_places_in_basket_with_policy_instruction")


def test_return_only_at_own_shelf():
    reset_robot()
    mock.navigate(mock.NavigateRequest(station="shelf_A"))
    pick_chips(fail=False)
    mock.navigate(mock.NavigateRequest(station="shelf_B"))
    result = mock.place(mock.PlaceRequest(target="return", item="chips_packet"))
    assert not result["success"] and "belongs on shelf_A" in result["message"], result

    mock.navigate(mock.NavigateRequest(station="shelf_A"))
    result = mock.place(mock.PlaceRequest(target="return", item="chips_packet"))
    assert result["success"] and result["item"] == "chips_packet" and result["target"] == "return", result
    print("PASS: test_return_only_at_own_shelf")


def run_all_tests():
    print("Running mock robot tests...\n")
    test_error_cleared_after_success()
    test_groot_print_shows_try_and_result()
    test_one_print_per_pick()
    test_pick_places_in_basket_with_policy_instruction()
    test_return_only_at_own_shelf()
    reset_robot()
    print("\nAll mock robot tests passed!")


if __name__ == "__main__":
    run_all_tests()
