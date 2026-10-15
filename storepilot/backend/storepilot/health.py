"""
Health of the parts the dashboard depends on:
- this Raspberry Pi (temperature, load, memory, disk, uptime) read from Linux
- the AI backend (Nemotron on Nebius Token Factory), checked from the server only
"""

import os
import shutil
import threading
import time

from app.config import MODEL_AGENT, MODEL_FAST, get_client, has_llm

STARTED = time.monotonic()


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def pi_health():
    temp = _read("/sys/class/thermal/thermal_zone0/temp")
    mem = {}
    for line in (_read("/proc/meminfo") or "").splitlines():
        key, _, value = line.partition(":")
        mem[key] = int(value.split()[0]) if value.split() else 0
    mem_used = None
    if mem.get("MemTotal"):
        mem_used = round(100 * (1 - mem.get("MemAvailable", 0) / mem["MemTotal"]))
    disk = shutil.disk_usage("/")
    uptime = _read("/proc/uptime")
    model = (_read("/proc/device-tree/model") or "").replace("\x00", "") or None
    return {
        "model": model,
        "cpu_temp_c": round(int(temp) / 1000, 1) if temp and temp.isdigit() else None,
        "load_1m": round(os.getloadavg()[0], 2),
        "cpus": os.cpu_count(),
        "mem_used_pct": mem_used,
        "disk_free_gb": round(disk.free / 1e9, 1),
        "system_uptime_sec": int(float(uptime.split()[0])) if uptime else None,
        "dashboard_uptime_sec": int(time.monotonic() - STARTED),
    }


class AIStatus:
    """Is Nemotron reachable? Checked at start and every 5 minutes (models list only, no tokens)."""

    def __init__(self, on_event):
        self.on_event = on_event
        self.lock = threading.Lock()
        self.state = "offline" if not has_llm() else "unknown"
        self.detail = "No NEBIUS_API_KEY: offline keyword parser and autopilot" if not has_llm() else "Checking…"
        self.checked_at = None
        self.last_call = None      # {"ok", "at", "error"}

    def start(self):
        if has_llm():
            threading.Thread(target=self._loop, name="ai-status", daemon=True).start()

    def _loop(self):
        while True:
            self.check()
            time.sleep(300)

    def check(self):
        try:
            client = get_client().with_options(timeout=10.0)
            ids = {m.id for m in client.models.list().data}
            missing = [m for m in (MODEL_FAST, MODEL_AGENT) if m not in ids]
            state = "degraded" if missing else "ready"
            detail = ("Model not listed: " + ", ".join(missing)) if missing else "Nemotron models available"
        except Exception as e:
            state, detail = "error", f"Nebius Token Factory not reachable ({e.__class__.__name__})"
        with self.lock:
            changed = state != self.state
            self.state, self.detail, self.checked_at = state, detail, time.time()
        if changed:
            self.on_event("system", "success" if state == "ready" else "error", f"AI backend: {detail}")

    def record(self, ok, error=None):
        """A real Nemotron call worked or failed."""
        with self.lock:
            self.last_call = {"ok": ok, "at": time.time(), "error": error}
            if ok and self.state in ("unknown", "error"):
                self.state, self.detail = "ready", "Nemotron answered"
            elif not ok:
                self.state, self.detail = "error", f"Last Nemotron call failed: {error}"

    def snapshot(self):
        with self.lock:
            return {
                "state": self.state,
                "detail": self.detail,
                "provider": "Nebius Token Factory" if has_llm() else None,
                "parser_model": MODEL_FAST.split("/")[-1] if has_llm() else None,
                "planner_model": MODEL_AGENT.split("/")[-1] if has_llm() else "offline autopilot",
                "checked_at": self.checked_at,
                "last_call": self.last_call,
            }
