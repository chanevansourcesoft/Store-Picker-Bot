"""
StorePilot AI dashboard server (runs on the Raspberry Pi).

    uvicorn storepilot.main:app --host 127.0.0.1 --port 8080   (from storepilot/backend)

REST:
    GET  /api/state                       full dashboard snapshot (same as the WebSocket)
    GET  /api/catalog                     products the robot can pick
    POST /api/orders {text}               parse + validate -> mission waiting for confirmation
    POST /api/missions/{id}/confirm {area_clear}
    POST /api/missions/{id}/discard
    GET  /api/missions?limit=50           mission history
    GET  /api/events?limit=100&mission_id=
    POST /api/robot/stop                  stop mission + robot (says what the robot answered)
    POST /api/robot/resume                clear the robot stop state (never restarts a mission)
    GET  /api/health                      liveness for systemd / kiosk launcher
WebSocket:
    /ws  pushes {"type": "state", "data": snapshot} 4 times a second
"""

import asyncio
import contextlib
import json
import logging
import time

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .db import Store
from .health import AIStatus, pi_health
from .drive import DriveController
from .missions import MissionError, MissionManager
from .stations import StationStore
from .telemetry import Telemetry

from app.config import load_inventory  # noqa: E402  (shared inventory, webapp/app/inventory.json)

log = logging.getLogger("storepilot")
PUSH_SEC = 0.25


def make_adapter(stations):
    if config.MODE == "mock":
        from .adapters.sim import SimRobot
        return SimRobot(load_inventory)
    if config.JETSON_LINK == "rosbridge":
        from .adapters.rosbridge import RosbridgeRobot
        if not config.ROSBRIDGE_URL:
            raise SystemExit("JETSON_LINK=rosbridge needs ROSBRIDGE_URL, e.g. ws://192.168.20.185:9090")
        return RosbridgeRobot(config.ROSBRIDGE_URL, stations, config.JETSON_MAPS_DIR, config.JETSON_MAPS,
                              base_frame=config.ROS_BASE_FRAME, battery_topic=config.ROS_BATTERY_TOPIC,
                              map_origins=config.JETSON_MAP_ORIGINS)
    from .adapters.jetson import JetsonAdapter
    if not config.JETSON_URL:
        from .adapters.base import AdapterError, RobotAdapter

        class NotConfigured(RobotAdapter):
            source = "jetson"

            def _no(self, *a, **k):
                raise AdapterError("JETSON_URL is not set")
            fetch_health = fetch_status = fetch_map = _no

            def _fail(self, *a, **k):
                return {"success": False, "message": "JETSON_URL is not set"}
            navigate = pick = scan = place = stop = resume = _fail
        return NotConfigured()
    return JetsonAdapter(config.JETSON_URL)


class Services:
    def __init__(self):
        self.store = Store(config.DB_PATH)
        self.stations = StationStore(config.STATIONS_PATH)
        self.adapter = make_adapter(self.stations)
        self.rosbridge = getattr(self.adapter, "link_kind", "") == "rosbridge"
        self.drive = DriveController(
            self.adapter, self.stations,
            lambda source, level, text: self.store.add_event(source, level, text, simulated=False),
            config.STATIONS_PATH.with_name("drive_settings.json")) if self.rosbridge else None
        simulated = self.adapter.source == "simulated"
        add = lambda source, level, text: self.store.add_event(source, level, text, simulated=simulated)  # noqa: E731
        self.ai = AIStatus(add)
        self.telemetry = Telemetry(self.adapter, add)
        self.missions = MissionManager(self.store, self.adapter, self.telemetry, self.ai)
        self.store.add_event("system", "info", f"Dashboard started in {config.MODE.upper()} mode", simulated=simulated)

    def start(self):
        self.telemetry.start()
        self.ai.start()


services: Services | None = None


# ---------- snapshot ----------

def robot_url():
    return config.ROSBRIDGE_URL if config.JETSON_LINK == "rosbridge" else config.JETSON_URL

def alerts(tel, robot, ai):
    out = []
    status = tel.get("status") or {}
    health = tel.get("health") or {}
    if config.MODE == "live" and not robot_url():
        out.append(("error", "No robot address set (JETSON_URL or ROSBRIDGE_URL): the dashboard cannot reach the robot."))
    elif not tel["connected"]:
        out.append(("error", "Robot disconnected: telemetry unavailable. Mission history is kept on this Pi."))
    elif tel["age"] is not None and tel["age"] > config.STALE_AFTER_SEC:
        out.append(("warning", f"Robot telemetry is stale ({tel['age']:.0f}s old)."))
    if config.MODE == "live" and tel["connected"] and health and not health.get("ros_available", True):
        out.append(("warning", "Jetson server runs in STUB mode (no ROS 2): pose, speed and battery are not real."))
    if status.get("stopped"):
        out.append(("warning", "Robot is stopped. Missions cannot start until it is resumed."))
    if status.get("obstacle"):
        out.append(("warning", "Obstacle reported by the robot."))
    battery = robot.get("battery")
    if isinstance(battery, (int, float)) and battery < 20:
        out.append(("error", f"Battery low: {battery:.0f}%."))
    if ai["state"] == "error":
        out.append(("warning", f"AI backend problem: {ai['detail']}"))
    if config.MODE == "live" and not config.ENABLE_MOTION:
        out.append(("info", "Physical motion is disabled. Missions can be planned but not started."))
    rosbridge = services is not None and services.rosbridge
    if config.MODE == "live" and not config.JETSON_STOP_CONFIRMED and not rosbridge:
        out.append(("warning", "Dashboard STOP is not connected to the Jetson. Use the hardware E-stop."))
    if rosbridge and not config.JETSON_STOP_CONFIRMED:
        out.append(("warning", "STOP cancels the goal and pauses Nav2, but is not physically verified yet. "
                               "Keep the hardware E-stop at hand."))
    if rosbridge and tel["connected"]:
        if status.get("nav2_active") is None:
            out.append(("warning", "Nav2 is not running on the Jetson: start `ros2 launch mecanum_nav navigation.launch.py`."))
        elif status.get("nav2_active") is False:
            out.append(("warning", "Nav2 is paused on the Jetson: press Start Nav2 on the Map page."))
        elif status.get("localized") is False:
            out.append(("warning", "Robot is not localized: set its position on the Map page (like 2D Pose Estimate)."))
        elif status.get("loc_quality") is not None and status["loc_quality"] < config.MIN_LOC_QUALITY:
            out.append(("warning", f"Localization looks poor: only {status['loc_quality']:.0%} of LiDAR points are on map "
                                   "walls. Use Auto-align or Set position; driving is blocked until it improves."))
        if status.get("battery_v") is not None and status["battery_v"] < 11.1:
            out.append(("warning", f"Battery {status['battery_v']:.2f} V: charge before driving (3S pack)."))
    if config.MODE == "live" and not config.MANIPULATION_VERIFIED:
        out.append(("info", "Pick, scan and place are unavailable: the Jetson has no verified implementation yet."))
    hidden = [f for f in config.TELEMETRY_FIELDS if f not in config.VERIFIED_TELEMETRY]
    if config.MODE == "live" and hidden and not rosbridge:
        out.append(("info", "Not verified, so not shown: " + ", ".join(hidden) +
                    " (STOREPILOT_VERIFIED_TELEMETRY)."))
    return [{"level": level, "text": text} for level, text in out]


def build_snapshot():
    s = services
    tel = s.telemetry.snapshot()
    status = tel.get("status") or {}
    fresh = tel["connected"] and tel["age"] is not None and tel["age"] <= config.STALE_AFTER_SEC
    health = tel.get("health") or {}
    stub = config.MODE == "live" and health and not health.get("ros_available", True)

    live = config.MODE == "live"
    # jetson_server.py (http) reports default battery / constant speed, so those wait for verification.
    # Over rosbridge, pose and speed come straight from ROS TF and /odom.
    unverified = [f for f in config.TELEMETRY_FIELDS
                  if live and not s.rosbridge and f not in config.VERIFIED_TELEMETRY]
    if live and not config.MANIPULATION_VERIFIED:
        unverified.append("holding")

    def value(key):
        """Telemetry value, or None when it is not known or not verified (never invented)."""
        if not tel["connected"] or key not in status or status[key] is None:
            return None
        if stub and key in config.TELEMETRY_FIELDS:
            return None
        if key in unverified:
            return None
        return status[key]

    state, detail = s.missions.robot_state(tel)
    robot = {
        "state": state,
        "detail": detail,
        "pose": value("pose"),
        "station": value("station"),
        "nav_state": value("nav_state"),
        "battery": value("battery"),
        "speed": value("speed"),
        "holding": value("holding"),
        "obstacle": value("obstacle"),
        # older jetson_server.py has no "stopped" field: unknown, not "no"
        "stopped": bool(status["stopped"]) if tel["connected"] and "stopped" in status else None,
        "busy": value("busy"),
        "last_error": value("last_error"),
        "target": s.missions.target(),
        "stop_pending": s.missions.stop_pending,
        "unverified": unverified,
        "battery_v": status.get("battery_v") if tel["connected"] else None,
        "loc_quality": status.get("loc_quality") if tel["connected"] else None,
        "localized": status.get("localized") if tel["connected"] else None,
        "needs_initial_pose": status.get("needs_initial_pose") if tel["connected"] else None,
        "nav2_active": status.get("nav2_active") if tel["connected"] else None,
        "goal": status.get("goal") if tel["connected"] else None,
        "map_name": status.get("map_name") if tel["connected"] else None,
    }

    mission = None
    mission_id = s.missions.current_id
    if mission_id is None:
        latest = s.store.list_missions(limit=1)
        mission = latest[0] if latest else None
    else:
        mission = s.store.get_mission(mission_id)
    if mission:
        mission = {k: v for k, v in mission.items() if k != "step_log"}
        mission["eta"] = s.missions.eta(mission) if mission["status"] in ("awaiting_confirmation", "running") else None

    ready, not_ready_reason = s.missions.readiness()
    ai = s.ai.snapshot()
    map_data = tel.get("map") or {}
    ros = None
    if s.rosbridge:
        map_data = {"stations": s.stations.get(s.adapter.map_name)} if tel["connected"] else {}
        ros = s.adapter.live_layers() if tel["connected"] else None
    return {
        "mode": config.MODE,
        "server_time": time.time(),
        "safety": {
            "motion_enabled": config.MODE == "mock" or config.ENABLE_MOTION,
            # rosbridge: STOP really cancels the goal and pauses Nav2; "verified" = tested on the robot
            "stop_connected": config.MODE == "mock" or config.JETSON_STOP_CONFIRMED or s.rosbridge,
            "stop_verified": config.MODE == "mock" or config.JETSON_STOP_CONFIRMED,
            "manipulation": "simulated" if config.MODE == "mock" else
                            ("verified" if config.MANIPULATION_VERIFIED else "unavailable"),
            "needs_area_confirmation": config.MODE == "live",
            "can_start": ready,
            "cannot_start_reason": not_ready_reason,
        },
        "connections": {
            "dashboard": {"state": "ok", **pi_health()},
            "jetson": {
                "state": "simulated" if s.adapter.source == "simulated" else
                         ("not_configured" if not robot_url() else ("connected" if tel["connected"] else "disconnected")),
                "url": robot_url() or None,
                "link": getattr(s.adapter, "link_kind", "http") if s.adapter.source != "simulated" else "simulator",
                "latency_ms": getattr(s.adapter, "last_latency_ms", None),
                "ros_available": health.get("ros_available"),
                "ros_connected": health.get("ros_connected"),
                "detail": tel.get("last_error"),
            },
            "telemetry": {
                "state": ("simulated" if s.adapter.source == "simulated" and fresh else
                          "stub" if stub and fresh else
                          "live" if fresh else "stale" if tel["connected"] else "unavailable"),
                "age_sec": None if tel["age"] is None else round(tel["age"], 1),
            },
            "ai": ai,
        },
        "robot": robot,
        "mission": mission,
        "map": {
            "stations": map_data.get("stations"),
            "source": s.adapter.source if map_data else None,
            "labels": config.STATION_LABELS,
        },
        "ros": ros,
        "route": s.drive.snapshot() if s.drive else None,
        "drive": {
            "available": s.rosbridge,
            "motion_enabled": config.ENABLE_MOTION,
            "stop_verified": config.JETSON_STOP_CONFIRMED,
        },
        "alerts": alerts(tel, robot, ai),
        "events": s.store.recent_events(limit=60),
    }


# ---------- app ----------

@contextlib.asynccontextmanager
async def lifespan(_app):
    global services
    services = Services()
    services.start()
    yield
    services.telemetry.shutdown()


app = FastAPI(title="StorePilot AI", lifespan=lifespan)


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    """Any bug becomes a readable message for the operator (and a traceback in the log)."""
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse({"detail": f"Dashboard error on {request.url.path}: {exc.__class__.__name__}: {exc}"},
                        status_code=500)


class OrderIn(BaseModel):
    text: str


class ConfirmIn(BaseModel):
    area_clear: bool = False


def _error(e):
    raise HTTPException(status_code=409, detail=str(e))


@app.get("/api/health")
def api_health():
    return {"status": "ok", "mode": config.MODE}


@app.get("/api/state")
def api_state():
    return build_snapshot()


@app.get("/api/catalog")
def api_catalog():
    return services.missions.catalog()


@app.post("/api/orders")
def api_order(body: OrderIn):
    try:
        return services.missions.submit_order(body.text)
    except MissionError as e:
        _error(e)


@app.post("/api/missions/{mission_id}/confirm")
def api_confirm(mission_id: int, body: ConfirmIn):
    try:
        return services.missions.confirm(mission_id, area_clear=body.area_clear)
    except MissionError as e:
        _error(e)


@app.post("/api/missions/{mission_id}/discard")
def api_discard(mission_id: int):
    try:
        services.missions.discard(mission_id)
        return {"ok": True}
    except MissionError as e:
        _error(e)


@app.get("/api/missions")
def api_missions(limit: int = Query(50, ge=1, le=500)):
    return [{k: v for k, v in m.items() if k != "step_log"} for m in services.store.list_missions(limit)]


@app.get("/api/events")
def api_events(limit: int = Query(100, ge=1, le=1000), mission_id: int | None = None):
    return services.store.recent_events(limit=limit, mission_id=mission_id)


@app.post("/api/robot/stop")
def api_stop():
    if services.drive:
        services.drive.abort.set()     # the route must not send another goal after STOP
    return services.missions.stop()


@app.post("/api/robot/resume")
def api_resume():
    try:
        return services.missions.resume()
    except MissionError as e:
        _error(e)


# ---------- live driving over rosbridge (Nav2 goals only, never wheel speeds) ----------

class GoalIn(BaseModel):
    x: float
    y: float
    theta: float = 0.0
    area_clear: bool = False


class PoseIn(BaseModel):
    x: float
    y: float
    theta: float = 0.0
    refine: bool = True


class ConfirmOnly(BaseModel):
    area_clear: bool = False


class NameIn(BaseModel):
    name: str


class RouteIn(BaseModel):
    waypoints: list[dict]
    area_clear: bool = False


class DriveSettingsIn(BaseModel):
    auto_align: bool | None = None
    guard: bool | None = None


def _ros():
    """The rosbridge adapter, if connected (409 otherwise)."""
    if not services.rosbridge:
        raise HTTPException(409, "Driving needs live mode with JETSON_LINK=rosbridge.")
    if not services.telemetry.snapshot()["connected"]:
        raise HTTPException(409, "Robot disconnected.")
    return services.adapter


def _check_motion(area_clear):
    if not config.ENABLE_MOTION:
        raise HTTPException(409, "Physical motion is disabled (STOREPILOT_ENABLE_MOTION=0).")
    if services.missions.current_id is not None:
        raise HTTPException(409, "A mission is running.")
    if services.drive and services.drive.busy():
        raise HTTPException(409, "A route is running: cancel it first.")
    if services.rosbridge:
        robot = services.adapter
        if robot.stopped:
            raise HTTPException(409, "Robot is stopped: press Resume first.")
        if robot.nav_active is not True:
            raise HTTPException(409, "Nav2 is not active: press Start Nav2 first.")
        if not robot.localized():
            raise HTTPException(409, "Robot is not localized: use Set position or Auto-align first.")
    if not area_clear:
        raise HTTPException(409, "Confirm that the robot's path is clear first.")
    q = services.adapter.localization_quality() if services.rosbridge else None
    if q is not None and q < config.MIN_LOC_QUALITY:
        raise HTTPException(409, f"Localization looks poor ({q:.0%} of LiDAR points on walls): "
                                 "use Auto-align or Set position first.")


def _event(source, level, text):
    services.store.add_event(source, level, text, simulated=False)


def _result(result, ok_text, fail_text):
    if result.get("success"):
        _event("operator", "info", ok_text)
        return result
    _event("robot", "error", f"{fail_text}: {result.get('message')}")
    raise HTTPException(409, result.get("message") or fail_text)


def _start_route(waypoints, text):
    _ros()
    try:
        route = services.drive.start(waypoints)
    except ValueError as e:
        raise HTTPException(409, str(e))
    _event("operator", "info", text + " — operator confirmed path clear")
    return route


@app.post("/api/drive/goal")
def api_drive_goal(body: GoalIn):
    _ros()
    _check_motion(body.area_clear)
    return _start_route([{"x": body.x, "y": body.y, "theta": body.theta, "label": "goal"}],
                        f"Drive goal ({body.x:.2f}, {body.y:.2f})")


@app.post("/api/drive/route")
def api_drive_route(body: RouteIn):
    _ros()
    _check_motion(body.area_clear)
    return _start_route(body.waypoints, f"Route with {len(body.waypoints)} waypoint(s) started")


@app.post("/api/drive/route/cancel")
def api_cancel_route():
    _ros()
    if not services.drive.cancel():
        raise HTTPException(409, "No route is running.")
    _event("operator", "warning", "Route cancelled by operator (goal canceled, Nav2 stays active)")
    return {"ok": True}


@app.post("/api/drive/settings")
def api_drive_settings(body: DriveSettingsIn):
    _ros()
    settings = services.drive.set_settings(auto_align=body.auto_align, guard=body.guard)
    _event("operator", "info", f"Drive options: auto-align at stops {'on' if settings['auto_align'] else 'off'}, "
                               f"localization guard {'on' if settings['guard'] else 'off'}")
    return settings


@app.post("/api/drive/station/{name}")
def api_drive_station(name: str, body: ConfirmOnly):
    _ros()
    _check_motion(body.area_clear)
    return _start_route([{"station": name}], f"Drive to {config.station_label(name)}")


@app.post("/api/drive/initial_pose")
def api_initial_pose(body: PoseIn):
    robot = _ros()
    if services.drive and services.drive.busy():
        raise HTTPException(409, "A route is running: cancel it first.")
    result = robot.set_initial_pose(body.x, body.y, body.theta, refine=body.refine)
    return _result(result, f"Robot position set — {result.get('message', '')}", "Setting the position failed")


@app.post("/api/drive/align")
def api_auto_align():
    robot = _ros()
    result = robot.auto_align()
    return _result(result, f"Auto-align — {result.get('message', '')}", "Auto-align failed")


@app.post("/api/nav2/start")
def api_nav2_start():
    robot = _ros()
    return _result(robot.start_nav2(), "Nav2 started (resumed) by operator", "Nav2 start failed")


@app.post("/api/maps/load")
def api_load_map(body: NameIn):
    robot = _ros()
    if (robot.goal_status or "") in ("accepted", "executing") or (services.drive and services.drive.busy()):
        raise HTTPException(409, "The robot is driving: stop it before switching maps.")
    return _result(robot.load_map(body.name), f"Map {body.name} loaded on the Jetson — set the robot position",
                   f"Loading map {body.name} failed")


@app.post("/api/stations")
def api_save_station(body: NameIn):
    robot = _ros()
    pose = robot.pose() if robot.localized() else None
    if not pose:
        raise HTTPException(409, "Robot position unknown: localize the robot first.")
    try:
        entry = services.stations.save(robot.map_name, body.name, pose)
    except ValueError as e:
        raise HTTPException(409, str(e))
    _event("operator", "success",
           f"{config.station_label(body.name)} saved on map {robot.map_name} at ({entry['x']:.2f}, {entry['y']:.2f})")
    return entry


@app.delete("/api/stations/{name}")
def api_delete_station(name: str):
    robot = _ros()
    services.stations.delete(robot.map_name, name)
    _event("operator", "info", f"{config.station_label(name)} removed from map {robot.map_name}")
    return {"ok": True}


@app.get("/api/rosmap.png")
def api_rosmap():
    png = getattr(services.adapter, "map_png", None)
    if not png:
        raise HTTPException(404, "No map received from the robot yet.")
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-cache"})


@app.websocket("/ws")
async def ws_state(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            snapshot = await asyncio.to_thread(build_snapshot)
            await ws.send_text(json.dumps({"type": "state", "data": snapshot}, default=str))
            await asyncio.sleep(PUSH_SEC)
    except (WebSocketDisconnect, RuntimeError):
        pass


# ---------- built frontend (npm run build -> frontend/dist) ----------

if (config.STATIC_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=config.STATIC_DIR / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith(("api/", "ws")):
        raise HTTPException(status_code=404)
    file = (config.STATIC_DIR / path).resolve()
    if path and file.is_file() and config.STATIC_DIR.resolve() in file.parents:
        return FileResponse(file)
    index = config.STATIC_DIR / "index.html"
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"error": "Frontend not built. Run: cd storepilot/frontend && npm run build"},
                        status_code=503)
