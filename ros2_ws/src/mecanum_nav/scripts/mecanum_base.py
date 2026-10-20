#!/usr/bin/env python3
"""Mecanum base driver: cmd_vel -> four motor speeds, plus dead-reckoned odom_raw.

Replaces controller/odom_publisher_node.py for this robot. It is a separate node, in a separate
package, so the Ackermann stack keeps its own driver untouched.

What it does differently from the Ackermann driver:

  * Three degrees of freedom instead of two. The Ackermann driver THREW AWAY linear.y
    (`self.linear_y = 0.0`) because a car cannot strafe, and converted angular.z into a steering
    angle for bus servo 9. Here linear.y is a first-class command: it drives the robot sideways
    at 90 degrees to its heading. There is no steering servo.

  * All four wheels driven. The Ackermann code sent zero to motors 1 and 3 -- the front pair were
    steered idlers. Strafing is impossible unless all four are powered, because it works by
    cancelling the four wheels' forward components against each other and adding their sideways
    ones.

  * A command timeout. The board latches the last motor speed indefinitely; it has no notion of a
    stale command. If whatever was publishing stops -- teleop killed, nav2 crashed, wifi dropped --
    the wheels keep turning. This node stops them after cmd_vel_timeout, and also zeroes them on
    every exit path (SIGINT, SIGTERM, exception, normal shutdown), which the stock driver only
    does for Ctrl-C.

Subscribes
    /controller/cmd_vel   geometry_msgs/Twist   nav2's output, and the usual teleop target
    /cmd_vel              geometry_msgs/Twist   convenience for `ros2 topic pub` and rqt
Publishes
    ros_robot_controller/set_motor  MotorsState
    odom_raw                        nav_msgs/Odometry
"""

import atexit
import math
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist, Pose
from nav_msgs.msg import Odometry
from ros_robot_controller_msgs.msg import MotorState, MotorsState

from mecanum_kinematics import MecanumKinematics

# Dead reckoning from COMMANDED velocity has no idea whether the robot actually moved, so the
# covariances say "trust this loosely". The EKF is configured to take velocity from rf2o laser
# odometry instead; this topic exists for completeness and for debugging.
ODOM_POSE_COVARIANCE = [0.0] * 36
ODOM_TWIST_COVARIANCE = [0.0] * 36
for _i, _v in ((0, 1e-3), (7, 1e-3), (14, 1e6), (21, 1e6), (28, 1e6), (35, 1e3)):
    ODOM_POSE_COVARIANCE[_i] = _v
    ODOM_TWIST_COVARIANCE[_i] = _v


def yaw_to_quaternion(yaw):
    q = Pose().orientation
    q.w = math.cos(yaw * 0.5)
    q.x = 0.0
    q.y = 0.0
    q.z = math.sin(yaw * 0.5)
    return q


class MecanumBase(Node):

    def __init__(self):
        super().__init__('mecanum_base')

        self.declare_parameter('wheelbase', 0.175)
        self.declare_parameter('track_width', 0.193)
        self.declare_parameter('wheel_diameter', 0.080)
        self.declare_parameter('id_front_left', 1)
        self.declare_parameter('id_rear_left', 2)
        self.declare_parameter('id_front_right', 3)
        self.declare_parameter('id_rear_right', 4)
        # PER-WHEEL inversion, not per-side. A two-parameter invert_left/invert_right scheme
        # cannot represent a DIAGONAL polarity pattern, and on this robot the inverted motors
        # are rear-left and front-right -- a diagonal. Hand-wired motors have no reason to
        # share polarity by side, so per-wheel is the only scheme that can always be made to
        # fit the hardware.
        self.declare_parameter('invert_front_left', 1.0)
        self.declare_parameter('invert_rear_left', 1.0)
        self.declare_parameter('invert_front_right', 1.0)
        self.declare_parameter('invert_rear_right', 1.0)
        self.declare_parameter('max_linear_x', 0.5)
        self.declare_parameter('max_linear_y', 0.4)
        self.declare_parameter('max_angular_z', 2.0)
        self.declare_parameter('cmd_vel_timeout', 0.5)
        # Axis lock: execute ONE degree of freedom at a time instead of blending them.
        # Nav2's controller always outputs a mixed twist (some vx + some vy + some yaw at once),
        # which on a holonomic base comes out as a continuous curve in an arbitrary direction.
        # With this on, only the dominant component survives and the robot moves in straight
        # segments: drive, then strafe, then turn -- never two at once.
        self.declare_parameter('axis_lock', False)
        # How much a different axis must beat the one currently running before the robot
        # switches to it. Without this margin the two largest components swap back and forth
        # every cycle near the crossover and the robot judders instead of committing.
        self.declare_parameter('axis_lock_hysteresis', 1.4)
        self.declare_parameter('base_frame_id', 'base_footprint')
        self.declare_parameter('odom_frame_id', 'odom')
        self.declare_parameter('linear_correction_factor', 1.0)
        self.declare_parameter('angular_correction_factor', 1.0)

        g = lambda n: self.get_parameter(n).value

        self.kin = MecanumKinematics(g('wheelbase'), g('track_width'), g('wheel_diameter'))
        self.ids = (g('id_front_left'), g('id_rear_left'),
                    g('id_front_right'), g('id_rear_right'))
        self.inv = (float(g('invert_front_left')), float(g('invert_rear_left')),
                    float(g('invert_front_right')), float(g('invert_rear_right')))
        self.max_x = float(g('max_linear_x'))
        self.max_y = float(g('max_linear_y'))
        self.max_w = float(g('max_angular_z'))
        self.timeout = float(g('cmd_vel_timeout'))
        self.axis_lock = bool(g('axis_lock'))
        self.axis_hyst = float(g('axis_lock_hysteresis'))
        self.locked_axis = None
        self.base_frame_id = g('base_frame_id')
        self.odom_frame_id = g('odom_frame_id')
        self.linear_factor = float(g('linear_correction_factor'))
        self.angular_factor = float(g('angular_correction_factor'))

        # The largest rim speed any single wheel may be asked for. The three twist components add
        # per wheel, so the worst case is max_x + max_y + k*max_w -- asking for all three at once
        # would otherwise demand far more than the motors can give, and the motion would come out
        # distorted rather than merely slow.
        self.max_wheel_mps = self.max_x + self.max_y + self.kin.k * self.max_w

        self.running = True
        self.vx = 0.0
        self.vy = 0.0
        self.wz = 0.0
        self.last_cmd_time = 0.0
        self.stopped = True
        self.lock = threading.Lock()

        self.x = 0.0
        self.y = 0.0
        self.yaw = 0.0

        self.motor_pub = self.create_publisher(MotorsState, 'ros_robot_controller/set_motor', 1)
        self.odom_pub = self.create_publisher(Odometry, 'odom_raw', 1)

        self.create_subscription(Twist, '/controller/cmd_vel', self.cmd_vel_cb, 1)
        self.create_subscription(Twist, '/cmd_vel', self.cmd_vel_cb, 1)

        self.create_timer(1.0 / 50.0, self.tick)

        # Belt and braces: every one of these paths zeroes the motors, because any of them
        # leaving a latched speed behind means a robot driving itself across the room.
        atexit.register(self.emergency_stop)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                signal.signal(sig, self._signal_stop)
            except (ValueError, OSError):
                pass

        # Log the id map and inversions too, not just the geometry. These are the settings that
        # produce confusing WRONG MOTION rather than an error when they are off, so having them
        # in the log makes "the robot drives in a circle" a two-second diagnosis.
        self.get_logger().info(
            'mecanum_base up: wheelbase=%.3f track=%.3f wheel_d=%.3f k=%.4f '
            'limits vx=%.2f vy=%.2f wz=%.2f timeout=%.2fs'
            % (self.kin.wheelbase, self.kin.track_width, self.kin.wheel_diameter, self.kin.k,
               self.max_x, self.max_y, self.max_w, self.timeout))
        self.get_logger().info(
            'axis_lock: %s (hysteresis %.2f)'
            % ('ON - one direction at a time' if self.axis_lock else 'off - blended motion',
               self.axis_hyst))
        self.get_logger().info(
            'motor ids: FL=%d RL=%d FR=%d RR=%d | invert FL=%+.0f RL=%+.0f FR=%+.0f RR=%+.0f'
            % (self.ids[0], self.ids[1], self.ids[2], self.ids[3],
               self.inv[0], self.inv[1], self.inv[2], self.inv[3]))

    # ---------------- commands ----------------

    def pick_axis(self, vx, vy, wz):
        """Keep only the dominant degree of freedom; zero the other two.

        The three components are not directly comparable -- vx and vy are m/s, wz is rad/s --
        so yaw is converted to the rim speed it asks of a wheel (k * wz, the same lever arm the
        kinematics uses). That makes "which of these is the robot mostly being asked to do"
        a fair question.

        The axis currently running is kept until another beats it by axis_lock_hysteresis,
        so the robot finishes a straight segment instead of swapping axis every control cycle.
        """
        mag = {'x': abs(vx), 'y': abs(vy), 'w': abs(wz) * self.kin.k}

        # Nothing meaningful commanded: release the lock so the next move starts fresh.
        if max(mag.values()) < 1e-4:
            self.locked_axis = None
            return 0.0, 0.0, 0.0

        best = max(mag, key=mag.get)
        if self.locked_axis is not None and self.locked_axis != best:
            current = mag[self.locked_axis]
            # Stay on the current axis unless the challenger clearly wins, OR the current axis
            # has essentially stopped being asked for (segment finished).
            if current > 1e-4 and mag[best] < current * self.axis_hyst:
                best = self.locked_axis
        self.locked_axis = best

        return (vx if best == 'x' else 0.0,
                vy if best == 'y' else 0.0,
                wz if best == 'w' else 0.0)

    def cmd_vel_cb(self, msg):
        with self.lock:
            vx = max(-self.max_x, min(self.max_x, msg.linear.x))
            vy = max(-self.max_y, min(self.max_y, msg.linear.y))
            wz = max(-self.max_w, min(self.max_w, msg.angular.z))
            if self.axis_lock:
                vx, vy, wz = self.pick_axis(vx, vy, wz)
            self.vx, self.vy, self.wz = vx, vy, wz
            self.last_cmd_time = time.time()

    def motors_msg(self, vx, vy, wz):
        v_fl, v_rl, v_fr, v_rr = self.kin.inverse(vx, vy, wz)
        (v_fl, v_rl, v_fr, v_rr), _ = self.kin.scale_to_limit(
            (v_fl, v_rl, v_fr, v_rr), self.max_wheel_mps)

        per_wheel = ((self.ids[0], v_fl * self.inv[0]),
                     (self.ids[1], v_rl * self.inv[1]),
                     (self.ids[2], v_fr * self.inv[2]),
                     (self.ids[3], v_rr * self.inv[3]))

        out = MotorsState()
        data = []
        for motor_id, rim_mps in per_wheel:
            state = MotorState()
            state.id = int(motor_id)
            state.rps = float(self.kin.mps_to_rps(rim_mps))
            data.append(state)
        out.data = data
        return out

    def tick(self):
        with self.lock:
            stale = (time.time() - self.last_cmd_time) > self.timeout
            if stale:
                self.vx = self.vy = self.wz = 0.0
            vx, vy, wz = self.vx, self.vy, self.wz

        moving = not (vx == 0.0 and vy == 0.0 and wz == 0.0)
        # Keep publishing while moving; once stopped, send zero a few times and then go quiet
        # rather than flooding the serial link with zeros forever.
        if moving or not self.stopped:
            self.motor_pub.publish(self.motors_msg(vx, vy, wz))
            self.stopped = not moving

        self.publish_odom(vx, vy, wz)

    # ---------------- odometry ----------------

    def publish_odom(self, vx, vy, wz):
        dt = 1.0 / 50.0
        # Body-frame velocity rotated into the odom frame. The vy term is what an Ackermann
        # dead-reckoner never needed and could not represent.
        self.x += (math.cos(self.yaw) * vx - math.sin(self.yaw) * vy) * dt
        self.y += (math.sin(self.yaw) * vx + math.cos(self.yaw) * vy) * dt
        self.yaw += self.angular_factor * wz * dt

        odom = Odometry()
        odom.header.stamp = self.get_clock().now().to_msg()
        odom.header.frame_id = self.odom_frame_id
        odom.child_frame_id = self.base_frame_id
        odom.pose.pose.position.x = self.linear_factor * self.x
        odom.pose.pose.position.y = self.linear_factor * self.y
        odom.pose.pose.position.z = 0.0
        odom.pose.pose.orientation = yaw_to_quaternion(self.yaw)
        odom.twist.twist.linear.x = vx
        odom.twist.twist.linear.y = vy
        odom.twist.twist.angular.z = wz
        odom.pose.covariance = ODOM_POSE_COVARIANCE
        odom.twist.covariance = ODOM_TWIST_COVARIANCE
        self.odom_pub.publish(odom)

    # ---------------- shutdown ----------------

    def emergency_stop(self):
        try:
            stop = MotorsState()
            data = []
            for motor_id in self.ids:
                state = MotorState()
                state.id = int(motor_id)
                state.rps = 0.0
                data.append(state)
            stop.data = data
            # Several times: the link is UART with no acknowledgement and a dropped stop frame
            # is the only dropped frame that matters.
            for _ in range(5):
                self.motor_pub.publish(stop)
                time.sleep(0.02)
        except Exception:
            pass

    def _signal_stop(self, signum, frame):
        """Only set a flag and stop the wheels.

        Deliberately does NOT call rclpy.shutdown/try_shutdown. Tearing the context down from
        inside a signal handler while rclpy.spin() is mid-iteration leaves the executor holding a
        freed guard condition, and the process dies with
        "AttributeError: 'NoneType' object has no attribute 'handle'" instead of shutting down
        cleanly -- which matters here, because a messy exit is exactly when motors get left
        latched. The spin loop in main() checks self.running and exits on its own terms.
        """
        self.running = False
        self.emergency_stop()


def main():
    rclpy.init()
    node = MecanumBase()
    try:
        # Hand-rolled spin loop so a signal can end it without touching the context from inside
        # the handler. See _signal_stop above.
        while rclpy.ok() and node.running:
            rclpy.spin_once(node, timeout_sec=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        node.emergency_stop()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
