from __future__ import annotations

import base64
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi_client.auth import (
    PrivateKeyError,
    load_private_key,
    sign_request,
    signing_message,
)


def test_signing_message_normalizes_method_and_removes_query() -> None:
    message = signing_message(
        1_703_123_456_789,
        " get ",
        "https://external-api.demo.kalshi.co/trade-api/v2/markets?limit=5",
    )

    assert message == b"1703123456789GET/trade-api/v2/markets"


def test_signature_is_valid_rsa_pss(
    private_key: rsa.RSAPrivateKey,
) -> None:
    signature = base64.b64decode(
        sign_request(private_key, "1703123456789", "GET", "/trade-api/v2/markets")
    )

    private_key.public_key().verify(
        signature,
        b"1703123456789GET/trade-api/v2/markets",
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )


def test_signature_does_not_cover_query_parameters(
    private_key: rsa.RSAPrivateKey,
) -> None:
    signature = base64.b64decode(
        sign_request(
            private_key,
            "1703123456789",
            "GET",
            "/trade-api/v2/markets?limit=5",
        )
    )

    with pytest.raises(InvalidSignature):
        private_key.public_key().verify(
            signature,
            b"1703123456789GET/trade-api/v2/markets?limit=5",
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )


def test_load_private_key_reads_unencrypted_rsa_pem(
    tmp_path: Path, private_key: rsa.RSAPrivateKey
) -> None:
    key_path = tmp_path / "private.pem"
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    loaded_key = load_private_key(key_path)

    assert loaded_key.private_numbers() == private_key.private_numbers()


def test_load_private_key_rejects_invalid_pem(tmp_path: Path) -> None:
    key_path = tmp_path / "private.pem"
    key_path.write_text("not a key", encoding="utf-8")

    with pytest.raises(PrivateKeyError, match="not a valid unencrypted PEM key"):
        load_private_key(key_path)
