"""Shared settings for the app.

Settings come from environment variables (or the .env file).
If a setting is not there, we look in Streamlit secrets (.streamlit/secrets.toml,
or the "Secrets" box on Streamlit Community Cloud).
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


def get_setting(name, default=""):
    """Read a setting: first the environment, then st.secrets. Never print secrets."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        import streamlit as st
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass  # no secrets file, or not running in Streamlit
    return default


NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
NEBIUS_API_KEY = get_setting("NEBIUS_API_KEY")
ROBOT_URL = get_setting("ROBOT_URL", "http://localhost:8000")
# DEMO_MODE=1 -> always use the simulated robot (no robot server needed)
DEMO_MODE = get_setting("DEMO_MODE", "0").strip().lower() in ("1", "true", "yes")

# Fast model for simple jobs (reading orders)
MODEL_FAST = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
# Main "brain" for the agent (tool calls to the robot)
MODEL_AGENT = "nvidia/nemotron-3-super-120b-a12b"
# Large model for summaries and reports (expensive, use sparingly)
MODEL_ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"

INVENTORY_FILE = Path(__file__).resolve().parent / "inventory.json"

# Starting stock for a fresh server (used when inventory.json is missing or empty)
DEFAULT_INVENTORY = {
    "soup_can": {"shelf": "shelf_A", "bin": "A1", "barcode": "8901000000011",
                 "price": 60, "weight": "heavy", "stock": 10},
    "chips_pack": {"shelf": "shelf_B", "bin": "B1", "barcode": "8901000000028",
                   "price": 20, "weight": "light", "stock": 15},
    "juice_box": {"shelf": "shelf_C", "bin": "C1", "barcode": "8901000000035",
                  "price": 35, "weight": "medium", "stock": 12},
}


def load_inventory():
    return json.loads(INVENTORY_FILE.read_text())


def ensure_inventory():
    """Write the starting inventory if inventory.json is missing or empty.
    Call this once at startup (not on every read). Returns True if it seeded."""
    try:
        if json.loads(INVENTORY_FILE.read_text()):
            return False
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    INVENTORY_FILE.write_text(json.dumps(DEFAULT_INVENTORY, indent=2) + "\n")
    return True


def get_client():
    if not NEBIUS_API_KEY:
        raise RuntimeError("NEBIUS_API_KEY is missing. Add it to .env or .streamlit/secrets.toml.")
    return OpenAI(base_url=NEBIUS_BASE_URL, api_key=NEBIUS_API_KEY)
