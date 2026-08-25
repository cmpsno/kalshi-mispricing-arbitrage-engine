"""Synchronous, read-only Kalshi REST client used by Phase 0."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, Literal

import httpx
from cryptography.hazmat.primitives.asymmetric import rsa

from .auth import auth_headers, load_private_key
from .config import Settings


MarketStatus = Literal["unopened", "open", "closed", "settled"]


class KalshiAPIError(RuntimeError):
    """A non-success response returned by the Kalshi API."""

    def __init__(self, status_code: int, response_text: str) -> None:
        detail = response_text.strip()[:500] or "empty response body"
        super().__init__(f"Kalshi API returned HTTP {status_code}: {detail}")
        self.status_code = status_code


class KalshiClient:
    """A minimal authenticated client with one Phase 0 operation: get markets."""

    def __init__(
        self,
        *,
        api_key_id: str,
        private_key: rsa.RSAPrivateKey,
        base_url: str,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._api_key_id = api_key_id
        self._private_key = private_key
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            headers={"Accept": "application/json"},
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "KalshiClient":
        return cls(
            api_key_id=settings.api_key_id,
            private_key=load_private_key(settings.private_key_path),
            base_url=settings.base_url,
            timeout_seconds=settings.timeout_seconds,
        )

    def __enter__(self) -> "KalshiClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def get_markets(
        self,
        *,
        limit: int = 100,
        status: MarketStatus | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Fetch one page from GET /markets with authenticated headers."""

        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")

        params: dict[str, str | int] = {"limit": limit}
        if status is not None:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor

        return self._get("/markets", params=params)

    def _get(
        self, path: str, *, params: Mapping[str, str | int] | None = None
    ) -> dict[str, Any]:
        request = self._http.build_request("GET", path, params=params)
        timestamp_ms = self._clock_ms()
        request.headers.update(
            auth_headers(
                self._api_key_id,
                self._private_key,
                timestamp_ms,
                request.method,
                request.url.path,
            )
        )

        response = self._http.send(request)
        if not response.is_success:
            raise KalshiAPIError(response.status_code, response.text)

        payload = response.json()
        if not isinstance(payload, dict):
            raise KalshiAPIError(response.status_code, "expected a JSON object")
        return payload
