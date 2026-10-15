"""
StorePilot backend tests. No robot, no Jetson, no API key needed.

Run (from storepilot/backend):
    .venv/bin/python -m tests.test_backend
"""

import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

from storepilot import config
from storepilot.adapters.base import AdapterError
from storepilot.adapters.jetson import JetsonAdapter
from storepilot.adapters.sim import SIM_STATIONS, SimRobot
from storepilot.db import Store
from storepilot.health import AIStatus
from storepilot.missions import MissionError, MissionManager, mentioned
from storepilot.telemetry import Telemetry

from app.config import DEFAULT_INVENTORY

INVENTORY = lambda: DEFAULT_INVENTORY  # noqa: E731


def fast_sim(**kw):
    kw.setdefault("pick_fail", 0)
    kw.setdefault("scan_fail", 0)
    kw.setdefault("nav_fail", 0)
    return SimRobot(INVENTORY, time_scale=0.02, seed=1, **kw)


def services(adapter=None, db=None):
    """Store + telemetry + mission manager, offline (no Nemotron)."""
    store = Store(db or Path(tempfile.mkdtemp()) / "t.db")
    adapter = adapter or fast_sim()
    add = lambda s, l, t: store.add_event(s, l, t)  # noqa: E731
    tel = Telemetry(adapter, add)
    tel.poll_sec = 0.02
    tel.start()
    ai = AIStatus(add)
    manager = MissionManager(store, adapter, tel, ai)
    for _ in range(100):
        if tel.snapshot()["connected"]:
            break
        time.sleep(0.02)
    return store, adapter, tel, manager


def offline():
    """No Nemotron: keyword parser + offline autopilot."""
    return [patch("storepilot.missions.has_llm", return_value=False),
            patch("app.agent.get_client", side_effect=RuntimeError("no key")),
            patch("app.tools.load_inventory", INVENTORY), patch("app.agent.load_inventory", INVENTORY),
            patch("storepilot.missions.load_inventory", INVENTORY)]


def wait_finished(store, mission_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        m = store.get_mission(mission_id)
        if m["status"] not in ("running", "awaiting_confirmation"):
            return m
        time.sleep(0.05)
    raise AssertionError(f"mission {mission_id} still {m['status']}")


def test_sim_moves_and_stops():
    robot = fast_sim()
    assert robot.navigate("shelf_A")["success"]
    pose = robot.fetch_status()["pose"]
    assert abs(pose["x"] - SIM_STATIONS["shelf_A"]["x"]) < 1e-6
    assert not robot.pick("juice_box", "shelf_C")["success"], "must be at the item's own shelf"
    assert robot.pick("soup_can", "shelf_A")["success"]
    assert robot.scan()["barcode"] == DEFAULT_INVENTORY["soup_can"]["barcode"]
    assert robot.place("basket")["message"] == "placed soup_can in basket"

    slow = SimRobot(INVENTORY, time_scale=1.0, nav_fail=0)
    t = threading.Thread(target=lambda: setattr(slow, "result", slow.navigate("customer")))
    t.start()
    time.sleep(0.3)
    slow.stop()
    t.join(2)
    assert slow.result["success"] is False and "stopped" in slow.result["message"]
    assert slow.fetch_status()["speed"] == 0.0
    assert not slow.navigate("home")["success"], "no motion while stopped"
    slow.resume()
    print("PASS: test_sim_moves_and_stops")


def test_offline_mission_runs_after_confirm_only():
    patches = offline()
    for p in patches:
        p.start()
    try:
        store, robot, tel, m = services()
        draft = m.submit_order("2 tomato soups and 1 juice please, and a pizza")
        assert draft["status"] == "awaiting_confirmation", draft
        assert [(i["item"], i["qty"]) for i in draft["items"]] == [("soup_can", 2), ("juice_box", 1)]
        assert draft["unknown"] == ["pizza"]
        time.sleep(0.3)
        assert robot.fetch_status()["station"] == "home", "nothing moves before confirmation"

        m.confirm(draft["id"])
        done = wait_finished(store, draft["id"])
        assert done["status"] == "completed", done
        assert done["packed"] == 3 and done["planner"].startswith("offline autopilot")
        texts = [e["text"] for e in store.recent_events(200, mission_id=draft["id"])]
        assert any(t.startswith("Mission plan validated") for t in texts)
        assert any(t.startswith("Pick verified") for t in texts)
        assert any(t.startswith("Mission completed: 3/3") for t in texts)
        tel.shutdown()
    finally:
        for p in patches:
            p.stop()
    print("PASS: test_offline_mission_runs_after_confirm_only")


def test_rejected_and_discarded():
    patches = offline()
    for p in patches:
        p.start()
    try:
        store, robot, tel, m = services()
        bad = m.submit_order("2 maggi and a coke")
        assert bad["status"] == "rejected" and bad["problems"], bad
        try:
            m.confirm(bad["id"])
            raise AssertionError("a rejected mission must not start")
        except MissionError:
            pass
        too_many = m.submit_order("11 soups")
        assert too_many["status"] == "rejected"
        ok = m.submit_order("1 chips")
        m.discard(ok["id"])
        assert store.get_mission(ok["id"])["status"] == "discarded"
        tel.shutdown()
    finally:
        for p in patches:
            p.stop()
    print("PASS: test_rejected_and_discarded")


def test_stop_aborts_and_never_resumes_mission():
    patches = offline()
    for p in patches:
        p.start()
    try:
        robot = SimRobot(INVENTORY, time_scale=0.3, pick_fail=0, scan_fail=0, nav_fail=0)
        store, robot, tel, m = services(adapter=robot)
        draft = m.submit_order("3 juice")
        m.confirm(draft["id"])
        time.sleep(0.4)
        assert m.stop()["success"]
        done = wait_finished(store, draft["id"])
        assert done["status"] == "aborted", done
        time.sleep(0.1)
        assert m.robot_state(tel.snapshot())[0] == "paused"
        ok, reason = m.readiness()
        assert not ok and "stopped" in reason.lower()
        m.resume()
        time.sleep(0.1)
        assert store.get_mission(draft["id"])["status"] == "aborted", "resume must not restart the mission"
        assert m.robot_state(tel.snapshot())[0] == "idle"
        tel.shutdown()
    finally:
        for p in patches:
            p.stop()
    print("PASS: test_stop_aborts_and_never_resumes_mission")


def test_restart_marks_running_interrupted():
    db = Path(tempfile.mkdtemp()) / "t.db"
    store = Store(db)
    running = store.create_mission(text="x", mode="mock", status="running", total=1)
    waiting = store.create_mission(text="y", mode="mock", status="awaiting_confirmation", total=1)
    patches = offline()
    for p in patches:
        p.start()
    try:
        store2, _, tel, m = services(db=db)
        assert store2.get_mission(running)["status"] == "interrupted"
        assert store2.get_mission(waiting)["status"] == "expired"
        assert m.current_id is None, "nothing resumes after a restart"
        tel.shutdown()
    finally:
        for p in patches:
            p.stop()
    print("PASS: test_restart_marks_running_interrupted")


def test_live_safety_switches():
    patches = offline() + [patch.object(config, "MODE", "live"),
                           patch.object(config, "ENABLE_MOTION", False),
                           patch.object(config, "JETSON_STOP_CONFIRMED", False)]
    for p in patches:
        p.start()
    try:
        store, robot, tel, m = services()
        draft = m.submit_order("1 soup")
        try:
            m.confirm(draft["id"], area_clear=True)
            raise AssertionError("motion disabled must block the start")
        except MissionError as e:
            assert "motion is disabled" in str(e)
        with patch.object(config, "ENABLE_MOTION", True):
            try:
                m.confirm(draft["id"], area_clear=False)
                raise AssertionError("live start needs the area-clear confirmation")
            except MissionError as e:
                assert "clear" in str(e)
        result = m.stop()
        assert result["success"] is False and "hardware" in result["message"]
        assert robot.fetch_status()["stopped"] is False, "unconfirmed stop endpoint must not be called"
        tel.shutdown()
    finally:
        for p in patches:
            p.stop()
    print("PASS: test_live_safety_switches")


def test_jetson_adapter_http():
    import socket

    import uvicorn
    from robot_server import mock_robot

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(mock_robot.app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)

    jetson = JetsonAdapter(f"http://127.0.0.1:{port}")
    assert jetson.fetch_health()["status"] == "ok"
    assert "pose" in jetson.fetch_status()
    assert set(jetson.fetch_map()["stations"]) >= {"home", "shelf_A", "customer"}
    assert jetson.navigate("kitchen")["success"] is False, "unknown stations never reach the robot"
    server.should_exit = True

    dead = JetsonAdapter("http://127.0.0.1:9")
    try:
        dead.fetch_status()
        raise AssertionError("an unreachable Jetson must raise, never return fake data")
    except AdapterError:
        pass
    assert dead.navigate("home")["success"] is False
    print("PASS: test_jetson_adapter_http")


def test_disconnect_is_reported():
    class Flaky(SimRobot):
        down = False

        def fetch_status(self):
            if self.down:
                raise AdapterError("no route to host")
            return super().fetch_status()

    robot = Flaky(INVENTORY, time_scale=0.02)
    store, _, tel, m = services(adapter=robot)
    with patch.object(config, "DISCONNECTED_AFTER_SEC", 0.2):
        robot.down = True
        time.sleep(0.5)
        snap = tel.snapshot()
        assert not snap["connected"]
        assert m.robot_state(snap)[0] == "offline"
        assert not m.readiness()[0]
        robot.down = False
        time.sleep(0.2)
        assert tel.snapshot()["connected"]
    texts = [e["text"] for e in store.recent_events(20)]
    assert any("disconnected" in t for t in texts) and any("restored" in t for t in texts), texts
    tel.shutdown()
    print("PASS: test_disconnect_is_reported")


def test_mentioned():
    assert mentioned("soup_can", "2 tomato soups please")
    assert mentioned("chips_pack", "one chips")
    assert not mentioned("chips_pack", "2 packets of Maggi")
    assert not mentioned("juice_box", "a coke bottle")
    print("PASS: test_mentioned")


def run_all():
    test_mentioned()
    test_sim_moves_and_stops()
    test_offline_mission_runs_after_confirm_only()
    test_rejected_and_discarded()
    test_stop_aborts_and_never_resumes_mission()
    test_restart_marks_running_interrupted()
    test_live_safety_switches()
    test_jetson_adapter_http()
    test_disconnect_is_reported()
    print("\nAll StorePilot backend tests passed!")


if __name__ == "__main__":
    run_all()
