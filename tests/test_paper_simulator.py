"""Phase 4 tests for the paper-trading simulator.

Fixtures are synthetic books (never live market data). The book fixture
below is a crossed YES-side book for the same-market opportunity
YES ask 45 / NO ask 50: YES bids 50, YES asks 45. A NO ask of 50 is the
YES bid of 50 converted (``100 - 50``), and a NO bid of 55 would be the
YES ask of 45 converted (``100 - 45``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.arbitrage.detectors import detect_same_market_cross
from src.models import Market, OrderBook, OrderBookLevel
from src.paper.config import PaperSettings
from src.paper.models import PaperAccountState
from src.paper.service import PaperService
from src.paper.simulator import mark_to_market, simulate_close, simulate_opportunity
from src.storage import Database

NOW_MS = 1_789_951_380_000  # 2026-09-21 00:50:00 UTC
NOW = datetime.fromtimestamp(NOW_MS / 1_000, tz=UTC)
MAX_AGE = 5.0


def book(
    *,
    bids: list[tuple[int, int]] | None = None,
    asks: list[tuple[int, int]] | None = None,
    timestamp: datetime = NOW,
) -> OrderBook:
    return OrderBook(
        ticker="MARKET",
        timestamp=timestamp,
        bids=[OrderBookLevel(price=p, size=s) for p, s in (bids or [])],
        asks=[OrderBookLevel(price=p, size=s) for p, s in (asks or [])],
    )


def buy_opp():
    return detect_same_market_cross(
        [Market(ticker="MARKET", event_ticker="EVENT", yes_ask=45, no_ask=50)]
    )[0]


def sell_opp():
    return detect_same_market_cross(
        [Market(ticker="MARKET", event_ticker="EVENT", yes_bid=55, no_bid=50)]
    )[0]


def fresh_state(cash: int = 100_000) -> PaperAccountState:
    return PaperAccountState(initial_cash_cents=100_000, cash_cents=cash)


def crossed_books() -> dict[str, OrderBook]:
    # YES bid 50 (NO ask 50), YES ask 45 (NO bid 55).
    return {"MARKET": book(bids=[(50, 10)], asks=[(45, 10)])}


def test_starting_balance_and_initial_equity() -> None:
    state = fresh_state()
    assert state.cash_cents == 100_000
    assert state.buying_power_cents == 100_000
    assert state.realized_pnl_cents == 0
    assert state.positions == {}


def test_full_fill_opens_both_legs() -> None:
    result = simulate_opportunity(
        buy_opp(), 2, crossed_books(), fresh_state(), NOW_MS, MAX_AGE
    )

    assert result.status == "accepted"
    assert [o.status for o in result.orders] == ["filled", "filled"]
    assert [(f.filled_quantity, f.total_cents) for f in result.fills] == [
        (2, 90),
        (2, 100),
    ]
    assert all(f.fee_cents is None for f in result.fills)
    state = result.state
    assert state is not None
    assert state.cash_cents == 100_000 - 190
    assert state.positions[("MARKET", "yes", "buy")].quantity == 2
    assert state.positions[("MARKET", "no", "buy")].quantity == 2
    assert state.buying_power_cents == state.cash_cents
    # MTM at the same crossed book: YES exit 50, NO exit 55.
    mtm, unrealized, stale = mark_to_market(
        state.positions, crossed_books(), NOW_MS, MAX_AGE
    )
    assert stale == []
    assert mtm == 2 * 50 + 2 * 55
    assert unrealized == 210 - 190
    assert result.snapshot is not None
    assert result.snapshot["equity_cents"] == state.cash_cents + mtm


def test_no_fill_when_book_is_beyond_limit() -> None:
    books = {"MARKET": book(bids=[(40, 10)], asks=[(60, 10)])}
    result = simulate_opportunity(buy_opp(), 2, books, fresh_state(), NOW_MS, MAX_AGE)

    assert result.status == "accepted"
    assert [o.status for o in result.orders] == ["expired", "expired"]
    assert result.fills == []
    assert result.state is not None
    assert result.state.cash_cents == 100_000
    assert result.state.positions == {}
    # The hold placed for each leg is fully released on expiry.
    assert sum(e.reserved_delta_cents for e in result.events) == 0


def test_partial_fill_keeps_residual_exposure() -> None:
    # YES ask depth 10, NO ask depth 1 (YES bid size 1).
    books = {"MARKET": book(bids=[(50, 1)], asks=[(45, 10)])}
    result = simulate_opportunity(buy_opp(), 2, books, fresh_state(), NOW_MS, MAX_AGE)

    assert result.status == "accepted"
    assert [o.status for o in result.orders] == ["filled", "partial"]
    assert [(f.filled_quantity, f.total_cents) for f in result.fills] == [
        (2, 90),
        (1, 50),
    ]
    state = result.state
    assert state is not None
    assert state.cash_cents == 100_000 - 140
    assert state.positions[("MARKET", "yes", "buy")].quantity == 2
    assert state.positions[("MARKET", "no", "buy")].quantity == 1
    # The unfilled NO contract expired; it is not a position.
    assert any(e.event_type == "ORDER_EXPIRED" for e in result.events)


def test_depth_walk_uses_vwap_across_levels() -> None:
    books = {
        "MARKET": book(
            bids=[(50, 10)],
            asks=[(44, 1), (45, 2)],
        )
    }
    result = simulate_opportunity(buy_opp(), 2, books, fresh_state(), NOW_MS, MAX_AGE)

    yes_fill = result.fills[0]
    assert yes_fill.filled_quantity == 2
    assert yes_fill.total_cents == 44 + 45
    # VWAP 44.5 rounds half-up to 45 for display; the ledger total is exact.
    assert yes_fill.fill_price_cents == 45


def test_rejected_when_book_missing_or_stale() -> None:
    result = simulate_opportunity(buy_opp(), 2, {}, fresh_state(), NOW_MS, MAX_AGE)
    assert result.status == "rejected"
    assert result.reject_reason == "missing_book"
    assert result.orders == [] and result.events == []

    old = datetime.fromtimestamp((NOW_MS - 10_000) / 1_000, tz=UTC)
    books = {"MARKET": book(bids=[(50, 10)], asks=[(45, 10)], timestamp=old)}
    result = simulate_opportunity(buy_opp(), 2, books, fresh_state(), NOW_MS, MAX_AGE)
    assert result.status == "rejected"
    assert result.reject_reason == "stale_book"


def test_rejected_when_cash_is_insufficient() -> None:
    result = simulate_opportunity(
        buy_opp(), 10, crossed_books(), fresh_state(cash=100), NOW_MS, MAX_AGE
    )

    assert result.status == "rejected"
    assert result.reject_reason == "insufficient_cash"
    assert [o.status for o in result.orders] == ["rejected", "rejected"]
    assert result.fills == []
    assert all(e.event_type == "REJECTED" for e in result.events)


def test_sell_legs_use_margin_and_short_mtm() -> None:
    # Sell YES @55 (YES bid 55), sell NO @50 (NO bid 50 = YES ask 50).
    books = {"MARKET": book(bids=[(55, 10)], asks=[(50, 10)])}
    result = simulate_opportunity(
        sell_opp(), 2, books, fresh_state(), NOW_MS, MAX_AGE
    )

    assert result.status == "accepted"
    state = result.state
    assert state is not None
    # Proceeds 2*55 + 2*50 land in cash.
    assert state.cash_cents == 100_000 + 210
    # Margin held: YES short 2*(100-55)=90, NO short 2*(100-50)=100.
    assert state.short_margin_cents == 190
    assert state.buying_power_cents == state.cash_cents - 190
    # MTM at the same crossed book: short YES exit ask 50, short NO
    # exit 100 - YES bid 55 = 45.
    mtm, unrealized, stale = mark_to_market(state.positions, books, NOW_MS, MAX_AGE)
    assert stale == []
    assert mtm == -(2 * 50 + 2 * 45)
    assert unrealized == 20
    assert state.cash_cents + mtm == 100_020


def test_close_long_position_realizes_pnl() -> None:
    opened = simulate_opportunity(
        buy_opp(), 2, crossed_books(), fresh_state(), NOW_MS, MAX_AGE
    )
    assert opened.state is not None
    position = opened.state.positions[("MARKET", "yes", "buy")]
    close_books = {"MARKET": book(bids=[(60, 10)], asks=[(65, 10)])}
    result = simulate_close(
        position, 2, close_books, opened.state, NOW_MS + 1_000, MAX_AGE
    )

    assert result.status == "accepted"
    assert result.state is not None
    assert result.state.cash_cents == 99_810 + 120
    assert result.state.realized_pnl_cents == 30
    assert ("MARKET", "yes", "buy") not in result.state.positions
    assert len(result.closed) == 1
    assert result.closed[0].realized_pnl_cents == 30
    assert result.closed[0].close_reason == "offset"
    # The NO leg stays open.
    assert ("MARKET", "no", "buy") in result.state.positions


def test_close_short_position_realizes_pnl() -> None:
    books = {"MARKET": book(bids=[(55, 10)], asks=[(50, 10)])}
    opened = simulate_opportunity(
        sell_opp(), 2, books, fresh_state(), NOW_MS, MAX_AGE
    )
    assert opened.state is not None
    position = opened.state.positions[("MARKET", "yes", "sell")]
    result = simulate_close(
        position, 2, books, opened.state, NOW_MS + 1_000, MAX_AGE
    )

    assert result.status == "accepted"
    assert result.state is not None
    # Buy back 2 @50 = 100; received 110 at open, so +10 realized.
    assert result.state.cash_cents == 100_210 - 100
    assert result.state.realized_pnl_cents == 10
    assert result.state.short_margin_cents == 100  # NO short remains


def test_partial_close_keeps_remainder_open() -> None:
    opened = simulate_opportunity(
        buy_opp(), 2, crossed_books(), fresh_state(), NOW_MS, MAX_AGE
    )
    assert opened.state is not None
    position = opened.state.positions[("MARKET", "yes", "buy")]
    close_books = {"MARKET": book(bids=[(60, 10)], asks=[(65, 10)])}
    result = simulate_close(
        position, 1, close_books, opened.state, NOW_MS + 1_000, MAX_AGE
    )

    assert result.status == "accepted"
    assert result.state is not None
    assert result.state.realized_pnl_cents == 15
    remaining = result.state.positions[("MARKET", "yes", "buy")]
    assert remaining.quantity == 1
    assert remaining.cost_basis_cents == 45
    assert result.closed == []


def test_close_rejected_without_liquidity() -> None:
    opened = simulate_opportunity(
        buy_opp(), 2, crossed_books(), fresh_state(), NOW_MS, MAX_AGE
    )
    assert opened.state is not None
    position = opened.state.positions[("MARKET", "yes", "buy")]
    empty = {"MARKET": book(bids=[], asks=[])}
    result = simulate_close(
        position, 1, empty, opened.state, NOW_MS + 1_000, MAX_AGE
    )
    assert result.status == "rejected"
    assert result.reject_reason == "no_liquidity"


def test_mtm_excludes_stale_positions() -> None:
    opened = simulate_opportunity(
        buy_opp(), 2, crossed_books(), fresh_state(), NOW_MS, MAX_AGE
    )
    assert opened.state is not None
    mtm, unrealized, stale = mark_to_market(opened.state.positions, {}, NOW_MS, MAX_AGE)
    assert mtm is None and unrealized is None
    assert stale == ["MARKET", "MARKET"]


def test_paper_code_never_submits_orders() -> None:
    repo = Path(__file__).resolve().parent.parent
    for relative in ("src/paper/simulator.py", "src/paper/service.py"):
        source = (repo / relative).read_text()
        assert "place_orders" not in source
        assert "KalshiClient" not in source


@pytest.mark.asyncio
async def test_service_persists_and_reloads_state(tmp_path: Path) -> None:
    async with Database(tmp_path / "paper.db") as db:
        service = PaperService(db, PaperSettings())
        result = await service.simulate_opportunity(
            buy_opp(), 2, crossed_books(), now_ms=NOW_MS
        )
        assert result.status == "accepted"

        # A fresh service on the same database replays the same state.
        reloaded = PaperService(db, PaperSettings())
        state = await reloaded.load_state()
        assert state.cash_cents == 100_000 - 190
        assert state.realized_pnl_cents == 0
        assert len(state.positions) == 2
        # Ledger replay reproduces cash exactly.
        events = await db.get_paper_ledger_events()
        assert sum(e["cash_delta_cents"] for e in events) == state.cash_cents
        assert await db.table_count("paper_orders") == 2
        assert await db.table_count("paper_fills") == 2
        assert await db.table_count("paper_equity_snapshots") == 1


@pytest.mark.asyncio
async def test_service_persists_insufficient_cash_rejection(
    tmp_path: Path,
) -> None:
    async with Database(tmp_path / "paper.db") as db:
        service = PaperService(db, PaperSettings(initial_cash_cents=100))
        result = await service.simulate_opportunity(
            buy_opp(), 10, crossed_books(), now_ms=NOW_MS
        )
        assert result.status == "rejected"
        assert await db.table_count("paper_orders") == 2
        events = await db.get_paper_ledger_events()
        assert [e["event_type"] for e in events if e["event_type"] != "DEPOSIT"] == [
            "REJECTED",
            "REJECTED",
        ]


@pytest.mark.asyncio
async def test_service_does_not_persist_stale_book_rejection(
    tmp_path: Path,
) -> None:
    async with Database(tmp_path / "paper.db") as db:
        service = PaperService(db, PaperSettings())
        result = await service.simulate_opportunity(
            buy_opp(), 2, {}, now_ms=NOW_MS
        )
        assert result.status == "rejected"
        assert await db.table_count("paper_orders") == 0


def test_paper_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPER_ENABLED", "true")
    monkeypatch.setenv("PAPER_INITIAL_CASH_CENTS", "50000")
    monkeypatch.setenv("PAPER_MAX_BOOK_AGE_SECONDS", "2")
    settings = PaperSettings.from_env()
    assert settings.enabled is True
    assert settings.initial_cash_cents == 50_000
    assert settings.max_book_age_seconds == 2.0
    monkeypatch.setenv("PAPER_INITIAL_CASH_CENTS", "0")
    with pytest.raises(ValueError):
        PaperSettings.from_env()
