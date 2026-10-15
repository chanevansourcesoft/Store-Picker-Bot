"""
StorePilot settings, read once from the environment and storepilot/.env.

Two modes, never mixed:
    mock  built-in simulator. Every value it produces is labelled SIMULATED.
    live  the Jetson robot server (webapp/robot_server/jetson_server.py, Contract v2).
          If the Jetson does not answer, the dashboard shows "Disconnected".
          It never falls back to the simulator.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]          # storepilot/
load_dotenv(ROOT / ".env")


def _flag(name, default="0"):
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


MODE = os.environ.get("STOREPILOT_MODE", "mock").strip().lower()
if MODE not in ("mock", "live"):
    raise SystemExit(f"STOREPILOT_MODE must be 'mock' or 'live', got {MODE!r}")

JETSON_URL = os.environ.get("JETSON_URL", "").strip().rstrip("/")
# Live-mode safety switches. Both are OFF unless the operator turns them on.
ENABLE_MOTION = _flag("STOREPILOT_ENABLE_MOTION")
JETSON_STOP_CONFIRMED = _flag("JETSON_STOP_CONFIRMED")

HOST = os.environ.get("STOREPILOT_HOST", "127.0.0.1")
PORT = int(os.environ.get("STOREPILOT_PORT", "8080"))

DB_PATH = Path(os.environ.get("STOREPILOT_DB", ROOT / "data" / "storepilot.db"))
STATIC_DIR = ROOT / "frontend" / "dist"

# The shared agent code (Nemotron planner, order parser, safety guards) lives in ../webapp/app
WEBAPP_DIR = Path(os.environ.get("STOREPILOT_WEBAPP_DIR", ROOT.parent / "webapp")).resolve()
if str(WEBAPP_DIR) not in sys.path:
    sys.path.insert(0, str(WEBAPP_DIR))

# Telemetry timing
POLL_SEC_LIVE = 1.0
POLL_SEC_MOCK = 0.2
STALE_AFTER_SEC = 3.0        # telemetry older than this is "stale"
DISCONNECTED_AFTER_SEC = 6.0  # no successful poll for this long -> "disconnected"

# Display names for stations (the robot uses the ids on the left)
STATION_LABELS = {
    "home": "Home",
    "shelf_A": "Shelf A",
    "shelf_B": "Shelf B",
    "shelf_C": "Shelf C",
    "customer": "Packing area",
}

# Display names for products in webapp/app/inventory.json
PRODUCT_LABELS = {
    "soup_can": "Tomato Soup",
    "chips_pack": "Classic Chips",
    "juice_box": "Orange Juice",
}

MAX_UNITS_PER_MISSION = 10   # sanity limit for one order


def station_label(station):
    return STATION_LABELS.get(station, str(station or "?").replace("_", " ").title())


def product_label(item):
    return PRODUCT_LABELS.get(item, str(item or "?").replace("_", " ").title())
