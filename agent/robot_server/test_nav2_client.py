"""
Tests for Nav2Client.send_goal in robot_server/jetson_server.py, without ROS.

The Nav2 action client is replaced by fakes whose futures the test finishes by hand,
so we can check that a result never carries over from one goal to the next.

Run:
    python -m robot_server.test_nav2_client
"""

import store_agent.testing_store  # noqa: F401  (first: the tests use the test store in agent/testdata)
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from robot_server import jetson_server as js

SUCCEEDED, ABORTED = 4, 6


class Msg:
    """Stand-in for a ROS message: any attribute path can be set."""

    def __getattr__(self, name):
        value = Msg()
        object.__setattr__(self, name, value)
        return value


class Future:
    def __init__(self):
        self.value = None
        self.callbacks = []

    def result(self):
        return self.value

    def add_done_callback(self, callback):
        self.callbacks.append(callback)

    def finish(self, value):
        self.value = value
        for callback in self.callbacks:
            callback(self)


class Goal:
    """One goal sent to the fake Nav2: the test accepts it and gives its result."""

    def __init__(self):
        self.response = Future()
        self.result = Future()
        self.cancelled = False

    def accept(self):
        handle = SimpleNamespace(accepted=True, get_result_async=lambda: self.result,
                                 cancel_goal_async=lambda: setattr(self, "cancelled", True))
        self.response.finish(handle)

    def end(self, status):
        self.result.finish(SimpleNamespace(status=status, result=None))


class FakeActionClient:
    def __init__(self):
        self.goals = []

    def wait_for_server(self, timeout_sec):
        return True

    def send_goal_async(self, goal_msg, feedback_callback=None):
        goal = Goal()
        self.goals.append(goal)
        return goal.response


def make_client():
    client = object.__new__(js.Nav2Client)   # no ROS node: set only what send_goal uses
    client._action_client = FakeActionClient()
    client._goal_handle = None
    client._goal_lock = threading.Lock()
    client._goal_seq = 0
    client._navigation_complete = threading.Event()
    client._navigation_result = None
    client._read_tolerance = lambda: None   # no parameter service: the tolerance stays as it is
    client._write_tolerance = lambda xy, yaw: False
    client._normal_tolerance = client._tolerance = None
    client._goal_pose = client.last_feedback_pose = None
    quiet =SimpleNamespace(info=lambda *a: None, warn=lambda *a: None, error=lambda *a: None)
    client.get_logger = lambda: quiet
    client.get_clock = lambda: SimpleNamespace(now=lambda: SimpleNamespace(to_msg=lambda: None))
    return client


def start_goal(client, station):
    """Run send_goal in a thread. Returns (thread, box); box["result"] is set when it returns."""
    box = {}
    count = len(client._action_client.goals)
    thread = threading.Thread(target=lambda: box.update(result=client.send_goal(station)))
    thread.start()
    deadline = time.monotonic() + 5
    while len(client._action_client.goals) == count:   # wait until the goal is sent
        assert thread.is_alive() and time.monotonic() < deadline, "send_goal ended (or hung) before sending a goal"
        time.sleep(0.01)
    return thread, box, client._action_client.goals[-1]


def ros_fakes(timeout=5.0):
    return [patch.object(js, "NavigateToPose", SimpleNamespace(Goal=Msg), create=True),
            patch.object(js, "PoseStamped", Msg, create=True),
            patch.object(js, "NAV_TIMEOUT_SEC", timeout)]


def run_patched(test, timeout=5.0):
    patches = ros_fakes(timeout)
    for p in patches:
        p.start()
    try:
        test()
    finally:
        for p in reversed(patches):
            p.stop()


def test_success_and_failure():
    def test():
        client = make_client()
        thread, box, goal = start_goal(client, "shelf_A")
        goal.accept()
        goal.end(SUCCEEDED)
        thread.join(2)
        assert box["result"] is True

        thread, box, goal = start_goal(client, "shelf_B")
        goal.accept()
        goal.end(ABORTED)
        thread.join(2)
        assert box["result"] is False
    run_patched(test)
    print("PASS: test_success_and_failure")


def test_timeout_does_not_reuse_last_result():
    """Goal 1 succeeds. Goal 2 never answers: it must fail, not return goal 1's success."""
    def test():
        client = make_client()
        thread, box, goal = start_goal(client, "shelf_A")
        goal.accept()
        goal.end(SUCCEEDED)
        thread.join(2)
        assert box["result"] is True

        thread, box, goal = start_goal(client, "shelf_B")
        goal.accept()                     # accepted, but no result before the timeout
        thread.join(3)
        assert box["result"] is False, "a timed-out goal must fail"
        assert goal.cancelled, "a timed-out goal is cancelled"
        assert js.robot_state.last_error == "Navigation timed out"
    run_patched(test, timeout=0.3)
    print("PASS: test_timeout_does_not_reuse_last_result")


def test_late_result_of_old_goal_is_ignored():
    """Goal 1 times out. While goal 2 runs, goal 1's late SUCCEEDED arrives: goal 2 must
    keep waiting for its own result."""
    def test():
        client = make_client()
        thread, box, old = start_goal(client, "shelf_A")
        old.accept()
        thread.join(3)
        assert box["result"] is False

        js.NAV_TIMEOUT_SEC = 5.0
        thread, box, new = start_goal(client, "shelf_B")
        new.accept()
        old.end(SUCCEEDED)                # late answer for the old goal
        time.sleep(0.2)
        assert "result" not in box, "the old goal's result must not finish the new goal"
        new.end(ABORTED)
        thread.join(2)
        assert box["result"] is False
    run_patched(test, timeout=0.3)
    print("PASS: test_late_result_of_old_goal_is_ignored")


def test_rack_goal_uses_tight_tolerance():
    """A rack goal sets PARK_*_TOLERANCE on the Nav2 goal checker; other goals get Nav2's own back."""
    client = make_client()
    writes = []
    client._read_tolerance = lambda: (0.25, 0.25)
    client._write_tolerance = lambda xy, yaw: writes.append((xy, yaw)) or True
    assert client.apply_tolerance("shelf_A")
    assert client.apply_tolerance("shelf_B")          # same values: nothing written again
    assert client.apply_tolerance("customer")
    assert writes == [(js.PARK_XY_TOLERANCE, js.PARK_YAW_TOLERANCE), (0.25, 0.25)], writes

    client = make_client()                            # no parameter service: the goal is still sent
    assert client.apply_tolerance("shelf_A") is False

    def test():
        broken = make_client()
        broken.apply_tolerance = lambda station: 1 / 0   # a ROS service error must not stop the goal
        thread, box, goal = start_goal(broken, "shelf_A")
        goal.accept()
        goal.end(SUCCEEDED)
        thread.join(2)
        assert box["result"] is True
    run_patched(test)
    print("PASS: test_rack_goal_uses_tight_tolerance")


def test_parking_error():
    distance, turn = js.parking_error({"x": 1.53, "y": 0.04, "theta": 1.57 + 0.1}, {"x": 1.5, "y": 0.0, "theta": 1.57})
    assert abs(distance - 0.05) < 1e-9 and abs(turn - 0.1) < 1e-9
    _, turn = js.parking_error({"x": 0, "y": 0, "theta": 3.1}, {"x": 0, "y": 0, "theta": -3.1})
    assert abs(turn - (2 * 3.141592653589793 - 6.2)) < 1e-9, "the heading difference wraps around"
    print("PASS: test_parking_error")


def run_all_tests():
    print("Running Nav2 client tests...\n")
    test_success_and_failure()
    test_timeout_does_not_reuse_last_result()
    test_late_result_of_old_goal_is_ignored()
    test_rack_goal_uses_tight_tolerance()
    test_parking_error()
    print("\nAll Nav2 client tests passed!")


if __name__ == "__main__":
    run_all_tests()
