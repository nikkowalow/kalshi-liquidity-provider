"""Shared value types.

Kalshi represents prices as fixed-point dollar strings ("0.5600") and contract
counts as fixed-point strings ("10.00"). We keep both as ``Decimal`` end to end
so there is never float rounding between what we compute and what we send.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum

ZERO = Decimal(0)
ONE = Decimal(1)

_PRICE_QUANTUM = Decimal("0.0001")
_COUNT_QUANTUM = Decimal("0.01")


class Side(StrEnum):
    """Order side on the YES book (Kalshi V2 semantics).

    ``BID`` buys YES. ``ASK`` sells YES, which rests in the order book as a NO
    bid at ``1 - price``. Kalshi nets YES and NO holdings, so an ASK fill
    reduces a long-YES position or builds a NO position.
    """

    BID = "bid"
    ASK = "ask"

    @property
    def opposite(self) -> Side:
        return Side.ASK if self is Side.BID else Side.BID


class Leg(StrEnum):
    """Which side of the order book an order rests on.

    The Liquidity Incentive Program scores the YES bid book and the NO bid book
    separately, so strategy code works per leg in that leg's own price terms.
    """

    YES = "yes"
    NO = "no"

    @property
    def order_side(self) -> Side:
        return Side.BID if self is Leg.YES else Side.ASK

    @property
    def opposite(self) -> Leg:
        return Leg.NO if self is Leg.YES else Leg.YES

    def to_yes_price(self, leg_price: Decimal) -> Decimal:
        return leg_price if self is Leg.YES else ONE - leg_price

    def inventory(self, position: Decimal) -> Decimal:
        """Position expressed in this leg's contracts (positive = long this leg)."""
        return position if self is Leg.YES else -position


def to_decimal(value: str | int | float | Decimal | None, default: Decimal = ZERO) -> Decimal:
    if value is None or value == "":
        return default
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


def fmt_price(price: Decimal) -> str:
    """Format a dollar price for the API, e.g. ``Decimal("0.56") -> "0.5600"``."""
    return str(price.quantize(_PRICE_QUANTUM))


def fmt_count(count: Decimal) -> str:
    """Format a contract count for the API, e.g. ``Decimal(10) -> "10.00"``."""
    return str(count.quantize(_COUNT_QUANTUM, rounding=ROUND_DOWN))


@dataclass(frozen=True, slots=True)
class Quote:
    """A desired resting order produced by the strategy."""

    ticker: str
    side: Side
    price: Decimal  # YES price in dollars
    size: Decimal  # contracts

    def __str__(self) -> str:
        return f"{self.side.value.upper()} {self.size.normalize():f} @ {fmt_price(self.price)}"
