"""
QuickPick Store Control - Owner Dashboard (laptop browser).

Control-room look:
- Needs-attention strip: robot, stock and order problems, most urgent first
- KPI cards: orders, done, queue, revenue, pack time, pick success
- Live ROS map (rosbridge, like RViz) or the 2D store map, and a robot card with STOP
- Live step log: robot actions and Nemotron decisions side by side
- Order inspector: follow the live order or open any order of today
- Live camera, orders per hour, orders table (filter, cancel, CSV), stock cards with Restock
- AI summary using Nemotron Ultra (rule-based summary without an API key)

The live parts are st.fragment(run_every=2): they refresh every 2 seconds
without reloading the whole page (so the camera stream keeps playing).
The live ROS map is NOT inside a fragment: it keeps its own websocket open.
"""

import sys
from pathlib import Path

# Streamlit runs this file as a script: put storepilot/ on the path so "import quickpick" works
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from quickpick import live_map, metrics, store
from quickpick.settings import LASER_OFFSET, ROSBRIDGE_URL
from store_agent import robot_client
from store_agent.agent import group_items_by_shelf, plan_shelf_route, strip_think
from store_agent.config import MODEL_ULTRA, ROBOT_URL, get_client, has_llm
from store_agent.inventory import load_inventory
from quickpick.step_log import is_recovered, step_target
from quickpick.worker import init_worker
from quickpick.ui_style import (
    BASE_CSS,
    OWNER_CSS,
    PRODUCT_INFO,
    BRAND_GREEN,
    BRAND_GREEN_LIGHT,
    BRAND_YELLOW_LIGHT,
    BRAND_DARK,
    LOW_STOCK,
    STATUS_ERROR,
    STATUS_WARNING,
    STATUS_SUCCESS,
    TOOL_ICONS,
    esc,
    html,
    inject_css,
    product_name,
    product_emoji,
    station_name,
    format_time,
    status_pill,
    order_status_pill,
    ORDER_STATUS_PILLS,
    owner_header_html,
    kpi_card_html,
    store_map_svg,
    timeline_html,
    empty_html,
    attention_html,
    progress_html,
    hourly_chart_svg,
)

# Page config
st.set_page_config(
    page_title="QuickPick - Store Control",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# Ensure worker is running
@st.cache_resource
def start_background_worker():
    init_worker()
    return True


start_background_worker()

# Initialize database
store.init_db()

# Default station positions (if /map fails)
DEFAULT_STATIONS = {
    "home": {"x": 0.0, "y": 0.0},
    "shelf_A": {"x": 2.0, "y": 1.0},
    "shelf_B": {"x": 2.0, "y": 2.0},
    "shelf_C": {"x": 2.0, "y": 3.0},
    "customer": {"x": 0.0, "y": 3.0},
}

REFRESH_SEC = 2
LIVE_MAP_HEIGHT = 430


# ============ Data (logic unchanged) ============

def get_robot_status():
    """Get robot status from the robot API."""
    offline = {
        "online": False, "busy": False, "holding": None, "station": "unknown",
        "pose": {"x": 0, "y": 0, "theta": 0}, "battery": 0, "speed": 0,
        "nav_state": "offline", "obstacle": False, "last_error": "Robot offline", "stopped": False,
    }
    try:
        health = robot_client.health()
        if not robot_client.is_healthy(health):
            return offline
        status = robot_client.status()
        return {
            "online": True,
            "busy": status.get("busy", False),
            "holding": status.get("holding"),
            "station": status.get("station", "home"),
            "pose": status.get("pose", {"x": 0, "y": 0, "theta": 0}),
            "battery": status.get("battery", 100),
            "speed": status.get("speed", 0),
            "nav_state": status.get("nav_state", "idle"),
            "obstacle": status.get("obstacle", False),
            "last_error": status.get("last_error"),
            # Older robot servers have no "stopped" field: fall back to the error text
            "stopped": bool(status.get("stopped")) or "stopped" in str(status.get("last_error") or "").lower(),
        }
    except Exception as e:
        return dict(offline, last_error=str(e))


def get_station_map():
    """Get station positions from robot or use defaults."""
    try:
        result = robot_client.get_map()
        if result.get("stations"):
            return result["stations"]
    except Exception:
        pass
    return DEFAULT_STATIONS


def format_items(items):
    """Format items list as readable string, e.g. '2× 🥫 Tomato Soup'."""
    if not items:
        return "-"
    parts = []
    for item in items:
        if isinstance(item, dict):
            name = item.get("item", "?")
            parts.append(f"{item.get('qty', 1)}× {product_emoji(name)} {product_name(name)}")
        else:
            parts.append(str(item))
    return ", ".join(parts)


def offline_summary():
    """Short rule-based summary for when Nemotron is not available (no API key)."""
    stats = store.get_stats_today()
    orders = store.get_orders_today()
    stock = store.get_stock()
    ok, tries = metrics.pick_stats(orders)
    lines = [f"**Today:** {stats['total']} orders, {stats['done']} done cleanly, "
             f"{stats['done_with_problems']} with problems, {stats['failed']} failed. Revenue Rs {stats['revenue']}."]
    if tries:
        lines.append(f"**Robot:** {ok} of {tries} GR00T picks worked ({round(100 * ok / tries)}%), "
                     f"average pack time {metrics.format_duration(metrics.avg_pack_seconds(orders))}.")
    problems = sorted({p for o in orders for p in (o.get("problems") or [])})
    lines.append("**Problems:** " + ("; ".join(problems[:5]) if problems else "none."))
    low = [f"{product_name(n)} ({q})" for n, q in stock.items() if q <= LOW_STOCK]
    lines.append("**Restock:** " + (", ".join(low) if low else "nothing urgent."))
    lines.append("_Rule-based summary: add NEBIUS_API_KEY for a Nemotron Ultra summary._")
    return "\n\n".join(lines)


def generate_ai_summary():
    """Generate AI summary using Nemotron Ultra (rule-based without an API key)."""
    if not has_llm():
        return offline_summary()
    try:
        client = get_client()
        stats = store.get_stats_today()
        orders = store.get_orders_today()
        stock = store.get_stock()

        problems_list = []
        for order in orders:
            if order.get("problems"):
                problems_list.extend(order["problems"])

        low_stock = [name for name, qty in stock.items() if qty <= 3]

        prompt = f"""You are a store operations assistant. Write a brief summary (3-5 sentences) for the store owner.

Today's data:
- Total orders: {stats['total']}
- Completed: {stats['done']}
- In queue: {stats['queued']}
- Currently packing: {stats['packing']}
- Revenue: Rs {stats['revenue']}

Problems today: {problems_list if problems_list else 'None'}

Stock levels:
{', '.join(f'{name}: {qty}' for name, qty in stock.items())}

Low stock items (3 or less): {low_stock if low_stock else 'None'}

Write a simple summary with:
1. What went well today
2. Any problems or issues
3. What needs restocking

Keep it short and use simple English."""

        response = client.chat.completions.create(
            model=MODEL_ULTRA,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        return f"Could not generate summary: {str(e)}"


def get_location_text(station, nav_state):
    """Get human-readable location text."""
    if nav_state == "offline":
        return "Unknown"
    if nav_state == "navigating":
        return f"Moving to {station_name(station)}"
    if station == "home":
        return "At home"
    if station == "customer":
        return "At customer"
    return f"At {station_name(station)}"


def pretty_task(text):
    """'Picked chips_packet' -> 'Picked Chips Packet', 'At shelf_A' -> 'At Shelf A'."""
    for item in PRODUCT_INFO:
        text = text.replace(item, product_name(item))
    for station in ("shelf_A", "shelf_B", "shelf_C"):
        text = text.replace(station, station_name(station))
    return text


def get_robot_state(robot, current_order):
    """idle / busy / offline / stopped. Busy while an order is packing."""
    if not robot["online"]:
        return "offline"
    if robot.get("stopped"):
        return "stopped"
    if robot["busy"] or current_order:
        return "busy"
    return "idle"


LIVE_ORDER = "live"  # step log choice: follow the order the robot is packing


def get_log_order(choice=LIVE_ORDER):
    """Order to show in the step log: the chosen one, else the one packing now,
    else the last one with steps."""
    if choice != LIVE_ORDER:
        order = store.get_order(choice)
        if order:
            return order
    current = store.get_current_packing_order()
    if current:
        return current
    for order in store.get_orders_today():
        if order.get("step_log"):
            return order
    return None


def order_route(order, inventory):
    """Shelves the order needs, in visit order (same plan as the agent)."""
    if not order:
        return []
    return [shelf for shelf, _ in plan_shelf_route(group_items_by_shelf(order.get("items", []), inventory))]


# ============ Step log entries ============

def robot_action_entry(entry):
    """One robot action (what the robot did and the result)."""
    tool = entry.get("tool", "?")
    data = entry.get("input", {}) or {}
    result = entry.get("result", {}) or {}
    ok = bool(result.get("success"))
    icon, tint = TOOL_ICONS.get(tool, ("⚙️", "#EEF1EF"))

    if tool == "navigate_to":
        title = f"Drive to {station_name(data.get('station'))}"
    elif tool == "pick_item":
        title = f"Pick {product_name(data.get('item', '?'))}"
    elif tool == "scan_item":
        product = result.get("product")
        title = f"Scan {product_name(product)}" if product else "Scan barcode"
    elif tool == "place_item":
        title = "Place in basket" if data.get("target") == "basket" else "Return to shelf"
    else:
        title = tool

    sub = result.get("message") or (f"Barcode {result['barcode']}" if result.get("barcode") else "")
    if not ok and tint != "#EEF1EF":
        tint = "#FDE7E8"
    return {"icon": icon, "tint": tint, "title": title, "sub": sub, "ok": ok, "time": format_time(entry.get("time"))}


def robot_action_entries(log):
    """All robot actions. A failure that a later retry fixed is shown as 'Recovered' (yellow)."""
    entries = []
    for index, step in enumerate(log):
        if step.get("result", {}).get("refused"):
            continue  # never reached the robot (shown in the Nemotron lane)
        entry = robot_action_entry(step)
        if not step.get("result", {}).get("success") and is_recovered(log, index):
            target = step_target(log, index) or "?"
            name = station_name(target) if step.get("tool") == "navigate_to" else product_name(target)
            problem = step.get("result", {}).get("message") or "failed"
            entry.update(
                title=f"Recovered: {problem} on {name}, retry succeeded",
                sub=entry["title"],
                tint=BRAND_YELLOW_LIGHT,
                ok="recovered",
            )
        entries.append(entry)
    return entries


def nemotron_entries(order, inventory):
    """What Nemotron decided: the plan, each tool call, GR00T instructions, retries, the bill."""
    brain = {"icon": "🧠", "tint": "#E9EFEB"}
    log = order.get("step_log") or []
    route = order_route(order, inventory)
    entries = [
        dict(brain, title="Read the order", sub=format_items(order.get("items", [])),
             time=format_time(order.get("created_at"))),
        dict(brain, title="Planned the route",
             sub=" → ".join(["Home"] + [station_name(s) for s in route] + ["Customer", "Home"])),
    ]

    prev = None
    for entry in log:
        tool = entry.get("tool", "?")
        data = entry.get("input", {}) or {}
        args = ", ".join(f"{k}={v}" for k, v in data.items() if k != "instruction")
        retry = prev is not None and prev.get("tool") == tool and not prev.get("result", {}).get("success")
        item = {
            **brain,
            "title": ("Retry " if retry else "Call ") + tool,
            "sub": args,
            "time": format_time(entry.get("time")),
        }
        if entry.get("result", {}).get("refused"):
            item.update(icon="🛡️", tint="#FFEBD6", title=f"Blocked by code: {tool}",
                        sub=entry["result"].get("message", ""), ok=False)
        elif data.get("instruction"):
            item["title"] = "Instruction for GR00T"
            item["quote"] = data["instruction"]
            item["tint"] = BRAND_YELLOW_LIGHT
        if retry:
            item["tint"] = "#FFF0E0"
        entries.append(item)
        prev = entry

    bill = order.get("bill") or {}
    if order["status"] in ("done", "done_with_problems") and isinstance(bill, dict):
        entries.append(dict(brain, title="Wrote the bill", sub=f"Total Rs {bill.get('total', 0)}", ok=True))
    if order["status"] in ("done_with_problems", "failed"):
        entries.append(dict(brain, title="Reported a problem", sub=" · ".join(order.get("problems") or []), ok=False))
    return entries


# ============ Page pieces ============

inject_css(BASE_CSS, OWNER_CSS)

# Orders table filter: label -> statuses (None = all)
ORDER_FILTERS = {
    "All": None,
    "Active": ("queued", "packing"),
    "Done": ("done",),
    "Problems": ("done_with_problems", "failed"),
    "Cancelled": ("cancelled",),
}


def decision_lane_title():
    """Who plans the robot's steps: Nemotron, or the offline autopilot without an API key."""
    if has_llm():
        return f'<span style="color:{BRAND_DARK};">🧠</span> NVIDIA Nemotron decisions'
    return '🧭 Autopilot decisions <span class="qp-pill qp-pill-grey">offline · no API key</span>'


@st.fragment(run_every=REFRESH_SEC)
def live_header():
    """Header, needs-attention strip and KPI cards."""
    stats = store.get_stats_today()
    orders = store.get_orders_today()
    robot = get_robot_status()
    html(owner_header_html(robot["online"], demo=robot_client.is_demo_mode()))

    stock = {product_name(name): qty for name, qty in store.get_stock().items()}
    html(attention_html(metrics.attention_items(robot, stats, stock, LOW_STOCK, has_llm())))

    finished = stats["done"] + stats["done_with_problems"]
    done_pct = round(100 * stats["done"] / stats["total"]) if stats["total"] else 0
    avg_basket = round(stats["revenue"] / finished) if finished else 0
    picks_ok, picks = metrics.pick_stats(orders)
    pick_pct = f"{round(100 * picks_ok / picks)}%" if picks else "-"
    kpis = [
        ("📦", stats["total"], "Orders today", f"{stats['cancelled']} cancelled" if stats["cancelled"] else "since midnight", "#E4EEFE"),
        ("✅", stats["done"], "Done", f"{done_pct}% · +{stats['done_with_problems']} with problems", BRAND_GREEN_LIGHT),
        ("⏳", stats["queued"], "In queue", f"+{stats['packing']} packing now", BRAND_YELLOW_LIGHT),
        ("💰", f"Rs {stats['revenue']}", "Revenue", f"avg basket Rs {avg_basket}" if finished else "from done orders", "#EEE8FD"),
        ("⏱", metrics.format_duration(metrics.avg_pack_seconds(orders)), "Avg pack time", "first robot step → last", "#E6F4F8"),
        ("🦾", pick_pct, "Pick success", f"{picks_ok} of {picks} GR00T picks" if picks else "no picks yet", "#FDEDE4"),
    ]
    # One CSS grid (not st.columns): 6 across on wide screens, 3 or 2 on smaller ones
    html('<div class="qp-kpis">' + "".join(kpi_card_html(*kpi) for kpi in kpis) + "</div>")


@st.cache_data(ttl=10, show_spinner=False)
def rosbridge_reachable(url):
    """Quick TCP check from this server (cached 10 s, so pages stay fast)."""
    return live_map.rosbridge_reachable(url)


@st.cache_data(ttl=60, show_spinner=False)
def live_map_page(url, laser_offset):
    """HTML for the live map. Same text every time -> the iframe is not reloaded."""
    return live_map.live_map_html(url, get_station_map(), laser_offset)


def map_card():
    """Live ROS map if rosbridge answers, else the 2D store map (fallback)."""
    with st.container(key="card_map"):
        reachable = rosbridge_reachable(ROSBRIDGE_URL)
        c1, c2 = st.columns([3, 1.3], vertical_alignment="center")
        with c1:
            html('<div class="qp-card-title">🗺️ Store map</div>')
        with c2:
            use_live = st.toggle("Live ROS map", value=True, key="live_map_on", disabled=not reachable,
                                 help=f"rosbridge at {ROSBRIDGE_URL}")
        if reachable and use_live:
            st.iframe(live_map_page(ROSBRIDGE_URL, LASER_OFFSET), height=LIVE_MAP_HEIGHT)
            html('<div class="qp-legend"><span><i style="background:#16a34a;"></i>Robot / plan</span>'
                 '<span><i style="background:#e11d48;"></i>Laser scan</span>'
                 '<span><i style="background:#f59e0b;border-radius:50%;"></i>Station</span>'
                 '<span class="qp-card-sub">Drag to move · wheel to zoom</span></div>')
            return
        if not reachable:
            html(f'<div class="qp-card-sub">Live map off: rosbridge at {esc(ROSBRIDGE_URL)} '
                 'is not reachable. Showing the static map (checking again every 10 s).</div>')
            watch_rosbridge()
        live_static_map()


@st.fragment(run_every=10)
def watch_rosbridge():
    """When rosbridge comes back, reload the page so the live map shows."""
    if live_map.rosbridge_reachable(ROSBRIDGE_URL):
        rosbridge_reachable.clear()
        st.rerun()


@st.fragment(run_every=REFRESH_SEC)
def live_static_map():
    """2D store map from the robot API (no ROS needed)."""
    robot = get_robot_status()
    stations = get_station_map()
    current_order = store.get_current_packing_order()
    inventory = load_inventory()
    robot_state = get_robot_state(robot, current_order)
    shelf_icons = {}
    for name, info in inventory.items():
        shelf_icons.setdefault(info.get("shelf"), []).append(product_emoji(name))
    route = order_route(current_order, inventory)
    route_note = (" → ".join(station_name(s) for s in route)) if route else "no active route"
    html(f"""
    <div class="qp-card-sub" style="margin-bottom:.6rem;">Route: {esc(route_note)}</div>
    <div class="qp-map">{store_map_svg(stations, robot["pose"], robot_state, route, shelf_icons)}</div>
    <div class="qp-legend">
        <span><i style="background:{BRAND_GREEN};border-radius:50%;"></i>Robot</span>
        <span><i style="background:#FFF8E6;border:2px solid #FFB627;"></i>Shelf in current order</span>
        <span><i style="background:{BRAND_GREEN_LIGHT};border:2px dashed {BRAND_GREEN};"></i>Home</span>
        <span><i style="background:#FFF4DA;border:2px solid #FFB627;"></i>Customer</span>
    </div>
    """)


@st.fragment(run_every=REFRESH_SEC)
def live_robot_card():
    """Robot state, battery, task, alerts, STOP (always) and Resume (only when stopped)."""
    robot = get_robot_status()
    current_order = store.get_current_packing_order()
    robot_state = get_robot_state(robot, current_order)
    with st.container(key="card_robot"):
        pills = {
            "idle": status_pill("Idle", "green", dot=True),
            "busy": status_pill("Busy", "yellow", dot=True),
            "offline": status_pill("Offline", "grey", dot=True),
            "stopped": status_pill("Stopped", "red", dot=True),
        }
        top_left, top_right = st.columns([1.5, 1], vertical_alignment="center")
        with top_left:
            html(f"""
            <div class="qp-card-title">🤖 Robot</div>
            <div style="margin-top:.35rem;">{pills[robot_state]}</div>
            """)
        with top_right:
            if st.button("■ STOP", key="stop_btn", use_container_width=True, help="Emergency stop",
                         disabled=robot_state in ("offline", "stopped")):
                result = robot_client.stop()
                if result.get("success"):
                    st.toast("Robot stopped", icon="🛑")
                else:
                    st.toast(result.get("message", "Error"), icon="⚠️")
                st.rerun(scope="fragment")

        battery = robot.get("battery", 0) or 0
        batt_color = STATUS_ERROR if battery < 20 else STATUS_WARNING if battery < 50 else STATUS_SUCCESS
        holding = robot.get("holding")
        holding_text = f"{product_emoji(holding)} {product_name(holding)}" if holding else "Nothing"
        task = pretty_task((current_order or {}).get("current_task") or "Waiting for orders")
        html(f"""
        <div class="qp-batt">
            <span>🔋</span>
            <div class="qp-batt-bar"><div class="qp-batt-fill" style="width:{battery:.0f}%;background:{batt_color};"></div></div>
            <span class="qp-batt-num">{battery:.0f}%</span>
        </div>
        <div class="qp-stats">
            <div class="qp-stat"><div class="qp-stat-label">Location</div>
                <div class="qp-stat-value">📍 {esc(get_location_text(robot.get("station", "home"), robot.get("nav_state", "idle")))}</div></div>
            <div class="qp-stat"><div class="qp-stat-label">Speed</div>
                <div class="qp-stat-value">{robot.get("speed", 0) or 0:.1f} m/s</div></div>
            <div class="qp-stat"><div class="qp-stat-label">Holding</div>
                <div class="qp-stat-value">{esc(holding_text)}</div></div>
            <div class="qp-stat"><div class="qp-stat-label">Task</div>
                <div class="qp-stat-value">{esc(task)}</div></div>
        </div>
        """)

        alerts = []
        if robot.get("obstacle"):
            alerts.append(("⚠️", "Obstacle detected"))
        if robot["online"] and battery < 20:
            alerts.append(("🔋", "Low battery"))
        if robot.get("last_error"):
            alerts.append(("❌", robot["last_error"]))
        for icon, msg in alerts:
            html(f'<div class="qp-alert">{icon} {esc(msg)}</div>')

        if robot_state == "stopped":
            st.write("")
            if st.button("▶ Resume after stop", key="resume_btn", use_container_width=True, type="primary"):
                result = robot_client.resume()
                if result.get("success"):
                    st.toast("Robot resumed", icon="▶️")
                else:
                    st.toast(result.get("message", "Error"), icon="⚠️")
                st.rerun(scope="fragment")


def order_choice_label(choice):
    if choice == LIVE_ORDER:
        return "● Follow live order"
    order = store.get_order(choice)
    if not order:
        return f"#{choice}"
    label = ORDER_STATUS_PILLS.get(order["status"], (order["status"],))[0]
    return f"#{choice} · {format_time(order.get('created_at'))} · {label}"


@st.fragment(run_every=REFRESH_SEC)
def live_step_log():
    """Robot actions and planner decisions for the live order, or any order picked from the list."""
    inventory = load_inventory()
    order_ids = [o["id"] for o in store.get_orders_today()]

    with st.container(key="card_log"):
        c1, c2 = st.columns([2.2, 1.3], vertical_alignment="center")
        with c2:
            choice = st.selectbox("Order", [LIVE_ORDER] + order_ids, key="log_order",
                                  format_func=order_choice_label, label_visibility="collapsed")
        order = get_log_order(choice)
        with c1:
            if order:
                html(f"""
                <div class="qp-card-title">
                    <span>📋 Step log · <span style="color:{BRAND_GREEN};">#{order['id']}</span></span>
                    <span>{order_status_pill(order['status'])}</span>
                </div>
                """)
            else:
                html('<div class="qp-card-title">📋 Step log</div>')
        if not order:
            html(empty_html("📭", "No orders yet. Place one on the customer kiosk."))
            return

        if choice != LIVE_ORDER:
            label = "Order"
        else:
            label = "Now packing" if order["status"] == "packing" else "Last order"
        done, total = metrics.order_progress(order)
        wait, pack = metrics.order_times(order)
        elapsed = ""
        if pack is not None:
            elapsed = f"packing {metrics.format_duration(pack)} · waited {metrics.format_duration(wait)}"
        html(f"""
        <div class="qp-card-sub" style="margin-top:.2rem;">{label}: {esc(format_items(order.get('items', [])))}</div>
        {progress_html(done, total, elapsed)}
        """)

        lane_robot, lane_brain = st.columns(2, gap="medium")
        log = order.get("step_log") or []
        with lane_robot:
            html('<div class="qp-lane-title">🤖 Robot actions</div>')
            with st.container(height=360, border=False, autoscroll=True):
                if log:
                    html(timeline_html(robot_action_entries(log)))
                elif order["status"] == "cancelled":
                    html(empty_html("🚫", "Cancelled before the robot started."))
                else:
                    html(empty_html("⏳", "Waiting for the first robot action..."))
        with lane_brain:
            html(f'<div class="qp-lane-title">{decision_lane_title()}</div>')
            with st.container(height=360, border=False, autoscroll=True):
                html(timeline_html(nemotron_entries(order, inventory)))


def camera_card():
    """Live camera (MJPEG). Not inside a fragment, so the stream is not reloaded."""
    with st.container(key="card_cam"):
        if robot_client.is_demo_mode():
            html("""
            <div class="qp-card-title" style="justify-content:space-between;margin-bottom:.6rem;">
                <span>📷 Live camera</span><span class="qp-demo-badge">🧪 Simulated robot</span>
            </div>
            <div class="qp-cam" style="aspect-ratio:16/7;"><span>No camera in demo mode</span></div>
            """)
            return
        html(f"""
        <div class="qp-card-title" style="justify-content:space-between;margin-bottom:.6rem;">
            <span>📷 Live camera</span><span class="qp-card-sub">{esc(ROBOT_URL)}</span>
        </div>
        <div class="qp-cam">
            <span class="qp-cam-tag">● LIVE</span>
            <span>Camera not connected</span>
            <img src="{esc(ROBOT_URL)}/camera" alt="" />
        </div>
        """)


@st.fragment(run_every=10)
def live_hourly_chart():
    """Orders per hour today (bar chart)."""
    hours = metrics.orders_by_hour(store.get_orders_today())
    peak_hour, peak = max(hours, key=lambda h: h[1]) if hours else (0, 0)
    sub = f"busiest {peak_hour:02d}:00 · {peak} order{'s' if peak != 1 else ''}" if peak else "no orders yet today"
    with st.container(key="card_hourly"):
        html(f"""
        <div class="qp-card-title" style="justify-content:space-between;margin-bottom:.5rem;">
            <span>📈 Orders per hour</span><span class="qp-card-sub">{esc(sub)}</span>
        </div>
        <div class="qp-chart">{hourly_chart_svg(hours)}</div>
        """)


@st.fragment(run_every=REFRESH_SEC)
def live_orders():
    """Orders table: status filter, wait / pack times, cancel a queued order, CSV export."""
    orders = store.get_orders_today()
    with st.container(key="card_orders"):
        c1, c2, c3 = st.columns([1.3, 2.6, 0.9], vertical_alignment="center")
        with c1:
            html('<div class="qp-card-title">🧾 Orders today</div>')
        with c2:
            choice = st.segmented_control("Filter", list(ORDER_FILTERS), default="All", key="orders_filter",
                                          label_visibility="collapsed") or "All"
        with c3, st.container(key="csv_btn"):
            st.download_button("⬇ CSV", metrics.orders_csv(orders), file_name="quickpick_orders_today.csv",
                               mime="text/csv", use_container_width=True, on_click="ignore",
                               disabled=not orders, help="Download today's orders")

        statuses = ORDER_FILTERS[choice]
        shown = [o for o in orders if statuses is None or o["status"] in statuses]
        if not shown:
            html(empty_html("📭", "No orders today" if not orders else f"No {choice.lower()} orders"))
        else:
            rows = ""
            for order in shown:
                bill = order.get("bill") or {}
                total = bill.get("total", 0) if isinstance(bill, dict) else 0
                wait, pack = metrics.order_times(order)
                problems = order.get("problems") or []
                problem_line = (f'<div class="qp-card-sub" style="color:#A34E00;">{esc(" · ".join(problems))}</div>'
                                if problems and order["status"] != "done" else "")
                rows += f"""
                <tr>
                    <td class="id">#{order['id']}</td>
                    <td class="muted">{format_time(order.get('created_at'))}</td>
                    <td>{esc(format_items(order.get('items', [])))}{problem_line}</td>
                    <td>{order_status_pill(order['status'])}</td>
                    <td class="muted">{metrics.format_duration(pack)}</td>
                    <td class="num">{'Rs ' + str(total) if total else '-'}</td>
                </tr>
                """
            with st.container(height=min(60 + 46 * len(shown), 420), border=False):
                html(f"""
                <table class="qp-table">
                    <thead><tr><th>Order</th><th>Time</th><th>Items</th><th>Status</th><th>Pack</th>
                    <th style="text-align:right;">Total</th></tr></thead>
                    <tbody>{rows}</tbody>
                </table>
                """)

        queued = [o["id"] for o in orders if o["status"] == "queued"]
        if queued:
            q1, q2 = st.columns([2.6, 1], vertical_alignment="center")
            with q1:
                to_cancel = st.selectbox("Cancel order", queued, key="cancel_pick", label_visibility="collapsed",
                                         format_func=lambda i: f"Queued #{i}: {format_items(store.get_order(i)['items'])}")
            with q2, st.container(key="cancel_btn"):
                if st.button("Cancel order", use_container_width=True, help="Only orders the robot has not started"):
                    if store.cancel_order(to_cancel):
                        st.toast(f"Order #{to_cancel} cancelled", icon="🚫")
                    else:
                        st.toast(f"Order #{to_cancel} already started, cannot cancel", icon="⚠️")
                    st.rerun(scope="fragment")


@st.fragment(run_every=REFRESH_SEC)
def live_stock():
    """Stock cards with low-stock badge and Restock buttons."""
    inventory = load_inventory()
    stock = store.get_stock()
    low_items = [name for name in inventory if stock.get(name, 0) <= LOW_STOCK]
    total_units = sum(stock.get(name, 0) for name in inventory)
    stock_value = sum(stock.get(name, 0) * info.get("price", 0) for name, info in inventory.items())
    t1, t2 = st.columns([1.6, 1], vertical_alignment="center")
    with t1:
        html(f"""
        <div class="qp-card-title" style="margin:.2rem 0 0;">📦 Stock</div>
        <div class="qp-card-sub" style="margin-bottom:.5rem;">{total_units} units · worth Rs {stock_value}</div>
        """)
    with t2, st.container(key="restock_low"):
        if st.button(f"Restock {len(low_items)} low" if low_items else "Stock OK", use_container_width=True,
                     disabled=not low_items, help="Add 10 to every low or empty item"):
            for name in low_items:
                store.add_stock(name, 10)
            st.toast(f"Restocked {len(low_items)} item(s) (+10 each)", icon="📦")
            st.rerun(scope="fragment")
    for name, info in inventory.items():
        qty = stock.get(name, 0)
        is_low = qty <= LOW_STOCK
        bar_color = STATUS_ERROR if qty == 0 else STATUS_WARNING if is_low else BRAND_GREEN
        badge = status_pill("Out of stock" if qty == 0 else "Low stock", "red" if qty == 0 else "yellow") if is_low else ""
        bg = PRODUCT_INFO.get(name, {}).get("bg", "#EEF1EF")
        with st.container(key=f"stock_{name}"):
            c1, c2 = st.columns([2.6, 1], vertical_alignment="center")
            with c1:
                html(f"""
                <div class="qp-stock {'qp-stock-low' if is_low else ''}">
                    <div class="qp-stock-emoji" style="background:{bg};">{product_emoji(name)}</div>
                    <div>
                        <div class="qp-stock-name">{esc(product_name(name))} {badge}</div>
                        <div class="qp-stock-meta">{esc(station_name(info.get('shelf')))} · bin {esc(info.get('bin', '-'))} · Rs {info.get('price', 0)}</div>
                    </div>
                    <div class="qp-stock-num">{qty}</div>
                </div>
                <div class="qp-stock-bar"><div style="width:{min(qty, 20) / 20 * 100:.0f}%;background:{bar_color};"></div></div>
                """)
            with c2:
                if st.button("+10", key=f"restock_{name}", use_container_width=True, help="Restock 10 units"):
                    store.add_stock(name, 10)
                    st.toast(f"Restocked {product_name(name)} (+10)", icon="📦")
                    st.rerun(scope="fragment")


def ai_summary_card():
    """Shift summary: Nemotron Ultra, or rule-based without an API key (only when the owner presses the button)."""
    if "ai_summary" not in st.session_state:
        st.session_state.ai_summary = None

    with st.container(key="card_ai"):
        c1, c2 = st.columns([4, 1], vertical_alignment="center")
        engine = status_pill("Nemotron Ultra", "grey") if has_llm() else status_pill("Offline · rule-based", "grey")
        with c1:
            html(f"""
            <div class="qp-card-title">🧠 Shift summary {engine}</div>
            <div class="qp-card-sub" style="margin-top:.2rem;">What went well, problems, and what to restock.</div>
            """)
        with c2:
            if st.button("Generate", key="ai_gen", type="primary", use_container_width=True):
                with st.spinner("Writing the summary..."):
                    st.session_state.ai_summary = strip_think(generate_ai_summary())
        if st.session_state.ai_summary:
            with st.container(key="ai_text"):
                st.markdown(st.session_state.ai_summary)


# ============ Layout ============
# Each row is a keyed container: on narrow screens OWNER_CSS stacks its two columns.

live_header()
st.write("")
with st.container(key="row_map"):
    col_map, col_robot = st.columns([1.6, 1], gap="medium")
    with col_map:
        map_card()
    with col_robot:
        live_robot_card()
st.write("")

with st.container(key="row_log"):
    col_log, col_side = st.columns([1.6, 1], gap="medium")
    with col_log:
        live_step_log()
    with col_side, st.container(key="side_stack"):  # side by side on medium screens
        camera_card()
        live_hourly_chart()

st.write("")
with st.container(key="row_orders"):
    col_orders, col_stock = st.columns([1.6, 1], gap="medium")
    with col_orders:
        live_orders()
    with col_stock:
        live_stock()

st.write("")
ai_summary_card()
