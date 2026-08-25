from __future__ import annotations

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src.client import KalshiClient
from src.config import DEMO_BASE_URL


@pytest.mark.asyncio
async def test_async_client_adapts_current_rest_payloads(
    private_key: rsa.RSAPrivateKey,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["KALSHI-ACCESS-KEY"] == "demo-key"
        assert "KALSHI-ACCESS-SIGNATURE" in request.headers
        if request.url.path.endswith("/events"):
            assert request.url.params["status"] == "open"
            return httpx.Response(
                200,
                json={
                    "events": [
                        {
                            "event_ticker": "EVENT",
                            "series_ticker": "SERIES",
                            "title": "Event",
                            "sub_title": "Subtitle",
                            "mutually_exclusive": True,
                        }
                    ],
                    "cursor": "",
                },
            )
        if request.url.path.endswith("/markets"):
            return httpx.Response(
                200,
                json={
                    "markets": [
                        {
                            "ticker": "MARKET-T100",
                            "event_ticker": "EVENT",
                            "title": "Above 100",
                            "status": "active",
                            "yes_bid_dollars": "0.4000",
                            "yes_ask_dollars": "0.4500",
                            "no_bid_dollars": "0.5500",
                            "no_ask_dollars": "0.6000",
                            "last_price_dollars": "0.4200",
                        }
                    ],
                    "cursor": "",
                },
            )
        if request.url.path.endswith("/orderbook"):
            return httpx.Response(
                200,
                json={
                    "orderbook_fp": {
                        "yes_dollars": [["0.4000", "2.00"]],
                        "no_dollars": [["0.5500", "3.00"]],
                    }
                },
            )
        if request.url.path.endswith("/markets/trades"):
            return httpx.Response(
                200,
                json={
                    "trades": [
                        {
                            "trade_id": "trade-1",
                            "ticker": "MARKET-T100",
                            "yes_price_dollars": "0.4200",
                            "count_fp": "1.00",
                            "taker_book_side": "bid",
                            "created_time": "2026-08-25T12:00:00Z",
                        }
                    ],
                    "cursor": "",
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    async with KalshiClient(
        api_key_id="demo-key",
        private_key=private_key,
        base_url=DEMO_BASE_URL,
        transport=httpx.MockTransport(handler),
        clock_ms=lambda: 1_703_123_456_789,
    ) as client:
        events = await client.get_events()
        markets = await client.get_markets()
        order_book = await client.get_order_book("MARKET-T100")
        trades = await client.get_trades("MARKET-T100")

    assert events[0].series_ticker == "SERIES"
    assert markets[0].series_ticker == "SERIES"
    assert markets[0].event_mutually_exclusive is True
    assert order_book.bids[0].price == 40
    assert order_book.asks[0].price == 45
    assert trades[0].price == 42


@pytest.mark.asyncio
async def test_place_order_is_always_simulated(
    private_key: rsa.RSAPrivateKey,
) -> None:
    async with KalshiClient(
        api_key_id="demo-key",
        private_key=private_key,
        base_url=DEMO_BASE_URL,
        transport=httpx.MockTransport(
            lambda request: pytest.fail(f"unexpected live request: {request.url}")
        ),
    ) as client:
        result = await client.place_order(
            "MARKET", "buy_yes", "limit", price=45, quantity=2
        )

    assert result["simulated"] is True
    assert result["payload"]["yes_price"] == 45
    assert result["payload"]["count"] == 2
