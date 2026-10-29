#!/usr/bin/env python3
"""Work out the motor id -> wheel map and polarity from MEASURED robot motion, not from eyes.

check_motor_mapping.py asks a person which way each wheel turned. On a mecanum wheel that is
genuinely hard to see -- the rollers spin while the hub turns -- and a single misread answer
makes every key wrong. This script asks nothing: it drives four whole-robot wheel patterns
straight to the board, reads how far the robot actually moved from rf2o laser odometry, and
solves for the map.

The four patterns (motor ids 1..4) are the rows of a 4x4 Hadamard matrix. Each is driven forward
and then reversed, so the robot ends up roughly where it started and slow drift cancels. From
the four measured (dx, dy, dyaw) the per-motor response falls out by one matrix product, and the
sign of each motor's (vx, vy, wz) response says which corner it is and whether it is reversed:

    corner   vx   vy   wz          (X-roller mecanum, REP-103: +x forward, +y left)
    FL       +    -    -
    RL       +    +    -
    FR       +    +    +
    RR       +    -    +

"Forward" is the lidar's forward (rf2o measures base_footprint through the URDF), which is the
direction Nav2 drives, so the result is right for navigation by construction.

Needs base.launch.py (or slam.launch.py) running for the board driver and rf2o, and NOTHING else
commanding the wheels: stop teleop first. The robot moves by itself, about 25 cm and a quarter
turn at most, and comes back. Give it half a metre of clear floor all round.

    python3 calibrate_motor_mapping.py
"""

import math
import sys
import time

import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from ros_robot_controller_msgs.msg import MotorState, MotorsState

SPEED_RPS = 0.5     # about 0.13 m/s at the rim on 80 mm wheels
RUN_SEC = 1.5
SETTLE_SEC = 1.0    # let the robot stop and rf2o catch up before reading the pose

HADAMARD = [(1, 1, 1, 1), (1, -1, 1, -1), (1, 1, -1, -1), (1, -1, -1, 1)]
CORNERS = {(1, -1, -1): 'front_left', (1, 1, -1): 'rear_left',
           (1, 1, 1): 'front_right', (1, -1, 1): 'rear_right'}


def yaw_of(q):
    return math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))


def wrap(a):
    return math.atan2(math.sin(a), math.cos(a))


class Calibrator(Node):
    def __init__(self):
        super().__init__('calibrate_motor_mapping')
        self.pose = None
        self.create_subscription(Odometry, '/odom_rf2o', self.on_odom, 10)
        self.pub = self.create_publisher(MotorsState, 'ros_robot_controller/set_motor', 1)

    def on_odom(self, msg):
        p = msg.pose.pose
        self.pose = (p.position.x, p.position.y, yaw_of(p.orientation))

    def wait(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            rclpy.spin_once(self, timeout_sec=0.02)

    def send(self, rps):
        msg = MotorsState()
        data = []
        for motor_id, value in zip((1, 2, 3, 4), rps):
            s = MotorState()
            s.id = motor_id
            s.rps = float(value)
            data.append(s)
        msg.data = data
        self.pub.publish(msg)

    def stop(self):
        for _ in range(5):
            self.send((0.0, 0.0, 0.0, 0.0))
            time.sleep(0.02)

    def drive(self, pattern):
        """Run one pattern; return the motion (dx, dy, dyaw) in the robot's starting frame."""
        self.wait(SETTLE_SEC)
        x0, y0, a0 = self.pose
        end = time.time() + RUN_SEC
        while time.time() < end:
            self.send([SPEED_RPS * v for v in pattern])
            self.wait(0.05)
        self.stop()
        self.wait(SETTLE_SEC)
        x1, y1, a1 = self.pose
        dx, dy = x1 - x0, y1 - y0
        c, s = math.cos(a0), math.sin(a0)
        return (c * dx + s * dy, -s * dx + c * dy, wrap(a1 - a0))


def main():
    print(__doc__)
    if input('Teleop stopped, robot ON THE FLOOR, half a metre clear? type yes: ').strip() != 'yes':
        print('aborted, nothing sent.')
        return 1

    rclpy.init()
    node = Calibrator()
    try:
        node.wait(3.0)
        if node.pose is None:
            print('No /odom_rf2o. Start base.launch.py (or slam.launch.py) first, with the lidar on.')
            return 1

        measured = []
        for pattern in HADAMARD:
            fwd = node.drive(pattern)
            back = node.drive([-v for v in pattern])
            m = tuple((f - b) / 2.0 for f, b in zip(fwd, back))
            measured.append(m)
            print('pattern %-16s dx=%+.3f m  dy=%+.3f m  dyaw=%+6.1f deg'
                  % (pattern, m[0], m[1], math.degrees(m[2])))
    finally:
        node.stop()

    # Per-motor response: column i = 1/4 * sum_j measured_j * H[j][i]  (H is its own inverse x4)
    cols = []
    for i in range(4):
        cols.append(tuple(sum(measured[j][a] * HADAMARD[j][i] for j in range(4)) / 4.0
                          for a in range(3)))

    print()
    print('motor  response(dx, dy, dyaw)        -> corner')
    params = {}
    for motor_id, (vx, vy, wz) in zip((1, 2, 3, 4), cols):
        sign = 1 if vx >= 0 else -1
        key = (1, 1 if vy * sign >= 0 else -1, 1 if wz * sign >= 0 else -1)
        corner = CORNERS[key]
        print('  %d    (%+.3f, %+.3f, %+6.1f deg)   -> %s%s'
              % (motor_id, vx, vy, math.degrees(wz), corner, '' if sign > 0 else ', REVERSED'))
        params.setdefault(corner, []).append((motor_id, sign))

    print()
    if sorted(params) != sorted(CORNERS.values()) or any(len(v) != 1 for v in params.values()):
        print('AMBIGUOUS: two motors landed on the same corner, so the result is not trusted.')
        print('Usual causes: wheels slipping on a smooth floor, lidar not seeing enough of the')
        print('room, or something else driving the wheels. Fix that and run it again.')
        return 1

    print('Put this in config/mecanum_base.yaml:')
    for corner in ('front_left', 'rear_left', 'front_right', 'rear_right'):
        print('    id_%-16s %d' % (corner + ':', params[corner][0][0]))
    for corner in ('front_left', 'rear_left', 'front_right', 'rear_right'):
        print('    invert_%-12s %.1f' % (corner + ':', float(params[corner][0][1])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
