"""
Polls the robot adapter in a background thread and keeps the latest answers
with their timestamps. Nothing is invented: when a value is missing or old,
the snapshot says so ("stale", "unavailable", "disconnected").
"""

import threading
import time

from . import config


class Telemetry:
    def __init__(self, adapter, on_event):
        self.adapter = adapter
        self.on_event = on_event          # on_event(source, level, text)
        self.poll_sec = config.POLL_SEC_MOCK if adapter.source == "simulated" else config.POLL_SEC_LIVE
        self.lock = threading.Lock()
        self.status = None
        self.status_at = None             # monotonic time of the last good /status
        self.health = None
        self.health_at = None
        self.map = None
        self.map_at = None
        self.last_error = None
        self.connected = None             # None = not known yet
        self.ever_connected = False
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="telemetry", daemon=True)

    def start(self):
        self.thread.start()

    def shutdown(self):
        self._stop.set()

    # ---------- loop ----------

    def _run(self):
        last_health = last_map = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            try:
                if now - last_health > 5 or self.health is None:
                    health = self.adapter.fetch_health()
                    with self.lock:
                        self.health, self.health_at = health, time.monotonic()
                    last_health = now
                status = self.adapter.fetch_status()
                with self.lock:
                    self.status, self.status_at, self.last_error = status, time.monotonic(), None
                if self.map is None or now - last_map > 60:
                    try:
                        robot_map = self.adapter.fetch_map()
                        with self.lock:
                            self.map, self.map_at = robot_map, time.monotonic()
                    except Exception as e:  # a missing map is not a lost connection
                        with self.lock:
                            self.last_error = f"map: {e}"
                    last_map = now
                self._set_connected(True)
            except Exception as e:
                with self.lock:
                    self.last_error = str(e)
                if self.status_at is None or time.monotonic() - self.status_at > config.DISCONNECTED_AFTER_SEC:
                    self._set_connected(False)
            self._stop.wait(self.poll_sec)

    def _set_connected(self, connected):
        if connected == self.connected:
            return
        previous, self.connected = self.connected, connected
        name = "Simulated robot" if self.adapter.source == "simulated" else "Jetson"
        if connected:
            text = f"{name} connected" if not self.ever_connected else f"{name} connection restored"
            self.ever_connected = True
            self.on_event("system", "success", text)
        elif previous is not None or not self.ever_connected:
            self.on_event("system", "error", f"{name} disconnected: {self.last_error or 'no answer'}")

    # ---------- reading ----------

    def age(self):
        """Seconds since the last good /status, or None if never."""
        with self.lock:
            return None if self.status_at is None else time.monotonic() - self.status_at

    def snapshot(self):
        with self.lock:
            return {
                "status": dict(self.status) if self.status else None,
                "health": dict(self.health) if self.health else None,
                "map": self.map,
                "age": None if self.status_at is None else time.monotonic() - self.status_at,
                "last_error": self.last_error,
                "connected": bool(self.connected),
                "ever_connected": self.ever_connected,
            }
