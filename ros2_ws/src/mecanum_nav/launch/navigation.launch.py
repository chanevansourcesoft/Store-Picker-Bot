"""Nav2 for the mecanum robot: base.launch.py + AMCL + the holonomic Nav2 stack.

    ros2 launch mecanum_nav navigation.launch.py
    ros2 launch mecanum_nav navigation.launch.py map:=my_map

Reads maps from slam/maps/<map>.yaml (shared with the Ackermann stack -- a map is a map,
independent of what drove around to build it).

The holonomic configuration lives in config/nav2_params.yaml and
config/nav2_controller_dwb.yaml; each mecanum-specific setting is marked "MECANUM:" with its
reason. In short: AMCL uses OmniMotionModel, the planner is SmacPlanner2D instead of
heading-constrained Hybrid-A*, DWB samples sideways velocities, the velocity smoother no longer
zeroes y, the footprint is the real centred chassis, and the behaviour tree has Spin back.

The cmd_vel chain is unchanged from the Ackermann stack:
    controller -> cmd_vel_nav -> velocity_smoother -> cmd_vel_smoothed -> collision_monitor
      -> /controller/cmd_vel -> mecanum_base
Every stage carries the full planar twist, so strafe survives end to end.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription, LaunchService
from launch.actions import (DeclareLaunchArgument, GroupAction, IncludeLaunchDescription,
                            OpaqueFunction, SetEnvironmentVariable, TimerAction)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import LoadComposableNodes, Node
from launch_ros.descriptions import ComposableNode

SRC = '/home/ubuntu/ros2_ws/src'

LIFECYCLE_NODES = ['controller_server',
                   'smoother_server',
                   'planner_server',
                   'behavior_server',
                   'bt_navigator',
                   'waypoint_follower',
                   'velocity_smoother',
                   'collision_monitor']


def launch_setup(context):
    compiled = os.environ['need_compile']

    map_name = LaunchConfiguration('map', default='map_01').perform(context)
    use_sim_time = LaunchConfiguration('use_sim_time', default='false')
    autostart = LaunchConfiguration('autostart', default='true')
    log_level = LaunchConfiguration('log_level', default='info')
    use_rviz = LaunchConfiguration('use_rviz', default='false')
    speed = float(LaunchConfiguration('speed', default='0.12').perform(context))

    map_arg = DeclareLaunchArgument('map', default_value=map_name)
    use_sim_time_arg = DeclareLaunchArgument('use_sim_time', default_value=use_sim_time)
    autostart_arg = DeclareLaunchArgument('autostart', default_value=autostart)
    log_level_arg = DeclareLaunchArgument('log_level', default_value=log_level)
    use_rviz_arg = DeclareLaunchArgument('use_rviz', default_value=use_rviz)
    speed_arg = DeclareLaunchArgument('speed', default_value='0.12')
    axis_lock_arg = DeclareLaunchArgument('axis_lock', default_value='true')

    if compiled == 'True':
        mecanum_path = get_package_share_directory('mecanum_nav')
        slam_path = get_package_share_directory('slam')
        navigation_path = get_package_share_directory('navigation')
    else:
        mecanum_path = os.path.join(SRC, 'mecanum_nav')
        slam_path = os.path.join(SRC, 'slam')
        navigation_path = os.path.join(SRC, 'navigation')

    params_file = os.path.join(mecanum_path, 'config/nav2_params.yaml')
    controller_params = os.path.join(mecanum_path, 'config/nav2_controller_dwb.yaml')
    map_yaml = os.path.join(slam_path, 'maps', map_name + '.yaml')

    remappings = [('/tf', 'tf'), ('/tf_static', 'tf_static')]

    # One speed knob, and nothing else. An earlier version of this file also overrode the DWB
    # critics, the goal checker and the acceleration limits from here; that was reverted on
    # 2026-10-14 after it made the robot wander instead of heading for the goal. Shaping the
    # controller's BEHAVIOUR belongs in config/nav2_controller_dwb.yaml, where it can be read in
    # one place. This only scales the speed limits, which is the one thing worth changing per
    # run without editing a file.
    # Strafe is kept a little below forward because mecanum rollers slip more sideways; the
    # smoother sits 1.3x above both so it clips nothing during normal driving.
    import math
    strafe = speed * 0.8
    yaw = max(0.4, speed * 4.0)
    speed_overrides = {
        'FollowPath.max_vel_x': speed,
        'FollowPath.min_vel_x': -speed * 0.8,
        'FollowPath.max_vel_y': strafe,
        'FollowPath.min_vel_y': -strafe,
        'FollowPath.max_vel_theta': yaw,
        # Must exceed sqrt(vx^2 + vy^2) or a commanded diagonal gets clipped unevenly and curves.
        'FollowPath.max_speed_xy': math.hypot(speed, strafe) * 1.05,
    }
    smoother_overrides = {
        'max_velocity': [speed * 1.3, strafe * 1.3, yaw * 1.3],
        'min_velocity': [-speed * 1.3, -strafe * 1.3, -yaw * 1.3],
    }

    base_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(mecanum_path, 'launch/base.launch.py')),
        launch_arguments={'use_sim_time': use_sim_time,
                          'axis_lock': LaunchConfiguration('axis_lock', default='true')}.items(),
    )

    container = Node(
        name='nav2_container',
        package='rclcpp_components',
        executable='component_container_isolated',
        parameters=[params_file, {'autostart': autostart}],
        arguments=['--ros-args', '--log-level', log_level],
        remappings=remappings,
        output='screen',
    )

    # map_server + AMCL. Reused from the Ackermann package unchanged: it is generic lifecycle
    # plumbing, and the mecanum-specific AMCL settings come from params_file.
    localization_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(navigation_path, 'launch/include/localization.launch.py')),
        launch_arguments={'namespace': '',
                          'map': map_yaml,
                          'params_file': params_file,
                          'use_sim_time': use_sim_time,
                          'use_namespace': 'false',
                          'autostart': autostart}.items(),
    )

    nav2_nodes = LoadComposableNodes(
        target_container='nav2_container',
        composable_node_descriptions=[
            ComposableNode(
                package='nav2_controller',
                plugin='nav2_controller::ControllerServer',
                name='controller_server',
                parameters=[controller_params, speed_overrides],
                remappings=remappings + [('cmd_vel', 'cmd_vel_nav')]),
            ComposableNode(
                package='nav2_smoother',
                plugin='nav2_smoother::SmootherServer',
                name='smoother_server',
                parameters=[params_file],
                remappings=remappings),
            ComposableNode(
                package='nav2_planner',
                plugin='nav2_planner::PlannerServer',
                name='planner_server',
                parameters=[params_file],
                remappings=remappings),
            ComposableNode(
                package='nav2_behaviors',
                plugin='behavior_server::BehaviorServer',
                name='behavior_server',
                parameters=[params_file],
                remappings=remappings),
            ComposableNode(
                package='nav2_bt_navigator',
                plugin='nav2_bt_navigator::BtNavigator',
                name='bt_navigator',
                parameters=[params_file],
                remappings=remappings),
            ComposableNode(
                package='nav2_waypoint_follower',
                plugin='nav2_waypoint_follower::WaypointFollower',
                name='waypoint_follower',
                parameters=[params_file],
                remappings=remappings),
            ComposableNode(
                package='nav2_velocity_smoother',
                plugin='nav2_velocity_smoother::VelocitySmoother',
                name='velocity_smoother',
                parameters=[params_file, smoother_overrides],
                remappings=remappings + [('cmd_vel', 'cmd_vel_nav')]),
            ComposableNode(
                package='nav2_collision_monitor',
                plugin='nav2_collision_monitor::CollisionMonitor',
                name='collision_monitor',
                parameters=[params_file],
                remappings=remappings),
            ComposableNode(
                package='nav2_lifecycle_manager',
                plugin='nav2_lifecycle_manager::LifecycleManager',
                name='lifecycle_manager_navigation',
                parameters=[{'use_sim_time': use_sim_time,
                             'autostart': autostart,
                             'node_names': LIFECYCLE_NODES}]),
        ],
    )

    rviz_node = Node(
        package='rviz2',
        executable='rviz2',
        name='rviz2',
        output='screen',
        arguments=['-d', os.path.join(mecanum_path, 'rviz/navigation.rviz')],
        condition=IfCondition(use_rviz),
    )

    return [map_arg,
            use_sim_time_arg,
            autostart_arg,
            log_level_arg,
            use_rviz_arg,
            speed_arg,
            axis_lock_arg,
            rviz_node,
            SetEnvironmentVariable('RCUTILS_LOGGING_BUFFERED_STREAM', '1'),
            base_launch,
            GroupAction([
                TimerAction(period=10.0,
                            actions=[container, localization_launch, nav2_nodes]),
            ])]


def generate_launch_description():
    return LaunchDescription([OpaqueFunction(function=launch_setup)])


if __name__ == '__main__':
    ld = generate_launch_description()
    ls = LaunchService()
    ls.include_launch_description(ld)
    ls.run()
