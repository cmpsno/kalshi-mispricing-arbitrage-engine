from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.arbitrage.detectors import scan_all_opportunities
from src.models import (
    ArbitrageOpportunity,
    Event,
    Market,
    OrderBook,
    OrderBookLevel,
    Trade,
)
from src.storage import Database


@pytest.mark.asyncio
async def test_database_round_trips_engine_models(tmp_path: Path) -> None:
    async with Database(tmp_path / "engine.db") as db:
        event = Event(
            event_ticker="EVENT",
            series_ticker="SERIES",
            title="Event",
            status="open",
            mutually_exclusive=True,
        )
        market = Market(
            ticker="MARKET",
            event_ticker="EVENT",
            series_ticker="SERIES",
            title="Market",
            status="active",
            yes_bid=40,
            yes_ask=45,
            no_bid=45,
            no_ask=50,
            event_mutually_exclusive=True,
        )
        await db.upsert_event(event)
        await db.upsert_market(market)

        stored = await db.get_market("MARKET")
        assert stored == market
        assert await db.get_markets_by_event("EVENT") == [market]
        assert await db.get_markets_by_series("SERIES") == [market]
        assert await db.get_mutually_exclusive_event_tickers() == ["EVENT"]

        order_book = OrderBook(
            ticker="MARKET",
            timestamp=datetime(2026, 8, 25, tzinfo=UTC),
            bids=[OrderBookLevel(price=40, size=2)],
            asks=[OrderBookLevel(price=45, size=3)],
        )
        trade = Trade(
            ticker="MARKET",
            trade_id="trade-1",
            price=42,
            size=1,
            side="buy",
            timestamp=datetime(2026, 8, 25, tzinfo=UTC),
        )
        await db.insert_order_book(order_book)
        await db.insert_trade(trade)

        opportunity = ArbitrageOpportunity(
            id="opportunity-1",
            detected_at=datetime(2026, 8, 25, tzinfo=UTC),
            algorithm="same_market_cross",
            markets_involved=["MARKET"],
            action="buy_yes_buy_no",
            gross_profit_cents=5,
            required_collateral_cents=95,
            confidence_score=0.5,
            details={"yes_ask": 45, "no_ask": 50},
        )
        await db.save_opportunity(opportunity)

        assert await db.table_count("events") == 1
        assert await db.table_count("markets") == 1
        assert await db.table_count("order_books") == 1
        assert await db.table_count("trades") == 1
        assert await db.get_recent_opportunities() == [opportunity]


@pytest.mark.asyncio
async def test_scanner_saves_detected_opportunities(tmp_path: Path) -> None:
    async with Database(tmp_path / "scanner.db") as db:
        await db.upsert_market(
            Market(
                ticker="MARKET",
                event_ticker="EVENT",
                series_ticker="SERIES",
                status="active",
                yes_ask=45,
                no_ask=50,
            )
        )

        opportunities = await scan_all_opportunities(db)

        assert len(opportunities) == 1
        assert await db.table_count("opportunities") == 1
