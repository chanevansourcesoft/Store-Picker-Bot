# ros2_ws

ROS 2 workspace for the mobile base and order orchestration.

| Package | Role |
|---|---|
| `robot_bringup` | Launch files, Nav2 config, saved map |
| `base_driver` | `cmd_vel` → wheel motors; encoders → odometry |
| `dock_controller` | Final approach into the dock guide; limit switch |
| `tray_scale` | Load cell → tray weight |
| `task_executor` | Orchestrator: navigation, docking, pick calls, weight checks, retries |
