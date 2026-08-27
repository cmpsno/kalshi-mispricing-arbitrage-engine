"""Read-only queries used by the Streamlit dashboard."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

OPPORTUNITY_COLUMNS = [
    "id",
    "detected_at",
    "algorithm",
    "markets_involved",
    "action",
    "gross_profit_cents",
    "required_collateral_cents",
    "confidence_score",
    "executed",
]


class DashboardDatabaseError(RuntimeError):
    """Raised when the dashboard database cannot be read yet."""


def _connect(database_path: str | Path) -> sqlite3.Connection:
    path = Path(database_path).expanduser().resolve()
    if not path.is_file():
        raise DashboardDatabaseError(
            f"Database not found at {path}. Run the engine once to create it."
        )
    try:
        connection = sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5
        )
        connection.execute("PRAGMA query_only=ON")
        return connection
    except sqlite3.Error as exc:
        raise DashboardDatabaseError(f"Could not open database at {path}: {exc}") from exc


def load_opportunities(
    database_path: str | Path,
    *,
    limit: int = 1_000,
    hours: int = 24,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Return recent opportunities ordered newest first."""

    if limit < 1:
        raise ValueError("limit must be at least 1")
    if hours < 1:
        raise ValueError("hours must be at least 1")

    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    cutoff_ms = int((current_time - timedelta(hours=hours)).timestamp() * 1_000)

    query = """
        SELECT id, detected_at, algorithm, markets_involved, action,
               gross_profit_cents, required_collateral_cents,
               confidence_score, executed
        FROM opportunities
        WHERE detected_at >= ?
        ORDER BY detected_at DESC
        LIMIT ?
    """
    try:
        with _connect(database_path) as connection:
            frame = pd.read_sql_query(query, connection, params=(cutoff_ms, limit))
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise DashboardDatabaseError(
            "The opportunities table is unavailable. Run the engine once to initialize it."
        ) from exc

    if frame.empty:
        return pd.DataFrame(columns=OPPORTUNITY_COLUMNS + ["roi_percent"])

    frame["detected_at"] = pd.to_datetime(
        frame["detected_at"], unit="ms", utc=True
    )
    collateral = frame["required_collateral_cents"].replace(0, pd.NA)
    frame["roi_percent"] = frame["gross_profit_cents"] / collateral * 100
    frame["executed"] = frame["executed"].astype(bool)
    return frame


def summarize_opportunities(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate counts and profit statistics by detector algorithm."""

    columns = [
        "algorithm",
        "count",
        "avg_profit_cents",
        "max_profit_cents",
        "avg_roi_percent",
    ]
    if frame.empty:
        return pd.DataFrame(columns=columns)

    return (
        frame.groupby("algorithm", as_index=False)
        .agg(
            count=("id", "count"),
            avg_profit_cents=("gross_profit_cents", "mean"),
            max_profit_cents=("gross_profit_cents", "max"),
            avg_roi_percent=("roi_percent", "mean"),
        )
        .sort_values("count", ascending=False)
        .reset_index(drop=True)
    )
