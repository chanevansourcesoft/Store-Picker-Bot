// Live map for the owner dashboard (like a small RViz).
// Talks to rosbridge with roslib.js. Python fills CFG (see live_map.py).
// Topics: /map, /tf (fallback /amcl_pose), /scan, /plan. Stations come from the robot API.

const CFG = __CONFIG__;

const canvas = document.getElementById("map");
const ctx = canvas.getContext("2d");
const badge = document.getElementById("badge");
const info = document.getElementById("info");
const overlay = document.getElementById("overlay");
const followBtn = document.getElementById("follow");

// view: pixels per meter, and the screen position (CSS px) of world point (0, 0)
const view = { scale: 60, ox: 0, oy: 0 };
let follow = false;
let mapFitted = false;  // fit the view once, when the first map arrives

const state = {
  map: null,        // {w, h, res, x0, y0, image, known: {minX, minY, maxX, maxY}}
  tf: {},           // child frame -> {parent, x, y, yaw, time}
  amcl: null,       // {x, y, yaw, time}
  scan: [],         // world points [x, y, x, y, ...]
  plan: [],         // world points [[x, y], ...]
  drawPose: null,   // smoothed pose for drawing
  lastData: 0,      // time of the last message
};

// ---------- small math helpers ----------

function now() { return performance.now(); }
function cleanFrame(name) { return (name || "").replace(/^\//, ""); }

function yawOf(q) {
  return Math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z));
}

// pose a, then pose b inside a
function compose(a, b) {
  const c = Math.cos(a.yaw), s = Math.sin(a.yaw);
  return { x: a.x + c * b.x - s * b.y, y: a.y + s * b.x + c * b.y, yaw: a.yaw + b.yaw };
}

function angleDiff(a, b) {
  return Math.atan2(Math.sin(a - b), Math.cos(a - b));
}

function worldToScreen(x, y) { return [view.ox + x * view.scale, view.oy - y * view.scale]; }
function screenToWorld(px, py) { return [(px - view.ox) / view.scale, (view.oy - py) / view.scale]; }

// ---------- robot pose ----------

// map -> odom (from AMCL) combined with odom -> base_footprint or base_link
function tfPose() {
  const mapOdom = state.tf[CFG.odom_frame];
  if (!mapOdom || mapOdom.parent !== CFG.map_frame) return null;
  for (const name of CFG.base_frames) {
    const base = state.tf[name];
    if (base && base.parent === CFG.odom_frame && now() - base.time < 2000) {
      return compose(mapOdom, base);
    }
  }
  return null;
}

// Returns {x, y, yaw, source} or null
function currentPose() {
  const p = tfPose();
  if (p) return Object.assign(p, { source: "tf" });
  if (state.amcl) return Object.assign({}, state.amcl, { source: "amcl_pose" });
  return null;
}

// ---------- message handlers ----------

function onMap(msg) {
  const w = msg.info.width, h = msg.info.height;
  let data = msg.data;
  if (typeof data === "string") {  // base64, just in case
    const bin = atob(data);
    data = new Int8Array(bin.length);
    for (let i = 0; i < bin.length; i++) data[i] = (bin.charCodeAt(i) << 24) >> 24;
  }
  const off = document.createElement("canvas");
  off.width = w;
  off.height = h;
  const offCtx = off.getContext("2d");
  const img = offCtx.createImageData(w, h);
  let minC = w, minR = h, maxC = -1, maxR = -1;
  for (let r = 0; r < h; r++) {
    const src = (h - 1 - r) * w;  // grid row 0 is the bottom (flip y)
    for (let c = 0; c < w; c++) {
      const v = data[src + c];
      let g;
      if (v < 0) g = 205;            // unknown: grey
      else if (v >= 65) g = 0;       // occupied: black
      else if (v <= 25) g = 255;     // free: white
      else g = 255 - Math.round(v * 2.55);
      const i = (r * w + c) * 4;
      img.data[i] = img.data[i + 1] = img.data[i + 2] = g;
      img.data[i + 3] = 255;
      if (v >= 0) {
        if (c < minC) minC = c;
        if (c > maxC) maxC = c;
        if (r < minR) minR = r;
        if (r > maxR) maxR = r;
      }
    }
  }
  offCtx.putImageData(img, 0, 0);
  const res = msg.info.resolution;
  const x0 = msg.info.origin.position.x, y0 = msg.info.origin.position.y;
  // known area in world meters (for "fit map")
  const known = maxC < 0
    ? { minX: x0, minY: y0, maxX: x0 + w * res, maxY: y0 + h * res }
    : { minX: x0 + minC * res, maxX: x0 + (maxC + 1) * res,
        minY: y0 + (h - 1 - maxR) * res, maxY: y0 + (h - minR) * res };
  state.map = { w, h, res, x0, y0, image: off, known };
  gotData();
  if (!mapFitted) {
    mapFitted = true;
    if (!follow) fitMap();
  }
}

function onTf(msg) {
  const t = now();
  for (const tr of msg.transforms || []) {
    const child = cleanFrame(tr.child_frame_id);
    const p = tr.transform.translation;
    state.tf[child] = {
      parent: cleanFrame(tr.header.frame_id),
      x: p.x, y: p.y, yaw: yawOf(tr.transform.rotation), time: t,
    };
  }
  gotData();
}

function onAmcl(msg) {
  const p = msg.pose.pose;
  state.amcl = { x: p.position.x, y: p.position.y, yaw: yawOf(p.orientation), time: now() };
  gotData();
}

function onScan(msg) {
  gotData();
  const pose = currentPose();
  if (!pose) { state.scan = []; return; }
  const laser = compose(pose, CFG.laser);  // laser offset from the robot base
  const pts = [];
  const ranges = msg.ranges || [];
  for (let i = 0; i < ranges.length; i++) {
    const r = ranges[i];  // inf / nan arrive as null
    if (r === null || !isFinite(r) || r < msg.range_min || r > msg.range_max) continue;
    const a = laser.yaw + msg.angle_min + i * msg.angle_increment;
    pts.push(laser.x + r * Math.cos(a), laser.y + r * Math.sin(a));
  }
  state.scan = pts;
}

function onPlan(msg) {
  let frame = null;
  // plan in the odom frame -> move it into the map frame
  if (cleanFrame(msg.header && msg.header.frame_id) === CFG.odom_frame) frame = state.tf[CFG.odom_frame];
  state.plan = (msg.poses || []).map((ps) => {
    const p = { x: ps.pose.position.x, y: ps.pose.position.y, yaw: 0 };
    const q = frame ? compose(frame, p) : p;
    return [q.x, q.y];
  });
  gotData();
}

function gotData() { state.lastData = now(); }

// ---------- connection ----------

let ros = null;
let retryMs = 1000;
let retryTimer = null;
let everConnected = false;
let connected = false;

function setBadge(kind, text) {
  badge.className = "badge " + kind;
  badge.textContent = text;
}

function showOverlay(text) {
  overlay.textContent = text || "";
  overlay.style.display = text ? "flex" : "none";
}

function scheduleReconnect() {
  if (retryTimer) return;
  retryTimer = setTimeout(() => { retryTimer = null; connect(); }, retryMs);
  retryMs = Math.min(retryMs * 2, 10000);
}

function subscribe(name, type, throttleMs, handler) {
  const topic = new ROSLIB.Topic({
    ros: ros, name: name, messageType: type, throttle_rate: throttleMs, queue_length: 1,
  });
  topic.subscribe(handler);
}

function connect() {
  if (!window.ROSLIB) {
    setBadge("lost", "roslib.js not loaded");
    showOverlay("Could not load roslib.js (no internet?). Put roslib.min.js in app/static/.");
    return;
  }
  setBadge("wait", everConnected ? "Reconnecting..." : "Connecting...");
  const myRos = new ROSLIB.Ros({ url: CFG.url });
  ros = myRos;
  myRos.on("connection", () => {
    if (ros !== myRos) return;
    connected = true;
    everConnected = true;
    retryMs = 1000;
    setBadge("ok", "Connected");
    showOverlay("");
    subscribe("/map", "nav_msgs/OccupancyGrid", 0, onMap);
    subscribe("/tf", "tf2_msgs/TFMessage", 0, onTf);
    subscribe("/amcl_pose", "geometry_msgs/PoseWithCovarianceStamped", 0, onAmcl);
    subscribe("/scan", "sensor_msgs/LaserScan", 100, onScan);  // 10 Hz max
    subscribe("/plan", "nav_msgs/Path", 200, onPlan);
  });
  myRos.on("error", () => {});  // "close" comes after this, we reconnect there
  myRos.on("close", () => {
    if (ros !== myRos) return;
    connected = false;
    setBadge("lost", everConnected ? "Connection lost" : "Not reachable");
    if (!everConnected) showOverlay("Cannot reach rosbridge at " + CFG.url + " - retrying...");
    scheduleReconnect();
  });
}

// ---------- view: fit, follow, pan, zoom ----------

function fitBox(minX, minY, maxX, maxY) {
  const w = canvas.clientWidth, h = canvas.clientHeight, pad = 20;
  const spanX = Math.max(maxX - minX, 0.5), spanY = Math.max(maxY - minY, 0.5);
  view.scale = Math.min((w - 2 * pad) / spanX, (h - 2 * pad) / spanY);
  view.ox = w / 2 - ((minX + maxX) / 2) * view.scale;
  view.oy = h / 2 + ((minY + maxY) / 2) * view.scale;
}

function fitMap() {
  let box;
  if (state.map) {
    box = Object.assign({}, state.map.known);
  } else {
    box = { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity };
  }
  // make sure stations and the robot are inside too
  const points = Object.values(CFG.stations).map((s) => [s.x, s.y]);
  if (state.drawPose) points.push([state.drawPose.x, state.drawPose.y]);
  for (const [x, y] of points) {
    box.minX = Math.min(box.minX, x - 0.3); box.maxX = Math.max(box.maxX, x + 0.3);
    box.minY = Math.min(box.minY, y - 0.3); box.maxY = Math.max(box.maxY, y + 0.3);
  }
  if (!isFinite(box.minX)) box = { minX: -2, minY: -2, maxX: 2, maxY: 2 };
  fitBox(box.minX, box.minY, box.maxX, box.maxY);
}

function setFollow(on) {
  follow = on;
  followBtn.classList.toggle("on", on);
}

document.getElementById("fit").onclick = () => { setFollow(false); fitMap(); };
followBtn.onclick = () => setFollow(!follow);

let drag = null;
canvas.addEventListener("mousedown", (e) => {
  drag = { x: e.clientX, y: e.clientY, ox: view.ox, oy: view.oy };
});
window.addEventListener("mousemove", (e) => {
  if (!drag) return;
  if (follow) setFollow(false);
  view.ox = drag.ox + (e.clientX - drag.x);
  view.oy = drag.oy + (e.clientY - drag.y);
});
window.addEventListener("mouseup", () => { drag = null; });
canvas.addEventListener("dblclick", () => { setFollow(false); fitMap(); });
canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const px = e.clientX - rect.left, py = e.clientY - rect.top;
  const [wx, wy] = screenToWorld(px, py);
  const factor = Math.exp(-e.deltaY * 0.0015);
  view.scale = Math.min(Math.max(view.scale * factor, 2), 2000);
  // keep the point under the mouse in the same place
  view.ox = px - wx * view.scale;
  view.oy = py + wy * view.scale;
}, { passive: false });

// ---------- drawing ----------

function resize() {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function drawGrid(w, h) {
  // 1 m grid lines (only when there is no map yet)
  const [x0, y1] = screenToWorld(0, 0), [x1, y0] = screenToWorld(w, h);
  ctx.strokeStyle = "#dde3df";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = Math.floor(x0); x <= x1; x++) {
    const [sx] = worldToScreen(x, 0);
    ctx.moveTo(sx, 0); ctx.lineTo(sx, h);
  }
  for (let y = Math.floor(y0); y <= y1; y++) {
    const [, sy] = worldToScreen(0, y);
    ctx.moveTo(0, sy); ctx.lineTo(w, sy);
  }
  ctx.stroke();
}

function drawMap() {
  const m = state.map;
  const [sx, sy] = worldToScreen(m.x0, m.y0 + m.h * m.res);  // top-left corner
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(m.image, sx, sy, m.w * m.res * view.scale, m.h * m.res * view.scale);
}

function drawPlan() {
  if (state.plan.length < 2) return;
  ctx.strokeStyle = "#16a34a";
  ctx.lineWidth = 3;
  ctx.lineJoin = "round";
  ctx.beginPath();
  state.plan.forEach(([x, y], i) => {
    const [sx, sy] = worldToScreen(x, y);
    if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
  });
  ctx.stroke();
}

function drawScan() {
  ctx.fillStyle = "#e11d48";
  const pts = state.scan;
  for (let i = 0; i < pts.length; i += 2) {
    const [sx, sy] = worldToScreen(pts[i], pts[i + 1]);
    ctx.fillRect(sx - 1.5, sy - 1.5, 3, 3);
  }
}

function drawStations() {
  ctx.font = "600 12px Inter, system-ui, sans-serif";
  ctx.textBaseline = "middle";
  for (const [name, s] of Object.entries(CFG.stations)) {
    const [sx, sy] = worldToScreen(s.x, s.y);
    ctx.fillStyle = name.startsWith("shelf") ? "#f59e0b" : name === "home" ? "#0d9488" : "#6366f1";
    ctx.beginPath();
    ctx.arc(sx, sy, 6, 0, Math.PI * 2);
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#ffffff";
    ctx.stroke();
    const label = name.replace("_", " ");
    ctx.lineWidth = 3;
    ctx.strokeText(label, sx + 9, sy);
    ctx.fillStyle = "#1f2937";
    ctx.fillText(label, sx + 9, sy);
  }
}

function drawRobot(p, fresh) {
  const [sx, sy] = worldToScreen(p.x, p.y);
  const size = Math.max(0.35 * view.scale, 16);  // about robot size, never tiny
  ctx.save();
  ctx.translate(sx, sy);
  ctx.rotate(-p.yaw);  // screen y goes down
  ctx.fillStyle = fresh ? "rgba(22,163,74,0.18)" : "rgba(100,116,139,0.18)";
  ctx.beginPath();
  ctx.arc(0, 0, size * 0.75, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = fresh ? "#16a34a" : "#64748b";
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(size * 0.6, 0);
  ctx.lineTo(-size * 0.4, size * 0.35);
  ctx.lineTo(-size * 0.2, 0);
  ctx.lineTo(-size * 0.4, -size * 0.35);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

function updateDrawPose(dt) {
  const target = currentPose();
  if (!target) return null;
  const d = state.drawPose;
  if (!d || Math.hypot(target.x - d.x, target.y - d.y) > 2) {
    state.drawPose = { x: target.x, y: target.y, yaw: target.yaw };
  } else {
    const k = 1 - Math.exp(-dt / 80);  // smooth over about 80 ms
    d.x += (target.x - d.x) * k;
    d.y += (target.y - d.y) * k;
    d.yaw += angleDiff(target.yaw, d.yaw) * k;
  }
  return target.source;
}

let lastFrame = now();
function frame() {
  const t = now();
  const dt = t - lastFrame;
  lastFrame = t;
  resize();
  const w = canvas.clientWidth, h = canvas.clientHeight;
  const source = updateDrawPose(dt);
  const p = state.drawPose;
  if (follow && p) {
    view.ox = w / 2 - p.x * view.scale;
    view.oy = h / 2 + p.y * view.scale;
  }

  ctx.fillStyle = "#eef1ef";
  ctx.fillRect(0, 0, w, h);
  if (state.map) drawMap(); else drawGrid(w, h);
  drawPlan();
  drawScan();
  drawStations();
  if (p) drawRobot(p, connected && source !== null);

  if (connected && t - state.lastData > 3000) setBadge("wait", "Connected, no data");
  else if (connected) setBadge("ok", "Connected");

  info.textContent = p
    ? `x ${p.x.toFixed(2)}  y ${p.y.toFixed(2)}  θ ${(p.yaw * 180 / Math.PI).toFixed(0)}°  · ${source || "last pose"}`
    : "no robot pose yet";
  requestAnimationFrame(frame);
}

fitMap();
connect();
requestAnimationFrame(frame);
