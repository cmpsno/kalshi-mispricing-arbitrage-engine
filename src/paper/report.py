"""Paper-trading performance reporting.

Every figure is computed from the append-only ledger, the positions
table, and equity snapshots — never from detector potential profit.
Fees are always reported as excluded (``fee_cents`` is ``None`` in v1);
a report that cannot establish a figure says so instead of estimating
one.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..models import OrderBook
from .models import PaperAccountState, PaperPosition
from .simulator import mark_to_market

REPORT_TIMEZONE = "America/New_York"  # confirmed by the owner (Phase 2)


def _pct(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator * 100


def week_bounds(reference_ms: int, tz_name: str = REPORT_TIMEZONE) -> tuple[int, int]:
    """Monday 00:00 (local) to the following Monday 00:00, in ms epoch UTC."""

    tz = ZoneInfo(tz_name)
    reference = datetime.fromtimestamp(reference_ms / 1_000, tz=tz)
    monday = (reference - timedelta(days=reference.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (
        int(monday.timestamp() * 1_000),
        int((monday + timedelta(days=7)).timestamp() * 1_000),
    )


def overall_report(
    state: PaperAccountState,
    books: dict[str, OrderBook],
    now_ms: int,
    max_book_age_seconds: float,
    *,
    orders: list[dict],
    fills: list[dict],
    opportunities_count: int,
) -> dict:
    """Current account report. ``equity``/``unrealized`` are ``None`` when
    any open position lacks a fresh book (stale MTM is excluded, not
    guessed)."""

    mtm_total, unrealized, stale = mark_to_market(
        state.positions, books, now_ms, max_book_age_seconds
    )
    equity = (
        state.cash_cents + mtm_total if mtm_total is not None else None
    )
    net_pnl = (
        equity - state.initial_cash_cents if equity is not None else None
    )
    filled_orders = [o for o in orders if o["status"] in ("filled", "partial")]
    filled_qty = sum(f["filled_quantity"] for f in fills)
    requested_qty = sum(o["requested_quantity"] for o in orders)
    return {
        "initial_capital_cents": state.initial_cash_cents,
        "cash_cents": state.cash_cents,
        "margin_cents": state.short_margin_cents,
        "capital_deployed_cents": state.capital_deployed_cents,
        "equity_cents": equity,
        "realized_pnl_cents": state.realized_pnl_cents,
        "unrealized_pnl_cents": unrealized,
        "net_pnl_cents": net_pnl,
        "roi_percent": _pct(net_pnl, state.initial_cash_cents),
        "roi_realized_only_percent": _pct(
            state.realized_pnl_cents, state.initial_cash_cents
        ),
        "includes_unrealized": unrealized is not None,
        "mtm_stale_tickers": stale,
        "open_positions": [
            {
                "ticker": p.ticker,
                "side": p.side,
                "direction": p.direction,
                "quantity": p.quantity,
                "cost_basis_cents": p.cost_basis_cents,
            }
            for p in state.positions.values()
        ],
        "simulated_orders": len(orders),
        "filled_orders": len(filled_orders),
        "unfilled_orders": len(orders) - len(filled_orders),
        "filled_quantity": filled_qty,
        "requested_quantity": requested_qty,
        "fill_rate_by_quantity": (
            filled_qty / requested_qty if requested_qty else None
        ),
        "fill_rate_by_order": (
            len(filled_orders) / len(orders) if orders else None
        ),
        "detector_opportunities": opportunities_count,
        "fees_excluded": True,
        "fees_note": "Fees are not included: no verified fee formula exists yet.",
    }


def weekly_report(
    *,
    start_ms: int,
    end_ms: int,
    initial_cash_cents: int,
    snapshot_at_start: dict | None,
    snapshot_at_end: dict | None,
    had_events_before_start: bool,
    ledger_events_in_range: list[dict],
    orders_in_range: list[dict],
    fills_in_range: list[dict],
    open_positions: list[PaperPosition],
    state_at_end: PaperAccountState,
) -> dict:
    """Weekly report for ``[start_ms, end_ms)``.

    Starting equity comes from the latest equity snapshot at or before
    the week start; if none exists and the account had no activity
    before the week, starting equity is the initial capital. If the
    account was active before the week but has no snapshot, starting
    equity is ``None`` and the weekly net P&L is not stated — the
    report says the week cannot be reconstructed rather than guessing.
    """

    if snapshot_at_start is not None:
        starting_equity = snapshot_at_start["equity_cents"]
        starting_unrealized = snapshot_at_start["unrealized_pnl_cents"]
    elif not had_events_before_start:
        starting_equity = initial_cash_cents
        starting_unrealized = 0
    else:
        starting_equity = None
        starting_unrealized = None

    if snapshot_at_end is not None:
        ending_equity = snapshot_at_end["equity_cents"]
        ending_unrealized = snapshot_at_end["unrealized_pnl_cents"]
    else:
        ending_equity = None
        ending_unrealized = None

    net_pnl = (
        ending_equity - starting_equity
        if starting_equity is not None and ending_equity is not None
        else None
    )
    realized_in_period = sum(
        event["realized_pnl_cents"] for event in ledger_events_in_range
    )
    filled_orders = [
        o for o in orders_in_range if o["status"] in ("filled", "partial")
    ]
    filled_qty = sum(f["filled_quantity"] for f in fills_in_range)
    requested_qty = sum(o["requested_quantity"] for o in orders_in_range)
    return {
        "period_start_ms": start_ms,
        "period_end_ms": end_ms,
        "timezone": REPORT_TIMEZONE,
        "starting_equity_cents": starting_equity,
        "ending_equity_cents": ending_equity,
        "net_pnl_cents": net_pnl,
        "roi_percent_vs_initial_capital": _pct(net_pnl, initial_cash_cents),
        "roi_percent_vs_starting_equity": _pct(net_pnl, starting_equity),
        "realized_pnl_cents": realized_in_period,
        "unrealized_start_cents": starting_unrealized,
        "unrealized_end_cents": ending_unrealized,
        "includes_unrealized": net_pnl is not None,
        "reconstructable": starting_equity is not None
        and ending_equity is not None,
        "simulated_orders": len(orders_in_range),
        "filled_orders": len(filled_orders),
        "unfilled_orders": len(orders_in_range) - len(filled_orders),
        "filled_quantity": filled_qty,
        "requested_quantity": requested_qty,
        "fill_rate_by_quantity": (
            filled_qty / requested_qty if requested_qty else None
        ),
        "fill_rate_by_order": (
            len(filled_orders) / len(orders_in_range)
            if orders_in_range
            else None
        ),
        "open_positions_at_end": [
            {
                "ticker": p.ticker,
                "side": p.side,
                "direction": p.direction,
                "quantity": p.quantity,
                "cost_basis_cents": p.cost_basis_cents,
            }
            for p in open_positions
        ],
        "capital_deployed_cents": state_at_end.capital_deployed_cents,
        "fees_excluded": True,
        "data_basis": (
            "live-observation paper fills"
            if fills_in_range
            else "no paper fills in period"
        ),
    }
