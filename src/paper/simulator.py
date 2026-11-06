"""Local paper-trading simulator.

Pure simulation over observed order books: this module never imports the
Kalshi client, never makes a network call, and never submits an order to
any host. A detector signal alone is not a fill — a fill exists only if
the observed order book supported it at that moment, under the rules in
PAPER_TRADING_PLAN.md §3.

Execution model (IOC limit orders, one coherent book snapshot per leg):

- Every leg's book must exist and be no older than ``max_book_age_seconds``
  at order time. If any leg's book is missing or stale, the whole
  opportunity is rejected — no leg is simulated.
- A buy fills only up to displayed size at ask prices at or below the
  limit, walking ask levels; a sell fills only up to displayed size at
  bid prices at or above the limit, walking bid levels. Fill price is
  the volume-weighted average of the levels consumed; the exact total
  (``sum(price * quantity)``) is what the ledger records.
- Unfilled quantity expires immediately (IOC). It never rests.
- Requested quantity is capped by buying power
  (``cash - short margin``) via the reserve check; the caller sizes by
  collateral first (``src/arbitrage/risk.py:calculate_max_quantity``).

Fees are not modelled: every fill records ``fee_cents=None`` and results
are gross of fees (see PAPER_TRADING_PLAN.md §1).
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field

from ..models import ArbitrageOpportunity, OrderBook, OrderBookLevel
from .models import (
    LedgerEvent,
    PaperAccountState,
    PaperFill,
    PaperLeg,
    PaperOrder,
    PaperPosition,
)

DIRECTIONS = {
    "buy_yes": ("buy", "yes"),
    "buy_no": ("buy", "no"),
    "sell_yes": ("sell", "yes"),
    "sell_no": ("sell", "no"),
}


def parse_legs(opp: ArbitrageOpportunity) -> list[PaperLeg]:
    """Parse execution legs from an opportunity, mirroring the executor."""

    legs = opp.details.get("legs", [])
    if not isinstance(legs, list) or not legs:
        raise ValueError("opportunity must contain at least one execution leg")
    parsed: list[PaperLeg] = []
    for leg in legs:
        if not isinstance(leg, dict):
            raise TypeError("each execution leg must be an object")
        try:
            direction, side = DIRECTIONS[str(leg["side"])]
            ticker = str(leg["ticker"]).strip()
            price = int(leg["price"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "execution leg has an invalid ticker, side, or price"
            ) from exc
        parsed.append(
            PaperLeg(
                ticker=ticker, side=side, direction=direction,
                limit_price_cents=price,
            )
        )
    return parsed


def reserve_for_order(direction: str, limit_price_cents: int, quantity: int) -> int:
    """Buying-power reserve for an order: buy pays the price, a sell must
    hold ``100 - price`` per contract against the $1.00 payout obligation."""

    if direction == "buy":
        return quantity * limit_price_cents
    return quantity * (100 - limit_price_cents)


def walk_book(
    levels: list[OrderBookLevel],
    limit_price_cents: int | None,
    quantity: int,
    *,
    buy: bool,
) -> tuple[int, int]:
    """Walk book levels for an IOC order.

    Returns ``(filled_quantity, total_cents)``. A buy consumes asks in
    ascending price order while ``price <= limit``; a sell consumes bids
    in descending price order while ``price >= limit``. ``limit=None``
    (used only for offsetting closes) accepts every level.
    """

    ordered = sorted(levels, key=lambda level: level.price, reverse=not buy)
    filled = 0
    total = 0
    for level in ordered:
        if filled >= quantity:
            break
        if limit_price_cents is not None:
            if buy and level.price > limit_price_cents:
                break
            if not buy and level.price < limit_price_cents:
                break
        take = min(level.size, quantity - filled)
        if take <= 0:
            continue
        filled += take
        total += take * level.price
    return filled, total


def _execution_levels(
    side: str, direction: str, book: OrderBook
) -> list[OrderBookLevel]:
    """Book levels in the leg's own price scale, in fill order.

    The engine's books are YES-side: ``bids`` are YES bids and ``asks``
    are YES asks. A NO leg trades against the complementary scale:
    NO ask = ``100 - YES bid`` and NO bid = ``100 - YES ask``. Buying
    NO therefore consumes converted YES bids (ascending NO price);
    selling NO consumes converted YES asks (descending NO price).
    """

    if side == "yes":
        levels = book.asks if direction == "buy" else book.bids
    elif direction == "buy":
        levels = [
            OrderBookLevel(price=100 - level.price, size=level.size)
            for level in book.bids
        ]
    else:
        levels = [
            OrderBookLevel(price=100 - level.price, size=level.size)
            for level in book.asks
        ]
    return sorted(levels, key=lambda level: level.price, reverse=direction == "sell")


def vwap_cents(total_cents: int, quantity: int) -> int:
    """Volume-weighted average price, rounded half-up to whole cents."""

    if quantity <= 0:
        return 0
    return (2 * total_cents + quantity) // (2 * quantity)


def exit_price_cents(position: PaperPosition, book: OrderBook) -> int | None:
    """Current exit price for a position from a YES-side order book.

    The engine's books are YES-side: ``bids`` are YES bids, ``asks`` are
    YES asks (derived from NO bids). A NO position exits against the
    complementary YES price: NO bid = ``100 - YES ask``,
    NO ask = ``100 - YES bid``.
    """

    if position.direction == "buy" and position.side == "yes":
        return book.bids[0].price if book.bids else None
    if position.direction == "buy" and position.side == "no":
        return 100 - book.asks[0].price if book.asks else None
    if position.direction == "sell" and position.side == "yes":
        return book.asks[0].price if book.asks else None
    return 100 - book.bids[0].price if book.bids else None


def book_age_ms(book: OrderBook, now_ms: int) -> int:
    timestamp = book.timestamp
    if timestamp is None:
        return 10**18
    return now_ms - int(timestamp.timestamp() * 1_000)


def mark_to_market(
    positions: dict[tuple[str, str, str], PaperPosition],
    books: dict[str, OrderBook],
    now_ms: int,
    max_book_age_seconds: float,
) -> tuple[int | None, int | None, list[str]]:
    """Return ``(mtm_total, unrealized_pnl, stale_tickers)``.

    MTM is ``None`` (with the stale tickers listed) if any open position
    lacks a fresh book, so callers never silently mark with stale prices.
    """

    stale: list[str] = []
    mtm_total = 0
    unrealized = 0
    for position in positions.values():
        book = books.get(position.ticker)
        if book is None or book_age_ms(book, now_ms) > max_book_age_seconds * 1_000:
            stale.append(position.ticker)
            continue
        exit_price = exit_price_cents(position, book)
        if exit_price is None:
            stale.append(position.ticker)
            continue
        mtm_value = (
            position.quantity * exit_price
            if position.direction == "buy"
            else -position.quantity * exit_price
        )
        mtm_total += mtm_value
        unrealized += mtm_value - position.entry_value_cents
    if stale:
        return None, None, stale
    return mtm_total, unrealized, stale


@dataclass(slots=True)
class ClosedPosition:
    position: PaperPosition
    realized_pnl_cents: int
    closed_at_ms: int
    close_reason: str


@dataclass(slots=True)
class SimulationResult:
    status: str  # accepted / rejected
    reject_reason: str | None = None
    orders: list[PaperOrder] = field(default_factory=list)
    fills: list[PaperFill] = field(default_factory=list)
    events: list[LedgerEvent] = field(default_factory=list)
    closed: list[ClosedPosition] = field(default_factory=list)
    state: PaperAccountState | None = None
    snapshot: dict | None = None


def _snapshot(
    state: PaperAccountState,
    books: dict[str, OrderBook],
    now_ms: int,
    max_book_age_seconds: float,
) -> dict | None:
    mtm_total, unrealized, _ = mark_to_market(
        state.positions, books, now_ms, max_book_age_seconds
    )
    if mtm_total is None or unrealized is None:
        return None
    return {
        "ts": now_ms,
        "equity_cents": state.cash_cents + mtm_total,
        "cash_cents": state.cash_cents,
        "margin_cents": state.short_margin_cents,
        "unrealized_pnl_cents": unrealized,
        "realized_pnl_cents": state.realized_pnl_cents,
    }


def simulate_opportunity(
    opp: ArbitrageOpportunity,
    quantity: int,
    books: dict[str, OrderBook],
    state: PaperAccountState,
    now_ms: int,
    max_book_age_seconds: float,
) -> SimulationResult:
    """Simulate one opportunity against observed books.

    Returns a result holding the new account state plus the orders,
    fills, and ledger events to persist. The input state is not mutated.
    Rejections for a missing/stale book carry no orders or events — the
    caller logs them; only insufficient-cash rejections are persisted
    (see ``src/paper/service.py``).
    """

    if quantity < 1:
        raise ValueError("quantity must be at least 1")
    legs = parse_legs(opp)

    for leg in legs:
        book = books.get(leg.ticker)
        if book is None:
            return SimulationResult(status="rejected", reject_reason="missing_book")
        if book_age_ms(book, now_ms) > max_book_age_seconds * 1_000:
            return SimulationResult(status="rejected", reject_reason="stale_book")

    total_reserve = sum(
        reserve_for_order(leg.direction, leg.limit_price_cents, quantity)
        for leg in legs
    )
    if total_reserve > state.buying_power_cents:
        orders = [
            PaperOrder(
                id=str(uuid.uuid4()),
                opportunity_id=opp.id,
                created_at_ms=now_ms,
                ticker=leg.ticker,
                side=leg.side,
                direction=leg.direction,
                limit_price_cents=leg.limit_price_cents,
                requested_quantity=quantity,
                status="rejected",
            )
            for leg in legs
        ]
        events = [
            LedgerEvent(
                ts_ms=now_ms,
                event_type="REJECTED",
                order_id=order.id,
                ticker=order.ticker,
                details={
                    "reason": "insufficient_cash",
                    "required_reserve_cents": total_reserve,
                    "buying_power_cents": state.buying_power_cents,
                },
            )
            for order in orders
        ]
        return SimulationResult(
            status="rejected",
            reject_reason="insufficient_cash",
            orders=orders,
            events=events,
            state=state,
        )

    new_state = PaperAccountState(
        initial_cash_cents=state.initial_cash_cents,
        cash_cents=state.cash_cents,
        realized_pnl_cents=state.realized_pnl_cents,
        positions={
            key: copy.copy(position)
            for key, position in state.positions.items()
        },
    )
    orders: list[PaperOrder] = []
    fills: list[PaperFill] = []
    events: list[LedgerEvent] = []

    for leg in legs:
        order_id = str(uuid.uuid4())
        reserve_requested = reserve_for_order(
            leg.direction, leg.limit_price_cents, quantity
        )
        events.append(
            LedgerEvent(
                ts_ms=now_ms,
                event_type="ORDER_PLACED",
                order_id=order_id,
                ticker=leg.ticker,
                reserved_delta_cents=reserve_requested,
                details={"opportunity_id": opp.id},
            )
        )
        book = books[leg.ticker]
        levels = _execution_levels(leg.side, leg.direction, book)
        filled, total = walk_book(
            levels, leg.limit_price_cents, quantity, buy=leg.direction == "buy"
        )
        if filled > 0:
            margin_added = 0
            if leg.direction == "buy":
                cash_delta = -total
            else:
                cash_delta = total
                margin_added = filled * 100 - total
            events.append(
                LedgerEvent(
                    ts_ms=now_ms,
                    event_type="FILL",
                    order_id=order_id,
                    ticker=leg.ticker,
                    cash_delta_cents=cash_delta,
                    reserved_delta_cents=-reserve_requested + margin_added,
                    details={
                        "opportunity_id": opp.id,
                        "filled_quantity": filled,
                        "total_cents": total,
                        "book_timestamp_ms": now_ms
                        - book_age_ms(book, now_ms),
                    },
                )
            )
            fills.append(
                PaperFill(
                    order_id=order_id,
                    filled_at_ms=now_ms,
                    fill_price_cents=vwap_cents(total, filled),
                    filled_quantity=filled,
                    total_cents=total,
                    book_timestamp_ms=now_ms - book_age_ms(book, now_ms),
                )
            )
            new_state.cash_cents += cash_delta
            key = leg.key()
            existing = new_state.positions.get(key)
            if existing is None:
                new_state.positions[key] = PaperPosition(
                    ticker=leg.ticker,
                    side=leg.side,
                    direction=leg.direction,
                    quantity=filled,
                    cost_basis_cents=total,
                    opened_at_ms=now_ms,
                )
            else:
                existing.quantity += filled
                existing.cost_basis_cents += total
        else:
            # No fill: the ORDER_EXPIRED event releases the hold placed
            # above, so buying power is restored in full.
            events.append(
                LedgerEvent(
                    ts_ms=now_ms,
                    event_type="ORDER_EXPIRED",
                    order_id=order_id,
                    ticker=leg.ticker,
                    reserved_delta_cents=-reserve_requested,
                    details={
                        "opportunity_id": opp.id,
                        "filled_quantity": 0,
                        "total_cents": 0,
                        "reason": "ioc_unfilled",
                    },
                )
            )
        if 0 < filled < quantity:
            events.append(
                LedgerEvent(
                    ts_ms=now_ms,
                    event_type="ORDER_EXPIRED",
                    order_id=order_id,
                    ticker=leg.ticker,
                    details={
                        "opportunity_id": opp.id,
                        "unfilled_quantity": quantity - filled,
                        "reason": "ioc_unfilled",
                    },
                )
            )
        status = (
            "filled"
            if filled == quantity
            else ("partial" if filled > 0 else "expired")
        )
        orders.append(
            PaperOrder(
                id=order_id,
                opportunity_id=opp.id,
                created_at_ms=now_ms,
                ticker=leg.ticker,
                side=leg.side,
                direction=leg.direction,
                limit_price_cents=leg.limit_price_cents,
                requested_quantity=quantity,
                status=status,
            )
        )

    snapshot = _snapshot(new_state, books, now_ms, max_book_age_seconds)
    return SimulationResult(
        status="accepted",
        orders=orders,
        fills=fills,
        events=events,
        state=new_state,
        snapshot=snapshot,
    )


def simulate_close(
    position: PaperPosition,
    quantity: int,
    books: dict[str, OrderBook],
    state: PaperAccountState,
    now_ms: int,
    max_book_age_seconds: float,
) -> SimulationResult:
    """Close (part of) an open position with an offsetting IOC trade.

    A long is closed by selling into the bids; a short is closed by
    buying from the asks. No limit price: the close takes whatever
    displayed depth exists, so a thin book means a partial close and
    the remainder stays open. A short close is rejected when cash
    cannot cover the buy-back cost.
    """

    if quantity < 1:
        raise ValueError("quantity must be at least 1")
    if quantity > position.quantity:
        raise ValueError("cannot close more than the open quantity")
    book = books.get(position.ticker)
    if book is None or book_age_ms(book, now_ms) > max_book_age_seconds * 1_000:
        return SimulationResult(status="rejected", reject_reason="stale_book")

    closing_sell = position.direction == "buy"
    levels = _execution_levels(
        position.side, "sell" if closing_sell else "buy", book
    )
    filled, total = walk_book(levels, None, quantity, buy=not closing_sell)
    if filled == 0:
        return SimulationResult(status="rejected", reject_reason="no_liquidity")
    if not closing_sell and total > state.cash_cents:
        return SimulationResult(
            status="rejected", reject_reason="insufficient_cash"
        )

    # Pro-rata share of the position's cost basis for the closed part.
    # Integer division keeps at most 1 cent of rounding per partial
    # close; the remainder stays in the open position's cost basis.
    closed_cost = (position.cost_basis_cents * filled) // position.quantity
    if closing_sell:
        cash_delta = total
        realized = total - closed_cost
        margin_released = 0
    else:
        cash_delta = -total
        realized = closed_cost - total
        margin_released = filled * 100 - closed_cost

    order_id = str(uuid.uuid4())
    order = PaperOrder(
        id=order_id,
        opportunity_id=None,
        created_at_ms=now_ms,
        ticker=position.ticker,
        side=position.side,
        direction="sell" if closing_sell else "buy",
        # Closing trades take displayed depth without a limit; record
        # the executed VWAP as the order price for the audit trail.
        limit_price_cents=vwap_cents(total, filled),
        requested_quantity=quantity,
        status="filled" if filled == quantity else "partial",
    )
    fill = PaperFill(
        order_id=order_id,
        filled_at_ms=now_ms,
        fill_price_cents=vwap_cents(total, filled),
        filled_quantity=filled,
        total_cents=total,
        book_timestamp_ms=now_ms - book_age_ms(book, now_ms),
    )
    events = [
        LedgerEvent(
            ts_ms=now_ms,
            event_type="CLOSE",
            order_id=order_id,
            ticker=position.ticker,
            cash_delta_cents=cash_delta,
            reserved_delta_cents=-margin_released,
            realized_pnl_cents=realized,
            details={
                "closed_quantity": filled,
                "closed_cost_cents": closed_cost,
                "close_reason": "offset",
            },
        )
    ]
    if filled < quantity:
        events.append(
            LedgerEvent(
                ts_ms=now_ms,
                event_type="ORDER_EXPIRED",
                order_id=order_id,
                ticker=position.ticker,
                details={
                    "unfilled_quantity": quantity - filled,
                    "reason": "ioc_unfilled",
                },
            )
        )

    new_state = PaperAccountState(
        initial_cash_cents=state.initial_cash_cents,
        cash_cents=state.cash_cents + cash_delta,
        realized_pnl_cents=state.realized_pnl_cents + realized,
        positions={
            key: copy.copy(item) for key, item in state.positions.items()
        },
    )
    remaining = copy.copy(position)
    remaining.quantity -= filled
    remaining.cost_basis_cents -= closed_cost
    closed: list[ClosedPosition] = []
    if remaining.quantity == 0:
        closed.append(
            ClosedPosition(
                position=remaining,
                realized_pnl_cents=realized,
                closed_at_ms=now_ms,
                close_reason="offset",
            )
        )
        del new_state.positions[position.key]
    else:
        new_state.positions[position.key] = remaining

    snapshot = _snapshot(new_state, books, now_ms, max_book_age_seconds)
    return SimulationResult(
        status="accepted",
        orders=[order],
        fills=[fill],
        events=events,
        closed=closed,
        state=new_state,
        snapshot=snapshot,
    )
