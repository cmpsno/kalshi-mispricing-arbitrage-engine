"""Phase 4 tests for paper-trading reporting (overall + weekly)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.paper.config import PaperSettings
from src.paper.service import PaperService
from src.storage import Database
from tests.test_paper_simulator import NOW_MS, buy_opp, crossed_books


@pytest.mark.asyncio
async def test_overall_report_after_full_fill(tmp_path: Path) -> None:
    async with Database(tmp_path / "paper.db") as db:
        service = PaperService(db, PaperSettings())
        await service.simulate_opportunity(
            buy_opp(), 2, crossed_books(), now_ms=NOW_MS
        )
        report = await service.overall_report(crossed_books(), now_ms=NOW_MS)

    assert report["initial_capital_cents"] == 100_000
    assert report["cash_cents"] == 99_810
    assert report["equity_cents"] == 100_020
    assert report["realized_pnl_cents"] == 0
    assert report["unrealized_pnl_cents"] == 20
    assert report["net_pnl_cents"] == 20
    assert report["roi_percent"] == pytest.approx(0.02)
    assert report["roi_realized_only_percent"] == 0.0
    assert report["simulated_orders"] == 2
    assert report["filled_orders"] == 2
    assert report["fill_rate_by_quantity"] == 1.0
    assert report["fill_rate_by_order"] == 1.0
    assert report["fees_excluded"] is True
    assert report["detector_opportunities"] == 0


@pytest.mark.asyncio
async def test_overall_report_excludes_stale_mtm(tmp_path: Path) -> None:
    async with Database(tmp_path / "paper.db") as db:
        service = PaperService(db, PaperSettings())
        await service.simulate_opportunity(
            buy_opp(), 2, crossed_books(), now_ms=NOW_MS
        )
        # No books available at report time: equity is unknown, not guessed.
        report = await service.overall_report({}, now_ms=NOW_MS)

    assert report["equity_cents"] is None
    assert report["unrealized_pnl_cents"] is None
    assert report["net_pnl_cents"] is None
    assert report["roi_percent"] is None
    assert report["mtm_stale_tickers"] == ["MARKET", "MARKET"]
    assert report["cash_cents"] == 99_810


@pytest.mark.asyncio
async def test_weekly_report_for_current_week(tmp_path: Path) -> None:
    async with Database(tmp_path / "paper.db") as db:
        service = PaperService(db, PaperSettings())
        await service.simulate_opportunity(
            buy_opp(), 2, crossed_books(), now_ms=NOW_MS
        )
        report = await service.weekly_report(
            reference_ms=NOW_MS, now_ms=NOW_MS
        )

    # NOW_MS is Monday 2026-09-21 00:50 UTC; the week starts Monday
    # 00:00 America/New_York, before the account existed, so starting
    # equity is the initial capital.
    assert report["starting_equity_cents"] == 100_000
    assert report["ending_equity_cents"] == 100_020
    assert report["net_pnl_cents"] == 20
    assert report["roi_percent_vs_initial_capital"] == pytest.approx(0.02)
    assert report["roi_percent_vs_starting_equity"] == pytest.approx(0.02)
    assert report["realized_pnl_cents"] == 0
    assert report["reconstructable"] is True
    assert report["simulated_orders"] == 2
    assert report["data_basis"] == "live-observation paper fills"
    assert report["timezone"] == "America/New_York"
    assert report["fees_excluded"] is True


@pytest.mark.asyncio
async def test_weekly_report_with_no_activity(tmp_path: Path) -> None:
    async with Database(tmp_path / "paper.db") as db:
        service = PaperService(db, PaperSettings())
        await service.ensure_account(now_ms=NOW_MS)
        report = await service.weekly_report(
            reference_ms=NOW_MS, now_ms=NOW_MS
        )

    assert report["starting_equity_cents"] == 100_000
    assert report["ending_equity_cents"] is None
    assert report["net_pnl_cents"] is None
    assert report["reconstructable"] is False
    assert report["data_basis"] == "no paper fills in period"
