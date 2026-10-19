"""
Adapter for the Jetson robot server (agent/robot_server/jetson_server.py, Contract v2).

    GET  /health  -> {"status": "ok", "ros_available": bool, "ros_connected": bool}  (older servers: "healthy")
    GET  /status  -> busy, holding, station, pose, battery, speed, nav_state, obstacle, last_error, stopped
    GET  /map     -> {"stations": {name: {x, y, theta}}}
    POST /navigate {station} | /pick {item, shelf, instruction} | /scan | /place {target}
    POST /stop | /resume

Nothing here falls back to simulated data: a failed request is a failure.
"""

import time

import requests

from .base import VALID_STATIONS, AdapterError, RobotAdapter

TELEMETRY_TIMEOUT = (1.5, 2.0)    # connect, read (seconds)
COMMAND_TIMEOUT = (3.0, 180.0)    # a navigation or pick can take minutes
STOP_TIMEOUT = (1.5, 3.0)


class JetsonAdapter(RobotAdapter):
    source = "jetson"

    def __init__(self, base_url):
        if not base_url:
            raise ValueError("JETSON_URL is not set")
        self.base_url = base_url.rstrip("/")
        self.last_latency_ms = None

    # ---------- telemetry ----------

    def _get(self, path):
        start = time.monotonic()
        try:
            r = requests.get(self.base_url + path, timeout=TELEMETRY_TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            raise AdapterError(f"{path}: {e.__class__.__name__}") from e
        except ValueError as e:
            raise AdapterError(f"{path}: answer is not JSON") from e
        self.last_latency_ms = round((time.monotonic() - start) * 1000)
        if not isinstance(data, dict):
            raise AdapterError(f"{path}: unexpected answer")
        return data

    def fetch_health(self):
        data = self._get("/health")
        # the contract says "ok"; jetson_server.py before the fix said "healthy": accept both
        if data.get("status") not in ("healthy", "ok"):
            raise AdapterError(f"/health: status {data.get('status')!r}")
        return data

    def fetch_status(self):
        return self._get("/status")

    def fetch_map(self):
        data = self._get("/map")
        stations = data.get("stations")
        if not isinstance(stations, dict) or not stations:
            raise AdapterError("/map: no stations")
        return {"stations": {name: {"x": float(p["x"]), "y": float(p["y"]), "theta": float(p.get("theta", 0.0))}
                             for name, p in stations.items() if isinstance(p, dict) and "x" in p and "y" in p}}

    # ---------- commands ----------

    def _post(self, path, body=None, timeout=COMMAND_TIMEOUT):
        try:
            r = requests.post(self.base_url + path, json=body, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else {"success": False, "message": "unexpected answer"}
        except requests.exceptions.ConnectionError:
            return {"success": False, "message": f"cannot reach Jetson at {self.base_url}"}
        except requests.exceptions.Timeout:
            return {"success": False, "message": "Jetson did not answer in time"}
        except Exception as e:
            return {"success": False, "message": f"Jetson error: {e}"}

    def navigate(self, station):
        if station not in VALID_STATIONS:
            return {"success": False, "message": f"unknown station {station!r}"}
        return self._post("/navigate", {"station": station})

    def pick(self, item, shelf, instruction=None):
        return self._post("/pick", {"item": item, "shelf": shelf, "instruction": instruction or ""})

    def scan(self):
        return self._post("/scan")

    def place(self, target):
        if target not in ("basket", "return"):
            return {"success": False, "message": "target must be 'basket' or 'return'"}
        return self._post("/place", {"target": target})

    def stop(self):
        return self._post("/stop", timeout=STOP_TIMEOUT)

    def resume(self):
        return self._post("/resume", timeout=STOP_TIMEOUT)
