"""Async REST client for the Kalshi Trade API v2."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

import httpx

from kalshi_lp.core.orderbook import Orderbook
from kalshi_lp.core.types import Quote, fmt_count, fmt_price
from kalshi_lp.exchange.auth import Signer
from kalshi_lp.exchange.errors import AuthError, KalshiAPIError, RateLimitedError
from kalshi_lp.exchange.models import (
    Balance,
    ExchangeStatus,
    IncentiveProgram,
    Market,
    Order,
    Position,
)
from kalshi_lp.exchange.rate_limit import Bucket, RateLimiter

log = logging.getLogger(__name__)

DEFAULT_COST = 10
CANCEL_COST = 2
MAX_ORDERBOOKS_PER_CALL = 100


@dataclass(frozen=True, slots=True)
class OrderResult:
    """Outcome of one create/amend/cancel, including per-item batch errors."""

    order_id: str | None
    client_order_id: str | None
    error: str | None = None
    filled: Decimal = Decimal(0)

    @property
    def ok(self) -> bool:
        return self.error is None


def _error_text(err: Mapping[str, Any] | None) -> str | None:
    if not err:
        return None
    return f"{err.get('code', 'error')}: {err.get('message', '')}".strip()


class KalshiClient:
    def __init__(
        self,
        base_url: str,
        signer: Signer | None = None,
        *,
        rate_limiter: RateLimiter | None = None,
        timeout: float = 10.0,
        max_retries: int = 3,
        subaccount: int = 0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._path_prefix = urlsplit(self.base_url).path  # e.g. /trade-api/v2
        self._signer = signer
        self._limiter = rate_limiter or RateLimiter(read_per_sec=200, write_per_sec=100)
        self._max_retries = max_retries
        self.subaccount = subaccount
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "kalshi-lp/0.1"},
        )

    async def __aenter__(self) -> KalshiClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ transport

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: httpx.QueryParams | Mapping[str, Any] | list[tuple[str, Any]] | None = None,
        json: Any = None,
        bucket: Bucket = Bucket.READ,
        cost: float = DEFAULT_COST,
        retry_server_errors: bool = True,
    ) -> Any:
        """Send a request, retrying 429s always and 5xx/network errors if safe.

        Order creation passes ``retry_server_errors=False``: a timeout or 5xx
        may still have placed the order, and blindly resending could double it.
        """
        attempt = 0
        while True:
            await self._limiter.acquire(bucket, cost)
            headers = self._signer.headers(method, self._path_prefix + path) if self._signer else {}
            try:
                resp = await self._http.request(
                    method, path, params=params, json=json, headers=headers
                )
            except httpx.TransportError as exc:
                if not retry_server_errors or attempt >= self._max_retries:
                    raise
                log.warning("%s %s transport error (%s); retrying", method, path, exc)
            else:
                if resp.status_code < 400:
                    return resp.json() if resp.content else {}
                error = self._to_error(resp)
                retryable = isinstance(error, RateLimitedError) or (
                    retry_server_errors and error.retryable
                )
                if not retryable or attempt >= self._max_retries:
                    raise error
                log.warning("%s %s -> %s; retrying", method, path, error)
            attempt += 1
            await asyncio.sleep(min(0.25 * 2**attempt, 4.0) * (0.5 + random.random()))

    @staticmethod
    def _to_error(resp: httpx.Response) -> KalshiAPIError:
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        err = body.get("error", body) if isinstance(body, dict) else {}
        if isinstance(err, str):
            err = {"message": err}
        code, message = err.get("code"), err.get("message") or str(body)
        cls: type[KalshiAPIError] = KalshiAPIError
        if resp.status_code == 429:
            cls = RateLimitedError
        elif resp.status_code in (401, 403):
            cls = AuthError
        return cls(resp.status_code, code, message, body)

    async def _paginate(
        self, path: str, key: str, params: dict[str, Any], *, max_items: int | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        params = dict(params)
        seen = 0
        while True:
            data = await self._request("GET", path, params=params)
            for item in data.get(key) or []:
                yield item
                seen += 1
                if max_items is not None and seen >= max_items:
                    return
            cursor = data.get("cursor") or data.get("next_cursor")
            if not cursor:
                return
            params["cursor"] = cursor

    # ---------------------------------------------------------------- exchange

    async def get_exchange_status(self) -> ExchangeStatus:
        try:
            data = await self._request("GET", "/exchange/status", retry_server_errors=False)
        except KalshiAPIError as exc:
            # During maintenance Kalshi answers 503 with a normal status body.
            if exc.status == 503 and isinstance(exc.body, dict) and "exchange_active" in exc.body:
                return ExchangeStatus.from_api(exc.body)
            raise
        return ExchangeStatus.from_api(data)

    # ----------------------------------------------------------------- markets

    async def get_market(self, ticker: str) -> Market:
        data = await self._request("GET", f"/markets/{ticker}")
        return Market.from_api(data["market"])

    async def get_markets(
        self,
        *,
        tickers: Iterable[str] | None = None,
        status: str | None = None,
        series_ticker: str | None = None,
        event_ticker: str | None = None,
        exclude_multivariate: bool = True,
        max_items: int | None = None,
    ) -> list[Market]:
        params: dict[str, Any] = {"limit": 1000}
        if tickers is not None:
            ticker_list = list(tickers)
            if not ticker_list:
                return []
            params["tickers"] = ",".join(ticker_list)
        if status:
            params["status"] = status
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if exclude_multivariate:
            params["mve_filter"] = "exclude"
        return [
            Market.from_api(m)
            async for m in self._paginate("/markets", "markets", params, max_items=max_items)
        ]

    async def get_orderbook(self, ticker: str, depth: int = 0) -> Orderbook:
        data = await self._request("GET", f"/markets/{ticker}/orderbook", params={"depth": depth})
        return Orderbook.from_api(ticker, data)

    async def get_orderbooks(self, tickers: Sequence[str]) -> dict[str, Orderbook]:
        books: dict[str, Orderbook] = {}
        for i in range(0, len(tickers), MAX_ORDERBOOKS_PER_CALL):
            chunk = tickers[i : i + MAX_ORDERBOOKS_PER_CALL]
            data = await self._request(
                "GET", "/markets/orderbooks", params=[("tickers", t) for t in chunk]
            )
            for entry in data.get("orderbooks") or []:
                books[entry["ticker"]] = Orderbook.from_api(entry["ticker"], entry)
        return books

    async def get_incentive_programs(
        self, *, status: str = "active", incentive_type: str = "liquidity"
    ) -> list[IncentiveProgram]:
        params = {"status": status, "type": incentive_type, "limit": 1000}
        programs = []
        async for raw in self._paginate("/incentive_programs", "incentive_programs", params):
            try:
                programs.append(IncentiveProgram.from_api(raw))
            except (KeyError, ValueError) as exc:
                log.debug("skipping malformed incentive program %s: %s", raw.get("id"), exc)
        return programs

    # --------------------------------------------------------------- portfolio

    async def get_balance(self) -> Balance:
        data = await self._request("GET", "/portfolio/balance", params=self._sub())
        return Balance.from_api(data)

    async def get_positions(self, ticker: str | None = None) -> dict[str, Position]:
        # No count_filter: flat markets must still appear so their realized P&L is tracked.
        params: dict[str, Any] = {"limit": 1000, **self._sub()}
        if ticker:
            params["ticker"] = ticker
        positions = {}
        async for raw in self._paginate("/portfolio/positions", "market_positions", params):
            pos = Position.from_api(raw)
            positions[pos.ticker] = pos
        return positions

    async def get_resting_orders(self, ticker: str | None = None) -> list[Order]:
        params: dict[str, Any] = {"status": "resting", "limit": 1000, **self._sub()}
        if ticker:
            params["ticker"] = ticker
        return [
            Order.from_api(o) async for o in self._paginate("/portfolio/orders", "orders", params)
        ]

    # ------------------------------------------------------------------ orders

    def _order_body(
        self, quote: Quote, client_order_id: str, *, post_only: bool, order_group_id: str | None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "ticker": quote.ticker,
            "side": quote.side.value,
            "price": fmt_price(quote.price),
            "count": fmt_count(quote.size),
            "time_in_force": "good_till_canceled",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": post_only,
            "cancel_order_on_pause": True,
            "client_order_id": client_order_id,
            "subaccount": self.subaccount,
        }
        if order_group_id:
            body["order_group_id"] = order_group_id
        return body

    async def create_order(
        self,
        quote: Quote,
        client_order_id: str,
        *,
        post_only: bool = True,
        order_group_id: str | None = None,
    ) -> OrderResult:
        body = self._order_body(
            quote, client_order_id, post_only=post_only, order_group_id=order_group_id
        )
        data = await self._request(
            "POST",
            "/portfolio/events/orders",
            json=body,
            bucket=Bucket.WRITE,
            retry_server_errors=False,
        )
        return OrderResult(
            data.get("order_id"), client_order_id, filled=Decimal(data.get("fill_count") or 0)
        )

    async def batch_create_orders(
        self,
        orders: Sequence[tuple[Quote, str]],
        *,
        post_only: bool = True,
        order_group_id: str | None = None,
    ) -> list[OrderResult]:
        if not orders:
            return []
        body = {
            "orders": [
                self._order_body(q, coid, post_only=post_only, order_group_id=order_group_id)
                for q, coid in orders
            ]
        }
        data = await self._request(
            "POST",
            "/portfolio/events/orders/batched",
            json=body,
            bucket=Bucket.WRITE,
            cost=DEFAULT_COST * len(orders),
            retry_server_errors=False,
        )
        return [
            OrderResult(
                r.get("order_id"),
                r.get("client_order_id"),
                _error_text(r.get("error")),
                Decimal(r.get("fill_count") or 0),
            )
            for r in data.get("orders") or []
        ]

    async def decrease_order(self, order: Order, reduce_to: Decimal) -> OrderResult:
        """Shrink a resting order in place. Unlike cancel/replace, keeps queue priority."""
        data = await self._request(
            "POST",
            f"/portfolio/events/orders/{order.order_id}/decrease",
            params=self._sub(),
            json={"reduce_to": fmt_count(reduce_to), "market_ticker": order.ticker},
            bucket=Bucket.WRITE,  # cost undocumented; assume the default
        )
        return OrderResult(data.get("order_id", order.order_id), data.get("client_order_id"))

    async def cancel_order(self, order_id: str, ticker: str | None = None) -> OrderResult:
        params: dict[str, Any] = self._sub()
        if ticker:
            params["market_ticker"] = ticker
        data = await self._request(
            "DELETE",
            f"/portfolio/events/orders/{order_id}",
            params=params,
            bucket=Bucket.WRITE,
            cost=CANCEL_COST,
        )
        return OrderResult(data.get("order_id", order_id), data.get("client_order_id"))

    async def batch_cancel_orders(self, orders: Sequence[Order]) -> list[OrderResult]:
        if not orders:
            return []
        body = {
            "orders": [
                {"order_id": o.order_id, "market_ticker": o.ticker, "subaccount": self.subaccount}
                for o in orders
            ]
        }
        data = await self._request(
            "DELETE",
            "/portfolio/events/orders/batched",
            json=body,
            bucket=Bucket.WRITE,
            cost=CANCEL_COST * len(orders),
        )
        return [
            OrderResult(r.get("order_id"), r.get("client_order_id"), _error_text(r.get("error")))
            for r in data.get("orders") or []
        ]

    async def cancel_all_orders(self) -> None:
        """Cancel every resting order on the account (not just the bot's)."""
        await self._request(
            "DELETE",
            "/portfolio/events/orders",
            params=self._sub(),
            bucket=Bucket.WRITE,
            cost=CANCEL_COST,
        )

    # ------------------------------------------------------------ order groups

    async def create_order_group(self, contracts_limit: int) -> str:
        data = await self._request(
            "POST",
            "/portfolio/order_groups/create",
            json={"contracts_limit": contracts_limit, "subaccount": self.subaccount},
            bucket=Bucket.WRITE,
        )
        return str(data["order_group_id"])

    async def reset_order_group(self, order_group_id: str) -> None:
        await self._request(
            "PUT",
            f"/portfolio/order_groups/{order_group_id}/reset",
            params=self._sub(),
            bucket=Bucket.WRITE,
        )

    async def delete_order_group(self, order_group_id: str) -> None:
        await self._request(
            "DELETE",
            f"/portfolio/order_groups/{order_group_id}",
            params=self._sub(),
            bucket=Bucket.WRITE,
        )

    def _sub(self) -> dict[str, Any]:
        return {"subaccount": self.subaccount}
