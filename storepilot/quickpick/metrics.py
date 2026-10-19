"""
Numbers for the owner dashboard, worked out from orders (no robot, no model needed).

    order_times(order)        -> (wait seconds, pack seconds)
    avg_pack_seconds(orders)  -> average pack time of finished orders
    pick_stats(orders)        -> (successful picks, pick tries)
    orders_by_hour(orders)    -> [(hour, count), ...] for the chart
    order_progress(order)     -> (items in basket, items ordered)
    attention_items(...)      -> things the owner should look at now
    orders_csv(orders)        -> today's orders as CSV text
"""

import csv
import io
from datetime import datetime

from quickpick.step_log import basket_item

CHARGED = ("done", "done_with_problems")


def _time(iso):
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def order_times(order, now=None):
    """(wait_sec, pack_sec). wait = placed -> first robot step, pack = first -> last step.
    For an order still packing, pack runs until now. None when not known."""
    log = order.get("step_log") or []
    created = _time(order.get("created_at"))
    times = [t for t in (_time(e.get("time")) for e in log) if t]
    if not created or not times:
        return None, None
    first, last = min(times), max(times)
    if order.get("status") == "packing":
        last = now or datetime.now()
    return max((first - created).total_seconds(), 0), max((last - first).total_seconds(), 0)


def avg_pack_seconds(orders):
    """Average pack time of orders the robot finished (done / done with problems)."""
    times = [order_times(o)[1] for o in orders if o.get("status") in CHARGED]
    times = [t for t in times if t is not None]
    return sum(times) / len(times) if times else None


def pick_stats(orders):
    """(ok, tries) over all GR00T picks that reached the robot (refused ones do not count)."""
    ok = tries = 0
    for order in orders:
        for entry in order.get("step_log") or []:
            result = entry.get("result") or {}
            if entry.get("tool") != "pick_item" or result.get("refused"):
                continue
            tries += 1
            ok += bool(result.get("success"))
    return ok, tries


def orders_by_hour(orders, now=None, min_hours=8):
    """[(hour, count)] from the first order's hour (or earlier, to show at least
    min_hours bars) until the current hour."""
    now = now or datetime.now()
    counts = {}
    for order in orders:
        created = _time(order.get("created_at"))
        if created and created.date() == now.date():
            counts[created.hour] = counts.get(created.hour, 0) + 1
    start = min([now.hour - min_hours + 1] + list(counts))
    start = max(start, 0)
    return [(hour, counts.get(hour, 0)) for hour in range(start, now.hour + 1)]


def order_progress(order):
    """(packed, needed): units placed in the basket (up to the ordered qty) and units ordered."""
    needed = {}
    for entry in order.get("items") or []:
        needed[entry["item"]] = needed.get(entry["item"], 0) + entry.get("qty", 1)
    packed = {}
    for entry in order.get("step_log") or []:
        item = basket_item(entry)
        if item:
            packed[item] = packed.get(item, 0) + 1
    done = sum(min(packed.get(item, 0), qty) for item, qty in needed.items())
    return done, sum(needed.values())


def format_duration(seconds):
    """75 -> '1m 15s', 42 -> '42s', None -> '-'."""
    if seconds is None:
        return "-"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60:02d}s"


def attention_items(robot, stats, stock, low_stock, llm_ready, demo=False):
    """What the owner should look at now: [(level, icon, text)], most urgent first.
    level: "critical", "warning" or "info"."""
    items = []
    if not robot.get("online"):
        items.append(("critical", "📡", "Robot offline. New orders wait in the queue."))
    elif robot.get("stopped"):
        items.append(("critical", "🛑", "Emergency stop is on. Press Resume when the area is safe."))
    else:
        battery = robot.get("battery") or 0
        if battery < 20:
            items.append(("critical", "🪫", f"Battery {battery:.0f}%. Send the robot to charge."))
        elif battery < 35:
            items.append(("warning", "🔋", f"Battery {battery:.0f}%. Charge soon."))
        if robot.get("obstacle"):
            items.append(("warning", "⚠️", "Obstacle in the robot's path."))

    out = [name for name, qty in stock.items() if qty == 0]
    low = [f"{name} ({qty})" for name, qty in stock.items() if 0 < qty <= low_stock]
    if out:
        items.append(("critical", "📦", "Out of stock: " + ", ".join(out)))
    if low:
        items.append(("warning", "📉", "Low stock: " + ", ".join(low)))
    if stats.get("failed"):
        n = stats["failed"]
        items.append(("warning", "❌", f"{n} order{'s' if n != 1 else ''} failed today."))
    if stats.get("queued", 0) >= 3:
        items.append(("warning", "⏳", f"{stats['queued']} orders waiting in the queue."))
    if not llm_ready:
        items.append(("info", "🧭", "No NEBIUS_API_KEY: orders are planned by the offline autopilot, not Nemotron."))
    return items


def orders_csv(orders):
    """Orders as CSV text (id, time, status, items, total, problems)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["order_id", "created_at", "status", "items", "total", "wait_sec", "pack_sec", "problems"])
    for order in orders:
        bill = order.get("bill") if isinstance(order.get("bill"), dict) else {}
        wait, pack = order_times(order)
        items = "; ".join(f"{e.get('qty', 1)}x {e.get('item')}" for e in order.get("items") or [])
        writer.writerow([
            order.get("id"), order.get("created_at"), order.get("status"), items, bill.get("total", 0),
            "" if wait is None else round(wait), "" if pack is None else round(pack),
            " | ".join(order.get("problems") or []),
        ])
    return out.getvalue()
