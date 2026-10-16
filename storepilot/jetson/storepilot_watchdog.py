#!/usr/bin/env python3
"""
StorePilot link watchdog (runs ON THE JETSON, outside ros2_ws, changes no robot code).

The Pi dashboard publishes /storepilot/heartbeat (std_msgs/Bool) twice a second:
    data = True   the Pi is driving the robot (it sent the current Nav2 goal / route)
    data = False  the Pi is connected but not driving

If the Pi was driving and its heartbeat stops for TIMEOUT seconds while a NavigateToPose goal
is still running (Wi-Fi drop, Pi crash), this node cancels all NavigateToPose goals. Nav2 then
stops sending velocities and the base's own cmd_vel watchdog (mecanum_base, 0.5 s) stops the wheels.

Goals sent from RViz or the Jetson itself are never touched: the watchdog only arms after the
Pi says it is driving, and disarms when the Pi says it is not.

State is published on /storepilot/watchdog (std_msgs/String, JSON) for the dashboard.

Run:  source ~/ros2_ws/.robotrc && python3 ~/storepilot_jetson/storepilot_watchdog.py
"""

import json
import time

import rclpy
from action_msgs.msg import GoalStatus, GoalStatusArray
from action_msgs.srv import CancelGoal
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool, String

TIMEOUT = 3.0          # s without heartbeat while armed and a goal runs -> cancel
RUNNING = (GoalStatus.STATUS_ACCEPTED, GoalStatus.STATUS_EXECUTING)


class Watchdog(Node):
    def __init__(self):
        super().__init__("storepilot_watchdog")
        self.armed = False
        self.last_beat = None
        self.goal_running = False
        self.cancels = 0
        self.last_cancel = 0.0
        self.last_reason = ""
        self.create_subscription(Bool, "/storepilot/heartbeat", self.on_beat, 10)
        self.create_subscription(GoalStatusArray, "/navigate_to_pose/_action/status", self.on_status,
                                 QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE))
        self.cancel_client = self.create_client(CancelGoal, "/navigate_to_pose/_action/cancel_goal")
        self.state_pub = self.create_publisher(String, "/storepilot/watchdog", 10)
        self.create_timer(0.25, self.check)
        self.create_timer(1.0, self.publish_state)
        self.get_logger().info(f"StorePilot watchdog ready (timeout {TIMEOUT:.1f} s)")

    def on_beat(self, msg):
        self.last_beat = time.monotonic()
        if msg.data and not self.armed:
            self.get_logger().info("Armed: the Pi is driving")
        if not msg.data and self.armed:
            self.get_logger().info("Disarmed: the Pi is not driving")
        self.armed = bool(msg.data)

    def on_status(self, msg):
        self.goal_running = any(s.status in RUNNING for s in msg.status_list)

    def check(self):
        if not (self.armed and self.goal_running and self.last_beat is not None):
            return
        silent = time.monotonic() - self.last_beat
        if silent < TIMEOUT or time.monotonic() - self.last_cancel < 2.0:
            return
        self.last_cancel = time.monotonic()
        self.cancels += 1
        self.last_reason = f"no heartbeat from the Pi for {silent:.1f} s"
        self.get_logger().warn(f"Cancelling all NavigateToPose goals: {self.last_reason}")
        if self.cancel_client.service_is_ready():
            self.cancel_client.call_async(CancelGoal.Request())   # zero goal id + zero stamp = cancel all
        self.armed = False          # one cancel per loss of link; re-armed by the next driving heartbeat

    def publish_state(self):
        age = None if self.last_beat is None else round(time.monotonic() - self.last_beat, 1)
        self.state_pub.publish(String(data=json.dumps({
            "armed": self.armed, "heartbeat_age": age, "goal_running": self.goal_running,
            "cancels": self.cancels, "last_reason": self.last_reason, "timeout": TIMEOUT})))


def main():
    rclpy.init()
    node = Watchdog()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
