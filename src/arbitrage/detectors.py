"""Pure integer-cent structural arbitrage detectors."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime
from itertools import pairwise

from ..models import ArbitrageOpportunity, Market
from ..storage import Database


def is_yes(ticker: str) -> bool:
    """Compatibility helper for synthetic fixtures; live Kalshi uses contract sides."""

    return ticker.endswith("-Y")


def is_no(ticker: str) -> bool:
    return ticker.endswith("-N")


def base_ticker(ticker: str) -> str:
    return ticker[:-2] if is_yes(ticker) or is_no(ticker) else ticker


def _confidence(edge_cents: int) -> float:
    return min(1.0, max(0.0, edge_cents / 10.0))


def _opportunity(
    *,
    algorithm: str,
    markets: list[str],
    action: str,
    profit: int,
    collateral: int,
    details: dict,
) -> ArbitrageOpportunity:
    return ArbitrageOpportunity(
        id=str(uuid.uuid4()),
        detected_at=datetime.now(UTC),
        algorithm=algorithm,
        markets_involved=markets,
        action=action,
        gross_profit_cents=profit,
        required_collateral_cents=collateral,
        confidence_score=_confidence(profit),
        details=details,
    )


def detect_same_market_cross(markets: list[Market]) -> list[ArbitrageOpportunity]:
    """Detect executable complement inconsistencies within each binary market.

    Kalshi exposes YES and NO as two sides of one market, not as separate ``-Y``
    and ``-N`` tickers. Buying both asks is profitable below 100 cents; selling
    both bids is profitable above 100 cents.
    """

    opportunities: list[ArbitrageOpportunity] = []
    for market in markets:
        ask_total = market.yes_ask + market.no_ask
        if market.yes_ask > 0 and market.no_ask > 0 and ask_total < 100:
            profit = 100 - ask_total
            opportunities.append(
                _opportunity(
                    algorithm="same_market_cross",
                    markets=[market.ticker],
                    action="buy_yes_buy_no",
                    profit=profit,
                    collateral=ask_total,
                    details={
                        "yes_ask": market.yes_ask,
                        "no_ask": market.no_ask,
                        "legs": [
                            {
                                "ticker": market.ticker,
                                "side": "buy_yes",
                                "price": market.yes_ask,
                            },
                            {
                                "ticker": market.ticker,
                                "side": "buy_no",
                                "price": market.no_ask,
                            },
                        ],
                    },
                )
            )

        bid_total = market.yes_bid + market.no_bid
        if market.yes_bid > 0 and market.no_bid > 0 and bid_total > 100:
            profit = bid_total - 100
            opportunities.append(
                _opportunity(
                    algorithm="same_market_cross",
                    markets=[market.ticker],
                    action="sell_yes_sell_no",
                    profit=profit,
                    collateral=200 - bid_total,
                    details={
                        "yes_bid": market.yes_bid,
                        "no_bid": market.no_bid,
                        "legs": [
                            {
                                "ticker": market.ticker,
                                "side": "sell_yes",
                                "price": market.yes_bid,
                            },
                            {
                                "ticker": market.ticker,
                                "side": "sell_no",
                                "price": market.no_bid,
                            },
                        ],
                    },
                )
            )
    return opportunities


def detect_series_sum_deviation(
    markets: list[Market], series_ticker: str
) -> list[ArbitrageOpportunity]:
    """Detect sum-to-$1 deviations, scoped independently to each event.

    The caller must only supply events known to be mutually exclusive and
    collectively exhaustive. A series may contain unrelated dates, so markets
    are never summed across event boundaries.
    """

    filtered = [market for market in markets if market.series_ticker == series_ticker]
    by_event: dict[str, list[Market]] = defaultdict(list)
    for market in filtered:
        by_event[market.event_ticker].append(market)

    opportunities: list[ArbitrageOpportunity] = []
    for event_ticker, event_markets in by_event.items():
        if len(event_markets) < 2:
            continue
        tickers = [market.ticker for market in event_markets]
        asks = [market.yes_ask for market in event_markets]
        if all(price > 0 for price in asks) and sum(asks) < 100:
            ask_total = sum(asks)
            profit = 100 - ask_total
            opportunities.append(
                _opportunity(
                    algorithm="mutually_exclusive_sum",
                    markets=tickers,
                    action="buy_all_outcomes",
                    profit=profit,
                    collateral=ask_total,
                    details={
                        "event_ticker": event_ticker,
                        "sum_yes_asks": ask_total,
                        "legs": [
                            {
                                "ticker": market.ticker,
                                "side": "buy_yes",
                                "price": market.yes_ask,
                            }
                            for market in event_markets
                        ],
                    },
                )
            )

        bids = [market.yes_bid for market in event_markets]
        if all(price > 0 for price in bids) and sum(bids) > 100:
            bid_total = sum(bids)
            profit = bid_total - 100
            opportunities.append(
                _opportunity(
                    algorithm="mutually_exclusive_sum",
                    markets=tickers,
                    action="sell_all_outcomes",
                    profit=profit,
                    collateral=len(event_markets) * 100 - bid_total,
                    details={
                        "event_ticker": event_ticker,
                        "sum_yes_bids": bid_total,
                        "legs": [
                            {
                                "ticker": market.ticker,
                                "side": "sell_yes",
                                "price": market.yes_bid,
                            }
                            for market in event_markets
                        ],
                    },
                )
            )
    return opportunities


def detect_strike_monotonicity_violations(
    markets: list[Market],
) -> list[ArbitrageOpportunity]:
    """Find higher-strike YES bids above executable lower-strike YES asks."""

    ladders: dict[tuple[str, str], list[Market]] = defaultdict(list)
    for market in markets:
        if market.strike_price is not None:
            ladders[(market.series_ticker, market.event_ticker)].append(market)

    opportunities: list[ArbitrageOpportunity] = []
    for (series_ticker, event_ticker), ladder in ladders.items():
        ordered = sorted(ladder, key=lambda market: market.strike_price)
        for lower, higher in pairwise(ordered):
            if lower.yes_ask <= 0 or higher.yes_bid <= lower.yes_ask:
                continue
            profit = higher.yes_bid - lower.yes_ask
            opportunities.append(
                _opportunity(
                    algorithm="strike_monotonicity",
                    markets=[lower.ticker, higher.ticker],
                    action="buy_spread",
                    profit=profit,
                    collateral=lower.yes_ask + (100 - higher.yes_bid),
                    details={
                        "series_ticker": series_ticker,
                        "event_ticker": event_ticker,
                        "lower_strike": str(lower.strike_price),
                        "higher_strike": str(higher.strike_price),
                        "lower_yes_ask": lower.yes_ask,
                        "higher_yes_bid": higher.yes_bid,
                        "legs": [
                            {
                                "ticker": lower.ticker,
                                "side": "buy_yes",
                                "price": lower.yes_ask,
                            },
                            {
                                "ticker": higher.ticker,
                                "side": "sell_yes",
                                "price": higher.yes_bid,
                            },
                        ],
                    },
                )
            )
    return opportunities


async def scan_all_opportunities(db: Database) -> list[ArbitrageOpportunity]:
    markets = await db.get_active_markets()
    opportunities = detect_same_market_cross(markets)

    mutually_exclusive_events = set(await db.get_mutually_exclusive_event_tickers())
    for event_ticker in mutually_exclusive_events:
        event_markets = [
            market for market in markets if market.event_ticker == event_ticker
        ]
        for series_ticker in {market.series_ticker for market in event_markets}:
            opportunities.extend(
                detect_series_sum_deviation(event_markets, series_ticker)
            )

    opportunities.extend(detect_strike_monotonicity_violations(markets))
    for opportunity in opportunities:
        await db.save_opportunity(opportunity)
    return opportunities
