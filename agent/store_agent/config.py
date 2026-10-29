"""Shared settings for the store agent (used by the StorePilot AI dashboard).

Settings come from environment variables (or the .env file in the folder the app
is started from, or one of its parents).
"""

import os

from dotenv import find_dotenv, load_dotenv
from openai import OpenAI

load_dotenv(find_dotenv(usecwd=True))


def get_setting(name, default=""):
    """Read a setting from the environment (or .env). Never print secrets."""
    return os.environ.get(name) or default


NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
NEBIUS_API_KEY = get_setting("NEBIUS_API_KEY")
ROBOT_URL = get_setting("ROBOT_URL", "http://localhost:8000")
# DEMO_MODE=1 -> always use the simulated robot (no robot server needed)
DEMO_MODE = get_setting("DEMO_MODE", "0").strip().lower() in ("1", "true", "yes")


def _count(name, default):
    try:
        return max(0, int(get_setting(name, str(default))))
    except ValueError:
        print(f"[Config] {name} must be a whole number; using {default}")
        return default


# A pick whose arm episode ran but did not put the unit in the basket is retried this many
# times by the code (not by the model). Then the item is reported as not picked and the order goes on.
PICK_RETRIES = _count("PICK_RETRIES", 2)

# Fast model for simple jobs (reading orders)
MODEL_FAST = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
# Main "brain" for the agent (tool calls to the robot)
MODEL_AGENT = "nvidia/nemotron-3-super-120b-a12b"
# Large model for summaries and reports (expensive, use sparingly)
MODEL_ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"


def has_llm():
    """True if Nemotron can be called. Without a key the app uses the offline autopilot."""
    return bool(NEBIUS_API_KEY)


def get_client():
    if not NEBIUS_API_KEY:
        raise RuntimeError("NEBIUS_API_KEY is missing. Add it to storepilot/.env.")
    return OpenAI(base_url=NEBIUS_BASE_URL, api_key=NEBIUS_API_KEY)
