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
from decimal import Decimal

from kalshi_lp.core.orderbook import Level
from kalshi_lp.core.pricing import PriceGrid
from kalshi_lp.core.types import ZERO


@dataclass(frozen=True, slots=True)
class RewardParams:
    target_size: Decimal
    discount_factor: Decimal
    full_credit_fraction: Decimal = Decimal("0.2")
    reward_per_day: Decimal = ZERO  # dollars, 0 when the market has no program

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


def score_side(
    others: Sequence[Level],
    our_price: Decimal,
    our_size: Decimal,
    params: RewardParams,
    grid: PriceGrid,
) -> SideScore:
    """Estimate our share of one side's score if we add a bid at ``our_price``.

    ``others`` is the side's bid ladder (best first) with our own orders
    removed. We assume our order joins the back of the queue at its price.
    Prices are in that side's own terms (NO prices for the NO side).
    """
    # (price, size, is_ours), best price first, ours last within its level.
    entries = [(lvl.price, lvl.size, False) for lvl in others if lvl.price >= our_price]
    entries.append((our_price, our_size, True))
    entries += [(lvl.price, lvl.size, False) for lvl in others if lvl.price < our_price]

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


def expected_daily_reward(yes: SideScore, no: SideScore, params: RewardParams) -> Decimal:
    """Rough daily payout if the current book persisted all day.

    Each snapshot's total score across participants is 2 (one per side), so
    our fraction of the period is the average of our two side shares. A
    snapshot where either side misses Target Size pays nobody.
    """
    if not (yes.meets_target and no.meets_target):
        return ZERO
    return params.reward_per_day * (yes.share + no.share) / 2
