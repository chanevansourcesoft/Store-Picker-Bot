#!/usr/bin/env python3
"""Check the three things navigation depends on: lidar, localization, collision protection.

Read-only. Commands nothing, changes nothing. Run with the full stack up and the robot
STANDING STILL:

    ros2 launch mecanum_nav navigation.launch.py map:=map_confrence      # terminal 1
    python3 check_safety.py                                              # terminal 2

Each section prints PASS / WARN / FAIL with the measured number behind it, so a later argument
about "localization is bad" has evidence instead of impressions.
"""

import math
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy, qos_profile_sensor_data
from geometry_msgs.msg import PoseWithCovarianceStamped, PolygonStamped
from nav_msgs.msg import OccupancyGrid, Odometry
from sensor_msgs.msg import LaserScan

LATCHED = QoSProfile(depth=1)
LATCHED.durability = DurabilityPolicy.TRANSIENT_LOCAL
LATCHED.reliability = ReliabilityPolicy.RELIABLE

OK, WARN, FAIL = 'PASS', 'WARN', 'FAIL'
results = []


def say(section, verdict, detail):
    results.append((section, verdict, detail))
    mark = {'PASS': '[ PASS ]', 'WARN': '[ WARN ]', 'FAIL': '[ FAIL ]'}[verdict]
    print('%s %-22s %s' % (mark, section, detail))


class Check(Node):
    def __init__(self):
        super().__init__('check_safety')
        self.scans = []
        self.amcl = None
        self.odom = []
        self.local_cm = None
        self.poly = 0
        self.create_subscription(LaserScan, '/scan_raw',
                                 lambda m: self.scans.append(m), qos_profile_sensor_data)
        self.create_subscription(PoseWithCovarianceStamped, '/amcl_pose',
                                 lambda m: setattr(self, 'amcl', m), 10)
        self.create_subscription(Odometry, '/odom', lambda m: self.odom.append(m), 10)
        self.create_subscription(OccupancyGrid, '/local_costmap/costmap',
                                 lambda m: setattr(self, 'local_cm', m), LATCHED)
        self.create_subscription(PolygonStamped, '/polygon_approach',
                                 lambda m: setattr(self, 'poly', self.poly + 1), 10)


def wrap(a):
    return math.atan2(math.sin(a), math.cos(a))


def check_lidar(n):
    print('\n--- 1. LIDAR ---')
    if not n.scans:
        say('scan data', FAIL, 'nothing on /scan_raw. Lidar is down; nothing below is meaningful.')
        return False
    scan = n.scans[-1]
    rate = len(n.scans) / DURATION
    verdict = OK if rate > 8 else (WARN if rate > 4 else FAIL)
    say('scan rate', verdict, '%.1f Hz over %.0f s (expect 10-14)' % (rate, DURATION))

    valid = sum(1 for r in scan.ranges
                if not math.isinf(r) and not math.isnan(r) and scan.range_min < r < scan.range_max)
    pct = 100.0 * valid / len(scan.ranges)
    verdict = OK if pct > 70 else (WARN if pct > 45 else FAIL)
    say('valid returns', verdict, '%d of %d beams (%.0f%%)' % (valid, len(scan.ranges), pct))

    # Blind sectors in ROBOT frame. lidar_frame is yawed pi from base_footprint.
    # Indoors every direction should have a wall within range, so a sector with no returns at
    # all is the robot occluding its own lidar -- a direction it cannot see a collision coming.
    B = 24                      # 15 degree buckets
    hit = [0] * B
    tot = [0] * B
    for m in n.scans[-8:]:
        for i, r in enumerate(m.ranges):
            a = wrap(m.angle_min + i * m.angle_increment + math.pi)
            b = int((math.degrees(a) + 180) / 15) % B
            tot[b] += 1
            if not math.isinf(r) and not math.isnan(r) and m.range_min < r < m.range_max:
                hit[b] += 1
    blind = [(b * 15 - 180) for b in range(B) if tot[b] and hit[b] / tot[b] < 0.10]

    def name(deg):
        d = wrap(math.radians(deg + 7.5))
        d = math.degrees(d)
        if -45 <= d <= 45: return 'FRONT'
        if 45 < d <= 135: return 'LEFT'
        if -135 <= d < -45: return 'RIGHT'
        return 'REAR'

    if not blind:
        say('angular coverage', OK, 'no blind sector: the robot can see a collision coming from '
                                    'every direction')
    else:
        where = sorted({name(b) for b in blind})
        sectors = ', '.join('%+d..%+d' % (b, b + 15) for b in blind)
        verdict = FAIL if ('FRONT' in where or 'REAR' in where) else WARN
        say('angular coverage', verdict,
            'BLIND at %s  (%s). Obstacles there are invisible to the lidar.' % (sectors, '/'.join(where)))
    return True


def check_localization(n):
    print('\n--- 2. LOCALIZATION ---')
    if n.amcl is None:
        say('amcl pose', FAIL, 'no /amcl_pose. Set an initial pose in RViz (2D Pose Estimate).')
    else:
        c = n.amcl.pose.covariance
        sx, sy, syaw = math.sqrt(max(c[0], 0)), math.sqrt(max(c[7], 0)), math.sqrt(max(c[35], 0))
        verdict = OK if (sx < 0.15 and sy < 0.15 and syaw < 0.15) else (
            WARN if (sx < 0.4 and sy < 0.4 and syaw < 0.4) else FAIL)
        say('amcl confidence', verdict,
            'std dev x %.3f m, y %.3f m, yaw %.3f rad  (tight = sure of itself)' % (sx, sy, syaw))

    if len(n.odom) >= 2:
        def yaw(q):
            return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))
        a, b = n.odom[0], n.odom[-1]
        dx = b.pose.pose.position.x - a.pose.pose.position.x
        dy = b.pose.pose.position.y - a.pose.pose.position.y
        dyaw = math.degrees(wrap(yaw(b.pose.pose.orientation) - yaw(a.pose.pose.orientation)))
        drift = math.hypot(dx, dy)
        per_min = abs(dyaw) / DURATION * 60
        verdict = OK if per_min < 2.0 else (WARN if per_min < 6.0 else FAIL)
        say('odom drift (parked)', verdict,
            '%.3f m and %+.2f deg in %.0f s  =  %.1f deg/min' % (drift, dyaw, DURATION, per_min))
    else:
        say('odom drift', FAIL, 'no /odom. The EKF is not running.')


def check_collision(n):
    print('\n--- 3. COLLISION PROTECTION ---')
    if n.local_cm is None:
        say('local costmap', FAIL, 'no /local_costmap/costmap')
        return
    m = n.local_cm
    d = list(m.data)
    lethal = sum(1 for v in d if v >= 99)
    say('local costmap', OK if lethal else WARN,
        '%dx%d, %d lethal cells from live lidar' % (m.info.width, m.info.height, lethal))

    # Where are the obstacles relative to the robot? The robot sits at the centre of a rolling
    # local costmap, so this says which directions are currently protected by real readings.
    w, h, res = m.info.width, m.info.height, m.info.resolution
    cx, cy = w / 2.0, h / 2.0
    quad = {'FRONT': 0, 'REAR': 0, 'LEFT': 0, 'RIGHT': 0}
    for idx, v in enumerate(d):
        if v < 99:
            continue
        gx, gy = idx % w, idx // w
        ex, ey = (gx - cx) * res, (gy - cy) * res
        if abs(ex) >= abs(ey):
            quad['FRONT' if ex > 0 else 'REAR'] += 1
        else:
            quad['LEFT' if ey > 0 else 'RIGHT'] += 1
    say('obstacles seen', OK,
        'FRONT %d | REAR %d | LEFT %d | RIGHT %d lethal cells' %
        (quad['FRONT'], quad['REAR'], quad['LEFT'], quad['RIGHT']))

    # The collision monitor only publishes its polygon while it is actually guarding.
    if n.poly:
        say('collision monitor', OK, 'publishing its approach polygon (%d msgs)' % n.poly)
    else:
        say('collision monitor', WARN,
            'no /polygon_approach seen. It only publishes while a velocity command is flowing, '
            'so this is expected on a parked robot -- recheck while driving.')


DURATION = 25

def main():
    print(__doc__)
    rclpy.init()
    n = Check()
    print('Measuring for %d s -- keep the robot STILL...' % DURATION)
    end = time.time() + DURATION
    while time.time() < end:
        rclpy.spin_once(n, timeout_sec=0.2)

    alive = check_lidar(n)
    check_localization(n)
    if alive:
        check_collision(n)

    print('\n' + '=' * 62)
    bad = [r for r in results if r[1] == FAIL]
    warn = [r for r in results if r[1] == WARN]
    if bad:
        print('%d FAIL, %d WARN -- fix the FAILs first:' % (len(bad), len(warn)))
        for s, _, d in bad:
            print('   %s: %s' % (s, d))
    elif warn:
        print('No failures, %d warning(s).' % len(warn))
    else:
        print('All checks passed.')
    n.destroy_node()
    rclpy.shutdown()
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
