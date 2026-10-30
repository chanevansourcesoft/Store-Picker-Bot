#!/usr/bin/env bash
# Shows on the training PC's own screen that a training is running, with its progress, so nobody
# reboots the PC. Opened by start_training.sh in a terminal window; closing the window does not
# stop the training. It refreshes every 30 seconds and says so when no training is left.
#
# Usage: ./show_training.sh
TRAINING_DIR="${TRAINING_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

while true; do
  status="$(python3 "$TRAINING_DIR/training_status.py" 2>/dev/null | tail -n 1)"
  clear
  python3 - "$status" <<'EOF'
import json, sys, time

RED, GREEN, BOLD, END = "\033[41;97m", "\033[42;30m", "\033[1m", "\033[0m"
try:
    status = json.loads(sys.argv[1])
except ValueError:
    print("Could not read the training status.")
    sys.exit(0)
runs = [r for r in status["runs"] if r.get("status") == "running"]
if not runs:
    print(f"\n{GREEN}{BOLD}   No training is running. This PC can be restarted.   {END}\n")
    sys.exit(3)
print(f"\n{RED}{BOLD}   TRAINING IS RUNNING: DO NOT REBOOT OR SHUT DOWN THIS PC   {END}\n")
for r in runs:
    step, total = r.get("step") or 0, r.get("steps") or 1
    done = min(1, step / total)
    bar = "#" * round(40 * done)
    print(f"{BOLD}{r['run']}{END}")
    print(f"  [{bar:<40}] {100 * done:.1f}%")
    print(f"  Step       {step:,} / {total:,}")
    if r.get("loss") is not None:
        print(f"  Loss       {r['loss']:.4f}")
    if r.get("sec_per_step"):
        print(f"  Speed      {r['sec_per_step']} s per step")
    if r.get("eta_s") is not None:
        ends = time.strftime("%a %H:%M", time.localtime(time.time() + r["eta_s"]))
        print(f"  Time left  about {r['eta_s'] // 3600} h {r['eta_s'] % 3600 // 60:02d} min (ends {ends})")
    print()
for g in status["gpus"]:
    print(f"  GPU {g['index']}: {g['used_mb'] / 1000:.1f} of {g['total_mb'] / 1000:.0f} GB, {g['util']}% busy")
print(f"\n  Free disk: {status['free_gb']:.0f} GB")
print(f"\n  Updated {time.strftime('%H:%M:%S')}. Closing this window does not stop the training.")
EOF
  [[ $? -eq 3 ]] && break
  sleep 30
done
read -r -p "Press Enter to close. "
