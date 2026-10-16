#!/bin/bash
# StorePilot control on the Jetson. Called by the Pi over SSH; also fine to run by hand.
#   status | bridge-restart | nav-start <map> <speed> | nav-stop | amcl-recovery on|off
# Robot code in ~/ros2_ws is never modified. Logs: ~/storepilot_jetson/logs/
set -u
DIR="$HOME/storepilot_jetson"; LOGS="$DIR/logs"; MAPS="$HOME/ros2_ws/src/slam/maps"
mkdir -p "$LOGS"
ros() { zsh -c "source ~/ros2_ws/.robotrc >/dev/null 2>&1; $*"; }
pids_of() { pgrep -f "$1" | grep -v -x "$$" || true; }
nav_pid() { pgrep -f "ros2 launch mecanum_nav navigation.launch.py" | head -1; }
NAV_NODES='component_container_isolated|mecanum_base.py|ros_robot_controller|ekf_node|rf2o_laser_odometry|imu_filter|imu_calib|scan_to_scan_filter_chain|robot_state_publisher|joint_state_publisher'

start_services() {
  setsid nohup "$DIR/start_rosbridge.sh" > "$LOGS/rosbridge.log" 2>&1 < /dev/null &
  setsid nohup zsh -c "source ~/ros2_ws/.robotrc >/dev/null 2>&1; exec python3 $DIR/storepilot_watchdog.py" \
    > "$LOGS/watchdog.log" 2>&1 < /dev/null &
}
stop_services() {
  local p
  p=$(pids_of 'rosbridge_websocket|rosapi_node|launch rosbridge_server|start_rosbridge.sh|storepilot_watchdog.py')
  [ -n "$p" ] && kill $p 2>/dev/null
  for i in 1 2 3 4 5; do [ -z "$(pids_of 'rosbridge_websocket|storepilot_watchdog.py')" ] && break; sleep 1; done
  p=$(pids_of 'rosbridge_websocket|rosapi_node|launch rosbridge_server|start_rosbridge.sh|storepilot_watchdog.py')
  [ -n "$p" ] && kill -9 $p 2>/dev/null
  true
}

case "${1:-status}" in
  status)
    np=$(nav_pid); cmd=""; [ -n "$np" ] && cmd=$(tr '\0' ' ' < /proc/$np/cmdline)
    map=$(echo "$cmd" | grep -o 'map:=[^ ]*' | cut -d= -f2); speed=$(echo "$cmd" | grep -o 'speed:=[^ ]*' | cut -d= -f2)
    up=""; [ -n "$np" ] && up=$(ps -o etimes= -p "$np" | tr -d ' ')
    rb=$(pids_of 'rosbridge_websocket --ros-args' | wc -l); wd=$(pids_of 'storepilot_watchdog.py' | grep -c . || true)
    # maps with a readable image: [{"name", "origin": [x, y]}]; broken ones (empty image) are left out
    maps=$(python3 - "$MAPS" <<'PY'
import json, os, sys, glob
out = []
for y in sorted(glob.glob(os.path.join(sys.argv[1], "*.yaml"))):
    try:
        info = {}
        for line in open(y):
            if ":" in line:
                k, v = line.split(":", 1)
                info[k.strip()] = v.strip()
        img = os.path.join(os.path.dirname(y), info.get("image", "").strip("'\""))
        if not os.path.isfile(img) or os.path.getsize(img) == 0:
            continue
        origin = json.loads(info.get("origin", "[0,0,0]"))
        out.append({"name": os.path.basename(y)[:-5], "origin": origin[:2]})
    except Exception:
        continue
print(json.dumps(out))
PY
)
    load=$(cut -d' ' -f1 /proc/loadavg)
    printf '{"nav_running": %s, "nav_pid": "%s", "nav_uptime_s": "%s", "map": "%s", "speed": "%s", "rosbridge": %s, "watchdog": %s, "maps": %s, "load": "%s"}\n' \
      "$([ -n "$np" ] && echo true || echo false)" "$np" "$up" "$map" "$speed" "$([ "$rb" -ge 1 ] && echo true || echo false)" \
      "$([ "${wd:-0}" -ge 1 ] && echo true || echo false)" "$maps" "$load" ;;
  bridge-restart)
    stop_services; sleep 1; start_services
    for i in $(seq 1 20); do ss -ltn | grep -q ':9090' && break; sleep 1; done
    ss -ltn | grep -q ':9090' && echo "OK rosbridge and watchdog restarted" || { echo "ERROR rosbridge did not start"; exit 1; } ;;
  nav-start)
    map="${2:-}"; speed="${3:-0.10}"
    [[ "$map" =~ ^[A-Za-z0-9_]+$ ]] && [ -f "$MAPS/$map.yaml" ] || { echo "ERROR unknown map '$map'"; exit 1; }
    awk -v s="$speed" 'BEGIN{exit !(s>=0.05 && s<=0.30)}' || { echo "ERROR speed must be 0.05-0.30"; exit 1; }
    [ -n "$(nav_pid)" ] && { echo "ERROR navigation is already running (stop it first)"; exit 1; }
    setsid nohup zsh -c "source ~/ros2_ws/.robotrc >/dev/null 2>&1; exec ros2 launch mecanum_nav navigation.launch.py map:=$map speed:=$speed" \
      > "$LOGS/navigation.log" 2>&1 < /dev/null &
    # Nav2's navigation half only activates once AMCL has a pose (map->odom TF), so wait for localization
    for i in $(seq 1 30); do
      ros "timeout 3 ros2 service call /lifecycle_manager_localization/is_active std_srvs/srv/Trigger" 2>/dev/null | grep -q "success=True" && { echo "OK navigation started on $map at $speed m/s: set the robot position"; exit 0; }
      sleep 2
    done
    echo "ERROR localization (map_server + AMCL) did not become active in 60 s (see $LOGS/navigation.log)"; exit 1 ;;
  nav-stop)
    np=$(nav_pid)
    [ -z "$np" ] && { echo "OK navigation was not running"; exit 0; }
    kill -INT "$np"
    for i in $(seq 1 20); do kill -0 "$np" 2>/dev/null || break; sleep 1; done
    kill -0 "$np" 2>/dev/null && kill -TERM "$np"
    sleep 2
    left=$(pids_of "$NAV_NODES"); [ -n "$left" ] && kill $left 2>/dev/null && sleep 2
    left=$(pids_of "$NAV_NODES"); [ -n "$left" ] && kill -9 $left 2>/dev/null
    echo "OK navigation stopped" ;;
  amcl-recovery)
    if [ "${2:-}" = "on" ]; then f=0.1; s=0.001; else f=0.0; s=0.0; fi
    ros "ros2 param set /amcl recovery_alpha_fast $f && ros2 param set /amcl recovery_alpha_slow $s" 2>&1 | grep -v -E "^\s*$" | tail -2
    ros "ros2 param get /amcl recovery_alpha_fast; ros2 param get /amcl recovery_alpha_slow" 2>&1 | grep -o 'value is: .*' ;;
  amcl-get)
    ros "ros2 param get /amcl recovery_alpha_fast; ros2 param get /amcl recovery_alpha_slow" 2>&1 | grep -o 'value is: .*' ;;
  *) echo "usage: $0 status|bridge-restart|nav-start <map> <speed>|nav-stop|amcl-recovery on|off|amcl-get"; exit 2 ;;
esac
