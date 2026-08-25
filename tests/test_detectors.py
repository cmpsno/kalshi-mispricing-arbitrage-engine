from __future__ import annotations

from decimal import Decimal

from src.arbitrage.detectors import (
    detect_same_market_cross,
    detect_series_sum_deviation,
    detect_strike_monotonicity_violations,
)
from src.arbitrage.risk import calculate_max_quantity
from src.models import Market


def market(ticker: str, **overrides) -> Market:
    values = {
        "ticker": ticker,
        "event_ticker": "EVENT-1",
        "series_ticker": "SERIES",
        "title": ticker,
        "status": "active",
    }
    values.update(overrides)
    return Market(**values)


def test_same_market_buy_both_complements_for_five_cent_profit() -> None:
    opportunities = detect_same_market_cross([market("BINARY", yes_ask=45, no_ask=50)])

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.action == "buy_yes_buy_no"
    assert opportunity.gross_profit_cents == 5
    assert opportunity.required_collateral_cents == 95


def test_yes_ask_below_no_bid_alone_is_not_binary_arbitrage() -> None:
    # This was the stale formula in the supplied spec. These are different
    # contract sides, so 55 <= 60 doesn't lock in a payout.
    assert detect_same_market_cross([market("BINARY", yes_ask=55, no_bid=60)]) == []


def test_same_market_sell_both_bids() -> None:
    opportunities = detect_same_market_cross([market("BINARY", yes_bid=60, no_bid=45)])

    assert opportunities[0].action == "sell_yes_sell_no"
    assert opportunities[0].gross_profit_cents == 5


def test_mutually_exclusive_sum_buys_all_outcomes() -> None:
    markets = [market(f"OUTCOME-{index}", yes_ask=30) for index in range(3)]

    opportunities = detect_series_sum_deviation(markets, "SERIES")

    assert len(opportunities) == 1
    assert opportunities[0].action == "buy_all_outcomes"
    assert opportunities[0].gross_profit_cents == 10
    assert opportunities[0].required_collateral_cents == 90


def test_sum_detector_never_combines_different_events() -> None:
    markets = [
        market("A", event_ticker="EVENT-A", yes_ask=30),
        market("B", event_ticker="EVENT-B", yes_ask=30),
        market("C", event_ticker="EVENT-B", yes_ask=70),
    ]

    assert detect_series_sum_deviation(markets, "SERIES") == []


def test_strike_monotonicity_buys_lower_and_sells_higher() -> None:
    markets = [
        market("LOW", strike_price=Decimal(100), yes_ask=60),
        market("HIGH", strike_price=Decimal(105), yes_bid=65),
    ]

    opportunities = detect_strike_monotonicity_violations(markets)

    assert len(opportunities) == 1
    assert opportunities[0].action == "buy_spread"
    assert opportunities[0].gross_profit_cents == 5
    assert opportunities[0].required_collateral_cents == 95


def test_position_size_adds_five_percent_collateral_buffer() -> None:
    opportunity = detect_same_market_cross([market("BINARY", yes_ask=45, no_ask=50)])[0]

    assert calculate_max_quantity(opportunity, 1_000) == 10
