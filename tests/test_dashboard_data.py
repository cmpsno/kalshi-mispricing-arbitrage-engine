from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.dashboard_data import (
    DashboardDatabaseError,
    load_opportunities,
    load_paper_summary,
    summarize_opportunities,
)
from src.paper.config import PaperSettings
from src.paper.service import PaperService
from src.storage import SCHEMA, Database
from tests.test_paper_simulator import NOW, NOW_MS, buy_opp, crossed_books


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


@pytest.mark.asyncio
async def test_load_paper_summary_none_without_account(tmp_path: Path) -> None:
    database_path = tmp_path / "engine.db"
    async with Database(database_path):
        pass

    assert load_paper_summary(database_path, now=NOW) is None


@pytest.mark.asyncio
async def test_load_paper_summary_none_without_paper_tables(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "sample.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE opportunities (
                id TEXT PRIMARY KEY, detected_at INTEGER, algorithm TEXT,
                markets_involved TEXT, action TEXT, gross_profit_cents INTEGER,
                required_collateral_cents INTEGER, confidence_score REAL,
                details_json TEXT, executed BOOLEAN DEFAULT 0
            )
            """
        )

    assert load_paper_summary(database_path, now=NOW) is None


@pytest.mark.asyncio
async def test_load_paper_summary_after_simulation(tmp_path: Path) -> None:
    database_path = tmp_path / "paper.db"
    async with Database(database_path) as db:
        service = PaperService(db, PaperSettings())
        await service.simulate_opportunity(
            buy_opp(), 2, crossed_books(), now_ms=NOW_MS
        )
        await db.insert_order_book(crossed_books()["MARKET"])

    summary = load_paper_summary(database_path, now=NOW)
    assert summary is not None
    overall = summary["overall"]
    assert overall["equity_cents"] == 100_020
    assert overall["roi_percent"] == pytest.approx(0.02)
    assert overall["simulated_orders"] == 2
    assert overall["filled_orders"] == 2
    assert overall["fees_excluded"] is True
    weekly = summary["weekly"]
    assert weekly["reconstructable"] is True
    assert weekly["net_pnl_cents"] == 20
    assert len(summary["equity_curve"]) == 1
    assert len(summary["fills"]) == 2
    assert not summary["ledger"].empty
    assert summary["last_update_ms"] == NOW_MS


@pytest.mark.asyncio
async def test_load_paper_summary_stale_books_hide_equity(tmp_path: Path) -> None:
    database_path = tmp_path / "paper.db"
    async with Database(database_path) as db:
        service = PaperService(db, PaperSettings())
        await service.simulate_opportunity(
            buy_opp(), 2, crossed_books(), now_ms=NOW_MS
        )
        # No order book persisted: the dashboard has nothing fresh to
        # mark open positions against, so equity must be unknown.
    summary = load_paper_summary(database_path, now=NOW)
    assert summary is not None
    assert summary["overall"]["equity_cents"] is None
    assert summary["overall"]["unrealized_pnl_cents"] is None
    assert summary["overall"]["cash_cents"] == 99_810
