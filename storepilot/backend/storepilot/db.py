"""
SQLite store for missions and events. It lives on the Pi, so history survives
Jetson disconnects and dashboard restarts.
"""

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

ACTIVE_STATUSES = ("awaiting_confirmation", "running")
JSON_FIELDS = ("items", "unknown", "route", "bill", "problems", "step_log")

SCHEMA = """
CREATE TABLE IF NOT EXISTS missions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    text TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    items TEXT NOT NULL DEFAULT '[]',
    unknown TEXT NOT NULL DEFAULT '[]',
    route TEXT NOT NULL DEFAULT '[]',
    parser TEXT,
    planner TEXT,
    confirmed_at TEXT,
    finished_at TEXT,
    packed INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    bill TEXT NOT NULL DEFAULT '{}',
    problems TEXT NOT NULL DEFAULT '[]',
    step_log TEXT NOT NULL DEFAULT '[]',
    stock_reserved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    mission_id INTEGER,
    source TEXT NOT NULL,     -- robot / ai / operator / system
    level TEXT NOT NULL,      -- info / success / warning / error
    text TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 0
);
"""


def now_iso():
    return datetime.now().isoformat(timespec="milliseconds")


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        # Databases from before stock reservation: add the column (old missions = not reserved)
        if "stock_reserved" not in [r[1] for r in self.conn.execute("PRAGMA table_info(missions)")]:
            self.conn.execute("ALTER TABLE missions ADD COLUMN stock_reserved INTEGER NOT NULL DEFAULT 0")
        self.conn.commit()

    # ---------- missions ----------

    def _mission(self, row):
        if row is None:
            return None
        m = dict(row)
        for field in JSON_FIELDS:
            try:
                m[field] = json.loads(m[field])
            except (TypeError, json.JSONDecodeError):
                pass
        return m

    def create_mission(self, **fields):
        fields.setdefault("created_at", now_iso())
        cols, values = [], []
        for key, value in fields.items():
            cols.append(key)
            values.append(json.dumps(value) if key in JSON_FIELDS else value)
        with self.lock:
            cur = self.conn.execute(
                f"INSERT INTO missions ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", values)
            self.conn.commit()
            return cur.lastrowid

    def update_mission(self, mission_id, **fields):
        if not fields:
            return
        sets = ", ".join(f"{key} = ?" for key in fields)
        values = [json.dumps(v) if k in JSON_FIELDS else v for k, v in fields.items()]
        with self.lock:
            self.conn.execute(f"UPDATE missions SET {sets} WHERE id = ?", values + [mission_id])
            self.conn.commit()

    def get_mission(self, mission_id):
        with self.lock:
            row = self.conn.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
        return self._mission(row)

    def list_missions(self, limit=50):
        with self.lock:
            rows = self.conn.execute("SELECT * FROM missions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._mission(r) for r in rows]

    def finished_missions(self, mode, limit=20):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM missions WHERE mode = ? AND status = 'completed' AND finished_at IS NOT NULL "
                "ORDER BY id DESC LIMIT ?", (mode, limit)).fetchall()
        return [self._mission(r) for r in rows]

    def take_reservation(self, mission_id):
        """True once for a mission that still holds reserved stock (then it is marked released)."""
        with self.lock:
            cursor = self.conn.execute(
                "UPDATE missions SET stock_reserved = 0 WHERE id = ? AND stock_reserved = 1", (mission_id,))
            self.conn.commit()
            return cursor.rowcount == 1

    def close_unfinished(self):
        """After a restart nothing resumes by itself: running -> interrupted, waiting -> expired."""
        with self.lock:
            running = self.conn.execute("SELECT id FROM missions WHERE status = 'running'").fetchall()
            waiting = self.conn.execute(
                "SELECT id FROM missions WHERE status = 'awaiting_confirmation'").fetchall()
            self.conn.execute(
                "UPDATE missions SET status = 'interrupted', finished_at = ? WHERE status = 'running'", (now_iso(),))
            self.conn.execute(
                "UPDATE missions SET status = 'expired', finished_at = ? WHERE status = 'awaiting_confirmation'",
                (now_iso(),))
            self.conn.commit()
        return [r["id"] for r in running], [r["id"] for r in waiting]

    # ---------- events ----------

    def add_event(self, source, level, text, mission_id=None, simulated=False):
        event = {"ts": now_iso(), "mission_id": mission_id, "source": source, "level": level,
                 "text": text, "simulated": bool(simulated)}
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO events (ts, mission_id, source, level, text, simulated) VALUES (?, ?, ?, ?, ?, ?)",
                (event["ts"], mission_id, source, level, text, int(simulated)))
            self.conn.commit()
        event["id"] = cur.lastrowid
        return event

    def recent_events(self, limit=60, mission_id=None):
        with self.lock:
            if mission_id is None:
                rows = self.conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = self.conn.execute("SELECT * FROM events WHERE mission_id = ? ORDER BY id DESC LIMIT ?",
                                         (mission_id, limit)).fetchall()
        events = [dict(r) for r in rows]
        for e in events:
            e["simulated"] = bool(e["simulated"])
        return events
