#!/usr/bin/env bash
# Run on the Jetson. READ-ONLY: it publishes nothing, calls no services, sends no robot commands.
#
#   bash jetson_collect_info.sh [extra ROS setup.bash ...]
#   e.g. bash jetson_collect_info.sh ~/nav2_ws/install/setup.bash
#
# If ROS 2 runs inside a Docker container, run it inside that container
# (docker exec -it <container> bash) as well as on the host.
# Writes ~/storepilot_jetson_report_<time>.txt; send that file back.
set -u
OUT="$HOME/storepilot_jetson_report_$(date +%Y%m%d_%H%M%S).txt"
exec > >(tee "$OUT") 2>&1
line() { printf '\n==== %s ====\n' "$1"; }
run() { echo "\$ $*"; timeout "${T:-8}" "$@" 2>&1 | head -"${N:-60}"; echo; }

line "System"
run hostname
run cat /etc/nv_tegra_release
run uname -a
run ip -4 -br addr
run ip route
echo "ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-unset} ROS_LOCALHOST_ONLY=${ROS_LOCALHOST_ONLY:-unset} RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION:-unset}"

line "Robot server process and ports"
run ss -ltnp
N=20 run bash -c 'ps -eo pid,etime,cmd | grep -E "jetson_server|uvicorn|rosbridge|nav2|lerobot|teensy|micro_ros" | grep -v grep'
run bash -c 'systemctl list-units --type=service --no-pager | grep -i -E "robot|ros|nav|jetson|teensy" || echo "no matching system services"'
run bash -c 'systemctl --user list-units --type=service --no-pager 2>/dev/null | grep -i -E "robot|ros|nav|jetson" || echo "no matching user services"'
run bash -c 'docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}" 2>&1'

line "Robot server answers (GET only, from the Jetson itself)"
for path in /health /status /map; do run curl -s -m 3 "http://127.0.0.1:8000$path"; done
run bash -c 'curl -s -m 3 http://127.0.0.1:8000/openapi.json | python3 -c "import json,sys; [print(\",\".join(m.upper() for m in o), p) for p,o in sorted(json.load(sys.stdin)[\"paths\"].items())]"'

line "jetson_server.py copies (is it the repo version, unchanged?)"
N=30 run bash -c 'find / -name jetson_server.py -not -path "*/proc/*" 2>/dev/null | while read f; do sha256sum "$f"; d=$(dirname "$f"); git -C "$d" log -1 --format="  git %h %cd %s" 2>/dev/null; git -C "$d" status --short -- "$f" 2>/dev/null | sed "s/^/  local change: /"; done'
N=40 run bash -c 'for f in $(find / -name stations.yaml -not -path "*/proc/*" 2>/dev/null); do echo "--- $f"; cat "$f"; done'

line "ROS 2 environment"
# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash 2>/dev/null || echo "no /opt/ros/humble/setup.bash"
for f in "$@"; do source "$f" && echo "sourced $f"; done
run ros2 doctor --report
N=80 run ros2 node list
N=150 run ros2 topic list -t
N=60 run ros2 action list -t
N=80 run ros2 service list

line "Who drives the wheels (/cmd_vel publishers and subscribers)"
for t in $(ros2 topic list 2>/dev/null | grep -i cmd_vel); do N=40 run ros2 topic info -v "$t"; done

line "Battery"
N=30 run ros2 topic info -v /battery_state
N=30 run ros2 topic echo --once /battery_state

line "Pose and speed sources"
N=20 run ros2 topic echo --once /odom --field twist.twist
T=10 N=10 run ros2 topic hz /odom
N=30 run ros2 topic echo --once /amcl_pose
T=6 N=20 run ros2 run tf2_ros tf2_echo map base_link
T=6 N=20 run ros2 run tf2_ros tf2_echo odom base_link

line "LiDAR"
N=15 run ros2 topic echo --once /scan --no-arr
T=10 N=10 run ros2 topic hz /scan

line "Nav2 lifecycle"
for n in /bt_navigator /controller_server /planner_server /amcl /map_server; do T=5 run ros2 lifecycle get "$n"; done

line "Arm, gripper, Teensy, e-stop related topics"
run bash -c 'ros2 topic list 2>/dev/null | grep -i -E "joint|arm|gripper|follower|so100|teensy|motor|wheel|estop|e_stop|emergency|load|scale|barcode" || echo "none found"'

line "Hardware"
run ls -l /dev/serial/by-id/
run bash -c 'ls /dev/ttyACM* /dev/ttyUSB* /dev/video* 2>&1'
run lsusb

echo
echo "Report saved to $OUT"
