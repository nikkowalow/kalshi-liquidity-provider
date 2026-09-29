"""Live estimate of the rewards the bot is actually earning.

Kalshi scores each rewarded market once per second at a random moment. This
tracker does the same thing on the live book: once per second, at a random
offset, it scores our resting orders using their real queue positions (from
Kalshi's queue-position endpoint), and credits

    earned = score / 2 * reward_per_day / 86,400

per snapshot (see :func:`strategy.rewards.earned_per_snapshot`). Summed over
the session, that is what the program's formula pays for the same snapshots,
as long as our view of the book matches Kalshi's.

What it cannot see: Kalshi's snapshot happens at its own random instant, not
ours, and queue positions are refreshed every few seconds rather than
continuously. Treat the numbers as a close estimate, not a statement.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from kalshi_lp.core.orderbook import Orderbook
from kalshi_lp.core.pricing import PriceGrid
from kalshi_lp.core.types import ZERO, Leg
from kalshi_lp.exchange.models import Order
from kalshi_lp.strategy.rewards import (
    OwnOrder,
    RewardParams,
    earned_per_snapshot,
    score_snapshot_side,
    snapshot_score,
)

_RATE_WINDOW_SECONDS = 600.0


@dataclass(slots=True)
class MarketRewardStats:
    snapshots: int = 0  # snapshots we scored
    scored: int = 0  # ...where we earned something (both sides met Target Size, we had a share)
    score_sum: Decimal = ZERO
    earned: Decimal = ZERO  # dollars
    recent: deque[tuple[float, Decimal]] = field(default_factory=deque)  # (time, earned)

    @property
    def avg_score(self) -> Decimal:
        return self.score_sum / self.snapshots if self.snapshots else ZERO

    def hourly_rate(self, now: float) -> Decimal:
        while self.recent and now - self.recent[0][0] > _RATE_WINDOW_SECONDS:
            self.recent.popleft()
        if not self.recent:
            return ZERO
        span = max(now - self.recent[0][0], 1.0)
        return sum((e for _, e in self.recent), ZERO) * Decimal(3600) / Decimal(span)


def own_orders_by_leg(
    orders: Iterable[Order], queue_ahead: Mapping[str, Decimal]
) -> dict[Leg, list[OwnOrder]]:
    by_leg: dict[Leg, list[OwnOrder]] = {Leg.YES: [], Leg.NO: []}
    for o in orders:
        by_leg[o.leg].append(OwnOrder(o.leg_price, o.remaining, queue_ahead.get(o.order_id)))
    return by_leg


def strip_own(book: Orderbook, own: Mapping[Leg, list[OwnOrder]]) -> Orderbook:
    levels: dict[Leg, dict[Decimal, Decimal]] = {Leg.YES: {}, Leg.NO: {}}
    for leg, orders in own.items():
        for o in orders:
            levels[leg][o.price] = levels[leg].get(o.price, ZERO) + o.size
    return book.without(levels)


def score_market(
    book: Orderbook,
    orders: Iterable[Order],
    queue_ahead: Mapping[str, Decimal],
    params: RewardParams,
    grid: PriceGrid,
    *,
    in_book: bool = True,
) -> Decimal:
    """Our snapshot score (0..2) for one market's live book and resting orders.

    ``in_book=False`` scores hypothetical orders that are not in ``book`` (dry-run).
    """
    own = own_orders_by_leg(orders, queue_ahead)
    others = strip_own(book, own) if in_book else book
    yes = score_snapshot_side(others.bids(Leg.YES), own[Leg.YES], params, grid)
    no = score_snapshot_side(others.bids(Leg.NO), own[Leg.NO], params, grid.mirrored())
    return snapshot_score(yes, no)


class RewardTracker:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self.stats: dict[str, MarketRewardStats] = {}
        self.started = clock()

    def record(self, ticker: str, score: Decimal, params: RewardParams) -> Decimal:
        """Credit one snapshot and return the dollars it earned."""
        stats = self.stats.setdefault(ticker, MarketRewardStats())
        stats.snapshots += 1
        earned = earned_per_snapshot(score, params)
        if score > 0:
            stats.scored += 1
        stats.score_sum += score
        stats.earned += earned
        stats.recent.append((self._clock(), earned))
        return earned

    def sample(
        self,
        ticker: str,
        book: Orderbook | None,
        orders: Iterable[Order],
        queue_ahead: Mapping[str, Decimal],
        params: RewardParams,
        grid: PriceGrid,
        *,
        in_book: bool = True,
    ) -> Decimal:
        if book is None or params.reward_per_day <= 0:
            return ZERO
        score = score_market(book, orders, queue_ahead, params, grid, in_book=in_book)
        return self.record(ticker, score, params)

    @property
    def total_earned(self) -> Decimal:
        return sum((s.earned for s in self.stats.values()), ZERO)

    def total_hourly_rate(self) -> Decimal:
        now = self._clock()
        return sum((s.hourly_rate(now) for s in self.stats.values()), ZERO)

    def report_lines(self) -> list[str]:
        now = self._clock()
        hours = Decimal(max(now - self.started, 1.0)) / 3600
        lines = [
            f"REWARDS est. earned ${self.total_earned:.4f} this session "
            f"({hours:.2f}h), current rate ${self.total_hourly_rate():.4f}/h "
            f"(${self.total_hourly_rate() * 24:.2f}/day)"
        ]
        for ticker, s in sorted(self.stats.items(), key=lambda kv: -kv[1].earned):
            lines.append(
                f"  {ticker:<44} ${s.earned:.4f}  ${s.hourly_rate(now):.4f}/h  "
                f"avg score {s.avg_score:.4f}/2  ({s.scored}/{s.snapshots} snapshots paying)"
            )
        return lines
