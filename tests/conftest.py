from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(scope="session")
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)
