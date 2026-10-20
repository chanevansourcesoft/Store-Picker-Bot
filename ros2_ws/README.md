# ros2_ws

ROS 2 Humble workspace for the mobile base and order orchestration.

| Package | Role |
|---|---|
| `mecanum_nav` | Mecanum base: `cmd_vel` → motors, odometry, teleop, SLAM and Nav2. Start here: [mecanum_nav/README.md](src/mecanum_nav/README.md) |
| `driver` | Controller board node and SDK (motors, IMU), messages |
| `peripherals` | Lidar, IMU filter, depth camera, teleop launch files |
| `slam`, `navigation` | slam_toolbox / RTAB-Map and Nav2 launch files, saved maps |
| `interfaces` | Shared messages and services |
| `simulations/jetacker_description` | Robot model (URDF) |
| `robot_bringup`, `base_driver`, `dock_controller`, `tray_scale`, `task_executor` | Planned, still empty |

## 1. Set the ports

The controller board and the lidar are found by their stable names, like the arms.

1. List the serial devices, plug in the board's USB cable, and list again:
   ```bash
   ls -l /dev/serial/by-id/
   ```
   The new line is the board. Do the same with the lidar (an A1 shows up as `Silicon_Labs_CP2102`).
   The board and the arms all start with `usb-1a86_`, so compare the lists, not the names.
2. In the repo root `.env` (copy it from `.env.example` if it does not exist), fill in:
   ```bash
   BASE_BOARD_PORT=/dev/serial/by-id/usb-1a86_...-if00
   LIDAR_PORT=/dev/serial/by-id/usb-Silicon_Labs_CP2102_...-if00-port0
   LIDAR_TYPE=A1
   ROS_DOMAIN_ID=0
   ```
   Left empty, the ports fall back to `/dev/rrc` and `/dev/lidar`, which only exist if the udev
   rules from `src/peripherals/scripts/` are installed.

The SO-100 arms are also `ttyACM` devices. Always use the `by-id` names, so the base never opens an arm.

## 2. Start a terminal

```bash
# With Docker (Jetson). Build the image once (about 10 minutes), then build the workspace once:
cd path/to/Store-Picker-Bot/ros2_ws
docker compose build
docker compose run --rm ros colcon build --symlink-install

# Without Docker (PC or the robot's own computer, ROS 2 Humble installed):
cd path/to/Store-Picker-Bot/ros2_ws
colcon build --symlink-install
source install/setup.bash
set -a; source ../.env; set +a
export need_compile=True
```

Build again (`colcon build --symlink-install`) after adding or renaming files. Edits to existing
Python files and configs work without a rebuild. After changing `.env`: with Docker nothing to do;
without Docker run the `set -a; source ../.env; set +a` line again.

Without Docker, also install the drivers the Dockerfile builds: `sllidar_ros2` and
`rf2o_laser_odometry` (see the `git clone` lines in [Dockerfile](Dockerfile)), and the apt packages
listed there.

## 3. Start the lidar

```bash
# With Docker (Jetson): runs in the background and restarts by itself
docker compose up -d lidar
docker compose logs -f lidar        # Ctrl+C stops the logs only

# Without Docker (PC)
ros2 launch peripherals sllidar_a1.launch.py
```

On the robot's own image, `lidar_watchdog.service` already runs the lidar. Do not start a second
driver on the same computer: only one program can hold the port.

## 4. Drive the base

Wheels off the ground the first time. Two terminals:

```bash
# Terminal 1: board, base, TF, scan filter, odometry
# With Docker (Jetson)
docker compose run --rm ros ros2 launch mecanum_nav base.launch.py
# Without Docker (PC)
ros2 launch mecanum_nav base.launch.py

# Terminal 2: keyboard teleop (w/s forward/back, a/d sideways, q/e turn, x stop)
# With Docker (Jetson)
docker compose run --rm ros python3 src/mecanum_nav/scripts/teleop_mecanum.py
# Without Docker (PC)
python3 src/mecanum_nav/scripts/teleop_mecanum.py
```

If a wheel keeps turning after a crash, stop it straight over the serial port:

```bash
docker compose run --rm ros python3 src/mecanum_nav/scripts/stop_motors.py   # With Docker (Jetson)
python3 src/mecanum_nav/scripts/stop_motors.py                               # Without Docker (PC)
```

SLAM, maps and Nav2: see [mecanum_nav/README.md](src/mecanum_nav/README.md). With Docker, put
`docker compose run --rm ros` in front of each command. After saving a new map, run
`colcon build --symlink-install` once more: Nav2 only finds maps that were there at the last build.

## Moving from the old Jetson

The base used to run on an 8 GB Jetson. Some things it used are not in this repo yet: the IMU
calibration, the lidar watchdog, the saved maps and the exact package versions. To bring them over:

1. Copy [tools/collect_from_old_jetson.sh](tools/collect_from_old_jetson.sh) to the old Jetson
   (USB stick or `scp`) and run it there:
   ```bash
   bash collect_from_old_jetson.sh               # workspace at ~/ros2_ws
   ```
   If ROS runs in a Docker container on that Jetson, run it once on the Jetson and once inside the
   container (`docker exec -it <name> bash`).
2. It only reads and copies. It writes `~/old_jetson_bundle_<...>.tar.gz`: send that file.

The file is then used to add the missing packages, the watchdog and the maps here, and to pin the
same versions in the Dockerfile.

## Notes

- **IMU calibration:** the `calibration` and `imu_calib` packages come with the robot's own image
  and are not in this repo. Without them the IMU filter uses the raw IMU (the gyro bias is not
  removed first), and the launch prints a line saying so.
- **Lidar types:** the image builds only the A1 driver (`sllidar_ros2`). For G4 or LD19, add
  `ydlidar_ros2_driver` or `ldlidar_stl_ros2` to the Dockerfile.
- **Not in the image:** RViz (run it on a PC on the same network with the same `ROS_DOMAIN_ID`),
  RTAB-Map and the depth camera driver.
- **Other containers:** host network and IPC, so ROS nodes in the policy container or on the host
  see these topics when they use the same `ROS_DOMAIN_ID`.
