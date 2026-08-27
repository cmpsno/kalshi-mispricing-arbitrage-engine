from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.dashboard_data import (
    DashboardDatabaseError,
    load_opportunities,
    summarize_opportunities,
)
from src.storage import SCHEMA


def _database_with_opportunities(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.executemany(
            """
            INSERT INTO opportunities (
                id, detected_at, algorithm, markets_involved, action,
                gross_profit_cents, required_collateral_cents,
                confidence_score, details_json, executed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', ?)
            """,
            [
                (
                    "new",
                    1_787_788_800_000,
                    "same_market_cross",
                    "MARKET-A",
                    "buy_yes_buy_no",
                    10,
                    100,
                    0.9,
                    1,
                ),
                (
                    "old",
                    1_787_698_800_000,
                    "strike_monotonicity",
                    "MARKET-B,MARKET-C",
                    "buy_spread",
                    5,
                    50,
                    0.7,
                    0,
                ),
            ],
        )


def test_load_opportunities_filters_and_converts_values(tmp_path: Path) -> None:
    database_path = tmp_path / "engine.db"
    _database_with_opportunities(database_path)

    frame = load_opportunities(
        database_path,
        hours=24,
        now=datetime(2026, 8, 27, 1, 0, tzinfo=UTC),
    )

    assert frame["id"].tolist() == ["new"]
    assert str(frame.loc[0, "detected_at"].tzinfo) == "UTC"
    assert frame.loc[0, "roi_percent"] == 10
    assert bool(frame.loc[0, "executed"]) is True


def test_summarize_opportunities_groups_by_algorithm(tmp_path: Path) -> None:
    database_path = tmp_path / "engine.db"
    _database_with_opportunities(database_path)
    frame = load_opportunities(
        database_path,
        hours=72,
        now=datetime(2026, 8, 27, 1, 0, tzinfo=UTC),
    )

    summary = summarize_opportunities(frame)

    assert set(summary["algorithm"]) == {
        "same_market_cross",
        "strike_monotonicity",
    }
    assert summary["count"].sum() == 2


def test_missing_database_has_an_actionable_error(tmp_path: Path) -> None:
    with pytest.raises(DashboardDatabaseError, match="Run the engine once"):
        load_opportunities(tmp_path / "missing.db")
