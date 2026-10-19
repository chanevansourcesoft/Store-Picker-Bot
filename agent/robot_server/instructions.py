"""
GR00T instructions per item, from policy/instructions.json (owned by the policy team).

    {"chips_packet": {"shelf": "shelf_A", "instruction": "pick up the chips packet and place it in the basket"}, ...}

The planner (Nemotron) only chooses item ids. The robot server looks up the
instruction here, so GR00T always gets the exact text it was trained with.
Set POLICY_INSTRUCTIONS_FILE to use another file.
"""

import json
import os
from pathlib import Path

INSTRUCTIONS_FILE = Path(os.environ.get(
    "POLICY_INSTRUCTIONS_FILE", Path(__file__).resolve().parents[2] / "policy" / "instructions.json"))


def load_instructions():
    """{item: {"shelf", "instruction"}}. Read on every call, so an edited file needs no restart."""
    return json.loads(Path(INSTRUCTIONS_FILE).read_text())


def lookup(item, shelf=None):
    """(instruction, None) for a known item, or (None, problem text)."""
    try:
        entry = load_instructions().get(item)
    except (OSError, json.JSONDecodeError) as e:
        return None, f"cannot read GR00T instructions ({Path(INSTRUCTIONS_FILE).name}): {e}"
    if not entry or not entry.get("instruction"):
        return None, f"no GR00T instruction for {item}"
    if shelf is not None and entry.get("shelf") != shelf:
        return None, f"{item} is on {entry.get('shelf')}, not {shelf}"
    return entry["instruction"], None
