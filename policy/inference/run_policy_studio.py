"""
Run a trained GR00T model on the arm for the Store Picker Bot app (Run step): the same model, arm and
cameras as run_policy.py (it uses its code), with the app's buttons instead of keys.

Commands come as lines on stdin:

    start                      start an episode (the arm must be in its rest pose); it ends by itself after
                               the drop in the basket (--drop_wait), and the arm goes back to rest
    stop                       stop the arm where it is (during an episode)
    back                       move the arm slowly back to where the episode started
    quit                       the arm first goes back to where the episode started, then its motors switch off
    product <id>               between episodes: another product (instruction), without reloading the model
    set speed=0.8 smooth=0.3   between episodes: change speed, smooth, blend_steps, substeps, accel, wait_act, drop_wait
                               or max_s

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
import run_settings
from run_policy import record
from run_settings import LIMITS as SETTINGS  # settings the app may change between episodes, with their limits


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
        pairs = dict(pair.partition("=")[::2] for pair in rest.split())
        unknown = [name for name in pairs if name not in SETTINGS]
        if unknown:
            return f"Unknown setting '{unknown[0]}'."
        try:
            values = run_settings.check(pairs)
        except ValueError as e:
            return str(e)
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
                stopped_by = runner.episode(commands)
                if stopped_by == "q":
                    break
                if stopped_by in ("dropped", "model did not start"):  # back to rest at once, so the arm
                    # does not wobble after the drop
                    runner.back_to_start()
                    commands.forget()
                    at_start = True
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
