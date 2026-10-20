#!/bin/bash
# Run this ON THE OLD JETSON (the 8 GB one the base ran on), where the ROS workspace is.
# It copies everything the new container needs to behave the same into one file:
#   ~/old_jetson_bundle_<date>.tar.gz
# It only reads and copies; nothing on the old Jetson is changed.
#
#   bash collect_from_old_jetson.sh                    # workspace at ~/ros2_ws
#   bash collect_from_old_jetson.sh /path/to/ros2_ws   # workspace somewhere else
#
# If ROS runs inside a Docker container there, run it twice: once on the Jetson itself
# (services, udev, docker settings) and once inside the container (`docker exec -it <name> bash`).
# Each run makes its own file. Send all files.

WS="${1:-$HOME/ros2_ws}"
STAMP="$(date +%Y%m%d_%H%M%S)"
WHERE="$( [ -f /.dockerenv ] && echo container || echo host )"
OUT="$HOME/old_jetson_bundle_${WHERE}_${STAMP}"
mkdir -p "$OUT"
cd "$OUT" || exit 1
echo "Collecting into $OUT (running on the $WHERE)"

run() {  # run <file> <command...>: save the output, never stop on errors
  local file="$1"; shift
  { echo "\$ $*"; "$@"; } >> "$file" 2>&1 || true
}

# 1. System and ROS setup
run system.txt cat /etc/os-release
run system.txt uname -a
run system.txt cat /etc/nv_tegra_release
run system.txt nproc
run system.txt free -h
env | grep -E '^(ROS_|RMW_|need_compile|LIDAR_TYPE|MACHINE_TYPE|DEPTH_CAMERA|HOST=|MASTER=|CYCLONEDDS|FASTRTPS|AMENT_PREFIX_PATH)' \
  | sort > environment.txt
for f in ~/.bashrc ~/.zshrc ~/.profile; do
  [ -f "$f" ] && grep -nE 'export|source|alias' "$f" > "shell_$(basename "$f").txt"
done

# 2. Exact versions
run versions_apt.txt bash -c "dpkg -l | grep -E 'ros-[a-z]+-' | awk '{print \$2, \$3}'"
run versions_pip.txt python3 -m pip freeze
mkdir -p git
for d in "$WS"/src/*/ "$WS"/src/*/*/; do
  if [ -d "$d/.git" ]; then
    name="$(echo "${d#"$WS"/src/}" | tr '/' '_')"
    {
      echo "dir: $d"
      git -C "$d" remote -v
      git -C "$d" rev-parse HEAD
      git -C "$d" status --short
    } > "git/$name.txt" 2>&1
    git -C "$d" diff > "git/$name.diff" 2>/dev/null
  fi
done

# 3. The workspace sources, including maps and calibration files.
# Large model files are listed but not copied.
if [ -d "$WS/src" ]; then
  find "$WS/src" -name '*.onnx' -o -name '*.pt' -o -name '*.db3' -o -name '*.bag' | xargs -r ls -l > large_files_not_copied.txt
  tar -czf ros2_ws_src.tar.gz -C "$WS" \
      --exclude='*.onnx' --exclude='*.pt' --exclude='*.db3' --exclude='*.bag' \
      --exclude='.git' --exclude='__pycache__' src
  ls "$WS/src" > ros2_ws_packages.txt
else
  echo "No workspace at $WS/src" > ros2_ws_packages.txt
fi
# Other workspaces and ROS files in the home folder (e.g. a second workspace, lidar settings)
ls -la ~ > home_listing.txt
mkdir -p home_files
for f in ~/.sllidar_params.yaml ~/.dtb ~/*.yaml ~/*.sh ~/*.py; do
  [ -e "$f" ] && cp -r "$f" home_files/ 2>/dev/null
done

# 4. Services: lidar watchdog, app start, anything ROS-related, and the scripts they run
mkdir -p services
if command -v systemctl > /dev/null; then
  run services/list.txt systemctl list-unit-files --type=service --no-pager
  for svc in $(systemctl list-unit-files --type=service --no-legend --no-pager 2>/dev/null \
               | awk '{print $1}' | grep -iE 'lidar|watchdog|start_app|^ros|ros2|robot|hiwonder|jetacker|jetrover|jetauto|mecanum'); do
    systemctl cat "$svc" --no-pager > "services/$svc" 2>&1
    systemctl status "$svc" --no-pager > "services/$svc.status" 2>&1
    # Copy the scripts the service runs (not system programs from /usr, /bin, /lib)
    grep -hE '^(ExecStart|ExecStartPre|ExecStop)=' "services/$svc" | sed -E 's/^[A-Za-z]+=[-@!+]*//' \
      | tr ' ' '\n' | grep -E '^/(home|opt|etc|root|usr/local)/' | while read -r p; do
        [ -f "$p" ] && [ "$(stat -c %s "$p")" -lt 5000000 ] && cp --parents "$p" services/ 2>/dev/null
      done
  done
fi

# 5. Devices and udev rules
run devices.txt ls -l /dev/serial/by-id/
run devices.txt ls -l /dev/rrc /dev/lidar /dev/ttyACM* /dev/ttyUSB*
mkdir -p udev
cp /etc/udev/rules.d/*.rules udev/ 2>/dev/null

# 6. Docker: how the ROS container was started (image, mounts, devices, env)
if command -v docker > /dev/null; then
  run docker.txt docker ps -a
  run docker.txt docker images
  for c in $(docker ps -aq 2>/dev/null); do
    docker inspect "$c" > "docker_inspect_$c.json" 2>&1
  done
  for f in ~/*compose*.y*ml ~/*/docker-compose.y*ml ~/*/compose.y*ml; do
    [ -f "$f" ] && cp "$f" "home_files/$(echo "${f#"$HOME"/}" | tr '/' '_')"
  done
fi

# 7. What ROS sees right now (only if it is running)
if command -v ros2 > /dev/null; then
  run ros_running.txt timeout 10 ros2 node list
  run ros_running.txt timeout 10 ros2 topic list
  run ros_running.txt timeout 10 ros2 param dump /ekf_filter_node
  run ros_running.txt timeout 10 ros2 param dump /imu_calib
  run ros_running.txt timeout 10 ros2 param dump /sllidar_node
fi

cd "$HOME" && tar -czf "$OUT.tar.gz" "$(basename "$OUT")" && rm -rf "$OUT"
echo
echo "Done: $OUT.tar.gz ($(du -h "$OUT.tar.gz" | cut -f1))"
echo "Send this file. It may contain Wi-Fi or account names from the shell files; check before sharing widely."
