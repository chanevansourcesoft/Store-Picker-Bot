#!/usr/bin/env python3
"""Drive each of the six basic motions, ask what the robot actually did, and diagnose.

Run this with the robot ON THE GROUND with clear space around it. It commands one motion at a
time for a short burst, you pick what you saw from a list, and at the end it says which setting
is wrong and what to change it to.

Why it asks about ROBOT motion rather than wheel direction: on a mecanum wheel the rollers spin
visibly while the hub turns, so "which way is that wheel going" is genuinely easy to misread.
Which way the whole robot went is not.

Needs base.launch.py running in another terminal.

    python3 verify_motion.py
"""

import sys
import time

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist

BURST = 1.6
SPEED_XY = 0.15
SPEED_W = 0.9

# key, label, twist, the answer that means "correct"
TESTS = [
    ('w', 'FORWARD',      (SPEED_XY, 0.0, 0.0),  'f'),
    ('s', 'BACKWARD',     (-SPEED_XY, 0.0, 0.0), 'b'),
    ('a', 'STRAFE LEFT',  (0.0, SPEED_XY, 0.0),  'l'),
    ('d', 'STRAFE RIGHT', (0.0, -SPEED_XY, 0.0), 'r'),
    ('q', 'ROTATE LEFT  (anticlockwise seen from above)', (0.0, 0.0, SPEED_W), 'ccw'),
    ('e', 'ROTATE RIGHT (clockwise seen from above)',     (0.0, 0.0, -SPEED_W), 'cw'),
]

CHOICES = {
    'f': 'drove forward',
    'b': 'drove backward',
    'l': 'slid left (heading unchanged)',
    'r': 'slid right (heading unchanged)',
    'ccw': 'rotated anticlockwise on the spot',
    'cw': 'rotated clockwise on the spot',
    'scrub': 'wheels turned but it barely moved / juddered / fought itself',
    'other': 'something else (diagonal, curve, circle)',
    'none': 'nothing moved at all',
}


class Driver(Node):
    def __init__(self, topic):
        super().__init__('verify_motion')
        self.pub = self.create_publisher(Twist, topic, 1)

    def burst(self, vx, vy, wz, seconds):
        t = Twist()
        t.linear.x, t.linear.y, t.angular.z = vx, vy, wz
        end = time.time() + seconds
        while time.time() < end:
            self.pub.publish(t)
            rclpy.spin_once(self, timeout_sec=0.05)
        self.stop()

    def stop(self):
        t = Twist()
        for _ in range(10):
            self.pub.publish(t)
            rclpy.spin_once(self, timeout_sec=0.02)


def ask(label):
    print('    What did the robot do?')
    for k, v in CHOICES.items():
        print('      %-6s %s' % (k, v))
    while True:
        a = input('    > ').strip().lower()
        if a in CHOICES:
            return a
        print('    pick one of: %s' % ', '.join(CHOICES))


def diagnose(res):
    """res maps test key -> observed answer."""
    print()
    print('=' * 72)
    print('DIAGNOSIS')
    print('=' * 72)

    ok = [k for k, label, tw, want in TESTS if res.get(k) == want]
    print('Correct: %s' % (', '.join(ok) if ok else 'none'))
    print()

    findings = []

    # Global forward/back flip.
    if res.get('w') == 'b' and res.get('s') == 'f':
        findings.append(
            'FORWARD AND BACKWARD ARE SWAPPED.\n'
            '  Flip the sign of ALL FOUR invert_* values in config/mecanum_base.yaml.\n'
            '  This is the one global ambiguity; it does not affect strafe or rotation.')

    # Drive and strafe have traded places -> the inverted set is wrong, by a diagonal.
    if res.get('w') in ('l', 'r') and res.get('a') in ('f', 'b'):
        findings.append(
            'DRIVE AND STRAFE HAVE TRADED PLACES.\n'
            '  A diagonal pair of motors is wired with the polarity opposite to what\n'
            '  config/mecanum_base.yaml says. Flip the two invert_* values that are currently\n'
            '  equal to each other on a diagonal (FL+RR, or RL+FR) and retest.')

    # Strafe mirrored.
    if res.get('a') == 'r' and res.get('d') == 'l':
        findings.append(
            'STRAFE IS MIRRORED (a goes right, d goes left).\n'
            '  The LEFT and RIGHT id pairs are swapped. In config/mecanum_base.yaml exchange\n'
            '  id_front_left <-> id_front_right and id_rear_left <-> id_rear_right.')

    # Rotation mirrored.
    if res.get('q') == 'cw' and res.get('e') == 'ccw':
        findings.append(
            'ROTATION IS MIRRORED.\n'
            '  Left and right are swapped. Exchange id_front_left <-> id_front_right and\n'
            '  id_rear_left <-> id_rear_right in config/mecanum_base.yaml.')

    # Front/back swapped shows up as rotation working but strafe inverted-per-axle.
    if res.get('w') == 'f' and res.get('a') in ('scrub', 'other') and res.get('d') in ('scrub', 'other'):
        findings.append(
            'DRIVING AND ROTATING WORK, BUT STRAFE DOES NOT. This is MECHANICAL, not software.\n'
            '  Strafe is the only motion that depends on the 45-degree rollers opposing each\n'
            '  other. Seen from above, the roller axes of the four wheels must form an X:\n'
            '  front-left must match rear-right, and front-right must match rear-left.\n'
            '  Mecanum wheels come in left-hand and right-hand versions; if two on a diagonal\n'
            '  are the same hand, the sideways forces cancel instead of adding and the robot\n'
            '  just scrubs. No software setting can compensate -- the wheels have to be moved\n'
            '  to the correct corners.\n'
            '  Check: spin each wheel and look down on it. The rollers on FL and RR should\n'
            '  lean the same way; FR and RL should lean the other way.')

    if res.get('none') or all(v == 'none' for v in res.values()):
        findings.append('NOTHING MOVED. Is base.launch.py running, and is the battery on?')

    if not findings and len(ok) == 6:
        print('All six motions correct. The base is calibrated.')
        print('config/mecanum_base.yaml is right as it stands -- commit it.')
        return

    if not findings:
        findings.append(
            'The pattern does not match a known single fault. Send this table back:\n' +
            '\n'.join('    %-4s commanded %-12s -> observed %s'
                      % (k, label, CHOICES[res.get(k, 'none')])
                      for k, label, tw, want in TESTS))

    for i, f in enumerate(findings, 1):
        print('%d. %s' % (i, f))
        print()


def main():
    topic = '/controller/cmd_vel'
    if len(sys.argv) > 1:
        topic = sys.argv[1]

    print(__doc__)
    print('Commanding on %s at %.2f m/s / %.2f rad/s, %.1f s per burst.'
          % (topic, SPEED_XY, SPEED_W, BURST))
    print('Robot ON THE GROUND, at least a metre clear all round.')
    if input('Ready? type yes: ').strip().lower() != 'yes':
        print('aborted.')
        return 1

    rclpy.init()
    node = Driver(topic)
    res = {}
    try:
        for key, label, twist, want in TESTS:
            print()
            print('--- key %s : commanding %s ---' % (key, label))
            input('    press Enter to run... ')
            node.burst(twist[0], twist[1], twist[2], BURST)
            res[key] = ask(label)
        diagnose(res)
    finally:
        node.stop()
        node.destroy_node()
        rclpy.try_shutdown()
    return 0


if __name__ == '__main__':
    sys.exit(main())
