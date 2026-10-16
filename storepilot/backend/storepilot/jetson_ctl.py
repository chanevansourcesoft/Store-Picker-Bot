"""
Control of the robot software on the Jetson, over SSH (key login, no password).

Runs ~/storepilot_jetson/storepilot_ctl.sh on the Jetson (deployed from storepilot/jetson/):
    status                      JSON: navigation running / map / speed, rosbridge, watchdog, maps + origins
    nav-start <map> <speed>     ros2 launch mecanum_nav navigation.launch.py map:=... speed:=...
    nav-stop                    Ctrl+C the navigation launch (base, lidar filter, Nav2)
    bridge-restart              rosbridge (allow-list) + StorePilot link watchdog
    amcl-recovery on|off        runtime-only AMCL recovery_alpha_fast/slow (config files untouched)

Nothing in ~/ros2_ws is modified by any of these.
"""

import json
import re
import subprocess
import threading
import time

CTL = "~/storepilot_jetson/storepilot_ctl.sh"


class JetsonControl:
    def __init__(self, target, on_maps=None):
        self.target = target              # e.g. ubuntu@192.168.149.1
        self.on_maps = on_maps            # callback(list of {"name", "origin"}) when the map list changes
        self.lock = threading.Lock()
        self.status = None
        self.status_at = None
        self.error = None
        self.busy = None                  # name of the action running now, e.g. "nav-start"
        self.amcl_recovery = None         # True / False / None (unknown)
        self._maps_sig = None
        threading.Thread(target=self._poll, name="jetson-ctl", daemon=True).start()

    def _ssh(self, args, timeout):
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4", "-o", "ServerAliveInterval=5",
               self.target, f"bash {CTL} " + " ".join(args)]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, f"no answer from the Jetson in {timeout} s"
        out = (p.stdout or "").strip()
        if p.returncode == 255:
            return False, "SSH to the Jetson failed (network or key)"
        if p.returncode != 0:
            return False, out.splitlines()[-1] if out else (p.stderr or "").strip()[-200:] or f"exit {p.returncode}"
        return True, out

    def refresh(self):
        ok, out = self._ssh(["status"], timeout=20)
        with self.lock:
            if ok:
                try:
                    self.status = json.loads(out.splitlines()[-1])
                    self.status_at = time.time()
                    self.error = None
                except (ValueError, IndexError):
                    self.error = "unreadable status from the Jetson"
            else:
                self.error = out
            maps = (self.status or {}).get("maps")
        if ok and maps and self.on_maps:
            sig = json.dumps(maps, sort_keys=True)
            if sig != self._maps_sig:
                self._maps_sig = sig
                self.on_maps(maps)
        return ok

    def _poll(self):
        while True:
            if self.busy is None:
                self.refresh()
            time.sleep(10)

    def _action(self, name, args, timeout):
        with self.lock:
            if self.busy:
                return False, f"Jetson is busy: {self.busy}"
            self.busy = name
        try:
            ok, out = self._ssh(args, timeout)
            msg = out.splitlines()[-1] if out else ""
            msg = re.sub(r"^(OK|ERROR)\s+", "", msg)
            return ok and not out.startswith("ERROR"), msg
        finally:
            with self.lock:
                self.busy = None
            self.refresh()

    def nav_start(self, map_name, speed):
        if not re.fullmatch(r"[A-Za-z0-9_]+", map_name or ""):        # it goes into a remote shell command
            return False, f"invalid map name {map_name!r}"
        speed = float(speed)
        if not 0.05 <= speed <= 0.30:
            return False, "speed must be between 0.05 and 0.30 m/s"
        return self._action("starting navigation", ["nav-start", map_name, f"{float(speed):.2f}"], timeout=100)

    def nav_stop(self):
        return self._action("stopping navigation", ["nav-stop"], timeout=45)

    def bridge_restart(self):
        return self._action("restarting rosbridge", ["bridge-restart"], timeout=40)

    def set_amcl_recovery(self, on):
        ok, out = self._action("setting AMCL recovery", ["amcl-recovery", "on" if on else "off"], timeout=40)
        if ok:
            self.amcl_recovery = bool(on)
        return ok, ("AMCL recovery " + ("on (alpha_fast 0.1, alpha_slow 0.001)" if on else "off (0, 0)")) if ok else out

    def snapshot(self):
        with self.lock:
            return {
                "target": self.target,
                "status": dict(self.status) if self.status else None,
                "age_s": None if self.status_at is None else round(time.time() - self.status_at),
                "error": self.error,
                "busy": self.busy,
                "amcl_recovery": self.amcl_recovery,
            }
