import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from kalshi_lp.exchange.auth import Signer, load_private_key


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def test_rsa_signature_verifies_and_strips_query(rsa_key: rsa.RSAPrivateKey) -> None:
    signer = Signer("key-id", rsa_key)
    sig = signer.sign("1700000000000", "get", "/trade-api/v2/portfolio/orders?status=resting")
    rsa_key.public_key().verify(
        base64.b64decode(sig),
        b"1700000000000GET/trade-api/v2/portfolio/orders",
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


def test_ed25519_signature_verifies() -> None:
    key = ed25519.Ed25519PrivateKey.generate()
    sig = Signer("key-id", key).sign("1", "POST", "/trade-api/v2/portfolio/events/orders")
    key.public_key().verify(base64.b64decode(sig), b"1POST/trade-api/v2/portfolio/events/orders")


def test_headers(rsa_key: rsa.RSAPrivateKey) -> None:
    headers = Signer("key-id", rsa_key).headers("GET", "/trade-api/v2/portfolio/balance")
    assert headers["KALSHI-ACCESS-KEY"] == "key-id"
    assert headers["KALSHI-ACCESS-TIMESTAMP"].isdigit()
    assert len(headers["KALSHI-ACCESS-TIMESTAMP"]) == 13  # milliseconds
    assert headers["KALSHI-ACCESS-SIGNATURE"]


def test_load_private_key_from_pem(tmp_path, rsa_key: rsa.RSAPrivateKey) -> None:
    pem = rsa_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    path = tmp_path / "k.key"
    path.write_bytes(pem)
    assert isinstance(load_private_key(path), rsa.RSAPrivateKey)
