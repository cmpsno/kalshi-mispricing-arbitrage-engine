from __future__ import annotations

import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src.client import KalshiClient
from src.config import DEMO_BASE_URL
from src.models import OrderRequest


@pytest.mark.parametrize(
    ("action", "side", "price", "book_side", "yes_price"),
    [
        ("buy", "yes", 45, "bid", "0.4500"),
        ("sell", "yes", 45, "ask", "0.4500"),
        ("buy", "no", 45, "ask", "0.5500"),
        ("sell", "no", 45, "bid", "0.5500"),
    ],
)
def test_order_request_maps_outcomes_to_v2_single_book(
    action: str, side: str, price: int, book_side: str, yes_price: str
) -> None:
    order = OrderRequest(
        ticker="MARKET",
        action=action,
        side=side,
        price=price,
        quantity=1,
        client_order_id="client-1",
    )

    payload = order.to_api_payload()

    assert payload["side"] == book_side
    assert payload["price"] == yes_price


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
async def test_place_orders_transmits_authenticated_ioc_batch(
    private_key: rsa.RSAPrivateKey,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/portfolio/events/orders/batched")
        assert request.headers["KALSHI-ACCESS-KEY"] == "demo-key"
        payload = json.loads(request.content)
        assert payload["orders"][0] == {
            "ticker": "MARKET",
            "client_order_id": "client-1",
            "side": "bid",
            "count": "2.00",
            "price": "0.4500",
            "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross",
            "cancel_order_on_pause": True,
        }
        return httpx.Response(201, json={"orders": [{
            "order_id": "order-1", "client_order_id": "client-1",
            "fill_count": "2.00", "remaining_count": "0.00",
            "average_fill_price": "0.4500", "average_fee_paid": "0.0100",
        }]})

    async with KalshiClient(
        api_key_id="demo-key",
        private_key=private_key,
        base_url=DEMO_BASE_URL,
        transport=httpx.MockTransport(handler),
    ) as client:
        results = await client.place_orders([OrderRequest(
            ticker="MARKET", action="buy", side="yes", price=45,
            quantity=2, client_order_id="client-1",
        )])

    assert results[0].status == "filled"
    assert results[0].filled_quantity == 2
    assert results[0].fees == "0.0100"


@pytest.mark.asyncio
async def test_place_orders_normalizes_partial_and_rejected_results(
    private_key: rsa.RSAPrivateKey,
) -> None:
    response = {"orders": [
        {"order_id": "order-1", "client_order_id": "client-1",
         "fill_count_fp": "1.00", "remaining_count_fp": "1.00"},
        {"client_order_id": "client-2",
         "error": {"code": "invalid_order", "message": "rejected"}},
    ]}
    orders = [OrderRequest(
        ticker=f"MARKET-{index}", action="buy", side="yes", price=45,
        quantity=2, client_order_id=f"client-{index}",
    ) for index in (1, 2)]
    async with KalshiClient(
        api_key_id="demo-key", private_key=private_key, base_url=DEMO_BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(201, json=response)),
    ) as client:
        results = await client.place_orders(orders)

    assert [result.status for result in results] == ["partial", "rejected"]
    assert results[1].error_code == "invalid_order"


@pytest.mark.asyncio
async def test_place_orders_rejects_duplicate_client_ids_before_network(
    private_key: rsa.RSAPrivateKey,
) -> None:
    order = OrderRequest(
        ticker="MARKET", action="buy", side="yes", price=45,
        quantity=1, client_order_id="duplicate",
    )
    async with KalshiClient(
        api_key_id="demo-key", private_key=private_key, base_url=DEMO_BASE_URL,
        transport=httpx.MockTransport(
            lambda request: pytest.fail(f"unexpected request: {request.url}")
        ),
    ) as client:
        with pytest.raises(ValueError, match="unique"):
            await client.place_orders([order, order])
