"""
Record teleoperated demonstrations on the real SO-100 arms, controlled with keys.

LeRobot does the recording (same loop, units and dataset format as `lerobot-record`).
This script only changes how episodes start and end, so it works like the sim teleop
of the SO-101 project:

    Space  start recording an episode      (while waiting)
    N      save the episode                (while recording)
    X      cancel the episode, nothing is saved (while recording)
    Q      quit; an unsaved episode is dropped

Between episodes nothing is recorded, but the follower still copies the leader, so you
can put the item back and move the arm to its rest pose. Keys are read from the terminal.

Each new dataset goes to dataset/<DDMMYYYY_HHMMSS>_<name>/ next to this script.
--resume <folder> adds episodes to an existing one.

The GR00T instruction comes from policy/instructions.json (--item <id>), so recording and
the robot at run time use exactly the same text. --task sets a custom text instead.

Usage (from policy/, on the Jetson):
    docker compose run --rm policy python3 data_collection/record.py --name soup_can --item soup_can
    docker compose run --rm policy python3 data_collection/record.py \
        --resume 12102026_153045_soup_can --item soup_can

Guide: policy/docs/DATA_COLLECTION.md
"""

import argparse
import json
import math
import os
import re
import select
import sys
import termios
import threading
import time
import tty
from datetime import datetime
from pathlib import Path

from lerobot.cameras.opencv import OpenCVCameraConfig
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

DATASET_DIR = Path(__file__).resolve().parent / "dataset"
# Item id (as in webapp/app/inventory.json) -> GR00T instruction. Shared with the robot at run time.
INSTRUCTIONS_FILE = Path(__file__).resolve().parent.parent / "instructions.json"

# Fixed for the whole project: training and the pick service rely on these names.
FOLLOWER_ID = "picker_follower"
LEADER_ID = "picker_leader"
GRIPPER_CAM = "gripper_cam"
SCENE_CAM = "scene_cam"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Record SO-100 demonstrations with LeRobot, controlled with keys.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--name", help="Name for a new dataset (letters, digits, - and _)")
    target.add_argument("--resume", help="Existing dataset folder in dataset/ to add episodes to")
    instruction = parser.add_mutually_exclusive_group(required=True)
    instruction.add_argument("--item",
                             help=f"Item id; its instruction is read from {INSTRUCTIONS_FILE.name}")
    instruction.add_argument("--task",
                             help="Custom instruction for every episode of this run (instead of --item)")
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
    args = parser.parse_args()

    missing = [flag for flag in ("follower_port", "leader_port", "gripper_cam", "scene_cam")
               if not getattr(args, flag)]
    if missing:
        parser.error("missing " + ", ".join(f"--{m}" for m in missing)
                     + " (pass them or set them in the repo root .env)")
    if args.name is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", args.name):
        parser.error("--name may only contain letters, digits, '-' and '_'")
    if args.item is not None:
        instructions = json.loads(INSTRUCTIONS_FILE.read_text())
        if args.item not in instructions:
            parser.error(f"unknown --item '{args.item}'. Items in {INSTRUCTIONS_FILE}: "
                         + ", ".join(instructions))
        args.task = instructions[args.item]
    return args


def camera_config(device, args):
    index_or_path = int(device) if device.isdigit() else Path(device)
    return OpenCVCameraConfig(
        index_or_path=index_or_path, fps=args.fps, width=args.width, height=args.height, fourcc=args.fourcc
    )


class KeyControl:
    """Reads single keys from the terminal in a background thread and shows a status line.

    A key ends the running record_loop by setting events["exit_early"]; the main thread
    then reads the command with take(). Keys that do not fit the current state are ignored.
    """

    KEYS = {
        "waiting": {" ": "start", "q": "quit"},
        "recording": {"n": "save", "x": "discard", "q": "quit"},
    }

    def __init__(self, events, warn_after_s):
        self.events = events
        self.warn_after_s = warn_after_s
        self.lock = threading.Lock()
        self.state = None
        self.command = None
        self.episode = 0
        self.saved = 0
        self.started_at = 0.0
        self.running = True
        self.fd = sys.stdin.fileno()
        self.old_term = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)  # one key at a time, no echo; Ctrl+C still works
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def set_state(self, state, episode, saved):
        with self.lock:
            self.state, self.episode, self.saved = state, episode, saved
            self.command = None
            self.started_at = time.monotonic()

    def take(self):
        with self.lock:
            command, self.command, self.state = self.command, None, None
            return command

    def _run(self):
        while self.running:
            ready, _, _ = select.select([sys.stdin], [], [], 0.2)
            if ready:
                key = os.read(self.fd, 1).decode(errors="ignore").lower()
                with self.lock:
                    command = self.KEYS.get(self.state, {}).get(key)
                    if command and self.command is None:
                        self.command = command
                        self.events["exit_early"] = True
            self._show_status()

    def _show_status(self):
        with self.lock:
            state, episode, saved = self.state, self.episode, self.saved
            elapsed = time.monotonic() - self.started_at
        if state == "waiting":
            line = f"WAITING   next episode {episode} | saved this run: {saved} | Space = record, Q = quit"
        elif state == "recording":
            line = f"RECORDING episode {episode} | {elapsed:5.1f} s | N = save, X = cancel, Q = quit"
            if elapsed > self.warn_after_s:
                line += "  << long episode: press N or X"
        else:
            line = "working..."
        sys.stdout.write("\r\033[K" + line)
        sys.stdout.flush()

    def close(self):
        self.running = False
        self.thread.join(timeout=1)
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old_term)
        sys.stdout.write("\n")


def say(text):
    sys.stdout.write("\r\033[K" + text + "\n")
    sys.stdout.flush()


def main():
    args = parse_args()
    init_logging()
    if not sys.stdin.isatty():
        sys.exit("Run this in a terminal: the keys are read from it.")

    robot = make_robot_from_config(SO100FollowerConfig(
        port=args.follower_port,
        id=FOLLOWER_ID,
        cameras={GRIPPER_CAM: camera_config(args.gripper_cam, args),
                 SCENE_CAM: camera_config(args.scene_cam, args)},
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

    if args.resume:
        root = DATASET_DIR / Path(args.resume).name
        if not (root / "meta" / "info.json").exists():
            sys.exit(f"{root} is not a dataset. Existing datasets: "
                     + ", ".join(sorted(p.name for p in DATASET_DIR.glob("*") if p.is_dir())))
    else:
        root = DATASET_DIR / f"{datetime.now().strftime('%d%m%Y_%H%M%S')}_{args.name}"
    repo_id = f"{os.environ.get('HF_USER') or 'local'}/{root.name}"
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
        single_task=args.task,
    )

    keys = None
    dataset = None
    saved = 0
    try:
        # Leader first so the follower is not left idle (same order as lerobot-record).
        # The follower moves straight to the leader's pose: start with both arms alike.
        teleop.connect()
        robot.connect()

        # Open the dataset only once the hardware works, so a wrong port leaves no empty dataset behind.
        if args.resume:
            dataset = LeRobotDataset.resume(repo_id, root=root, **writer_kwargs)
            sanity_check_dataset_robot_compatibility(dataset, robot, args.fps, features)
            print(f"Adding episodes to {root} ({dataset.num_episodes} already saved)")
        else:
            DATASET_DIR.mkdir(parents=True, exist_ok=True)
            dataset = LeRobotDataset.create(
                repo_id, args.fps, root=root, robot_type=robot.name, features=features, use_videos=True,
                **writer_kwargs,
            )
            print(f"New dataset: {root}")
        print(f"Task: {args.task}")

        keys = KeyControl(events, args.warn_after_s)

        with VideoEncodingManager(dataset):
            while True:
                keys.set_state("waiting", dataset.num_episodes, saved)
                record_loop(dataset=None, **loop_kwargs)
                if keys.take() == "quit":
                    break

                keys.set_state("recording", dataset.num_episodes, saved)
                record_loop(dataset=dataset, **loop_kwargs)
                command = keys.take()

                if command == "save" and dataset.has_pending_frames():
                    say(f"Saving episode {dataset.num_episodes}...")
                    dataset.save_episode()
                    saved += 1
                    say(f"Saved episode {dataset.num_episodes - 1}.")
                else:
                    dataset.clear_episode_buffer()
                    say(f"Episode {dataset.num_episodes} cancelled, nothing saved.")
                if command == "quit":
                    break
    finally:
        if keys is not None:
            keys.close()
        if robot.is_connected:
            robot.disconnect()
        if teleop.is_connected:
            teleop.disconnect()
        if dataset is not None:
            print(f"Saved {saved} episode(s) this run. Dataset: {root} ({dataset.num_episodes} episodes)")


if __name__ == "__main__":
    main()
