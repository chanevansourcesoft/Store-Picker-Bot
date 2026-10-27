"""
Run a trained GR00T model on the arm for the Store Picker Bot app (Run step): the same model, arm and
cameras as run_policy.py (it uses its code), with the app's buttons instead of keys.

Commands come as lines on stdin:

    start                      start an episode (the arm must be in its rest pose)
    stop                       stop the arm where it is (during an episode)
    back                       move the arm slowly back to where the episode started
    quit                       the arm first goes back to where the episode started, then its motors switch off
    product <id>               between episodes: another product (instruction), without reloading the model
    set speed=0.8 smooth=0.3   between episodes: change speed, smooth, blend_steps, substeps, accel or max_s

Events go to stdout as "[STUDIO] {json}" lines (state, ready, episode, task, settings, error), and
--preview_dir gets small camera snapshots for the app's live view. Started by studio.py; it takes
the same options as run_policy.py, plus --preview_dir.
"""

import json
import sys
import threading
import time
from collections import deque

import run_policy
from run_policy import record

# Settings the app may change between episodes, with their limits (as studio.py checks them).
SETTINGS = {"speed": (float, 0.3, 1.5), "smooth": (float, 0.0, 0.9), "blend_steps": (int, 0, 16),
            "substeps": (int, 1, 5), "accel": (int, 0, 253), "max_s": (float, 5, 300)}


def emit(event, **fields):
    print("[STUDIO] " + json.dumps({"event": event, **fields}), flush=True)


class Commands:
    """The app's commands from stdin, turned into run_policy's keys (Space, R, Q); others as they are."""

    KEYS = {"start": " ", "stop": " ", "back": "r", "quit": "q"}

    def __init__(self):
        self.lines = deque()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in sys.stdin:
            if line.strip():
                self.lines.append(line.strip())
        self.lines.append("quit")  # the app went away

    def get(self):
        if not self.lines:
            return None
        line = self.lines.popleft()
        return self.KEYS.get(line, line)

    def wait(self):
        while not self.lines:
            time.sleep(0.05)
        return self.get()

    def forget(self):
        """Drop commands sent during a move back, except quit."""
        self.lines = deque(line for line in self.lines if line == "quit")


def change(runner, line):
    """Between episodes: "product <id>" or "set name=value ...". Returns a problem text, or None."""
    word, _, rest = line.partition(" ")
    if word == "product":
        items = record.load_instructions()
        if rest not in items:
            return f"Unknown product '{rest}'."
        runner.task = items[rest]["instruction"]
        runner.args.product = rest
        emit("task", product=rest, instruction=runner.task)
        return None
    if word == "set":
        values = {}
        for pair in rest.split():
            name, _, text = pair.partition("=")
            if name not in SETTINGS:
                return f"Unknown setting '{name}'."
            kind, low, high = SETTINGS[name]
            try:
                value = kind(text)
            except ValueError:
                return f"{name} must be a number."
            if not low <= value <= high:
                return f"{name} must be {low:g} to {high:g}."
            values[name] = value
        for name, value in values.items():
            setattr(runner.args, name, value)
        if "accel" in values:
            runner.set_accel(values["accel"])
        emit("settings", **{name: getattr(runner.args, name) for name in SETTINGS})
        return None
    return f"Unknown command '{line}'."


def run(args):
    runner, robot = run_policy.load(args, notify=emit)
    runner.show = lambda text: None  # the app shows the state from the events
    emit("state", state="connecting")
    robot.connect()
    runner.set_accel(args.accel)
    preview = None
    commands = Commands()
    at_start = True
    try:
        if args.preview_dir:
            preview = record.Preview(robot.cameras, args.preview_dir)
        runner.warm_up()
        emit("ready", product=args.product, instruction=runner.task, dry_run=args.dry_run, model_ms=runner.model_ms,
             **{name: getattr(args, name) for name in SETTINGS})
        while True:
            emit("state", state="waiting", at_start=at_start)
            key = commands.wait()
            if key == " ":
                at_start = False
                if runner.episode(commands) == "q":
                    break
            elif key == "r":
                runner.back_to_start()
                commands.forget()
                at_start = True
            elif key == "q":
                break
            else:
                problem = change(runner, key)
                if problem:
                    emit("error", message=problem, fatal=False)
        if not at_start:
            runner.back_to_start()
        runner.set_accel(0)  # as LeRobot set it on connect
        emit("state", state="stopping")
        robot.bus.disable_torque()
        print("Arm motors off.", flush=True)
    finally:
        if preview is not None:
            preview.close()
        robot.disconnect()


def main():
    parser = run_policy.make_parser()
    parser.add_argument("--preview_dir", help="Folder for the live camera snapshots")
    args = run_policy.parse_args(parser)
    try:
        run(args)
    except SystemExit as e:
        if e.code not in (None, 0):
            emit("error", message=str(e.code))
        raise
    except Exception as e:
        emit("error", message=f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
