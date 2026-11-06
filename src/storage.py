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
CREATE TABLE IF NOT EXISTS execution_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id TEXT NOT NULL,
    attempted_at INTEGER NOT NULL,
    status TEXT NOT NULL,
    results_json TEXT NOT NULL,
    FOREIGN KEY (opportunity_id) REFERENCES opportunities(id)
);
CREATE INDEX IF NOT EXISTS idx_execution_attempts_opportunity
    ON execution_attempts(opportunity_id);
CREATE TABLE IF NOT EXISTS paper_accounts (
    id INTEGER PRIMARY KEY,
    initial_cash_cents INTEGER NOT NULL CHECK (initial_cash_cents > 0),
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_orders (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT,
    created_at INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    direction TEXT NOT NULL,
    limit_price_cents INTEGER NOT NULL,
    requested_quantity INTEGER NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    filled_at INTEGER NOT NULL,
    fill_price_cents INTEGER NOT NULL,
    filled_quantity INTEGER NOT NULL,
    total_cents INTEGER NOT NULL,
    book_timestamp INTEGER NOT NULL,
    fee_cents INTEGER,
    FOREIGN KEY (order_id) REFERENCES paper_orders(id)
);
CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    direction TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    cost_basis_cents INTEGER NOT NULL,
    opened_at INTEGER NOT NULL,
    closed_at INTEGER,
    realized_pnl_cents INTEGER,
    close_reason TEXT
);
CREATE TABLE IF NOT EXISTS paper_ledger_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    order_id TEXT,
    ticker TEXT,
    cash_delta_cents INTEGER NOT NULL DEFAULT 0,
    reserved_delta_cents INTEGER NOT NULL DEFAULT 0,
    realized_pnl_cents INTEGER NOT NULL DEFAULT 0,
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS paper_equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    equity_cents INTEGER NOT NULL,
    cash_cents INTEGER NOT NULL,
    margin_cents INTEGER NOT NULL,
    unrealized_pnl_cents INTEGER NOT NULL,
    realized_pnl_cents INTEGER NOT NULL
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

    async def save_execution_attempt(
        self,
        opportunity_id: str,
        status: str,
        results: list[dict],
    ) -> None:
        if status not in {"filled", "partial", "rejected"}:
            raise ValueError("unsupported execution status")
        db = await self._db()
        await db.execute(
            """
            INSERT INTO execution_attempts (
                opportunity_id, attempted_at, status, results_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                opportunity_id,
                _to_ms(datetime.now(UTC)),
                status,
                json.dumps(results, separators=(",", ":"), default=str),
            ),
        )
        await db.commit()

    # ------------------------------------------------------------------
    # Paper trading persistence (Phase 3). Amounts are integer cents;
    # timestamps are ms epoch UTC. The ledger is append-only: events are
    # inserted, never updated or deleted. Positions are a materialized
    # view of fills/closes; cash is derived from ledger deltas.
    # ------------------------------------------------------------------

    async def get_paper_account(self) -> dict | None:
        db = await self._db()
        cursor = await db.execute(
            "SELECT * FROM paper_accounts ORDER BY id LIMIT 1"
        )
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row is not None else None

    async def create_paper_account(
        self, initial_cash_cents: int, created_at_ms: int
    ) -> int:
        if initial_cash_cents <= 0:
            raise ValueError("initial paper cash must be greater than zero")
        db = await self._db()
        cursor = await db.execute(
            """
            INSERT INTO paper_accounts (initial_cash_cents, created_at)
            VALUES (?, ?)
            """,
            (initial_cash_cents, created_at_ms),
        )
        await db.commit()
        return int(cursor.lastrowid)

    async def save_paper_ledger_event(
        self,
        ts_ms: int,
        event_type: str,
        *,
        order_id: str | None = None,
        ticker: str | None = None,
        cash_delta_cents: int = 0,
        reserved_delta_cents: int = 0,
        realized_pnl_cents: int = 0,
        details: dict | None = None,
    ) -> int:
        db = await self._db()
        cursor = await db.execute(
            """
            INSERT INTO paper_ledger_events (
                ts, event_type, order_id, ticker, cash_delta_cents,
                reserved_delta_cents, realized_pnl_cents, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts_ms,
                event_type,
                order_id,
                ticker,
                cash_delta_cents,
                reserved_delta_cents,
                realized_pnl_cents,
                json.dumps(details or {}, separators=(",", ":"), default=str),
            ),
        )
        await db.commit()
        return int(cursor.lastrowid)

    async def get_paper_ledger_events(
        self,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[dict]:
        db = await self._db()
        query = "SELECT * FROM paper_ledger_events"
        params: list[int] = []
        clauses: list[str] = []
        if start_ms is not None:
            clauses.append("ts >= ?")
            params.append(start_ms)
        if end_ms is not None:
            clauses.append("ts < ?")
            params.append(end_ms)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id"
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def save_paper_order(self, order: dict) -> None:
        db = await self._db()
        await db.execute(
            """
            INSERT INTO paper_orders (
                id, opportunity_id, created_at, ticker, side, direction,
                limit_price_cents, requested_quantity, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order["id"],
                order.get("opportunity_id"),
                order["created_at"],
                order["ticker"],
                order["side"],
                order["direction"],
                order["limit_price_cents"],
                order["requested_quantity"],
                order["status"],
            ),
        )
        await db.commit()

    async def update_paper_order_status(self, order_id: str, status: str) -> None:
        db = await self._db()
        await db.execute(
            "UPDATE paper_orders SET status = ? WHERE id = ?",
            (status, order_id),
        )
        await db.commit()

    async def get_paper_orders(
        self,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[dict]:
        db = await self._db()
        query = "SELECT * FROM paper_orders"
        params: list[int] = []
        clauses: list[str] = []
        if start_ms is not None:
            clauses.append("created_at >= ?")
            params.append(start_ms)
        if end_ms is not None:
            clauses.append("created_at < ?")
            params.append(end_ms)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at, id"
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def save_paper_fill(self, fill: dict) -> int:
        db = await self._db()
        cursor = await db.execute(
            """
            INSERT INTO paper_fills (
                order_id, filled_at, fill_price_cents, filled_quantity,
                total_cents, book_timestamp, fee_cents
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill["order_id"],
                fill["filled_at"],
                fill["fill_price_cents"],
                fill["filled_quantity"],
                fill["total_cents"],
                fill["book_timestamp"],
                fill.get("fee_cents"),
            ),
        )
        await db.commit()
        return int(cursor.lastrowid)

    async def get_paper_fills(
        self,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[dict]:
        db = await self._db()
        query = "SELECT * FROM paper_fills"
        params: list[int] = []
        clauses: list[str] = []
        if start_ms is not None:
            clauses.append("filled_at >= ?")
            params.append(start_ms)
        if end_ms is not None:
            clauses.append("filled_at < ?")
            params.append(end_ms)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id"
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def get_open_paper_position(
        self, ticker: str, side: str, direction: str
    ) -> dict | None:
        db = await self._db()
        cursor = await db.execute(
            """
            SELECT * FROM paper_positions
            WHERE ticker = ? AND side = ? AND direction = ?
              AND closed_at IS NULL
            ORDER BY id LIMIT 1
            """,
            (ticker, side, direction),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row is not None else None

    async def list_open_paper_positions(self) -> list[dict]:
        db = await self._db()
        cursor = await db.execute(
            "SELECT * FROM paper_positions WHERE closed_at IS NULL ORDER BY id"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def list_paper_positions(self) -> list[dict]:
        db = await self._db()
        cursor = await db.execute(
            "SELECT * FROM paper_positions ORDER BY id"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def insert_paper_position(self, position: dict) -> int:
        db = await self._db()
        cursor = await db.execute(
            """
            INSERT INTO paper_positions (
                ticker, side, direction, quantity, cost_basis_cents,
                opened_at, closed_at, realized_pnl_cents, close_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position["ticker"],
                position["side"],
                position["direction"],
                position["quantity"],
                position["cost_basis_cents"],
                position["opened_at"],
                position.get("closed_at"),
                position.get("realized_pnl_cents"),
                position.get("close_reason"),
            ),
        )
        await db.commit()
        return int(cursor.lastrowid)

    async def update_paper_position(self, position_id: int, **fields: object) -> None:
        allowed = {
            "quantity",
            "cost_basis_cents",
            "closed_at",
            "realized_pnl_cents",
            "close_reason",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            raise ValueError("no valid paper position fields to update")
        assignments = ", ".join(f"{key} = ?" for key in updates)
        db = await self._db()
        await db.execute(
            f"UPDATE paper_positions SET {assignments} WHERE id = ?",
            (*updates.values(), position_id),
        )
        await db.commit()

    async def get_latest_order_book(self, ticker: str) -> OrderBook | None:
        db = await self._db()
        cursor = await db.execute(
            """
            SELECT * FROM order_books WHERE ticker = ?
            ORDER BY timestamp DESC LIMIT 1
            """,
            (ticker,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        from .models import OrderBookLevel

        return OrderBook(
            ticker=row["ticker"],
            timestamp=_from_ms(row["timestamp"]),
            bids=[
                OrderBookLevel(price=price, size=size)
                for price, size in json.loads(row["bids_json"])
            ],
            asks=[
                OrderBookLevel(price=price, size=size)
                for price, size in json.loads(row["asks_json"])
            ],
        )

    async def save_equity_snapshot(
        self,
        ts_ms: int,
        equity_cents: int,
        cash_cents: int,
        margin_cents: int,
        unrealized_pnl_cents: int,
        realized_pnl_cents: int,
    ) -> int:
        db = await self._db()
        cursor = await db.execute(
            """
            INSERT INTO paper_equity_snapshots (
                ts, equity_cents, cash_cents, margin_cents,
                unrealized_pnl_cents, realized_pnl_cents
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ts_ms,
                equity_cents,
                cash_cents,
                margin_cents,
                unrealized_pnl_cents,
                realized_pnl_cents,
            ),
        )
        await db.commit()
        return int(cursor.lastrowid)

    async def get_equity_snapshot_at(self, ts_ms: int) -> dict | None:
        db = await self._db()
        cursor = await db.execute(
            """
            SELECT * FROM paper_equity_snapshots WHERE ts <= ?
            ORDER BY ts DESC, id DESC LIMIT 1
            """,
            (ts_ms,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row is not None else None

    async def table_count(self, table: str) -> int:
        if table not in {
            "events",
            "markets",
            "order_books",
            "trades",
            "opportunities",
            "execution_attempts",
            "paper_accounts",
            "paper_orders",
            "paper_fills",
            "paper_positions",
            "paper_ledger_events",
            "paper_equity_snapshots",
        }:
            raise ValueError("unsupported table")
        db = await self._db()
        cursor = await db.execute(f"SELECT COUNT(*) AS count FROM {table}")
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["count"])
