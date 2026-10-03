"""Model of Kalshi's Liquidity Incentive Program scoring.

Per the program rules (help.kalshi.com, "Liquidity Incentive Program"):

* Once per second, at a random moment, Kalshi snapshots each eligible market.
* The YES bid book and NO bid book are scored separately. On each side, orders
  qualify as they build toward the program's *Target Size*, walking down from
  the best bid. If a side's total depth is below Target Size, the snapshot is
  excluded for everyone.
* The *reference price* is the first level, walking down from the best bid,
  where cumulative size reaches Target Size / 5. Orders at or better than it
  get full credit; orders ``n`` ticks below it get ``discount_factor ** n``.
* Your share on a side is your raw score over all raw scores on that side.
  Your snapshot score is your YES share plus your NO share (max 2.0), and you
  are paid your fraction of all participants' snapshot scores in the period.

So resting near the top of each book, within the first Target Size of depth,
earns rewards, and resting at the reference price rather than the best bid
earns the same credit with less chance of being filled.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from kalshi_lp.core.orderbook import Level, Orderbook
from kalshi_lp.core.pricing import PriceGrid
from kalshi_lp.core.types import ZERO, Leg


@dataclass(frozen=True, slots=True)
class RewardParams:
    target_size: Decimal
    discount_factor: Decimal
    full_credit_fraction: Decimal = Decimal("0.2")
    reward_per_day: Decimal = ZERO  # dollars, 0 when the market has no program
    period_end: datetime | None = None  # when the current program period ends (payout follows)
    period_start: datetime | None = None
    period_reward: Decimal = ZERO  # dollars the program pays out over the whole period

    def days_left(self, now: datetime | None = None) -> Decimal:
        if self.period_end is None:
            return ZERO
        seconds = (self.period_end - (now or datetime.now(UTC))).total_seconds()
        return Decimal(max(seconds, 0.0)) / 86_400

    @property
    def reference_depth(self) -> Decimal:
        return self.target_size * self.full_credit_fraction


@dataclass(frozen=True, slots=True)
class SideScore:
    share: Decimal  # our normalized share of this side's score, 0..1
    meets_target: bool
    reference_price: Decimal | None


def reference_price(bids: Sequence[Level], depth: Decimal) -> Decimal | None:
    """First price, walking down from the best bid, where cumulative size reaches ``depth``."""
    cumulative = ZERO
    for level in bids:
        cumulative += level.size
        if cumulative >= depth:
            return level.price
    return None


@dataclass(frozen=True, slots=True)
class OwnOrder:
    """One of our resting orders on a side, for scoring."""

    price: Decimal  # in the side's own terms
    size: Decimal
    ahead: Decimal | None = None  # others' contracts ahead of it at its price; None = back of queue


def score_snapshot_side(
    others: Sequence[Level],
    own: Sequence[OwnOrder],
    params: RewardParams,
    grid: PriceGrid,
) -> SideScore:
    """Our share of one side's score for a snapshot, exactly as the program rules compute it.

    ``others`` is the side's bid ladder (best first) excluding our orders.
    ``own`` are our orders on this side, each placed in the price level's
    time-priority queue behind ``ahead`` contracts from other traders.
    Prices are in the side's own terms (NO prices for the NO side).
    """
    prices = sorted({lvl.price for lvl in others} | {o.price for o in own}, reverse=True)
    others_at = {lvl.price: lvl.size for lvl in others}

    # Build the full queue, best price first, time priority within a level.
    entries: list[tuple[Decimal, Decimal, bool]] = []  # (price, size, is_ours)
    for price in prices:
        remaining_others = others_at.get(price, ZERO)
        mine = sorted(
            (o for o in own if o.price == price),
            key=lambda o: remaining_others if o.ahead is None else o.ahead,
        )
        placed = ZERO
        for order in mine:
            ahead = remaining_others if order.ahead is None else min(order.ahead, remaining_others)
            if ahead > placed:
                entries.append((price, ahead - placed, False))
                placed = ahead
            entries.append((price, order.size, True))
        if remaining_others > placed:
            entries.append((price, remaining_others - placed, False))
    return _score_entries(entries, params, grid)


def score_side(
    others: Sequence[Level],
    our_price: Decimal,
    our_size: Decimal,
    params: RewardParams,
    grid: PriceGrid,
    ahead: Decimal | None = None,
) -> SideScore:
    """Estimate our share if we rest one bid at ``our_price``.

    By default the order joins the back of the queue at its price; pass
    ``ahead`` for an existing order whose real queue position is known.
    """
    return score_snapshot_side(others, [OwnOrder(our_price, our_size, ahead)], params, grid)


def _score_entries(
    entries: Sequence[tuple[Decimal, Decimal, bool]], params: RewardParams, grid: PriceGrid
) -> SideScore:
    total_depth = sum((size for _, size, _ in entries), ZERO)
    ref = reference_price([Level(p, s) for p, s, _ in entries], params.reference_depth)
    if total_depth < params.target_size or ref is None:
        return SideScore(ZERO, False, ref)

    ours = total = ZERO
    remaining = params.target_size
    for price, size, is_ours in entries:
        if remaining <= 0:
            break
        counted = min(size, remaining)
        remaining -= counted
        multiplier = (
            Decimal(1) if price >= ref else params.discount_factor ** grid.ticks_between(price, ref)
        )
        raw = counted * multiplier
        total += raw
        if is_ours:
            ours += raw
    share = ours / total if total > 0 else ZERO
    return SideScore(share, True, ref)


def snapshot_score(yes: SideScore, no: SideScore) -> Decimal:
    """Our score for one snapshot (0..2). Zero if either side misses Target Size."""
    if not (yes.meets_target and no.meets_target):
        return ZERO
    return yes.share + no.share


def expected_daily_reward(yes: SideScore, no: SideScore, params: RewardParams) -> Decimal:
    """Daily payout if this snapshot's book persisted all day.

    Payout = (our summed snapshot scores / everyone's summed scores) x reward x
    (eligible snapshots / all snapshots). Every eligible snapshot hands out a
    total score of 2 (a full share per side), so each one-second snapshot
    pays us ``score / 2`` of that second's slice of the reward.
    """
    return params.reward_per_day * snapshot_score(yes, no) / 2


# Competition levels by "room" (see competition()). Kalshi's site shows High/Medium/Low
# but the API doesn't expose it, so this is our own measure.
HIGH_COMPETITION_ROOM = Decimal("0.01")  # Target Size fills within 1c of the best bid
MEDIUM_COMPETITION_ROOM = Decimal("0.03")


@dataclass(frozen=True, slots=True)
class Competition:
    """How crowded a market's qualifying depth is.

    Only the first Target Size contracts on a side are scored, so more
    competitors don't shrink the reward pool; they compress it toward the top
    of the book. ``room`` is how far below the best bid others' orders reach
    Target Size, on the tighter side. Little room means you must quote at the
    top to earn at all (fill risk), and any better-priced order pushes you out
    of the counted depth. Plenty of room means you can rest deep and still
    count. ``room`` is None when neither side's others reach Target Size.
    """

    level: str  # "low" | "medium" | "high"
    room: Decimal | None


def side_room(bids: Sequence[Level], target_size: Decimal) -> Decimal | None:
    """Dollars from the best bid down to where ``bids`` reach ``target_size``, or None."""
    boundary = reference_price(bids, target_size)
    return None if boundary is None or not bids else bids[0].price - boundary


def competition(others: Orderbook, params: RewardParams) -> Competition:
    """Competition from a book of *other* traders' orders (ours removed)."""
    rooms = [side_room(others.bids(leg), params.target_size) for leg in Leg]
    known = [r for r in rooms if r is not None]
    if not known:
        return Competition("low", None)
    room = min(known)
    if room <= HIGH_COMPETITION_ROOM:
        return Competition("high", room)
    if room <= MEDIUM_COMPETITION_ROOM:
        return Competition("medium", room)
    return Competition("low", room)


def earned_per_snapshot(score: Decimal, params: RewardParams) -> Decimal:
    """Dollars one snapshot (one second) with this score earns."""
    return params.reward_per_day / 86_400 * score / 2
