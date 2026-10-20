#!/usr/bin/env python3
# encoding: utf-8
# Stand-in for imu_calib's apply_calib, which is not in this repo (it came with the robot's own
# image). Same job, same topics: subscribe to raw, average the first gyro_calib_samples gyro
# readings while the robot stands still, then publish every message on corrected with that
# bias subtracted. Nothing is published while it calibrates.
# An accel calibration file is optional, in apply_calib's format (SM: 9 values, row by row;
# bias: 3 values; corrected = SM * raw - bias). Empty calib_file = accel passes through.
import yaml
import rclpy
import numpy as np
from rclpy.node import Node
from sensor_msgs.msg import Imu


class ImuBias(Node):
    def __init__(self, name):
        rclpy.init()
        super().__init__(name)
        self.declare_parameter('calib_file', '')
        self.declare_parameter('calibrate_gyros', True)
        self.declare_parameter('gyro_calib_samples', 100)
        self.declare_parameter('queue_size', 5)

        calib_file = self.get_parameter('calib_file').value
        self.calibrate_gyros = self.get_parameter('calibrate_gyros').value
        self.calib_samples = self.get_parameter('gyro_calib_samples').value
        queue_size = self.get_parameter('queue_size').value

        self.accel_sm = None
        self.accel_bias = None
        if calib_file:
            with open(calib_file) as f:
                calib = yaml.safe_load(f)
            self.accel_sm = np.array(calib['SM'], dtype=float).reshape(3, 3)
            self.accel_bias = np.array(calib['bias'], dtype=float).reshape(3)
            self.get_logger().info('accel calibration from %s' % calib_file)

        self.gyro_sum = np.zeros(3)
        self.gyro_count = 0
        self.gyro_bias = np.zeros(3)

        self.pub = self.create_publisher(Imu, 'corrected', queue_size)
        self.create_subscription(Imu, 'raw', self.imu_callback, queue_size)
        if self.calibrate_gyros:
            self.get_logger().info('Calibrating gyros; do not move the IMU')

    def imu_callback(self, msg):
        gyro = np.array([msg.angular_velocity.x, msg.angular_velocity.y, msg.angular_velocity.z])
        if self.calibrate_gyros:
            self.gyro_sum += gyro
            self.gyro_count += 1
            if self.gyro_count < self.calib_samples:
                return
            self.gyro_bias = self.gyro_sum / self.gyro_count
            self.calibrate_gyros = False
            self.get_logger().info('\033[1;32mGyro bias (rad/s): x %.5f y %.5f z %.5f\033[0m' % tuple(self.gyro_bias))

        if self.accel_sm is not None:
            accel = np.array([msg.linear_acceleration.x, msg.linear_acceleration.y, msg.linear_acceleration.z])
            accel = self.accel_sm @ accel - self.accel_bias
            msg.linear_acceleration.x, msg.linear_acceleration.y, msg.linear_acceleration.z = accel.tolist()
        gyro -= self.gyro_bias
        msg.angular_velocity.x, msg.angular_velocity.y, msg.angular_velocity.z = gyro.tolist()
        self.pub.publish(msg)


def main():
    node = ImuBias('imu_calib')
    rclpy.spin(node)  # 循环等待ROS2退出(loop and wait for ROS2 to exit)


if __name__ == "__main__":
    main()
