"""
Step 3: Streamlit web UI for the Dark Store Order Picker.

Run:
    streamlit run app/ui.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import threading
import time

import streamlit as st

from app import robot_client
from app.agent import process_order
from app.config import MODEL_AGENT, MODEL_FAST, ROBOT_URL, load_inventory

# Page config
st.set_page_config(
    page_title="Dark Store Order Picker",
    page_icon="🤖",
    layout="wide",
)

# Demo mode: run mock robot in-process
DEMO_MODE_KEY = "demo_mode"
DEMO_SERVER_KEY = "demo_server"


def start_demo_server():
    """Start mock robot server in a background thread for demo mode."""
    if DEMO_SERVER_KEY in st.session_state and st.session_state[DEMO_SERVER_KEY]:
        return  # Already running

    import uvicorn
    from robot_server.mock_robot import app as mock_app

    def run_server():
        uvicorn.run(mock_app, host="127.0.0.1", port=8000, log_level="warning")

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    st.session_state[DEMO_SERVER_KEY] = True
    time.sleep(1)  # Wait for server to start


def check_robot_health():
    """Check if robot is online."""
    try:
        result = robot_client.health()
        return result.get("status") == "ok"
    except Exception:
        return False


def get_step_icon(entry):
    """Get icon for a step based on tool and result."""
    tool = entry["tool"]
    result = entry["result"]
    success = result.get("success", True)

    if tool == "get_inventory":
        return "📦"
    elif tool == "pick_item":
        return "✅" if success else "🔄"
    elif tool == "scan_item":
        if success:
            return "📷"
        else:
            return "🔄"
    elif tool == "place_item":
        target = entry["input"].get("target", "")
        if target == "basket" and success:
            return "🛒"
        elif target == "return":
            return "↩️"
        else:
            return "❌"
    return "⚙️"


def format_step(entry):
    """Format a step entry for display."""
    tool = entry["tool"]
    result = entry["result"]
    success = result.get("success", True)

    if tool == "get_inventory":
        return "Getting inventory list..."
    elif tool == "pick_item":
        item = entry["input"].get("item", "?")
        if success:
            return f"Picked {item}"
        else:
            return f"Failed to pick {item}: {result.get('message', 'error')}"
    elif tool == "scan_item":
        if success:
            product = result.get("product", "?")
            return f"Scanned barcode: {product}"
        else:
            return f"Scan failed: {result.get('message', 'barcode not readable')}"
    elif tool == "place_item":
        target = entry["input"].get("target", "?")
        if success:
            if target == "basket":
                return "Placed in basket"
            else:
                return f"Returned to bin"
        else:
            return f"Place failed: {result.get('message', 'error')}"
    return f"{tool}: {result}"


# Initialize session state
if "order_text" not in st.session_state:
    st.session_state.order_text = ""
if "steps" not in st.session_state:
    st.session_state.steps = []
if "parsed_order" not in st.session_state:
    st.session_state.parsed_order = None
if "bill" not in st.session_state:
    st.session_state.bill = None
if "running" not in st.session_state:
    st.session_state.running = False
if DEMO_MODE_KEY not in st.session_state:
    st.session_state[DEMO_MODE_KEY] = False

# Header
st.title("🤖 Dark Store Order Picker")
st.caption("Powered by NVIDIA Nemotron on Nebius Token Factory")

# Demo mode toggle in sidebar
with st.sidebar:
    st.header("Settings")
    demo_mode = st.toggle(
        "Demo Mode",
        value=st.session_state[DEMO_MODE_KEY],
        help="Run with built-in mock robot (no separate server needed)"
    )
    st.session_state[DEMO_MODE_KEY] = demo_mode

    if demo_mode:
        start_demo_server()
        st.success("Demo mode: using built-in mock robot")
        robot_url = "http://127.0.0.1:8000 (demo)"
    else:
        robot_url = ROBOT_URL

    st.divider()
    st.subheader("Robot Status")
    robot_online = check_robot_health()
    if robot_online:
        st.success("🟢 Robot Online")
    else:
        st.error("🔴 Robot Offline")
    st.caption(f"URL: {robot_url}")

    st.divider()
    st.subheader("Models")
    st.caption(f"**Parser:** {MODEL_FAST.split('/')[-1]}")
    st.caption(f"**Agent:** {MODEL_AGENT.split('/')[-1]}")

    st.divider()
    st.subheader("Inventory")
    inventory = load_inventory()
    for name, info in inventory.items():
        st.caption(f"• {name}: ₹{info['price']} ({info['weight']})")

# Main content
col1, col2 = st.columns([1, 2])

with col1:
    st.subheader("Place Order")

    # Order text input
    order_text = st.text_area(
        "What would you like to order?",
        value=st.session_state.order_text,
        height=100,
        placeholder="Example: 2 soup cans and one chips",
        key="order_input"
    )

    # Example buttons
    st.caption("Quick examples:")
    example_cols = st.columns(3)
    with example_cols[0]:
        if st.button("🥫 Soup + Chips", use_container_width=True):
            st.session_state.order_text = "2 soup cans and one chips"
            st.rerun()
    with example_cols[1]:
        if st.button("🧃 All Items", use_container_width=True):
            st.session_state.order_text = "soup, juice and chips"
            st.rerun()
    with example_cols[2]:
        if st.button("🍕 With Unknown", use_container_width=True):
            st.session_state.order_text = "one soup and a pizza"
            st.rerun()

    # Place order button
    st.divider()
    place_button = st.button(
        "🚀 Place Order",
        type="primary",
        use_container_width=True,
        disabled=st.session_state.running or not order_text.strip()
    )

with col2:
    st.subheader("Order Progress")

    # Check robot before starting
    if place_button:
        if not check_robot_health():
            st.error("❌ Cannot place order: Robot is offline. Start the robot server or enable Demo Mode.")
        elif not order_text.strip():
            st.warning("Please enter an order first.")
        else:
            # Reset state
            st.session_state.steps = []
            st.session_state.parsed_order = None
            st.session_state.bill = None
            st.session_state.running = True
            st.session_state.order_text = order_text

    # Show parsed order
    if st.session_state.running or st.session_state.parsed_order:
        with st.container():
            if st.session_state.parsed_order:
                parsed = st.session_state.parsed_order
                items_text = ", ".join(f"{e['qty']}x {e['item']}" for e in parsed.get("items", []))
                unknown_text = ", ".join(parsed.get("unknown", []))

                st.markdown(f"**Parsed order** (by {MODEL_FAST.split('/')[-1]})")
                if items_text:
                    st.success(f"📋 Items: {items_text}")
                if unknown_text:
                    st.warning(f"❓ Unknown: {unknown_text}")

    # Step log container
    step_container = st.container()

    # Bill container
    bill_container = st.container()

    # Process the order
    if st.session_state.running:
        steps_placeholder = step_container.empty()
        progress_placeholder = st.empty()

        def on_step(entry):
            st.session_state.steps.append(entry)

        def on_parsed(parsed):
            st.session_state.parsed_order = parsed

        try:
            progress_placeholder.info("🔄 Processing order...")

            # Run the agent
            bill, log = process_order(
                st.session_state.order_text,
                on_step=on_step,
                on_parsed=on_parsed
            )

            st.session_state.bill = bill
            st.session_state.steps = log
            progress_placeholder.empty()

        except Exception as e:
            progress_placeholder.error(f"❌ Error: {str(e)}")
            st.session_state.bill = {"items": [], "total": 0, "problems": [str(e)]}

        finally:
            st.session_state.running = False
            st.rerun()

    # Show steps
    if st.session_state.steps:
        with step_container:
            st.markdown(f"**Step Log** (by {MODEL_AGENT.split('/')[-1]})")
            for entry in st.session_state.steps:
                icon = get_step_icon(entry)
                text = format_step(entry)
                success = entry["result"].get("success", True)

                if entry["tool"] == "get_inventory":
                    st.caption(f"{icon} {text}")
                elif success:
                    st.success(f"{icon} {text}")
                elif entry["tool"] in ["pick_item", "scan_item"] and not success:
                    st.warning(f"{icon} {text}")
                else:
                    st.error(f"{icon} {text}")

    # Show bill
    if st.session_state.bill:
        with bill_container:
            st.divider()
            bill = st.session_state.bill

            st.markdown("### 🧾 Bill")

            if bill.get("items"):
                # Create a nice bill display
                for item in bill["items"]:
                    col_a, col_b, col_c = st.columns([3, 1, 1])
                    with col_a:
                        st.write(f"**{item['item']}**")
                    with col_b:
                        st.write(f"x{item['qty']}")
                    with col_c:
                        st.write(f"₹{item['price'] * item['qty']}")

                st.divider()
                total_col1, total_col2 = st.columns([3, 1])
                with total_col1:
                    st.markdown("**Total**")
                with total_col2:
                    st.markdown(f"**₹{bill['total']}**")
            else:
                st.info("No items packed.")

            # Show problems
            if bill.get("problems"):
                st.divider()
                st.markdown("**Notes:**")
                for problem in bill["problems"]:
                    if "not found" in problem or "not available" in problem:
                        st.warning(f"⚠️ {problem}")
                    else:
                        st.error(f"❌ {problem}")
