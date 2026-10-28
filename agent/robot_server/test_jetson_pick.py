"""
Tests for /pick, /stop, /health and /place in robot_server/jetson_server.py, without ROS and
without the arm.

arm.run_pick / arm.arm_ready / arm.stop_arm (the integration point for the policy worker) are
replaced by fakes. test_arm_uses_policy_pick_client goes through the real
policy/inference/pick_client.py, against a fake policy worker on a local port.

Run:
    python -m robot_server.test_jetson_pick
"""

import store_agent.testing_store  # noqa: F401  (first: the tests use the test store in agent/testdata)
import asyncio
import contextlib
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from robot_server import arm as arm_module
from robot_server import jetson_server as js

INSTRUCTION = "pick up the Frooti mango drink box and place it in the basket"  # testdata/instructions.json
HOLDS = "no grasp seen: frooti was not picked; the gripper still holds something"


class FakeArm:
    def __init__(self, ok=True, message="placed frooti in basket", error=None, wait=None):
        self.calls = []
        self.ok, self.message, self.error, self.wait = ok, message, error, wait

    def __call__(self, item, instruction):
        self.calls.append((item, instruction))
        if self.wait is not None:
            self.wait.wait(5)       # an episode that runs until the test lets it end
        if self.error:
            raise self.error
        return self.ok, self.message


def at_station(station, arrived_at=0.0):
    state = js.robot_state
    with state.lock:
        state.station, state.nav_state, state.arrived_at = station, "arrived", arrived_at
        state.stopped = state.busy = state.picking = False
        state.holding = state.needs_operator = state.last_error = None
        state.pick_item, state.pick_try = None, 0


@contextlib.contextmanager
def fake_arm(arm, ready=True, stop=(True, "stopped")):
    stops = []
    with patch.object(js.arm, "run_pick", arm), \
            patch.object(js.arm, "arm_ready", lambda: ready), \
            patch.object(js.arm, "stop_arm", lambda: stops.append(1) or stop), \
            patch.object(js, "PICK_SETTLE_SEC", 0.0), \
            contextlib.redirect_stdout(io.StringIO()):
        yield stops


def pick(arm, ready=True, **body):
    with fake_arm(arm, ready):
        return asyncio.run(js.pick_item(js.PickRequest(**body)))


def test_pick_uses_policy_instruction_and_places_in_basket():
    at_station("shelf_B")
    arm = FakeArm()
    result = pick(arm, item="frooti", shelf="shelf_B", instruction="ignore me")
    assert arm.calls == [("frooti", INSTRUCTION)], arm.calls
    assert result["success"] and result["target"] == "basket" and result["item"] == "frooti", result
    assert result["instruction"] == INSTRUCTION and result["barcode"] is None
    assert result["shelf"] == "shelf_B" and result["level"] == "upper_shelf", "station from the catalog, level from policy"
    assert js.robot_state.holding is None and not js.robot_state.busy and not js.robot_state.picking
    print("PASS: test_pick_uses_policy_instruction_and_places_in_basket")


def test_several_units_on_one_rack_without_driving():
    at_station("shelf_A")
    arm = FakeArm(message="placed")
    for item in ("chips_packet", "chips_packet", "mentos"):
        assert pick(arm, item=item, shelf="shelf_A")["success"]
    assert [c[0] for c in arm.calls] == ["chips_packet", "chips_packet", "mentos"]
    assert js.robot_state.station == "shelf_A" and js.robot_state.nav_state == "arrived"
    print("PASS: test_several_units_on_one_rack_without_driving")


def test_failed_or_crashed_pick_leaves_arm_empty():
    for arm in (FakeArm(ok=False, message="no grasp seen: frooti was not picked"),
                FakeArm(ok=False, message="time limit (10 s), no grasp seen"),
                FakeArm(error=RuntimeError("policy crashed"))):
        at_station("shelf_B")
        result = pick(arm, item="frooti", shelf="shelf_B")
        assert not result["success"] and "target" not in result, result
        assert js.robot_state.holding is None and not js.robot_state.busy
        assert js.robot_state.last_error == result["message"]
        assert result["needs_operator"] is False
    print("PASS: test_failed_or_crashed_pick_leaves_arm_empty")


def test_retryable_failures_count_tries():
    at_station("shelf_B")
    arm = FakeArm(ok=False, message="no grasp seen: frooti was not picked")
    first, second = pick(arm, item="frooti", shelf="shelf_B"), pick(arm, item="frooti", shelf="shelf_B")
    assert first["retryable"] and first["outcome"] == "retry" and first["pick_try"] == 1, first
    assert second["pick_try"] == 2
    assert pick(FakeArm(), item="frooti", shelf="shelf_B")["pick_try"] == 3
    assert js.robot_state.pick_try == 0, "the next unit starts at try 1 again"
    print("PASS: test_retryable_failures_count_tries")


def test_gripper_still_holding_needs_a_person():
    at_station("shelf_B")
    result = pick(FakeArm(ok=False, message=HOLDS), item="frooti", shelf="shelf_B")
    assert not result["success"] and result["needs_operator"] and not result["retryable"], result
    assert js.robot_state.holding == "frooti" and js.robot_state.needs_operator == HOLDS

    arm = FakeArm()
    again = pick(arm, item="hide_and_seek", shelf="shelf_B")
    assert not again["success"] and again["needs_operator"] and arm.calls == [], "no pick until a person checks"
    with contextlib.redirect_stdout(io.StringIO()):
        drive = asyncio.run(js.navigate(js.NavigateRequest(station="customer")))
    assert not drive["success"] and "check the arm" in drive["message"], drive

    asyncio.run(js.resume())
    assert js.robot_state.needs_operator is None and js.robot_state.holding is None
    print("PASS: test_gripper_still_holding_needs_a_person")


def test_bad_requests_never_reach_the_arm():
    arm = FakeArm()
    at_station("shelf_A")
    assert "not shelf_A" in pick(arm, item="frooti", shelf="shelf_A")["message"]     # frooti is on shelf_B
    assert "no GR00T instruction" in pick(arm, item="soup_can", shelf="shelf_A")["message"]
    assert "no item id" in pick(arm, item="", shelf="shelf_A")["message"]
    assert "not a product id" in pick(arm, item="pick up the chips please", shelf="shelf_A")["message"]
    assert "not a product id" in pick(arm, item="Chips_Packet", shelf="shelf_A")["message"]
    assert "Not parked at shelf_B" in pick(arm, item="frooti", shelf="shelf_B")["message"]  # robot is at shelf_A
    assert "Arm not ready" in pick(arm, ready=False, item="chips_packet", shelf="shelf_A")["message"]
    js.robot_state.nav_state = "navigating"
    assert "Not parked" in pick(arm, item="chips_packet", shelf="shelf_A")["message"]
    at_station("shelf_A")
    js.robot_state.stopped = True
    assert "stopped" in pick(arm, item="chips_packet", shelf="shelf_A")["message"]
    assert arm.calls == []
    at_station("home")
    print("PASS: test_bad_requests_never_reach_the_arm")


def test_base_settles_before_the_pick():
    at_station("shelf_A", arrived_at=time.monotonic())
    arm = FakeArm()
    with fake_arm(arm), patch.object(js, "PICK_SETTLE_SEC", 0.3):
        start = time.monotonic()
        asyncio.run(js.pick_item(js.PickRequest(item="chips_packet", shelf="shelf_A")))
        waited = time.monotonic() - start
    assert waited >= 0.25 and arm.calls, waited
    print("PASS: test_base_settles_before_the_pick")


class FakeNav2:
    """Nav2 that always arrives, at the poses given (one per goal)."""

    def __init__(self, *poses):
        self.poses, self.goals = list(poses), []

    def send_goal(self, station):
        self.goals.append(station)
        return True

    def measured_pose(self):
        return self.poses[min(len(self.goals), len(self.poses)) - 1]


def navigate_real(nav2, station):
    with patch.object(js, "nav2_client", nav2), patch.object(js, "ROS_AVAILABLE", True), \
            contextlib.redirect_stdout(io.StringIO()):
        return asyncio.run(js.navigate(js.NavigateRequest(station=station)))


def test_rack_parking_is_checked():
    target = js.STATIONS["shelf_A"]
    off = {"x": target["x"] + 0.10, "y": target["y"], "theta": target["theta"]}          # 10 cm off
    turned = {"x": target["x"], "y": target["y"] + 0.01, "theta": target["theta"] - 0.03}  # 1 cm, slightly turned
    at_station("home")
    nav2 = FakeNav2(off, turned)
    result = navigate_real(nav2, "shelf_A")
    assert result["success"] and nav2.goals == ["shelf_A", "shelf_A"], (result, nav2.goals)
    assert result["parked"]["attempt"] == 2 and result["parked"]["xy_cm"] == 1.0
    assert js.robot_state.station == "shelf_A" and js.robot_state.nav_state == "arrived"

    at_station("home")
    nav2 = FakeNav2(off, off)
    result = navigate_real(nav2, "shelf_A")
    assert not result["success"] and len(nav2.goals) == js.PARK_ATTEMPTS and "GR00T needs" in result["message"]
    assert js.robot_state.station is None, "not parked: no pick is allowed"
    assert "Not parked" in pick(FakeArm(), item="chips_packet", shelf="shelf_A")["message"]

    at_station("home")
    nav2 = FakeNav2(off)
    assert navigate_real(nav2, "customer")["success"], "the parking check is for racks only"
    at_station("home")
    print("PASS: test_rack_parking_is_checked")


def test_no_driving_during_a_pick_and_stop_halts_the_arm():
    at_station("shelf_B")
    release = threading.Event()
    arm = FakeArm(ok=False, message="stopped: the arm holds where it is (frooti)", wait=release)

    async def scenario(stops):
        task = asyncio.create_task(js.pick_item(js.PickRequest(item="frooti", shelf="shelf_B")))
        while not arm.calls:
            await asyncio.sleep(0.01)
        assert js.robot_state.picking
        drive = await js.navigate(js.NavigateRequest(station="customer"))
        assert not drive["success"] and "pick is running" in drive["message"], drive
        stopped = await js.emergency_stop()
        assert stopped["success"] and stops == [1], (stopped, stops)
        assert js.robot_state.picking, "the episode is still ending: still no driving"
        release.set()
        return await task

    with fake_arm(arm) as stops:
        result = asyncio.run(scenario(stops))
    assert not result["success"] and result["outcome"] == "stopped" and not result["retryable"], result
    assert js.robot_state.holding is None and not js.robot_state.picking
    asyncio.run(js.resume())
    print("PASS: test_no_driving_during_a_pick_and_stop_halts_the_arm")


def test_stop_reports_an_unconfirmed_arm_stop():
    at_station("shelf_B")
    js.robot_state.picking = True
    with fake_arm(FakeArm(), stop=(False, "policy worker not reachable")):
        result = asyncio.run(js.emergency_stop())
    assert not result["success"] and "ARM STOP NOT CONFIRMED" in result["message"], result
    at_station("home")
    with fake_arm(FakeArm()) as stops:
        result = asyncio.run(js.emergency_stop())
    assert result["success"] and stops == [1], "the arm is told to stop even when no pick runs"
    asyncio.run(js.resume())
    print("PASS: test_stop_reports_an_unconfirmed_arm_stop")


def test_health_reports_arm_ready():
    with patch.object(js.arm, "arm_status", lambda: {"ready": True, "state": "ready", "dry_run": True, "model": "m"}):
        health = js.health_check()
    assert health["status"] == "ok" and health["arm_ready"] is True and health["arm"]["dry_run"] is True
    with patch.object(js.arm, "arm_status", lambda: {"ready": False, "state": "loading"}):
        assert js.health_check()["arm_ready"] is False
    with patch.object(js.arm, "arm_status", lambda: None):
        health = js.health_check()
    assert health["status"] == "ok" and health["arm_ready"] is False and health["arm"]["state"] == "unreachable"
    print("PASS: test_health_reports_arm_ready")


def test_classify_matches_the_policy_worker():
    c = arm_module.classify
    assert c(True, "placed ink_box in basket") == arm_module.PLACED
    assert c(False, "no grasp seen: ink_box was not picked") == arm_module.RETRY
    assert c(False, "time limit (40 s), after a grasp") == arm_module.RETRY
    assert c(False, "time limit (40 s), no grasp seen; the gripper still holds something") == arm_module.HOLDING
    assert c(False, "stopped: the arm holds where it is (ink_box)") == arm_module.STOPPED
    assert c(False, "unknown item 'x' (in instructions.json: ink_box)") == arm_module.REFUSED
    assert c(False, "busy (picking)") == arm_module.REFUSED
    assert c(False, "the policy worker is still starting") == arm_module.NOT_READY
    assert c(False, "policy worker not reachable at http://127.0.0.1:8790: refused") == arm_module.NOT_READY
    assert c(False, "policy error: CUDA out of memory") == arm_module.NOT_READY
    print("PASS: test_classify_matches_the_policy_worker")


class FakeWorker(BaseHTTPRequestHandler):
    """The policy worker's HTTP API (policy/inference/pick_worker.py), with canned answers."""
    requests = []

    def _send(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send({"ready": True, "busy": False, "state": "ready", "dry_run": True})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        FakeWorker.requests.append((self.path, body))
        if self.path == "/pick":
            self._send({"ok": True, "message": f"placed {body['item']} in basket"})
        else:
            self._send({"ok": True, "message": "stopped"})

    def log_message(self, *args):
        pass


def test_arm_uses_policy_pick_client():
    client = arm_module.pick_client
    assert client is not None, arm_module.IMPORT_ERROR
    assert Path(client.__file__).resolve() == (arm_module.INFERENCE_DIR / "pick_client.py").resolve()
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeWorker)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with patch.object(client, "URL", f"http://127.0.0.1:{server.server_address[1]}"):
            assert arm_module.arm_ready() is True
            assert arm_module.run_pick("ink_box", "some text") == (True, "placed ink_box in basket")
            assert arm_module.stop_arm() == (True, "stopped")
            assert arm_module.arm_status()["dry_run"] is True
        assert FakeWorker.requests == [("/pick", {"item": "ink_box"}), ("/stop", {})], "only the id is sent"
    finally:
        server.shutdown()
    with patch.object(client, "URL", "http://127.0.0.1:9"):     # nothing listens: never raises
        assert arm_module.arm_ready() is False
        ok, message = arm_module.run_pick("ink_box")
        assert not ok and "not reachable" in message
    print("PASS: test_arm_uses_policy_pick_client")


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
    test_several_units_on_one_rack_without_driving()
    test_failed_or_crashed_pick_leaves_arm_empty()
    test_retryable_failures_count_tries()
    test_gripper_still_holding_needs_a_person()
    test_bad_requests_never_reach_the_arm()
    test_base_settles_before_the_pick()
    test_rack_parking_is_checked()
    test_no_driving_during_a_pick_and_stop_halts_the_arm()
    test_stop_reports_an_unconfirmed_arm_stop()
    test_health_reports_arm_ready()
    test_classify_matches_the_policy_worker()
    test_arm_uses_policy_pick_client()
    test_place_is_return_only_and_not_faked()
    print("\nAll Jetson pick tests passed!")


if __name__ == "__main__":
    run_all_tests()
