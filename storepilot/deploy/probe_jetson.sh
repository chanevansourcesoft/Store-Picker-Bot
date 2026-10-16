#!/usr/bin/env bash
# Run on the Raspberry Pi. READ-ONLY: only GET requests and TCP connects, never POST.
#
#   ./probe_jetson.sh                 find hosts on this network with port 8000 open
#   ./probe_jetson.sh <jetson-ip>     check the robot server API from the Pi
#   ./probe_jetson.sh <ip> <port>
#
# Paste the whole output back into the chat.
set -u
PORT="${2:-8000}"
line() { printf '\n==== %s ====\n' "$1"; }
get() {  # get <path> : prints HTTP code, time and body (GET only)
  curl -s -m 4 -o /tmp/storepilot_probe.$$ -w 'HTTP %{http_code} in %{time_total}s\n' "http://$IP:$PORT$1" \
    || echo "no answer"
  [ -s /tmp/storepilot_probe.$$ ] && python3 -m json.tool /tmp/storepilot_probe.$$ 2>/dev/null | head -60 \
    || cat /tmp/storepilot_probe.$$ 2>/dev/null | head -20
  rm -f /tmp/storepilot_probe.$$
}

line "Pi network"
hostname; ip -4 -br addr | grep -v '^lo'; ip route | head -3

if [ -z "${1:-}" ]; then
  line "Known neighbours (ARP)"
  ip neigh show | grep -v FAILED
  NET=$(ip -4 route | awk '/proto kernel/ {print $1; exit}')
  BASE=$(echo "$NET" | cut -d/ -f1 | cut -d. -f1-3)
  line "Hosts in $BASE.0/24 with TCP port $PORT open (takes ~10 s)"
  for i in $(seq 1 254); do
    ( timeout 0.6 bash -c "</dev/tcp/$BASE.$i/$PORT" 2>/dev/null && echo "  $BASE.$i:$PORT open" ) &
  done
  wait
  echo "Then run: $0 <jetson-ip>"
  exit 0
fi

IP="$1"
line "Reachability $IP"
ping -c 3 -W 1 "$IP" | tail -2
timeout 2 bash -c "</dev/tcp/$IP/$PORT" 2>/dev/null && echo "TCP $PORT open" || echo "TCP $PORT CLOSED"
timeout 2 bash -c "</dev/tcp/$IP/9090" 2>/dev/null && echo "TCP 9090 (rosbridge) open" || echo "TCP 9090 (rosbridge) closed"

line "GET /health";  get /health
line "GET /status";  get /status
line "GET /map";     get /map

line "Endpoints the server really has (/openapi.json)"
curl -s -m 4 "http://$IP:$PORT/openapi.json" | python3 -c '
import json, sys
try:
    spec = json.load(sys.stdin)
except Exception:
    print("no openapi.json (not a FastAPI server?)"); sys.exit()
print("title:", spec.get("info", {}).get("title"))
for path, ops in sorted(spec.get("paths", {}).items()):
    print(" ", ",".join(m.upper() for m in ops), path)
'

line "Stub / default value check on /status"
curl -s -m 4 "http://$IP:$PORT/status" | python3 -c '
import json, sys
try:
    s = json.load(sys.stdin)
except Exception:
    print("no /status"); sys.exit()
notes = []
if s.get("battery") == 100.0: notes.append("battery is exactly 100.0: the server default (no /battery_state published?)")
if s.get("speed") in (0.3,): notes.append("speed is exactly 0.3: the constant set while navigating, not odometry")
if s.get("pose") == {"x": 0.0, "y": 0.0, "theta": 0.0}: notes.append("pose is 0,0,0: probably never updated (only Nav2 feedback writes it)")
if "stopped" not in s: notes.append("no stopped field: older jetson_server.py than this repo")
print("\n".join(notes) if notes else "nothing obviously fake")
'

line "Latency, 5 x GET /status"
for i in 1 2 3 4 5; do curl -s -m 3 -o /dev/null -w '  %{time_total}s\n' "http://$IP:$PORT/status" || echo "  timeout"; done
