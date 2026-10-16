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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .db import Store
from .health import AIStatus, pi_health
from .missions import MissionError, MissionManager
from .telemetry import Telemetry

from app.config import load_inventory  # noqa: E402  (shared inventory, webapp/app/inventory.json)

log = logging.getLogger("storepilot")
PUSH_SEC = 0.25


def make_adapter():
    if config.MODE == "mock":
        from .adapters.sim import SimRobot
        return SimRobot(load_inventory)
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
        self.adapter = make_adapter()
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

def alerts(tel, robot, ai):
    out = []
    status = tel.get("status") or {}
    health = tel.get("health") or {}
    if config.MODE == "live" and not config.JETSON_URL:
        out.append(("error", "JETSON_URL is not set: the dashboard cannot reach the robot."))
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
    if config.MODE == "live" and not config.JETSON_STOP_CONFIRMED:
        out.append(("warning", "Dashboard STOP is not connected to the Jetson. Use the hardware E-stop."))
    if config.MODE == "live" and not config.MANIPULATION_VERIFIED:
        out.append(("info", "Pick, scan and place are unavailable: the Jetson has no verified implementation yet."))
    hidden = [f for f in config.TELEMETRY_FIELDS if f not in config.VERIFIED_TELEMETRY]
    if config.MODE == "live" and hidden:
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
    unverified = [f for f in config.TELEMETRY_FIELDS if live and f not in config.VERIFIED_TELEMETRY]
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
    return {
        "mode": config.MODE,
        "server_time": time.time(),
        "safety": {
            "motion_enabled": config.MODE == "mock" or config.ENABLE_MOTION,
            "stop_connected": config.MODE == "mock" or config.JETSON_STOP_CONFIRMED,
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
                         ("not_configured" if not config.JETSON_URL else ("connected" if tel["connected"] else "disconnected")),
                "url": config.JETSON_URL or None,
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
    return services.missions.stop()


@app.post("/api/robot/resume")
def api_resume():
    try:
        return services.missions.resume()
    except MissionError as e:
        _error(e)


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
