"""
studio.py

Store Picker Bot: a local app that guides anyone through recording demonstrations on the SO-100
arms without a terminal: campaign (one dataset and its recording plan), checks of the computer,
the arms and the cameras, recording one round at a time with a live camera view, and reviewing
every episode. Start it with run_studio.sh or the desktop icon (install_desktop_icon.sh).

The server runs in lerobot_env. For each round it starts data_collection/record.py --studio and
sends it the operator's commands (start, save, discard, quit) on stdin; the dataset, its plan and
its episode log are the same as when recording in a terminal. It listens on 127.0.0.1 only and
stops by itself a few minutes after the app window is closed, never while a round runs.
Admin settings: studio_config.json. Guide: policy/docs/DATA_STUDIO.md.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

STUDIO_DIR = Path(__file__).resolve().parent
POLICY_DIR = STUDIO_DIR.parent
REPO_DIR = POLICY_DIR.parent
WEB_DIR = STUDIO_DIR / "web"
DATA_COLLECTION_DIR = POLICY_DIR / "data_collection"
RECORD_SCRIPT = DATA_COLLECTION_DIR / "record.py"
REMOVE_SCRIPT = DATA_COLLECTION_DIR / "remove_bad_episodes.py"

# record.py holds the plan logic (rounds, base positions, what comes next); the app uses the same.
# Importing it also points HF_LEROBOT_CALIBRATION at policy/calibration/.
sys.path.insert(0, str(DATA_COLLECTION_DIR))
import record  # noqa: E402
from dataset_view import DatasetView  # noqa: E402  (studio/dataset_view.py)

CONFIG = json.loads((STUDIO_DIR / "studio_config.json").read_text())
STATE_FILE = Path.home() / ".config" / "store_picker_studio" / "state.json"  # open campaign
CACHE_DIR = Path.home() / ".cache" / "store_picker_studio"
LIVE_DIR = CACHE_DIR / "live"  # camera snapshots written by record.py while it runs
TEST_DIR = CACHE_DIR / "camera_test"  # test pictures from the Check step
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
STATIC_RE = re.compile(r"^[A-Za-z0-9_.-]+\.(html|css|js|svg|jpg|png)$")
DEVICES = ["FOLLOWER_PORT", "LEADER_PORT", "GRIPPER_CAM", "SCENE_CAM"]
CAMERAS = {record.GRIPPER_CAM: "GRIPPER_CAM", record.SCENE_CAM: "SCENE_CAM"}
JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
IDLE_EXIT_S = 180  # stop the server this long after the window stopped sending heartbeats


def device_env():
    """The process environment plus the repo root .env (arm ports, cameras), read fresh each time."""
    env = dict(os.environ)
    path = REPO_DIR / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
            if m and not line.lstrip().startswith("#"):
                env[m.group(1)] = m.group(2).strip().strip("'\"")
    return env


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Campaigns: one dataset each (data_collection/dataset/<folder>) with meta/plan.json
# ---------------------------------------------------------------------------
def campaign_root(folder):
    if not folder or not NAME_RE.match(folder) or folder.endswith(("_old", "_new")):
        raise ValueError(f"not a dataset folder: {folder!r}")
    return record.DATASET_DIR / folder


class NoEpisodes:
    """Stands in for the episode log of a campaign that has not recorded anything yet."""

    data = {"episodes": []}

    def good_count(self, item, round_):
        return 0


def load_campaign(folder):
    """(plan, log) of a dataset recorded with a plan, or None."""
    root = campaign_root(folder)
    try:
        plan = json.loads((root / "meta" / record.PLAN_FILE).read_text())
    except (FileNotFoundError, ValueError):
        return None
    return plan, record.EpisodeLog(root)


def list_campaigns():
    rows = []
    if not record.DATASET_DIR.is_dir():
        return rows
    for path in sorted(record.DATASET_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            loaded = path.is_dir() and load_campaign(path.name)
        except ValueError:
            continue
        if not loaded:
            continue
        plan, log = loaded
        done, total = record.plan_progress(plan, log)
        counts = review_counts(plan, log)
        rows.append({"folder": path.name, "done": done, "total": total, "products": list(plan["products"]),
                     "rounds": plan["rounds"], "per_round": plan["per_round"], "to_review": counts["to_review"]})
    return rows


def review_counts(plan, log):
    """Episodes still to review are those of finished rounds; the round in progress is reviewed
    once it is complete."""
    slot = record.next_slot(plan, log)
    open_round = slot[:2] if slot else None
    episodes = log.data["episodes"]
    unreviewed = [e for e in episodes if not e["bad"] and not e.get("approved")]
    return {"episodes": len(episodes), "approved": sum(bool(e.get("approved")) and not e["bad"] for e in episodes),
            "flagged": sum(e["bad"] for e in episodes),
            "to_review": sum((e["shelf"], e["round"]) != open_round for e in unreviewed),
            "in_open_round": sum((e["shelf"], e["round"]) == open_round for e in unreviewed)}


def next_round(plan, log):
    """What the next run records: one round (base position) at one shelf, or None when complete."""
    slot = record.next_slot(plan, log)
    if slot is None:
        return None
    shelf, round_, _ = slot
    position = plan["positions"][round_ - 1]
    done, size = record.round_progress(plan, log, shelf, round_)
    items = dict(record.shelves(plan))[shelf]
    return {"shelf": shelf, "round": round_, "rounds": plan["rounds"], "position": position,
            "where": record.describe_position(position, plan["offset_cm"], plan["turn_deg"]),
            "done": done, "size": size,
            "items": [{"item": i, "instruction": plan["products"][i]["instruction"],
                       "done": min(plan["per_round"], log.good_count(i, round_)), "need": plan["per_round"]}
                      for i in items]}


def make_plan(products, rounds, per_round, offset_cm, turn_deg):
    """The same plan record.py makes for a new dataset, from the app's form."""
    catalog = json.loads(record.INSTRUCTIONS_FILE.read_text())
    products = [p for p in catalog if p in set(products or [])]  # catalog order
    if not products:
        raise ValueError("Choose at least one product.")
    rounds, per_round = int(rounds), int(per_round)
    if not 1 <= rounds <= len(record.POSITIONS_7):
        raise ValueError(f"Rounds must be 1 to {len(record.POSITIONS_7)}.")
    if not 1 <= per_round <= 100:
        raise ValueError("Episodes per product per round must be 1 to 100.")
    offset_cm, turn_deg = float(offset_cm), float(turn_deg)
    if not (0 < offset_cm <= 30 and 0 < turn_deg <= 30):
        raise ValueError("Base offset and turn must be between 0 and 30.")
    return {"products": {p: catalog[p] for p in products}, "rounds": rounds, "per_round": per_round,
            "positions": record.round_positions(rounds), "offset_cm": offset_cm, "turn_deg": turn_deg}


# ---------------------------------------------------------------------------
# Arm test: both arms answer, and every joint of the leader moves
# ---------------------------------------------------------------------------
class ArmTest:
    MOVE = 15.0  # a joint counts as moved once it has travelled this far (LeRobot units)

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.error = None
        self.follower = None  # None = not checked, else {"ok": bool, "message": str}
        self.reset()
        self._thread = None

    def reset(self):
        self.values = [None] * 6
        self.low = [None] * 6
        self.high = [None] * 6

    def start(self, env):
        if self.running:
            return
        self.error = None
        self.follower = None
        self.reset()
        self.running = True
        self._thread = threading.Thread(target=self._run, args=(env,), daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self, env):
        from lerobot.robots.so_follower import SO100Follower, SO100FollowerConfig
        from lerobot.teleoperators.so_leader import SO100Leader, SO100LeaderConfig

        # The follower only has to answer: its torque is left as it is, so it does not move or drop.
        try:
            follower = SO100Follower(SO100FollowerConfig(port=env["FOLLOWER_PORT"], id=record.FOLLOWER_ID))
            follower.bus.connect()
            try:
                follower.bus.sync_read("Present_Position")
            finally:
                follower.bus.disconnect(disable_torque=False)
            self.follower = {"ok": True, "message": "All six motors answer."}
        except Exception as e:
            self.follower = {"ok": False, "message": arm_error("follower", e)}

        try:
            leader = SO100Leader(SO100LeaderConfig(port=env["LEADER_PORT"], id=record.LEADER_ID))
            leader.connect(calibrate=False)
        except Exception as e:
            self.error = arm_error("leader", e)
            self.running = False
            return
        try:
            while self.running:
                action = leader.get_action()
                with self.lock:
                    for i, joint in enumerate(JOINTS):
                        v = float(action[f"{joint}.pos"])
                        self.values[i] = v
                        self.low[i] = v if self.low[i] is None else min(self.low[i], v)
                        self.high[i] = v if self.high[i] is None else max(self.high[i], v)
                time.sleep(0.05)
        except Exception as e:
            self.error = arm_error("leader", e)
        finally:
            try:
                leader.disconnect()
            except Exception:
                pass
            self.running = False

    def status(self):
        with self.lock:
            joints = []
            for i, name in enumerate(JOINTS):
                travel = float(self.high[i] - self.low[i]) if self.low[i] is not None else 0.0
                joints.append({"name": name, "value": self.values[i], "travel": round(travel, 1),
                               "moved": bool(travel >= self.MOVE)})
        leader_ok = all(j["moved"] for j in joints)
        return {"running": self.running, "error": self.error, "joints": joints, "follower": self.follower,
                "passed": leader_ok and bool(self.follower and self.follower["ok"])}


def arm_error(which, e):
    """A sentence the operator can act on, from LeRobot's motor bus errors."""
    text = str(e).strip()
    if "Could not connect on port" in text:
        cause = str(e.__cause__ or "")
        if "Permission" in cause:
            return f"No permission to open the {which} arm's port: ask the admin (dialout group)."
        return f"Cannot open the {which} arm's port: is its USB cable plugged in? Unplug it, plug it back, and test again."
    if "Missing motor IDs" in text:
        ids = [int(m) for m in re.findall(r"^\s*-\s*(\d+)\s", text.split("Missing motor IDs:")[1].split("\n\n")[0], re.M)]
        names = ", ".join(JOINTS[i - 1].replace("_", " ") for i in ids if 1 <= i <= 6) or "some motors"
        if len(ids) >= 6:
            return f"No motor of the {which} arm answers: is its power supply plugged in and switched on?"
        return f"These motors of the {which} arm do not answer: {names}. Check the cables between the motors."
    if "Failed to sync read" in text or "There is no status packet" in text:
        return f"The {which} arm stopped answering: check its power supply and cables, then test again."
    return f"{which.capitalize()} arm: {text.splitlines()[0][:300]}"


def explain_record_error(message, env):
    """record.py's error as a sentence for the operator (the full log stays below it)."""
    port = re.search(r"port '([^']+)'", message)
    which = None
    if port:
        which = {env.get("FOLLOWER_PORT"): "follower", env.get("LEADER_PORT"): "leader"}.get(port.group(1))
    if which and ("Could not connect on port" in message or "Missing motor IDs" in message):
        return arm_error(which, RuntimeError(message.split(": ", 1)[-1]))
    for name, var in CAMERAS.items():
        if env.get(var) and env[var] in message:
            return (f"The {'gripper' if name == record.GRIPPER_CAM else 'scene'} camera could not be opened: unplug it, "
                    "plug it back, and take test pictures on the Check step.")
    return message.strip().splitlines()[0] if message.strip() else "Unknown error"


# ---------------------------------------------------------------------------
# Camera test pictures (only while nothing records)
# ---------------------------------------------------------------------------
def camera_test(env):
    import cv2

    TEST_DIR.mkdir(parents=True, exist_ok=True)
    devices = {name: env.get(var, "") for name, var in CAMERAS.items()}
    results = {}
    try:  # the same fixed exposure and white balance as the recording
        record.apply_camera_settings({n: d for n, d in devices.items() if d and os.path.exists(d)})
        settings = None
    except SystemExit as e:
        settings = str(e.code)
    for name, device in devices.items():
        (TEST_DIR / f"{name}.jpg").unlink(missing_ok=True)
        if not device or not os.path.exists(device):
            results[name] = {"ok": False, "message": "Not plugged in (or not set in .env)."}
            continue
        cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
        try:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            frame = None
            for _ in range(15):  # a few frames so exposure settles
                ok, f = cap.read()
                if ok:
                    frame = f
        finally:
            cap.release()
        if frame is None:
            results[name] = {"ok": False, "message": "No picture: the camera may be in use by another program, "
                                                     "or its cable is loose. Unplug it, plug it back, try again."}
            continue
        cv2.imwrite(str(TEST_DIR / f"{name}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        results[name] = {"ok": True, "message": f"{frame.shape[1]}×{frame.shape[0]}"}
    return {"cameras": results, "settings_error": settings, "taken": time.time()}


# ---------------------------------------------------------------------------
# Jobs: a recording round (record.py --studio) or the removal of flagged episodes
# ---------------------------------------------------------------------------
class Job:
    def __init__(self, kind, cmd, env, on_event=None, on_exit=None):
        self.kind = kind
        self.status = "running"
        self.stopping = False
        self.returncode = None
        self.log = deque(maxlen=300)
        self.info = {"phase": "starting", "saved": 0, "discarded": 0}
        self.on_event = on_event
        self.on_exit = on_exit
        env = {**env, "PYTHONUNBUFFERED": "1"}
        # Own process group, so a forced stop reaches the video encoders too, but not this server.
        self.proc = subprocess.Popen(cmd, cwd=POLICY_DIR, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            if line.startswith("[STUDIO] "):
                try:
                    self._event(json.loads(line[len("[STUDIO] "):]))
                except Exception as e:
                    print(f"[ERROR] event {line!r}: {e}", flush=True)
                continue
            if line.startswith("Map:") or not line.strip():  # dataset progress bars
                continue
            self.log.append(line)
            print(f"[{self.kind}] {line}", flush=True)
        self.returncode = self.proc.wait()
        self.status = "finished" if self.returncode == 0 else "stopped" if self.stopping else "failed"
        if self.on_exit:
            self.on_exit(self)

    def _event(self, event):
        kind = event.pop("event")
        info = self.info
        if kind == "state":
            info.update(phase=event["state"], since=time.time())
        elif kind == "slot":
            info["slot"] = event
        elif kind == "saving":
            info.update(phase="saving", since=time.time())
        elif kind == "saved":
            info["saved"] += 1
            info["last_saved"] = event["index"]
        elif kind == "discarded":
            info["discarded"] += 1
        elif kind == "camera_rate":
            info.setdefault("rates", {})[event["camera"]] = event["fps"]
        elif kind == "round_done":
            info["round_done"] = True
            info["plan_complete"] = event["plan_complete"]
        elif kind == "error":
            info["error"] = event["message"]
        elif kind in ("connecting", "ready"):
            info["phase"] = kind
            info.update(event)
        if self.on_event:
            self.on_event(kind, event)

    def send(self, word):
        try:
            self.proc.stdin.write(word + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            pass

    def running(self):
        return self.proc.poll() is None

    def stop(self):
        """Quit like Q in the terminal (a save in progress finishes first); force it if it hangs."""
        if not self.running() or self.stopping:
            return
        self.stopping = True
        self.send("quit")

        def escalate():
            for sig, wait in ((None, 90), (signal.SIGINT, 20), (signal.SIGTERM, 10), (signal.SIGKILL, 0)):
                if sig is not None:
                    try:
                        os.killpg(self.proc.pid, sig)
                    except ProcessLookupError:
                        return
                try:
                    self.proc.wait(timeout=wait or 1)
                    return
                except subprocess.TimeoutExpired:
                    continue

        threading.Thread(target=escalate, daemon=True).start()

    def public(self):
        info = dict(self.info)
        if "since" in info:
            info["elapsed"] = round(time.time() - info["since"], 1)
        return {"kind": self.kind, "status": self.status, "stopping": self.stopping, "returncode": self.returncode,
                "info": info, "log": list(self.log)[-15:]}


# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------
class Studio:
    def __init__(self):
        self.lock = threading.Lock()
        try:
            self.state = json.loads(STATE_FILE.read_text())
        except (FileNotFoundError, ValueError):
            self.state = {}
        self.jobs = {}
        self.arm_test = ArmTest()
        self.arm_test_passed = False  # since the campaign was opened in this app session
        self.camera_result = None
        self.last_heartbeat = time.time()
        self.view = None
        self._open_view()

    def _save_state(self):
        write_json(STATE_FILE, self.state)

    def _open_view(self):
        folder = self.state.get("folder")
        try:
            ok = folder and load_campaign(folder)
        except ValueError:
            ok = False
        if ok:
            self.view = DatasetView(campaign_root(folder), CONFIG["long_episode_s"])
        else:
            self.state.pop("folder", None)
            self.view = None

    def recording(self):
        job = self.jobs.get("record")
        return bool(job and job.running())

    def busy(self):
        return any(j.running() for j in self.jobs.values())

    # --- campaign ---
    def open_campaign(self, folder):
        if self.busy():
            if folder == self.state.get("folder"):
                return  # the window was reopened during a round: nothing changes
            raise ValueError("Wait until the current round has finished.")
        if not load_campaign(folder):
            raise ValueError(f"{folder} is not a dataset recorded with a plan.")
        self.state["folder"] = folder
        self.state.pop("draft", None)
        self._campaign_changed()

    def new_campaign(self, body):
        if self.busy():
            raise ValueError("Wait until the current round has finished.")
        name = str(body.get("name") or "").strip() or record.next_version_name()
        if not NAME_RE.match(name):
            raise ValueError("The name may only use letters, numbers, '-' and '_'.")
        plan = make_plan(body.get("products"), body.get("rounds"), body.get("per_round"),
                         body.get("offset_cm", CONFIG["default_offset_cm"]), body.get("turn_deg", CONFIG["default_turn_deg"]))
        self.state.pop("folder", None)
        self.state["draft"] = {"name": name, "plan": plan}
        self._campaign_changed()

    def _campaign_changed(self):
        self._save_state()
        self._open_view()
        self.arm_test_passed = False
        self.camera_result = None

    def _current(self):
        """(folder or None, plan, log) of the open campaign, or None."""
        folder = self.state.get("folder")
        if folder:
            loaded = load_campaign(folder)
            if loaded:
                return folder, *loaded
        draft = self.state.get("draft")
        if draft:
            return None, draft["plan"], NoEpisodes()
        return None

    def snapshot(self):
        current = self._current()
        campaign = None
        upcoming = None
        if current:
            folder, plan, log = current
            done, total = record.plan_progress(plan, log)
            draft = self.state.get("draft")
            campaign = {"folder": folder, "name": folder or draft["name"], "draft": folder is None,
                        "products": [{"item": i, **plan["products"][i]} for i in plan["products"]],
                        "rounds": plan["rounds"], "per_round": plan["per_round"], "offset_cm": plan["offset_cm"],
                        "turn_deg": plan["turn_deg"], "done": done, "total": total, **review_counts(plan, log)}
            upcoming = next_round(plan, log)
        catalog = json.loads(record.INSTRUCTIONS_FILE.read_text())
        return {
            "campaign": campaign,
            "next": upcoming,
            "record": self.jobs["record"].public() if "record" in self.jobs else None,
            "finish": self.jobs["finish"].public() if "finish" in self.jobs else None,
            "arm_test_passed": self.arm_test_passed,
            "camera_test": self.camera_result,
            "campaigns": list_campaigns(),
            "catalog": [{"item": k, **v} for k, v in catalog.items()],
            "next_name": record.next_version_name(),
            "config": {k: CONFIG[k] for k in ("default_rounds", "default_per_round", "default_offset_cm",
                                              "default_turn_deg", "long_episode_s")},
            "max_rounds": len(record.POSITIONS_7),
        }

    # --- checks ---
    def checks(self):
        env = device_env()
        rows = []
        missing = [v for v in DEVICES if not env.get(v)]
        rows.append(("env", "Devices set in .env", not missing, ", ".join(missing) and f"missing: {', '.join(missing)}",
                     "Ask the admin to fill in the arm ports and cameras in the repo's .env file (DATA_COLLECTION.md, step 2)."))
        for var, label, thing in (("FOLLOWER_PORT", "Follower arm plugged in", "the follower arm (the one with the gripper camera)"),
                                  ("LEADER_PORT", "Leader arm plugged in", "the leader arm (the one you move by hand)")):
            port = env.get(var, "")
            exists = bool(port) and os.path.exists(port)
            access = exists and os.access(port, os.R_OK | os.W_OK)
            rows.append((var, label, access, "" if access else "no permission" if exists else "not found",
                         "Ask the admin to give this user access to the arm ports (dialout group)." if exists
                         else f"Plug the USB cable of {thing} into this computer and wait a few seconds."))
        for name, var in CAMERAS.items():
            device = env.get(var, "")
            ok = bool(device) and os.path.exists(device)
            label = "Gripper camera plugged in" if name == record.GRIPPER_CAM else "Scene camera plugged in"
            rows.append((var, label, ok, "" if ok else "not found",
                         "Plug the camera's USB cable into this computer and wait a few seconds."))
        calibration = Path(os.environ["HF_LEROBOT_CALIBRATION"])
        files = [calibration / "robots" / "so_follower" / f"{record.FOLLOWER_ID}.json",
                 calibration / "teleoperators" / "so_leader" / f"{record.LEADER_ID}.json"]
        missing_cal = [f.stem for f in files if not f.exists()]
        rows.append(("calibration", "Arms calibrated", not missing_cal, ", ".join(missing_cal),
                     "Ask the admin to calibrate the arms (ARM_SETUP.md)."))
        settings_ok = record.CAMERA_SETTINGS_FILE.exists() and shutil.which("v4l2-ctl") is not None
        rows.append(("camera_settings", "Camera settings ready", settings_ok,
                     "" if settings_ok else "camera_settings.json or v4l2-ctl missing",
                     "Ask the admin to set up the camera settings (CAMERA_SETUP.md, step 4)."))
        free_gb = shutil.disk_usage(POLICY_DIR).free / 1e9
        rows.append(("disk", "Enough disk space", free_gb >= CONFIG["min_free_disk_gb"], f"{free_gb:.0f} GB free",
                     f"Free up disk space: at least {CONFIG['min_free_disk_gb']} GB are needed."))
        ours = {str(j.proc.pid) for j in self.jobs.values() if j.running()}
        others = subprocess.run(["pgrep", "-f", r"python[0-9.]* .*record\.py( |$)|lerobot-(record|teleoperate|replay|calibrate)( |$)"],
                                capture_output=True, text=True).stdout.split()
        others = [p for p in others if p not in ours]
        rows.append(("others", "Arms free", not others, "",
                     "Another program is using the arms (a terminal recording or teleoperation): close it first."))
        return [{"id": i, "label": label, "ok": bool(ok), "detail": detail, "fix": fix}
                for i, label, ok, detail, fix in rows]

    def start_arm_test(self):
        if self.recording():
            raise ValueError("A round is running: the arms are in use.")
        env = device_env()
        if not env.get("FOLLOWER_PORT") or not env.get("LEADER_PORT"):
            raise ValueError("The arm ports are not set in .env.")
        self.arm_test.start(env)

    def stop_arm_test(self):
        if self.arm_test.status()["passed"]:
            self.arm_test_passed = True
        self.arm_test.stop()

    def test_cameras(self):
        if self.recording():
            raise ValueError("A round is running: the cameras are in use (see the live view).")
        self.camera_result = camera_test(device_env())
        return self.camera_result

    # --- record ---
    def start_round(self):
        current = self._current()
        if not current:
            raise ValueError("Choose a campaign first.")
        if self.busy():
            raise ValueError("A round is already running.")
        folder, plan, log = current
        counts = review_counts(plan, log)
        if counts["to_review"]:
            raise ValueError(f"Review the last round first ({counts['to_review']} episode(s) waiting).")
        if next_round(plan, log) is None:
            raise ValueError("The plan is complete: every episode is recorded.")
        if not self.arm_test_passed:
            raise ValueError("Do the arm test on the Check step first.")
        failed = [c["label"] for c in self.checks() if not c["ok"]]
        if failed:
            raise ValueError("Fix these checks first: " + ", ".join(failed))
        self.arm_test.stop()  # the round needs the ports

        cmd = [sys.executable, "-u", str(RECORD_SCRIPT), "--studio", "--preview_dir", str(LIVE_DIR),
               "--note", "app"]
        if folder:
            cmd += ["--resume", folder]
        else:
            draft = self.state["draft"]
            cmd += ["--name", draft["name"], "--products", ",".join(plan["products"]),
                    "--rounds", str(plan["rounds"]), "--per_round", str(plan["per_round"]),
                    "--offset_cm", str(plan["offset_cm"]), "--turn_deg", str(plan["turn_deg"])]
        for name in CAMERAS:
            (LIVE_DIR / f"{name}.jpg").unlink(missing_ok=True)

        env = device_env()

        def on_event(kind, event):
            if kind == "error":
                self.jobs["record"].info["error"] = explain_record_error(event["message"], env)
            if kind == "ready" and "draft" in self.state:  # the new dataset exists now
                with self.lock:
                    self.state["folder"] = event["dataset"]
                    self.state.pop("draft", None)
                    self._save_state()
                    self._open_view()

        self.jobs["record"] = Job("record", cmd, env, on_event)

    def record_command(self, word):
        job = self.jobs.get("record")
        if not job or not job.running():
            raise ValueError("No round is running.")
        allowed = {"waiting": {"start"}, "recording": {"save", "discard"}}.get(job.info.get("phase"), set())
        if word not in allowed:
            raise ValueError("Not now: wait until the app shows the next step.")
        job.send(word)

    def stop_round(self):
        job = self.jobs.get("record")
        if job:
            job.stop()

    # --- review ---
    def decide(self, index, decision, reason):
        if self.view is None:
            raise ValueError("Choose a campaign first.")
        if self.recording():
            raise ValueError("Wait until the round has finished: the recording is writing the episode log.")
        if decision not in ("approve", "flag", "clear"):
            raise ValueError("Unknown decision.")
        self.view.decide(int(index), decision, reason)

    def remove_flagged(self):
        folder = self.state.get("folder")
        if not folder or self.busy():
            raise ValueError("Wait until nothing is running.")
        plan, log = load_campaign(folder)
        counts = review_counts(plan, log)
        if not counts["flagged"]:
            raise ValueError("No episode is flagged.")
        if counts["to_review"]:
            raise ValueError("Review every episode first.")
        self.jobs["finish"] = Job("finish", [sys.executable, "-u", str(REMOVE_SCRIPT), folder, "--yes"], device_env())


STUDIO = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep the log quiet; errors are printed separately
        pass

    def _json(self, data, status=HTTPStatus.OK):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message):
        self._json({"error": message}, status)

    def _file(self, path: Path, content_type, cache=True):
        """Send a file, honouring a byte Range request (needed for seeking in videos)."""
        size = path.stat().st_size
        start, end = 0, size - 1
        match = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
        if match and (match.group(1) or match.group(2)):
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else end
            else:  # suffix range: the last N bytes
                start = max(size - int(match.group(2)), 0)
            end = min(end, size - 1)
            if start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if not cache:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(1 << 16, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):  # the window seeked elsewhere
                    return
                remaining -= len(chunk)

    def _static(self, name):
        if not STATIC_RE.match(name) or not (WEB_DIR / name).is_file():
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        types = {"html": "text/html; charset=utf-8", "css": "text/css; charset=utf-8",
                 "js": "text/javascript; charset=utf-8", "svg": "image/svg+xml", "jpg": "image/jpeg", "png": "image/png"}
        self._file(WEB_DIR / name, types[name.rsplit(".", 1)[1]], cache=False)

    def _picture(self, folder, name):
        """A live or test camera picture; 404 when there is none (yet)."""
        if f"{name}.jpg" not in {f"{c}.jpg" for c in CAMERAS}:
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        path = folder / f"{name}.jpg"
        if not path.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "no picture")
        self._file(path, "image/jpeg", cache=False)

    def do_GET(self):
        path = urlparse(self.path).path.strip("/")
        parts = [unquote(p) for p in path.split("/") if p]
        try:
            if not parts:
                return self._static("index.html")
            if parts[0] == "static" and len(parts) == 2:
                return self._static(parts[1])
            if path == "api/ping":
                return self._json({"ok": True})
            if path == "api/studio":
                return self._json(STUDIO.snapshot())
            if path == "api/checks":
                return self._json({"checks": STUDIO.checks()})
            if path == "api/arms":
                return self._json(STUDIO.arm_test.status())
            if len(parts) == 2 and parts[0] == "live":
                return self._picture(LIVE_DIR, parts[1].removesuffix(".jpg"))
            if len(parts) == 2 and parts[0] == "camera_test":
                return self._picture(TEST_DIR, parts[1].removesuffix(".jpg"))
            if path == "api/live":  # how old each live picture is: a stopped camera shows as stopped
                ages = {}
                for name in CAMERAS:
                    p = LIVE_DIR / f"{name}.jpg"
                    ages[name] = round(time.time() - p.stat().st_mtime, 1) if p.exists() else None
                return self._json(ages)
            view = STUDIO.view
            if parts[0] in ("video",) or path == "api/episodes" or parts[:2] == ["api", "joints"]:
                if view is None:
                    return self._error(HTTPStatus.CONFLICT, "No recorded dataset is open yet.")
            if path == "api/episodes":
                return self._json({"episodes": view.list()})
            if len(parts) == 3 and parts[:2] == ["api", "joints"]:
                return self._json(view.joints(int(parts[2])))
            if len(parts) == 4 and parts[0] == "video":
                return self._file(view.video_path(parts[1], parts[2], parts[3]), "video/mp4")
            self._error(HTTPStatus.NOT_FOUND, "not found")
        except (ValueError, FileNotFoundError) as e:
            self._error(HTTPStatus.NOT_FOUND, str(e))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            print(f"[ERROR] GET {self.path}: {e}", flush=True)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    def do_POST(self):
        path = urlparse(self.path).path.strip("/")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if path == "api/heartbeat":
                STUDIO.last_heartbeat = time.time()
                return self._json({"ok": True})
            if path == "api/record/cmd":  # Space / N / X: quick, no need to wait for the app lock
                STUDIO.record_command(str(body.get("cmd")))
                return self._json({"ok": True})
            if path == "api/cameras/test":  # takes a few seconds
                with STUDIO.lock:
                    return self._json(STUDIO.test_cameras())
            with STUDIO.lock:
                if path == "api/campaign/open":
                    STUDIO.open_campaign(body.get("folder"))
                elif path == "api/campaign/new":
                    STUDIO.new_campaign(body)
                elif path == "api/arms/start":
                    STUDIO.start_arm_test()
                elif path == "api/arms/stop":
                    STUDIO.stop_arm_test()
                elif path == "api/record/start":
                    if STUDIO.arm_test.status()["passed"]:
                        STUDIO.arm_test_passed = True
                    STUDIO.start_round()
                elif path == "api/record/stop":
                    STUDIO.stop_round()
                elif path == "api/review":
                    STUDIO.decide(body["index"], body["decision"], body.get("reason", ""))
                elif path == "api/remove_flagged":
                    STUDIO.remove_flagged()
                else:
                    return self._error(HTTPStatus.NOT_FOUND, "not found")
            self._json({"ok": True})
        except (ValueError, KeyError) as e:
            self._error(HTTPStatus.BAD_REQUEST, str(e))
        except Exception as e:
            print(f"[ERROR] POST {self.path}: {e}", flush=True)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))


def watchdog(server):
    """Stop the server once the window has been closed for a while and nothing is running."""
    while True:
        time.sleep(15)
        idle = time.time() - STUDIO.last_heartbeat
        if idle > IDLE_EXIT_S and not STUDIO.busy() and not STUDIO.arm_test.running:
            print("[INFO] No window open for a while: stopping Store Picker Bot.", flush=True)
            server.shutdown()
            return


def main():
    global STUDIO
    STUDIO = Studio()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", CONFIG["port"]), Handler)
    except OSError as e:
        raise SystemExit(f"Port {CONFIG['port']} is in use: Store Picker Bot may already be running ({e}).") from None
    threading.Thread(target=watchdog, args=(server,), daemon=True).start()
    print(f"Store Picker Bot on http://127.0.0.1:{CONFIG['port']}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STUDIO.arm_test.stop()
        for job in STUDIO.jobs.values():
            job.stop()


if __name__ == "__main__":
    main()
