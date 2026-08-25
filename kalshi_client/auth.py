"""RSA-PSS authentication helpers for Kalshi's Trade API."""

from __future__ import annotations

import base64
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class PrivateKeyError(ValueError):
    """Raised when a private key cannot be loaded as an RSA key."""


def load_private_key(path: Path) -> rsa.RSAPrivateKey:
    """Load an unencrypted PEM-encoded RSA private key from disk."""

    try:
        key_data = path.read_bytes()
    except OSError as exc:
        raise PrivateKeyError(f"Could not read private key at {path}: {exc}") from exc

    try:
        private_key = serialization.load_pem_private_key(key_data, password=None)
    except (TypeError, ValueError) as exc:
        raise PrivateKeyError(
            f"Private key at {path} is not a valid unencrypted PEM key"
        ) from exc

    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise PrivateKeyError(f"Private key at {path} is not an RSA private key")

    return private_key


def signing_message(timestamp_ms: int | str, method: str, path_or_url: str) -> bytes:
    """Build Kalshi's signing payload, excluding any query parameters."""

    timestamp = str(timestamp_ms)
    if not timestamp.isdigit():
        raise ValueError("timestamp_ms must contain only decimal digits")

    normalized_method = method.strip().upper()
    if not normalized_method:
        raise ValueError("method must not be empty")

    path = urlsplit(path_or_url).path
    if not path.startswith("/"):
        raise ValueError("the signed request path must start with '/'")

    return f"{timestamp}{normalized_method}{path}".encode()


def sign_request(
    private_key: rsa.RSAPrivateKey,
    timestamp_ms: int | str,
    method: str,
    path_or_url: str,
) -> str:
    """Return the base64-encoded RSA-PSS/SHA-256 request signature."""

    signature = private_key.sign(
        signing_message(timestamp_ms, method, path_or_url),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def auth_headers(
    api_key_id: str,
    private_key: rsa.RSAPrivateKey,
    timestamp_ms: int,
    method: str,
    path_or_url: str,
) -> dict[str, str]:
    """Create the three authentication headers required by Kalshi."""

    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-SIGNATURE": sign_request(
            private_key, timestamp_ms, method, path_or_url
        ),
        "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
    }
