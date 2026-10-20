"""SLAM (slam_toolbox) for the mecanum robot: base.launch.py + slam_toolbox.

    ros2 launch mecanum_nav slam.launch.py

Then drive with teleop and watch the map build in RViz (Fixed Frame: map, add Map + LaserScan).
Save with the FULL PATH in the name:

    ros2 service call /slam_toolbox/save_map slam_toolbox/srv/SaveMap \
      "{name: {data: '/home/ubuntu/ros2_ws/src/slam/maps/my_map'}}"

The full path matters. slam_toolbox writes relative to the NODE's working directory, not to
slam/maps, so a bare name like 'my_map' lands wherever the launch happened to be run from and
navigation.launch.py will not find it. The service still answers result=0 either way, so a
bare name looks like it worked.

navigation.launch.py reads them back as  map:=my_map  (name only, no path, no extension).

Mecanum note: strafing is genuinely useful while mapping. It lets you sweep sideways along a wall
keeping the lidar square to it, which gives the scan matcher cleaner geometry than the oblique
views you get from a car-like robot that must arc past everything.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription, LaunchService
from launch.actions import (DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction,
                            TimerAction)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from nav2_common.launch import RewrittenYaml

SRC = '/home/ubuntu/ros2_ws/src'


def launch_setup(context):
    compiled = os.environ['need_compile']

    use_sim_time = LaunchConfiguration('use_sim_time', default='false')
    enable_save = LaunchConfiguration('enable_save', default='true').perform(context)

    use_sim_time_arg = DeclareLaunchArgument('use_sim_time', default_value=use_sim_time)
    enable_save_arg = DeclareLaunchArgument('enable_save', default_value=enable_save)

    if compiled == 'True':
        mecanum_path = get_package_share_directory('mecanum_nav')
    else:
        mecanum_path = os.path.join(SRC, 'mecanum_nav')

    base_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(mecanum_path, 'launch/base.launch.py')),
        launch_arguments={'use_sim_time': use_sim_time}.items(),
    )

    slam_params = RewrittenYaml(
        source_file=os.path.join(mecanum_path, 'config/slam.yaml'),
        param_rewrites={'use_sim_time': use_sim_time,
                        'map_frame': 'map',
                        'odom_frame': 'odom',
                        'base_frame': 'base_footprint'},
        convert_types=True,
    )

    remappings = [('/tf', 'tf'), ('/tf_static', 'tf_static'),
                  ('/map', 'map'), ('/map_metadata', 'map_metadata')]
    if enable_save == 'false':
        remappings.append(('/slam_toolbox/save_map', '/save_map'))

    slam_node = Node(
        package='slam_toolbox',
        executable='sync_slam_toolbox_node',
        name='slam_toolbox',
        output='screen',
        parameters=[slam_params],
        remappings=remappings,
    )

    return [use_sim_time_arg,
            enable_save_arg,
            base_launch,
            # The base needs to be publishing TF and scans before slam_toolbox starts looking
            # for them, otherwise it spends its first seconds complaining.
            TimerAction(period=8.0, actions=[slam_node])]


def generate_launch_description():
    return LaunchDescription([OpaqueFunction(function=launch_setup)])


if __name__ == '__main__':
    ld = generate_launch_description()
    ls = LaunchService()
    ls.include_launch_description(ld)
    ls.run()
