"""
QuickPick - Home Page

Entry point for the Streamlit multipage app.
Run: streamlit run app/Home.py
"""

import sys
from pathlib import Path

# Fix imports when running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from app.worker import init_worker
from app.ui_style import (
    BASE_CSS,
    HOME_CSS,
    BRAND_GREEN_LIGHT,
    BRAND_YELLOW_LIGHT,
    html,
    inject_css,
    logo_html,
)

# Page config
st.set_page_config(
    page_title="QuickPick",
    page_icon="🛒",
    layout="centered",
    initial_sidebar_state="collapsed",
)


@st.cache_resource
def start_background_worker():
    """Start the background worker once, shared across all pages."""
    init_worker()
    return True


# Start worker on app load
start_background_worker()

inject_css(BASE_CSS, HOME_CSS)

# Hero
html(f"""
<div class="qp-hero">
    <div class="qp-hero-badge">🧠 NVIDIA Nemotron plans · 🦾 GR00T picks · 🤖 Robot delivers</div>
    <div>{logo_html('3.6rem')}</div>
    <div class="qp-hero-line">
        Your grocery order, picked and packed by a mobile robot in minutes.
    </div>
</div>
""")

# Two big cards
col1, col2 = st.columns(2, gap="large")

with col1, st.container(key="home_kiosk"):
    html(f"""
    <div class="qp-home-icon" style="background:{BRAND_GREEN_LIGHT};">🛒</div>
    <div class="qp-home-title">Customer kiosk</div>
    <div class="qp-home-desc">Pick products or just type your order. Pay, then watch the robot pack it live.</div>
    """)
    if st.button("Open kiosk  →", use_container_width=True, type="primary"):
        st.switch_page("pages/1_Customer.py")

with col2, st.container(key="home_control"):
    html(f"""
    <div class="qp-home-icon" style="background:{BRAND_YELLOW_LIGHT};">📡</div>
    <div class="qp-home-title">Store control</div>
    <div class="qp-home-desc">Live robot map, Nemotron step log, orders, stock and the emergency STOP.</div>
    """)
    if st.button("Open store control  →", use_container_width=True):
        st.switch_page("pages/2_Owner.py")

html("""
<div class="qp-home-foot">
    Nebius × NVIDIA Global AI Hackathon 2026 · Physical AI track
</div>
""")
