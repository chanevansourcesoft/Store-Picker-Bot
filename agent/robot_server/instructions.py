"""
GR00T instructions per item, from policy/instructions.json (owned by the policy team).

    {"ink_box": {"shelf": "upper_shelf", "instruction": "pick up the ink box and place it in the basket"}, ...}

The planner (Nemotron) only chooses item ids. The robot server looks up the
instruction here, so GR00T always gets the exact text it was trained with.

"shelf" in that file is the shelf LEVEL on the rack (upper_shelf, lower_shelf), not a Nav2
station. Which station (rack) an item is at comes from the catalog
(agent/store_agent/catalog.json), so lookup() never compares the two.

Set POLICY_INSTRUCTIONS_FILE to use another file.
"""

import json
import os
import re
from pathlib import Path

INSTRUCTIONS_FILE = Path(os.environ.get(
    "POLICY_INSTRUCTIONS_FILE", Path(__file__).resolve().parents[2] / "policy" / "instructions.json"))

ITEM_ID = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")   # "ink_box": no spaces, no free text


def load_instructions():
    """{item: {"shelf", "instruction"}}. Read on every call, so an edited file needs no restart."""
    return json.loads(Path(INSTRUCTIONS_FILE).read_text())


def check_item_id(item):
    """None for a well-formed product id, else the problem (empty, free text, not a string)."""
    if not isinstance(item, str) or not item.strip():
        return "no item id given"
    if not ITEM_ID.match(item):
        return f"'{item[:60]}' is not a product id (expected an id like 'ink_box', not free text)"
    return None


def lookup(item):
    """(entry, None) for a known item, or (None, problem text). entry = {"instruction", "level"}."""
    problem = check_item_id(item)
    if problem:
        return None, problem
    try:
        entry = load_instructions().get(item)
    except (OSError, json.JSONDecodeError) as e:
        return None, f"cannot read GR00T instructions ({Path(INSTRUCTIONS_FILE).name}): {e}"
    if not isinstance(entry, dict) or not entry.get("instruction"):
        return None, f"no GR00T instruction for {item}"
    return {"instruction": entry["instruction"], "level": entry.get("shelf")}, None
