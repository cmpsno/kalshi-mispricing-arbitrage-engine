"""Paper-trading models.

Money is integer cents and quantities are whole contracts, matching the
existing engine models. Fees are ``None`` until a verified fee formula
exists (see PAPER_TRADING_PLAN.md §1): v1 results are gross of fees.

Accounting convention (corrected from the first draft of the plan, which
double-counted short-sale proceeds — see PAPER_TRADING_PLAN.md §2):

- ``cash_cents`` is the actual paper cash balance. A buy decreases it by
  the fill cost; a sell increases it by the fill proceeds; settlement
  pays out / collects $1.00 per contract.
- A position's mark-to-market value is its current exit value: long YES
  at the best YES bid, long NO at the best NO bid (``100 - best YES
  ask``), short YES at ``-best YES ask``, short NO at ``-(100 - best YES
  bid)``.
- ``equity = cash + sum(mark-to-market values of open positions)``.
- ``cost_basis_cents`` is the total cash paid (long) or received (short)
  to open the position. For a long, entry MTM value is ``+cost_basis``;
  for a short it is ``-cost_basis``.
- ``margin`` (reserved collateral, reported separately and *not* added to
  equity) is ``cost_basis`` for a long and ``quantity * 100 -
  cost_basis`` for a short — the cash that must stay aside to cover a
  short's $1.00 payout obligation. Buying power for a new order is
  ``cash - short margin``; a long's cost already left cash, so it needs
  no additional margin.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PaperLeg:
    ticker: str
    side: str  # "yes" | "no"
    direction: str  # "buy" | "sell"
    limit_price_cents: int

    def key(self) -> tuple[str, str, str]:
        return (self.ticker, self.side, self.direction)


@dataclass(slots=True)
class PaperPosition:
    ticker: str
    side: str
    direction: str
    quantity: int
    cost_basis_cents: int
    opened_at_ms: int
    db_id: int | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.ticker, self.side, self.direction)

    @property
    def avg_price_cents(self) -> int:
        """Rounded per-contract entry price, for display only.

        Accounting uses the exact ``cost_basis_cents`` total, never this
        rounded average.
        """

        if self.quantity <= 0:
            return 0
        return (2 * self.cost_basis_cents + self.quantity) // (2 * self.quantity)

    @property
    def entry_value_cents(self) -> int:
        """Mark-to-market value of the position at entry."""

        return self.cost_basis_cents if self.direction == "buy" else -self.cost_basis_cents

    @property
    def margin_cents(self) -> int:
        """Collateral that must stay aside while this position is open."""

        if self.direction == "buy":
            return 0
        return self.quantity * 100 - self.cost_basis_cents


@dataclass(slots=True)
class PaperAccountState:
    """Paper account state, rebuilt by replaying the ledger + positions."""

    initial_cash_cents: int
    cash_cents: int
    realized_pnl_cents: int = 0
    positions: dict[tuple[str, str, str], PaperPosition] = field(default_factory=dict)

    @property
    def short_margin_cents(self) -> int:
        return sum(
            position.margin_cents for position in self.positions.values()
        )

    @property
    def capital_deployed_cents(self) -> int:
        """Cash committed to open positions: long cost + short margin."""

        return sum(
            position.cost_basis_cents
            if position.direction == "buy"
            else position.margin_cents
            for position in self.positions.values()
        )

    @property
    def buying_power_cents(self) -> int:
        return self.cash_cents - self.short_margin_cents


@dataclass(frozen=True, slots=True)
class PaperOrder:
    id: str
    opportunity_id: str | None
    created_at_ms: int
    ticker: str
    side: str
    direction: str
    limit_price_cents: int
    requested_quantity: int
    status: str  # placed/filled/partial/expired/rejected

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "opportunity_id": self.opportunity_id,
            "created_at": self.created_at_ms,
            "ticker": self.ticker,
            "side": self.side,
            "direction": self.direction,
            "limit_price_cents": self.limit_price_cents,
            "requested_quantity": self.requested_quantity,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class PaperFill:
    order_id: str
    filled_at_ms: int
    fill_price_cents: int  # VWAP of the levels consumed, rounded half-up
    filled_quantity: int
    total_cents: int  # exact sum of price * quantity over levels consumed
    book_timestamp_ms: int
    fee_cents: int | None = None  # always None in v1: fees unverified

    def as_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "filled_at": self.filled_at_ms,
            "fill_price_cents": self.fill_price_cents,
            "filled_quantity": self.filled_quantity,
            "total_cents": self.total_cents,
            "book_timestamp": self.book_timestamp_ms,
            "fee_cents": self.fee_cents,
        }


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    ts_ms: int
    event_type: str  # DEPOSIT/ORDER_PLACED/FILL/ORDER_EXPIRED/REJECTED/CLOSE
    order_id: str | None = None
    ticker: str | None = None
    cash_delta_cents: int = 0
    reserved_delta_cents: int = 0
    realized_pnl_cents: int = 0
    details: dict | None = None
