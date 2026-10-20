#!/bin/bash
# Sets up ROS in the container, then runs the given command (default: bash).
set -e

source /opt/ros/humble/setup.bash
source /opt/ros_deps/install/setup.bash
if [ -f /home/ubuntu/ros2_ws/install/setup.bash ]; then
  source /home/ubuntu/ros2_ws/install/setup.bash
elif [ "$1" != "colcon" ]; then
  echo "The workspace is not built yet. Build it once with:" >&2
  echo "  docker compose run --rm ros colcon build --symlink-install" >&2
fi

# The launch files read these. Set them in the repo root .env to change them.
export LIDAR_TYPE="${LIDAR_TYPE:-A1}"
export MACHINE_TYPE="${MACHINE_TYPE:-JetAcker}"
export HOST="${HOST:-/}"
export MASTER="${MASTER:-/}"

exec "$@"
