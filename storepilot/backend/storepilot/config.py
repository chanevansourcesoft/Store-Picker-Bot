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
# How live mode talks to the Jetson:
#   http      = jetson_server.py (Contract v2) at JETSON_URL
#   rosbridge = Nav2 directly through rosbridge_websocket at ROSBRIDGE_URL (no Jetson code needed)
JETSON_LINK = os.environ.get("JETSON_LINK", "http").strip().lower()
ROSBRIDGE_URL = os.environ.get("ROSBRIDGE_URL", "").strip()
JETSON_MAPS_DIR = os.environ.get("JETSON_MAPS_DIR", "/home/ubuntu/ros2_ws/src/slam/maps")
JETSON_MAPS = [m.strip() for m in os.environ.get(
    "JETSON_MAPS", "conf3,conf2,conf_mecanum,map_confrence,map_reception,map_test_new,office_map,map_01,"
    "map_100,map_main,map_09,map_garden,map_outside,map_office,map_02").split(",") if m.strip()]
ROS_BASE_FRAME = os.environ.get("ROS_BASE_FRAME", "base_footprint")
ROS_BATTERY_TOPIC = os.environ.get("ROS_BATTERY_TOPIC", "/ros_robot_controller/battery")
# Live-mode safety switches. Both are OFF unless the operator turns them on.
ENABLE_MOTION = _flag("STOREPILOT_ENABLE_MOTION")
JETSON_STOP_CONFIRMED = _flag("JETSON_STOP_CONFIRMED")
# Pick / scan / place on the Jetson are timed stubs today. Until a real implementation returns
# verified results, live missions cannot start and holding / pick results are not shown.
MANIPULATION_VERIFIED = _flag("STOREPILOT_MANIPULATION_VERIFIED")
# Live telemetry fields shown only after you checked them on the robot: pose, battery, speed.
# jetson_server.py reports battery 100 by default and a constant speed, so none are trusted by default.
TELEMETRY_FIELDS = ("pose", "battery", "speed")
VERIFIED_TELEMETRY = {f.strip() for f in os.environ.get("STOREPILOT_VERIFIED_TELEMETRY", "").split(",")
                      if f.strip() in TELEMETRY_FIELDS}

HOST = os.environ.get("STOREPILOT_HOST", "127.0.0.1")
PORT = int(os.environ.get("STOREPILOT_PORT", "8080"))

DB_PATH = Path(os.environ.get("STOREPILOT_DB", ROOT / "data" / "storepilot.db"))
STATIONS_PATH = Path(os.environ.get("STOREPILOT_STATIONS", ROOT / "data" / "stations.json"))
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
