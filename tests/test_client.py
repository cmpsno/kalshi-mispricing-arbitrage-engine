from __future__ import annotations

import base64

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi_client import DEMO_BASE_URL, KalshiAPIError, KalshiClient


FIXED_TIMESTAMP = 1_703_123_456_789


def test_get_markets_sends_authenticated_demo_request(
    private_key: rsa.RSAPrivateKey,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == (
            "https://external-api.demo.kalshi.co/trade-api/v2/markets"
            "?limit=5&status=open"
        )
        assert request.headers["KALSHI-ACCESS-KEY"] == "demo-key-id"
        assert request.headers["KALSHI-ACCESS-TIMESTAMP"] == str(FIXED_TIMESTAMP)

        private_key.public_key().verify(
            base64.b64decode(request.headers["KALSHI-ACCESS-SIGNATURE"]),
            b"1703123456789GET/trade-api/v2/markets",
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return httpx.Response(200, json={"markets": [{"ticker": "TEST"}], "cursor": ""})

    with KalshiClient(
        api_key_id="demo-key-id",
        private_key=private_key,
        base_url=DEMO_BASE_URL,
        transport=httpx.MockTransport(handler),
        clock_ms=lambda: FIXED_TIMESTAMP,
    ) as client:
        payload = client.get_markets(limit=5, status="open")

    assert payload["markets"][0]["ticker"] == "TEST"


def test_get_markets_rejects_invalid_limit(private_key: rsa.RSAPrivateKey) -> None:
    with KalshiClient(
        api_key_id="demo-key-id",
        private_key=private_key,
        base_url=DEMO_BASE_URL,
    ) as client:
        with pytest.raises(ValueError, match="between 1 and 1000"):
            client.get_markets(limit=0)


def test_api_error_includes_status_and_bounded_body(
    private_key: rsa.RSAPrivateKey,
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, text="invalid signature", request=request)
    )
    with KalshiClient(
        api_key_id="demo-key-id",
        private_key=private_key,
        base_url=DEMO_BASE_URL,
        transport=transport,
    ) as client:
        with pytest.raises(KalshiAPIError, match="HTTP 401: invalid signature") as exc_info:
            client.get_markets()

    assert exc_info.value.status_code == 401
