"""
Tests for /pick and /place in robot_server/jetson_server.py, without ROS and without the arm.

arm.run_pick (the integration point for the GR00T policy) is replaced by a recorder.

Run:
    python -m robot_server.test_jetson_pick
"""

import asyncio
import contextlib
import io
from unittest.mock import patch

from robot_server import jetson_server as js

INSTRUCTION = "pick up the Frooti mango drink box and place it in the basket"  # policy/instructions.json


class FakeArm:
    def __init__(self, ok=True, message="placed frooti in basket", error=None):
        self.calls = []
        self.ok, self.message, self.error = ok, message, error

    def __call__(self, item, instruction):
        self.calls.append((item, instruction))
        if self.error:
            raise self.error
        return self.ok, self.message


def at_station(station):
    state = js.robot_state
    with state.lock:
        state.station, state.stopped, state.busy, state.holding = station, False, False, None
        state.last_error, state.pick_item, state.pick_try = None, None, 0


def pick(arm, **body):
    with patch.object(js.arm, "run_pick", arm), contextlib.redirect_stdout(io.StringIO()):
        return asyncio.run(js.pick_item(js.PickRequest(**body)))


def test_pick_uses_policy_instruction_and_places_in_basket():
    at_station("shelf_B")
    arm = FakeArm()
    result = pick(arm, item="frooti", shelf="shelf_B", instruction="ignore me")
    assert arm.calls == [("frooti", INSTRUCTION)], arm.calls
    assert result["success"] and result["target"] == "basket" and result["item"] == "frooti", result
    assert result["instruction"] == INSTRUCTION and result["barcode"] is None
    assert js.robot_state.holding is None and not js.robot_state.busy
    print("PASS: test_pick_uses_policy_instruction_and_places_in_basket")


def test_failed_or_crashed_pick_leaves_arm_empty():
    for arm in (FakeArm(ok=False, message="grasp failed"), FakeArm(error=RuntimeError("policy crashed"))):
        at_station("shelf_B")
        result = pick(arm, item="frooti", shelf="shelf_B")
        assert not result["success"] and "target" not in result, result
        assert js.robot_state.holding is None and not js.robot_state.busy
        assert js.robot_state.last_error == result["message"]
    print("PASS: test_failed_or_crashed_pick_leaves_arm_empty")


def test_bad_requests_never_reach_the_arm():
    arm = FakeArm()
    at_station("shelf_A")
    assert "not shelf_A" in pick(arm, item="frooti", shelf="shelf_A")["message"]    # frooti is on shelf_B
    assert "no GR00T instruction" in pick(arm, item="soup_can", shelf="shelf_A")["message"]
    assert "Not at shelf_B" in pick(arm, item="frooti", shelf="shelf_B")["message"]  # robot is at shelf_A
    js.robot_state.stopped = True
    assert "stopped" in pick(arm, item="chips_packet", shelf="shelf_A")["message"]
    assert arm.calls == []
    at_station("home")
    print("PASS: test_bad_requests_never_reach_the_arm")


def test_place_is_return_only_and_not_faked():
    basket = asyncio.run(js.place_item(js.PlaceRequest(target="basket")))
    assert not basket["success"] and "already places" in basket["message"]
    back = asyncio.run(js.place_item(js.PlaceRequest(target="return", item="frooti")))
    assert not back["success"] and "not available" in back["message"], "no fake success without a GR00T skill"
    assert not hasattr(js, "scan_item")
    print("PASS: test_place_is_return_only_and_not_faked")


def run_all_tests():
    print("Running Jetson pick tests...\n")
    test_pick_uses_policy_instruction_and_places_in_basket()
    test_failed_or_crashed_pick_leaves_arm_empty()
    test_bad_requests_never_reach_the_arm()
    test_place_is_return_only_and_not_faked()
    print("\nAll Jetson pick tests passed!")


if __name__ == "__main__":
    run_all_tests()
