"""
training_link.py

The Store Picker Bot app's link to the training PC, over SSH (key login, set up once by the admin):
send a dataset with rsync, start and stop a GR00T training there (policy/training/start_training.sh,
stop_training.sh), and read its status (policy/training/training_status.py).

Settings: the "training" block of studio_config.json. Guide: policy/docs/DATA_STUDIO.md (Train).
"""

import json
import re
import shlex
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

MIN_STEPS, MAX_STEPS = 1000, 200000
DISK_MARGIN_GB = 2.0  # free space left over at the fullest moment of a run
# Sizes of one checkpoint when the training PC has none to measure yet (GR00T N1.7, memory mode low).
DEFAULT_SIZES = {"model_gb": 9.4, "state_gb": 2.2}
GPU_BUSY_MB = 2000  # a GPU using more memory than this is taken by something else


def disk_needed_gb(steps, save_freq, keep_last, keep_model_every, sizes):
    """The most disk space a run uses at any moment: right after each checkpoint is written,
    before prune_checkpoints.py thins out the older ones (it runs within a minute)."""
    model, full = sizes["model_gb"], sizes["model_gb"] + sizes["state_gb"]
    saved, peak = [], 0.0
    saves = list(range(save_freq, steps + 1, save_freq))
    if not saves or saves[-1] != steps:
        saves.append(steps)  # LeRobot also saves at the last step
    for step in saves:
        saved.append(step)
        total = 0.0
        for i, s in enumerate(saved):
            newest = i >= len(saved) - keep_last - 1  # before thinning: one more than keep_last is complete
            total += full if newest else (model if keep_model_every and s % keep_model_every == 0 else 0)
        peak = max(peak, total)
        saved = [s for i, s in enumerate(saved)
                 if i >= len(saved) - keep_last or (keep_model_every and s % keep_model_every == 0)]
    return round(peak, 1)


class SendJob:
    """Sends one dataset folder to the training PC with rsync, and keeps its progress.

    The network between the PCs may limit each connection, not the total: so the files are shared
    out over several rsync streams, each with its own SSH connection. A last rsync pass then deletes
    files the training PC has but this dataset no longer has, and checks that everything arrived.
    """

    def __init__(self, link, local_root, folder, streams):
        self.kind = "send"
        self.folder = folder
        self.status = "running"
        self.stopping = False
        self.returncode = None
        self.log = deque(maxlen=50)
        self.info = {"percent": 0, "speed": "", "eta": "", "started": time.time(), "phase": "sending"}
        self.procs = []
        self.bytes = []
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, args=(link, Path(local_root), streams), daemon=True)
        self.thread.start()

    def _run(self, link, root, streams):
        try:
            files = sorted(((p.stat().st_size, p.relative_to(root).as_posix()) for p in root.rglob("*") if p.is_file()),
                           reverse=True)
            total = sum(size for size, _ in files) or 1
            buckets = [[] for _ in range(max(1, min(streams, len(files))))]
            loads = [0] * len(buckets)
            for size, name in files:  # biggest first, each into the least loaded stream
                i = loads.index(min(loads))
                buckets[i].append(name)
                loads[i] += size
            self.bytes = [0] * len(buckets)
            lists = []
            for i, names in enumerate(buckets):
                path = link.cache_dir / f"send_{i}.txt"
                path.write_text("\n".join(names) + "\n")
                lists.append(path)
            self._start(link, root, lists, total)
            if self.stopping or self.status != "running":
                return
            self.info.update(phase="checking", percent=100, eta="")
            self._pass(link.rsync_command(root, self.folder, final=True))
            if self.status == "running":
                self.status, self.returncode = "finished", 0
        except Exception as e:  # shown on the Train page
            self.log.append(str(e))
            self.status = "failed"

    def _start(self, link, root, lists, total):
        with self.lock:
            if self.stopping:
                self.status = "stopped"
                return
            for i, path in enumerate(lists):
                proc = subprocess.Popen(link.rsync_command(root, self.folder, files_from=path), stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
                self.procs.append(proc)
                threading.Thread(target=self._read, args=(proc, i), daemon=True).start()
        last, last_t = 0, time.time()
        while any(p.poll() is None for p in self.procs):
            time.sleep(1)
            sent = sum(self.bytes)
            now = time.time()
            if now - last_t >= 3:
                rate = (sent - last) / (now - last_t)
                last, last_t = sent, now
                self.info["speed"] = f"{rate / 1e6:.2f}MB/s"
                self.info["eta"] = time.strftime("%H:%M:%S", time.gmtime((total - sent) / rate)) if rate > 0 else ""
            self.info.update(sent_bytes=sent, percent=min(99, int(100 * sent / total)))
        codes = [p.wait() for p in self.procs]
        self.returncode = next((c for c in codes if c), 0)
        if self.stopping:
            self.status = "stopped"
        elif self.returncode:
            self.status = "failed"

    def _read(self, proc, i):
        buffer = b""
        while True:
            chunk = proc.stdout.read1(4096)
            if not chunk:
                break
            buffer += chunk
            *lines, buffer = re.split(rb"[\r\n]", buffer)  # rsync redraws its progress line with \r
            for raw in lines:
                self._line(raw.decode(errors="replace").strip(), i)
        self._line(buffer.decode(errors="replace").strip(), i)

    def _line(self, line, i):
        if not line:
            return
        # "    123,456,789  45%  1.02MB/s    0:04:12 (xfr#3, to-chk=10/50)"
        m = re.match(r"^([\d,]+)\s+\d+%\s+\S+/s\s+\d+:\d\d:\d\d", line)
        if m:
            self.bytes[i] = int(m.group(1).replace(",", ""))
        else:
            self.log.append(line)
            print(f"[send] {line}", flush=True)

    def _pass(self, cmd):
        with self.lock:
            if self.stopping:
                self.status = "stopped"
                return
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    start_new_session=True, text=True)
            self.procs = [proc]
        out, _ = proc.communicate()
        for line in out.splitlines():
            if line.strip():
                self.log.append(line.strip())
        if proc.returncode:
            self.returncode = proc.returncode
            self.status = "stopped" if self.stopping else "failed"

    def running(self):
        return self.status == "running"

    def stop(self):
        with self.lock:
            if self.status != "running":
                return
            self.stopping = True
            for proc in self.procs:
                if proc.poll() is None:
                    proc.terminate()

    def public(self):
        return {"kind": self.kind, "folder": self.folder, "status": self.status, "returncode": self.returncode,
                "streams": len(self.bytes), "info": dict(self.info, elapsed=round(time.time() - self.info["started"])),
                "log": list(self.log)[-8:]}


class TrainingLink:
    def __init__(self, config, cache_dir):
        self.cfg = config
        self.host = config["host"]
        self.repo = config["repo"].rstrip("/")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()  # one status request at a time; others get the cached answer
        self._status = None
        self._status_at = 0.0
        self._same = {}  # dataset folder -> (local stamp, remote copy is the same)

    # --- SSH ---
    def ssh_options(self, shared=True):
        """shared: one connection for all requests, as each new SSH login takes seconds on a slow network.
        A send stream needs its own connection instead (SSH uses the first value given for an option)."""
        options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15"]
        if shared:
            options += ["-o", "ControlMaster=auto", "-o", f"ControlPath={self.cache_dir}/ssh-%C", "-o", "ControlPersist=300"]
        return options

    def ssh(self, command, timeout=60):
        try:
            result = subprocess.run(["ssh", *self.ssh_options(), self.host, command], capture_output=True, text=True,
                                    timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise ValueError(f"The training PC ({self.host}) did not answer in {timeout} s.") from None
        if result.returncode == 255:  # SSH itself failed
            raise ValueError(self.explain_ssh(result.stderr))
        if result.returncode != 0:
            message = (result.stderr.strip() or result.stdout.strip()).splitlines()
            raise ValueError(message[-1] if message else f"Command failed on the training PC: {command}")
        return result.stdout

    def explain_ssh(self, stderr):
        text = stderr.strip()
        if "Permission denied" in text:
            return (f"The training PC refused the login ({self.host}): the admin must set up the SSH key once "
                    f"(ssh-copy-id {self.host}).")
        if "Connection refused" in text:
            return f"The training PC ({self.host}) does not accept SSH: is openssh-server installed there?"
        if "timed out" in text or "No route to host" in text or "Could not resolve" in text:
            return f"Cannot reach the training PC ({self.host}): is it switched on and on the network?"
        if "Host key verification failed" in text:
            return (f"The training PC is not known to this computer yet: the admin connects once with "
                    f"ssh {self.host} and answers yes.")
        return f"Cannot connect to the training PC ({self.host}): {text.splitlines()[-1] if text else 'SSH failed'}"

    def remote_path(self, *parts):
        return "/".join([self.repo, *parts])

    # --- status ---
    def status(self, max_age=4.0):
        """The training PC's state: reachable, project version, GPUs, disk, datasets, runs. Cached briefly."""
        with self.lock:
            if self._status and time.time() - self._status_at < max_age:
                return self._status
            training = shlex.quote(self.remote_path("policy", "training"))
            command = (f"cd {shlex.quote(self.repo)} 2>/dev/null || {{ echo NO_REPO; exit 0; }}; "
                       f"git rev-parse --short HEAD 2>/dev/null || echo unknown; "
                       f"test -f {training}/training_status.py || {{ echo OLD_VERSION; exit 0; }}; "
                       f"python3 {training}/training_status.py")
            try:
                lines = self.ssh(command, timeout=40).strip().splitlines()
                if lines and lines[0] == "NO_REPO":
                    status = {"ok": False, "error": f"The project is not at {self.repo} on the training PC: "
                                                    "check \"repo\" in studio_config.json."}
                elif len(lines) >= 2 and lines[1] == "OLD_VERSION":
                    status = {"ok": False, "commit": lines[0],
                              "error": "The training PC has an older version of the project: run git pull in "
                                       f"{self.repo} there."}
                else:
                    status = {"ok": True, "commit": lines[0], **json.loads(lines[-1])}
            except (ValueError, json.JSONDecodeError) as e:
                status = {"ok": False, "error": str(e)}
            status["host"] = self.host
            status["checked"] = time.time()
            self._status, self._status_at = status, time.time()
            return status

    def forget_status(self):
        with self.lock:
            self._status_at = 0.0

    def run_detail(self, run):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", run or ""):
            raise ValueError("Not a run name.")
        training = shlex.quote(self.remote_path("policy", "training"))
        return json.loads(self.ssh(f"python3 {training}/training_status.py --run {shlex.quote(run)}", timeout=40))

    # --- dataset ---
    def rsync_command(self, local_root, folder, dry_run=False, files_from=None, final=False):
        """rsync of a dataset folder: a dry run (what differs), one stream of files (files_from: its own SSH
        connection, so streams do not share one), or the final pass that deletes extra files."""
        target = self.remote_path("policy", "data_collection", "dataset", folder) + "/"
        ssh = ["ssh", *self.ssh_options(shared=not files_from)]
        # --omit-dir-times: folder dates do not matter, so a copy that differs only in them counts as the same.
        cmd = ["rsync", "-a", "--omit-dir-times", "--partial", "--mkpath", "-e", shlex.join(ssh)]
        if dry_run:
            cmd += ["--delete", "--dry-run", "--itemize-changes"]
        elif files_from:
            cmd += [f"--files-from={files_from}", "--info=progress2", "--no-inc-recursive"]
        elif final:
            cmd += ["--delete"]
        return cmd + [f"{local_root}/", f"{self.host}:{target}"]

    def dataset_same(self, local_root, folder, stamp):
        """Whether the training PC has exactly this dataset (rsync dry run; remembered until it changes here)."""
        known = self._same.get(folder)
        if known and known[0] == stamp:
            return known[1]
        result = subprocess.run(self.rsync_command(local_root, folder, dry_run=True), capture_output=True, text=True,
                                timeout=120, stdin=subprocess.DEVNULL)
        if result.returncode == 255:
            raise ValueError(self.explain_ssh(result.stderr))
        if result.returncode != 0:
            raise ValueError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "rsync failed")
        same = not result.stdout.strip()
        self._same[folder] = (stamp, same)
        return same

    def forget_dataset(self, folder):
        self._same.pop(folder, None)

    def send(self, local_root, folder):
        return SendJob(self, local_root, folder, int(self.cfg.get("send_streams", 4)))

    # --- training ---
    def plan(self, steps, status):
        """Settings and estimates of a run of this many steps, from the training PC's status."""
        cfg = self.cfg
        sizes = (status or {}).get("sizes") or DEFAULT_SIZES
        need = disk_needed_gb(steps, cfg["save_freq"], cfg["keep_last"], cfg["keep_model_every"], sizes)
        paces = [r["sec_per_step"] for r in (status or {}).get("runs", []) if r.get("sec_per_step")]
        pace = paces[0] if paces else cfg["sec_per_step"]
        return {"steps": steps, "gpu": cfg["gpu"], "batch_size": cfg["batch_size"], "memory_mode": cfg["memory_mode"],
                "save_freq": cfg["save_freq"], "keep_last": cfg["keep_last"], "keep_model_every": cfg["keep_model_every"],
                "disk_needed_gb": need, "free_gb": (status or {}).get("free_gb"),
                "hours": round(steps * pace / 3600, 1), "sec_per_step": pace}

    def start(self, folder, steps):
        cfg = self.cfg
        env = {"GPU": cfg["gpu"], "STEPS": steps, "BATCH_SIZE": cfg["batch_size"], "MEMORY_MODE": cfg["memory_mode"],
               "SAVE_FREQ": cfg["save_freq"], "KEEP_LAST": cfg["keep_last"], "KEEP_MODEL_EVERY": cfg["keep_model_every"],
               "CONDA_ENV": cfg.get("conda_env", "")}
        assignments = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in env.items())
        training = shlex.quote(self.remote_path("policy", "training"))
        out = self.ssh(f"cd {training} && {assignments} ./start_training.sh {shlex.quote(folder)}", timeout=60)
        self.forget_status()
        return out.strip().splitlines()[-1]

    def stop(self, run):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", run or ""):
            raise ValueError("Not a run name.")
        training = shlex.quote(self.remote_path("policy", "training"))
        out = self.ssh(f"cd {training} && ./stop_training.sh {shlex.quote(run)}", timeout=60)
        self.forget_status()
        return out.strip()
