"""Streamlit dashboard for the Kalshi mispricing arbitrage engine.

Styled after the Kalshi brand kit (kalshi.com/brandkit): brand green #28cc95,
solid colors, bordered cards, no gradients. Kalshi Sans is proprietary, so the
system sans stack is used as the closest available match.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from src.dashboard_data import (
    DashboardDatabaseError,
    load_opportunities,
    summarize_opportunities,
)

# ----------------------------------------------------------------------------
# Brand tokens (Kalshi brand kit)
# ----------------------------------------------------------------------------
KALSHI_GREEN = "#28cc95"       # Lightmode Green — primary accent on white
KALSHI_DARK_GREEN = "#0E7A5F"  # Dark Green — secondary accent / text on tints
KALSHI_INK = "#111214"         # near-black text
KALSHI_BG = "#FFFFFF"
KALSHI_SURFACE = "#F4F6F5"     # light gray
KALSHI_BORDER = "#E3E6E4"
KALSHI_TINT = "#E9F9F2"        # green tint for info banners

ALGORITHM_LABELS = {
    "same_market_cross": "Same-market cross",
    "mutually_exclusive_sum": "Mutually-exclusive sum",
    "strike_monotonicity": "Strike monotonicity",
}

ACTION_LABELS = {
    "buy_yes_buy_no": "Buy YES + Buy NO",
    "sell_yes_sell_no": "Sell YES + Sell NO",
    "buy_all_outcomes": "Buy all outcomes",
    "sell_all_outcomes": "Sell all outcomes",
    "buy_spread": "Buy spread",
}

PLOTLY_GREENS = ["#28cc95", "#0E7A5F", "#5ED3A6", "#8A8F98"]


def dollars(cents: float) -> str:
    return f"${cents / 100:,.2f}"


def label_algorithm(name: str) -> str:
    return ALGORITHM_LABELS.get(name, name.replace("_", " ").title())


def label_action(name: str) -> str:
    return ACTION_LABELS.get(name, name.replace("_", " ").title())


st.set_page_config(
    page_title="Kalshi Arbitrage Opportunities",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ----------------------------------------------------------------------------
# Kalshi-style chrome: hide Streamlit decorations, bordered cards, 4px grid
# ----------------------------------------------------------------------------
st.markdown(
    f"""
    <style>
      #MainMenu {{ visibility: hidden; }}
      footer {{ visibility: hidden; }}
      [data-testid="stToolbar"] {{ visibility: hidden; }}
      [data-testid="stDecoration"] {{ visibility: hidden; }}
      .block-container {{ padding-top: 2rem; }}

      /* Metric cards: border, no shadow, 16px radius */
      [data-testid="stMetric"] {{
        background: {KALSHI_BG};
        border: 1px solid {KALSHI_BORDER};
        border-radius: 16px;
        padding: 16px;
      }}
      [data-testid="stMetricLabel"] {{ color: #5A5E5B; font-size: 0.8rem; }}
      [data-testid="stMetricValue"] {{ color: {KALSHI_INK}; }}

      /* Sample-data banner */
      .sample-banner {{
        background: {KALSHI_TINT};
        border: 1px solid {KALSHI_GREEN};
        border-radius: 12px;
        padding: 12px 16px;
        color: {KALSHI_DARK_GREEN};
        font-size: 0.9rem;
        margin: 0 0 1rem 0;
      }}
      .page-subtitle {{ color: #5A5E5B; font-size: 1.05rem; margin-top: -0.5rem; }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown("# Arbitrage Opportunities")
st.markdown(
    '<p class="page-subtitle">Structural price inconsistencies the engine '
    "detected across Kalshi markets — each row is a trade the detectors "
    "believe locks in a profit.</p>",
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="sample-banner"><b>Demo dataset.</b> These are generated sample '
    "opportunities for illustration — point <code>KALSHI_DATABASE_PATH</code> at "
    "a real engine database for live data.</div>",
    unsafe_allow_html=True,
)

with st.expander("How to read this dashboard"):
    st.markdown(
        """
        - **Opportunities** — how many mispricings the detectors found in the
          selected time window.
        - **Potential gross profit** — total profit if every opportunity were
          executed at the quoted prices, before fees.
        - **Best opportunity** — the single largest edge found.
        - **Marked executed** — opportunities already flagged as traded.
        - **Detector** — which structural check found it: *same-market cross*
          (YES + NO priced under $1.00), *mutually-exclusive sum* (all outcomes
          of one event priced under/over $1.00), or *strike monotonicity*
          (a higher strike quoted above a lower one).
        - **ROI** — profit divided by the collateral the trade would tie up.
        - **Confidence** — scales with the size of the edge (10¢+ edge = 100%).
        """
    )

# ----------------------------------------------------------------------------
# Sidebar controls
# ----------------------------------------------------------------------------
st.sidebar.title("Controls")
refresh_interval = st.sidebar.slider("Auto-refresh interval (seconds)", 2, 60, 10, step=2)
hours_back = st.sidebar.slider("Show data from last (hours)", 1, 168, 24)
limit_rows = st.sidebar.number_input(
    "Maximum rows", min_value=100, max_value=10_000, value=1_000, step=100
)

database_path = Path(os.getenv("KALSHI_DATABASE_PATH", "kalshi_arbitrage.db")).expanduser()
st.sidebar.divider()
st.sidebar.caption(f"Database: {database_path}")
st.sidebar.info(
    "Run the engine separately. This dashboard reads its SQLite database "
    "without modifying it."
)


@st.fragment(run_every=refresh_interval)
def render_dashboard() -> None:
    """Render data that should update on the selected interval."""
    if st.sidebar.button("Refresh now", width="stretch"):
        st.rerun(scope="fragment")

    try:
        opportunities = load_opportunities(
            database_path, limit=int(limit_rows), hours=hours_back
        )
    except DashboardDatabaseError as exc:
        st.warning(str(exc), icon="⚠️")
        return

    summary = summarize_opportunities(opportunities)
    total_profit = (
        int(opportunities["gross_profit_cents"].sum()) if not opportunities.empty else 0
    )
    best_profit = (
        int(opportunities["gross_profit_cents"].max()) if not opportunities.empty else 0
    )
    executed = int(opportunities["executed"].sum()) if not opportunities.empty else 0

    metric_columns = st.columns(4)
    metric_columns[0].metric("Opportunities", f"{len(opportunities):,}")
    metric_columns[1].metric("Potential gross profit", dollars(total_profit))
    metric_columns[2].metric("Best opportunity", dollars(best_profit))
    metric_columns[3].metric("Marked executed", f"{executed:,}")
    st.caption("Profits shown in USD.")

    if opportunities.empty:
        st.info("No opportunities were detected in the selected time range.")
        return

    view = opportunities.copy()
    view["detector"] = view["algorithm"].map(label_algorithm)
    view["action_label"] = view["action"].map(label_action)

    profit_chart = px.scatter(
        view,
        x="detected_at",
        y="gross_profit_cents",
        color="detector",
        size="required_collateral_cents",
        title="Potential gross profit over time",
        labels={
            "detected_at": "Detected at (UTC)",
            "gross_profit_cents": "Gross profit (cents)",
            "detector": "Detector",
            "required_collateral_cents": "Collateral (cents)",
        },
        hover_data=["markets_involved", "action_label", "roi_percent"],
        color_discrete_sequence=PLOTLY_GREENS,
        template="plotly_white",
    )
    profit_chart.update_layout(font_family="system-ui, sans-serif")
    st.plotly_chart(profit_chart, width="stretch")

    hourly = view.assign(hour=view["detected_at"].dt.floor("h"))
    hourly = hourly.groupby(["hour", "detector"]).size().reset_index(name="count")
    count_chart = px.bar(
        hourly,
        x="hour",
        y="count",
        color="detector",
        barmode="group",
        title="Opportunities per hour",
        labels={"hour": "Hour (UTC)", "count": "Opportunities", "detector": "Detector"},
        color_discrete_sequence=PLOTLY_GREENS,
        template="plotly_white",
    )
    count_chart.update_layout(font_family="system-ui, sans-serif")
    st.plotly_chart(count_chart, width="stretch")

    st.subheader("Detector summary")
    formatted_summary = summary.copy()
    formatted_summary["algorithm"] = formatted_summary["algorithm"].map(label_algorithm)
    formatted_summary["avg_profit_cents"] = (
        formatted_summary["avg_profit_cents"].round(2).map(dollars)
    )
    formatted_summary["max_profit_cents"] = (
        formatted_summary["max_profit_cents"].map(dollars)
    )
    formatted_summary["avg_roi_percent"] = formatted_summary["avg_roi_percent"].round(2)
    formatted_summary = formatted_summary.rename(
        columns={
            "algorithm": "Detector",
            "count": "Opportunities",
            "avg_profit_cents": "Avg profit",
            "max_profit_cents": "Best profit",
            "avg_roi_percent": "Avg ROI (%)",
        }
    )
    st.dataframe(formatted_summary, width="stretch", hide_index=True)

    st.subheader(f"Latest {len(view):,} opportunities")
    table = view[
        [
            "detected_at",
            "detector",
            "markets_involved",
            "action_label",
            "gross_profit_cents",
            "required_collateral_cents",
            "roi_percent",
            "confidence_score",
            "executed",
        ]
    ].copy()
    table["gross_profit_cents"] = table["gross_profit_cents"].map(dollars)
    table["required_collateral_cents"] = table["required_collateral_cents"].map(dollars)
    table["roi_percent"] = table["roi_percent"].round(2)
    table["confidence_score"] = table["confidence_score"].map(lambda c: f"{c:.0%}")
    table["executed"] = table["executed"].map(lambda e: "Yes" if e else "—")
    table = table.rename(
        columns={
            "detected_at": "Detected (UTC)",
            "detector": "Detector",
            "markets_involved": "Markets",
            "action_label": "Action",
            "gross_profit_cents": "Profit",
            "required_collateral_cents": "Collateral",
            "roi_percent": "ROI (%)",
            "confidence_score": "Confidence",
            "executed": "Executed",
        }
    )
    st.dataframe(table, width="stretch", hide_index=True, height=420)


render_dashboard()

st.sidebar.divider()
st.sidebar.caption(
    f"Auto-refreshing every {refresh_interval} seconds · Built with Streamlit"
)
