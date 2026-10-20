#!/usr/bin/env python3
"""Command all four drive motors to zero, directly over the serial link to the controller board.

Two uses:

1. Emergency stop when a wheel is turning and no ROS node is running. The board LATCHES the last
   motor command -- set_motor_speed is fire and forget, the firmware holds that speed until it is
   told otherwise. ros_robot_controller_node.py only zeroes the motors on KeyboardInterrupt
   (ros_robot_controller_node.py, main()), so a node killed by SIGTERM, SIGKILL, a crash, or a
   launch teardown leaves the wheels driving with nothing left running to stop them.

2. A diagnostic. If a wheel is spinning and this STOPS it, the cause was a latched command (see
   above) and the problem is software lifecycle. If the wheel keeps spinning after this, no command
   is involved and the cause is electrical -- wiring, or a stuck motor-driver channel.

Does not use ROS. Needs no nodes running.
"""

import sys
import time

sys.path.insert(0, '/home/ubuntu/ros2_ws/src/driver/ros_robot_controller')

from ros_robot_controller.ros_robot_controller_sdk import Board


def main():
    board = Board()
    board.enable_reception(True)

    zeros = [[1, 0.0], [2, 0.0], [3, 0.0], [4, 0.0]]
    # Sent repeatedly: the link is UART with no acknowledgement, so a single frame can be lost,
    # and losing the stop frame is the one failure that matters here.
    for _ in range(10):
        board.set_motor_speed(zeros)
        time.sleep(0.05)

    print('Sent zero speed to motors 1-4 (ten times).')
    print()
    print('If a wheel is STILL turning, no software command is driving it. Next checks:')
    print('  1. Power the robot fully off and on. Still spinning at power-up, before the Jetson')
    print('     has booted? Then it is purely electrical and no software change can fix it.')
    print('  2. Unplug that motor from the board. Stops? The board channel is stuck on.')
    print('     Keeps going? Its power leads are bridged to a live rail somewhere.')
    print('  3. Swap that motor onto a channel you know is good. If the spinning follows the')
    print('     CHANNEL the driver is blown; if it follows the MOTOR it is that motor or its loom.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
