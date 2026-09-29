"""Order book snapshot.

Kalshi's book lists only bids: YES bids and NO bids. A NO bid at ``p`` is
economically a YES ask at ``1 - p``. We keep both sides as bid ladders so the
strategy can treat YES and NO symmetrically.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from kalshi_lp.core.types import ONE, ZERO, Leg, to_decimal


@dataclass(frozen=True, slots=True)
class Level:
    price: Decimal
    size: Decimal


def _ladder(raw: Iterable[Sequence[Any]] | None) -> tuple[Level, ...]:
    """Parse ``[[price, size], ...]`` into levels sorted best (highest) first."""
    levels: dict[Decimal, Decimal] = {}
    for entry in raw or []:
        price, size = to_decimal(entry[0]), to_decimal(entry[1])
        if size > 0:
            levels[price] = levels.get(price, ZERO) + size
    return tuple(Level(p, s) for p, s in sorted(levels.items(), reverse=True))


@dataclass(frozen=True, slots=True)
class Orderbook:
    ticker: str
    yes: tuple[Level, ...] = field(default_factory=tuple)  # YES bids, best first
    no: tuple[Level, ...] = field(default_factory=tuple)  # NO bids, best first

    @classmethod
    def from_api(cls, ticker: str, payload: Mapping[str, Any]) -> Orderbook:
        book = payload.get("orderbook_fp") or payload
        return cls(ticker, _ladder(book.get("yes_dollars")), _ladder(book.get("no_dollars")))

    def bids(self, leg: Leg) -> tuple[Level, ...]:
        return self.yes if leg is Leg.YES else self.no

    def best_bid(self, leg: Leg) -> Decimal | None:
        ladder = self.bids(leg)
        return ladder[0].price if ladder else None

    @property
    def best_yes_bid(self) -> Decimal | None:
        return self.best_bid(Leg.YES)

    @property
    def best_yes_ask(self) -> Decimal | None:
        best_no = self.best_bid(Leg.NO)
        return None if best_no is None else ONE - best_no

    @property
    def mid(self) -> Decimal | None:
        """YES mid price, or ``None`` if either side is empty."""
        bid, ask = self.best_yes_bid, self.best_yes_ask
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2

    def depth(self, leg: Leg) -> Decimal:
        return sum((lvl.size for lvl in self.bids(leg)), ZERO)

    def without(self, own: Mapping[Leg, Mapping[Decimal, Decimal]]) -> Orderbook:
        """Return the book with our own resting size removed.

        ``own`` maps leg -> {leg price -> size}. Without this the bot would see
        its own orders as competition and chase itself.
        """

        def strip(leg: Leg) -> tuple[Level, ...]:
            mine = own.get(leg, {})
            return tuple(
                Level(lvl.price, lvl.size - mine.get(lvl.price, ZERO))
                for lvl in self.bids(leg)
                if lvl.size - mine.get(lvl.price, ZERO) > 0
            )

        return Orderbook(self.ticker, strip(Leg.YES), strip(Leg.NO))
