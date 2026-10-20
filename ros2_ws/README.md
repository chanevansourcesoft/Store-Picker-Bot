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

The lidar runs under a watchdog, [tools/lidar_watchdog.py](tools/lidar_watchdog.py). It starts the
A1 driver and restarts it when `/scan_raw` stops for 5 seconds or the driver quits, for example
after the lidar is unplugged and plugged back in. Ctrl+C stops the driver cleanly.

```bash
# With Docker (Jetson): runs in the background, starts again after a reboot
docker compose up -d lidar
docker compose logs -f lidar        # Ctrl+C stops the logs only
docker compose stop lidar           # stop it

# Without Docker (PC)
python3 tools/lidar_watchdog.py
```

On the robot's own image, `lidar_watchdog.service` already runs the lidar. Do not start a second
driver on the same computer: only one program can hold the port.

## 4. Drive the base

Wheels off the ground the first time. Two terminals.

Keep the robot still for the first 2 seconds after the launch starts: the IMU measures its gyro
bias then (see "IMU bias" below).

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

The base used to run on an 8 GB Jetson whose files could not be copied. What it had that this repo
lacked was rebuilt here:

- **IMU calibration** (`calibration`, `imu_calib`): replaced by the `imu_bias` node (see Notes).
- **`lidar_watchdog.service`**: replaced by [tools/lidar_watchdog.py](tools/lidar_watchdog.py),
  which the `lidar` service in Docker runs (see section 3).
- **Saved maps**: a new map of the store area is mapped here (see mecanum_nav/README.md).

## Notes

- **IMU bias:** the robot's own image used the `calibration` and `imu_calib` packages, which are
  not in this repo. Here `peripherals`' `imu_bias` node does the same job: at startup it averages
  the first 100 gyro readings (about 2 s, robot standing still), then publishes the IMU with that
  bias removed on `imu_corrected`, which the Madgwick filter reads. Its log shows
  `Gyro bias (rad/s): ...`. If the robot moved during those 2 s, start the launch again.
  An accel calibration in `imu_calib`'s format (`SM` and `bias`) can be passed with
  `accel_calib_file:=/path/to/file.yaml` to `peripherals imu_filter.launch.py`; without one the
  accel values pass through. When `imu_calib` is installed (the robot's own image), it is used
  instead.
- **Lidar types:** the image builds only the A1 driver (`sllidar_ros2`). For G4 or LD19, add
  `ydlidar_ros2_driver` or `ldlidar_stl_ros2` to the Dockerfile.
- **Not in the image:** RViz (run it on a PC on the same network with the same `ROS_DOMAIN_ID`),
  RTAB-Map and the depth camera driver.
- **Other containers:** host network and IPC, so ROS nodes in the policy container or on the host
  see these topics when they use the same `ROS_DOMAIN_ID`.
