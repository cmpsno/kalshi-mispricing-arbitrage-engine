from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src.client import KalshiClient
from src.config import DEMO_BASE_URL, DEMO_WS_URL
from src.models import Market
from src.storage import Database
from src.websocket_manager import WebSocketManager


class FakeConnection:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


@pytest.mark.asyncio
async def test_websocket_uses_handshake_auth_and_current_subscription_shape(
    tmp_path: Path, private_key: rsa.RSAPrivateKey
) -> None:
    connection = FakeConnection()
    connector_calls = []

    async def connector(url, **kwargs):
        connector_calls.append((url, kwargs))
        return connection

    WebSocketManager.reset_singleton()
    async with (
        Database(tmp_path / "ws.db") as db,
        KalshiClient(
            api_key_id="demo-key",
            private_key=private_key,
            base_url=DEMO_BASE_URL,
            transport=httpx.MockTransport(
                lambda request: pytest.fail(f"unexpected REST call: {request.url}")
            ),
            clock_ms=lambda: 1_703_123_456_789,
        ) as client,
    ):
        manager = WebSocketManager(db, client, DEMO_WS_URL, connector=connector)
        await manager.subscribe_markets(["MARKET"])

        assert connector_calls[0][0] == DEMO_WS_URL
        headers = connector_calls[0][1]["additional_headers"]
        assert headers["KALSHI-ACCESS-KEY"] == "demo-key"
        command = json.loads(connection.sent[0])
        assert command["cmd"] == "subscribe"
        assert command["params"] == {
            "channels": ["orderbook_delta", "trade"],
            "market_tickers": ["MARKET"],
        }
        await manager.close()
        assert connection.closed is True
    WebSocketManager.reset_singleton()


@pytest.mark.asyncio
async def test_websocket_applies_deltas_and_persists_trades(
    tmp_path: Path, private_key: rsa.RSAPrivateKey
) -> None:
    WebSocketManager.reset_singleton()
    async with Database(tmp_path / "messages.db") as db:
        await db.upsert_market(
            Market(
                ticker="MARKET",
                event_ticker="EVENT",
                series_ticker="SERIES",
                status="active",
            )
        )
        async with KalshiClient(
            api_key_id="demo-key",
            private_key=private_key,
            base_url=DEMO_BASE_URL,
            transport=httpx.MockTransport(
                lambda request: pytest.fail(f"unexpected REST call: {request.url}")
            ),
        ) as client:
            manager = WebSocketManager(db, client, DEMO_WS_URL)
            await manager.handle_message(
                {
                    "type": "orderbook_snapshot",
                    "msg": {
                        "market_ticker": "MARKET",
                        "yes_dollars_fp": [["0.4000", "2.00"]],
                        "no_dollars_fp": [["0.5500", "3.00"]],
                    },
                }
            )
            assert manager.orderbooks["MARKET"].bids[0].price == 40
            assert manager.orderbooks["MARKET"].asks[0].price == 45

            await manager.handle_message(
                {
                    "type": "orderbook_delta",
                    "msg": {
                        "market_ticker": "MARKET",
                        "side": "no",
                        "price_dollars": "0.5500",
                        "delta_fp": "-3.00",
                        "ts_ms": 1_703_123_456_789,
                    },
                }
            )
            assert manager.orderbooks["MARKET"].asks == []

            await manager.handle_message(
                {
                    "type": "trade",
                    "msg": {
                        "trade_id": "trade-1",
                        "market_ticker": "MARKET",
                        "yes_price_dollars": "0.4200",
                        "count_fp": "1.00",
                        "taker_book_side": "bid",
                        "ts_ms": 1_703_123_456_789,
                    },
                }
            )
            assert await db.table_count("order_books") == 2
            assert await db.table_count("trades") == 1
    WebSocketManager.reset_singleton()
