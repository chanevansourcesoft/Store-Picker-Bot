# mecanum_nav

SLAM and Nav2 for the 4WD mecanum robot, kept **separate from the Ackermann (JetAcker) stack**.
Nothing in `slam/`, `navigation/`, `driver/` or `peripherals/` is modified by this package, so the
Ackermann robot still runs exactly as before from its own launch files.

## Why mecanum needs its own config

A mecanum wheel has free-spinning rollers set at 45 degrees around its rim, so driving the hub
pushes at 45 degrees rather than straight ahead. With four wheels (rollers mirrored left/right to
form an X seen from above) those 45 degree components add or cancel:

| wheel pattern | result |
|---|---|
| all four forward | drive forward |
| left pair forward, right pair back | rotate in place |
| FL + RR forward, FR + RL back | **strafe right** |
| FL + RR back, FR + RL forward | **strafe left** |

So the robot has three independent degrees of freedom (vx, vy, yaw) instead of two, and it can
translate sideways at 90 degrees without changing heading. Almost every car-specific assumption in
the Ackermann config actively removes that ability — see the `MECANUM:` comments in each config file
for the specific reason behind each change.

## Layout

```
config/
  mecanum_base.yaml      geometry + limits + the cmd_vel timeout. ONE source of truth.
  ekf.yaml               Ackermann EKF with the y axis enabled throughout
  slam.yaml              copy of the tuned slam_toolbox config (scan matching is not kinematic)
  nav2_params.yaml       OmniMotionModel AMCL, SmacPlanner2D, centred footprint, y in the smoother
  nav2_controller_dwb.yaml   DWB with strafe sampling enabled
  navigate_to_pose_w_replanning_and_recovery_mecanum.xml   BT with Spin restored
launch/
  robot_description.launch.py   TF from mecanum_robot.urdf
  base.launch.py                board + mecanum_base + TF + scan filter + EKF   <- teleop needs this
  teleop.launch.py              wrapper; prefer running the script directly
  slam.launch.py                base + slam_toolbox
  navigation.launch.py          base + AMCL + Nav2
scripts/
  mecanum_kinematics.py         twist <-> four wheel speeds
  mecanum_base.py               cmd_vel -> motors, dead-reckoned odom, command timeout
  teleop_mecanum.py             holonomic keyboard teleop
  check_motor_mapping.py        which motor id is which wheel, and which way round
  check_lidar_orientation.py    is lidar_frame's yaw correct (use --diff)
  stop_motors.py                emergency stop straight over serial
```

## Quickstart

```bash
# 0. nothing else may be driving the motors -- see "Conflicts" below
pkill -f 'ros2 launch'; python3 scripts/stop_motors.py

# 1. confirm all four wheels are actually driven and find the id -> wheel map
python3 scripts/check_motor_mapping.py      # wheels OFF the ground

# 2. bring up the base (terminal 1)
ros2 launch mecanum_nav base.launch.py

# 3. teleop (terminal 2)
python3 scripts/teleop_mecanum.py
```

`w/s` forward/back, **`a/d` strafe left/right**, `q/e` rotate, `x` stop, `z/c` slower/faster.

## Saving a map

slam_toolbox writes relative to the NODE's working directory, not to slam/maps, and answers
result=0 either way -- so a bare name looks like it saved and then cannot be found. Always pass
the full path:

```bash
ros2 service call /slam_toolbox/save_map slam_toolbox/srv/SaveMap \
  "{name: {data: '/home/ubuntu/ros2_ws/src/slam/maps/my_map'}}"
```

Load it with `ros2 launch mecanum_nav navigation.launch.py map:=my_map` (name only).

## Conflicts

Only one thing may publish `ros_robot_controller/set_motor`. The Ackermann `odom_publisher` (from
`controller.launch.py`, pulled in by `bringup.launch.py`, `slam/launch/slam.launch.py` and
`navigation/launch/navigation*.launch.py`) publishes to the same topic. Run one stack or the other,
never both: with two publishers the board receives interleaved commands from both and the robot
does neither thing.

## Safety

`mecanum_base` stops the wheels if no command arrives for `cmd_vel_timeout` (0.5 s), and zeroes
them on every exit path. This matters because the controller board **latches** the last motor
speed indefinitely — it has no concept of a stale command. The stock
`ros_robot_controller_node.py` only zeroes motors on `KeyboardInterrupt`, so killing it any other
way (SIGTERM from a launch teardown, SIGKILL, a crash) leaves the wheels driving with nothing
left running to stop them. If that happens: `python3 scripts/stop_motors.py`, or the power switch.
