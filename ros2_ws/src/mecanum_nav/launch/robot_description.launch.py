# Publishes TF for the 4WD mecanum robot from urdf/mecanum_robot.urdf.
#
# Mecanum-specific, and the reason this is a separate file rather than an argument to
# jetacker_description/launch/robot_description.launch.py:
#
#  * Different URDF. The Ackermann one has steering joints (link1/w_link) driven by bus servo 9;
#    this robot has four independently driven mecanum wheels and no steering servo at all.
#
#  * No joint_states source_list. The Ackermann launch feeds joint_state_publisher from
#    /controller_manager/joint_states, which carries joint1..joint5, r_joint and w_joint -- the
#    arm and steering servos. None of those joints exist in this URDF, and this robot has no such
#    servos, so subscribing would only make robot_state_publisher warn about unknown joints.
#    Instead joint_state_publisher reads the description and publishes zeros for everything it
#    finds there.
#
#    "Everything" is 44 joints: the 4 wheel hubs plus 10 passive rollers per wheel. Mecanum wheels
#    carry free-spinning rollers set at 45 degrees around the rim -- that is what lets the robot
#    strafe, and it is why the URDF models each roller as its own continuous joint. The rollers are
#    unactuated and unsensed: nothing reports their angle and nothing needs to. Holding them at
#    zero is correct for TF and for navigation; the wheels simply render static in RViz. Driving
#    them would be cosmetic only, so it is deliberately not done here.
#
# Frames used downstream: base_footprint (costmaps, AMCL, slam_toolbox), lidar_frame (every
# LaserScan is stamped with it), imu_link (EKF).

import os
from ament_index_python.packages import get_package_share_directory

from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch.conditions import IfCondition
from launch import LaunchDescription, LaunchService
from launch.substitutions import Command, LaunchConfiguration
from launch.actions import DeclareLaunchArgument, OpaqueFunction


def launch_setup(context):
    compiled = os.environ['need_compile']

    use_rviz = LaunchConfiguration('use_rviz', default='false')
    namespace = LaunchConfiguration('namespace', default='')
    use_namespace = LaunchConfiguration('use_namespace', default='false')
    frame_prefix = LaunchConfiguration('frame_prefix', default='')
    use_sim_time = LaunchConfiguration('use_sim_time', default='false')

    use_rviz_arg = DeclareLaunchArgument('use_rviz', default_value=use_rviz)
    namespace_arg = DeclareLaunchArgument('namespace', default_value=namespace)
    use_namespace_arg = DeclareLaunchArgument('use_namespace', default_value=use_namespace)
    frame_prefix_arg = DeclareLaunchArgument('frame_prefix', default_value=frame_prefix)
    use_sim_time_arg = DeclareLaunchArgument('use_sim_time', default_value=use_sim_time)

    # The URDF still lives in jetacker_description/urdf because that is where it was authored.
    # Nothing in the Ackermann stack references it, so the two robots do not interfere.
    if compiled == 'True':
        description_package_path = get_package_share_directory('jetacker_description')
    else:
        description_package_path = '/home/ubuntu/ros2_ws/src/simulations/jetacker_description'

    urdf_path = os.path.join(description_package_path, 'urdf/mecanum_robot.urdf')

    # Plain URDF, no xacro macros -- xacro passes it through unchanged. Kept as a xacro call so
    # the file can grow xacro properties later without touching this launch file.
    # ParameterValue(..., value_type=str) is required: without it launch tries to parse the URDF
    # text as YAML and the whole launch dies with "Unable to parse the value of parameter
    # robot_description as yaml".
    robot_description = ParameterValue(Command(['xacro ', urdf_path]), value_type=str)

    robot_state_publisher_node = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        name='robot_state_publisher',
        output='screen',
        parameters=[{'robot_description': robot_description,
                     'frame_prefix': frame_prefix,
                     'use_sim_time': use_sim_time}],
        remappings=[('/tf', 'tf'),
                    ('/tf_static', 'tf_static')],
    )

    joint_state_publisher_node = Node(
        package='joint_state_publisher',
        executable='joint_state_publisher',
        name='joint_state_publisher',
        output='screen',
        # 1 Hz, not the usual 20. Every joint_states message makes robot_state_publisher emit
        # TF for all 44 continuous joints -- 4 wheel hubs and 40 rollers -- and MEASURED on this
        # robot that was 44 transforms at 12.7 Hz, about 560 transforms per second. Every TF
        # consumer (AMCL, both costmaps, rf2o, the collision monitor, RViz) deserialises and
        # buffers all of them, forever, for frames nothing uses.
        # The rollers and wheels are unactuated and unsensed, so these joint values are CONSTANT
        # ZERO -- republishing them 20 times a second communicates nothing. 1 Hz keeps the
        # frames present in TF for RViz without the traffic.
        # OLD: rate 20.0
        parameters=[{'rate': 1.0, 'use_sim_time': use_sim_time}],
    )

    rviz_node = Node(
        package='rviz2',
        executable='rviz2',
        name='rviz2',
        output='screen',
        condition=IfCondition(use_rviz),
    )

    return [use_rviz_arg,
            namespace_arg,
            use_namespace_arg,
            frame_prefix_arg,
            use_sim_time_arg,
            robot_state_publisher_node,
            joint_state_publisher_node,
            rviz_node]


def generate_launch_description():
    return LaunchDescription([
        OpaqueFunction(function=launch_setup)
    ])


if __name__ == '__main__':
    ld = generate_launch_description()
    ls = LaunchService()
    ls.include_launch_description(ld)
    ls.run()
