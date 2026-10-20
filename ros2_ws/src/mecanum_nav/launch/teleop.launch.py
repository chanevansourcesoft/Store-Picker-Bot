"""Holonomic keyboard teleop. Needs base.launch.py running in another terminal.

Teleop is a foreground, interactive process -- it reads the keyboard -- so running it under
`ros2 launch` hides its prompts and swallows keystrokes. Prefer running the script directly:

    python3 /home/ubuntu/ros2_ws/src/mecanum_nav/scripts/teleop_mecanum.py

This launch file exists only for consistency with the rest of the package and forces the
script's output through, but the direct invocation above is the one to use.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction
from launch.substitutions import LaunchConfiguration

SRC = '/home/ubuntu/ros2_ws/src'


def launch_setup(context):
    compiled = os.environ['need_compile']
    topic = LaunchConfiguration('topic', default='/controller/cmd_vel').perform(context)
    topic_arg = DeclareLaunchArgument('topic', default_value=topic)

    if compiled == 'True':
        mecanum_path = get_package_share_directory('mecanum_nav')
    else:
        mecanum_path = os.path.join(SRC, 'mecanum_nav')

    teleop = ExecuteProcess(
        cmd=['python3', os.path.join(mecanum_path, 'scripts/teleop_mecanum.py'),
             '--topic', topic],
        output='screen',
        emulate_tty=True,
    )
    return [topic_arg, teleop]


def generate_launch_description():
    return LaunchDescription([OpaqueFunction(function=launch_setup)])
