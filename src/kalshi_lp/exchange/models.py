"""Typed views over Kalshi API payloads.

Parsers are deliberately tolerant: unknown fields are ignored and optional
fields default sensibly, so additive API changes don't break the bot.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from kalshi_lp.core.pricing import PriceGrid
from kalshi_lp.core.types import ONE, ZERO, Leg, Side, to_decimal

# Market lifecycle statuses in which orders can rest. GET /markets filters with
# "open", but market objects report "active".
TRADABLE_STATUSES = frozenset({"active", "open"})


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _opt_decimal(value: Any) -> Decimal | None:
    return None if value in (None, "") else to_decimal(value)


@dataclass(frozen=True, slots=True)
class ExchangeStatus:
    exchange_active: bool
    trading_active: bool

    @classmethod
    def from_api(cls, d: Mapping[str, Any]) -> ExchangeStatus:
        return cls(bool(d.get("exchange_active")), bool(d.get("trading_active")))


@dataclass(frozen=True, slots=True)
class Balance:
    balance: Decimal  # available cash, dollars
    portfolio_value: Decimal  # dollars

    @classmethod
    def from_api(cls, d: Mapping[str, Any]) -> Balance:
        cash = (
            to_decimal(d["balance_dollars"])
            if d.get("balance_dollars") is not None
            else to_decimal(d.get("balance")) / 100
        )
        return cls(cash, to_decimal(d.get("portfolio_value")) / 100)


@dataclass(frozen=True, slots=True)
class Market:
    ticker: str
    event_ticker: str
    status: str
    close_time: datetime | None
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    last_price: Decimal | None
    volume_24h: Decimal
    open_interest: Decimal
    grid: PriceGrid
    market_type: str = "binary"
    title: str = ""

    @classmethod
    def from_api(cls, d: Mapping[str, Any]) -> Market:
        return cls(
            ticker=d["ticker"],
            event_ticker=d.get("event_ticker", ""),
            status=d.get("status", ""),
            close_time=parse_ts(d.get("close_time")),
            yes_bid=_opt_decimal(d.get("yes_bid_dollars")),
            yes_ask=_opt_decimal(d.get("yes_ask_dollars")),
            last_price=_opt_decimal(d.get("last_price_dollars")),
            volume_24h=to_decimal(d.get("volume_24h_fp")),
            open_interest=to_decimal(d.get("open_interest_fp")),
            grid=PriceGrid.from_api(d.get("price_ranges")),
            market_type=d.get("market_type", "binary"),
            title=d.get("yes_sub_title") or d.get("title") or "",
        )

    @property
    def is_tradable(self) -> bool:
        return self.status in TRADABLE_STATUSES

    @property
    def spread(self) -> Decimal | None:
        """YES ask minus bid from the summary, or None if a side is empty (reads as 0 or 1)."""
        bid, ask = self.yes_bid, self.yes_ask
        if bid is None or ask is None or not ZERO < bid < ask < ONE:
            return None
        return ask - bid

    def seconds_to_close(self, now: datetime | None = None) -> float | None:
        if self.close_time is None:
            return None
        return (self.close_time - (now or datetime.now(UTC))).total_seconds()


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    client_order_id: str
    ticker: str
    side: Side
    yes_price: Decimal
    remaining: Decimal
    status: str

    @classmethod
    def from_api(cls, d: Mapping[str, Any]) -> Order:
        side = d.get("book_side")
        if side is None:  # legacy order shape: side (yes/no) + action (buy/sell)
            buys_yes = (d.get("side") == "yes") == (d.get("action", "buy") == "buy")
            side = "bid" if buys_yes else "ask"
        return cls(
            order_id=d["order_id"],
            client_order_id=d.get("client_order_id") or "",
            ticker=d["ticker"],
            side=Side(side),
            yes_price=to_decimal(d.get("yes_price_dollars")),
            remaining=to_decimal(d.get("remaining_count_fp")),
            status=d.get("status", ""),
        )

    @property
    def leg(self) -> Leg:
        return Leg.YES if self.side is Side.BID else Leg.NO

    @property
    def leg_price(self) -> Decimal:
        return self.yes_price if self.side is Side.BID else ONE - self.yes_price


@dataclass(frozen=True, slots=True)
class Position:
    ticker: str
    position: Decimal  # positive = long YES, negative = long NO
    market_exposure: Decimal  # dollars paid for the current position
    realized_pnl: Decimal
    fees_paid: Decimal

    @classmethod
    def from_api(cls, d: Mapping[str, Any]) -> Position:
        return cls(
            ticker=d["ticker"],
            position=to_decimal(d.get("position_fp")),
            market_exposure=to_decimal(d.get("market_exposure_dollars")),
            realized_pnl=to_decimal(d.get("realized_pnl_dollars")),
            fees_paid=to_decimal(d.get("fees_paid_dollars")),
        )

    @classmethod
    def flat(cls, ticker: str) -> Position:
        return cls(ticker, ZERO, ZERO, ZERO, ZERO)


@dataclass(frozen=True, slots=True)
class IncentiveProgram:
    id: str
    market_ticker: str
    incentive_type: str
    start: datetime
    end: datetime
    period_reward: Decimal  # dollars for the whole period
    paid_out: bool
    discount_factor: Decimal | None  # 0..1
    target_size: Decimal | None  # contracts per side

    @classmethod
    def from_api(cls, d: Mapping[str, Any]) -> IncentiveProgram:
        bps = d.get("discount_factor_bps")
        start, end = parse_ts(d.get("start_date")), parse_ts(d.get("end_date"))
        if start is None or end is None:
            raise ValueError(f"incentive program {d.get('id')} missing dates")
        return cls(
            id=d["id"],
            market_ticker=d["market_ticker"],
            incentive_type=d.get("incentive_type", ""),
            start=start,
            end=end,
            period_reward=to_decimal(d.get("period_reward")) / 10_000,  # centi-cents
            paid_out=bool(d.get("paid_out")),
            discount_factor=None if bps is None else Decimal(bps) / 10_000,
            target_size=_opt_decimal(d.get("target_size_fp")),
        )

    @property
    def reward_per_day(self) -> Decimal:
        days = Decimal((self.end - self.start).total_seconds()) / 86_400
        return self.period_reward / days if days > 0 else ZERO

    def is_active(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        return self.start <= now < self.end
