"""Request signing.

Every authenticated request carries three headers. The signature covers
``timestamp_ms + METHOD + path``, where ``path`` is the full URL path from the
API root (``/trade-api/v2/...``) with the query string stripped. RSA keys sign
with RSA-PSS/SHA-256 (salt length = digest length); Ed25519 keys sign directly.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

PrivateKey = RSAPrivateKey | Ed25519PrivateKey


def load_private_key(path: str | Path) -> PrivateKey:
    data = Path(path).expanduser().read_bytes()
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, RSAPrivateKey | Ed25519PrivateKey):
        raise TypeError(f"unsupported key type {type(key).__name__}; Kalshi uses RSA or Ed25519")
    return key


class Signer:
    def __init__(self, key_id: str, private_key: PrivateKey):
        self.key_id = key_id
        self._key = private_key

    @classmethod
    def from_file(cls, key_id: str, path: str | Path) -> Signer:
        return cls(key_id, load_private_key(path))

    def sign(self, timestamp_ms: str, method: str, path: str) -> str:
        message = f"{timestamp_ms}{method.upper()}{path.split('?', 1)[0]}".encode()
        if isinstance(self._key, Ed25519PrivateKey):
            signature = self._key.sign(message)
        else:
            signature = self._key.sign(
                message,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH
                ),
                hashes.SHA256(),
            )
        return base64.b64encode(signature).decode()

    def headers(self, method: str, path: str) -> dict[str, str]:
        timestamp_ms = str(int(time.time() * 1000))
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": self.sign(timestamp_ms, method, path),
        }
