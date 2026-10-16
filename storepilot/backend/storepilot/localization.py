"""
Localization assist on the Pi: match the live LiDAR scan against the Nav2 map.

It only ever helps the operator's own estimate. Given a rough pose (from "Set position"
or AMCL's current pose) it searches a small window (default +-0.5 m, +-16 deg) for the
pose where the scan best fits the walls, then StorePilot sends that pose to AMCL on
/initialpose, exactly like a careful RViz "2D Pose Estimate". Nothing runs on the Jetson.

quality(): share of scan points within 10 cm of a wall at a given pose (0..1).
"""

import math
from collections import deque

import numpy as np

SIGMA = 0.08        # m, how sharply the score falls off with distance to a wall
HIT_DIST = 0.10     # m, a scan point this close to a wall counts as "on the wall"


class LikelihoodField:
    def __init__(self, info, data, max_dist=0.5):
        self.w, self.h = info["width"], info["height"]
        self.res = info["resolution"]
        self.ox = info["origin"]["position"]["x"]
        self.oy = info["origin"]["position"]["y"]
        self.max_dist = max_dist
        grid = np.asarray(data, dtype=np.int16).reshape(self.h, self.w)
        occupied = grid >= 65
        self.unknown = grid < 0
        # distance to the nearest wall in cells (8-neighbour chamfer, capped at max_dist)
        steps = int(max_dist / self.res) + 1
        dist = np.where(occupied, 0.0, float(steps)).astype(np.float32)
        diag = math.sqrt(2.0)
        for _ in range(steps):
            d = dist.copy()
            d[1:, :] = np.minimum(d[1:, :], dist[:-1, :] + 1)
            d[:-1, :] = np.minimum(d[:-1, :], dist[1:, :] + 1)
            d[:, 1:] = np.minimum(d[:, 1:], dist[:, :-1] + 1)
            d[:, :-1] = np.minimum(d[:, :-1], dist[:, 1:] + 1)
            d[1:, 1:] = np.minimum(d[1:, 1:], dist[:-1, :-1] + diag)
            d[:-1, :-1] = np.minimum(d[:-1, :-1], dist[1:, 1:] + diag)
            d[1:, :-1] = np.minimum(d[1:, :-1], dist[:-1, 1:] + diag)
            d[:-1, 1:] = np.minimum(d[:-1, 1:], dist[1:, :-1] + diag)
            if np.array_equal(d, dist):
                break
            dist = d
        self.dist = np.minimum(dist * self.res, max_dist)

    def _distances(self, points, poses):
        """points (M, 2) in the robot base frame, poses (N, 3) map->base. Returns (N, M) metres."""
        x, y, t = poses[:, 0:1], poses[:, 1:2], poses[:, 2:3]
        c, s = np.cos(t), np.sin(t)
        px = x + c * points[:, 0] - s * points[:, 1]
        py = y + s * points[:, 0] + c * points[:, 1]
        col = np.floor((px - self.ox) / self.res).astype(np.int64)
        row = np.floor((py - self.oy) / self.res).astype(np.int64)
        inside = (col >= 0) & (col < self.w) & (row >= 0) & (row < self.h)
        d = np.full(col.shape, self.max_dist, dtype=np.float32)
        d[inside] = self.dist[row[inside], col[inside]]
        return d

    def clearance(self, x, y):
        """(distance to the nearest wall in m, capped at max_dist; 'unknown' / 'outside' / None)."""
        c = int(math.floor((x - self.ox) / self.res))
        r = int(math.floor((y - self.oy) / self.res))
        if not (0 <= c < self.w and 0 <= r < self.h):
            return 0.0, "outside"
        if self.unknown[r, c]:
            return 0.0, "unknown"
        return float(self.dist[r, c]), None

    def reachable(self, x, y, min_clear):
        """Cells the robot centre can reach from (x, y) without coming closer than min_clear to a wall
        (4-connected flood fill). Cached per start area. None if (x, y) is outside the map."""
        c0 = int(math.floor((x - self.ox) / self.res))
        r0 = int(math.floor((y - self.oy) / self.res))
        if not (0 <= c0 < self.w and 0 <= r0 < self.h):
            return None
        key = (r0 // 4, c0 // 4, round(min_clear, 2))
        cache = getattr(self, "_reach_cache", {})
        if key in cache:
            return cache[key]
        free = (self.dist >= min_clear) & ~self.unknown
        start = None
        # the robot may stand a little closer to a wall than min_clear: start from the nearest free cell
        for rad in range(0, 8):
            rs = slice(max(0, r0 - rad), min(self.h, r0 + rad + 1))
            cs = slice(max(0, c0 - rad), min(self.w, c0 + rad + 1))
            idx = np.argwhere(free[rs, cs])
            if len(idx):
                start = (int(idx[0][0]) + rs.start, int(idx[0][1]) + cs.start)
                break
        seen = np.zeros_like(free)
        if start is not None:
            q = deque([start])
            seen[start] = True
            while q:
                r, c = q.popleft()
                for nr, nc in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                    if 0 <= nr < self.h and 0 <= nc < self.w and free[nr, nc] and not seen[nr, nc]:
                        seen[nr, nc] = True
                        q.append((nr, nc))
        if len(cache) > 20:
            cache.clear()
        cache[key] = seen
        self._reach_cache = cache
        return seen

    def score(self, points, poses):
        d = self._distances(points, poses)
        return np.exp(-(d * d) / (2 * SIGMA * SIGMA)).mean(axis=1)

    def quality(self, points, pose):
        if len(points) == 0:
            return None
        d = self._distances(points, np.asarray([pose], dtype=np.float64))
        return float((d <= HIT_DIST).mean())

    def refine(self, points, pose, window=0.5, angle_deg=16.0):
        """Best pose near `pose` (x, y, theta). Returns (pose, quality_before, quality_after)."""
        if len(points) < 20:
            return tuple(pose), None, None
        x0, y0, t0 = pose

        def search(pts, cx, cy, ct, half, step, half_t, step_t):
            xs = np.arange(-half, half + 1e-9, step) + cx
            ys = np.arange(-half, half + 1e-9, step) + cy
            ts = np.arange(-half_t, half_t + 1e-9, step_t) + ct
            gx, gy, gt = np.meshgrid(xs, ys, ts, indexing="ij")
            poses = np.stack([gx.ravel(), gy.ravel(), gt.ravel()], axis=1)
            best, best_score = None, -1.0
            for chunk in np.array_split(poses, max(1, len(poses) // 1500)):
                scores = self.score(pts, chunk)
                i = int(np.argmax(scores))
                if scores[i] > best_score:
                    best, best_score = chunk[i], float(scores[i])
            return best, best_score

        coarse = points[:: max(1, len(points) // 120)]   # fewer points for the wide search
        a = math.radians(angle_deg)
        best, _ = search(coarse, x0, y0, t0, window, 0.05, a, math.radians(2.0))
        best, _ = search(points, best[0], best[1], best[2], 0.06, 0.02, math.radians(2.5), math.radians(0.5))
        best, _ = search(points, best[0], best[1], best[2], 0.02, 0.01, math.radians(0.6), math.radians(0.2))
        before = self.quality(points, (x0, y0, t0))
        after = self.quality(points, tuple(best))
        return (float(best[0]), float(best[1]), float(math.atan2(math.sin(best[2]), math.cos(best[2])))), before, after


def scan_points_base(scan, laser_in_base, max_points=360):
    """LaserScan ranges -> (M, 2) points in the robot base frame."""
    frame, a0, inc, ranges, rmax = scan[:5]
    r = np.asarray([v if v is not None else np.nan for v in ranges], dtype=np.float64)
    angles = a0 + inc * np.arange(len(r))
    ok = np.isfinite(r) & (r > 0.05) & (r < rmax)
    r, angles = r[ok], angles[ok]
    if len(r) > max_points:
        keep = np.linspace(0, len(r) - 1, max_points).astype(int)
        r, angles = r[keep], angles[keep]
    lx, ly, lt = laser_in_base
    return np.stack([lx + r * np.cos(lt + angles), ly + r * np.sin(lt + angles)], axis=1)
