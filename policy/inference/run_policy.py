"""
Run a trained GR00T model on the real SO-100 arm: the model picks one product and puts it in the basket.

The same program runs on a PC (for testing) and on the Jetson; pc/run.sh and jetson/run.sh only
start it in the right environment. It opens the follower arm and both cameras exactly like
record.py (same ports, camera settings and size), so the model sees what it saw in training.
The leader arm is not used.

Each episode: the model predicts 16 actions at a time (one chunk, about half a second at 30 fps)
from the two camera images, the arm position and the product's instruction. While the arm plays a
chunk, the model already predicts the next one from a newer picture, so the arm does not stop
between chunks. Keys (read from the terminal):

    Space  start an episode (the arm must be in its rest pose) / stop the arm where it is
    R      move the arm slowly back to where the episode started
    Q      quit: the arm first goes back to where the episode started, then its motors switch off

An episode also stops after --max_s seconds. --dry_run runs the model but never moves the arm:
use it first with every new model.

Usage (from any folder; --model is a folder name in policy/inference/models/, or a path):
    policy/inference/pc/run.sh --model <name> --product mentos --dry_run       # PC (conda env active)
    policy/inference/jetson/run.sh --model <name> --product mentos --dry_run   # Jetson (Docker)

Guide: policy/docs/INFERENCE.md
"""

import argparse
import dataclasses
import json
import math
import os
import select
import sys
import tempfile
import termios
import time
import tty
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "data_collection"))
import record  # noqa: E402  (same arm and camera setup as the recordings)

import numpy as np  # noqa: E402
import torch  # noqa: E402
from lerobot.configs import PreTrainedConfig  # noqa: E402
from lerobot.policies.factory import make_pre_post_processors  # noqa: E402
from lerobot.policies.groot.configuration_groot import GrootConfig  # noqa: E402
from lerobot.policies.groot.modeling_groot import GrootPolicy  # noqa: E402
from lerobot.policies.utils import prepare_observation_for_inference  # noqa: E402
from lerobot.robots import make_robot_from_config  # noqa: E402
from lerobot.robots.so_follower import SO100FollowerConfig  # noqa: E402
from lerobot.utils.robot_utils import precise_sleep  # noqa: E402

MODELS_DIR = HERE / "models"
TRAINING_OUTPUTS = HERE.parent / "training" / "outputs"
RETURN_SPEED = 30.0  # joint units per second when moving back to the start pose


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run a trained GR00T model on the real SO-100 arm.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--model", required=True,
                        help="Training run folder, checkpoint folder or pretrained_model folder; a bare name is "
                             "looked up in inference/models/ and training/outputs/")
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument("--product", help="Item id from instructions.json (its instruction is sent to the model)")
    what.add_argument("--task", help="Instruction text, instead of --product")
    parser.add_argument("--dry_run", action="store_true", help="Run the model but never move the arm")
    parser.add_argument("--max_s", type=float, default=40.0, help="Stop an episode after this many seconds")
    parser.add_argument("--execute_steps", type=int, default=None,
                        help="Play only the first N actions of each chunk before the model looks again "
                             "(default: the whole chunk)")
    parser.add_argument("--max_step", type=float, default=30.0,
                        help="Largest move per command, in joint units from where the joint is "
                             "(recordings stay under 30; 0 = no limit)")
    parser.add_argument("--follower_port", default=os.environ.get("FOLLOWER_PORT"),
                        help="Follower serial port (default: FOLLOWER_PORT from .env)")
    parser.add_argument("--gripper_cam", default=os.environ.get("GRIPPER_CAM"),
                        help="Gripper camera device (default: GRIPPER_CAM from .env)")
    parser.add_argument("--scene_cam", default=os.environ.get("SCENE_CAM"),
                        help="Scene camera device (default: SCENE_CAM from .env)")
    parser.add_argument("--fps", type=int, default=30, help="Same as the recordings")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fourcc", default=None, help="Camera format, e.g. MJPG (same as when recording)")
    args = parser.parse_args()

    missing = [flag for flag in ("follower_port", "gripper_cam", "scene_cam") if not getattr(args, flag)]
    if missing:
        parser.error("missing " + ", ".join(f"--{m}" for m in missing)
                     + " (pass them or set them in the repo root .env)")
    return args


def find_model(name):
    """The pretrained_model folder for a run folder, a checkpoint folder or the folder itself."""
    path = Path(name).expanduser()
    if not path.exists():
        for base in (MODELS_DIR, TRAINING_OUTPUTS):
            if (base / name).exists():
                path = base / name
                break
        else:
            sys.exit(f"Model not found: {name} (not in the current folder, {MODELS_DIR} or {TRAINING_OUTPUTS})")
    for candidate in (path, path / "pretrained_model", path / "checkpoints" / "last" / "pretrained_model"):
        if (candidate / "config.json").exists() and (candidate / "model.safetensors").exists():
            return candidate.resolve()
    sys.exit(f"{path} has no trained model (config.json and model.safetensors, in it or in "
             "pretrained_model/ or checkpoints/last/pretrained_model/)")


def load_config(path, device):
    """The model's config, readable by the installed LeRobot, with bf16 weights.

    LeRobot 0.6.2 saves the weight precision as "dtype", which 0.6.1 (the Jetson's version) does not
    know; 0.6.1 calls it model_params_fp32. bf16 weights take half the memory, and the model computes
    in bf16 anyway.
    """
    data = json.loads((path / "config.json").read_text())
    fields = {f.name for f in dataclasses.fields(GrootConfig)}
    if "dtype" in fields:
        data["dtype"] = "bfloat16"
        data.pop("model_params_fp32", None)
    else:
        data.pop("dtype", None)
        data["model_params_fp32"] = False
    data["device"] = device
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "config.json").write_text(json.dumps(data))
        return PreTrainedConfig.from_pretrained(tmp)


def instruction(args):
    if args.task:
        return args.task
    items = record.load_instructions()
    if args.product not in items:
        sys.exit(f"Unknown product '{args.product}'. In instructions.json: {', '.join(items)}")
    return items[args.product]["instruction"]


class Keys:
    """Single keys from the terminal, without waiting. Ctrl+C still works."""

    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.old = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)

    def get(self):
        if select.select([self.fd], [], [], 0)[0]:
            return os.read(self.fd, 1).decode(errors="ignore").lower()
        return None

    def forget(self):
        """Drop keys pressed meanwhile (for example during a move back)."""
        termios.tcflush(self.fd, termios.TCIFLUSH)

    def close(self):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)


def status(text):
    sys.stdout.write("\r\033[K" + text)
    sys.stdout.flush()


class Runner:
    def __init__(self, args, robot, policy, preprocessor, postprocessor, task):
        self.args = args
        self.robot = robot
        self.policy = policy
        self.preprocessor = preprocessor
        self.postprocessor = postprocessor
        self.task = task
        self.joints = list(robot.action_features)  # shoulder_pan.pos ... gripper.pos, as in the dataset
        self.cameras = [key.removeprefix("observation.images.") for key in policy.config.image_features]
        self.device = torch.device(policy.config.device)
        self.start_pose = None
        self.lead_steps = 0
        self.worker = ThreadPoolExecutor(max_workers=1)  # predicts the next chunk in the background

    def state(self, obs):
        return np.array([obs[joint] for joint in self.joints], dtype=np.float32)

    def predict_chunk(self, obs):
        """Joint targets for the next steps, the first one for the step of obs. Also returns the time it took."""
        t = time.monotonic()
        frame = {"observation.state": self.state(obs)}
        for cam in self.cameras:
            frame[f"observation.images.{cam}"] = obs[cam]
        with torch.inference_mode():
            batch = prepare_observation_for_inference(frame, self.device, self.task, self.robot.name)
            batch = self.preprocessor(batch)
            # The whole chunk is turned into joint targets at once: with relative actions, every
            # action of the chunk is relative to the arm position the model saw.
            chunk = self.postprocessor(self.policy.predict_action_chunk(batch)).squeeze(0)
        chunk = chunk.float().cpu().numpy()
        if self.args.execute_steps:
            chunk = chunk[: self.args.execute_steps]
        return chunk, time.monotonic() - t

    def reset(self):
        self.policy.reset()
        self.preprocessor.reset()
        self.postprocessor.reset()

    def warm_up(self):
        """The first predictions are slow (seconds): do them before the first episode, and measure the speed."""
        times = [self.predict_chunk(self.robot.get_observation())[1] for _ in range(3)]
        self.reset()
        self.set_lead(times[-1])
        print(f"Model ready: {times[-1] * 1000:.0f} ms per chunk.")

    def set_lead(self, seconds):
        """How many steps before the chunk runs out the next one is asked for: the model's time, plus a margin."""
        self.lead_steps = math.ceil(seconds * self.args.fps) + 2

    def send(self, values):
        self.robot.send_action({joint: float(v) for joint, v in zip(self.joints, values, strict=True)})

    def episode(self, keys):
        """Run the model until Space, Q, Ctrl+C or the time limit. Returns the key that stopped it.

        The model predicts the next chunk in the background while the arm plays the current one, so
        the arm does not stop to wait. When the new chunk arrives, its actions for the steps that went
        by meanwhile are skipped.
        """
        self.reset()
        queue = deque()
        pending = None  # the chunk being predicted, and the step its observation was taken at
        period = 1 / self.args.fps
        start = time.monotonic()
        step = 0
        infer_ms = []
        waited = 0  # steps without a target because the model was not ready
        biggest = 0.0
        obs = self.robot.get_observation()
        self.start_pose = self.state(obs)
        while True:
            tick = time.monotonic()
            elapsed = tick - start
            key = keys.get()
            if key in (" ", "q"):
                stopped_by = key
                break
            if elapsed > self.args.max_s:
                stopped_by = "time"
                break
            obs = self.robot.get_observation()
            if pending is None and len(queue) <= self.lead_steps:
                pending = (self.worker.submit(self.predict_chunk, obs), step)
            if pending is not None and pending[0].done():
                chunk, seconds = pending[0].result()
                stale = step - pending[1]
                queue = deque(chunk[min(stale, len(chunk) - 1):])
                pending = None
                infer_ms.append(seconds * 1000)
                self.set_lead(np.median(infer_ms) / 1000)
            if queue:
                action = queue.popleft()
                biggest = max(biggest, float(np.abs(action - self.state(obs)).max()))
                if not self.args.dry_run:
                    self.send(action)
            else:
                waited += 1  # the arm holds its last target
            step += 1
            model = f"model {infer_ms[-1]:4.0f} ms" if infer_ms else "model starting"
            status(f"{'DRY RUN' if self.args.dry_run else 'RUNNING'} {elapsed:5.1f} s | {model} | "
                   "Space = stop, Q = quit")
            precise_sleep(max(0.0, period - (time.monotonic() - tick)))
        if pending is not None:
            pending[0].result()  # let the model finish before it is used again
        print()
        reason = {"time": f"time limit ({self.args.max_s:g} s)", " ": "Space", "q": "Q"}[stopped_by]
        print(f"Stopped by {reason} after {time.monotonic() - start:.1f} s. Model: {len(infer_ms)} chunks, "
              f"{np.median(infer_ms or [0]):.0f} ms median, {max(infer_ms or [0]):.0f} ms slowest; "
              f"waited for it {waited} of {step} steps. Largest target away from the arm: {biggest:.1f} joint units.")
        return stopped_by

    def back_to_start(self):
        """Move the arm smoothly to where the last episode started."""
        if self.start_pose is None or self.args.dry_run:
            return
        here = self.state(self.robot.get_observation())
        distance = float(np.abs(self.start_pose - here).max())
        steps = max(1, int(distance / RETURN_SPEED * self.args.fps))
        print(f"Moving back to the start pose ({steps / self.args.fps:.1f} s)...")
        for i in range(1, steps + 1):
            tick = time.monotonic()
            self.send(here + (self.start_pose - here) * i / steps)
            precise_sleep(max(0.0, 1 / self.args.fps - (time.monotonic() - tick)))
        time.sleep(0.5)


def main():
    args = parse_args()
    if not sys.stdin.isatty():
        sys.exit("Run this in a terminal: the keys are read from it.")
    task = instruction(args)
    model_path = find_model(args.model)
    if not torch.cuda.is_available():
        sys.exit("No CUDA GPU found: GR00T needs one.")

    print(f"Loading the model from {model_path} ...")
    # Loaded on the CPU, then moved: loading on the GPU holds the weights twice for a moment (16 GB GPUs).
    config = load_config(model_path, "cpu")
    if not isinstance(config, GrootConfig):
        sys.exit(f"{model_path} is a '{config.type}' model; this program runs GR00T models.")
    cameras = {key.removeprefix("observation.images.") for key in config.image_features}
    if cameras != {record.GRIPPER_CAM, record.SCENE_CAM}:
        sys.exit(f"The model expects the cameras {sorted(cameras)}, but this robot has "
                 f"{record.GRIPPER_CAM} and {record.SCENE_CAM}.")
    t = time.monotonic()
    policy = GrootPolicy.from_pretrained(model_path, config=config)
    policy.config.device = "cuda"
    policy.to("cuda").eval()
    preprocessor, postprocessor = make_pre_post_processors(
        policy.config, pretrained_path=str(model_path),
        preprocessor_overrides={"device_processor": {"device": "cuda"}},
    )
    print(f"Model loaded in {time.monotonic() - t:.0f} s, "
          f"{torch.cuda.memory_allocated() / 1e9:.1f} GB of GPU memory.")

    record.apply_camera_settings({record.GRIPPER_CAM: args.gripper_cam, record.SCENE_CAM: args.scene_cam})
    robot = make_robot_from_config(SO100FollowerConfig(
        port=args.follower_port,
        id=record.FOLLOWER_ID,
        cameras={record.GRIPPER_CAM: record.camera_config(record.GRIPPER_CAM, args.gripper_cam, args),
                 record.SCENE_CAM: record.camera_config(record.SCENE_CAM, args.scene_cam, args)},
        max_relative_target=args.max_step or None,
        # Motors stay on when the program ends unexpectedly, so the arm does not drop.
        # They are switched off only after the arm is back at its start pose.
        disable_torque_on_disconnect=False,
    ))
    runner = Runner(args, robot, policy, preprocessor, postprocessor, task)

    print(f"Instruction: {task}")
    if args.dry_run:
        print("DRY RUN: the model runs, the arm does not move.")
    robot.connect()
    keys = None
    at_start = True
    try:
        runner.warm_up()
        keys = Keys()
        try:
            while True:
                status("READY | put the arm in its rest pose and the product on the shelf | "
                       "Space = start, R = back to start, Q = quit")
                key = keys.get()
                if key == " ":
                    print()
                    stopped_by = runner.episode(keys)
                    at_start = False
                    if stopped_by == "q":
                        break
                elif key == "r":
                    print()
                    runner.back_to_start()
                    keys.forget()
                    at_start = True
                elif key == "q":
                    break
                else:
                    time.sleep(0.05)
        except KeyboardInterrupt:  # like Q; a second Ctrl+C while the arm moves back leaves it holding
            print("\nCtrl+C")
        print()
        if not at_start:
            runner.back_to_start()
        robot.bus.disable_torque()
        print("Arm motors off.")
    finally:
        if keys:
            keys.close()
        robot.disconnect()


if __name__ == "__main__":
    main()
