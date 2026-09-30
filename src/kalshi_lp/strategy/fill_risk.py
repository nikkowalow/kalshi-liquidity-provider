"""Expected cost of getting filled, from a market's recent public trades.

Rewards only pay for resting near the top of the book, so a quote can't be
made fill-proof. What fills it is a *sweep*: a burst of taker orders that eats
every contract in front of ours faster than the bot can move (it reacts in
about a second). Slow selling isn't the danger: as the depth ahead of us thins,
the cushion rule (quoting.min_cushion) moves the quote down.

So for each leg we replay the market's recent trades that hit that leg's bid
book, group them into sweeps (prints within ``sweep_window_seconds`` of each
other), and ask how much of each sweep would have reached our order:

    filled = min(our size, max(0, sweep volume - contracts ahead of us))

using today's depth ahead of the quote we'd post, as if we joined the back of
the queue at our price. Volume, not price, decides: prices drift over a day,
but "a burst bigger than the queue in front of us" doesn't depend on where the
market happened to be trading at the time.

Each filled contract then costs about the taker fee to get out (with
risk.flatten_on_fill every fill is closed at once, crossing the book) plus the
move that came with the sweep (``adverse_move``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from kalshi_lp.core.orderbook import Level
from kalshi_lp.core.types import ONE, ZERO, Leg
from kalshi_lp.exchange.models import Trade


@dataclass(frozen=True, slots=True)
class PlannedQuote:
    """The resting bid we would post on one leg, in that leg's price terms."""

    price: Decimal
    size: Decimal
    ahead: Decimal  # other traders' contracts at this price or better, in front of us


@dataclass(frozen=True, slots=True)
class Sweep:
    leg: Leg  # the bid book it hit
    volume: Decimal  # contracts
    started: datetime


@dataclass(frozen=True, slots=True)
class FillRisk:
    fills_per_day: Decimal  # our contracts that would have been filled, per day
    cost_per_day: Decimal  # dollars those fills would have cost, per day
    sweeps_per_day: Decimal  # all sweeps on either book, per day
    window_hours: Decimal  # how much trade history this is based on

    @property
    def cost_per_contract(self) -> Decimal:
        return self.cost_per_day / self.fills_per_day if self.fills_per_day else ZERO


def depth_ahead(bids: Sequence[Level], price: Decimal) -> Decimal:
    """Contracts a new bid at ``price`` would queue behind (better prices plus the level)."""
    return sum((level.size for level in bids if level.price >= price), ZERO)


def taker_fee(price: Decimal, rate: Decimal) -> Decimal:
    """Kalshi's taker fee per contract: rate x P x (1 - P)."""
    return rate * price * (ONE - price)


def sweeps(trades: Iterable[Trade], window_seconds: float) -> list[Sweep]:
    """Group each book's trade prints into bursts: gaps of at most ``window_seconds``."""
    out: list[Sweep] = []
    for leg in Leg:
        prints = sorted((t for t in trades if t.hit_leg is leg and not t.block), key=lambda t: t.ts)
        start: datetime | None = None
        last: datetime | None = None
        volume = ZERO
        for t in prints:
            if last is not None and (t.ts - last).total_seconds() <= window_seconds:
                volume += t.count
            else:
                if start is not None:
                    out.append(Sweep(leg, volume, start))
                start, volume = t.ts, t.count
            last = t.ts
        if start is not None:
            out.append(Sweep(leg, volume, start))
    return out


def estimate_fill_risk(
    trades: Iterable[Trade],
    quotes: Mapping[Leg, PlannedQuote],
    *,
    window_hours: float,
    sweep_window_seconds: float,
    fee_rate: Decimal,
    adverse_move: Decimal,
) -> FillRisk:
    """Replay ``window_hours`` of ``trades`` against ``quotes``: fills and their cost per day."""
    hours = Decimal(str(max(window_hours, 1 / 60)))  # at least a minute of history
    days = hours / 24
    bursts = sweeps(trades, sweep_window_seconds)
    fills = cost = ZERO
    for leg, quote in quotes.items():
        filled = sum(
            (min(quote.size, max(s.volume - quote.ahead, ZERO)) for s in bursts if s.leg is leg),
            ZERO,
        )
        per_day = filled / days
        fills += per_day
        cost += per_day * (taker_fee(quote.price, fee_rate) + adverse_move)
    return FillRisk(fills, cost, Decimal(len(bursts)) / days, hours)
