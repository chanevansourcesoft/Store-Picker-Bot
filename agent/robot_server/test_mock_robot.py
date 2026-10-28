"""
Tests for robot_server/mock_robot.py: last_error, the GR00T try print, and the pick contract
(one pick = pick AND place in the basket, instruction from policy/instructions.json).

Run:
    python -m robot_server.test_mock_robot
"""

import store_agent.testing_store  # noqa: F401  (first: the tests use the test store in agent/testdata)
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


def test_answers_like_the_jetson():
    """Same fields as jetson_server.py: retryable, needs_operator, pick_try, level; ids only."""
    reset_robot()
    mock.navigate(mock.NavigateRequest(station="shelf_A"))
    failed, _ = pick_chips(fail=True)
    assert failed["retryable"] and not failed["needs_operator"] and failed["pick_try"] == 1, failed
    placed, _ = pick_chips(fail=False)
    assert placed["level"] == "upper_shelf" and placed["shelf"] == "shelf_A" and placed["pick_try"] == 2, placed
    with contextlib.redirect_stdout(io.StringIO()):
        for item, problem in (("", "no item id"), ("pick up the chips", "not a product id"),
                              ("soup_can", "no GR00T instruction")):
            result = mock.pick(mock.PickRequest(item=item, shelf="shelf_A"))
            assert not result["success"] and problem in result["message"] and not result["retryable"], result
    assert mock.health()["arm_ready"] is True
    print("PASS: test_answers_like_the_jetson")


def test_gripper_still_holding_blocks_until_resume():
    reset_robot()
    mock.navigate(mock.NavigateRequest(station="shelf_A"))
    mock.PICK_HOLD_RATE = 1.0
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            result = mock.pick(mock.PickRequest(item="chips_packet", shelf="shelf_A"))
    finally:
        mock.PICK_HOLD_RATE = 0.0
    assert not result["success"] and result["needs_operator"] and not result["retryable"], result
    assert mock.status()["holding"] == "chips_packet" and mock.status()["needs_operator"]
    assert "check the arm" in mock.navigate(mock.NavigateRequest(station="customer"))["message"]
    mock.resume()
    assert mock.status()["needs_operator"] is None and mock.status()["holding"] is None
    print("PASS: test_gripper_still_holding_blocks_until_resume")


def run_all_tests():
    print("Running mock robot tests...\n")
    test_error_cleared_after_success()
    test_groot_print_shows_try_and_result()
    test_one_print_per_pick()
    test_pick_places_in_basket_with_policy_instruction()
    test_return_only_at_own_shelf()
    test_answers_like_the_jetson()
    test_gripper_still_holding_blocks_until_resume()
    reset_robot()
    print("\nAll mock robot tests passed!")


if __name__ == "__main__":
    run_all_tests()
