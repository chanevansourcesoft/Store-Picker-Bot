"""
Record teleoperated demonstrations on the real SO-100 arms, following a recording plan.

A plan is: which products, how many rounds, and how many episodes per product per round.
A round is one base position in front of the shelf. The robot stops a little differently
every time it drives to a shelf, so each round puts the base at a different small offset
(the script says which), and every product on that shelf is recorded there. Order:
shelf -> round -> product, so the base moves once per round.

LeRobot does the recording (same loop, units and dataset format as `lerobot-record`).
Keys (read from the terminal), like the sim teleop of the SO-101 project:

    Space  start recording an episode                 (while waiting)
    N      save the episode                           (while recording)
    X      cancel the episode, nothing is saved       (while recording)
    B      mark the last saved episode bad            (while waiting; it is recorded again)
    Q      quit; an unsaved episode is dropped        (Ctrl+C does the same)

Between episodes nothing is recorded, but the follower still copies the leader, so you
can put the item back and move the arm to its rest pose.

The GR00T instruction of each product comes from policy/instructions.json. The plan
(with each product's instruction) is saved in the dataset's meta/plan.json, and every saved
episode is listed in meta/episode_log.json with its round and product. --resume continues
the plan where it stopped. Episodes marked bad are removed later with remove_bad_episodes.py.

A new dataset is called dataset_v<N>_<DDMMYYYY_HHMMSS> (N = next free version), or
<name>_<DDMMYYYY_HHMMSS> with --name.

The Store Picker Bot app (policy/studio/) runs this script with --studio: commands come as
lines on stdin (start, save, discard, bad, quit) instead of keys, events go to stdout as
"[STUDIO] {json}" lines, one run records one round (shelf + base position) and then waits
for quit, and --preview_dir gets small camera snapshots for the app's live view.

Usage (from policy/):
    docker compose run --rm policy python3 data_collection/record.py     # Jetson (Docker)
    python data_collection/record.py                                     # PC, after `source env.sh`
    python data_collection/record.py --resume dataset_v1_12102026_153045

Guide: policy/docs/DATA_COLLECTION.md
"""

import argparse
import json
import math
import os
import re
import select
import _thread
import shutil
import subprocess
import sys
import termios
import threading
import time
import tty
from datetime import datetime
from pathlib import Path

# Keep the terminal readable: no video-encoder banner or progress bars at every save.
os.environ.setdefault("SVT_LOG", "2")  # SVT-AV1 encoder: warnings and errors only
os.environ.setdefault("HF_DATASETS_DISABLE_PROGRESS_BARS", "1")
# Arm calibration from policy/calibration/ (also set by compose.yaml and env.sh).
os.environ.setdefault("HF_LEROBOT_CALIBRATION", str(Path(__file__).resolve().parent.parent / "calibration"))

from lerobot.cameras.opencv import OpenCVCamera, OpenCVCameraConfig
from lerobot.common.control_utils import sanity_check_dataset_robot_compatibility
from lerobot.datasets import (
    LeRobotDataset,
    VideoEncodingManager,
    aggregate_pipeline_dataset_features,
    create_initial_features,
)
from lerobot.processor import make_default_processors
from lerobot.robots import make_robot_from_config
from lerobot.robots.so_follower import SO100FollowerConfig
from lerobot.scripts.lerobot_record import record_loop
from lerobot.teleoperators import make_teleoperator_from_config
from lerobot.teleoperators.so_leader import SO100LeaderConfig
from lerobot.utils.feature_utils import combine_feature_dicts
from lerobot.utils.utils import init_logging

HERE = Path(__file__).resolve().parent
DATASET_DIR = HERE / "dataset"
# Item id (as in webapp/app/inventory.json) -> shelf and GR00T instruction. Shared with the robot at run time.
INSTRUCTIONS_FILE = HERE.parent / "instructions.json"
# Fixed camera controls (v4l2-ctl names), applied at every start. See docs/CAMERA_SETUP.md.
CAMERA_SETTINGS_FILE = HERE / "camera_settings.json"
PLAN_FILE = "plan.json"  # in the dataset's meta/
LOG_FILE = "episode_log.json"  # in the dataset's meta/

# Fixed for the whole project: training and the pick service rely on these names.
FOLLOWER_ID = "picker_follower"
LEADER_ID = "picker_leader"
GRIPPER_CAM = "gripper_cam"
SCENE_CAM = "scene_cam"

# Base position of each round, seen from behind the robot facing the shelf.
# Up to 5 rounds the turns are combined with closer/further; 6 or 7 rounds give them their own round.
POSITIONS_5 = ["centre", "left", "right", "closer, turned left", "further, turned right"]
POSITIONS_7 = ["centre", "left", "right", "closer", "further", "turned left", "turned right"]

STUDIO = False  # set by --studio: run by the Store Picker Bot app


def emit(event, **fields):
    """An event for the Store Picker Bot app (nothing in the terminal)."""
    if STUDIO:
        print("[STUDIO] " + json.dumps({"event": event, **fields}), flush=True)


def round_positions(rounds):
    if rounds <= len(POSITIONS_5):
        return POSITIONS_5[:rounds]
    return POSITIONS_7[:rounds]


def describe_position(position, offset_cm, turn_deg):
    if position == "centre":
        return "at the normal stop point"
    words = {"left": f"{offset_cm:g} cm left", "right": f"{offset_cm:g} cm right",
             "closer": f"{offset_cm:g} cm closer to the shelf", "further": f"{offset_cm:g} cm further from the shelf",
             "turned left": f"turned {turn_deg:g} deg left", "turned right": f"turned {turn_deg:g} deg right"}
    return ", ".join(words[part] for part in position.split(", ")) + " of the normal stop point"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Record SO-100 demonstrations with LeRobot, following a plan of products and rounds.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--name", help="Name for a new dataset (letters, digits, - and _); "
                                       "default: dataset_v<next version>")
    target.add_argument("--resume", help="Existing dataset folder in dataset/ to continue")
    plan = parser.add_argument_group("plan of a new dataset (asked in the terminal when not given)")
    plan.add_argument("--products", help="Comma-separated item ids from instructions.json, or 'all'")
    plan.add_argument("--rounds", type=int, help=f"Base positions per shelf (1-{len(POSITIONS_7)})")
    plan.add_argument("--per_round", type=int, help="Episodes per product per round")
    plan.add_argument("--offset_cm", type=float, default=3.0, help="Base offset of the left/right/closer/further rounds")
    plan.add_argument("--turn_deg", type=float, default=3.0, help="Base turn of the turned rounds")
    parser.add_argument("--note", default="", help="Note for this session, saved in the episode log")
    parser.add_argument("--follower_port", default=os.environ.get("FOLLOWER_PORT"),
                        help="Follower serial port (default: FOLLOWER_PORT from .env)")
    parser.add_argument("--leader_port", default=os.environ.get("LEADER_PORT"),
                        help="Leader serial port (default: LEADER_PORT from .env)")
    parser.add_argument("--gripper_cam", default=os.environ.get("GRIPPER_CAM"),
                        help="Gripper camera device (default: GRIPPER_CAM from .env)")
    parser.add_argument("--scene_cam", default=os.environ.get("SCENE_CAM"),
                        help="Scene camera device (default: SCENE_CAM from .env)")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fourcc", default=None, help="Camera format, e.g. MJPG (see CAMERA_SETUP.md)")
    parser.add_argument("--warn_after_s", type=float, default=60.0,
                        help="Show a warning when an episode runs longer than this")
    parser.add_argument("--streaming_encoding", action="store_true",
                        help="Encode video while recording (faster save; uses more CPU during the episode)")
    parser.add_argument("--studio", action="store_true",
                        help="Run by the Store Picker Bot app: commands on stdin, one round per run")
    parser.add_argument("--preview_dir", help="With --studio: folder for the live camera snapshots")
    args = parser.parse_args()

    missing = [flag for flag in ("follower_port", "leader_port", "gripper_cam", "scene_cam")
               if not getattr(args, flag)]
    if missing:
        parser.error("missing " + ", ".join(f"--{m}" for m in missing)
                     + " (pass them or set them in the repo root .env)")
    if args.name is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", args.name):
        parser.error("--name may only contain letters, digits, '-' and '_'")
    if args.resume and (args.products or args.rounds or args.per_round):
        parser.error("--products, --rounds and --per_round are for a new dataset; --resume uses its saved plan")
    if args.studio and not args.resume and None in (args.products, args.rounds, args.per_round):
        parser.error("--studio needs --products, --rounds and --per_round for a new dataset (nothing is asked)")
    return args


# ---------------------------------------------------------------------------------------------
# Plan and episode log


def next_version_name():
    """dataset_v<N> with N one higher than the highest dataset_v<N>_... folder in dataset/."""
    versions = [int(m.group(1)) for p in DATASET_DIR.glob("dataset_v*")
                if (m := re.match(r"dataset_v(\d+)_", p.name))]
    return f"dataset_v{max(versions, default=0) + 1}"


def ask(question, default):
    answer = input(f"{question} [{default}]: ").strip()
    return answer or str(default)


def make_plan(args):
    """Products, rounds and episodes per round for a new dataset, from the options or asked."""
    catalog = json.loads(INSTRUCTIONS_FILE.read_text())
    ids = list(catalog)
    if args.products is None:
        print("Products (from instructions.json):")
        for i, item in enumerate(ids, 1):
            print(f"  {i}) {item:<14} {catalog[item]['shelf']:<10} \"{catalog[item]['instruction']}\"")
        args.products = ask("Products to record (numbers or ids, comma-separated, or all)", "all")
    if args.products.strip().lower() == "all":
        products = ids
    else:
        products = []
        for part in args.products.split(","):
            part = part.strip()
            item = ids[int(part) - 1] if part.isdigit() and 1 <= int(part) <= len(ids) else part
            if item not in catalog:
                sys.exit(f"Unknown product '{part}'. Products in {INSTRUCTIONS_FILE}: {', '.join(ids)}")
            if item not in products:
                products.append(item)
    if args.rounds is None:
        args.rounds = int(ask(f"Rounds (base positions per shelf, 1-{len(POSITIONS_7)})", 5))
    if not 1 <= args.rounds <= len(POSITIONS_7):
        sys.exit(f"Rounds must be 1 to {len(POSITIONS_7)}.")
    if args.per_round is None:
        args.per_round = int(ask("Episodes per product per round", 10))
    if args.per_round < 1:
        sys.exit("Episodes per round must be at least 1.")

    plan = {
        "products": {item: catalog[item] for item in products},
        "rounds": args.rounds,
        "per_round": args.per_round,
        "positions": round_positions(args.rounds),
        "offset_cm": args.offset_cm,
        "turn_deg": args.turn_deg,
    }
    total = len(products) * args.rounds * args.per_round
    print(f"\nPlan: {len(products)} product(s) x {args.rounds} round(s) x {args.per_round} episodes = {total} episodes")
    for shelf, items in shelves(plan):
        print(f"  {shelf}: {', '.join(items)}")
    if not STUDIO and ask("Start?", "Y").lower() not in ("y", "yes"):
        sys.exit("Nothing recorded.")
    return plan


def check_plan_instructions(plan):
    """Refuse to continue when instructions.json changed a planned product's text since the plan was made."""
    catalog = json.loads(INSTRUCTIONS_FILE.read_text())
    changed = [item for item, entry in plan["products"].items()
               if catalog.get(item, {}).get("instruction") != entry["instruction"]]
    if changed:
        sys.exit("instructions.json changed the instruction of " + ", ".join(changed) + " since this dataset was "
                 "started.\nOne dataset must use one text per product: put the old text back, or start a new dataset.")


def shelves(plan):
    """[(shelf, [items...]), ...] in the order the products were chosen."""
    order = {}
    for item, entry in plan["products"].items():
        order.setdefault(entry["shelf"], []).append(item)
    return list(order.items())


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


class EpisodeLog:
    """meta/episode_log.json: sessions, and per saved episode its round, product and bad flag."""

    def __init__(self, root):
        self.path = root / "meta" / LOG_FILE
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {"sessions": [], "episodes": []}

    def start_session(self, note):
        self.data["sessions"].append({"started": datetime.now().isoformat(timespec="seconds"), "note": note})
        self.save()

    def add(self, episode_index, item, shelf, round_, position):
        self.data["episodes"].append({
            "episode_index": episode_index, "item": item, "shelf": shelf, "round": round_,
            "position": position, "session": len(self.data["sessions"]) - 1, "bad": False,
        })
        self.save()

    def mark_last_bad(self):
        """Mark the newest episode bad. Returns its entry, or None when there is none to mark."""
        if not self.data["episodes"]:
            return None
        last = max(self.data["episodes"], key=lambda e: e["episode_index"])
        if last["bad"]:
            return None
        last["bad"] = True
        self.save()
        return last

    def good_count(self, item, round_):
        return sum(1 for e in self.data["episodes"]
                   if e["item"] == item and e["round"] == round_ and not e["bad"])

    def bad_count(self):
        return sum(1 for e in self.data["episodes"] if e["bad"])

    def save(self):
        write_json(self.path, self.data)


def next_slot(plan, log):
    """The (shelf, round, item) to record next, or None when the plan is complete."""
    for shelf, items in shelves(plan):
        for round_ in range(1, plan["rounds"] + 1):
            for item in items:
                if log.good_count(item, round_) < plan["per_round"]:
                    return shelf, round_, item
    return None


def plan_progress(plan, log):
    done = sum(min(plan["per_round"], log.good_count(item, r))
               for item in plan["products"] for r in range(1, plan["rounds"] + 1))
    return done, len(plan["products"]) * plan["rounds"] * plan["per_round"]


def round_progress(plan, log, shelf, round_):
    """Good episodes and episodes needed in one round (one base position at one shelf)."""
    items = dict(shelves(plan))[shelf]
    done = sum(min(plan["per_round"], log.good_count(item, round_)) for item in items)
    return done, len(items) * plan["per_round"]


# ---------------------------------------------------------------------------------------------
# Hardware


def camera_config(name, device, args):
    """Camera config at exactly --fps if the camera offers it, otherwise at the camera's own, slightly
    higher rate (e.g. 30.5 fps). LeRobot only accepts an exact fps match, but the recording loop takes
    the newest frame --fps times per second either way, so the dataset is the same."""
    index_or_path = int(device) if device.isdigit() else Path(device)
    config = OpenCVCameraConfig(
        index_or_path=index_or_path, fps=args.fps, width=args.width, height=args.height, fourcc=args.fourcc
    )
    probe = OpenCVCamera(config)
    try:
        probe.connect(warmup=False)
        return config
    except RuntimeError as e:
        if "failed to set fps" not in str(e):
            raise
    finally:
        if probe.is_connected:
            probe.disconnect()
    config.fps = None  # ask the camera for its own rate at this size and format
    probe = OpenCVCamera(config)
    probe.connect(warmup=False)
    own_fps = probe.fps
    probe.disconnect()
    config.fps = own_fps  # a robot needs a set fps; this one the camera accepts exactly
    if own_fps < args.fps:
        sys.exit(f"{name} ({device}) runs at {own_fps:g} fps at {args.width}x{args.height}, slower than "
                 f"--fps {args.fps}: frames would repeat. Choose another --fourcc or camera (see CAMERA_SETUP.md).")
    print(f"{name} has no exact {args.fps} fps mode: it runs at {own_fps:g} fps, and {args.fps} frames per second "
          "are saved from it.")
    return config


def check_camera_rates(robot, fps, seconds=1.5):
    """Count the new frames each camera delivers. A camera that is slower than --fps (for example
    because its exposure time is too long) makes LeRobot save the same frame several times, silently."""
    import numpy as np

    slow = []
    for name, cam in robot.cameras.items():
        previous, new, start = None, 0, time.monotonic()
        while time.monotonic() - start < seconds:
            frame = cam.read_latest()
            if previous is None or not np.array_equal(frame, previous):
                new += 1
            previous = frame
            time.sleep(1 / (2 * fps))
        rate = new / seconds
        print(f"{name}: {rate:.0f} new frames per second")
        emit("camera_rate", camera=name, fps=round(rate))
        if rate < 0.93 * fps:
            slow.append(f"{name} ({rate:.0f} fps)")
    if slow and STUDIO:
        sys.exit(f"Camera slower than {fps} fps: {', '.join(slow)}. Its frames would be saved twice. "
                 "Ask the admin to lower exposure_time_absolute in camera_settings.json (CAMERA_SETUP.md, step 4).")
    if slow:
        print(f"WARNING: slower than {fps} fps: {', '.join(slow)}. Its frames would be saved twice. Usually the "
              "exposure time is too long: lower exposure_time_absolute in camera_settings.json and raise gain "
              "instead (CAMERA_SETUP.md, step 4).")
        if input("Record anyway? [y/N] ").strip().lower() not in ("y", "yes"):
            sys.exit("Nothing recorded.")


def apply_camera_settings(devices):
    """Set the fixed camera controls from camera_settings.json with v4l2-ctl (they reset on unplug or reboot)."""
    if not CAMERA_SETTINGS_FILE.exists():
        print(f"WARNING: no {CAMERA_SETTINGS_FILE.name}: camera exposure, white balance and focus are automatic "
              "(see CAMERA_SETUP.md, step 4).")
        return
    if shutil.which("v4l2-ctl") is None:
        sys.exit("v4l2-ctl not found: it is needed to apply camera_settings.json.")
    settings = json.loads(CAMERA_SETTINGS_FILE.read_text())
    for name, device in devices.items():
        controls = settings.get(name)
        if not controls:
            print(f"WARNING: {CAMERA_SETTINGS_FILE.name} has no settings for {name}: its image settings are automatic.")
            continue
        # Order matters: auto modes must be off before their manual values are accepted.
        for control, value in controls.items():
            result = subprocess.run(["v4l2-ctl", "-d", device, f"--set-ctrl={control}={value}"],
                                    capture_output=True, text=True)
            if result.returncode != 0:
                sys.exit(f"Could not set {control}={value} on {name} ({device}): {result.stderr.strip()}\n"
                         f"Check the control names with: v4l2-ctl -d {device} --list-ctrls")
        print(f"Camera settings applied to {name}: " + ", ".join(f"{k}={v}" for k, v in controls.items()))


# ---------------------------------------------------------------------------------------------
# Keys and status line


class Control:
    """Commands for the recording loop, from the terminal keys (KeyControl) or from the app (StudioControl).

    A command ends the running record_loop by setting events["exit_early"]; the main thread
    then reads it with take(). Commands that do not fit the current state are ignored.
    Quitting lets the main thread finish what it is doing (also a save) first.
    """

    KEYS = {
        "waiting": {" ": "start", "b": "bad", "q": "quit"},
        "recording": {"n": "save", "x": "discard", "q": "quit"},
        "done": {"q": "quit"},  # app only: the round is complete, the follower still follows the leader
    }

    def __init__(self, events):
        self.events = events
        self.lock = threading.Lock()
        self.state = None
        self.command = None
        self.quit_requested = False
        self.where = ""
        self.started_at = 0.0
        self.running = True

    def set_state(self, state, where):
        with self.lock:
            self.state, self.where = state, where
            self.command = None
            self.started_at = time.monotonic()
        if self.quit_requested:
            self.events["exit_early"] = True

    def take(self):
        with self.lock:
            command, self.command, self.state = self.command, None, None
        return "quit" if self.quit_requested and command != "save" else command

    def press(self, key):
        with self.lock:
            command = self.KEYS.get(self.state, {}).get(key)
            if command and self.command is None:
                self.command = command
                self.events["exit_early"] = True

    def request_quit(self):
        self.quit_requested = True
        self.events["exit_early"] = True

    def close(self):
        self.running = False


class StudioControl(Control):
    """Commands from the app: one word per line on stdin. A closed stdin (the app stopped) quits.

    Reads the raw file descriptor, not sys.stdin: saving an episode forks video encoders, and a
    forked child closes sys.stdin, which would wait forever for this thread's read lock."""

    WORDS = {"start": " ", "save": "n", "discard": "x", "bad": "b"}

    def __init__(self, events):
        super().__init__(events)
        self.fd = sys.stdin.fileno()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        pending = b""
        while self.running:
            if not select.select([self.fd], [], [], 0.2)[0]:
                continue
            chunk = os.read(self.fd, 1024)
            if not chunk:
                break
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                word = line.decode(errors="ignore").strip().lower()
                if word == "quit":
                    self.request_quit()
                elif word in self.WORDS:
                    self.press(self.WORDS[word])
        self.request_quit()

    def set_state(self, state, where):
        super().set_state(state, where)
        emit("state", state=state)


class KeyControl(Control):
    """Reads single keys from the terminal in a background thread and shows a status line.

    Ctrl+C asks to quit like Q. The terminal does not turn Ctrl+C into a signal meanwhile, so
    no process is interrupted in the middle of writing an episode; a second Ctrl+C stops at once.
    """

    def __init__(self, events, warn_after_s):
        super().__init__(events)
        self.warn_after_s = warn_after_s
        self.fd = sys.stdin.fileno()
        self.old_term = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)  # one key at a time, no echo
        attrs = termios.tcgetattr(self.fd)
        attrs[3] &= ~termios.ISIG  # Ctrl+C arrives as a key ("\x03") instead of a signal
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _on_ctrl_c(self):
        if self.quit_requested:  # second Ctrl+C: stop right away
            _thread.interrupt_main()
            return
        self.request_quit()
        say("Ctrl+C: quitting after the current step (press Ctrl+C again to stop at once).")

    def _run(self):
        while self.running:
            ready, _, _ = select.select([sys.stdin], [], [], 0.2)
            if ready:
                key = os.read(self.fd, 1).decode(errors="ignore").lower()
                if key == "\x03":
                    self._on_ctrl_c()
                    continue
                self.press(key)
            self._show_status()

    def _show_status(self):
        with self.lock:
            state, where = self.state, self.where
            elapsed = time.monotonic() - self.started_at
        if state == "waiting":
            line = f"WAITING   {where} | Space = record, B = last one was bad, Q = quit"
        elif state == "recording":
            line = f"RECORDING {where} | {elapsed:5.1f} s | N = save, X = cancel, Q = quit"
            if elapsed > self.warn_after_s:
                line += "  << long episode: press N or X"
        else:
            line = "working..."
        sys.stdout.write("\r\033[K" + line)
        sys.stdout.flush()

    def close(self):
        super().close()
        self.thread.join(timeout=1)
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old_term)
        sys.stdout.write("\n")


class Preview:
    """App only: a small JPEG of each camera's newest frame, a few times per second, for the live
    view. It takes the frames the robot's cameras already capture, so nothing else opens them."""

    def __init__(self, cameras, folder, fps=4):
        self.cameras = cameras
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.period = 1 / fps
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        import cv2

        while self.running:
            for name, cam in self.cameras.items():
                try:
                    frame = cam.read_latest()
                except Exception:  # no new frame: the app shows the camera as stopped
                    continue
                height, width = frame.shape[:2]
                small = cv2.resize(frame, (320, round(320 * height / width)))
                ok, jpg = cv2.imencode(".jpg", cv2.cvtColor(small, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    tmp = self.folder / f"{name}.jpg.tmp"
                    tmp.write_bytes(jpg.tobytes())
                    tmp.replace(self.folder / f"{name}.jpg")
            time.sleep(self.period)

    def close(self):
        self.running = False
        self.thread.join(timeout=2)


def say(text):
    sys.stdout.write(text + "\n" if STUDIO else "\r\033[K" + text + "\n")
    sys.stdout.flush()


def announce(plan, slot, previous):
    """Tell the operator what changes for the next episode: shelf, base position, product."""
    shelf, round_, item = slot
    prev_shelf, prev_round, prev_item = previous or (None, None, None)
    if shelf != prev_shelf:
        say(f"\n=== {shelf}: drive the robot to {shelf} ===")
    if (shelf, round_) != (prev_shelf, prev_round):
        position = plan["positions"][round_ - 1]
        say(f"--- Round {round_}/{plan['rounds']} at {shelf}: put the base "
            f"{describe_position(position, plan['offset_cm'], plan['turn_deg'])} ({position}) ---")
    if slot != previous:
        say(f"Product: {item}  \"{plan['products'][item]['instruction']}\"  "
            f"({plan['per_round']} episodes this round)")


# ---------------------------------------------------------------------------------------------


def main():
    global STUDIO
    args = parse_args()
    STUDIO = args.studio
    init_logging()
    if not STUDIO and not sys.stdin.isatty():
        sys.exit("Run this in a terminal: the keys are read from it.")

    # Plan first, so typos are caught before the hardware is touched.
    if args.resume:
        root = DATASET_DIR / Path(args.resume).name
        if not (root / "meta" / "info.json").exists():
            sys.exit(f"{root} is not a dataset. Existing datasets: "
                     + ", ".join(sorted(p.name for p in DATASET_DIR.glob("*") if p.is_dir())))
        if not (root / "meta" / PLAN_FILE).exists():
            sys.exit(f"{root} has no {PLAN_FILE}: it was not recorded with a plan.")
        plan = json.loads((root / "meta" / PLAN_FILE).read_text())
        check_plan_instructions(plan)
    else:
        root = DATASET_DIR / f"{args.name or next_version_name()}_{datetime.now().strftime('%d%m%Y_%H%M%S')}"
        plan = make_plan(args)
    repo_id = f"{os.environ.get('HF_USER') or 'local'}/{root.name}"

    apply_camera_settings({GRIPPER_CAM: args.gripper_cam, SCENE_CAM: args.scene_cam})
    robot = make_robot_from_config(SO100FollowerConfig(
        port=args.follower_port,
        id=FOLLOWER_ID,
        cameras={GRIPPER_CAM: camera_config(GRIPPER_CAM, args.gripper_cam, args),
                 SCENE_CAM: camera_config(SCENE_CAM, args.scene_cam, args)},
    ))
    teleop = make_teleoperator_from_config(SO100LeaderConfig(port=args.leader_port, id=LEADER_ID))

    # Same features as lerobot-record: action = leader command, state = follower position.
    teleop_action_processor, robot_action_processor, robot_observation_processor = make_default_processors()
    features = combine_feature_dicts(
        aggregate_pipeline_dataset_features(
            pipeline=teleop_action_processor,
            initial_features=create_initial_features(action=robot.action_features),
            use_videos=True,
        ),
        aggregate_pipeline_dataset_features(
            pipeline=robot_observation_processor,
            initial_features=create_initial_features(observation=robot.observation_features),
            use_videos=True,
        ),
    )
    writer_kwargs = dict(
        image_writer_processes=0,
        image_writer_threads=4 * len(robot.cameras),
        streaming_encoding=args.streaming_encoding,
    )

    events = {"exit_early": False}
    loop_kwargs = dict(
        robot=robot,
        events=events,
        fps=args.fps,
        teleop_action_processor=teleop_action_processor,
        robot_action_processor=robot_action_processor,
        robot_observation_processor=robot_observation_processor,
        teleop=teleop,
        control_time_s=math.inf,  # episodes end only with a key
    )

    keys = None
    preview = None
    dataset = None
    log = None
    saved = 0
    try:
        # Leader first so the follower is not left idle (same order as lerobot-record).
        # The follower moves straight to the leader's pose: start with both arms alike.
        emit("connecting")
        teleop.connect()
        robot.connect()
        if STUDIO and args.preview_dir:
            preview = Preview(robot.cameras, args.preview_dir)
        check_camera_rates(robot, args.fps)

        # Open the dataset only once the hardware works, so a wrong port leaves no empty dataset behind.
        if args.resume:
            dataset = LeRobotDataset.resume(repo_id, root=root, **writer_kwargs)
            sanity_check_dataset_robot_compatibility(dataset, robot, args.fps, features)
        else:
            DATASET_DIR.mkdir(parents=True, exist_ok=True)
            dataset = LeRobotDataset.create(
                repo_id, args.fps, root=root, robot_type=robot.name, features=features, use_videos=True,
                **writer_kwargs,
            )
            write_json(root / "meta" / PLAN_FILE, plan)
        log = EpisodeLog(root)
        if len(log.data["episodes"]) != dataset.num_episodes:
            say(f"WARNING: {LOG_FILE} lists {len(log.data['episodes'])} episodes but the dataset has "
                f"{dataset.num_episodes}: the plan progress may be wrong.")
        log.start_session(args.note)
        done, total = plan_progress(plan, log)
        print(f"Dataset: {root} ({dataset.num_episodes} episodes saved, plan {done}/{total})")
        emit("ready", dataset=root.name, episodes=dataset.num_episodes, done=done, total=total)

        keys = StudioControl(events) if STUDIO else KeyControl(events, args.warn_after_s)
        previous = None
        studio_round = None  # app: the (shelf, round) of this run; the run ends once it is complete

        with VideoEncodingManager(dataset):
            while True:
                slot = next_slot(plan, log)
                if STUDIO:
                    studio_round = studio_round or (slot and slot[:2])
                    if slot is None or slot[:2] != studio_round:
                        # The arm keeps following the leader until the operator has put it at rest
                        # and the app sends quit: the motors switch off when this script ends.
                        emit("round_done", plan_complete=slot is None)
                        say("Round complete.")
                        keys.set_state("done", "")
                        record_loop(dataset=None, **loop_kwargs)
                        keys.take()
                        break
                if slot is None:
                    say("Plan complete: every product has all its episodes in every round.")
                    break
                shelf, round_, item = slot
                announce(plan, slot, previous)
                previous = slot
                loop_kwargs["single_task"] = plan["products"][item]["instruction"]
                done, total = plan_progress(plan, log)
                where = (f"{shelf} round {round_}/{plan['rounds']} | {item} "
                         f"{log.good_count(item, round_) + 1}/{plan['per_round']} | plan {done}/{total}")
                position = plan["positions"][round_ - 1]
                round_done, round_size = round_progress(plan, log, shelf, round_)
                emit("slot", shelf=shelf, round=round_, rounds=plan["rounds"], position=position,
                     where=describe_position(position, plan["offset_cm"], plan["turn_deg"]),
                     item=item, instruction=plan["products"][item]["instruction"],
                     count=log.good_count(item, round_) + 1, per_round=plan["per_round"],
                     round_done=round_done, round_size=round_size, done=done, total=total)

                keys.set_state("waiting", where)
                record_loop(dataset=None, **loop_kwargs)
                command = keys.take()
                if command == "quit":
                    break
                if command == "bad":
                    entry = log.mark_last_bad()
                    if entry is None:
                        say("No saved episode to mark (or it is already marked).")
                    else:
                        say(f"Episode {entry['episode_index']} ({entry['item']}, round {entry['round']}) marked bad: "
                            "it will be recorded again.")
                        emit("marked_bad", index=entry["episode_index"])
                    continue

                keys.set_state("recording", f"{item} round {round_}/{plan['rounds']}")
                record_loop(dataset=dataset, **loop_kwargs)
                command = keys.take()

                if command == "save" and dataset.has_pending_frames():
                    index = dataset.num_episodes
                    say(f"Saving episode {index}...")
                    emit("saving", index=index)
                    dataset.save_episode()
                    log.add(index, item, shelf, round_, plan["positions"][round_ - 1])
                    saved += 1
                    say(f"Saved episode {index} ({item}, round {round_}).")
                    emit("saved", index=index, item=item, round=round_)
                else:
                    dataset.clear_episode_buffer()
                    say(f"Episode {dataset.num_episodes} cancelled, nothing saved.")
                    emit("discarded")
                if command == "quit" or keys.quit_requested:
                    break
    finally:
        if keys is not None:
            keys.close()
        if preview is not None:
            preview.close()
        if robot.is_connected:
            robot.disconnect()
        if teleop.is_connected:
            teleop.disconnect()
        if dataset is not None and log is not None:
            done, total = plan_progress(plan, log)
            print(f"Saved {saved} episode(s) this run. Dataset: {root} ({dataset.num_episodes} episodes, "
                  f"plan {done}/{total}).")
            if log.bad_count():
                print(f"{log.bad_count()} episode(s) marked bad: remove them with "
                      f"python3 data_collection/remove_bad_episodes.py {root.name}")
            if done < total:
                print(f"Continue with: python3 data_collection/record.py --resume {root.name}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        if isinstance(e.code, str):
            emit("error", message=e.code)
        raise
    except Exception as e:
        emit("error", message=f"{type(e).__name__}: {e}")
        raise
