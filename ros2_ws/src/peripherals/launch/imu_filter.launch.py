import os
from ament_index_python.packages import PackageNotFoundError, get_package_share_directory

from launch_ros.actions import Node
from launch import LaunchDescription, LaunchService
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration

def generate_launch_description():
    compiled = os.environ['need_compile']
    # The calibration and imu_calib packages are not in this repo (they come with the robot's own
    # image). Without them peripherals' imu_bias does the same job on the same topics: it measures
    # the gyro bias at startup (robot standing still) and publishes imu_corrected.
    # accel_calib_file: optional accel calibration in imu_calib's format (SM + bias). Empty = none.
    accel_calib_file = LaunchConfiguration('accel_calib_file', default='')
    accel_calib_file_arg = DeclareLaunchArgument('accel_calib_file', default_value=accel_calib_file)
    try:
        if compiled == 'True':
            calibration_package_path = get_package_share_directory('calibration')
        else:
            calibration_package_path = '/home/ubuntu/ros2_ws/src/calibration'
        get_package_share_directory('imu_calib')
        calib_file = os.path.join(calibration_package_path, 'config/imu_calib.yaml')
        have_calib = os.path.exists(calib_file)
    except PackageNotFoundError:
        have_calib = False
    imu_calib_node = Node(
        package='imu_calib',
        executable='apply_calib',
        name='imu_calib',
        output='screen',
        parameters=[{"calib_file": calib_file if have_calib else ''
                     }],
        remappings=[
            ('raw', 'ros_robot_controller/imu_raw'),
            ('corrected', 'imu_corrected')
            ]
        )

    imu_bias_node = Node(
        package='peripherals',
        executable='imu_bias',
        name='imu_calib',
        output='screen',
        parameters=[{'calib_file': accel_calib_file}],
        remappings=[
            ('raw', 'ros_robot_controller/imu_raw'),
            ('corrected', 'imu_corrected')
            ]
        )


    # imu_filter_node = Node(
        # package='imu_complementary_filter',
        # executable='complementary_filter_node',
        # name='imu_filter',
        # output='screen',
        # parameters=[
            # {'use_mag': False,
            # # 'gain_acc': 0.2,
            # # 'bias_alpha': 0.2,
            # 'do_bias_estimation': True,
            # 'do_adaptive_gain': True,
            # 'publish_debug_topics': False}
        # ],
        # remappings=[
            # ('/tf', 'tf'),
            # ('/imu/data_raw', 'imu_corrected'),
            # ('imu/data', 'imu')
            # ]
        # )

    imu_filter_node = Node(
        package='imu_filter_madgwick',
        executable='imu_filter_madgwick_node',
        name='imu_filter',
        output='screen',
        parameters=[
            {'fixed_frame': "imu_link",
            'use_mag': False,
            'publish_tf': False,
            'world_frame': "enu",
            'orientation_stddev': 0.05}
        ],
        remappings=[
            ('/tf', 'tf'),
            ('/imu/data_raw', 'imu_corrected'),
            ('imu/data', 'imu')
            ]
    )

    return LaunchDescription([
        accel_calib_file_arg,
        imu_calib_node if have_calib else imu_bias_node,
        imu_filter_node
    ])

if __name__ == '__main__':
    # 创建一个LaunchDescription对象(create a LaunchDescription object)
    ld = generate_launch_description()

    ls = LaunchService()
    ls.include_launch_description(ld)
    ls.run()
