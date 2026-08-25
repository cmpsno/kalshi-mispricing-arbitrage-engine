"""Asynchronous SQLite persistence using parameterized SQL."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Self

import aiosqlite

from .models import ArbitrageOpportunity, Event, Market, OrderBook, Trade

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_ticker TEXT PRIMARY KEY,
    series_ticker TEXT,
    title TEXT,
    subtitle TEXT,
    status TEXT,
    close_time INTEGER,
    mutually_exclusive BOOLEAN NOT NULL DEFAULT 0,
    updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS markets (
    ticker TEXT PRIMARY KEY,
    event_ticker TEXT,
    series_ticker TEXT,
    title TEXT,
    subtitle TEXT,
    status TEXT,
    yes_bid INTEGER,
    yes_ask INTEGER,
    no_bid INTEGER,
    no_ask INTEGER,
    last_price INTEGER,
    strike_price NUMERIC,
    event_mutually_exclusive BOOLEAN NOT NULL DEFAULT 0,
    updated_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_markets_event ON markets(event_ticker);
CREATE INDEX IF NOT EXISTS idx_markets_series ON markets(series_ticker);
CREATE TABLE IF NOT EXISTS order_books (
    ticker TEXT,
    timestamp INTEGER,
    bids_json TEXT,
    asks_json TEXT,
    PRIMARY KEY (ticker, timestamp)
);
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    ticker TEXT,
    price INTEGER,
    size INTEGER,
    side TEXT,
    timestamp INTEGER
);
CREATE TABLE IF NOT EXISTS opportunities (
    id TEXT PRIMARY KEY,
    detected_at INTEGER,
    algorithm TEXT,
    markets_involved TEXT,
    action TEXT,
    gross_profit_cents INTEGER,
    required_collateral_cents INTEGER,
    confidence_score REAL,
    details_json TEXT,
    executed BOOLEAN DEFAULT 0
);
"""


def _to_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1_000)


def _from_ms(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1_000, tz=UTC)


class Database:
    def __init__(self, path: str | Path = "kalshi_arbitrage.db") -> None:
        self.path = Path(path)
        self._connection: aiosqlite.Connection | None = None
        self._initialize_lock = asyncio.Lock()

    async def __aenter__(self) -> Self:
        await self.initialize()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def initialize(self) -> None:
        if self._connection is not None:
            return
        async with self._initialize_lock:
            if self._connection is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = await aiosqlite.connect(self.path)
            self._connection.row_factory = aiosqlite.Row
            await self._connection.execute("PRAGMA journal_mode=WAL")
            await self._connection.execute("PRAGMA foreign_keys=ON")
            await self._connection.executescript(SCHEMA)
            await self._connection.commit()

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def _db(self) -> aiosqlite.Connection:
        await self.initialize()
        assert self._connection is not None
        return self._connection

    async def upsert_event(self, event: Event) -> None:
        db = await self._db()
        await db.execute(
            """
            INSERT INTO events (
                event_ticker, series_ticker, title, subtitle, status,
                close_time, mutually_exclusive, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_ticker) DO UPDATE SET
                series_ticker=excluded.series_ticker,
                title=excluded.title,
                subtitle=excluded.subtitle,
                status=excluded.status,
                close_time=excluded.close_time,
                mutually_exclusive=excluded.mutually_exclusive,
                updated_at=excluded.updated_at
            """,
            (
                event.event_ticker,
                event.series_ticker,
                event.title,
                event.subtitle,
                event.status,
                _to_ms(event.close_time),
                event.mutually_exclusive,
                _to_ms(datetime.now(UTC)),
            ),
        )
        await db.commit()

    async def upsert_market(self, market: Market) -> None:
        db = await self._db()
        await db.execute(
            """
            INSERT INTO markets (
                ticker, event_ticker, series_ticker, title, subtitle, status,
                yes_bid, yes_ask, no_bid, no_ask, last_price, strike_price,
                event_mutually_exclusive, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                event_ticker=excluded.event_ticker,
                series_ticker=excluded.series_ticker,
                title=excluded.title,
                subtitle=excluded.subtitle,
                status=excluded.status,
                yes_bid=excluded.yes_bid,
                yes_ask=excluded.yes_ask,
                no_bid=excluded.no_bid,
                no_ask=excluded.no_ask,
                last_price=excluded.last_price,
                strike_price=excluded.strike_price,
                event_mutually_exclusive=excluded.event_mutually_exclusive,
                updated_at=excluded.updated_at
            """,
            (
                market.ticker,
                market.event_ticker,
                market.series_ticker,
                market.title,
                market.subtitle,
                market.status,
                market.yes_bid,
                market.yes_ask,
                market.no_bid,
                market.no_ask,
                market.last_price,
                str(market.strike_price) if market.strike_price is not None else None,
                market.event_mutually_exclusive,
                _to_ms(datetime.now(UTC)),
            ),
        )
        await db.commit()

    async def insert_order_book(self, order_book: OrderBook) -> None:
        db = await self._db()
        bids = [[level.price, level.size] for level in order_book.bids]
        asks = [[level.price, level.size] for level in order_book.asks]
        await db.execute(
            """
            INSERT OR REPLACE INTO order_books
                (ticker, timestamp, bids_json, asks_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                order_book.ticker,
                _to_ms(order_book.timestamp),
                json.dumps(bids, separators=(",", ":")),
                json.dumps(asks, separators=(",", ":")),
            ),
        )
        await db.commit()

    async def insert_trade(self, trade: Trade) -> None:
        db = await self._db()
        await db.execute(
            """
            INSERT OR IGNORE INTO trades
                (trade_id, ticker, price, size, side, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                trade.trade_id,
                trade.ticker,
                trade.price,
                trade.size,
                trade.side,
                _to_ms(trade.timestamp),
            ),
        )
        await db.commit()

    @staticmethod
    def _market_from_row(row: aiosqlite.Row) -> Market:
        strike = row["strike_price"]
        return Market(
            ticker=row["ticker"],
            event_ticker=row["event_ticker"],
            series_ticker=row["series_ticker"] or "",
            title=row["title"] or "",
            subtitle=row["subtitle"] or "",
            status=row["status"] or "active",
            yes_bid=row["yes_bid"] or 0,
            yes_ask=row["yes_ask"] or 0,
            no_bid=row["no_bid"] or 0,
            no_ask=row["no_ask"] or 0,
            last_price=row["last_price"],
            strike_price=Decimal(str(strike)) if strike is not None else None,
            event_mutually_exclusive=bool(row["event_mutually_exclusive"]),
        )

    async def _get_markets(self, where: str, value: str) -> list[Market]:
        db = await self._db()
        cursor = await db.execute(
            f"SELECT * FROM markets WHERE {where} = ? ORDER BY ticker", (value,)
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [self._market_from_row(row) for row in rows]

    async def get_markets_by_event(self, event_ticker: str) -> list[Market]:
        return await self._get_markets("event_ticker", event_ticker)

    async def get_markets_by_series(self, series_ticker: str) -> list[Market]:
        return await self._get_markets("series_ticker", series_ticker)

    async def get_market(self, ticker: str) -> Market:
        db = await self._db()
        cursor = await db.execute("SELECT * FROM markets WHERE ticker = ?", (ticker,))
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            raise KeyError(f"market not found: {ticker}")
        return self._market_from_row(row)

    async def get_active_markets(self) -> list[Market]:
        db = await self._db()
        cursor = await db.execute(
            "SELECT * FROM markets WHERE status IN ('active', 'open') ORDER BY ticker"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [self._market_from_row(row) for row in rows]

    async def get_mutually_exclusive_event_tickers(self) -> list[str]:
        db = await self._db()
        cursor = await db.execute(
            "SELECT event_ticker FROM events WHERE mutually_exclusive = 1"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [str(row["event_ticker"]) for row in rows]

    async def save_opportunity(self, opp: ArbitrageOpportunity) -> None:
        db = await self._db()
        await db.execute(
            """
            INSERT OR IGNORE INTO opportunities (
                id, detected_at, algorithm, markets_involved, action,
                gross_profit_cents, required_collateral_cents,
                confidence_score, details_json, executed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                opp.id,
                _to_ms(opp.detected_at),
                opp.algorithm,
                ",".join(opp.markets_involved),
                opp.action,
                opp.gross_profit_cents,
                opp.required_collateral_cents,
                opp.confidence_score,
                json.dumps(opp.details, separators=(",", ":"), default=str),
            ),
        )
        await db.commit()

    async def get_recent_opportunities(
        self, limit: int = 100
    ) -> list[ArbitrageOpportunity]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        db = await self._db()
        cursor = await db.execute(
            """
            SELECT * FROM opportunities
            ORDER BY detected_at DESC LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            ArbitrageOpportunity(
                id=row["id"],
                detected_at=_from_ms(row["detected_at"]),
                algorithm=row["algorithm"],
                markets_involved=str(row["markets_involved"]).split(","),
                action=row["action"],
                gross_profit_cents=row["gross_profit_cents"],
                required_collateral_cents=row["required_collateral_cents"],
                confidence_score=row["confidence_score"],
                details=json.loads(row["details_json"]),
            )
            for row in rows
        ]

    async def mark_opportunity_executed(self, opportunity_id: str) -> None:
        db = await self._db()
        await db.execute(
            "UPDATE opportunities SET executed = 1 WHERE id = ?", (opportunity_id,)
        )
        await db.commit()

    async def table_count(self, table: str) -> int:
        if table not in {"events", "markets", "order_books", "trades", "opportunities"}:
            raise ValueError("unsupported table")
        db = await self._db()
        cursor = await db.execute(f"SELECT COUNT(*) AS count FROM {table}")
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["count"])
