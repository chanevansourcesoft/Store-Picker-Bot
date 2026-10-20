#!/usr/bin/env python3
"""Holonomic keyboard teleop for the mecanum robot.

Mecanum wheels give three independent degrees of freedom (vx, vy, yaw), so unlike a car this
robot can translate in ANY direction without turning. The key layout is therefore a compass grid
rather than the drive/steer pair a car-like base needs:

        q   w   e          q  forward-left     w  forward      e  forward-right
        a   s   d          a  strafe LEFT      s  STOP         d  strafe RIGHT
        z   x   c          z  back-left        x  backward     c  back-right

        j       l          j  rotate left (anticlockwise)   l  rotate right (clockwise)

        space / s   stop now
        , .         slower / faster
        Ctrl-C      quit (stops the robot first)

The four corner keys are genuine diagonals: the robot slides north-east, north-west, south-east
or south-west with its heading unchanged. They exist as separate keys because a terminal never
reports key RELEASE, so holding w and d together cannot be detected reliably -- the shell only
sees a stream of repeated presses with no way to tell which are still held.

Diagonal speed is normalised: vx and vy are each set to speed/sqrt(2), so the resultant speed
matches the straight-line speed rather than being 1.41x faster. Worth knowing that a pure
diagonal drives only ONE pair of wheels (the other pair sits still), each at sqrt(2) times the
rim speed of straight driving -- that is inherent to the 45 degree rollers, not a bug, and it is
why diagonals slip slightly more than straight moves.

Hold a key to keep moving. Release everything and the robot stops: the command is republished
continuously, and mecanum_base also enforces its own 0.5 s timeout, so letting go of the keyboard
or this process dying brings the wheels to a halt rather than latching the last speed.

Publishes geometry_msgs/Twist on /controller/cmd_vel (override with --topic).
"""

import argparse
import math
import select
import sys
import termios
import time
import tty

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist

# 1/sqrt(2): a diagonal splits the commanded speed between the two axes so the resultant
# magnitude equals the straight-line speed.
D = 1.0 / math.sqrt(2.0)

HELP = """
Mecanum holonomic teleop
========================

        q   w   e          q  forward-LEFT    w  FORWARD     e  forward-RIGHT
        a   s   d          a  strafe LEFT     s  STOP        d  strafe RIGHT
        z   x   c          z  back-LEFT       x  BACKWARD    c  back-RIGHT

        j       l          j  rotate LEFT (anticlockwise)    l  rotate RIGHT (clockwise)

  space  stop        ,  slower        .  faster        Ctrl-C  quit

  The four corners (q e z c) are true diagonals: the robot slides that way and its
  nose keeps pointing the same direction.
"""

# key -> (vx_factor, vy_factor, wz_factor).  +y is LEFT and +z is anticlockwise (REP-103).
KEYS = {
    'w': (1.0, 0.0, 0.0),      # forward
    'x': (-1.0, 0.0, 0.0),     # backward
    'a': (0.0, 1.0, 0.0),      # strafe left
    'd': (0.0, -1.0, 0.0),     # strafe right
    'q': (D, D, 0.0),          # forward-left
    'e': (D, -D, 0.0),         # forward-right
    'z': (-D, D, 0.0),         # back-left
    'c': (-D, -D, 0.0),        # back-right
    'j': (0.0, 0.0, 1.0),      # rotate anticlockwise
    'l': (0.0, 0.0, -1.0),     # rotate clockwise
    's': (0.0, 0.0, 0.0),      # stop
    ' ': (0.0, 0.0, 0.0),      # stop
}


class Teleop(Node):
    def __init__(self, topic, max_x, max_y, max_w, rate):
        super().__init__('teleop_mecanum')
        self.pub = self.create_publisher(Twist, topic, 1)
        self.max_x = max_x
        self.max_y = max_y
        self.max_w = max_w
        self.period = 1.0 / rate
        self.scale = 1.0

    def send(self, vx, vy, wz):
        t = Twist()
        t.linear.x = vx
        t.linear.y = vy
        t.angular.z = wz
        self.pub.publish(t)


def read_key(timeout):
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    return sys.stdin.read(1) if ready else ''


def main():
    ap = argparse.ArgumentParser(description='Holonomic teleop for the mecanum base')
    ap.add_argument('--topic', default='/controller/cmd_vel')
    ap.add_argument('--max-x', type=float, default=0.25, help='m/s forward/back')
    ap.add_argument('--max-y', type=float, default=0.25, help='m/s strafe')
    ap.add_argument('--max-w', type=float, default=1.0, help='rad/s rotate')
    ap.add_argument('--rate', type=float, default=20.0, help='Hz republish rate')
    args = ap.parse_args()

    if not sys.stdin.isatty():
        print('teleop needs a terminal (stdin is not a tty).')
        return 1

    rclpy.init()
    node = Teleop(args.topic, args.max_x, args.max_y, args.max_w, args.rate)

    print(HELP)
    print('  publishing to %s   limits: x=%.2f  y=%.2f m/s  yaw=%.2f rad/s\n'
          % (args.topic, args.max_x, args.max_y, args.max_w))

    settings = termios.tcgetattr(sys.stdin)
    vx = vy = wz = 0.0
    # A key counts as held while presses keep arriving within this long. Terminals send no
    # key-release event, so the auto-repeat rate is the only signal available.
    HOLD = 0.35
    last_press = 0.0

    try:
        tty.setcbreak(sys.stdin.fileno())
        while rclpy.ok():
            key = read_key(node.period)

            if key:
                if key == '\x03':          # Ctrl-C
                    break
                k = key.lower()
                if k in KEYS:
                    last_press = time.time()
                    fx, fy, fw = KEYS[k]
                    vx = fx * node.max_x * node.scale
                    vy = fy * node.max_y * node.scale
                    wz = fw * node.max_w * node.scale
                elif k == ',':
                    node.scale = max(0.1, node.scale - 0.1)
                    print('\r  scale %.1f    ' % node.scale, end='', flush=True)
                elif k == '.':
                    node.scale = min(1.0, node.scale + 0.1)
                    print('\r  scale %.1f    ' % node.scale, end='', flush=True)
            elif time.time() - last_press > HOLD:
                vx = vy = wz = 0.0

            node.send(vx, vy, wz)
            rclpy.spin_once(node, timeout_sec=0.0)
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
        for _ in range(5):
            node.send(0.0, 0.0, 0.0)
            rclpy.spin_once(node, timeout_sec=0.0)
            time.sleep(0.02)
        print('\n  stopped.')
        node.destroy_node()
        rclpy.try_shutdown()
    return 0


if __name__ == '__main__':
    sys.exit(main())
