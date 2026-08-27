"""Streamlit dashboard for the Kalshi mispricing arbitrage engine."""

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

st.set_page_config(
    page_title="Kalshi Arbitrage Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📊 Kalshi Arbitrage Opportunities")

st.sidebar.title("⚙️ Controls")
refresh_interval = st.sidebar.slider(
    "Auto-refresh interval (seconds)", 2, 60, 10, step=2
)
hours_back = st.sidebar.slider("Show data from last (hours)", 1, 168, 24)
limit_rows = st.sidebar.number_input(
    "Maximum rows", min_value=100, max_value=10_000, value=1_000, step=100
)
database_path = Path(
    os.getenv("KALSHI_DATABASE_PATH", "kalshi_arbitrage.db")
).expanduser()

st.sidebar.divider()
st.sidebar.caption(f"Database: {database_path}")
st.sidebar.info(
    "Run the engine separately. This dashboard reads its SQLite database without "
    "modifying it."
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
        int(opportunities["gross_profit_cents"].sum())
        if not opportunities.empty
        else 0
    )
    best_profit = (
        int(opportunities["gross_profit_cents"].max())
        if not opportunities.empty
        else 0
    )
    executed = int(opportunities["executed"].sum()) if not opportunities.empty else 0

    metric_columns = st.columns(4)
    metric_columns[0].metric("Opportunities", f"{len(opportunities):,}")
    metric_columns[1].metric("Potential gross profit", f"{total_profit:,}¢")
    metric_columns[2].metric("Best opportunity", f"{best_profit:,}¢")
    metric_columns[3].metric("Marked executed", f"{executed:,}")

    if opportunities.empty:
        st.info("No opportunities were detected in the selected time range.")
        return

    profit_chart = px.scatter(
        opportunities,
        x="detected_at",
        y="gross_profit_cents",
        color="algorithm",
        size="required_collateral_cents",
        title="Potential Gross Profit Over Time",
        labels={
            "detected_at": "Detected at (UTC)",
            "gross_profit_cents": "Gross profit (cents)",
            "algorithm": "Detector",
            "required_collateral_cents": "Collateral (cents)",
        },
        hover_data=["markets_involved", "action", "roi_percent"],
    )
    st.plotly_chart(profit_chart, width="stretch")

    hourly = opportunities.assign(
        hour=opportunities["detected_at"].dt.floor("h")
    )
    hourly = hourly.groupby(["hour", "algorithm"]).size().reset_index(name="count")
    count_chart = px.bar(
        hourly,
        x="hour",
        y="count",
        color="algorithm",
        barmode="group",
        title="Opportunities per Hour",
        labels={
            "hour": "Hour (UTC)",
            "count": "Opportunities",
            "algorithm": "Detector",
        },
    )
    st.plotly_chart(count_chart, width="stretch")

    st.subheader("Detector summary")
    formatted_summary = summary.copy()
    formatted_summary["avg_profit_cents"] = formatted_summary["avg_profit_cents"].round(2)
    formatted_summary["avg_roi_percent"] = formatted_summary["avg_roi_percent"].round(2)
    st.dataframe(formatted_summary, width="stretch", hide_index=True)

    st.subheader(f"Latest {len(opportunities):,} opportunities")
    table = opportunities[
        [
            "detected_at",
            "algorithm",
            "markets_involved",
            "action",
            "gross_profit_cents",
            "required_collateral_cents",
            "roi_percent",
            "confidence_score",
            "executed",
        ]
    ].copy()
    table["roi_percent"] = table["roi_percent"].round(2)
    st.dataframe(table, width="stretch", hide_index=True, height=420)


render_dashboard()

st.sidebar.divider()
st.sidebar.caption(
    f"Auto-refreshing every {refresh_interval} seconds • Built with Streamlit"
)
