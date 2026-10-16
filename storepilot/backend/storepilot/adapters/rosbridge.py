"""
Live robot over rosbridge (ROS 2 Nav2 on the Jetson). Nothing has to be installed on the
Jetson except running rosbridge_websocket. The dashboard sends ONLY what RViz sends:

    /initialpose          "2D Pose Estimate"
    /goal_pose            "Nav2 Goal" (bt_navigator turns it into NavigateToPose)
    cancel_goal + lifecycle pause / resume of Nav2      STOP / Resume
    /map_server/load_map  switch environment

It never publishes /cmd_vel. On the Jetson, rosbridge is started with an allow-list
(see storepilot/README.md) so /cmd_vel cannot be published through it at all.

Telemetry comes straight from ROS topics: pose from TF map -> odom -> base_footprint,
speed from /odom, battery voltage from the robot board, LiDAR /scan, Nav2 /plan.
"""

import itertools
import json
import math
import threading
import time
import zlib
import struct

from websockets.sync.client import connect

from .base import AdapterError, RobotAdapter

GOAL_STATUS = {0: "unknown", 1: "accepted", 2: "executing", 3: "canceling", 4: "succeeded",
               5: "canceled", 6: "aborted"}
MANAGE = {"startup": 0, "pause": 1, "resume": 2}


# ---------------------------------------------------------------- 2D TF tree

def yaw_of(q):
    return math.atan2(2.0 * (q["w"] * q["z"] + q["x"] * q["y"]), 1.0 - 2.0 * (q["y"] ** 2 + q["z"] ** 2))


def quat(yaw):
    return {"x": 0.0, "y": 0.0, "z": math.sin(yaw / 2), "w": math.cos(yaw / 2)}


def compose(a, b):
    """a then b, both (x, y, yaw)."""
    ax, ay, at = a
    bx, by, bt = b
    return (ax + bx * math.cos(at) - by * math.sin(at), ay + bx * math.sin(at) + by * math.cos(at), at + bt)


class TfTree:
    def __init__(self):
        self.edges = {}   # child -> (parent, (x, y, yaw), monotonic time, static)

    def add(self, msg, static=False):
        now = time.monotonic()
        for t in msg.get("transforms", []):
            child = t["child_frame_id"].lstrip("/")
            parent = t["header"]["frame_id"].lstrip("/")
            tr = t["transform"]
            self.edges[child] = (parent, (tr["translation"]["x"], tr["translation"]["y"], yaw_of(tr["rotation"])),
                                 now, static)

    def lookup(self, target, frame, max_age=None):
        """Pose of `frame` in `target` as (x, y, yaw), or None. max_age applies to dynamic edges."""
        chain, cur, now = [], frame, time.monotonic()
        for _ in range(20):
            if cur == target:
                pose = (0.0, 0.0, 0.0)
                for p in reversed(chain):
                    pose = compose(pose, p)
                return pose
            edge = self.edges.get(cur)
            if edge is None:
                return None
            parent, pose, t, static = edge
            if max_age is not None and not static and now - t > max_age:
                return None
            chain.append(pose)
            cur = parent
        return None

    def age(self, child):
        edge = self.edges.get(child)
        return None if edge is None else time.monotonic() - edge[2]


# ---------------------------------------------------------------- map image

def grid_to_png(info, data):
    """nav_msgs/OccupancyGrid -> palette PNG (dark theme). Row 0 of the grid is the bottom."""
    w, h = info["width"], info["height"]
    raw = bytearray()
    for row in range(h - 1, -1, -1):
        raw.append(0)  # filter: none
        for v in data[row * w:(row + 1) * w]:
            raw.append(0 if v < 0 else 2 if v >= 65 else 1)
    palette = bytes([13, 19, 26,  27, 36, 46,  200, 212, 224])   # unknown, free, occupied

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 3, 0, 0, 0)) +
            chunk(b"PLTE", palette) + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


# ---------------------------------------------------------------- connection

class RosbridgeLink:
    """One websocket to rosbridge, in a background thread, reconnecting forever."""

    def __init__(self, url, subscriptions, advertise, on_message, on_connect=None):
        self.url = url
        self.subscriptions = subscriptions   # [(topic, type, throttle_ms)]
        self.advertise = advertise           # [(topic, type)]
        self.on_message = on_message         # (topic, msg)
        self.on_connect = on_connect
        self.ws = None
        self.send_lock = threading.Lock()
        self.ids = itertools.count(1)
        self.pending = {}                    # id -> [Event, response]
        self.connected = False
        self.last_error = None
        self.last_message_at = None
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="rosbridge", daemon=True)

    def start(self):
        self.thread.start()

    def shutdown(self):
        self._stop.set()
        if self.ws:
            self.ws.close()

    def _send(self, payload):
        ws = self.ws
        if ws is None or not self.connected:
            raise AdapterError("rosbridge not connected")
        with self.send_lock:
            ws.send(json.dumps(payload))

    def _run(self):
        while not self._stop.is_set():
            try:
                with connect(self.url, open_timeout=3, max_size=None, ping_interval=5, ping_timeout=5) as ws:
                    self.ws = ws
                    self.connected = True
                    self.last_error = None
                    for topic, kind, throttle in self.subscriptions:
                        self._send({"op": "subscribe", "id": f"sub:{topic}", "topic": topic, "type": kind,
                                    "throttle_rate": throttle, "queue_length": 1})
                    for topic, kind in self.advertise:
                        self._send({"op": "advertise", "id": f"adv:{topic}", "topic": topic, "type": kind})
                    if self.on_connect:
                        threading.Thread(target=self.on_connect, daemon=True).start()
                    for raw in ws:
                        self._handle(json.loads(raw))
            except Exception as e:
                self.last_error = f"{e.__class__.__name__}: {e}"[:200]
            finally:
                self.connected = False
                self.ws = None
                for event_box in list(self.pending.values()):
                    event_box[0].set()
            self._stop.wait(2.0)

    def _handle(self, msg):
        self.last_message_at = time.monotonic()
        op = msg.get("op")
        if op == "publish":
            self.on_message(msg.get("topic"), msg.get("msg") or {})
        elif op == "service_response":
            box = self.pending.get(msg.get("id"))
            if box:
                box[1] = msg
                box[0].set()
        elif op == "status" and msg.get("level") in ("error", "warning"):
            self.last_error = str(msg.get("msg"))[:200]

    def publish(self, topic, msg):
        self._send({"op": "publish", "topic": topic, "msg": msg})

    def call(self, service, args=None, service_type=None, timeout=5.0):
        """Call a ROS service. Returns (ok, values). Never raises."""
        call_id = f"call:{next(self.ids)}"
        box = [threading.Event(), None]
        self.pending[call_id] = box
        payload = {"op": "call_service", "id": call_id, "service": service, "args": args or {}}
        if service_type:
            payload["type"] = service_type
        try:
            self._send(payload)
        except AdapterError as e:
            self.pending.pop(call_id, None)
            return False, {"error": str(e)}
        box[0].wait(timeout)
        self.pending.pop(call_id, None)
        response = box[1]
        if response is None:
            return False, {"error": "no answer from rosbridge" if self.connected else "rosbridge disconnected"}
        return bool(response.get("result")), response.get("values") or {}


# ---------------------------------------------------------------- adapter

class RosbridgeRobot(RobotAdapter):
    source = "jetson"
    link_kind = "rosbridge"
    poll_sec = 0.25          # status is read from the local cache, polling is cheap

    def __init__(self, url, stations_store, maps_dir, maps, base_frame="base_footprint",
                 battery_topic="/ros_robot_controller/battery"):
        self.url = url
        self.stations = stations_store
        self.maps_dir = maps_dir.rstrip("/")
        self.maps = list(maps)
        self.base_frame = base_frame
        self.battery_topic = battery_topic
        self.lock = threading.Lock()
        self.tf = TfTree()
        self.map_info = None
        self.map_png = None
        self.map_version = 0
        self.map_name = None
        self.scan = None            # (frame, angle_min, angle_inc, ranges, range_max)
        self.plan = []
        self.odom_speed = None
        self.odom_at = None
        self.battery_v = None
        self.battery_at = None
        self.goal = None            # {"x","y","theta","sent_at","status"}
        self.goal_status = None
        self.nav_active = None      # lifecycle_manager_navigation is_active
        self.loc_active = None
        self.needs_initial_pose = False
        self.stopped = False        # STOP pressed and Nav2 paused, until resume
        self.last_latency_ms = None
        self.link = RosbridgeLink(
            url,
            subscriptions=[
                ("/map", "nav_msgs/msg/OccupancyGrid", 0),
                ("/tf", "tf2_msgs/msg/TFMessage", 0),
                ("/tf_static", "tf2_msgs/msg/TFMessage", 0),
                ("/odom", "nav_msgs/msg/Odometry", 200),
                ("/scan", "sensor_msgs/msg/LaserScan", 200),
                ("/plan", "nav_msgs/msg/Path", 500),
                (battery_topic, "std_msgs/msg/UInt16", 1000),
                ("/navigate_to_pose/_action/status", "action_msgs/msg/GoalStatusArray", 0),
            ],
            advertise=[("/goal_pose", "geometry_msgs/msg/PoseStamped"),
                       ("/initialpose", "geometry_msgs/msg/PoseWithCovarianceStamped")],
            on_message=self._on_message,
            on_connect=self._on_connect)
        self.link.start()
        threading.Thread(target=self._poll_lifecycle, name="nav2-lifecycle", daemon=True).start()

    # ---------- incoming ----------

    def _on_connect(self):
        ok, values = self.link.call("/rosapi/get_param", {"name": "/map_server:yaml_filename"}, timeout=3)
        if ok:
            try:
                path = json.loads(values.get("value", '""'))
            except (TypeError, ValueError):
                path = values.get("value", "")
            name = str(path).rsplit("/", 1)[-1].removesuffix(".yaml")
            if name:
                self.map_name = name

    def _on_message(self, topic, msg):
        if topic == "/tf":
            with self.lock:
                self.tf.add(msg)
        elif topic == "/tf_static":
            with self.lock:
                self.tf.add(msg, static=True)
        elif topic == "/map":
            png = grid_to_png(msg["info"], msg["data"])
            with self.lock:
                self.map_info = msg["info"]
                self.map_png = png
                self.map_version += 1
        elif topic == "/odom":
            tw = msg["twist"]["twist"]
            with self.lock:
                self.odom_speed = math.hypot(tw["linear"]["x"], tw["linear"]["y"])
                self.odom_turn = tw["angular"]["z"]
                self.odom_at = time.monotonic()
        elif topic == "/scan":
            with self.lock:
                self.scan = (msg["header"]["frame_id"].lstrip("/"), msg["angle_min"], msg["angle_increment"],
                             msg["ranges"], msg["range_max"], time.monotonic())
        elif topic == "/plan":
            poses = msg.get("poses", [])
            step = max(1, len(poses) // 120)
            with self.lock:
                self.plan = [(p["pose"]["position"]["x"], p["pose"]["position"]["y"]) for p in poses[::step]]
        elif topic == self.battery_topic:
            with self.lock:
                self.battery_v = msg.get("data", 0) / 1000.0
                self.battery_at = time.monotonic()
        elif topic == "/navigate_to_pose/_action/status":
            statuses = msg.get("status_list", [])
            if statuses:
                latest = max(statuses, key=lambda s: (s["goal_info"]["stamp"]["sec"], s["goal_info"]["stamp"]["nanosec"]))
                with self.lock:
                    self.goal_status = GOAL_STATUS.get(latest.get("status"), "unknown")

    def _poll_lifecycle(self):
        while True:
            if self.link.connected:
                start = time.monotonic()
                ok, v = self.link.call("/lifecycle_manager_navigation/is_active", {}, timeout=3)
                self.last_latency_ms = round((time.monotonic() - start) * 1000) if ok else None
                self.nav_active = bool(v.get("success")) if ok else None
                ok, v = self.link.call("/lifecycle_manager_localization/is_active", {}, timeout=3)
                self.loc_active = bool(v.get("success")) if ok else None
            else:
                self.nav_active = self.loc_active = None
            time.sleep(2.0)

    # ---------- derived ----------

    def pose(self):
        with self.lock:
            p = self.tf.lookup("map", self.base_frame, max_age=3.0)
        return None if p is None else {"x": p[0], "y": p[1], "theta": math.atan2(math.sin(p[2]), math.cos(p[2]))}

    def localized(self):
        with self.lock:
            age = self.tf.age("odom")
        return age is not None and age < 3.0 and not self.needs_initial_pose

    def scan_points(self, max_points=360):
        with self.lock:
            scan = self.scan
            if not scan or time.monotonic() - scan[5] > 2.0:
                return []
            frame, a0, inc, ranges, rmax, _ = scan
            origin = self.tf.lookup("map", frame, max_age=3.0)
        if origin is None:
            return []
        step = max(1, len(ranges) // max_points)
        pts = []
        ox, oy, ot = origin
        for i in range(0, len(ranges), step):
            r = ranges[i]
            if r is None or not (0.05 < r < rmax):
                continue
            a = ot + a0 + i * inc
            pts.append((round(ox + r * math.cos(a), 3), round(oy + r * math.sin(a), 3)))
        return pts

    # ---------- RobotAdapter telemetry ----------

    def fetch_health(self):
        if not self.link.connected:
            raise AdapterError(f"rosbridge {self.url}: {self.link.last_error or 'not connected'}")
        return {"status": "ok", "ros_available": True, "link": "rosbridge"}

    def fetch_status(self):
        if not self.link.connected:
            raise AdapterError(f"rosbridge {self.url}: {self.link.last_error or 'not connected'}")
        now = time.monotonic()
        pose = self.pose() if self.localized() else None
        with self.lock:
            speed = self.odom_speed if self.odom_at and now - self.odom_at < 2 else None
            battery_v = self.battery_v if self.battery_at and now - self.battery_at < 5 else None
            goal = dict(self.goal) if self.goal else None
            status = self.goal_status
        nav_state = "unknown"
        if self.nav_active is False:
            nav_state = "nav2_paused"
        elif goal and status in ("accepted", "executing"):
            nav_state = "navigating"
        elif goal and status:
            nav_state = {"succeeded": "arrived", "aborted": "failed", "canceled": "canceled"}.get(status, status)
        elif self.nav_active:
            nav_state = "idle"
        if goal and pose:
            goal["distance"] = round(math.hypot(goal["x"] - pose["x"], goal["y"] - pose["y"]), 2)
        if goal:
            goal["status"] = status
        return {
            "busy": nav_state == "navigating",
            "holding": None,
            "station": self._station_at(pose),
            "pose": pose,
            "battery": None,
            "battery_v": battery_v,
            "speed": speed,
            "nav_state": nav_state,
            "obstacle": None,
            "last_error": None,
            "stopped": self.stopped,
            "localized": self.localized(),
            "needs_initial_pose": self.needs_initial_pose,
            "nav2_active": self.nav_active,
            "localization_active": self.loc_active,
            "goal": goal,
            "goal_station": goal.get("station") if goal else None,
            "map_name": self.map_name,
        }

    def _station_at(self, pose, radius=0.3):
        if not pose:
            return None
        for name, s in self.stations.get(self.map_name).items():
            if math.hypot(s["x"] - pose["x"], s["y"] - pose["y"]) < radius:
                return name
        return None

    def fetch_map(self):
        if not self.link.connected:
            raise AdapterError("rosbridge not connected")
        stations = self.stations.get(self.map_name)
        return {"stations": stations}

    def live_layers(self):
        with self.lock:
            info = self.map_info
            plan = list(self.plan)
            version = self.map_version
        return {
            "map": None if info is None else {
                "name": self.map_name, "version": version, "width": info["width"], "height": info["height"],
                "resolution": info["resolution"],
                "origin": {"x": info["origin"]["position"]["x"], "y": info["origin"]["position"]["y"]},
            },
            "maps": self.maps,
            "scan": self.scan_points(),
            "plan": plan,
        }

    # ---------- commands (never raise) ----------

    def _fail(self, message):
        return {"success": False, "message": message}

    def send_goal(self, x, y, theta, station=None):
        """Like RViz "Nav2 Goal". Returns at once."""
        if self.stopped:
            return self._fail("robot is stopped: press Resume first")
        if self.nav_active is not True:
            return self._fail("Nav2 is not active")
        if not self.localized():
            return self._fail("robot is not localized: set its position first")
        stamp = {"sec": 0, "nanosec": 0}
        try:
            self.link.publish("/goal_pose", {"header": {"frame_id": "map", "stamp": stamp},
                                             "pose": {"position": {"x": x, "y": y, "z": 0.0}, "orientation": quat(theta)}})
        except AdapterError as e:
            return self._fail(str(e))
        with self.lock:
            self.goal = {"x": x, "y": y, "theta": theta, "sent_at": time.time(), "station": station}
            self.goal_status = "accepted"
        return {"success": True, "message": f"goal sent ({x:.2f}, {y:.2f})"}

    def set_initial_pose(self, x, y, theta):
        """Like RViz "2D Pose Estimate". Does not move the robot."""
        cov = [0.0] * 36
        cov[0], cov[7], cov[35] = 0.25, 0.25, 0.0685
        try:
            self.link.publish("/initialpose", {
                "header": {"frame_id": "map", "stamp": {"sec": 0, "nanosec": 0}},
                "pose": {"pose": {"position": {"x": x, "y": y, "z": 0.0}, "orientation": quat(theta)},
                         "covariance": cov}})
        except AdapterError as e:
            return self._fail(str(e))
        self.needs_initial_pose = False
        return {"success": True, "message": f"position set to ({x:.2f}, {y:.2f})"}

    def lifecycle(self, manager, command):
        ok, v = self.link.call(f"/lifecycle_manager_{manager}/manage_nodes", {"command": MANAGE[command]}, timeout=20)
        return ok and bool(v.get("success")), v

    def start_nav2(self):
        """Resume localization and navigation (they are paused, not shut down)."""
        results = []
        for manager in ("localization", "navigation"):
            ok, _ = self.lifecycle(manager, "resume")
            if not ok:
                ok, _ = self.lifecycle(manager, "startup")
            results.append(ok)
        if all(results):
            self.stopped = False
            self.nav_active = self.loc_active = True   # don't wait for the next 2 s poll
            return {"success": True, "message": "Nav2 active"}
        return self._fail("Nav2 did not start (see the Jetson terminal)")

    def load_map(self, name):
        if name not in self.maps:
            return self._fail(f"unknown map {name!r}")
        ok, v = self.link.call("/map_server/load_map", {"map_url": f"{self.maps_dir}/{name}.yaml"}, timeout=20)
        if not ok or v.get("result", 1) != 0:
            return self._fail(f"map_server could not load {name} (result {v.get('result', v.get('error'))})")
        self.map_name = name
        self.needs_initial_pose = True
        with self.lock:
            self.goal = None
            self.plan = []
        return {"success": True, "message": f"map {name} loaded: set the robot position"}

    def navigate(self, station):
        """Blocking version for the mission planner: drive to a saved station of the current map."""
        target = self.stations.get(self.map_name).get(station)
        if not target:
            return self._fail(f"no station {station!r} saved for map {self.map_name}")
        result = self.send_goal(target["x"], target["y"], target["theta"], station=station)
        if not result["success"]:
            return result
        end = time.monotonic() + 300
        time.sleep(0.5)
        while time.monotonic() < end:
            status = self.goal_status
            if self.stopped:
                return self._fail("robot is stopped")
            if status == "succeeded":
                return {"success": True, "message": f"arrived at {station}"}
            if status in ("aborted", "canceled"):
                return self._fail(f"navigation {status}")
            if not self.link.connected:
                return self._fail("rosbridge disconnected")
            time.sleep(0.2)
        return self._fail("navigation timed out")

    def pick(self, item, shelf, instruction=None):
        return self._fail("pick is not available over this link")

    def scan(self):
        return self._fail("scan is not available over this link")

    def place(self, target):
        return self._fail("place is not available over this link")

    def stop(self):
        """Cancel every Nav2 goal, then pause Nav2 (controller and recoveries deactivate).
        The base's 0.5 s cmd_vel watchdog then stops the wheels."""
        cancel_ok, _ = self.link.call("/navigate_to_pose/_action/cancel_goal",
                                      {"goal_info": {"goal_id": {"uuid": [0] * 16}, "stamp": {"sec": 0, "nanosec": 0}}},
                                      service_type="action_msgs/srv/CancelGoal", timeout=3)
        pause_ok, _ = self.lifecycle("navigation", "pause")
        if pause_ok:
            self.nav_active = False
        if cancel_ok or pause_ok:
            self.stopped = True
            with self.lock:
                if self.goal:
                    self.goal_status = "canceled"
            parts = [("goal canceled" if cancel_ok else "cancel failed"), ("Nav2 paused" if pause_ok else "pause failed")]
            return {"success": True, "message": ", ".join(parts)}
        return self._fail("Jetson did not confirm cancel or pause")

    def resume(self):
        ok, _ = self.lifecycle("navigation", "resume")
        if ok:
            self.stopped = False
            self.nav_active = True
            return {"success": True, "message": "Nav2 resumed (no goal re-sent)"}
        return self._fail("Nav2 resume failed")
