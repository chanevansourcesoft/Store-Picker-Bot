"""Minimum hardware bringup for the mecanum robot: board + kinematics + TF.

This is what teleop needs, and the foundation SLAM and Nav2 build on. It deliberately does NOT
start the things the Ackermann bringup does and this robot does not have:

  * servo_controller -- arm and steering bus servos. This robot has neither. Worse, the Ackermann
    path also starts init_pose, which BLOCKS waiting on /controller_manager/init_finish, so a
    missing servo chain can stall the whole launch.
  * the Ackermann odom_publisher -- replaced by mecanum_base.py, which keeps linear.y instead of
    discarding it.

The lidar DRIVER is also not started here: lidar_watchdog.service already owns it (it holds the
serial port and pkills competing sllidar nodes). The laser_filters chain that turns /scan_raw into
/scan is started, because nothing else does it.

    ros2 launch mecanum_nav base.launch.py                  # board + base + TF + scan filter
    ros2 launch mecanum_nav base.launch.py enable_odom:=false   # skip EKF/rf2o (teleop only)
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription, LaunchService
from launch.actions import (DeclareLaunchArgument, ExecuteProcess, IncludeLaunchDescription,
                            OpaqueFunction)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from nav2_common.launch import ReplaceString

SRC = '/home/ubuntu/ros2_ws/src'


def launch_setup(context):
    compiled = os.environ['need_compile']

    enable_odom = LaunchConfiguration('enable_odom', default='true')
    axis_lock = LaunchConfiguration('axis_lock', default='true').perform(context)
    enable_lidar = LaunchConfiguration('enable_lidar', default='true')
    use_sim_time = LaunchConfiguration('use_sim_time', default='false')

    enable_odom_arg = DeclareLaunchArgument('enable_odom', default_value=enable_odom)
    enable_lidar_arg = DeclareLaunchArgument('enable_lidar', default_value=enable_lidar)
    use_sim_time_arg = DeclareLaunchArgument('use_sim_time', default_value=use_sim_time)

    if compiled == 'True':
        mecanum_path = get_package_share_directory('mecanum_nav')
        peripherals_path = get_package_share_directory('peripherals')
        robot_controller_path = get_package_share_directory('ros_robot_controller')
    else:
        mecanum_path = os.path.join(SRC, 'mecanum_nav')
        peripherals_path = os.path.join(SRC, 'peripherals')
        robot_controller_path = os.path.join(SRC, 'driver/ros_robot_controller')

    base_params = os.path.join(mecanum_path, 'config/mecanum_base.yaml')

    # TF for the mecanum URDF (base_footprint, lidar_frame, imu_link, wheels + rollers).
    robot_description_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(mecanum_path, 'launch/robot_description.launch.py')),
        launch_arguments={'use_rviz': 'false', 'use_sim_time': use_sim_time}.items(),
    )

    # Board driver: motors, buzzer, raw IMU. Shared with the Ackermann stack unchanged -- it is
    # hardware access, with no kinematics in it.
    robot_controller_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(robot_controller_path, 'launch/ros_robot_controller.launch.py')),
        launch_arguments={'imu_frame': 'imu_link'}.items(),
    )

    # Run as a plain script rather than an installed entry point, so it works without a colcon
    # build -- the same pattern navigation.launch.py uses for goal_point_relay.py.
    mecanum_base_node = ExecuteProcess(
        cmd=['python3', os.path.join(mecanum_path, 'scripts/mecanum_base.py'),
             '--ros-args', '--params-file', base_params, '-r', '__node:=mecanum_base',
             '-p', 'axis_lock:=' + axis_lock],
        output='screen',
    )

    # scan_raw -> scan. Run the filter node directly rather than including
    # peripherals/launch/lidar.launch.py, which would load the config file shared with the
    # Ackermann robot. This robot needs an extra self-filter for the arm mounted behind the
    # LiDAR; see config/lidar_filters_a1.yaml.
    # The LiDAR DRIVER is still not started here -- lidar_watchdog.service owns it.
    laser_filter_node = Node(
        package='laser_filters',
        executable='scan_to_scan_filter_chain',
        name='scan_to_scan_filter_chain',
        output='screen',
        parameters=[os.path.join(mecanum_path, 'config/lidar_filters_a1.yaml')],
        remappings=[('scan', 'scan_raw'),
                    ('scan_filtered', 'scan')],
        condition=IfCondition(enable_lidar),
    )

    imu_filter_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(peripherals_path, 'launch/imu_filter.launch.py')),
        condition=IfCondition(enable_odom),
    )

    # Laser scan matching for real measured velocity. Unlike wheel dead reckoning it notices when
    # the robot is stuck, and unlike an Ackermann base this robot can move sideways -- rf2o solves
    # a full planar twist (vx, vy, vyaw), so strafe is observed rather than mistaken for drift.
    rf2o_node = Node(
        package='rf2o_laser_odometry',
        executable='rf2o_laser_odometry_node',
        name='rf2o_laser_odometry',
        output='screen',
        # /scan, not /scan_raw: rf2o estimates motion by matching consecutive scans, and the
        # arm's returns are FIXED in the robot frame -- they never move no matter how the robot
        # drives. Matching against them biases rf2o towards "the robot is standing still",
        # which corrupts the odometry the EKF and slam_toolbox both depend on.
        parameters=[{'laser_scan_topic': '/scan',
                     'odom_topic': '/odom_rf2o',
                     'publish_tf': False,      # odom->base belongs to the EKF
                     'base_frame_id': 'base_footprint',
                     'odom_frame_id': 'odom',
                     'init_pose_from_topic': '',
                     'freq': 10.0}],
        # WARN, not the default INFO. rf2o logs three INFO lines per scan ("execution time",
        # "Laser odom", "Robot-base odom"), which at 10 Hz is 30 lines a second -- over 300 KB
        # of log in two minutes. It scrolls everything else off the screen, including
        # mecanum_base's startup banner showing the motor id map, which is the one line you
        # actually want to read when bringing the robot up. Warnings and errors still show.
        arguments=['--ros-args', '--log-level', 'rf2o_laser_odometry:=WARN'],
        condition=IfCondition(enable_odom),
    )

    ekf_param = ReplaceString(
        source_file=os.path.join(mecanum_path, 'config/ekf.yaml'),
        replacements={'namespace/': ''})

    ekf_node = Node(
        package='robot_localization',
        executable='ekf_node',
        name='ekf_filter_node',
        output='screen',
        parameters=[ekf_param, {'use_sim_time': use_sim_time}],
        remappings=[('/tf', 'tf'),
                    ('/tf_static', 'tf_static'),
                    ('odometry/filtered', 'odom')],
        condition=IfCondition(enable_odom),
    )

    return [enable_odom_arg,
            enable_lidar_arg,
            use_sim_time_arg,
            robot_description_launch,
            robot_controller_launch,
            mecanum_base_node,
            laser_filter_node,
            imu_filter_launch,
            rf2o_node,
            ekf_node]


def generate_launch_description():
    return LaunchDescription([OpaqueFunction(function=launch_setup)])


if __name__ == '__main__':
    ld = generate_launch_description()
    ls = LaunchService()
    ls.include_launch_description(ld)
    ls.run()
