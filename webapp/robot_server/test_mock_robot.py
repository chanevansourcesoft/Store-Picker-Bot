"""
Tests for robot_server/mock_robot.py: last_error and the GR00T try print.

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
mock.SCAN_FAIL_RATE = 0


def reset_robot():
    mock.resume()
    mock.state.update(holding=None, from_shelf=None, station="home", pick_item=None, pick_try=0)


def pick_soup(fail):
    """Pick a soup can. fail=True forces 'grasp failed'. Returns (result, printed text)."""
    mock.PICK_FAIL_RATE = 1.0 if fail else 0.0
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = mock.pick(mock.PickRequest(item="soup_can", shelf="shelf_A", instruction="pick up the soup can"))
    return result, out.getvalue().strip()


def test_error_cleared_after_success():
    reset_robot()
    mock.navigate(mock.NavigateRequest(station="shelf_A"))
    result, _ = pick_soup(fail=True)
    assert not result["success"]
    assert mock.status()["last_error"] == "grasp failed"

    result, _ = pick_soup(fail=False)
    assert result["success"]
    assert mock.status()["last_error"] is None
    print("PASS: test_error_cleared_after_success")


def test_groot_print_shows_try_and_result():
    reset_robot()
    mock.navigate(mock.NavigateRequest(station="shelf_A"))
    _, line1 = pick_soup(fail=True)
    _, line2 = pick_soup(fail=False)
    assert line1 == "[GR00T] pick up the soup can (try 1) -> grasp failed", line1
    assert line2 == "[GR00T] pick up the soup can (try 2) -> picked", line2

    # Next unit (after it is in the basket) starts at try 1 again
    mock.scan()
    mock.place(mock.PlaceRequest(target="basket"))
    _, line3 = pick_soup(fail=False)
    assert line3 == "[GR00T] pick up the soup can (try 1) -> picked", line3
    print("PASS: test_groot_print_shows_try_and_result")


def test_one_print_per_pick():
    reset_robot()
    mock.navigate(mock.NavigateRequest(station="shelf_A"))
    _, text = pick_soup(fail=False)
    assert text.count("[GR00T]") == 1, text
    print("PASS: test_one_print_per_pick")


def test_return_only_at_own_shelf():
    reset_robot()
    mock.navigate(mock.NavigateRequest(station="shelf_A"))
    pick_soup(fail=False)
    mock.navigate(mock.NavigateRequest(station="shelf_B"))
    result = mock.place(mock.PlaceRequest(target="return"))
    assert not result["success"] and "belongs on shelf_A" in result["message"], result
    assert mock.state["holding"] == "soup_can", "Still holding after a refused return"

    mock.navigate(mock.NavigateRequest(station="shelf_A"))
    assert mock.place(mock.PlaceRequest(target="return"))["success"]
    print("PASS: test_return_only_at_own_shelf")


def run_all_tests():
    print("Running mock robot tests...\n")
    test_error_cleared_after_success()
    test_groot_print_shows_try_and_result()
    test_one_print_per_pick()
    test_return_only_at_own_shelf()
    reset_robot()
    print("\nAll mock robot tests passed!")


if __name__ == "__main__":
    run_all_tests()
