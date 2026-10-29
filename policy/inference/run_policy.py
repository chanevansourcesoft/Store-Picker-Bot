"""
Run a trained GR00T model on the real SO-100 arm: the model picks one product and puts it in the basket.

The same program runs on a PC (for testing) and on the Jetson; pc/run.sh and jetson/run.sh only
start it in the right environment. It opens the follower arm and both cameras exactly like
record.py (same ports, camera settings and size), so the model sees what it saw in training.
The leader arm is not used.

Each episode: the model predicts 16 actions at a time (one chunk, about half a second at 30 fps)
from the two camera images, the arm position and the product's instruction. While the arm plays a
chunk, the model already predicts the next one from a newer picture, so the arm does not stop
between chunks. A new chunk fades in over its first steps (--blend_steps) and the arm joints'
targets are lightly smoothed (--smooth), so the arm does not jerk when a chunk replaces the
previous one; --speed below 1 plays the movements slower. Between two targets, --substeps more
commands move the arm part of the way, so it moves smoothly instead of in 30 small jumps a second,
and --accel makes the servos ramp their speed up and down gently. Keys (read from the terminal):

    Space  start an episode (the arm must be in its rest pose) / stop the arm where it is
    R      move the arm slowly back to where the episode started
    Q      quit: the arm first goes back to where the episode started, then its motors switch off

An episode also stops after --max_s seconds. --dry_run runs the model but never moves the arm:
use it first with every new model.

With --wait_act, the model only gets the arm once its predictions move a reaching joint or open the
gripper; if it does not want to act within that many seconds, the episode ends.

The Store Picker Bot app runs the same model and arm code with buttons instead of keys:
run_policy_studio.py (Run step).

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
import run_settings  # noqa: E402  (saved defaults)

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
# --wait_act: the model "wants to act" when its chunk moves a reaching joint (pan, shoulder, elbow, wrist
# roll) this much from where it is, or opens the gripper by ACT_MARGIN (joint units). In all 240 demos of
# dataset_v3 this happens within 3.2 s of the start (median 0.8 s). Small moves while the model holds the
# arm at rest (up to 12 seen) do not count.
REACH_MARGIN = 20.0
ACT_MARGIN = 8.0
# Grasp, drop and end of episode, in joint units (checked on dataset_v3: the grasp is seen in all 240
# recorded episodes, the end in 94%; the others were saved before the arm settled).
HOLD_OPEN = (6, 45)      # the gripper holds an item somewhere in this range (closed on nothing: about 1)
HOLD_STEADY = 2          # ... and moves less than this for half a second
HOLD_PRESS = 2.5         # ... while the model commands it this much more closed (it is blocked by the item)
DROP_OPEN = 5            # the drop: after a grasp, the gripper opens this much wider than it held the item,
DROP_NEAR = 88           # with the arm this close to its rest pose (over the basket). In dataset_v3 the grasp is
                         # always 97 or more from rest, the drop 79 or less; 219 of 240 demos open this wide
AWAY = 40                # the arm went at least this far from its rest pose (the episode really started)
NEAR_REST = 20           # end: the arm joints are back within this of the rest pose,
STILL = 8                # moved less than this over the last half second,
CLOSED = 6               # and the gripper is closed (under this)


def make_parser(product_required=True):
    parser = argparse.ArgumentParser(
        description="Run a trained GR00T model on the real SO-100 arm.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--model", required=True,
                        help="Training run folder, checkpoint folder or pretrained_model folder; a bare name is "
                             "looked up in inference/models/ and training/outputs/")
    what = parser.add_mutually_exclusive_group(required=product_required)
    what.add_argument("--product", help="Item id from instructions.json (its instruction is sent to the model)")
    what.add_argument("--task", help="Instruction text, instead of --product")
    parser.add_argument("--dry_run", action="store_true", help="Run the model but never move the arm")
    parser.add_argument("--max_s", type=float, default=40.0, help="Stop an episode after this many seconds")
    parser.add_argument("--execute_steps", type=int, default=None,
                        help="Play only the first N actions of each chunk before the model looks again "
                             "(default: the whole chunk)")
    parser.add_argument("--blend_steps", type=int, default=8,
                        help="A new chunk fades in over this many steps, from the old chunk's actions (0 = switch at once)")
    parser.add_argument("--smooth", type=float, default=0.3,
                        help="Smoothing of the arm joints' targets, 0 (off) to 0.9 (strong, but lags); the gripper is not smoothed")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Play the predicted movements at this speed: below 1 is slower (e.g. 0.8)")
    parser.add_argument("--substeps", type=int, default=3,
                        help="Commands per target: the arm is moved part of the way between two targets "
                             "(3 = 90 commands a second at 30 fps; 1 = off)")
    parser.add_argument("--accel", type=int, default=0,
                        help="Acceleration limit of the arm servos, 1 (very gentle) to 253; 0 = off (LeRobot's 254, "
                             "the fastest). The servos ramp their speed up and down; the gripper keeps its own")
    parser.add_argument("--wait_act", type=float, default=0.0,
                        help="The model only gets the arm once its predictions move a reaching joint or open the "
                             "gripper (it wants to act); until then the arm holds still. If it does not want to act within this many "
                             "seconds, the episode ends ('model did not start'). 0 = off (the model drives at once)")
    parser.add_argument("--drop_wait", type=float, default=0.5,
                        help="End the episode this many seconds after the item is dropped over the basket (the jaw "
                             "opens after a grasp), then move the arm back to rest, so it does not wobble there. "
                             "0 = off (the model keeps the arm until Stop or the time limit)")
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
    # Saved defaults (the app's "Save as default"); options on the command line still win.
    parser.set_defaults(**run_settings.load())
    return parser


def parse_args(parser=None):
    parser = parser or make_parser()
    args = parser.parse_args()

    if not 0 <= args.smooth <= 0.9:
        parser.error("--smooth must be 0 to 0.9")
    if not 0.3 <= args.speed <= 1.5:
        parser.error("--speed must be 0.3 to 1.5")
    if args.blend_steps < 0:
        parser.error("--blend_steps must be 0 or more")
    if not 1 <= args.substeps <= 5:
        parser.error("--substeps must be 1 to 5")
    if not 0 <= args.accel <= 253:
        parser.error("--accel must be 0 (off) to 253")
    if not 0 <= args.wait_act <= 30:
        parser.error("--wait_act must be 0 (off) to 30")
    if not 0 <= args.drop_wait <= 5:
        parser.error("--drop_wait must be 0 (off) to 5")
    missing = [flag for flag in ("follower_port", "gripper_cam", "scene_cam") if not getattr(args, flag)]
    if missing:
        parser.error("missing " + ", ".join(f"--{m}" for m in missing)
                     + " (pass them or set them in the repo root .env)")
    return args


def stretch(chunk, speed):
    """The chunk resampled to play at this speed (below 1: more steps, so slower), linearly between actions."""
    n = max(2, round(len(chunk) / speed))
    old_t = np.arange(len(chunk))
    new_t = np.linspace(0, len(chunk) - 1, n)
    return np.stack([np.interp(new_t, old_t, chunk[:, j]) for j in range(chunk.shape[1])], axis=1).astype(chunk.dtype)


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


class PickCheck:
    """Watches one episode: sees the grasp and the drop over the basket, and ends the episode --drop_wait
    seconds after the drop ("dropped"), or when the arm is back at rest if runner.end_at_rest ("back at rest")."""

    def __init__(self, runner):
        self.runner = runner
        self.gripper = runner.joints.index("gripper.pos")
        self.arm = np.arange(len(runner.joints)) != self.gripper
        n = max(2, round(runner.args.fps / 2))  # half a second
        self.grip = deque(maxlen=n)
        self.press = deque(maxlen=n)
        self.recent = deque(maxlen=n)
        self.away = False
        self.held = None  # where the gripper held the item (the last grasp seen)
        self.dropped_at = None

    @property
    def grasped(self):
        return self.held is not None

    def __call__(self, seconds, state, target):
        rest = self.runner.start_pose[self.arm]
        arm, grip = state[self.arm], float(state[self.gripper])
        self.grip.append(grip)
        self.press.append(grip - float(target[self.gripper]) if target is not None else 0.0)
        self.recent.append(arm)
        distance = float(np.abs(arm - rest).max())
        self.away = self.away or distance > AWAY
        full = len(self.grip) == self.grip.maxlen
        if (full and HOLD_OPEN[0] <= min(self.grip) and max(self.grip) <= HOLD_OPEN[1]
                and max(self.grip) - min(self.grip) < HOLD_STEADY and np.mean(self.press) >= HOLD_PRESS):
            if self.held is None:
                print(f"\nGrasp seen at {seconds:.1f} s (gripper at {grip:.0f}).", flush=True)
            self.held = float(np.mean(self.grip))
        if (self.dropped_at is None and self.held is not None and grip > self.held + DROP_OPEN
                and distance < DROP_NEAR):
            self.dropped_at = seconds
            print(f"\nDrop seen at {seconds:.1f} s.", flush=True)
        wait = self.runner.args.drop_wait
        if self.dropped_at is not None and wait and seconds - self.dropped_at >= wait:
            return "dropped"
        if (self.runner.end_at_rest and self.away and full and distance < NEAR_REST and grip < CLOSED
                and float(np.abs(np.array(self.recent) - arm).max()) < STILL):
            return "back at rest"
        return None


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
        self.model_ms = None
        self.worker = ThreadPoolExecutor(max_workers=1)  # predicts the next chunk in the background
        # Hooks for run_policy_studio.py: events for the app, and the one-line status (the terminal's).
        self.notify = lambda event, **fields: None
        self.show = status
        # The last episode's PickCheck (grasp, drop); pick_worker.py also ends episodes back at rest.
        self.check = None
        self.end_at_rest = False
        self.plan_shown = 0.0  # when wants_to_act last printed the model's plan

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
        if self.args.speed != 1.0:
            chunk = stretch(chunk, self.args.speed)
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
        self.model_ms = round(times[-1] * 1000)
        print(f"Model ready: {times[-1] * 1000:.0f} ms per chunk.")

    def set_lead(self, seconds):
        """How many steps before the chunk runs out the next one is asked for: the model's time, plus a margin."""
        self.lead_steps = math.ceil(seconds * self.args.fps) + 2

    def set_accel(self, value):
        """The arm servos' acceleration limit (0 = off: 254, as LeRobot sets it on connect); not the gripper's.
        A setting in the servos' memory: it is lost when they lose power."""
        if self.args.dry_run:
            return
        for joint in self.joints:
            motor = joint.removesuffix(".pos")
            if motor != "gripper":
                self.robot.bus.write("Acceleration", motor, value or 254)

    def send(self, values):
        self.robot.send_action({joint: float(v) for joint, v in zip(self.joints, values, strict=True)})

    def blend(self, old, new):
        """The new chunk, fading in over its first --blend_steps steps from the old chunk's remaining actions."""
        new = np.array(new)
        n = min(self.args.blend_steps, len(old), len(new))
        for i in range(n):
            w = (i + 1) / (n + 1)
            new[i] = w * new[i] + (1 - w) * old[i]
        return deque(new)

    def episode(self, keys):
        """Run the model until Space, Q, Ctrl+C, the time limit or the PickCheck. Returns the key that stopped
        it ("time" for the time limit, the PickCheck's reason for it).

        The model predicts the next chunk in the background while the arm plays the current one, so
        the arm does not stop to wait. When the new chunk arrives, its actions for the steps that went
        by meanwhile are skipped, and the rest fades in from the old chunk (blend). The arm joints'
        targets are smoothed (--smooth); the gripper's are not, so it still closes at once.
        """
        self.reset()
        obs = self.robot.get_observation()
        self.start_pose = self.state(obs)  # the rest pose: the arm comes back here (R)
        self.notify("state", state="running")
        self.check = PickCheck(self)
        queue = deque()
        pending = None  # the chunk being predicted, and the step its observation was taken at
        period = 1 / self.args.fps
        start = time.monotonic()
        step = 0
        infer_ms = []
        waited = 0  # steps without a target because the model was not ready
        biggest = 0.0
        jumps = []  # largest arm-joint change between two targets in a row, per step: how smooth it was
        target = None  # the last target sent
        arm = np.arange(len(self.joints)) != self.joints.index("gripper.pos")
        acting = not self.args.wait_act  # --wait_act: the model drives only once it wants to act
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
            if not acting and elapsed > self.args.wait_act:
                stopped_by = "model did not start"
                break
            obs = self.robot.get_observation()
            stopped_by = self.check(elapsed, self.state(obs), target)
            if stopped_by:
                break
            if pending is None and len(queue) <= self.lead_steps:
                pending = (self.worker.submit(self.predict_chunk, obs), step)
            if pending is not None and pending[0].done():
                chunk, seconds = pending[0].result()
                stale = step - pending[1]
                if not acting and self.wants_to_act(chunk, self.state(obs)):
                    acting = True
                    print(f"\nThe model starts after {elapsed:.1f} s.")
                if not acting:
                    chunk = chunk[:0]  # not played: the arm keeps holding still, the model looks again
                queue = self.blend(list(queue), chunk[min(stale, len(chunk) - 1):])
                pending = None
                infer_ms.append(seconds * 1000)
                self.set_lead(np.median(infer_ms) / 1000)
            if queue:
                action = queue.popleft()
                if target is not None:
                    action[arm] = self.args.smooth * target[arm] + (1 - self.args.smooth) * action[arm]
                    jumps.append(float(np.abs(action[arm] - target[arm]).max()))
                previous, target = target, action
                biggest = max(biggest, float(np.abs(action - self.state(obs)).max()))
                if not self.args.dry_run:
                    # Part of the way first, then the target, spread over this step (--substeps).
                    k = self.args.substeps if previous is not None else 1
                    for i in range(1, k + 1):
                        if i > 1:
                            precise_sleep(max(0.0, tick + (i - 1) * period / k - time.monotonic()))
                        self.send(action if i == k else previous + (action - previous) * i / k)
            elif acting:
                waited += 1  # the arm holds its last target
            step += 1
            model = f"model {infer_ms[-1]:4.0f} ms" if infer_ms else "model starting"
            what = ("DRY RUN" if self.args.dry_run else "RUNNING") if acting else "WAITING FOR THE MODEL TO ACT"
            self.show(f"{what} {elapsed:5.1f} s | {model} | Space = stop, Q = quit")
            precise_sleep(max(0.0, period - (time.monotonic() - tick)))
        if pending is not None:
            pending[0].result()  # let the model finish before it is used again
        print()
        reason = {"time": f"time limit ({self.args.max_s:g} s)", " ": "Space", "q": "Q",
                  "model did not start": f"the model did not want to act within {self.args.wait_act:g} s",
                  "dropped": f"the drop in the basket ({self.args.drop_wait:g} s after it)"}.get(stopped_by, stopped_by)
        print(f"Stopped by {reason} after {time.monotonic() - start:.1f} s. Model: {len(infer_ms)} chunks, "
              f"{np.median(infer_ms or [0]):.0f} ms median, {max(infer_ms or [0]):.0f} ms slowest; "
              f"waited for it {waited} of {step} steps. Largest target away from the arm: {biggest:.1f} joint units.")
        self.notify("episode", stopped_by=reason, seconds=round(time.monotonic() - start, 1), chunks=len(infer_ms),
                    model_ms=round(float(np.median(infer_ms or [0]))), waited=waited, steps=step,
                    biggest=round(biggest, 1),
                    smooth_median=round(float(np.median(jumps)), 2) if jumps else None,
                    smooth_95=round(float(np.percentile(jumps, 95)), 2) if jumps else None,
                    smooth_max=round(max(jumps), 2) if jumps else None)
        if jumps:
            print(f"Smoothness (arm joints, change between two targets in a row): median {np.median(jumps):.2f}, "
                  f"95% under {np.percentile(jumps, 95):.2f}, largest {max(jumps):.2f} joint units.")
        return stopped_by

    def wants_to_act(self, chunk, state):
        """True if the chunk moves a reaching joint (pan, shoulder, elbow, wrist roll) or opens the gripper: the
        model starts the task. Holding still means it thinks there is nothing to do. The wrist flex alone does
        not count: bending it at rest is not the start of the task."""
        reach = [self.joints.index(j) for j in ("shoulder_pan.pos", "shoulder_lift.pos", "elbow_flex.pos",
                                                "wrist_roll.pos")]
        gripper = self.joints.index("gripper.pos")
        moves = chunk[-1] - state
        if time.monotonic() - self.plan_shown >= 1:
            # What the model plans while the arm waits (once a second), to see why it holds still.
            self.plan_shown = time.monotonic()
            print("\nWaiting, the model plans (end of chunk minus now): "
                  + ", ".join(f"{j.removesuffix('.pos')} {m:+.0f}" for j, m in zip(self.joints, moves)), flush=True)
        return (float(np.abs(moves[reach]).max()) > REACH_MARGIN
                or float(chunk[:, gripper].max()) > float(state[gripper]) + ACT_MARGIN)

    def back_to_start(self):
        """Move the arm smoothly to where the last episode started."""
        if self.start_pose is None or self.args.dry_run:
            return
        self.notify("state", state="returning")
        seconds = self.move_to(self.start_pose)
        print(f"Moved back to the start pose ({seconds:.1f} s).")

    def move_to(self, target):
        """Move the arm smoothly to target, at RETURN_SPEED. Returns the time it took (s)."""
        here = self.state(self.robot.get_observation())
        distance = float(np.abs(target - here).max())
        steps = max(1, int(distance / RETURN_SPEED * self.args.fps))
        for i in range(1, steps + 1):
            tick = time.monotonic()
            self.send(here + (target - here) * i / steps)
            precise_sleep(max(0.0, 1 / self.args.fps - (time.monotonic() - tick)))
        time.sleep(0.5)
        return steps / self.args.fps


def load(args, notify=lambda event, **fields: None):
    """The model, the follower arm (not connected yet) and its cameras, set up like the recordings.
    Returns (runner, robot)."""
    task = instruction(args)
    model_path = find_model(args.model)
    if not torch.cuda.is_available():
        sys.exit("No CUDA GPU found: GR00T needs one.")

    print(f"Loading the model from {model_path} ...")
    notify("state", state="loading")
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
    runner.notify = notify
    return runner, robot


def main():
    args = parse_args()
    if not sys.stdin.isatty():
        sys.exit("Run this in a terminal: the keys are read from it.")
    runner, robot = load(args)
    task = runner.task

    print(f"Instruction: {task}")
    if args.dry_run:
        print("DRY RUN: the model runs, the arm does not move.")
    robot.connect()
    runner.set_accel(args.accel)
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
                    if stopped_by in ("dropped", "model did not start"):  # back to rest at once, so the arm
                        # does not wobble after the drop
                        runner.back_to_start()
                        keys.forget()
                        at_start = True
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
        runner.set_accel(0)  # as LeRobot set it on connect
        robot.bus.disable_torque()
        print("Arm motors off.")
    finally:
        if keys:
            keys.close()
        robot.disconnect()


if __name__ == "__main__":
    main()
