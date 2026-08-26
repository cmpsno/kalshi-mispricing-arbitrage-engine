"""Validated internal models and current Kalshi API adapters."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


def dollars_to_cents(value: str | Decimal | float | None) -> int:
    """Convert a fixed-point dollar value to whole cents without binary floats."""

    if value in (None, ""):
        return 0
    cents = Decimal(str(value)) * 100
    return int(cents.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def count_to_int(value: str | Decimal | float | None) -> int:
    if value in (None, ""):
        return 0
    return int(Decimal(str(value)))


def _strike_from_payload(payload: dict[str, Any]) -> Decimal | None:
    for key in ("strike_price", "floor_strike", "cap_strike"):
        value = payload.get(key)
        if value not in (None, ""):
            try:
                return Decimal(str(value))
            except (InvalidOperation, ValueError):
                pass

    ticker = str(payload.get("ticker", ""))
    match = re.search(r"-T(-?\d+(?:\.\d+)?)$", ticker)
    if match:
        return Decimal(match.group(1))

    title = str(payload.get("title", ""))
    if re.search(
        r"\b(above|below|over|under|at least|more than|less than)\b",
        title,
        re.IGNORECASE,
    ):
        match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", title)
        if match:
            return Decimal(match.group(0).replace(",", ""))
    return None


class EngineModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Event(EngineModel):
    event_ticker: str
    series_ticker: str = ""
    title: str = ""
    subtitle: str = ""
    status: str = "open"
    close_time: datetime | None = None
    markets: list[str] = Field(default_factory=list)
    mutually_exclusive: bool = False

    @classmethod
    def from_api(cls, payload: dict[str, Any], *, status: str = "open") -> Event:
        nested_markets = payload.get("markets") or []
        tickers = [
            item["ticker"] if isinstance(item, dict) else str(item)
            for item in nested_markets
            if item
        ]
        close_times = [
            item.get("close_time")
            for item in nested_markets
            if isinstance(item, dict) and item.get("close_time")
        ]
        return cls(
            event_ticker=payload["event_ticker"],
            series_ticker=payload.get("series_ticker", ""),
            title=payload.get("title", ""),
            subtitle=payload.get("sub_title", payload.get("subtitle", "")),
            status=payload.get("status", status),
            close_time=max(close_times) if close_times else payload.get("close_time"),
            markets=tickers,
            mutually_exclusive=bool(payload.get("mutually_exclusive", False)),
        )


class Market(EngineModel):
    ticker: str
    event_ticker: str
    series_ticker: str = ""
    title: str = ""
    subtitle: str = ""
    status: str = "active"
    yes_bid: int = 0
    yes_ask: int = 0
    no_bid: int = 0
    no_ask: int = 0
    last_price: int | None = None
    strike_price: Decimal | None = None
    event_mutually_exclusive: bool = False

    @classmethod
    def from_api(
        cls,
        payload: dict[str, Any],
        *,
        series_ticker: str = "",
        event_mutually_exclusive: bool = False,
    ) -> Market:
        last_price = payload.get("last_price_dollars")
        return cls(
            ticker=payload["ticker"],
            event_ticker=payload.get("event_ticker", ""),
            series_ticker=payload.get("series_ticker", series_ticker),
            title=payload.get("title", ""),
            subtitle=payload.get("subtitle", payload.get("yes_sub_title", "")),
            status=payload.get("status", "active"),
            yes_bid=dollars_to_cents(
                payload.get("yes_bid_dollars", payload.get("yes_bid"))
            ),
            yes_ask=dollars_to_cents(
                payload.get("yes_ask_dollars", payload.get("yes_ask"))
            ),
            no_bid=dollars_to_cents(
                payload.get("no_bid_dollars", payload.get("no_bid"))
            ),
            no_ask=dollars_to_cents(
                payload.get("no_ask_dollars", payload.get("no_ask"))
            ),
            last_price=(
                dollars_to_cents(last_price) if last_price not in (None, "") else None
            ),
            strike_price=_strike_from_payload(payload),
            event_mutually_exclusive=event_mutually_exclusive,
        )


class OrderBookLevel(EngineModel):
    price: int
    size: int


class OrderBook(EngineModel):
    ticker: str
    timestamp: datetime = Field(default_factory=utc_now)
    bids: list[OrderBookLevel] = Field(default_factory=list)
    asks: list[OrderBookLevel] = Field(default_factory=list)


class Trade(EngineModel):
    ticker: str
    trade_id: str
    price: int
    size: int
    side: Literal["buy", "sell"]
    timestamp: datetime

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> Trade:
        timestamp = payload.get("created_time")
        if timestamp is None and payload.get("ts_ms") is not None:
            timestamp = datetime.fromtimestamp(payload["ts_ms"] / 1000, tz=UTC)
        if timestamp is None and payload.get("ts") is not None:
            timestamp = datetime.fromtimestamp(payload["ts"], tz=UTC)
        book_side = payload.get("taker_book_side", "bid")
        return cls(
            ticker=payload.get("ticker", payload.get("market_ticker", "")),
            trade_id=payload["trade_id"],
            price=dollars_to_cents(
                payload.get("yes_price_dollars", payload.get("yes_price"))
            ),
            size=count_to_int(payload.get("count_fp", payload.get("count"))),
            side="buy" if book_side == "bid" else "sell",
            timestamp=timestamp or utc_now(),
        )


OpportunityAlgorithm = Literal[
    "same_market_cross", "mutually_exclusive_sum", "strike_monotonicity"
]
OpportunityAction = Literal[
    "buy_yes_buy_no",
    "sell_yes_sell_no",
    "buy_all_outcomes",
    "sell_all_outcomes",
    "buy_spread",
    "sell_spread",
]


class ArbitrageOpportunity(EngineModel):
    id: str
    detected_at: datetime = Field(default_factory=utc_now)
    algorithm: OpportunityAlgorithm
    markets_involved: list[str]
    action: OpportunityAction
    gross_profit_cents: int
    required_collateral_cents: int
    confidence_score: float = Field(ge=0.0, le=1.0)
    details: dict[str, Any] = Field(default_factory=dict)


OrderAction = Literal["buy", "sell"]
OrderSide = Literal["yes", "no"]
ExecutionStatus = Literal["dry_run", "filled", "partial", "rejected"]


class OrderRequest(EngineModel):
    ticker: str = Field(min_length=1)
    action: OrderAction
    side: OrderSide
    price: int = Field(ge=1, le=99)
    quantity: int = Field(ge=1)
    client_order_id: str = Field(min_length=1)

    def to_api_payload(self) -> dict[str, Any]:
        if (self.action, self.side) in {("buy", "yes"), ("sell", "no")}:
            book_side = "bid"
        else:
            book_side = "ask"
        yes_price = self.price if self.side == "yes" else 100 - self.price
        payload: dict[str, Any] = {
            "ticker": self.ticker,
            "client_order_id": self.client_order_id,
            "side": book_side,
            "count": f"{self.quantity}.00",
            "price": f"{yes_price / 100:.4f}",
            "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross",
            "cancel_order_on_pause": True,
        }
        return payload


class OrderExecutionResult(EngineModel):
    client_order_id: str
    order_id: str | None = None
    ticker: str
    requested_quantity: int
    filled_quantity: int = 0
    remaining_quantity: int = 0
    average_fill_price: str | None = None
    fees: str | None = None
    status: ExecutionStatus
    error_code: str | None = None
    error_message: str | None = None
