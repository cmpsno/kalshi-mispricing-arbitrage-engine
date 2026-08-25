from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from src.models import Event, Market, Trade, dollars_to_cents


def test_fixed_point_dollars_are_converted_without_floats() -> None:
    assert dollars_to_cents("0.6500") == 65
    assert dollars_to_cents(Decimal("0.0500")) == 5


def test_event_adapter_uses_current_sub_title_and_flags() -> None:
    event = Event.from_api(
        {
            "event_ticker": "EVENT",
            "series_ticker": "SERIES",
            "title": "Title",
            "sub_title": "Subtitle",
            "mutually_exclusive": True,
        }
    )

    assert event.subtitle == "Subtitle"
    assert event.status == "open"
    assert event.mutually_exclusive is True


def test_market_adapter_uses_current_fixed_point_fields_and_strike() -> None:
    market = Market.from_api(
        {
            "ticker": "SERIES-EVENT-T105.5",
            "event_ticker": "EVENT",
            "title": "Above 105.5",
            "status": "active",
            "yes_bid_dollars": "0.6000",
            "yes_ask_dollars": "0.6500",
            "no_bid_dollars": "0.3500",
            "no_ask_dollars": "0.4000",
            "last_price_dollars": "0.6200",
        },
        series_ticker="SERIES",
    )

    assert market.yes_bid == 60
    assert market.yes_ask == 65
    assert market.no_bid == 35
    assert market.strike_price == Decimal("105.5")


def test_trade_adapter_uses_current_book_side() -> None:
    trade = Trade.from_api(
        {
            "ticker": "MARKET",
            "trade_id": "trade-1",
            "yes_price_dollars": "0.4200",
            "count_fp": "3.00",
            "taker_book_side": "ask",
            "created_time": "2026-08-25T12:00:00Z",
        }
    )

    assert trade.price == 42
    assert trade.size == 3
    assert trade.side == "sell"
    assert trade.timestamp == datetime(2026, 8, 25, 12, tzinfo=UTC)
