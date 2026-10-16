#!/bin/bash
# Restart the StorePilot rosbridge on the Jetson: stop every old copy (TERM, then KILL), start one.
PATTERN='rosbridge_websocket|rosapi_node|launch rosbridge_server|storepilot_logs/start_rosbridge'
pids() { pgrep -f "$PATTERN" | grep -v -x "$$"; }
pids | xargs -r kill 2>/dev/null
for i in 1 2 3 4 5; do [ -z "$(pids)" ] && break; sleep 1; done
pids | xargs -r kill -9 2>/dev/null
sleep 1
echo "old rosbridge processes left: $(pids | wc -l)"
setsid nohup "$HOME/storepilot_logs/start_rosbridge.sh" > "$HOME/storepilot_logs/rosbridge.log" 2>&1 < /dev/null &
for i in $(seq 1 15); do ss -ltn | grep -q ':9090' && break; sleep 1; done
sleep 2
echo "rosbridge processes now: $(pgrep -f '[r]osbridge_websocket --ros-args' | wc -l)"
ss -ltn | grep -q ':9090' && echo "rosbridge listening on 9090" || echo "rosbridge NOT listening"
grep -i -E 'error|died|already in use' "$HOME/storepilot_logs/rosbridge.log" | head -3
