"""
Which station (rack) each product is at, for the robot server.

Read straight from agent/store_agent/catalog.json (STORE_CATALOG_FILE to use another file),
without importing store_agent, so the robot server's ROS container needs no openai or dotenv.

    station_of("ink_box") -> "shelf_A"     (None for an unknown item)
"""

import json
import os
from pathlib import Path

CATALOG_FILE = Path(os.environ.get(
    "STORE_CATALOG_FILE", Path(__file__).resolve().parents[1] / "store_agent" / "catalog.json"))


def load_products():
    """{item: {...}} from the catalog. Read on every call, like instructions.json."""
    return json.loads(Path(CATALOG_FILE).read_text())["products"]


def station_of(item):
    try:
        info = load_products().get(item)
    except (OSError, ValueError, KeyError):
        return None
    return info.get("shelf") if isinstance(info, dict) else None
