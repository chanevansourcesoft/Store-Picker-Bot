"""
QuickPick look: shared CSS and small HTML helpers for all pages.

How it works:
- inject_css() puts our CSS on the page.
- html() shows a piece of HTML.
- A card with buttons inside is an st.container(key="...").
  Streamlit gives it the CSS class "st-key-<key>", and our CSS styles that class.

Brand: one green + one warm yellow, white cards, light grey page, Inter font.
The theme colors in .streamlit/config.toml must match the colors below.
"""

import html as html_lib
from datetime import datetime

import streamlit as st

# --- Brand colors ---
BRAND_GREEN = "#14A85A"
BRAND_GREEN_DARK = "#0E8446"
BRAND_GREEN_LIGHT = "#E5F6EC"
BRAND_YELLOW = "#FFB627"
BRAND_YELLOW_LIGHT = "#FFF4DA"
BRAND_BG = "#F3F5F4"
BRAND_CARD = "#FFFFFF"
BRAND_TEXT = "#18231D"
BRAND_MUTED = "#6B7A72"
BRAND_BORDER = "#E3E8E5"
BRAND_DARK = "#0F1A14"  # owner header

# --- Status colors ---
STATUS_SUCCESS = BRAND_GREEN
STATUS_WARNING = "#F08C00"
STATUS_ERROR = "#E5484D"
STATUS_INFO = "#3B82F6"

LOW_STOCK = 3  # "Only N left" when stock is this or less

# Category chips on the customer page (None = show everything)
CATEGORIES = {
    "All": None,
    "Snacks": ["chips_pack"],
    "Drinks": ["juice_box"],
    "Soups": ["soup_can"],
}
CATEGORY_ICONS = {"All": "🛒", "Snacks": "🍿", "Drinks": "🧃", "Soups": "🥫"}

# How each product looks in the app
PRODUCT_INFO = {
    "soup_can": {
        "emoji": "🥫",
        "name": "Tomato Soup",
        "size": "400 g",
        "category": "Soups",
        "bg": "#FDE8E4",
    },
    "chips_pack": {
        "emoji": "🍿",
        "name": "Classic Chips",
        "size": "150 g",
        "category": "Snacks",
        "bg": "#FFF1D2",
    },
    "juice_box": {
        "emoji": "🧃",
        "name": "Orange Juice",
        "size": "200 ml",
        "category": "Drinks",
        "bg": "#E3EEFD",
    },
}


# ============ Small helpers ============

def esc(text):
    """Make any text safe to put inside HTML."""
    return html_lib.escape(str(text))


def product_name(item):
    return PRODUCT_INFO.get(item, {}).get("name", item.replace("_", " ").title())


def product_emoji(item):
    return PRODUCT_INFO.get(item, {}).get("emoji", "📦")


def station_name(station):
    """'shelf_A' -> 'Shelf A', 'home' -> 'Home'."""
    return str(station or "?").replace("_", " ").title()


def html(markup):
    """Show HTML. Lines are joined so Markdown never turns them into code blocks."""
    one_line = " ".join(line.strip() for line in markup.splitlines() if line.strip())
    st.markdown(one_line, unsafe_allow_html=True)


def inject_css(*parts):
    """Put CSS on the page. It takes no space on the page."""
    st.html("<style>" + "".join(parts) + "</style>")


def logo_html(size="1.6rem", dark=False):
    """QuickPick logo text with a small green icon."""
    pick_color = "#FFFFFF" if dark else BRAND_TEXT
    return f"""
    <span class="qp-logo" style="font-size:{size};">
        <span class="qp-logo-mark">⚡</span>
        <span><span style="color:{BRAND_GREEN};">Quick</span><span style="color:{pick_color};">Pick</span></span>
    </span>
    """


# ============ CSS ============

BASE_CSS = f"""
/* Hide Streamlit menu, header, footer and sidebar */
header[data-testid="stHeader"], #MainMenu, footer,
[data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"],
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="stSidebarNav"],
.stDeployButton {{ display: none !important; }}

.block-container, [data-testid="stMainBlockContainer"] {{
    padding: 1rem 1.5rem 2rem !important;
    max-width: 1400px !important;
}}
.stApp {{ background: {BRAND_BG} !important; }}
html, body, .stApp, button, input, [data-testid="stMarkdownContainer"] {{
    font-family: 'Inter', -apple-system, 'Segoe UI', 'Noto Color Emoji', 'Apple Color Emoji', 'Segoe UI Emoji', sans-serif !important;
}}
.stApp {{ color: {BRAND_TEXT}; }}

/* No grey "loading" fade when live parts refresh */
[data-stale="true"] {{ opacity: 1 !important; transition: none !important; }}

/* Remove the empty space Markdown adds after our HTML */
[data-testid="stMarkdownContainer"] p {{ margin-bottom: 0; }}

/* Buttons */
.stButton > button, .stFormSubmitButton > button {{
    border-radius: 12px !important;
    font-weight: 700 !important;
    min-height: 2.75rem;
    transition: transform .12s ease, box-shadow .12s ease, background .12s ease !important;
}}
.stButton > button p, .stFormSubmitButton > button p {{ font-weight: inherit; }}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"],
.stButton > button[kind="primaryFormSubmit"], .stFormSubmitButton > button[kind="primaryFormSubmit"] {{
    background: {BRAND_GREEN} !important; border: none !important; color: #fff !important;
    box-shadow: 0 4px 12px rgba(20,168,90,.25);
}}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button:hover {{
    background: {BRAND_GREEN_DARK} !important; transform: translateY(-1px);
}}
.stButton > button[kind="secondary"] {{
    background: #fff !important; border: 1.5px solid {BRAND_BORDER} !important; color: {BRAND_TEXT} !important;
}}
.stButton > button[kind="secondary"]:hover {{ border-color: {BRAND_GREEN} !important; color: {BRAND_GREEN_DARK} !important; }}
.stButton > button:disabled {{
    background: #EEF1EF !important; color: #A3AEA8 !important; border: none !important; box-shadow: none !important;
}}

[data-testid="InputInstructions"] {{ display: none !important; }}
/* Text input */
.stTextInput input {{ font-size: 1rem !important; padding: .8rem 1rem !important; }}

/* Generic white card (keyed container) */
[class*="st-key-card"] {{
    background: {BRAND_CARD};
    border-radius: 16px;
    padding: 1.1rem 1.2rem;
    box-shadow: 0 1px 2px rgba(16,24,20,.04), 0 6px 20px rgba(16,24,20,.06);
}}
.qp-card-title {{
    font-weight: 700; font-size: 1.02rem; display: flex; align-items: center; gap: .5rem;
}}
.qp-card-sub {{ color: {BRAND_MUTED}; font-size: .8rem; font-weight: 500; }}

/* Logo */
.qp-logo {{ font-weight: 800; letter-spacing: -0.02em; display: inline-flex; align-items: center; gap: .45rem; }}
.qp-logo-mark {{
    display: inline-flex; align-items: center; justify-content: center;
    width: 1.5em; height: 1.5em; border-radius: .45em; font-size: .7em;
    background: {BRAND_GREEN}; color: {BRAND_YELLOW}; box-shadow: 0 3px 10px rgba(20,168,90,.35);
}}

/* Pills and tags */
.qp-pill {{
    display: inline-flex; align-items: center; gap: .35rem;
    padding: .22rem .65rem; border-radius: 999px; font-size: .75rem; font-weight: 700; white-space: nowrap;
}}
.qp-pill-dot {{ width: .5rem; height: .5rem; border-radius: 50%; background: currentColor; }}
.qp-pill-green  {{ background: {BRAND_GREEN_LIGHT}; color: {BRAND_GREEN_DARK}; }}
.qp-pill-yellow {{ background: {BRAND_YELLOW_LIGHT}; color: #8A5A00; }}
.qp-pill-blue   {{ background: #E4EEFE; color: #1D4FB8; }}
.qp-pill-red    {{ background: #FDE7E8; color: #B42328; }}
.qp-pill-grey   {{ background: #EEF1EF; color: {BRAND_MUTED}; }}

/* "Demo mode: simulated robot" badge */
.qp-demo-badge {{
    display: inline-flex; align-items: center; gap: .35rem; white-space: nowrap;
    background: #EEF1EF; color: #4A5750; border: 1px dashed #B8C3BD;
    padding: .3rem .7rem; border-radius: 999px; font-size: .75rem; font-weight: 700;
}}
.qp-dash-header .qp-demo-badge {{ background: rgba(255,255,255,.1); color: #fff; border-color: rgba(255,255,255,.35); }}

@keyframes qp-pulse {{ 0%,100% {{ transform: scale(1); }} 50% {{ transform: scale(1.08); }} }}
@keyframes qp-blink {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: .3; }} }}
.qp-live {{ animation: qp-blink 1.4s infinite; }}
"""

CUSTOMER_CSS = f"""
/* Top bar */
.qp-topbar {{
    background: #fff; border-radius: 16px; padding: .8rem 1.2rem; margin-bottom: .9rem;
    display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap;
    box-shadow: 0 1px 2px rgba(16,24,20,.04), 0 6px 20px rgba(16,24,20,.06);
}}
.qp-topbar-left {{ display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }}
.qp-badge {{
    display: inline-flex; align-items: center; gap: .4rem;
    background: {BRAND_YELLOW_LIGHT}; color: #7A4F00; border: 1px solid #FFE3A3;
    padding: .35rem .8rem; border-radius: 999px; font-size: .82rem; font-weight: 700;
}}
.qp-cart-btn {{
    display: inline-flex; align-items: center; gap: .6rem;
    background: {BRAND_GREEN}; color: #fff; padding: .55rem 1rem; border-radius: 12px;
    font-weight: 700; box-shadow: 0 4px 12px rgba(20,168,90,.3);
}}
.qp-cart-btn small {{ display: block; font-size: .7rem; font-weight: 600; opacity: .9; line-height: 1.1; }}
.qp-cart-count {{
    background: #fff; color: {BRAND_GREEN_DARK}; min-width: 1.6rem; height: 1.6rem; border-radius: 8px;
    display: inline-flex; align-items: center; justify-content: center; font-size: .85rem;
}}

/* Search card */
.st-key-card_search .stForm {{ border: none; padding: 0; }}
.qp-ai-tag {{
    display: inline-flex; align-items: center; gap: .35rem; margin-top: .35rem;
    background: linear-gradient(90deg, {BRAND_YELLOW_LIGHT}, #FFFBEF); color: #7A4F00;
    border: 1px solid #FFE3A3; padding: .25rem .7rem; border-radius: 999px; font-size: .78rem; font-weight: 700;
}}
.qp-filter-note {{ color: {BRAND_MUTED}; font-size: .85rem; }}

/* Category chips (st.pills) */
.st-key-chips [data-testid="stButtonGroup"] button {{
    border-radius: 999px !important; padding: .45rem 1.1rem !important; font-weight: 600 !important;
    background: #fff; border: 1.5px solid {BRAND_BORDER} !important; min-height: 2.6rem;
}}
.st-key-chips [data-testid="stButtonGroup"] button[aria-checked="true"] {{
    background: {BRAND_GREEN} !important; border-color: {BRAND_GREEN} !important; color: #fff !important;
    box-shadow: 0 4px 12px rgba(20,168,90,.25);
}}
.st-key-chips [data-testid="stButtonGroup"] button[aria-checked="true"] p {{ color: #fff !important; }}

/* Product card */
[class*="st-key-prod_"] {{
    background: #fff; border-radius: 16px; padding: .8rem .8rem .9rem;
    box-shadow: 0 1px 2px rgba(16,24,20,.04), 0 6px 20px rgba(16,24,20,.06);
    transition: transform .15s ease, box-shadow .15s ease;
}}
[class*="st-key-prod_"]:hover {{ transform: translateY(-2px); box-shadow: 0 10px 28px rgba(16,24,20,.10); }}
.qp-prod-tile {{
    position: relative; height: 130px; border-radius: 12px;
    display: flex; align-items: center; justify-content: center; font-size: 4.2rem;
}}
.qp-prod-flag {{
    position: absolute; left: .5rem; top: .5rem; font-size: .7rem; font-weight: 800;
    padding: .2rem .5rem; border-radius: 6px; background: #fff;
}}
.qp-prod-flag-low {{ color: {STATUS_WARNING}; }}
.qp-prod-flag-out {{ color: {BRAND_MUTED}; }}
.qp-prod-eta {{
    display: inline-flex; align-items: center; gap: .25rem; margin-top: .65rem;
    font-size: .68rem; font-weight: 700; color: {BRAND_MUTED}; background: #F3F5F4;
    padding: .15rem .45rem; border-radius: 6px;
}}
.qp-prod-name {{ font-weight: 700; font-size: 1rem; margin-top: .35rem; line-height: 1.25; }}
.qp-prod-size {{ color: {BRAND_MUTED}; font-size: .82rem; margin-top: .1rem; }}
.qp-prod-price {{ font-weight: 800; font-size: 1.15rem; margin: .45rem 0 .1rem; }}
.qp-prod-low {{ color: {STATUS_WARNING}; font-size: .78rem; font-weight: 700; }}
.qp-prod-out {{ color: {BRAND_MUTED}; font-size: .78rem; font-weight: 700; }}
.qp-out {{ filter: grayscale(1); opacity: .55; }}

/* ADD button: white with green border and green text */
[class*="st-key-add_"] .stButton > button[kind] {{
    background: #fff !important; color: {BRAND_GREEN_DARK} !important;
    border: 1.5px solid {BRAND_GREEN} !important; font-weight: 800 !important; letter-spacing: .04em;
    box-shadow: none !important;
}}
[class*="st-key-add_"] .stButton > button[kind]:hover {{ background: {BRAND_GREEN_LIGHT} !important; }}

/* "- 1 +" quantity control: one green pill */
[class*="st-key-qty_"] {{
    background: {BRAND_GREEN}; border-radius: 12px; padding: 0 .2rem;
    box-shadow: 0 4px 12px rgba(20,168,90,.25); min-height: 2.75rem;
}}
[class*="st-key-qty_"] .stButton > button[kind] {{
    background: transparent !important; border: none !important; color: #fff !important;
    font-size: 1.3rem !important; box-shadow: none !important; min-height: 2.75rem; padding: 0 .9rem !important;
}}
[class*="st-key-qty_"] .stButton > button[kind] p {{ font-size: 1.45rem; font-weight: 800; }}
[class*="st-key-qty_"] .stButton > button[kind]:disabled {{ background: transparent !important; color: rgba(255,255,255,.45) !important; }}
.qp-qty-num {{ color: #fff; font-weight: 800; font-size: 1.15rem; text-align: center; line-height: 2.75rem; }}
[class*="st-key-qty_"] [data-testid="stMarkdownContainer"] {{ margin: 0 !important; }}

/* Cart panel: sticky on the right */
[data-testid="stColumn"]:has(.st-key-card_cart) {{ position: sticky; top: 1rem; align-self: flex-start; }}
.qp-cart-item {{
    display: flex; align-items: center; gap: .7rem; padding: .6rem 0; border-bottom: 1px dashed {BRAND_BORDER};
}}
.qp-cart-emoji {{
    width: 2.6rem; height: 2.6rem; border-radius: 10px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center; font-size: 1.5rem;
}}
.qp-cart-name {{ font-weight: 600; font-size: .92rem; }}
.qp-cart-meta {{ color: {BRAND_MUTED}; font-size: .78rem; }}
.qp-cart-line {{ margin-left: auto; font-weight: 700; }}
.qp-row {{ display: flex; justify-content: space-between; padding: .3rem 0; font-size: .92rem; }}
.qp-row-muted {{ color: {BRAND_MUTED}; }}
.qp-row-free {{ color: {BRAND_GREEN_DARK}; font-weight: 700; }}
.qp-row-total {{ font-weight: 800; font-size: 1.15rem; border-top: 1.5px solid {BRAND_BORDER}; margin-top: .4rem; padding-top: .7rem; }}
.qp-cart-empty {{ text-align: center; padding: 1.6rem .5rem 1rem; color: {BRAND_MUTED}; }}
.qp-cart-empty-icon {{
    width: 4.5rem; height: 4.5rem; margin: 0 auto .6rem; border-radius: 50%;
    background: {BRAND_GREEN_LIGHT}; display: flex; align-items: center; justify-content: center; font-size: 2.1rem;
}}
.qp-powered {{ color: {BRAND_MUTED}; font-size: .72rem; text-align: center; margin-top: .3rem; }}
.st-key-pay_btn button {{ min-height: 3.4rem !important; font-size: 1.1rem !important; }}
.st-key-clear_btn button {{ border: none !important; color: {BRAND_MUTED} !important; background: transparent !important; }}

/* Payment screen */
.qp-pay {{ text-align: center; padding: 3rem 1rem; }}
.qp-pay-icon {{
    width: 6rem; height: 6rem; border-radius: 50%; margin: 0 auto 1.2rem;
    display: flex; align-items: center; justify-content: center; font-size: 3rem;
}}
.qp-pay-title {{ font-size: 1.5rem; font-weight: 800; }}
.qp-pay-sub {{ color: {BRAND_MUTED}; margin-top: .35rem; }}

/* Order tracking */
.qp-track-head {{ text-align: center; padding-top: .4rem; }}
.qp-track-label {{ color: {BRAND_MUTED}; font-size: .85rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; }}
.qp-track-id {{ font-size: 3.2rem; font-weight: 800; color: {BRAND_GREEN}; letter-spacing: -0.03em; line-height: 1.1; }}
.qp-track-meta {{ color: {BRAND_MUTED}; font-size: .9rem; margin-top: .2rem; }}
.qp-steps {{ display: flex; position: relative; margin: 2rem 0 1.4rem; }}
.qp-steps-line, .qp-steps-fill {{ position: absolute; top: 23px; left: 10%; height: 5px; border-radius: 5px; }}
.qp-steps-line {{ right: 10%; background: {BRAND_BORDER}; }}
.qp-steps-fill {{ background: {BRAND_GREEN}; transition: width .6s ease; }}
.qp-step {{ flex: 1; display: flex; flex-direction: column; align-items: center; z-index: 1; text-align: center; }}
.qp-step-icon {{
    width: 50px; height: 50px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
    font-size: 1.35rem; border: 4px solid #fff; box-shadow: 0 2px 8px rgba(16,24,20,.08);
}}
.qp-step-done .qp-step-icon {{ background: {BRAND_GREEN}; color: #fff; }}
.qp-step-active .qp-step-icon {{ background: {BRAND_YELLOW}; animation: qp-pulse 1.4s infinite; box-shadow: 0 0 0 6px rgba(255,182,39,.25); }}
.qp-step-failed .qp-step-icon {{ background: {STATUS_ERROR}; color: #fff; }}
.qp-step-todo .qp-step-icon {{ background: #EEF1EF; filter: grayscale(1); opacity: .7; }}
.qp-step-label {{ font-size: .78rem; font-weight: 600; color: {BRAND_MUTED}; margin-top: .5rem; max-width: 6.5rem; }}
.qp-step-active .qp-step-label, .qp-step-done .qp-step-label {{ color: {BRAND_TEXT}; }}
.qp-track-msg {{
    display: flex; align-items: center; justify-content: center; gap: .7rem;
    background: {BRAND_GREEN_LIGHT}; color: {BRAND_GREEN_DARK}; border-radius: 14px;
    padding: 1rem 1.2rem; font-size: 1.2rem; font-weight: 700; text-align: center;
}}
.qp-track-msg-failed {{ background: #FDE7E8; color: #B42328; }}
.qp-track-live {{ text-align: center; color: {BRAND_MUTED}; font-size: .8rem; margin-top: .7rem; }}
.qp-problem {{
    background: #FFF6F6; border: 1px solid #F9CFD1; color: #B42328; border-radius: 12px;
    padding: .7rem 1rem; margin-top: .9rem; font-size: .88rem;
}}

/* Receipt */
.qp-receipt {{
    background: #FCFDFC; border: 1.5px dashed {BRAND_BORDER}; border-radius: 14px;
    padding: 1.1rem 1.2rem; margin-top: 1.2rem;
}}
.qp-receipt-head {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: .6rem; }}
.qp-receipt-title {{ font-weight: 800; font-size: 1.05rem; }}
.qp-receipt-foot {{ color: {BRAND_MUTED}; font-size: .78rem; text-align: center; margin-top: .8rem; }}
.st-key-new_order button {{ min-height: 3.2rem !important; font-size: 1.05rem !important; }}
"""

OWNER_CSS = f"""
/* Dark header bar */
.qp-dash-header {{
    background: linear-gradient(100deg, {BRAND_DARK} 0%, #173325 100%); color: #fff;
    border-radius: 16px; padding: .95rem 1.3rem; margin-bottom: 1rem;
    display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap;
    box-shadow: 0 8px 24px rgba(15,26,20,.25);
}}
.qp-dash-title {{ display: flex; align-items: center; gap: .9rem; }}
.qp-dash-divider {{ width: 1px; height: 1.6rem; background: rgba(255,255,255,.2); }}
.qp-dash-name {{ font-weight: 600; font-size: 1.05rem; color: rgba(255,255,255,.85); }}
.qp-dash-right {{ display: flex; align-items: center; gap: 1rem; font-size: .85rem; color: rgba(255,255,255,.75); }}
.qp-dash-clock {{ font-variant-numeric: tabular-nums; font-weight: 700; color: #fff; font-size: 1rem; }}
.qp-dash-link {{
    display: inline-flex; align-items: center; gap: .4rem; padding: .3rem .7rem; border-radius: 999px;
    background: rgba(255,255,255,.08); font-weight: 600;
}}

/* KPI cards */
.qp-kpi {{
    background: #fff; border-radius: 16px; padding: 1rem 1.1rem; display: flex; align-items: center; gap: .9rem;
    box-shadow: 0 1px 2px rgba(16,24,20,.04), 0 6px 20px rgba(16,24,20,.06);
}}
.qp-kpi-icon {{
    width: 3rem; height: 3rem; border-radius: 12px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center; font-size: 1.45rem;
}}
.qp-kpi-value {{ font-size: 1.75rem; font-weight: 800; line-height: 1.1; font-variant-numeric: tabular-nums; }}
.qp-kpi-label {{ color: {BRAND_MUTED}; font-size: .8rem; font-weight: 600; }}
.qp-kpi-sub {{ color: {BRAND_MUTED}; font-size: .72rem; margin-top: .1rem; }}

/* Map */
.qp-map svg {{ width: 100%; height: auto; display: block; border-radius: 12px; }}
.qp-legend {{ display: flex; gap: 1rem; flex-wrap: wrap; color: {BRAND_MUTED}; font-size: .75rem; margin-top: .55rem; }}
.qp-legend span {{ display: inline-flex; align-items: center; gap: .35rem; }}
.qp-legend i {{ width: .7rem; height: .7rem; border-radius: 3px; display: inline-block; }}

/* Robot card */
.st-key-stop_btn button {{
    background: {STATUS_ERROR} !important; color: #fff !important; border: none !important;
    font-weight: 800 !important; letter-spacing: .05em; min-height: 3rem;
    box-shadow: 0 4px 14px rgba(229,72,77,.4) !important;
}}
.st-key-stop_btn button:hover {{ background: #C8353A !important; }}
.st-key-resume_btn button {{ min-height: 2.4rem; font-size: .85rem; }}
.qp-batt {{ display: flex; align-items: center; gap: .6rem; margin: .9rem 0 .2rem; }}
.qp-batt-bar {{ flex: 1; height: 10px; background: #EEF1EF; border-radius: 6px; overflow: hidden; }}
.qp-batt-fill {{ height: 100%; border-radius: 6px; }}
.qp-batt-num {{ font-weight: 800; font-size: .9rem; font-variant-numeric: tabular-nums; min-width: 2.8rem; text-align: right; }}
.qp-stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: .55rem; margin-top: .8rem; }}
.qp-stat {{ background: #F6F8F7; border-radius: 12px; padding: .6rem .75rem; }}
.qp-stat-label {{ color: {BRAND_MUTED}; font-size: .7rem; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; }}
.qp-stat-value {{ font-weight: 700; font-size: .95rem; margin-top: .1rem; }}
.qp-alert {{
    display: flex; align-items: center; gap: .5rem; margin-top: .55rem;
    background: #FFF6F6; border: 1px solid #F9CFD1; color: #B42328;
    border-radius: 10px; padding: .5rem .75rem; font-size: .83rem; font-weight: 600;
}}

/* Step log timeline */
.qp-lane-title {{
    display: flex; align-items: center; gap: .45rem; font-size: .75rem; font-weight: 800;
    text-transform: uppercase; letter-spacing: .06em; color: {BRAND_MUTED}; margin: .4rem 0 .5rem;
}}
.qp-tl {{ position: relative; }}
.qp-tl-item {{ display: flex; gap: .7rem; position: relative; padding-bottom: .75rem; }}
.qp-tl-item:not(:last-child)::before {{
    content: ""; position: absolute; left: 15px; top: 34px; bottom: 0; width: 2px; background: {BRAND_BORDER};
}}
.qp-tl-icon {{
    width: 32px; height: 32px; border-radius: 10px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center; font-size: .95rem;
}}
.qp-tl-body {{ flex: 1; min-width: 0; }}
.qp-tl-title {{ font-weight: 600; font-size: .88rem; display: flex; justify-content: space-between; gap: .5rem; }}
.qp-tl-sub {{ color: {BRAND_MUTED}; font-size: .78rem; margin-top: .1rem; overflow-wrap: anywhere; }}
.qp-tl-quote {{
    margin-top: .3rem; background: {BRAND_YELLOW_LIGHT}; border-left: 3px solid {BRAND_YELLOW};
    padding: .3rem .55rem; border-radius: 0 8px 8px 0; font-size: .8rem; color: #6B4600;
}}
.qp-tl-time {{ color: #A3AEA8; font-size: .72rem; font-weight: 500; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.qp-ok {{ color: {BRAND_GREEN_DARK}; font-weight: 800; }}
.qp-bad {{ color: {STATUS_ERROR}; font-weight: 800; }}
.qp-empty {{ text-align: center; color: {BRAND_MUTED}; padding: 1.6rem .5rem; font-size: .9rem; }}
.qp-empty div:first-child {{ font-size: 2rem; margin-bottom: .3rem; }}

/* Camera */
.qp-cam {{
    position: relative; background: #101914; border-radius: 12px; aspect-ratio: 4/3; overflow: hidden;
    display: flex; align-items: center; justify-content: center; color: #6B7A72; font-size: .85rem;
}}
.qp-cam img {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }}
.qp-cam-tag {{
    position: absolute; left: .6rem; top: .6rem; z-index: 2; background: rgba(229,72,77,.9); color: #fff;
    font-size: .68rem; font-weight: 800; padding: .15rem .45rem; border-radius: 6px; letter-spacing: .06em;
}}

/* Orders table */
.qp-table {{ width: 100%; border-collapse: collapse; font-size: .88rem; border: none !important; display: table !important; }}
.qp-table th, .qp-table td, .qp-table tr {{ border-left: none !important; border-right: none !important; border-top: none !important; background: transparent !important; }}
.qp-table th {{
    text-align: left; color: {BRAND_MUTED}; font-size: .72rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: .05em; padding: .5rem .5rem; border-bottom: 1.5px solid {BRAND_BORDER};
}}
.qp-table td {{ padding: .65rem .5rem; border-bottom: 1px solid #F0F3F1; vertical-align: middle; }}
.qp-table tr:last-child td {{ border-bottom: none; }}
.qp-table .num {{ text-align: right; font-weight: 700; font-variant-numeric: tabular-nums; }}
.qp-table .id {{ font-weight: 800; }}
.qp-table .muted {{ color: {BRAND_MUTED}; font-variant-numeric: tabular-nums; }}

/* Stock cards */
[class*="st-key-stock_"] {{
    background: #fff; border-radius: 14px; padding: .75rem .9rem;
    box-shadow: 0 1px 2px rgba(16,24,20,.04), 0 6px 20px rgba(16,24,20,.06);
}}
[class*="st-key-stock_"]:has(.qp-stock-low) {{ box-shadow: inset 0 0 0 2px #FFD08A, 0 6px 20px rgba(16,24,20,.06); }}
.qp-stock {{ display: flex; align-items: center; gap: .75rem; }}
.qp-stock-emoji {{
    width: 2.8rem; height: 2.8rem; border-radius: 12px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center; font-size: 1.6rem;
}}
.qp-stock-name {{ font-weight: 700; font-size: .92rem; }}
.qp-stock-meta {{ color: {BRAND_MUTED}; font-size: .75rem; }}
.qp-stock-num {{ margin-left: auto; font-size: 1.5rem; font-weight: 800; font-variant-numeric: tabular-nums; }}
.qp-stock-bar {{ height: 6px; background: #EEF1EF; border-radius: 4px; overflow: hidden; margin-top: .55rem; }}
.qp-stock-bar div {{ height: 100%; border-radius: 4px; }}
[class*="st-key-restock_"] button {{ min-height: 2.2rem; font-size: .82rem; }}

/* AI summary */
.st-key-ai_text {{
    background: {BRAND_GREEN_LIGHT}; border-radius: 12px; padding: .9rem 1.1rem; margin-top: .4rem;
}}
"""

HOME_CSS = f"""
.block-container, [data-testid="stMainBlockContainer"] {{ max-width: 860px !important; padding-top: 3rem !important; }}
.qp-hero {{ text-align: center; margin-bottom: 2.2rem; }}
.qp-hero-badge {{
    display: inline-flex; align-items: center; gap: .45rem; background: #fff; border: 1px solid {BRAND_BORDER};
    padding: .4rem .9rem; border-radius: 999px; font-size: .8rem; font-weight: 700; color: {BRAND_MUTED};
    box-shadow: 0 2px 8px rgba(16,24,20,.05); margin-bottom: 1.3rem;
}}
.qp-hero-line {{ color: {BRAND_MUTED}; font-size: 1.1rem; margin-top: .8rem; line-height: 1.55; }}
[class*="st-key-home_"] {{
    background: #fff; border-radius: 20px; padding: 1.6rem 1.5rem 1.4rem;
    box-shadow: 0 1px 2px rgba(16,24,20,.04), 0 10px 30px rgba(16,24,20,.08);
    transition: transform .15s ease, box-shadow .15s ease;
}}
[class*="st-key-home_"]:hover {{ transform: translateY(-3px); box-shadow: 0 16px 40px rgba(16,24,20,.12); }}
.qp-home-icon {{
    width: 4.2rem; height: 4.2rem; border-radius: 16px; display: flex; align-items: center; justify-content: center;
    font-size: 2.2rem; margin-bottom: 1rem;
}}
.qp-home-title {{ font-size: 1.3rem; font-weight: 800; }}
.qp-home-desc {{ color: {BRAND_MUTED}; font-size: .92rem; margin: .3rem 0 1.1rem; line-height: 1.45; min-height: 2.7rem; }}
.qp-home-foot {{ text-align: center; color: {BRAND_MUTED}; font-size: .8rem; margin-top: 2.5rem; }}
"""


# ============ Shared pieces ============

def demo_badge_html(demo):
    """Small badge shown when the app uses the simulated robot."""
    return '<span class="qp-demo-badge">🧪 Demo mode: simulated robot</span>' if demo else ""


def status_pill(text, color, dot=False):
    """Small rounded label. color: green, yellow, blue, red, grey."""
    dot_html = '<span class="qp-pill-dot"></span>' if dot else ""
    return f'<span class="qp-pill qp-pill-{color}">{dot_html}{esc(text)}</span>'


ORDER_STATUS_PILLS = {
    "queued": ("Queued", "yellow"),
    "packing": ("Packing", "blue"),
    "done": ("Done", "green"),
    "failed": ("Failed", "red"),
}


def order_status_pill(status):
    label, color = ORDER_STATUS_PILLS.get(status, (status, "grey"))
    return status_pill(label, color, dot=True)


def format_time(iso_time):
    """'2026-10-09T14:02:11.123' -> '14:02:11'."""
    try:
        return datetime.fromisoformat(iso_time).strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return "-"


def receipt_html(bill_items, total, title="Receipt", note=""):
    """Receipt card with item lines and total."""
    rows = ""
    for line in bill_items:
        item = line["item"]
        qty = line["qty"]
        rows += f"""
        <div class="qp-row">
            <span>{product_emoji(item)} {esc(product_name(item))} <span class="qp-row-muted">× {qty}</span></span>
            <span>Rs {line['price'] * qty}</span>
        </div>
        """
    return f"""
    <div class="qp-receipt">
        <div class="qp-receipt-head">
            <span class="qp-receipt-title">🧾 {esc(title)}</span>
            {status_pill('Paid', 'green')}
        </div>
        {rows}
        <div class="qp-row qp-row-free"><span>Robot delivery</span><span>FREE</span></div>
        <div class="qp-row qp-row-total"><span>Total</span><span>Rs {total}</span></div>
        <div class="qp-receipt-foot">{esc(note)}</div>
    </div>
    """


# ============ Customer page pieces ============

def topbar_html(cart_count, cart_total, show_cart=True, demo=False):
    """Top bar: logo, robot badge, cart button with count."""
    items_word = "item" if cart_count == 1 else "items"
    cart_text = f"<small>{cart_count} {items_word}</small>Rs {cart_total}" if cart_count else "<small>Your</small>Cart"
    cart_html = f"""
    <div class="qp-cart-btn">
        <span style="font-size:1.25rem;">🛒</span>
        <span>{cart_text}</span>
        <span class="qp-cart-count">{cart_count}</span>
    </div>
    """
    return f"""
    <div class="qp-topbar">
        <div class="qp-topbar-left">
            {logo_html('1.7rem')}
            <span class="qp-badge">🤖 Delivered by robot in minutes</span>
            {demo_badge_html(demo)}
        </div>
        {cart_html if show_cart else ""}
    </div>
    """


def product_card_html(item, price, stock):
    """Top part of a product card (the buttons are added by the page)."""
    info = PRODUCT_INFO.get(item, {})
    if stock == 0:
        flag = '<span class="qp-prod-flag qp-prod-flag-out">Out of stock</span>'
        stock_line = '<div class="qp-prod-out">Out of stock</div>'
    elif stock <= LOW_STOCK:
        flag = f'<span class="qp-prod-flag qp-prod-flag-low">Only {stock} left</span>'
        stock_line = f'<div class="qp-prod-low">Only {stock} left</div>'
    else:
        flag = ""
        stock_line = '<div class="qp-prod-size">&nbsp;</div>'
    out_class = "qp-out" if stock == 0 else ""
    return f"""
    <div class="{out_class}">
        <div class="qp-prod-tile" style="background:{info.get('bg', '#EEF1EF')};">
            {flag}{product_emoji(item)}
        </div>
        <span class="qp-prod-eta">⏱ ROBOT · 3 MIN</span>
        <div class="qp-prod-name">{esc(product_name(item))}</div>
        <div class="qp-prod-size">{esc(info.get('size', ''))}</div>
        <div class="qp-prod-price">Rs {price}</div>
        {stock_line}
    </div>
    """


def cart_item_html(item, qty, price):
    bg = PRODUCT_INFO.get(item, {}).get("bg", "#EEF1EF")
    return f"""
    <div class="qp-cart-item">
        <div class="qp-cart-emoji" style="background:{bg};">{product_emoji(item)}</div>
        <div>
            <div class="qp-cart-name">{esc(product_name(item))}</div>
            <div class="qp-cart-meta">{qty} × Rs {price}</div>
        </div>
        <div class="qp-cart-line">Rs {qty * price}</div>
    </div>
    """


TRACK_STEPS = [
    ("Order placed", "🧾"),
    ("Robot at shelf", "🗄️"),
    ("Picking items", "🦾"),
    ("Coming to you", "🤖"),
    ("Delivered", "🎉"),
]


def progress_bar_html(current_step, failed=False):
    """Delivery-app progress bar.

    current_step: 0..4 = that step is happening now, 5 = all steps done.
    failed: show the current step in red.
    """
    last = len(TRACK_STEPS) - 1
    fill = min(current_step, last) / last * 80  # dots sit between 10% and 90%
    steps = ""
    for i, (label, icon) in enumerate(TRACK_STEPS):
        if i < current_step:
            css, shown = "qp-step-done", "✓"
        elif i == current_step:
            css, shown = ("qp-step-failed", "✕") if failed else ("qp-step-active", icon)
        else:
            css, shown = "qp-step-todo", icon
        steps += f"""
        <div class="qp-step {css}">
            <div class="qp-step-icon">{shown}</div>
            <div class="qp-step-label">{label}</div>
        </div>
        """
    return f"""
    <div class="qp-steps">
        <div class="qp-steps-line"></div>
        <div class="qp-steps-fill" style="width:{fill}%;"></div>
        {steps}
    </div>
    """


# ============ Owner page pieces ============

def owner_header_html(robot_online, demo=False):
    now = datetime.now().strftime("%H:%M:%S")
    link = status_pill("Robot online", "green", dot=True) if robot_online else status_pill("Robot offline", "red", dot=True)
    return f"""
    <div class="qp-dash-header">
        <div class="qp-dash-title">
            {logo_html('1.45rem', dark=True)}
            <span class="qp-dash-divider"></span>
            <span class="qp-dash-name">Store Control</span>
        </div>
        <div class="qp-dash-right">
            {demo_badge_html(demo)}
            <span class="qp-dash-link"><span class="qp-live" style="color:{STATUS_ERROR};">●</span> LIVE</span>
            {link}
            <span class="qp-dash-clock">{now}</span>
        </div>
    </div>
    """


def kpi_card_html(icon, value, label, sub="", tint=BRAND_GREEN_LIGHT):
    return f"""
    <div class="qp-kpi">
        <div class="qp-kpi-icon" style="background:{tint};">{icon}</div>
        <div>
            <div class="qp-kpi-label">{esc(label)}</div>
            <div class="qp-kpi-value">{esc(value)}</div>
            <div class="qp-kpi-sub">{esc(sub)}</div>
        </div>
    </div>
    """


ROBOT_COLORS = {"idle": BRAND_GREEN, "busy": BRAND_YELLOW, "offline": "#9AA6A0", "stopped": STATUS_ERROR}


def store_map_svg(stations, pose, robot_state="idle", route_shelves=(), shelf_items=None):
    """2D store map: shelves as boxes, home and customer points, robot with a soft glow.

    stations: {"home": {"x", "y"}, ...} in meters (from /map).
    pose: robot {"x", "y", "theta"}.
    robot_state: idle / busy / offline / stopped (sets the robot color).
    route_shelves: shelves the current order needs (drawn with a yellow border).
    shelf_items: {"shelf_A": ["🥫"], ...} product icons on each shelf.
    """
    import math

    shelf_items = shelf_items or {}
    width, height, pad = 560, 330, 50
    xs = [p["x"] for p in stations.values()] + [pose.get("x", 0)]
    ys = [p["y"] for p in stations.values()] + [pose.get("y", 0)]
    min_x, max_x = min(xs), max(xs) + 0.9  # room on the right for shelf boxes
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 0.1)
    span_y = max(max_y - min_y, 0.1)

    def sx(x):
        return pad + (x - min_x) / span_x * (width - 2 * pad)

    def sy(y):
        return height - pad - (y - min_y) / span_y * (height - 2 * pad)

    parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" font-family="Inter, sans-serif">',
        '<defs><pattern id="qpgrid" width="28" height="28" patternUnits="userSpaceOnUse">'
        '<path d="M 28 0 L 0 0 0 28" fill="none" stroke="#E3E8E5" stroke-width="1"/></pattern>'
        f'<radialGradient id="qpglow"><stop offset="0%" stop-color="{ROBOT_COLORS.get(robot_state, BRAND_GREEN)}" stop-opacity=".55"/>'
        f'<stop offset="100%" stop-color="{ROBOT_COLORS.get(robot_state, BRAND_GREEN)}" stop-opacity="0"/></radialGradient></defs>',
        f'<rect width="{width}" height="{height}" fill="#F7F9F8"/>',
        f'<rect width="{width}" height="{height}" fill="url(#qpgrid)"/>',
    ]

    # Planned route: home -> shelves -> customer -> home (dashed line)
    route = ["home"] + [s for s in route_shelves if s in stations] + ["customer", "home"]
    points = [f"{sx(stations[s]['x']):.0f},{sy(stations[s]['y']):.0f}" for s in route if s in stations]
    if route_shelves and len(points) > 2:
        parts.append(
            f'<polyline points="{" ".join(points)}" fill="none" stroke="{BRAND_YELLOW}" '
            'stroke-width="3" stroke-dasharray="7 7" stroke-linecap="round" stroke-linejoin="round" opacity=".9"/>'
        )

    for name, pos in stations.items():
        x, y = sx(pos["x"]), sy(pos["y"])
        if name.startswith("shelf"):
            in_route = name in route_shelves
            stroke = BRAND_YELLOW if in_route else "#C9D3CE"
            fill = "#FFF8E6" if in_route else "#FFFFFF"
            parts.append(f'<rect x="{x + 22:.0f}" y="{y - 22:.0f}" width="96" height="44" rx="10" '
                         f'fill="{fill}" stroke="{stroke}" stroke-width="2.5"/>')
            parts.append(f'<text x="{x + 34:.0f}" y="{y - 4:.0f}" font-size="12" font-weight="800" fill="{BRAND_TEXT}">'
                         f'{esc(station_name(name).upper())}</text>')
            icons = " ".join(shelf_items.get(name, [])) or "📦"
            parts.append(f'<text x="{x + 34:.0f}" y="{y + 14:.0f}" font-size="13">{icons}</text>')
            parts.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="5" fill="#C9D3CE"/>')
        elif name == "home":
            parts.append(f'<rect x="{x - 30:.0f}" y="{y - 20:.0f}" width="60" height="40" rx="10" '
                         f'fill="{BRAND_GREEN_LIGHT}" stroke="{BRAND_GREEN}" stroke-width="2" stroke-dasharray="4 3"/>')
            parts.append(f'<text x="{x:.0f}" y="{y + 36:.0f}" text-anchor="middle" font-size="11" font-weight="800" '
                         f'fill="{BRAND_GREEN_DARK}">⌂ HOME</text>')
        elif name == "customer":
            parts.append(f'<rect x="{x - 30:.0f}" y="{y - 20:.0f}" width="60" height="40" rx="10" '
                         f'fill="{BRAND_YELLOW_LIGHT}" stroke="{BRAND_YELLOW}" stroke-width="2"/>')
            parts.append(f'<text x="{x:.0f}" y="{y - 28:.0f}" text-anchor="middle" font-size="11" font-weight="800" '
                         f'fill="#8A5A00">🧍 CUSTOMER</text>')
        else:
            parts.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="6" fill="#C9D3CE"/>')
            parts.append(f'<text x="{x:.0f}" y="{y + 20:.0f}" text-anchor="middle" font-size="10" fill="{BRAND_MUTED}">'
                         f'{esc(station_name(name))}</text>')

    # Robot: soft glow + dot + heading line
    rx, ry = sx(pose.get("x", 0)), sy(pose.get("y", 0))
    theta = pose.get("theta", 0) or 0
    hx, hy = rx + 22 * math.cos(theta), ry - 22 * math.sin(theta)
    color = ROBOT_COLORS.get(robot_state, BRAND_GREEN)
    parts.append(f'<circle cx="{rx:.0f}" cy="{ry:.0f}" r="34" fill="url(#qpglow)">'
                 '<animate attributeName="r" values="26;38;26" dur="2s" repeatCount="indefinite"/></circle>')
    parts.append(f'<line x1="{rx:.0f}" y1="{ry:.0f}" x2="{hx:.0f}" y2="{hy:.0f}" stroke="{color}" '
                 'stroke-width="4" stroke-linecap="round"/>')
    parts.append(f'<circle cx="{rx:.0f}" cy="{ry:.0f}" r="14" fill="{color}" stroke="#fff" stroke-width="4"/>')
    parts.append(f'<text x="{rx:.0f}" y="{ry + 5:.0f}" text-anchor="middle" font-size="13">🤖</text>')
    parts.append("</svg>")
    return "".join(parts)


# Robot action icons and tile colors in the step log
TOOL_ICONS = {
    "navigate_to": ("🧭", "#E4EEFE"),
    "pick_item": ("🦾", BRAND_YELLOW_LIGHT),
    "scan_item": ("🔎", "#EEE8FD"),
    "place_item": ("🧺", BRAND_GREEN_LIGHT),
}


def timeline_html(entries):
    """entries: list of dicts with icon, tint, title, sub, time, ok (True/False/None), quote."""
    rows = ""
    for e in entries:
        if e.get("ok") is True:
            mark = '<span class="qp-ok">✓</span>'
        elif e.get("ok") is False:
            mark = '<span class="qp-bad">✕</span>'
        else:
            mark = ""
        quote = f'<div class="qp-tl-quote">“{esc(e["quote"])}”</div>' if e.get("quote") else ""
        sub = f'<div class="qp-tl-sub">{esc(e["sub"])}</div>' if e.get("sub") else ""
        rows += f"""
        <div class="qp-tl-item">
            <div class="qp-tl-icon" style="background:{e.get('tint', '#EEF1EF')};">{e.get('icon', '•')}</div>
            <div class="qp-tl-body">
                <div class="qp-tl-title"><span>{esc(e['title'])} {mark}</span>
                <span class="qp-tl-time">{esc(e.get('time', ''))}</span></div>
                {sub}{quote}
            </div>
        </div>
        """
    return f'<div class="qp-tl">{rows}</div>'


def empty_html(icon, text):
    return f'<div class="qp-empty"><div>{icon}</div><div>{esc(text)}</div></div>'
