"""
Mock robot server (Contract v2).

It follows the same API contract as the real robot server (see CLAUDE.md).
The app team uses this until the real mobile robot is ready.

Run:
    python robot_server/mock_robot.py
    # or: uvicorn robot_server.mock_robot:app --port 8000
"""

import json
import math
import random
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

INVENTORY_FILE = Path(__file__).resolve().parent.parent / "app" / "inventory.json"

def load_inventory():
    return json.loads(INVENTORY_FILE.read_text())

# Chance that a step fails, so the agent can practice handling problems.
PICK_FAIL_RATE = 0.10
SCAN_FAIL_RATE = 0.10
NAV_FAIL_RATE = 0.05
FAKE_DELAY_SEC = 0.5
NAV_DELAY_SEC = 1.0

# Station positions (x, y in meters)
STATION_MAP = {
    "home": {"x": 0.0, "y": 0.0},
    "shelf_A": {"x": 2.0, "y": 1.0},
    "shelf_B": {"x": 2.0, "y": 2.0},
    "shelf_C": {"x": 2.0, "y": 3.0},
    "customer": {"x": 0.0, "y": 3.0},
}

VALID_STATIONS = set(STATION_MAP.keys())

app = FastAPI(title="Mock Robot Server (Contract v2)")

# Robot state
state = {
    "busy": False,
    "holding": None,
    "from_shelf": None,
    "station": "home",
    "pose": {"x": 0.0, "y": 0.0, "theta": 0.0},
    "battery": 100.0,
    "speed": 0.0,
    "nav_state": "idle",  # "idle", "navigating", "arrived", "failed"
    "obstacle": False,
    "last_error": None,
    "stopped": False,
}


class NavigateRequest(BaseModel):
    station: str  # "home", "shelf_A", "shelf_B", "shelf_C", "customer"


class PickRequest(BaseModel):
    item: str
    shelf: str  # "shelf_A", "shelf_B", "shelf_C"
    instruction: Optional[str] = None  # Text instruction for GR00T


class PlaceRequest(BaseModel):
    target: str  # "basket" or "return"


def fail(message):
    state["last_error"] = message
    return {"success": False, "message": message}


def drain_battery(amount=0.1):
    """Simulate battery drain."""
    state["battery"] = max(0.0, state["battery"] - amount)


def move_to_station(station):
    """Simulate movement to a station."""
    target = STATION_MAP[station]
    state["pose"]["x"] = target["x"]
    state["pose"]["y"] = target["y"]
    # Calculate angle to face the shelf (or home)
    if station.startswith("shelf"):
        state["pose"]["theta"] = math.pi / 2  # Face the shelf
    elif station == "customer":
        state["pose"]["theta"] = math.pi  # Face the customer
    else:
        state["pose"]["theta"] = 0.0  # Face forward


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/status")
def status():
    return {
        "busy": state["busy"],
        "holding": state["holding"],
        "station": state["station"],
        "pose": state["pose"].copy(),
        "battery": round(state["battery"], 1),
        "speed": state["speed"],
        "nav_state": state["nav_state"],
        "obstacle": state["obstacle"],
        "last_error": state["last_error"],
    }


@app.get("/map")
def get_map():
    """Return station positions for the dashboard."""
    return {
        "stations": {
            name: {"x": pos["x"], "y": pos["y"]}
            for name, pos in STATION_MAP.items()
        }
    }


@app.post("/navigate")
def navigate(req: NavigateRequest):
    """Navigate the robot to a station."""
    if state["stopped"]:
        return fail("robot is stopped, call /status to check")
    if state["busy"]:
        return fail("busy")
    if req.station not in VALID_STATIONS:
        return fail(f"unknown station '{req.station}', must be one of: {', '.join(sorted(VALID_STATIONS))}")
    if state["station"] == req.station:
        return {"success": True, "message": f"already at {req.station}"}

    state["busy"] = True
    state["nav_state"] = "navigating"
    state["speed"] = 0.3  # m/s

    time.sleep(NAV_DELAY_SEC)
    drain_battery(0.5)

    state["busy"] = False
    state["speed"] = 0.0

    if random.random() < NAV_FAIL_RATE:
        state["nav_state"] = "failed"
        state["obstacle"] = True
        return fail("navigation failed, obstacle detected")

    state["obstacle"] = False
    state["nav_state"] = "arrived"
    state["station"] = req.station
    move_to_station(req.station)

    return {"success": True, "message": f"arrived at {req.station}"}


@app.post("/pick")
def pick(req: PickRequest):
    """Pick an item. The instruction field is passed to GR00T policy."""
    if state["stopped"]:
        return fail("robot is stopped")
    if state["busy"]:
        return fail("busy")
    if state["holding"]:
        return fail(f"already holding {state['holding']}")

    inventory = load_inventory()
    if req.item not in inventory:
        return fail(f"unknown item {req.item}")
    if inventory[req.item]["shelf"] != req.shelf:
        return fail(f"{req.item} is not on {req.shelf}")
    if not state["station"].startswith("shelf"):
        return fail(f"not at a shelf, currently at {state['station']}")

    # Log instruction if provided (for debugging)
    if req.instruction:
        print(f"[GR00T instruction]: {req.instruction}")

    state["busy"] = True
    time.sleep(FAKE_DELAY_SEC)
    drain_battery(0.2)
    state["busy"] = False

    if random.random() < PICK_FAIL_RATE:
        return fail("grasp failed")

    state["holding"] = req.item
    state["from_shelf"] = req.shelf
    return {"success": True, "message": "picked"}


@app.post("/scan")
def scan():
    """Scan the barcode of the held item."""
    if state["stopped"]:
        return fail("robot is stopped")
    if state["busy"]:
        return fail("busy")
    if not state["holding"]:
        return fail("not holding anything")

    time.sleep(FAKE_DELAY_SEC)
    drain_battery(0.1)

    if random.random() < SCAN_FAIL_RATE:
        return {"success": False, "barcode": None, "message": "barcode not readable"}

    inventory = load_inventory()
    barcode = inventory[state["holding"]]["barcode"]
    return {"success": True, "barcode": barcode}


@app.post("/place")
def place(req: PlaceRequest):
    """Place the held item in basket or return it to shelf."""
    if state["stopped"]:
        return fail("robot is stopped")
    if state["busy"]:
        return fail("busy")
    if not state["holding"]:
        return fail("not holding anything")
    if req.target not in ("basket", "return"):
        return fail("target must be 'basket' or 'return'")

    time.sleep(FAKE_DELAY_SEC)
    drain_battery(0.1)

    item = state["holding"]
    state["holding"] = None
    state["from_shelf"] = None

    if req.target == "basket":
        return {"success": True, "message": f"placed {item} in basket"}
    return {"success": True, "message": f"returned {item} to shelf"}


@app.post("/stop")
def stop():
    """Emergency stop the robot."""
    state["stopped"] = True
    state["busy"] = False
    state["speed"] = 0.0
    state["nav_state"] = "idle"
    return {"success": True, "message": "robot stopped"}


@app.post("/resume")
def resume():
    """Resume after emergency stop."""
    state["stopped"] = False
    state["last_error"] = None
    return {"success": True, "message": "robot resumed"}


@app.get("/camera")
def camera():
    """
    Return a fake MJPEG stream (for dashboard).
    In the real robot, this streams from the camera.
    """
    def generate_fake_frames():
        # Generate a simple placeholder frame (1x1 black JPEG)
        fake_jpeg = bytes([
            0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,
            0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
            0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08, 0x07, 0x07, 0x07, 0x09,
            0x09, 0x08, 0x0A, 0x0C, 0x14, 0x0D, 0x0C, 0x0B, 0x0B, 0x0C, 0x19, 0x12,
            0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E, 0x1D, 0x1A, 0x1C, 0x1C, 0x20,
            0x24, 0x2E, 0x27, 0x20, 0x22, 0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29,
            0x2C, 0x30, 0x31, 0x34, 0x34, 0x34, 0x1F, 0x27, 0x39, 0x3D, 0x38, 0x32,
            0x3C, 0x2E, 0x33, 0x34, 0x32, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01,
            0x00, 0x01, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4, 0x00, 0x1F, 0x00, 0x00,
            0x01, 0x05, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
            0x09, 0x0A, 0x0B, 0xFF, 0xC4, 0x00, 0xB5, 0x10, 0x00, 0x02, 0x01, 0x03,
            0x03, 0x02, 0x04, 0x03, 0x05, 0x05, 0x04, 0x04, 0x00, 0x00, 0x01, 0x7D,
            0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06,
            0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xA1, 0x08,
            0x23, 0x42, 0xB1, 0xC1, 0x15, 0x52, 0xD1, 0xF0, 0x24, 0x33, 0x62, 0x72,
            0x82, 0x09, 0x0A, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x25, 0x26, 0x27, 0x28,
            0x29, 0x2A, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3A, 0x43, 0x44, 0x45,
            0x46, 0x47, 0x48, 0x49, 0x4A, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
            0x5A, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x73, 0x74, 0x75,
            0x76, 0x77, 0x78, 0x79, 0x7A, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
            0x8A, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3,
            0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6,
            0xB7, 0xB8, 0xB9, 0xBA, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9,
            0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA, 0xE1, 0xE2,
            0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xF1, 0xF2, 0xF3, 0xF4,
            0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA, 0xFF, 0xDA, 0x00, 0x08, 0x01, 0x01,
            0x00, 0x00, 0x3F, 0x00, 0xFB, 0xD5, 0xDB, 0x20, 0xA8, 0xC0, 0x1F, 0xFF,
            0xD9
        ])
        while True:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + fake_jpeg + b"\r\n"
            time.sleep(0.1)  # 10 fps

    return StreamingResponse(
        generate_fake_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


if __name__ == "__main__":
    import uvicorn
    print("Starting mock robot server on http://127.0.0.1:8000")
    print(f"Stations: {', '.join(sorted(VALID_STATIONS))}")
    uvicorn.run(app, host="127.0.0.1", port=8000)
