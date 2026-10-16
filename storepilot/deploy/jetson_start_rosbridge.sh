#!/usr/bin/zsh
# Run ON THE JETSON. Starts rosbridge for the StorePilot dashboard with an allow-list:
# only these topics / services are reachable, so /cmd_vel can never be published through it.
# The glob values are STRINGS that rosbridge splits itself, hence the '"[...]"' quoting.
source ~/ros2_ws/.robotrc
exec ros2 launch rosbridge_server rosbridge_websocket_launch.xml port:=9090 \
  topics_glob:='"[/map, /amcl_pose, /tf_static, /odom, /scan, /plan, /ros_robot_controller/battery, /navigate_to_pose/_action/status, /goal_pose, /initialpose]"' \
  services_glob:='"[/lifecycle_manager_navigation/*, /lifecycle_manager_localization/*, /navigate_to_pose/_action/cancel_goal, /map_server/load_map, /request_nomotion_update]"' \
  params_glob:='"[]"'
