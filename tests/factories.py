"""Builders for domain objects used across tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from kalshi_lp.core.orderbook import Orderbook
from kalshi_lp.core.types import Side
from kalshi_lp.exchange.models import Market, Order, Position


def D(x: str | int) -> Decimal:
    return Decimal(str(x))


def make_market(ticker: str = "TEST-MKT", *, hours_to_close: float = 48, **kw: Any) -> Market:
    payload = {
        "ticker": ticker,
        "event_ticker": "TEST",
        "status": "active",
        "market_type": "binary",
        "close_time": (datetime.now(UTC) + timedelta(hours=hours_to_close)).isoformat(),
        "yes_bid_dollars": "0.4500",
        "yes_ask_dollars": "0.5500",
        "volume_24h_fp": "1000.00",
        "price_ranges": [{"start": "0.0000", "end": "1.0000", "step": "0.0100"}],
        **kw,
    }
    return Market.from_api(payload)


def make_book(
    yes: list[tuple[str, str | int]], no: list[tuple[str, str | int]], ticker: str = "TEST-MKT"
) -> Orderbook:
    return Orderbook.from_api(
        ticker,
        {
            "orderbook_fp": {
                "yes_dollars": [[p, str(s)] for p, s in yes],
                "no_dollars": [[p, str(s)] for p, s in no],
            }
        },
    )


def make_order(
    side: Side,
    price: str,
    remaining: str | int,
    *,
    ticker: str = "TEST-MKT",
    order_id: str | None = None,
    client_order_id: str = "klp-abc",
) -> Order:
    return Order.from_api(
        {
            "order_id": order_id or f"{side.value}-{price}-{remaining}",
            "client_order_id": client_order_id,
            "ticker": ticker,
            "book_side": side.value,
            "yes_price_dollars": price,
            "remaining_count_fp": str(remaining),
            "status": "resting",
        }
    )


def make_position(ticker: str = "TEST-MKT", position: str | int = 0, **kw: str) -> Position:
    return Position.from_api(
        {
            "ticker": ticker,
            "position_fp": str(position),
            "market_exposure_dollars": kw.get("exposure", "0"),
            "realized_pnl_dollars": kw.get("realized", "0"),
            "fees_paid_dollars": kw.get("fees", "0"),
        }
    )
