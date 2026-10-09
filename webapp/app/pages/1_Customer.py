"""
QuickPick Customer Kiosk - touch-friendly ordering page (Raspberry Pi screen).

Features:
- Top bar with cart count, category chips, product grid with ADD / - 1 + buttons
- Search box: a short word filters products, a sentence goes to the Nemotron order parser
- Sticky cart with total and a fake "Pay" button
- Live order tracking (progress bar) and a receipt at the end
"""

import sys
import time
from datetime import datetime
from pathlib import Path

# Fix imports when running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from app import robot_client, store
from app.config import load_inventory
from app.order_parser import parse_order
from app.worker import init_worker
from app.ui_style import (
    BASE_CSS,
    CUSTOMER_CSS,
    CATEGORIES,
    CATEGORY_ICONS,
    PRODUCT_INFO,
    BRAND_GREEN_LIGHT,
    esc,
    html,
    inject_css,
    product_name,
    topbar_html,
    product_card_html,
    cart_item_html,
    progress_bar_html,
    receipt_html,
    status_pill,
)

# Page config
st.set_page_config(
    page_title="QuickPick - Order",
    page_icon="🛒",
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

# Session state initialization
defaults = {
    "cart": {},
    "page": "shop",
    "order_id": None,
    "error_message": None,
    "category": "All",
    "ai_parsed": False,
    "ai_note": "",
    "search_filter": "",
}
for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============ Cart logic (unchanged) ============

def get_cart_total(inventory):
    """Calculate cart total."""
    total = 0
    for item, qty in st.session_state.cart.items():
        if item in inventory:
            total += inventory[item]["price"] * qty
    return total


def get_cart_count():
    """Get total items in cart."""
    return sum(st.session_state.cart.values())


def add_to_cart(item, inventory):
    """Add one item to cart if stock available."""
    current = st.session_state.cart.get(item, 0)
    stock = inventory.get(item, {}).get("stock", 0)
    if current < stock:
        st.session_state.cart[item] = current + 1
        st.session_state.error_message = None


def remove_from_cart(item):
    """Remove one item from cart."""
    current = st.session_state.cart.get(item, 0)
    if current > 1:
        st.session_state.cart[item] = current - 1
    elif current == 1:
        del st.session_state.cart[item]
    st.session_state.error_message = None


def clear_cart():
    """Clear the cart."""
    st.session_state.cart = {}
    st.session_state.error_message = None
    st.session_state.ai_parsed = False


def parse_ai_order(text):
    """Parse order text using AI and fill cart."""
    if not text.strip():
        return

    try:
        parsed = parse_order(text)
        inventory = load_inventory()

        # Clear cart and add parsed items
        st.session_state.cart = {}
        for entry in parsed.get("items", []):
            item = entry["item"]
            qty = entry["qty"]
            if item in inventory:
                stock = inventory[item]["stock"]
                if stock > 0:
                    st.session_state.cart[item] = min(qty, stock)

        # Tell the customer what Nemotron understood
        added = [f"{qty}× {product_name(item)}" for item, qty in st.session_state.cart.items()]
        st.session_state.ai_parsed = bool(added)
        st.session_state.ai_note = ", ".join(added)

        # Show unknown items as error
        unknown = parsed.get("unknown", [])
        if unknown:
            st.session_state.error_message = f"We don't sell: {', '.join(unknown)}"
        elif not added:
            st.session_state.error_message = "Sorry, we could not find these products."
        else:
            st.session_state.error_message = None

    except Exception as e:
        st.session_state.error_message = f"Could not parse order: {str(e)}"


def place_order():
    """Place the order and start tracking."""
    if not st.session_state.cart:
        st.session_state.error_message = "Your cart is empty."
        return

    items = [{"item": item, "qty": qty} for item, qty in st.session_state.cart.items()]
    order_id, problems = store.create_order(items)

    if order_id:
        st.session_state.order_id = order_id
        st.session_state.page = "paying"
        st.session_state.error_message = None
    else:
        st.session_state.error_message = " ".join(problems) if problems else "Could not create order."


def reset_for_new_order():
    """Reset state for a new order."""
    st.session_state.cart = {}
    st.session_state.page = "shop"
    st.session_state.order_id = None
    st.session_state.error_message = None
    st.session_state.ai_parsed = False
    st.session_state.ai_note = ""
    st.session_state.search_filter = ""


# ============ Search ============

def looks_like_order(text):
    """A sentence ("2 soups and chips") goes to Nemotron. One or two words are a search."""
    words = text.lower().replace(",", " , ").split()
    has_number = any(ch.isdigit() for ch in text)
    return has_number or len(words) >= 3 or "and" in words or "," in words


def handle_search():
    """Called when the customer presses Enter or 'Go' in the search box."""
    text = st.session_state.search_text.strip()
    st.session_state.error_message = None
    if looks_like_order(text):
        st.session_state.search_filter = ""
        st.session_state.category_pills = "All"
        with st.spinner("Nemotron is reading your order..."):
            parse_ai_order(text)
    else:
        st.session_state.search_filter = text
        st.session_state.ai_parsed = False


def matches_search(item, text):
    """True if the product name, id or category contains the search text."""
    text = text.lower()
    info = PRODUCT_INFO.get(item, {})
    words = [item, info.get("name", ""), info.get("category", "")]
    return any(text in w.lower() for w in words)


# ============ Tracking ============

def count_basket_places(step_log):
    return sum(
        1 for e in step_log
        if e.get("tool") == "place_item"
        and e.get("input", {}).get("target") == "basket"
        and e.get("result", {}).get("success")
    )


def reached_customer(step_log):
    return any(
        e.get("tool") == "navigate_to"
        and e.get("input", {}).get("station") == "customer"
        and e.get("result", {}).get("success")
        for e in step_log
    )


def get_tracking_step(order):
    """Progress step: 0 placed, 1 at shelf, 2 picking, 3 coming, 4 delivered, 5 = all done."""
    status = order.get("status", "queued")
    log = order.get("step_log") or []
    needed = sum(entry.get("qty", 1) for entry in order.get("items", []))

    if status == "queued":
        return 0
    if status == "done" or reached_customer(log):
        return 5
    if needed and count_basket_places(log) >= needed:
        return 3
    if any(e.get("tool") in ("pick_item", "scan_item", "place_item") for e in log):
        return 2
    return 1


def last_picked_item(step_log):
    for e in reversed(step_log):
        if e.get("tool") == "pick_item":
            return e.get("input", {}).get("item", "")
    return ""


def get_status_message(order):
    """Friendly line under the progress bar, like 'Robot is picking your Tomato Soup'."""
    status = order.get("status", "queued")
    log = order.get("step_log") or []

    if status == "queued":
        ahead = sum(1 for o in store.get_orders(status="queued") if o["id"] < order["id"])
        if ahead:
            return "⏳", f"Your order is in line. {ahead} order(s) before you."
        return "⏳", "Your order is next. The robot is getting ready."
    if status == "done":
        return "🎉", "Delivered! Please take your basket. Enjoy!"
    if status == "failed":
        return "😕", "Sorry, the robot could not finish your order."

    if reached_customer(log):
        return "🎉", "Your basket is here! Robot will go home after."
    if get_tracking_step(order) == 3:
        return "🤖", "All items packed. Robot is coming to you!"
    if not log:
        return "🧭", "Robot is driving to the shelves..."

    last = log[-1]
    tool = last.get("tool")
    ok = last.get("result", {}).get("success")
    data = last.get("input", {})
    name = product_name(data.get("item") or last_picked_item(log))

    if not ok:
        return "🔁", "Small problem. Nemotron is fixing it, please wait..."
    if tool == "navigate_to":
        return "🗄️", f"Robot reached {data.get('station', 'the shelf').replace('_', ' ').title()}"
    if tool == "pick_item":
        return "🦾", f"Robot is picking your {name}"
    if tool == "scan_item":
        return "🔎", f"Checking the barcode of your {name}"
    if tool == "place_item":
        return "🧺", f"{name} is in your basket"
    return "🤖", "Robot is working on your order..."


# ============ Page ============

inject_css(BASE_CSS, CUSTOMER_CSS)
inventory = load_inventory()
demo = robot_client.is_demo_mode()


def show_shop():
    html(topbar_html(get_cart_count(), get_cart_total(inventory), demo=demo))

    col_products, col_cart = st.columns([2.3, 1], gap="large")

    with col_products:
        # Search box
        with st.container(key="card_search"):
            with st.form("search_form", border=False, enter_to_submit=True):
                c1, c2 = st.columns([5, 1], vertical_alignment="bottom")
                c1.text_input(
                    "Search",
                    placeholder="🔍  Search or type your order, e.g. 2 soups and chips",
                    label_visibility="collapsed",
                    key="search_text",
                )
                c2.form_submit_button("Go", type="primary", use_container_width=True, on_click=handle_search)

            if st.session_state.ai_parsed and st.session_state.cart:
                html(f'<span class="qp-ai-tag">✨ Understood by Nemotron · {esc(st.session_state.ai_note)}</span>')
            if st.session_state.search_filter:
                f1, f2 = st.columns([5, 1], vertical_alignment="center")
                f1.markdown(
                    f'<span class="qp-filter-note">Results for “{esc(st.session_state.search_filter)}”</span>',
                    unsafe_allow_html=True,
                )
                if f2.button("Clear", key="clear_search", type="tertiary"):
                    st.session_state.search_filter = ""
                    st.rerun()

        if st.session_state.error_message:
            st.error(st.session_state.error_message, icon="⚠️")

        # Category chips
        if "category_pills" not in st.session_state:
            st.session_state.category_pills = "All"
        with st.container(key="chips"):
            picked = st.pills(
                "Category",
                list(CATEGORIES.keys()),
                format_func=lambda c: f"{CATEGORY_ICONS.get(c, '')} {c}",
                label_visibility="collapsed",
                key="category_pills",
            )
        st.session_state.category = picked or "All"

        # Filter products by category and search
        filtered = []
        for name, info in inventory.items():
            cat_items = CATEGORIES.get(st.session_state.category)
            if cat_items is not None and name not in cat_items:
                continue
            if st.session_state.search_filter and not matches_search(name, st.session_state.search_filter):
                continue
            filtered.append((name, info))

        if not filtered:
            html('<div class="qp-cart-empty"><div class="qp-cart-empty-icon">🔍</div>No products found</div>')

        # Product grid
        cols = st.columns(3, gap="medium")
        for i, (name, info) in enumerate(filtered):
            with cols[i % 3]:
                show_product(name, info)

    with col_cart:
        show_cart()


def show_product(name, info):
    stock = info.get("stock", 0)
    in_cart = st.session_state.cart.get(name, 0)

    with st.container(key=f"prod_{name}"):
        html(product_card_html(name, info.get("price", 0), stock))

        if stock == 0:
            st.button("Out of stock", key=f"oos_{name}", use_container_width=True, disabled=True)
        elif in_cart == 0:
            if st.button("ADD", key=f"add_{name}", use_container_width=True):
                add_to_cart(name, inventory)
                st.rerun()
        else:
            with st.container(
                key=f"qty_{name}", horizontal=True,
                horizontal_alignment="distribute", vertical_alignment="center", gap=None,
            ):
                if st.button("−", key=f"minus_{name}"):
                    remove_from_cart(name)
                    st.rerun()
                html(f'<div class="qp-qty-num">{in_cart}</div>')
                if st.button("+", key=f"plus_{name}", disabled=in_cart >= stock):
                    add_to_cart(name, inventory)
                    st.rerun()


def show_cart():
    with st.container(key="card_cart"):
        count = get_cart_count()
        html(f"""
        <div class="qp-card-title" style="justify-content:space-between;">
            <span>🛒 Your cart</span>
            {status_pill(f"{count} item{'s' if count != 1 else ''}", 'green' if count else 'grey')}
        </div>
        """)

        if not st.session_state.cart:
            html("""
            <div class="qp-cart-empty">
                <div class="qp-cart-empty-icon">🧺</div>
                <div style="font-weight:700;color:#18231D;">Your cart is empty</div>
                <div style="font-size:.85rem;margin-top:.2rem;">Tap ADD, or just type your order above</div>
            </div>
            """)
            return

        rows = ""
        for item, qty in st.session_state.cart.items():
            if item in inventory:
                rows += cart_item_html(item, qty, inventory[item]["price"])
        subtotal = get_cart_total(inventory)
        html(f"""
        {rows}
        <div style="margin-top:.6rem;">
            <div class="qp-row qp-row-muted"><span>Subtotal</span><span>Rs {subtotal}</span></div>
            <div class="qp-row qp-row-free"><span>🤖 Robot delivery</span><span>FREE</span></div>
            <div class="qp-row qp-row-total"><span>Total</span><span>Rs {subtotal}</span></div>
        </div>
        """)

        if st.button(f"Pay Rs {subtotal}  →", key="pay_btn", use_container_width=True, type="primary"):
            place_order()
            st.rerun()
        if st.button("Clear cart", key="clear_btn", use_container_width=True, type="tertiary"):
            clear_cart()
            st.rerun()
        html('<div class="qp-powered">Picked by robot · Planned by NVIDIA Nemotron</div>')


def show_paying():
    """Fake payment: short 'processing' screen, then 'success', then tracking."""
    _, mid, _ = st.columns([1, 1.6, 1])
    with mid, st.container(key="card_pay"):
        box = st.empty()
        with box:
            html(f"""
            <div class="qp-pay">
                <div class="qp-pay-icon" style="background:#FFF4DA;"><span class="qp-live">💳</span></div>
                <div class="qp-pay-title">Processing payment…</div>
                <div class="qp-pay-sub">Order #{st.session_state.order_id} · Demo payment</div>
            </div>
            """)
        time.sleep(1.5)
        with box:
            html(f"""
            <div class="qp-pay">
                <div class="qp-pay-icon" style="background:{BRAND_GREEN_LIGHT};">✅</div>
                <div class="qp-pay-title">Payment successful</div>
                <div class="qp-pay-sub">Sending your order to the robot…</div>
            </div>
            """)
        time.sleep(1)
    st.session_state.page = "tracking"
    st.rerun()


def show_tracking_missing(text):
    st.error(text)
    if st.button("Start new order", type="primary"):
        reset_for_new_order()
        st.rerun()


@st.fragment(run_every=1.5)
def show_tracking():
    """Live order tracking. Refreshes itself every 1.5 seconds."""
    order_id = st.session_state.order_id
    order = store.get_order(order_id) if order_id else None
    if not order:
        show_tracking_missing("Order not found.")
        return

    status = order["status"]
    step = get_tracking_step(order)
    icon, message = get_status_message(order)
    item_count = sum(entry.get("qty", 1) for entry in order.get("items", []))
    try:
        placed_at = datetime.fromisoformat(order["created_at"]).strftime("%H:%M")
    except (ValueError, TypeError):
        placed_at = "-"

    with st.container(key="card_track"):
        html(f"""
        <div class="qp-track-head">
            <div class="qp-track-label">Order number</div>
            <div class="qp-track-id">#{order_id}</div>
            <div class="qp-track-meta">Placed at {placed_at} · {item_count} item{'s' if item_count != 1 else ''}</div>
        </div>
        """)
        html(progress_bar_html(step, failed=(status == "failed")))

        msg_class = "qp-track-msg qp-track-msg-failed" if status == "failed" else "qp-track-msg"
        html(f'<div class="{msg_class}"><span style="font-size:1.6rem;">{icon}</span>{esc(message)}</div>')

        problems = order.get("problems") or []
        if problems:
            html(f'<div class="qp-problem"><b>What happened:</b> {esc(" · ".join(problems))}</div>')

        if status in ("done", "failed"):
            bill = order.get("bill") or {}
            bill_items = bill.get("items", []) if isinstance(bill, dict) else []
            if bill_items:
                title = "Receipt" if status == "done" else "Partial receipt"
                note = f"Order #{order_id} · Picked by robot · Bill written by NVIDIA Nemotron"
                html(receipt_html(bill_items, bill.get("total", 0), title, note))
            st.write("")
            if st.button("🛒  New order", key="new_order", use_container_width=True, type="primary"):
                reset_for_new_order()
                st.rerun()
        else:
            html('<div class="qp-track-live"><span class="qp-live" style="color:#E5484D;">●</span> Live · updates by itself</div>')


if st.session_state.page == "shop":
    show_shop()
elif st.session_state.page == "paying":
    show_paying()
elif st.session_state.page == "tracking":
    if not st.session_state.order_id:
        show_tracking_missing("No order to track.")
    else:
        html(topbar_html(0, 0, show_cart=False, demo=demo))
        _, mid, _ = st.columns([1, 2.4, 1])
        with mid:
            show_tracking()
