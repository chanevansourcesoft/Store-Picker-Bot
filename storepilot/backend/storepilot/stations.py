"""
Named stations (Home, Shelf A, Packing area...) saved per ROS map, on the Pi.
Every map has its own coordinates, so stations are always looked up by map name.

data/stations.json:
    {"conf3": {"home": {"x": 0.1, "y": -0.2, "theta": 1.57}, "shelf_A": {...}}}
"""

import json
import math
import threading
from pathlib import Path

VALID_NAMES = ("home", "shelf_A", "shelf_B", "shelf_C", "customer")


class StationStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        try:
            self.data = json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}

    def get(self, map_name):
        with self.lock:
            return {k: dict(v) for k, v in self.data.get(map_name or "", {}).items()}

    def save(self, map_name, name, pose):
        if not map_name:
            raise ValueError("The current map is unknown: load a map from the dashboard first.")
        if name not in VALID_NAMES:
            raise ValueError(f"Station must be one of {', '.join(VALID_NAMES)}.")
        entry = {"x": round(pose["x"], 3), "y": round(pose["y"], 3),
                 "theta": round(math.atan2(math.sin(pose["theta"]), math.cos(pose["theta"])), 3)}
        with self.lock:
            self.data.setdefault(map_name, {})[name] = entry
            self._write()
        return entry

    def delete(self, map_name, name):
        with self.lock:
            self.data.get(map_name or "", {}).pop(name, None)
            self._write()

    def _write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2) + "\n")
        tmp.replace(self.path)
