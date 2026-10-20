#!/usr/bin/env python3
"""Mecanum wheel kinematics.

What makes a mecanum wheel different from a normal one: around its rim sit free-spinning rollers
whose axes are at 45 degrees to the hub axis. Driving the hub therefore produces force along the
roller axis, i.e. at 45 degrees, not straight ahead. Combine four such wheels (rollers mirrored
left-to-right so the diagonals form an X seen from above) and the 45-degree components add up or
cancel depending on the pattern of wheel directions:

    all four forward                  -> drive forward     (side components cancel)
    left pair fwd, right pair back    -> rotate in place
    FL + RR forward, FR + RL back     -> strafe RIGHT       (forward components cancel)
    FL + RR back, FR + RL forward     -> strafe LEFT

That third and fourth case is the whole point: the robot translates sideways at 90 degrees to its
heading without ever changing heading. No steering, no three-point turn.

Convention is REP-103, as the rest of the stack assumes: +x forward, +y LEFT, +z up, so a positive
angular.z is counter-clockwise seen from above.

The rollers are passive. Nothing commands them and nothing measures them; they are a mechanical
consequence of driving the hubs. Only the four hub speeds are ever computed here.
"""

import math


class MecanumKinematics:
    """Converts a body twist to four wheel speeds, and back again."""

    def __init__(self, wheelbase, track_width, wheel_diameter):
        self.wheelbase = float(wheelbase)
        self.track_width = float(track_width)
        self.wheel_diameter = float(wheel_diameter)

        # The rotation lever arm. For mecanum this is the SUM of the half-wheelbase and
        # half-track, not either alone: a wheel's 45-degree roller axis means both offsets
        # contribute to the yaw moment it can produce.
        self.k = (self.wheelbase + self.track_width) / 2.0

    @property
    def wheel_radius(self):
        return self.wheel_diameter / 2.0

    def mps_to_rps(self, v):
        """Wheel rim speed in m/s -> revolutions per second (what the board's motor API takes)."""
        return v / (math.pi * self.wheel_diameter)

    def rps_to_mps(self, rps):
        return rps * math.pi * self.wheel_diameter

    def inverse(self, vx, vy, wz):
        """Body twist -> rim speed (m/s) of each wheel.

        Returns (front_left, rear_left, front_right, rear_right).

        The signs are the X-roller mecanum standard. Note vy enters with OPPOSITE sign on the
        diagonals -- that is the strafe, and it is what you would lose by treating these as
        ordinary wheels.
        """
        kw = self.k * wz
        front_left = vx - vy - kw
        rear_left = vx + vy - kw
        front_right = vx + vy + kw
        rear_right = vx - vy + kw
        return front_left, rear_left, front_right, rear_right

    def forward(self, v_fl, v_rl, v_fr, v_rr):
        """Four wheel rim speeds (m/s) -> body twist (vx, vy, wz). The inverse of inverse().

        Used for odometry when real wheel feedback is available. With four wheels and three
        degrees of freedom the system is overdetermined, so this is the least-squares solution,
        which for the symmetric layout reduces to plain averages.
        """
        vx = (v_fl + v_rl + v_fr + v_rr) / 4.0
        vy = (-v_fl + v_rl + v_fr - v_rr) / 4.0
        wz = (-v_fl - v_rl + v_fr + v_rr) / (4.0 * self.k)
        return vx, vy, wz

    def scale_to_limit(self, speeds, max_wheel_mps):
        """Shrink a set of wheel speeds so none exceeds max_wheel_mps, preserving direction.

        Needed because the three twist components add per wheel: vx 0.4 plus vy 0.4 plus a yaw
        term asks one wheel for well over 0.8 m/s. Clamping each wheel on its own would distort
        the motion into something other than what was commanded -- a commanded straight strafe
        would curve. Scaling all four by one factor keeps the direction exact and only reduces
        the speed.
        """
        peak = max(abs(s) for s in speeds)
        if max_wheel_mps <= 0.0 or peak <= max_wheel_mps:
            return speeds, 1.0
        factor = max_wheel_mps / peak
        return tuple(s * factor for s in speeds), factor
