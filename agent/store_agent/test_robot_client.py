"""
Tests for store_agent/robot_client.py: the /health answer of every robot server is accepted.

Run:
    python -m store_agent.test_robot_client
"""

import store_agent.testing_store  # noqa: F401  (first: the tests use the test store in agent/testdata)
from types import SimpleNamespace
from unittest.mock import patch

from robot_server import jetson_server, mock_robot
from store_agent import robot_client


def answer(json_body, ok=True):
    return SimpleNamespace(ok=ok, json=lambda: json_body)


def test_servers_say_ok():
    """Both robot servers follow the contract: /health says "ok"."""
    assert jetson_server.health_check()["status"] == "ok"
    assert mock_robot.health()["status"] == "ok"
    print("PASS: test_servers_say_ok")


def test_real_robot_is_used_when_healthy():
    """The Jetson answer (also the old "healthy") means: use the real robot, not demo mode."""
    for status in ("ok", "healthy"):
        with patch("store_agent.robot_client.requests.get", return_value=answer({"status": status})):
            assert robot_client._robot_reachable(), status
    with patch("store_agent.robot_client.requests.get", return_value=answer({"status": "starting"})):
        assert not robot_client._robot_reachable()
    with patch("store_agent.robot_client.requests.get", return_value=answer({}, ok=False)):
        assert not robot_client._robot_reachable()
    with patch("store_agent.robot_client.requests.get", side_effect=OSError("no route")):
        assert not robot_client._robot_reachable()
    assert not robot_client.is_healthy({"success": False, "message": "cannot reach robot"})
    print("PASS: test_real_robot_is_used_when_healthy")


def run_all_tests():
    print("Running robot client tests...\n")
    test_servers_say_ok()
    test_real_robot_is_used_when_healthy()
    print("\nAll robot client tests passed!")


if __name__ == "__main__":
    run_all_tests()
