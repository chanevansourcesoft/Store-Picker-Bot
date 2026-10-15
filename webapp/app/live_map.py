"""Live ROS map for the owner dashboard (like a small RViz).

The browser talks to rosbridge on the Jetson directly (websocket + roslib.js).
This file only builds the HTML for st.iframe. The app does NOT need ROS 2.

Settings (in config.py): ROSBRIDGE_URL, LASER_OFFSET.
If app/static/roslib.min.js exists, we put it inside the page (works without internet).
Otherwise the page loads roslib.js from a CDN.
"""

import html
import json
import socket
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
JS_FILE = HERE / "live_map.js"
LOCAL_ROSLIB = HERE / "static" / "roslib.min.js"
ROSLIB_CDN = "https://cdn.jsdelivr.net/npm/roslib@1.4.1/build/roslib.min.js"

MAP_FRAME = "map"
ODOM_FRAME = "odom"
BASE_FRAMES = ["base_footprint", "base_link"]  # first one found under odom wins


def parse_offset(text):
    """'0.1,0,3.14' -> {"x": 0.1, "y": 0.0, "yaw": 3.14}. Bad text -> all zero."""
    try:
        parts = [float(v) for v in str(text).split(",")]
    except ValueError:
        parts = []
    if len(parts) != 3:
        parts = [0.0, 0.0, 0.0]
    return {"x": parts[0], "y": parts[1], "yaw": parts[2]}


def host_port(url):
    """'ws://192.168.149.1:9090' -> ('192.168.149.1', 9090). Bad url -> (None, None)."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("ws", "wss") or not parsed.hostname:
            return None, None
        return parsed.hostname, parsed.port or (443 if parsed.scheme == "wss" else 80)
    except ValueError:
        return None, None


def rosbridge_reachable(url, timeout=1.0):
    """True if we can open a TCP connection to the rosbridge port (quick check, no ROS)."""
    host, port = host_port(url)
    if host is None:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def roslib_tag():
    """<script> with roslib.js: local copy if we have one, else the CDN."""
    if LOCAL_ROSLIB.exists():
        return "<script>" + LOCAL_ROSLIB.read_text() + "</script>"
    return f'<script src="{ROSLIB_CDN}"></script>'


def _safe_json(data):
    """JSON that is safe inside a <script> tag."""
    return json.dumps(data).replace("</", "<\\/")


def live_map_html(url, stations, laser_offset):
    """Full HTML page for the live map component (fills the iframe height)."""
    config = {
        "url": url,
        "stations": {name: {"x": float(p.get("x", 0)), "y": float(p.get("y", 0))}
                     for name, p in (stations or {}).items()},
        "laser": dict(parse_offset(laser_offset) if isinstance(laser_offset, str) else laser_offset),
        "map_frame": MAP_FRAME,
        "odom_frame": ODOM_FRAME,
        "base_frames": BASE_FRAMES,
    }
    script = JS_FILE.read_text().replace("__CONFIG__", _safe_json(config))
    return PAGE.format(roslib=roslib_tag(), url=html.escape(url), script=script)


PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: Inter, system-ui, sans-serif; background: #ffffff; }}
  .wrap {{ position: relative; height: 100vh; min-height: 200px; border-radius: 12px; overflow: hidden;
           border: 1px solid #e3e8e5; background: #eef1ef; }}
  canvas {{ width: 100%; height: 100%; display: block; cursor: grab; }}
  canvas:active {{ cursor: grabbing; }}
  .bar {{ position: absolute; top: 8px; left: 8px; right: 8px; display: flex; gap: 6px;
          align-items: center; pointer-events: none; }}
  .bar > * {{ pointer-events: auto; }}
  .badge {{ font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 999px;
            background: #e5e7eb; color: #374151; }}
  .badge::before {{ content: "●"; margin-right: 5px; }}
  .badge.ok {{ background: #dcfce7; color: #166534; }}
  .badge.wait {{ background: #fef3c7; color: #92400e; }}
  .badge.lost {{ background: #fee2e2; color: #991b1b; }}
  .spacer {{ flex: 1; }}
  button {{ font: inherit; font-size: 12px; font-weight: 600; border: 1px solid #d1d5db;
            background: #ffffffee; border-radius: 8px; padding: 4px 10px; cursor: pointer; }}
  button:hover {{ background: #f3f4f6; }}
  button.on {{ background: #16a34a; color: #ffffff; border-color: #16a34a; }}
  .info {{ position: absolute; left: 8px; bottom: 8px; font-size: 11px; color: #374151;
           background: #ffffffcc; padding: 3px 8px; border-radius: 6px;
           font-variant-numeric: tabular-nums; }}
  .url {{ position: absolute; right: 8px; bottom: 8px; font-size: 11px; color: #6b7280;
          background: #ffffffcc; padding: 3px 8px; border-radius: 6px; }}
  .overlay {{ position: absolute; inset: 0; display: none; align-items: center;
              justify-content: center; text-align: center; padding: 24px; font-size: 13px;
              color: #991b1b; background: #ffffff99; pointer-events: none; }}
</style>
{roslib}
</head><body>
<div class="wrap">
  <canvas id="map"></canvas>
  <div class="overlay" id="overlay"></div>
  <div class="bar">
    <span class="badge wait" id="badge">Connecting...</span>
    <span class="spacer"></span>
    <button id="fit" title="Show the whole map (or double click)">Fit map</button>
    <button id="follow" title="Keep the robot in the middle">Follow robot</button>
  </div>
  <div class="info" id="info">no robot pose yet</div>
  <div class="url">{url}</div>
</div>
<script>
{script}
</script>
</body></html>
"""
