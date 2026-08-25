"""Authenticated WebSocket ingestion using Kalshi's current v2 protocol."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from itertools import count
from typing import Any, Self

import structlog
from websockets.asyncio.client import connect as websocket_connect
from websockets.exceptions import ConnectionClosed

from .client import KalshiClient
from .models import OrderBook, OrderBookLevel, Trade, count_to_int, dollars_to_cents
from .storage import Database

log = structlog.get_logger()


class WebSocketManager:
    _instance: WebSocketManager | None = None

    def __new__(cls, *args: Any, **kwargs: Any) -> Self:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        db: Database,
        client: KalshiClient,
        ws_url: str,
        *,
        connector: Callable[..., Any] = websocket_connect,
    ) -> None:
        if getattr(self, "_initialized", False):
            return
        self.db = db
        self.client = client
        self.ws_url = ws_url
        self.connection: Any = None
        self.subscribed_tickers: set[str] = set()
        self.running = False
        self.orderbooks: dict[str, OrderBook] = {}
        self._channels_by_ticker: set[tuple[str, str]] = set()
        self._request_ids = count(1)
        self._connector = connector
        self._initialized = True

    @classmethod
    def reset_singleton(cls) -> None:
        """Test helper that discards the process-wide manager instance."""

        cls._instance = None

    async def connect(self) -> None:
        if self.connection is not None:
            return
        headers = self.client.authentication_headers("GET", "/trade-api/ws/v2")
        self.connection = await self._connector(
            self.ws_url,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=20,
        )
        await log.ainfo("websocket_connected", url=self.ws_url)

    async def _send_subscription(self, channels: list[str], tickers: list[str]) -> None:
        await self.connect()
        command = {
            "id": next(self._request_ids),
            "cmd": "subscribe",
            "params": {"channels": channels, "market_tickers": tickers},
        }
        await self.connection.send(json.dumps(command, separators=(",", ":")))

    async def subscribe_orderbook(self, ticker: str) -> None:
        key = (ticker, "orderbook_delta")
        if key in self._channels_by_ticker:
            return
        await self._send_subscription(["orderbook_delta"], [ticker])
        self._channels_by_ticker.add(key)
        self.subscribed_tickers.add(ticker)

    async def subscribe_trades(self, ticker: str) -> None:
        key = (ticker, "trade")
        if key in self._channels_by_ticker:
            return
        await self._send_subscription(["trade"], [ticker])
        self._channels_by_ticker.add(key)
        self.subscribed_tickers.add(ticker)

    async def subscribe_markets(self, tickers: list[str], batch_size: int = 50) -> None:
        """Batch subscriptions to stay below command and market limits."""

        unique = list(dict.fromkeys(tickers))
        for index in range(0, len(unique), batch_size):
            batch = unique[index : index + batch_size]
            await self._send_subscription(["orderbook_delta", "trade"], batch)
            for ticker in batch:
                self._channels_by_ticker.add((ticker, "orderbook_delta"))
                self._channels_by_ticker.add((ticker, "trade"))
                self.subscribed_tickers.add(ticker)

    async def listen(self) -> None:
        self.running = True
        reconnecting = False
        reconnect_delay = 1.0
        try:
            while self.running:
                try:
                    await self.connect()
                    if reconnecting and self.subscribed_tickers:
                        await self._resubscribe()
                    async for raw_message in self.connection:
                        try:
                            await self.handle_message(raw_message)
                        except (
                            KeyError,
                            TypeError,
                            ValueError,
                            json.JSONDecodeError,
                        ) as exc:
                            await log.awarning(
                                "websocket_message_rejected", error=str(exc)
                            )
                except (ConnectionClosed, OSError, TimeoutError) as exc:
                    await log.awarning("websocket_disconnected", error=str(exc))
                finally:
                    if self.connection is not None:
                        await self.connection.close()
                        self.connection = None

                if self.running:
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 30.0)
                    reconnecting = True
        finally:
            self.running = False

    async def _resubscribe(self, batch_size: int = 50) -> None:
        tickers = sorted(self.subscribed_tickers)
        for index in range(0, len(tickers), batch_size):
            await self._send_subscription(
                ["orderbook_delta", "trade"], tickers[index : index + batch_size]
            )

    async def handle_message(self, raw_message: str | bytes | dict[str, Any]) -> None:
        if isinstance(raw_message, dict):
            message = raw_message
        else:
            message = json.loads(raw_message)
        message_type = message.get("type")
        payload = message.get("msg", {})

        if message_type == "orderbook_snapshot":
            await self._handle_orderbook_snapshot(payload)
        elif message_type == "orderbook_delta":
            await self._handle_orderbook_delta(payload)
        elif message_type == "trade":
            await self.db.insert_trade(Trade.from_api(payload))
        elif message_type == "error":
            await log.aerror("websocket_error", payload=payload)

    async def _handle_orderbook_snapshot(self, payload: dict[str, Any]) -> None:
        ticker = payload["market_ticker"]
        bids = sorted(
            [
                OrderBookLevel(price=dollars_to_cents(price), size=count_to_int(size))
                for price, size, *_ in payload.get("yes_dollars_fp", [])
            ],
            key=lambda level: level.price,
            reverse=True,
        )
        asks = sorted(
            [
                OrderBookLevel(
                    price=100 - dollars_to_cents(price), size=count_to_int(size)
                )
                for price, size, *_ in payload.get("no_dollars_fp", [])
            ],
            key=lambda level: level.price,
        )
        book = OrderBook(
            ticker=ticker, timestamp=datetime.now(UTC), bids=bids, asks=asks
        )
        await self._persist_book(book)

    async def _handle_orderbook_delta(self, payload: dict[str, Any]) -> None:
        ticker = payload["market_ticker"]
        existing = self.orderbooks.get(ticker) or OrderBook(ticker=ticker)
        side = payload["side"]
        raw_price = dollars_to_cents(payload["price_dollars"])
        price = raw_price if side == "yes" else 100 - raw_price
        delta = count_to_int(payload["delta_fp"])
        levels = existing.bids if side == "yes" else existing.asks

        sizes = {level.price: level.size for level in levels}
        sizes[price] = sizes.get(price, 0) + delta
        if sizes[price] <= 0:
            sizes.pop(price, None)
        updated = [
            OrderBookLevel(price=level_price, size=size)
            for level_price, size in sizes.items()
        ]
        updated.sort(key=lambda level: level.price, reverse=(side == "yes"))

        timestamp = datetime.now(UTC)
        if payload.get("ts_ms") is not None:
            timestamp = datetime.fromtimestamp(payload["ts_ms"] / 1_000, tz=UTC)
        book = existing.model_copy(
            update={
                "timestamp": timestamp,
                "bids": updated if side == "yes" else existing.bids,
                "asks": updated if side == "no" else existing.asks,
            }
        )
        await self._persist_book(book)

    async def _persist_book(self, book: OrderBook) -> None:
        self.orderbooks[book.ticker] = book
        await self.db.insert_order_book(book)
        try:
            market = await self.db.get_market(book.ticker)
        except KeyError:
            return

        if book.bids:
            market.yes_bid = book.bids[0].price
            market.no_ask = 100 - market.yes_bid
        if book.asks:
            market.yes_ask = book.asks[0].price
            market.no_bid = 100 - market.yes_ask
        await self.db.upsert_market(market)

    async def close(self) -> None:
        self.running = False
        if self.connection is not None:
            await self.connection.close()
            self.connection = None
