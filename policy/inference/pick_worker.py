"""
The policy worker for the robot server (agent/robot_server/arm.py): keeps GR00T loaded and the
arm and cameras open, and runs one pick when asked. It answers on a small local web port
(default 127.0.0.1:8790); pick_client.py is the code the robot server uses to call it.

    GET  /status   {"ready", "busy", "state", "at_rest", "last", ...}
    POST /pick     {"item": "ink_box"}: one episode, then the answer {"ok", "reason", "message", ...}
    POST /stop     stop the arm where it is (during a pick); it goes back to rest before the next pick
    POST /home     move the arm back to its rest pose

A pick: the item's instruction comes from policy/instructions.json (the caller sends only the item
id). The arm starts in its rest pose, the model picks the item and puts it in the basket, and the
episode ends by itself --drop_wait seconds after the drop (the jaw opens over the basket after a
grasp), or when the arm is back near its rest pose with the gripper closed and still. Then the arm
moves exactly back to its rest pose, as R does in run_policy.py. The answer says whether it worked:
ok only if the episode ended by itself and a grasp was seen (the jaw stayed steady part-way open
while the model pressed it closed). The episode also ends at --max_s (not ok).
The arm never moves without a /pick or /home.

"reason" is a fixed code for programs (the message is for people and may be reworded):
    placed        ok: the item is in the basket, the arm at rest
    no_grasp      the episode ended at rest without a grasp: nothing picked
    time_limit    --max_s reached (after a grasp or not); the arm went back to rest
    no_start      --wait_act: the model did not want to act (it may think the task is done); arm back at rest
                  (the message starts with "no grasp seen", so callers that read it retry, as for no_grasp)
    holding       the gripper still holds something at rest: a person should look
    stopped       /stop during the pick: the arm holds where it is
    unknown_item  the item is not in instructions.json: the arm did not move
    busy          another pick or move is running: the arm did not move for this one
    not_ready     the worker is still starting: the arm did not move
    policy_error  the model or the arm failed (see the message)

Usage (same options as run_policy.py, without --product):
    policy/inference/pc/pick_worker.sh --model <name>                # PC (conda env active)
    policy/inference/jetson/pick_worker.sh --model <name>            # Jetson (Docker)
Stop it with Ctrl+C: the arm goes back to rest, then its motors switch off.

Guide: policy/docs/INFERENCE.md
"""

import json
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import run_policy
from run_policy import CLOSED, record


def log(text):
    print(time.strftime("%H:%M:%S"), text, flush=True)


class Worker:
    def __init__(self, args):
        self.args = args
        run_policy.find_model(args.model)  # stops here if there is no such model
        self.model = Path(args.model).name
        self.runner = None
        self.robot = None
        self.lock = threading.Lock()  # one pick or move at a time
        self.stop_flag = threading.Event()
        self.state = "loading"
        self.item = None
        self.at_rest = True
        self.last = None
        self.error = None

    def start(self):
        """Load the model, connect the arm and cameras, warm up. Then the worker is ready."""
        self.runner, self.robot = run_policy.load(self.args)
        self.runner.show = lambda text: None  # no progress line: this is a log
        self.runner.end_at_rest = True
        self.state = "connecting"
        self.robot.connect()
        self.runner.set_accel(self.args.accel)
        self.runner.warm_up()
        self.state = "ready"
        log(f"Ready on http://{self.args.host}:{self.args.port}"
            + (" (DRY RUN: the arm does not move)" if self.args.dry_run else ""))

    def status(self):
        return {"ready": self.state not in ("loading", "connecting", "error", "closing"), "busy": self.lock.locked(),
                "state": self.state, "item": self.item, "at_rest": self.at_rest, "dry_run": self.args.dry_run,
                "model": self.model, "model_ms": getattr(self.runner, "model_ms", None), "last": self.last,
                "error": self.error}

    # The runner reads its keys from here during an episode: " " stops it.
    def get(self):
        return " " if self.stop_flag.is_set() else None

    def pick(self, item):
        if self.state in ("loading", "connecting"):
            return {"ok": False, "reason": "not_ready", "message": "the policy worker is still starting"}
        try:
            items = record.load_instructions()
        except (OSError, ValueError) as e:
            return {"ok": False, "reason": "policy_error", "message": f"cannot read instructions.json: {e}"}
        if not isinstance(item, str) or item not in items:
            return {"ok": False, "reason": "unknown_item",
                    "message": f"unknown item '{item}' (in instructions.json: {', '.join(items)})"}
        if not self.lock.acquire(blocking=False):
            return {"ok": False, "reason": "busy", "message": f"busy ({self.state})"}
        try:
            self.stop_flag.clear()
            if not self.at_rest:
                self._home()
            self.runner.task = items[item]["instruction"]
            self.item, self.state, self.at_rest = item, "picking", False
            log(f"Pick {item}: {self.runner.task}")
            start = time.monotonic()
            stopped_by = self.runner.episode(self)
            check = self.runner.check
            seconds = round(time.monotonic() - start, 1)
            if stopped_by == " ":
                result = {"ok": False, "reason": "stopped", "message": f"stopped: the arm holds where it is ({item})"}
                self.state = "stopped"
            else:
                self._home()
                holding = self._holding()
                if stopped_by in ("dropped", "back at rest") and check.grasped and not holding:
                    result = {"ok": True, "reason": "placed", "message": f"placed {item} in basket"}
                elif stopped_by == "back at rest":
                    result = {"ok": False, "reason": "no_grasp", "message": f"no grasp seen: {item} was not picked"}
                elif stopped_by == "model did not start":
                    # Starts like no_grasp, so callers that read the message retry it (the arm is at rest).
                    result = {"ok": False, "reason": "no_start", "message": f"no grasp seen: the model did not start "
                              f"within {self.args.wait_act:g} s, {item} was not picked"}
                else:
                    result = {"ok": False, "reason": "time_limit", "message": f"time limit ({self.args.max_s:g} s), "
                              + ("after a grasp" if check.grasped else "no grasp seen")}
                if holding:  # whatever else happened, a person should look
                    result["reason"] = "holding"
                    result["message"] += "; the gripper still holds something"
            result.update(item=item, instruction=self.runner.task, seconds=seconds, grasp_seen=check.grasped,
                          ended_by=stopped_by.strip() or "stop")
            log(f"Pick {item}: {'OK' if result['ok'] else 'FAILED'}, {result['message']} ({seconds} s)")
        except Exception as e:  # the arm or the model failed: report it, keep serving
            self.error = f"{type(e).__name__}: {e}"
            self.state = "error"
            result = {"ok": False, "reason": "policy_error", "message": f"policy error: {self.error}", "item": item}
            log(f"Pick {item}: ERROR {self.error}")
        finally:
            self.item = None
            self.lock.release()
        self.last = result
        return result

    def stop(self):
        """Stop a running pick at once (the arm holds where it is). Does nothing when idle."""
        running = self.state == "picking"
        self.stop_flag.set()
        if running:
            log("Stop.")
        return {"ok": True, "message": "stopped" if running else "nothing running"}

    def home(self):
        if self.state in ("loading", "connecting"):
            return {"ok": False, "reason": "not_ready", "message": "the policy worker is still starting"}
        if not self.lock.acquire(blocking=False):
            return {"ok": False, "reason": "busy", "message": f"busy ({self.state})"}
        try:
            self._home()
            return {"ok": True, "message": "at rest"}
        finally:
            self.lock.release()

    def _home(self):
        """Back to the rest pose: where the last pick started."""
        if self.runner.start_pose is not None and not self.at_rest:
            self.state = "returning"
            self.runner.back_to_start()
        self.at_rest = True
        self.state = "ready"

    def _holding(self):
        """True if the gripper is blocked open at rest, by an item (closed on nothing it is about 1)."""
        if self.args.dry_run:
            return False
        grip = self.runner.state(self.robot.get_observation())[self.runner.joints.index("gripper.pos")]
        return float(grip) >= CLOSED

    def close(self):
        """At the end: stop, back to rest, motors off."""
        self.stop_flag.set()
        if self.robot is None or not self.robot.is_connected:
            return
        with self.lock:
            self.state = "closing"
            try:
                if not self.at_rest and self.runner.start_pose is not None:
                    self.runner.back_to_start()
                self.runner.set_accel(0)  # as LeRobot set it on connect
                self.robot.bus.disable_torque()
                log("Arm motors off.")
            finally:
                self.robot.disconnect()


def make_handler(worker):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, data):
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/status":
                self._send(200, worker.status())
            else:
                self._send(404, {"ok": False, "message": "not found"})

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"ok": False, "message": "the body must be JSON"})
            if self.path == "/pick":
                self._send(200, worker.pick(body.get("item")))
            elif self.path == "/stop":
                self._send(200, worker.stop())
            elif self.path == "/home":
                self._send(200, worker.home())
            else:
                self._send(404, {"ok": False, "message": "not found"})

        def log_message(self, format, *args):  # no line per request
            pass

    return Handler


def main():
    parser = run_policy.make_parser(product_required=False)
    parser.description = "Keep a GR00T model loaded and run one pick per request (for the robot server)."
    parser.add_argument("--host", default="127.0.0.1", help="Address to answer on (127.0.0.1: this computer only)")
    parser.add_argument("--port", type=int, default=8790, help="Port to answer on")
    args = run_policy.parse_args(parser)
    if args.task:
        parser.error("--task is not used: each pick names its item, and its instruction comes from instructions.json")
    args.product = args.product or next(iter(record.load_instructions()), None)  # for the warm-up only

    worker = Worker(args)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(worker))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    quit_event = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: quit_event.set())
    try:
        worker.start()
        quit_event.wait()
        log("Closing: the arm goes back to rest first.")
    except SystemExit as e:
        worker.state, worker.error = "error", str(e.code)
        raise
    finally:
        server.shutdown()
        worker.close()


if __name__ == "__main__":
    main()
