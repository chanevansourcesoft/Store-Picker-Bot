"""
Status of this training PC as JSON, for the Store Picker Bot app (it runs this over SSH):
free disk space, GPUs, datasets, and every training run in outputs/ with its progress (from the
logs of start_training.sh), checkpoints and size.

Only uses the Python standard library, so it runs with the system python3.

Usage:
    python3 training_status.py            # all runs
    python3 training_status.py --run <run name>   # one run, with its loss curve and log tail
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

TRAINING_DIR = Path(__file__).resolve().parent
OUTPUTS = TRAINING_DIR / "outputs"
LOGS = TRAINING_DIR / "logs"
DATASETS = TRAINING_DIR.parent / "data_collection" / "dataset"
# A LeRobot training line: "INFO 2026-10-22 12:00:00 ot_train.py:641 step:10 smpl:640 ... loss:0.123 ...".
# LeRobot 0.6.2 also draws a progress bar ("Training: 1%| ... 10/1000 [...]") on the same line, before it.
STEP_LINE = re.compile(r"INFO (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) .* step:\S+ .*?\bloss:([0-9.eE+-]+|nan)")
# Written by start_training.sh --resume: the steps after it count again from this checkpoint.
# It may follow a progress bar cut off by a crash on the same line, so it is searched, not matched.
RESUME_LINE = re.compile(r"\[resume\] step (\d+)")


def folder_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def gpus():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for line in out.strip().splitlines():
        index, name, used, total, util = [x.strip() for x in line.split(",")]
        rows.append({"index": int(index), "name": name, "used_mb": int(used), "total_mb": int(total),
                     "util": int(util)})
    return rows


def gpu_of_processes():
    """{pid: GPU index} of every process using a GPU (nvidia-smi)."""
    try:
        q = lambda what: subprocess.run(["nvidia-smi", f"--query-{what}", "--format=csv,noheader"],
                                        capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
        index = dict(reversed([x.strip() for x in line.split(",")]) for line in q("gpu=index,uuid"))
        return {int(pid): int(index[uuid.strip()]) for pid, uuid in
                (line.split(",")[:2] for line in q("compute-apps=pid,gpu_uuid")) if uuid.strip() in index}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {}


def visible_gpus(pid):
    """GPU indices a process may use (its CUDA_VISIBLE_DEVICES), or None: all of them, or unknown."""
    try:
        env = dict(x.split("=", 1) for x in open(f"/proc/{pid}/environ").read().split("\0") if "=" in x)
        return [int(g) for g in env["CUDA_VISIBLE_DEVICES"].split(",") if g.strip()]
    except (OSError, KeyError, ValueError):
        return None


def parent(pid):
    try:
        return int(open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def trainings():
    """Every lerobot-train running on this PC (also ones started by hand), with its GPUs:
    [{"pid", "gpus": [index, ...] or None (unknown: may use any GPU)}]."""
    out = subprocess.run(["pgrep", "-f", r"lerobot-train( |$)"], capture_output=True, text=True).stdout
    pids = {int(p) for p in out.split()}
    on_gpu = gpu_of_processes()
    rows = []
    for pid in sorted(pids):
        if parent(pid) in pids:
            continue  # a data loader worker of a training already listed
        gpus = [on_gpu[pid]] if pid in on_gpu else visible_gpus(pid)
        rows.append({"pid": pid, "gpus": gpus})
    return rows


def datasets():
    rows = {}
    if DATASETS.is_dir():
        for path in sorted(DATASETS.iterdir()):
            try:
                info = json.loads((path / "meta" / "info.json").read_text())
            except (OSError, ValueError):
                continue
            rows[path.name] = {"episodes": info.get("total_episodes"), "frames": info.get("total_frames")}
    return rows


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def parse_log(path, log_freq):
    """(step, [(step, loss)], seconds per step over the last lines, last lines) of a start_training.sh log."""
    points, times = [], []
    lines = []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return 0, [], None, []
    step = 0
    for line in re.split(r"[\r\n]", text):
        r = RESUME_LINE.search(line)
        m = None if r else STEP_LINE.search(line)
        if m:
            step += log_freq
            points.append((step, float(m.group(2))))
            times.append(time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")))
        elif r:  # the steps after the resumed checkpoint are trained again
            step = int(r.group(1))
            keep = sum(1 for s, _ in points if s <= step)
            points, times = points[:keep], []  # no pace across the pause
            lines.append(r.group(0))
        elif line.strip() and not line.startswith("Training:"):  # progress bar redraws
            lines.append(line)
    pace = None
    if len(times) >= 2:
        recent = times[-50:]
        if recent[-1] > recent[0]:
            pace = (recent[-1] - recent[0]) / ((len(recent) - 1) * log_freq)
    step = points[-1][0] if points else 0
    return step, points, pace, lines[-20:]


def resume_step(run_dir):
    """Step of the newest complete checkpoint (checkpoints/last with its training state), or None."""
    last = run_dir / "checkpoints" / "last"
    if (last / "training_state").is_dir() and last.resolve().name.isdigit():
        return int(last.resolve().name)
    return None


def checkpoints(run_dir):
    rows = []
    folder = run_dir / "checkpoints"
    if folder.is_dir():
        for path in sorted((p for p in folder.iterdir() if p.is_dir() and p.name.isdigit()), key=lambda p: int(p.name)):
            rows.append({"step": int(path.name), "model": (path / "pretrained_model" / "model.safetensors").exists(),
                         "resume": (path / "training_state").is_dir()})
    return rows


def run_status(name, detail=False):
    run_dir = OUTPUTS / name
    meta_path = LOGS / f"{name}.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    row = {"run": name, "app": bool(meta), "dataset": meta.get("dataset") or re.sub(r"^\d{8}_\d{6}_", "", name),
           "started": meta.get("started") or (run_dir.stat().st_mtime if run_dir.exists() else None),
           "steps": meta.get("steps"), "gpu": meta.get("gpu"), "batch_size": meta.get("batch_size"),
           "checkpoints": checkpoints(run_dir),
           "size_gb": round(folder_size(run_dir) / 1e9, 1) if run_dir.exists() else 0}
    if not meta:
        row["status"] = "unknown"  # started by hand: no log here
        return row
    pid_path, exit_path = LOGS / f"{name}.pid", LOGS / f"{name}.exit"
    if pid_path.exists() and alive(int(pid_path.read_text().strip() or 0)):
        row["status"] = "running"
    elif exit_path.exists():
        row["status"] = "finished" if exit_path.read_text().strip() == "0" else "failed"
    elif (LOGS / f"{name}.stopped").exists():
        row["status"] = "stopped"
        row["stopped_by"] = (LOGS / f"{name}.stopped").read_text(errors="replace").strip() or None  # stop_training.sh
    else:
        row["status"] = "stopped"  # killed some other way (or the PC restarted)
    step, points, pace, tail = parse_log(LOGS / f"{name}.log", meta.get("log_freq", 10))
    row["step"] = step
    row["loss"] = points[-1][1] if points else None
    row["sec_per_step"] = round(pace, 3) if pace else None
    if row["status"] == "running" and pace and meta.get("steps"):
        row["eta_s"] = round(max(0, meta["steps"] - step) * pace)
    if row["status"] == "failed":
        row["error"] = tail[-12:]
    if row["status"] in ("stopped", "failed"):
        from_step = resume_step(run_dir)
        if from_step is not None and from_step < (meta.get("steps") or 0):
            row["resume_from"] = from_step  # the app offers Resume
    if detail:
        sampled = points[::max(1, len(points) // 300)]
        if points and sampled[-1] != points[-1]:
            sampled.append(points[-1])
        row["losses"] = sampled
        row["log"] = tail
    return row


def typical_sizes():
    """GB of one pretrained_model/ and one training_state/, from the newest complete checkpoint here."""
    newest = None
    if OUTPUTS.is_dir():
        for path in OUTPUTS.glob("*/checkpoints/[0-9]*"):
            if (path / "pretrained_model").is_dir() and (path / "training_state").is_dir():
                if newest is None or path.stat().st_mtime > newest.stat().st_mtime:
                    newest = path
    if newest is None:
        return None
    return {"model_gb": round(folder_size(newest / "pretrained_model") / 1e9, 2),
            "state_gb": round(folder_size(newest / "training_state") / 1e9, 2)}


def main():
    parser = argparse.ArgumentParser(description="Status of this training PC as JSON.")
    parser.add_argument("--run", help="Only this run, with its loss curve and log tail")
    args = parser.parse_args()
    if args.run:
        print(json.dumps(run_status(Path(args.run).name, detail=True)))
        return
    names = set()
    if OUTPUTS.is_dir():
        names |= {p.name for p in OUTPUTS.iterdir() if p.is_dir()}
    if LOGS.is_dir():
        names |= {p.stem for p in LOGS.glob("*.json")}
    runs = sorted((run_status(n) for n in names), key=lambda r: r["started"] or 0, reverse=True)
    running = trainings() + [{"run": r["run"], "gpus": [int(g) for g in str(r["gpu"]).split(",")]}
                             for r in runs if r["status"] == "running"]
    print(json.dumps({
        "time": time.time(),
        "free_gb": round(shutil.disk_usage(TRAINING_DIR).free / 1e9, 1),
        "gpus": gpus(),
        "trainings": running,  # lerobot-train processes and runs of start_training.sh still going, with their GPUs
        "datasets": datasets(),
        "runs": runs,
        "sizes": typical_sizes(),
    }))


if __name__ == "__main__":
    main()
