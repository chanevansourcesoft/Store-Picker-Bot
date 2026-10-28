"""
StorePilot backend tests. No robot, no Jetson, no API key needed.

Run (from storepilot/backend):
    .venv/bin/python -m tests.test_backend
"""

import store_agent.testing_store  # noqa: F401  (first: the tests use the test store in agent/testdata)
import math
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

# Tests never depend on this Pi's storepilot/.env (which may be in live mode):
# load_dotenv() does not override variables that are already set.
os.environ.update(STOREPILOT_MODE="mock", JETSON_LINK="http", STOREPILOT_ENABLE_MOTION="0",
                  JETSON_STOP_CONFIRMED="0", STOREPILOT_MANIPULATION_VERIFIED="0", STOREPILOT_VERIFIED_TELEMETRY="")

from storepilot import config
from storepilot.adapters.base import AdapterError
from storepilot.adapters.jetson import JetsonAdapter
from storepilot.adapters.sim import SIM_STATIONS, SimRobot
from storepilot.db import Store
from storepilot.health import AIStatus
from storepilot.missions import MissionError, MissionManager, mentioned
from storepilot.telemetry import Telemetry

from store_agent import inventory as stock_store
from store_agent.inventory import DEFAULT_INVENTORY

# Missions reserve stock: use a temporary stock file, never the real one
stock_store.STOCK_FILE = Path(tempfile.mkdtemp()) / "stock.json"

INVENTORY = lambda: DEFAULT_INVENTORY  # noqa: E731


def fast_sim(**kw):
    kw.setdefault("pick_fail", 0)
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
            patch("store_agent.agent.get_client", side_effect=RuntimeError("no key")),
            patch("store_agent.tools.load_inventory", INVENTORY), patch("store_agent.agent.load_inventory", INVENTORY),
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
    assert not robot.pick("frooti", "shelf_A")["success"], "must be at the item's own shelf"
    result = robot.pick("chips_packet", "shelf_A")   # one GR00T run: pick AND place in the basket
    assert result["success"] and result["target"] == "basket" and result["item"] == "chips_packet", result
    assert result["instruction"] == "pick up the chips packet and place it in the basket", result
    assert robot.fetch_status()["holding"] is None
    assert not hasattr(robot, "scan")
    assert robot.return_item("chips_packet")["success"]

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
        start = stock_store.get_stock()
        draft = m.submit_order("2 chips and 1 frooti please, and a pizza")
        assert draft["status"] == "awaiting_confirmation", draft
        assert [(i["item"], i["qty"]) for i in draft["items"]] == [("chips_packet", 2), ("frooti", 1)]
        assert draft["unknown"] == ["pizza"]
        time.sleep(0.3)
        assert robot.fetch_status()["station"] == "home", "nothing moves before confirmation"

        assert stock_store.get_stock() == start, "nothing is reserved before confirmation"
        m.confirm(draft["id"])
        assert stock_store.get_stock()["chips_packet"] == start["chips_packet"] - 2, "reserved at confirm"
        done = wait_finished(store, draft["id"])
        assert done["status"] == "completed", done
        assert done["packed"] == 3 and done["planner"].startswith("offline autopilot")
        texts = [e["text"] for e in store.recent_events(200, mission_id=draft["id"])]
        assert any(t.startswith("Mission plan validated") for t in texts)
        assert sum(" placed in basket (" in t for t in texts) == 3, texts
        assert any("GR00T instruction “pick up the Frooti" in t for t in texts), texts
        assert any(t.startswith("Mission completed: 3/3") for t in texts)
        stock = stock_store.get_stock()
        assert (stock["chips_packet"], stock["frooti"]) == (start["chips_packet"] - 2, start["frooti"] - 1), stock
        assert store.get_mission(draft["id"])["stock_reserved"] == 0
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
        too_many = m.submit_order("11 chips")
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
        robot = SimRobot(INVENTORY, time_scale=0.3, pick_fail=0, nav_fail=0)
        store, robot, tel, m = services(adapter=robot)
        start = stock_store.get_stock()["frooti"]
        draft = m.submit_order("3 frooti")
        m.confirm(draft["id"])
        time.sleep(0.4)
        assert m.stop()["success"]
        done = wait_finished(store, draft["id"])
        assert done["status"] == "aborted", done
        assert stock_store.get_stock()["frooti"] == start - done["packed"], "unpacked units go back to stock"
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
    running = store.create_mission(text="x", mode="mock", status="running", total=2, stock_reserved=1,
                                   items=[{"item": "mentos", "qty": 2}])
    assert stock_store.reserve([{"item": "mentos", "qty": 2}])[0]
    before = stock_store.get_stock()["mentos"]
    waiting = store.create_mission(text="y", mode="mock", status="awaiting_confirmation", total=1)
    patches = offline()
    for p in patches:
        p.start()
    try:
        store2, _, tel, m = services(db=db)
        assert store2.get_mission(running)["status"] == "interrupted"
        assert stock_store.get_stock()["mentos"] == before + 2, "an interrupted mission gives its stock back"
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
        draft = m.submit_order("1 chips")
        try:
            m.confirm(draft["id"], area_clear=True)
            raise AssertionError("motion disabled must block the start")
        except MissionError as e:
            assert "motion is disabled" in str(e)
        with patch.object(config, "ENABLE_MOTION", True):
            try:
                m.confirm(draft["id"], area_clear=True)
                raise AssertionError("unverified pick / scan / place must block a live start")
            except MissionError as e:
                assert "not verified" in str(e)
            with patch.object(config, "MANIPULATION_VERIFIED", True):
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


def test_missions_wait_for_the_arm():
    """/health "arm_ready": false (GR00T policy worker not loaded) blocks a start; a gripper that
    still holds something ("needs_operator") too, until Resume."""
    patches = offline()
    for p in patches:
        p.start()
    try:
        store, robot, tel, m = services()
        with tel.lock:
            tel.health = {"status": "ok", "arm_ready": False, "arm": {"state": "loading"}}
        ok, reason = m.readiness()
        assert not ok and "arm is not ready" in reason, reason
        with tel.lock:
            tel.health = {"status": "ok", "arm_ready": True}
        assert m.readiness() == (True, "")
        held = tel.snapshot()
        held["status"] = dict(held["status"], needs_operator="the gripper still holds something")
        with patch.object(tel, "snapshot", return_value=held):
            ok, reason = m.readiness()
        assert not ok and "check the arm" in reason, reason
        tel.shutdown()
    finally:
        for p in patches:
            p.stop()
    print("PASS: test_missions_wait_for_the_arm")


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


def test_rosbridge_drive():
    """Live driving over rosbridge against the fake Nav2: paused + unlocalized at start like the real robot."""
    import asyncio
    import socket

    from storepilot.adapters.rosbridge import RosbridgeRobot
    from storepilot.stations import StationStore
    from tests import fake_nav2_rosbridge as fake

    fake.ROBOT = fake.Robot()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    threading.Thread(target=lambda: asyncio.run(fake.main("127.0.0.1", port)), daemon=True).start()
    time.sleep(0.5)

    stations = StationStore(Path(tempfile.mkdtemp()) / "stations.json")
    robot = RosbridgeRobot(f"ws://127.0.0.1:{port}", stations, fake.MAPS_DIR, ["conf3", "office_map"],
                           map_origins={"conf3": (-4.4, -2.16), "office_map": (-1.36, -12.2)})

    def wait(cond, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.05)
        raise AssertionError("timed out")

    wait(lambda: robot.link.connected and robot.nav_active is False and robot.map_png)
    assert robot.map_name == "conf3", robot.map_name
    status = robot.fetch_status()
    assert status["pose"] is None and not status["localized"], "unlocalized robot must have no pose"
    assert status["battery_v"] == 11.286
    assert not robot.send_goal(1, 0, 0)["success"], "goals refused while Nav2 is paused"

    assert robot.start_nav2()["success"]
    wait(lambda: robot.nav_active is True)

    # Localization assist: the robot really is at (-2.0, -1.0, 0); the operator's tap is 30 cm / 7 deg off.
    fake.ROBOT.truth = (-2.0, -1.0, 0.0)
    time.sleep(0.6)   # let a scan taken at the new spot arrive
    wait(lambda: robot._scan_in_base() is not None and robot._field() is not None)
    result = robot.set_initial_pose(-1.75, -1.15, 0.12)
    p = result["pose"]
    assert result["success"] and "auto-aligned" in result["message"], result
    assert abs(p["x"] + 2.0) < 0.05 and abs(p["y"] + 1.0) < 0.05 and abs(p["theta"]) < math.radians(3), p
    wait(lambda: fake.ROBOT.nomotion_calls >= 3)
    wait(lambda: (robot.localization_quality() or 0) > 0.8)
    assert robot.auto_align()["success"]
    wait(lambda: robot.fetch_status()["pose"] is not None)
    stations.save(robot.map_name, "home", robot.pose())

    assert robot.send_goal(-0.5, 1.5, 1.57)["success"]
    wait(lambda: robot.fetch_status()["nav_state"] == "navigating")
    wait(lambda: robot.scan_points() and robot.live_layers()["plan"])
    assert robot.stop()["message"] == "goal canceled, Nav2 paused"
    wait(lambda: robot.nav_active is False)
    assert robot.fetch_status()["stopped"] and not robot.send_goal(0, 0, 0)["success"]
    assert robot.resume()["success"] and robot.goal_status == "canceled", "resume must not re-send the goal"

    assert robot.navigate("home")["success"], "blocking drive to a saved station"
    assert robot.fetch_status()["station"] == "home"

    # Multi-waypoint route with auto-align at each stop, then STOP aborts a running route.
    from storepilot.drive import DriveController
    events = []
    drive = DriveController(robot, stations, lambda src, lvl, txt: events.append(txt),
                            Path(tempfile.mkdtemp()) / "drive.json")
    drive.start([{"x": -1.4, "y": -0.6, "theta": 0.8}, {"station": "home"}])
    wait(lambda: not drive.busy(), timeout=40)
    route = drive.snapshot()["route"]
    assert route["state"] == "done", route
    assert sum("Arrived at" in t for t in events) == 2, events
    assert sum(t.startswith("Auto-align (at ") for t in events) == 2, events
    try:
        drive.start([{"station": "shelf_A"}])
        raise AssertionError("unsaved station must be refused")
    except ValueError:
        pass
    drive.start([{"x": 0.5, "y": 1.5, "theta": 0.0}])
    wait(lambda: robot.goal_status == "executing")
    drive.abort.set()
    assert robot.stop()["success"]
    wait(lambda: not drive.busy())
    assert drive.snapshot()["route"]["state"] == "canceled"
    assert robot.resume()["success"]

    # Goal checks: free floor ok, wall / outside / unknown bad
    assert robot.check_point(-2.0, -1.0)["level"] == "ok", robot.check_point(-2.0, -1.0)
    assert robot.check_point(-0.7, 0.4)["level"] == "bad"          # inside the box obstacle
    assert robot.check_point(9.0, 9.0)["level"] == "bad"           # outside the map
    assert robot.check_point(-4.3, 0.0)["level"] == "bad"          # in the outer wall
    try:
        drive.start([{"x": -0.7, "y": 0.4, "theta": 0.0}])
        raise AssertionError("a goal inside an obstacle must be refused")
    except ValueError as e:
        assert "Inside a wall" in str(e) or "does not fit" in str(e), e

    # Costmap overlay and footprint arrive in the map frame
    wait(lambda: robot.costmap_layer()["costmap"] and robot.costmap_layer()["footprint"])
    fp = robot.costmap_layer()["footprint"]
    p = robot.pose()
    assert abs(sum(x for x, _ in fp) / 4 - p["x"]) < 0.05 and abs(sum(y for _, y in fp) / 4 - p["y"]) < 0.05, (fp, p)

    # Link watchdog: the Pi drives, its heartbeat stops -> the Jetson side cancels the goal
    wait(lambda: robot.watchdog is not None)
    drive.start([{"x": 0.3, "y": 1.5, "theta": 0.0}])
    wait(lambda: robot.goal_status == "executing" and fake.ROBOT.armed)
    robot.heartbeat_paused = True
    wait(lambda: fake.ROBOT.watchdog_cancels == 1, timeout=8)
    robot.heartbeat_paused = False
    wait(lambda: not drive.busy(), timeout=10)
    assert drive.snapshot()["route"]["state"] == "failed", drive.snapshot()["route"]

    # Stations export / import
    exported = stations.export()
    assert exported["conf3"]["home"]
    assert stations.import_data({"office_map": {"shelf_A": {"x": 1, "y": 2, "theta": 0}}}) == 1
    try:
        stations.import_data({"conf3": {"kitchen": {"x": 0, "y": 0, "theta": 0}}})
        raise AssertionError("unknown station names must be refused")
    except ValueError:
        pass

    # After a navigation (re)start the last good pose on the same map is restored (auto-aligned)
    assert robot.scan_age() is not None and robot.scan_age() < 2
    robot.last_good = (robot.map_name, robot.pose(), time.time())
    assert "position set" in robot.restore_pose_after_start(robot.map_name, timeout=10)
    assert "no recent position" in robot.restore_pose_after_start("other_map", timeout=1)

    assert not robot.load_map("../../etc/passwd")["success"]
    assert robot.load_map("office_map")["success"]
    wait(lambda: robot.live_layers()["map"]["width"] == 156)
    assert not robot.fetch_status()["localized"], "a new map needs a new initial pose"
    assert fake.ROBOT.cmd_vel_published == 0, "StorePilot must never publish /cmd_vel"
    robot.link.shutdown()
    print("PASS: test_rosbridge_drive")


def test_mentioned():
    assert mentioned("hide_and_seek", "2 hide and seek please")
    assert mentioned("chips_packet", "one chips")
    assert not mentioned("chips_packet", "2 packets of Maggi")
    assert not mentioned("hide_and_seek", "2 chips and 1 frooti"), "'and' alone does not name Hide and Seek"
    assert not mentioned("frooti", "a coke bottle")
    print("PASS: test_mentioned")


def run_all():
    test_mentioned()
    test_sim_moves_and_stops()
    test_offline_mission_runs_after_confirm_only()
    test_rejected_and_discarded()
    test_stop_aborts_and_never_resumes_mission()
    test_restart_marks_running_interrupted()
    test_live_safety_switches()
    test_missions_wait_for_the_arm()
    test_jetson_adapter_http()
    test_disconnect_is_reported()
    test_rosbridge_drive()
    print("\nAll StorePilot backend tests passed!")


if __name__ == "__main__":
    run_all()
