"""Paper-trading service: persistence around the pure simulator.

This is the only paper module that touches the database. It never
imports the Kalshi client and never submits an order to any host —
paper mode is isolated from demo-host submission by construction:
``src/main.py`` calls this service *instead of* the demo executor when
paper mode is enabled.
"""

from __future__ import annotations

import structlog
from datetime import UTC, datetime

from ..models import ArbitrageOpportunity, OrderBook
from ..storage import Database
from .config import PaperSettings
from .models import PaperAccountState, PaperPosition
from .report import overall_report, week_bounds, weekly_report
from .simulator import SimulationResult, simulate_close, simulate_opportunity

log = structlog.get_logger()


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1_000)


class PaperService:
    def __init__(self, db: Database, settings: PaperSettings) -> None:
        self._db = db
        self._settings = settings

    @property
    def settings(self) -> PaperSettings:
        return self._settings

    async def ensure_account(self, now_ms: int | None = None) -> dict:
        """Create the paper account on first use, with a DEPOSIT event.

        The account is created once; later calls return the existing row
        and never reset the balance.
        """

        account = await self._db.get_paper_account()
        if account is not None:
            return account
        now = now_ms if now_ms is not None else _now_ms()
        account_id = await self._db.create_paper_account(
            self._settings.initial_cash_cents, now
        )
        await self._db.save_paper_ledger_event(
            now,
            "DEPOSIT",
            cash_delta_cents=self._settings.initial_cash_cents,
            details={
                "initial_cash_cents": self._settings.initial_cash_cents,
                "fees": "excluded",
            },
        )
        account = await self._db.get_paper_account()
        assert account is not None and account["id"] == account_id
        return account

    async def load_state(self) -> PaperAccountState:
        """Rebuild account state: cash from ledger deltas, positions
        from the positions table, realized P&L from ledger deltas."""

        account = await self._db.get_paper_account()
        if account is None:
            raise ValueError("paper account does not exist yet")
        events = await self._db.get_paper_ledger_events()
        cash = sum(event["cash_delta_cents"] for event in events)
        realized = sum(event["realized_pnl_cents"] for event in events)
        positions: dict[tuple[str, str, str], PaperPosition] = {}
        for row in await self._db.list_open_paper_positions():
            position = PaperPosition(
                ticker=row["ticker"],
                side=row["side"],
                direction=row["direction"],
                quantity=row["quantity"],
                cost_basis_cents=row["cost_basis_cents"],
                opened_at_ms=row["opened_at"],
                db_id=row["id"],
            )
            positions[position.key] = position
        return PaperAccountState(
            initial_cash_cents=account["initial_cash_cents"],
            cash_cents=cash,
            realized_pnl_cents=realized,
            positions=positions,
        )

    async def _persist_result(
        self, result: SimulationResult, *, persist_rejected: bool
    ) -> None:
        if result.status == "rejected" and not persist_rejected:
            return
        for order in result.orders:
            await self._db.save_paper_order(order.as_dict())
        for fill in result.fills:
            await self._db.save_paper_fill(fill.as_dict())
        for event in result.events:
            await self._db.save_paper_ledger_event(
                event.ts_ms,
                event.event_type,
                order_id=event.order_id,
                ticker=event.ticker,
                cash_delta_cents=event.cash_delta_cents,
                reserved_delta_cents=event.reserved_delta_cents,
                realized_pnl_cents=event.realized_pnl_cents,
                details=event.details,
            )
        if result.state is not None:
            for position in result.state.positions.values():
                if position.db_id is None:
                    position.db_id = await self._db.insert_paper_position(
                        {
                            "ticker": position.ticker,
                            "side": position.side,
                            "direction": position.direction,
                            "quantity": position.quantity,
                            "cost_basis_cents": position.cost_basis_cents,
                            "opened_at": position.opened_at_ms,
                            "closed_at": None,
                            "realized_pnl_cents": None,
                            "close_reason": None,
                        }
                    )
                else:
                    await self._db.update_paper_position(
                        position.db_id,
                        quantity=position.quantity,
                        cost_basis_cents=position.cost_basis_cents,
                    )
        for closed in result.closed:
            position = closed.position
            assert position.db_id is not None
            await self._db.update_paper_position(
                position.db_id,
                quantity=0,
                cost_basis_cents=0,
                closed_at=closed.closed_at_ms,
                realized_pnl_cents=closed.realized_pnl_cents,
                close_reason=closed.close_reason,
            )
        if result.snapshot is not None:
            snapshot = result.snapshot
            await self._db.save_equity_snapshot(
                snapshot["ts"],
                snapshot["equity_cents"],
                snapshot["cash_cents"],
                snapshot["margin_cents"],
                snapshot["unrealized_pnl_cents"],
                snapshot["realized_pnl_cents"],
            )

    async def simulate_opportunity(
        self,
        opp: ArbitrageOpportunity,
        quantity: int,
        books: dict[str, OrderBook],
        now_ms: int | None = None,
    ) -> SimulationResult:
        """Simulate one detected opportunity and persist the outcome.

        Missing/stale-book rejections are logged, not persisted: with a
        2-second scan loop they would otherwise flood the ledger with
        one rejected row per scan while the feed is down. Insufficient-
        cash rejections are persisted (they are account events).
        """

        now = now_ms if now_ms is not None else _now_ms()
        await self.ensure_account(now)
        state = await self.load_state()
        result = simulate_opportunity(
            opp,
            quantity,
            books,
            state,
            now,
            self._settings.max_book_age_seconds,
        )
        if result.status == "rejected":
            if result.reject_reason in ("missing_book", "stale_book"):
                await log.ainfo(
                    "paper_simulation_rejected",
                    opportunity_id=opp.id,
                    reason=result.reject_reason,
                )
                return result
            await self._persist_result(result, persist_rejected=True)
            return result
        await self._persist_result(result, persist_rejected=False)
        await log.ainfo(
            "paper_simulation",
            opportunity_id=opp.id,
            orders=len(result.orders),
            fills=len(result.fills),
        )
        return result

    async def close_position(
        self,
        ticker: str,
        side: str,
        direction: str,
        quantity: int,
        books: dict[str, OrderBook],
        now_ms: int | None = None,
    ) -> SimulationResult:
        """Close (part of) an open position by offsetting trade."""

        now = now_ms if now_ms is not None else _now_ms()
        state = await self.load_state()
        position = state.positions.get((ticker, side, direction))
        if position is None:
            raise ValueError(f"no open paper position: {ticker} {side} {direction}")
        result = simulate_close(
            position,
            quantity,
            books,
            state,
            now,
            self._settings.max_book_age_seconds,
        )
        if result.status == "rejected":
            await log.ainfo(
                "paper_close_rejected",
                ticker=ticker,
                reason=result.reject_reason,
            )
            return result
        await self._persist_result(result, persist_rejected=False)
        return result

    async def latest_books(self) -> dict[str, OrderBook]:
        """Latest persisted order book per ticker with an open position."""

        books: dict[str, OrderBook] = {}
        state = await self.load_state()
        for position in state.positions.values():
            book = await self._db.get_latest_order_book(position.ticker)
            if book is not None:
                books[position.ticker] = book
        return books

    async def overall_report(
        self,
        books: dict[str, OrderBook],
        now_ms: int | None = None,
    ) -> dict:
        now = now_ms if now_ms is not None else _now_ms()
        state = await self.load_state()
        return overall_report(
            state,
            books,
            now,
            self._settings.max_book_age_seconds,
            orders=await self._db.get_paper_orders(),
            fills=await self._db.get_paper_fills(),
            opportunities_count=await self._db.table_count("opportunities"),
        )

    async def weekly_report(
        self,
        reference_ms: int | None = None,
        now_ms: int | None = None,
    ) -> dict:
        """Weekly report for the week containing ``reference_ms``.

        Ending equity uses the latest snapshot at or before week end;
        for the current week that is the most recent snapshot, which the
        simulator writes after every accepted simulation.
        """

        now = now_ms if now_ms is not None else _now_ms()
        reference = reference_ms if reference_ms is not None else now
        start_ms, end_ms = week_bounds(reference)
        state = await self.load_state()
        events_before = await self._db.get_paper_ledger_events(end_ms=start_ms)
        return weekly_report(
            start_ms=start_ms,
            end_ms=end_ms,
            initial_cash_cents=state.initial_cash_cents,
            snapshot_at_start=await self._db.get_equity_snapshot_at(start_ms),
            snapshot_at_end=await self._db.get_equity_snapshot_at(end_ms),
            had_events_before_start=len(events_before) > 0,
            ledger_events_in_range=await self._db.get_paper_ledger_events(
                start_ms, end_ms
            ),
            orders_in_range=await self._db.get_paper_orders(start_ms, end_ms),
            fills_in_range=await self._db.get_paper_fills(start_ms, end_ms),
            open_positions=list(state.positions.values()),
            state_at_end=state,
        )
