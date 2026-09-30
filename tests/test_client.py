import json
from decimal import Decimal

import httpx
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa

from kalshi_lp.core.types import Quote, Side
from kalshi_lp.exchange.auth import Signer
from kalshi_lp.exchange.client import KalshiClient
from kalshi_lp.exchange.errors import AuthError, KalshiAPIError
from kalshi_lp.exchange.rate_limit import RateLimiter
from tests.factories import make_order

BASE = "https://demo.test/trade-api/v2"
D = Decimal


@pytest.fixture
async def client():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    c = KalshiClient(
        BASE,
        Signer("kid", key),
        rate_limiter=RateLimiter(1e6, 1e6),
        max_retries=2,
    )
    yield c
    await c.aclose()


@respx.mock
async def test_signed_request_and_balance(client: KalshiClient) -> None:
    route = respx.get(f"{BASE}/portfolio/balance").respond(
        json={"balance": 12345, "balance_dollars": "123.4500", "portfolio_value": 500}
    )
    bal = await client.get_balance()
    assert bal.balance == D("123.45")
    assert bal.portfolio_value == D(5)
    headers = route.calls[0].request.headers
    assert headers["KALSHI-ACCESS-KEY"] == "kid"
    assert "KALSHI-ACCESS-SIGNATURE" in headers


@respx.mock
async def test_create_order_payload(client: KalshiClient) -> None:
    route = respx.post(f"{BASE}/portfolio/events/orders").respond(
        201, json={"order_id": "o1", "fill_count": "0.00", "remaining_count": "5.00", "ts_ms": 1}
    )
    result = await client.create_order(
        Quote("MKT", Side.ASK, D("0.52"), D(5)), "klp-1", order_group_id="g1"
    )
    assert result.ok and result.order_id == "o1"
    body = json.loads(route.calls[0].request.content)
    assert body == {
        "ticker": "MKT",
        "side": "ask",
        "price": "0.5200",
        "count": "5.00",
        "time_in_force": "good_till_canceled",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": True,
        "cancel_order_on_pause": True,
        "client_order_id": "klp-1",
        "subaccount": 0,
        "order_group_id": "g1",
    }


@respx.mock
async def test_batch_create_surfaces_per_order_errors(client: KalshiClient) -> None:
    respx.post(f"{BASE}/portfolio/events/orders/batched").respond(
        201,
        json={
            "orders": [
                {"order_id": "o1", "client_order_id": "a", "fill_count": "0.00"},
                {
                    "order_id": None,
                    "client_order_id": "b",
                    "error": {"code": "post_only_cross", "message": "would cross"},
                },
            ]
        },
    )
    quotes = [
        (Quote("M", Side.BID, D("0.4"), D(1)), "a"),
        (Quote("M", Side.ASK, D("0.6"), D(1)), "b"),
    ]
    results = await client.batch_create_orders(quotes)
    assert results[0].ok
    assert not results[1].ok and "post_only_cross" in (results[1].error or "")


@respx.mock
async def test_batch_cancel_body(client: KalshiClient) -> None:
    route = respx.delete(f"{BASE}/portfolio/events/orders/batched").respond(
        json={"orders": [{"order_id": "x", "reduced_by": "5.00"}]}
    )
    await client.batch_cancel_orders([make_order(Side.BID, "0.4000", 5, order_id="x")])
    body = json.loads(route.calls[0].request.content)
    assert body == {"orders": [{"order_id": "x", "market_ticker": "TEST-MKT", "subaccount": 0}]}


@respx.mock
async def test_orderbooks_use_repeated_params(client: KalshiClient) -> None:
    route = respx.get(f"{BASE}/markets/orderbooks").respond(
        json={
            "orderbooks": [
                {
                    "ticker": "A",
                    "orderbook_fp": {"yes_dollars": [["0.40", "10.00"]], "no_dollars": []},
                },
                {
                    "ticker": "B",
                    "orderbook_fp": {"yes_dollars": [], "no_dollars": [["0.30", "1.00"]]},
                },
            ]
        }
    )
    books = await client.get_orderbooks(["A", "B"])
    assert route.calls[0].request.url.params.get_list("tickers") == ["A", "B"]
    assert books["A"].best_yes_bid == D("0.40")
    assert books["B"].best_yes_ask == D("0.70")


@respx.mock
async def test_pagination(client: KalshiClient) -> None:
    order = {
        "order_id": "1",
        "ticker": "M",
        "book_side": "bid",
        "yes_price_dollars": "0.1",
        "remaining_count_fp": "1.00",
        "client_order_id": "klp-1",
    }
    respx.get(f"{BASE}/portfolio/orders").mock(
        side_effect=[
            httpx.Response(200, json={"orders": [order], "cursor": "next"}),
            httpx.Response(200, json={"orders": [{**order, "order_id": "2"}], "cursor": ""}),
        ]
    )
    orders = await client.get_resting_orders()
    assert [o.order_id for o in orders] == ["1", "2"]


@respx.mock
async def test_retries_429_then_succeeds(client: KalshiClient) -> None:
    respx.get(f"{BASE}/exchange/status").mock(
        side_effect=[
            httpx.Response(429, json={"error": "too many requests"}),
            httpx.Response(200, json={"exchange_active": True, "trading_active": True}),
        ]
    )
    status = await client.get_exchange_status()
    assert status.trading_active


@respx.mock
async def test_create_order_not_retried_on_5xx(client: KalshiClient) -> None:
    route = respx.post(f"{BASE}/portfolio/events/orders").respond(503, json={})
    with pytest.raises(KalshiAPIError):
        await client.create_order(Quote("M", Side.BID, D("0.4"), D(1)), "klp-1")
    assert route.call_count == 1


@respx.mock
async def test_auth_error(client: KalshiClient) -> None:
    respx.get(f"{BASE}/portfolio/balance").respond(
        401, json={"error": {"code": "unauthorized", "message": "bad signature"}}
    )
    with pytest.raises(AuthError, match="bad signature"):
        await client.get_balance()


@respx.mock
async def test_incentive_programs_parsing(client: KalshiClient) -> None:
    respx.get(f"{BASE}/incentive_programs").respond(
        json={
            "incentive_programs": [
                {
                    "id": "p1",
                    "market_id": "m",
                    "market_ticker": "MKT",
                    "incentive_type": "liquidity",
                    "incentive_description": "",
                    "start_date": "2026-09-01T00:00:00Z",
                    "end_date": "2026-09-11T00:00:00Z",
                    "period_reward": 1_000_000,
                    "paid_out": False,
                    "discount_factor_bps": 5000,
                    "target_size_fp": "300.00",
                }
            ]
        }
    )
    [p] = await client.get_incentive_programs()
    assert p.period_reward == D(100)  # centi-cents -> dollars
    assert p.reward_per_day == D(10)
    assert p.discount_factor == D("0.5")
    assert p.target_size == D(300)


@respx.mock
async def test_exchange_status_503_during_maintenance(client: KalshiClient) -> None:
    route = respx.get(f"{BASE}/exchange/status").respond(
        503, json={"exchange_active": False, "trading_active": False}
    )
    status = await client.get_exchange_status()
    assert not status.exchange_active and not status.trading_active
    assert route.call_count == 1  # a status answer, not an error to retry


@respx.mock
async def test_queue_positions(client: KalshiClient) -> None:
    route = respx.get(f"{BASE}/portfolio/orders/queue_positions").respond(
        json={
            "queue_positions": [
                {"order_id": "o1", "market_ticker": "M", "queue_position_fp": "42.00"}
            ]
        }
    )
    assert await client.get_queue_positions(["M", "N"]) == {"o1": D(42)}
    assert route.calls[0].request.url.params["market_tickers"] == "M,N"


@respx.mock
async def test_trades_paginate_and_parse(client: KalshiClient) -> None:
    raw = {
        "trade_id": "t1",
        "ticker": "MKT",
        "count_fp": "12.50",
        "yes_price_dollars": "0.4200",
        "taker_book_side": "ask",
        "taker_side": "no",
        "created_time": "2026-09-30T02:31:01.613143Z",
    }
    route = respx.get(f"{BASE}/markets/trades").mock(
        side_effect=[
            httpx.Response(200, json={"trades": [raw], "cursor": "next"}),
            httpx.Response(200, json={"trades": [{**raw, "trade_id": "t2"}, {"bad": 1}]}),
        ]
    )
    trades = await client.get_trades("MKT", min_ts=1_790_000_000.7)
    assert [t.trade_id for t in trades] == ["t1", "t2"]  # the malformed one is skipped
    assert trades[0].count == D("12.50") and trades[0].yes_price == D("0.42")
    first = route.calls[0].request.url.params
    assert first["ticker"] == "MKT" and first["min_ts"] == "1790000000"
    assert route.calls[1].request.url.params["cursor"] == "next"
