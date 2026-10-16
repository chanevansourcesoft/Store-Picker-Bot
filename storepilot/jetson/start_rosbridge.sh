#!/usr/bin/zsh
# Run ON THE JETSON. rosbridge for the StorePilot dashboard with an allow-list:
# only these topics / services are reachable, so /cmd_vel can never be published through it.
# The glob values are STRINGS that rosbridge splits itself, hence the '"[...]"' quoting.
source ~/ros2_ws/.robotrc
# call_services_in_new_thread: a slow ROS service (e.g. Nav2's lifecycle manager while it waits for a pose)
# must never block the Pi's other messages (heartbeat, STOP). default_call_service_timeout: never wait forever.
exec ros2 launch rosbridge_server rosbridge_websocket_launch.xml port:=9090 \
  call_services_in_new_thread:=true send_action_goals_in_new_thread:=true default_call_service_timeout:=5.0 \
  topics_glob:='"[/map, /amcl_pose, /tf_static, /odom, /scan, /plan, /ros_robot_controller/battery, /navigate_to_pose/_action/status, /goal_pose, /initialpose, /local_costmap/costmap, /local_costmap/published_footprint, /storepilot/heartbeat, /storepilot/watchdog]"' \
  services_glob:='"[/lifecycle_manager_navigation/*, /lifecycle_manager_localization/*, /navigate_to_pose/_action/cancel_goal, /map_server/load_map, /request_nomotion_update]"' \
  params_glob:='"[]"'
