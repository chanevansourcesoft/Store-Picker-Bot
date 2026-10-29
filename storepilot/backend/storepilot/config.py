"""
StorePilot settings, read once from the environment and storepilot/.env.

Two modes, never mixed:
    mock  built-in simulator. Every value it produces is labelled SIMULATED.
    live  the Jetson robot server (agent/robot_server/jetson_server.py, Contract v2).
          If the Jetson does not answer, the dashboard shows "Disconnected".
          It never falls back to the simulator.
"""

import os
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
# Map origins (x, y) from the Jetson's map .yaml files, to recognise which map Nav2 has loaded.
# (Asking rosapi for map_server's yaml_filename crashes rosapi on Humble when map_server is down.)
JETSON_MAP_ORIGINS = {
    "conf3": (-4.4, -2.16), "conf2": (-4.73, -1.65), "conf_mecanum": (-3.97, -3.26),
    "map_confrence": (-3.54, -0.981), "map_reception": (-1.43, -1.54), "map_test_new": (-1.18, -2.98),
    "office_map": (-1.36, -12.2), "map_01": (-6.41, -2.57), "map_100": (-10.9, -4.14),
    "map_main": (-0.607, -7.23), "map_09": (-5.3, -4.04), "map_garden": (-14.2, -7.02),
    "map_outside": (-5.67, -28.7), "map_office": (-9.91, -6.66), "map_02": (-20.7, -7.92),
}
ROS_BASE_FRAME = os.environ.get("ROS_BASE_FRAME", "base_footprint")
# SSH target for starting / stopping the robot software on the Jetson (key login)
JETSON_SSH = os.environ.get("JETSON_SSH", "ubuntu@192.168.149.1")
# Drive commands are refused when fewer LiDAR points than this lie on map walls (bad localization).
MIN_LOC_QUALITY = float(os.environ.get("STOREPILOT_MIN_LOC_QUALITY", "0.30"))
ROS_BATTERY_TOPIC = os.environ.get("ROS_BATTERY_TOPIC", "/ros_robot_controller/battery")
# Live-mode safety switches. Both are OFF unless the operator turns them on.
ENABLE_MOTION = _flag("STOREPILOT_ENABLE_MOTION")
JETSON_STOP_CONFIRMED = _flag("JETSON_STOP_CONFIRMED")
# The GR00T pick (pick + place in basket, robot_server/arm.py) is a timed stub today. Until it returns
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

# Real robot videos (Real robot page). Uploading, adding links and deleting need this team password;
# empty = the page only plays the videos already there (anyone can watch).
VIDEOS_DIR = Path(os.environ.get("STOREPILOT_VIDEOS_DIR", ROOT / "data" / "videos"))
UPLOAD_PASSWORD = os.environ.get("STOREPILOT_UPLOAD_PASSWORD", "")
MAX_VIDEO_MB = int(os.environ.get("STOREPILOT_MAX_VIDEO_MB", "500"))
# Live robot camera as an MJPEG stream the browser can open (live mode), e.g. http://<jetson>:8090/live/scene_cam.mjpg.
# Empty = the Robot camera panel plays the newest real robot video instead.
CAMERA_URL = os.environ.get("STOREPILOT_CAMERA_URL", "").strip()

# Telemetry timing
POLL_SEC_LIVE = 1.0
POLL_SEC_MOCK = 0.2
STALE_AFTER_SEC = 3.0        # telemetry older than this is "stale"
DISCONNECTED_AFTER_SEC = 6.0  # no successful poll for this long -> "disconnected"

# Display names for stations (the robot uses the ids on the left)
# The store: one table (two shelf levels) and the reception. The robot hands the basket over at
# the reception and parks there, so "home" and "customer" are the same place.
STATION_LABELS = {
    "home": "Reception",
    "shelf_A": "Table",       # one table with two shelf levels (upper_shelf, lower_shelf)
    "shelf_B": "Shelf B",
    "shelf_C": "Shelf C",
    "customer": "Reception",
}

MAX_UNITS_PER_MISSION = 10   # sanity limit for one order


def station_label(station):
    return STATION_LABELS.get(station, str(station or "?").replace("_", " ").title())


def product_label(item):
    """Display name from the shared catalog (agent/store_agent/catalog.json)."""
    from store_agent.inventory import product_name   # after .env is loaded above
    return product_name(item)
