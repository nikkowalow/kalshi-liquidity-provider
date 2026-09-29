from __future__ import annotations

from typing import Any


class KalshiError(Exception):
    """Base class for exchange errors."""


class KalshiAPIError(KalshiError):
    def __init__(self, status: int, code: str | None, message: str, body: Any = None):
        self.status = status
        self.code = code
        self.message = message
        self.body = body
        super().__init__(f"HTTP {status} [{code or '-'}]: {message}")

    @property
    def retryable(self) -> bool:
        return self.status == 429 or self.status >= 500


class RateLimitedError(KalshiAPIError):
    pass


class AuthError(KalshiAPIError):
    pass
