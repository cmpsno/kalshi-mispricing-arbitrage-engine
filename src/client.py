"""Authenticated asynchronous REST client for Kalshi Trade API v2."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Self

import httpx
from cryptography.hazmat.primitives.asymmetric import rsa

from kalshi_client.client import KalshiAPIError

from .auth import auth_headers, load_private_key
from .config import Settings
from .models import (
    Event,
    Market,
    OrderBook,
    OrderBookLevel,
    Trade,
    count_to_int,
    dollars_to_cents,
)


class KalshiClient:
    """Small async client covering the engine's read-only ingestion surface."""

    def __init__(
        self,
        *,
        api_key_id: str,
        private_key: rsa.RSAPrivateKey,
        base_url: str,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.api_key_id = api_key_id
        self._private_key = private_key
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._async_client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            headers={"Accept": "application/json"},
        )
        self._event_metadata: dict[str, Event] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> KalshiClient:
        return cls(
            api_key_id=settings.api_key_id,
            private_key=load_private_key(settings.private_key_path),
            base_url=settings.base_url,
            timeout_seconds=settings.timeout_seconds,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._async_client.aclose()

    def authentication_headers(self, method: str, path: str) -> dict[str, str]:
        timestamp_ms = self._clock_ms()
        return auth_headers(
            self.api_key_id,
            self._private_key,
            timestamp_ms,
            method,
            path,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int | bool] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = self._async_client.build_request(
            method, path, params=params, json=json
        )
        request.headers.update(
            self.authentication_headers(request.method, request.url.path)
        )
        response = await self._async_client.send(request)
        if not response.is_success:
            raise KalshiAPIError(response.status_code, response.text)
        payload = response.json()
        if not isinstance(payload, dict):
            raise KalshiAPIError(response.status_code, "expected a JSON object")
        return payload

    async def get_events(self, status: str = "active") -> list[Event]:
        """Fetch all event pages. ``active`` maps to Kalshi's ``open`` filter."""

        api_status = "open" if status == "active" else status
        if api_status not in {"unopened", "open", "closed", "settled"}:
            raise ValueError(
                "status must be active, unopened, open, closed, or settled"
            )

        events: list[Event] = []
        cursor = ""
        while True:
            params: dict[str, str | int | bool] = {
                "limit": 200,
                "status": api_status,
                "with_nested_markets": False,
            }
            if cursor:
                params["cursor"] = cursor
            payload = await self._request("GET", "/events", params=params)
            page = [
                Event.from_api(item, status=api_status)
                for item in payload.get("events", [])
            ]
            events.extend(page)
            cursor = str(payload.get("cursor", ""))
            if not cursor:
                break

        self._event_metadata.update({event.event_ticker: event for event in events})
        return events

    async def get_markets(
        self, event_ticker: str | None = None, limit: int = 1_000
    ) -> list[Market]:
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")

        params: dict[str, str | int] = {"limit": limit, "status": "open"}
        if event_ticker:
            params["event_ticker"] = event_ticker
        payload = await self._request("GET", "/markets", params=params)

        markets: list[Market] = []
        for item in payload.get("markets", []):
            metadata = self._event_metadata.get(item.get("event_ticker", ""))
            markets.append(
                Market.from_api(
                    item,
                    series_ticker=metadata.series_ticker if metadata else "",
                    event_mutually_exclusive=(
                        metadata.mutually_exclusive if metadata else False
                    ),
                )
            )
        return markets

    async def get_order_book(self, ticker: str) -> OrderBook:
        payload = await self._request(
            "GET", f"/markets/{ticker}/orderbook", params={"depth": 100}
        )
        raw = payload.get("orderbook_fp", payload.get("orderbook", {}))
        yes_levels = raw.get("yes_dollars", raw.get("yes", [])) or []
        no_levels = raw.get("no_dollars", raw.get("no", [])) or []

        bids = sorted(
            [
                OrderBookLevel(price=dollars_to_cents(price), size=count_to_int(size))
                for price, size, *_ in yes_levels
            ],
            key=lambda level: level.price,
            reverse=True,
        )
        asks = sorted(
            [
                OrderBookLevel(
                    price=100 - dollars_to_cents(price), size=count_to_int(size)
                )
                for price, size, *_ in no_levels
            ],
            key=lambda level: level.price,
        )
        return OrderBook(
            ticker=ticker, timestamp=datetime.now(UTC), bids=bids, asks=asks
        )

    async def get_trades(self, ticker: str, limit: int = 100) -> list[Trade]:
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        payload = await self._request(
            "GET", "/markets/trades", params={"ticker": ticker, "limit": limit}
        )
        return [Trade.from_api(item) for item in payload.get("trades", [])]

    async def place_order(
        self,
        ticker: str,
        side: str,
        type: str,
        price: int,
        quantity: int,
    ) -> dict[str, Any]:
        """Build a current Kalshi order payload without transmitting it.

        Phase 6 remains simulation-only. This deliberately prevents an environment
        mistake from placing a real-money order before Phase 7 risk controls exist.
        """

        if type not in {"limit", "market"}:
            raise ValueError("type must be limit or market")
        if not 1 <= price <= 99:
            raise ValueError("price must be between 1 and 99 cents")
        if quantity < 1:
            raise ValueError("quantity must be at least 1")

        directions = {
            "yes": ("buy", "yes"),
            "no": ("buy", "no"),
            "buy_yes": ("buy", "yes"),
            "buy_no": ("buy", "no"),
            "sell_yes": ("sell", "yes"),
            "sell_no": ("sell", "no"),
        }
        try:
            action, outcome_side = directions[side]
        except KeyError as exc:
            raise ValueError(f"unsupported order side: {side}") from exc

        payload: dict[str, Any] = {
            "ticker": ticker,
            "client_order_id": str(uuid.uuid4()),
            "action": action,
            "side": outcome_side,
            "count": quantity,
            "time_in_force": "immediate_or_cancel",
        }
        payload[f"{outcome_side}_price"] = price
        return {
            "simulated": True,
            "status": "simulated_filled",
            "order_type": type,
            "payload": payload,
        }
