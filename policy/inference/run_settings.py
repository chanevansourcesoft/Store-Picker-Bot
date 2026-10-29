"""
The run settings that can be changed between episodes, their limits, and the saved defaults.

The app's Run step saves a good set of settings with "Save as default" into run_settings.json (next to
this file, not in git: it belongs to this arm). They are then the defaults of run_policy.py (run.sh),
the policy worker and the app's Run form; options given on the command line still win.
Plain Python (no LeRobot), so the app can use it too.
"""

import json
from pathlib import Path

FILE = Path(__file__).resolve().parent / "run_settings.json"

# name: (type, lowest, highest)
LIMITS = {"speed": (float, 0.3, 1.5), "smooth": (float, 0.0, 0.9), "blend_steps": (int, 0, 16),
          "substeps": (int, 1, 5), "accel": (int, 0, 253), "wait_act": (float, 0, 30),
          "drop_wait": (float, 0, 5), "max_s": (float, 5, 300)}


def check(values):
    """The known settings of values, converted and checked. Raises ValueError with a message for people."""
    out = {}
    for name, (kind, low, high) in LIMITS.items():
        if values.get(name) is None:
            continue
        try:
            value = kind(values[name])
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be a number.") from None
        if not low <= value <= high:
            raise ValueError(f"{name} must be {low:g} to {high:g}.")
        out[name] = value
    return out


def load():
    """The saved settings ({} when none are saved). A broken file is reported and ignored."""
    if not FILE.exists():
        return {}
    try:
        return check(json.loads(FILE.read_text()))
    except (OSError, ValueError, AttributeError) as e:
        print(f"Ignoring {FILE}: {e}", flush=True)
        return {}


def save(values):
    """Check and save the settings as the new defaults. Returns what was saved."""
    settings = check(values)
    missing = [name for name in LIMITS if name not in settings]
    if missing:
        raise ValueError("Missing: " + ", ".join(missing))
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n")
    tmp.replace(FILE)
    return settings
