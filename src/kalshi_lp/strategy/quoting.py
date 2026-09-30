"""Quote generation.

For each market we rest two bids: one on the YES book (a YES ``bid``) and one
on the NO book (a YES ``ask``). Both legs are priced by the same routine in
their own price terms, which keeps the logic symmetric:

1. Work out the highest price we are willing to bid: never within
   ``min_edge`` of mid, never crossing the opposite best, never above
   ``max_price``.
2. Pick a base price at or below that cap:
   * ``reward``: evaluate every grid price from the reward reference price
     up to the best bid with the program's scoring model, and take the one
     that earns the largest share. Among prices whose share is within
     ``share_tolerance`` of the best, take the deepest (least likely to fill).
   * ``join``: the best bid.  * ``improve``: one tick better than the best bid.
3. Step back ``offset_ticks`` if configured.
4. Skew for inventory: holding this leg lowers its bid (buy less of it) and
   being short it raises the bid (buy it back sooner).
5. Size to the per-market position limit.

Note that bidding exactly *at* the reference price is not always good: if the
queue ahead already fills the program's Target Size, an order at the back of
that queue earns nothing. That is why ``reward`` searches instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal

from kalshi_lp.config import QuotingConfig
from kalshi_lp.core.orderbook import Orderbook
from kalshi_lp.core.pricing import PriceGrid
from kalshi_lp.core.types import ONE, ZERO, Leg, Quote
from kalshi_lp.exchange.models import Market
from kalshi_lp.strategy.rewards import (
    OwnOrder,
    RewardParams,
    SideScore,
    reference_price,
    score_side,
)

_MAX_PRICES_SEARCHED = 100  # bounds work on sub-penny grids


@dataclass(frozen=True, slots=True)
class MarketContext:
    market: Market
    book: Orderbook  # with our own orders removed
    position: Decimal  # + long YES, - long NO
    reward: RewardParams
    allow_increase: bool = True  # False: only quote legs that reduce the position
    # Our current resting order per leg (leg-terms price, real queue position if known).
    resting: Mapping[Leg, OwnOrder] = field(default_factory=dict)
    resting_age: Mapping[Leg, float] = field(default_factory=dict)  # seconds since placed
    size: Decimal | None = None  # per-side size for this market (auto sizing); None = cfg.size


@dataclass(frozen=True, slots=True)
class LegDecision:
    leg: Leg
    quote: Quote | None
    reason: str
    score: SideScore | None = None
    leg_price: Decimal | None = None  # price in the leg's own terms


class QuoteEngine:
    def __init__(self, cfg: QuotingConfig, max_position: Decimal):
        self.cfg = cfg
        self.max_position = max_position

    def quote(self, ctx: MarketContext) -> dict[Leg, LegDecision]:
        decisions = {leg: self._quote_leg(ctx, leg) for leg in Leg}
        return self._prevent_self_cross(ctx, decisions)

    # ------------------------------------------------------------------ legs

    def _size(self, ctx: MarketContext, leg: Leg) -> Decimal:
        inventory = leg.inventory(ctx.position)
        room = self.max_position - inventory
        if not ctx.allow_increase:
            room = min(room, max(-inventory, ZERO))  # only buy back what we're short
        size = min(ctx.size if ctx.size is not None else self.cfg.size, room)
        return size.to_integral_value(rounding=ROUND_FLOOR) if size > 0 else ZERO

    def _price_cap(self, ctx: MarketContext, leg: Leg, grid: PriceGrid) -> Decimal | None:
        """Highest price this leg may bid, or None if the leg must not be quoted.

        The cushion rule caps the price at the first level (walking down from
        the best bid) where others' resting size reaches ``min_cushion``
        (at most the program's full-credit depth), so
        at least that many contracts sit ahead of us and absorb a sell-off
        first. A book too thin to provide the cushion isn't quoted at all.
        """
        cap = self.cfg.max_price
        if self.cfg.min_cushion > 0:
            # Never demand more cushion than the program's full-credit depth
            # (Target Size / 5): past that point the cushion would push us to a
            # discounted price. With a 300 target, full credit ends at 60 contracts.
            depth = min(self.cfg.min_cushion, ctx.reward.reference_depth)
            cushion = reference_price(ctx.book.bids(leg), depth)
            if cushion is None:
                return None
            cap = min(cap, cushion)
        best, opp_best = ctx.book.best_bid(leg), ctx.book.best_bid(leg.opposite)
        if best is not None and opp_best is not None:
            cap = min(cap, (best + ONE - opp_best) / 2 - self.cfg.min_edge)
        if opp_best is not None:
            cap = min(cap, grid.tick_below(ONE - opp_best))  # never cross
        return grid.round_down(cap)

    def _reward_price(
        self, ctx: MarketContext, leg: Leg, grid: PriceGrid, cap: Decimal, size: Decimal
    ) -> Decimal:
        """Deepest price whose reward share is within tolerance of the best achievable."""
        own = ctx.book.bids(leg)
        top = min(own[0].price, cap)
        ref = reference_price(own, ctx.reward.reference_depth)
        price = min(ref, top) if ref is not None else top

        scored: list[tuple[Decimal, Decimal]] = []  # (price, share), deepest first
        for _ in range(_MAX_PRICES_SEARCHED):
            scored.append((price, score_side(own, price, size, ctx.reward, grid).share))
            if price >= top:
                break
            price = grid.tick_above(price)

        best_share = max(share for _, share in scored)
        if best_share <= 0:
            return top  # nothing qualifies anywhere; sit at the top of the book
        threshold = best_share * (ONE - self.cfg.share_tolerance)
        return next(p for p, share in scored if share >= threshold)

    def _base_price(
        self, ctx: MarketContext, leg: Leg, grid: PriceGrid, cap: Decimal, size: Decimal
    ) -> tuple[Decimal | None, str]:
        best = ctx.book.best_bid(leg)
        opp_best = ctx.book.best_bid(leg.opposite)

        if best is None:
            if opp_best is None:
                return None, "empty book"
            return ONE - opp_best - self.cfg.one_sided_edge, "one-sided"

        match self.cfg.placement:
            case "join":
                return best, "join"
            case "improve":
                return grid.tick_above(best), "improve"
            case _:
                return self._reward_price(ctx, leg, grid, cap, size), "reward"

    def _quote_leg(self, ctx: MarketContext, leg: Leg) -> LegDecision:
        size = self._size(ctx, leg)
        if size <= 0:
            return LegDecision(leg, None, "position limit")

        grid = ctx.market.grid if leg is Leg.YES else ctx.market.grid.mirrored()
        cap = self._price_cap(ctx, leg, grid)
        if cap is None:
            return LegDecision(leg, None, "thin book (no cushion)")
        price, how = self._base_price(ctx, leg, grid, cap, size)
        if price is None:
            return LegDecision(leg, None, how)

        for _ in range(self.cfg.offset_ticks):
            price = grid.tick_below(price)

        inventory = leg.inventory(ctx.position)
        skew = max(
            -self.cfg.max_skew, min(self.cfg.max_skew, inventory * self.cfg.skew_per_contract)
        )
        price = grid.round_down(min(price - skew, cap))

        if price < self.cfg.min_price or not grid.is_valid(price):
            return LegDecision(leg, None, f"price {price} below floor")

        score = score_side(ctx.book.bids(leg), price, size, ctx.reward, grid)
        kept = self._keep_resting(ctx, leg, grid, price, cap, size, score)
        if kept is not None:
            price, score, how = kept[0], kept[1], how + "(kept)"
        quote = Quote(ctx.market.ticker, leg.order_side, leg.to_yes_price(price), size)
        return LegDecision(leg, quote, how, score, price)

    def _keep_resting(
        self,
        ctx: MarketContext,
        leg: Leg,
        grid: PriceGrid,
        price: Decimal,
        cap: Decimal,
        size: Decimal,
        score: SideScore,
    ) -> tuple[Decimal, SideScore] | None:
        """Sticky quotes: keep the resting order's price (and queue spot) if it is still good.

        A resting order is never kept if it is unsafe: above the cap (which
        covers crossing, the edge from mid, and the cushion) or below the
        price floor. Otherwise it is kept if it is younger than
        ``min_quote_life_seconds``, or if it is no more aggressive than the
        new price and its share, using its real queue position, is within
        ``share_tolerance`` of the new price's. Moving costs our place in the
        queue, so this avoids churn for nothing.
        """
        current = ctx.resting.get(leg)
        if self.cfg.placement != "reward" or current is None or current.price == price:
            return None
        if not (self.cfg.min_price <= current.price <= cap) or not grid.is_valid(current.price):
            return None  # unsafe: move now
        kept = score_side(ctx.book.bids(leg), current.price, size, ctx.reward, grid, current.ahead)
        age = ctx.resting_age.get(leg)
        young = age is not None and age < self.cfg.min_quote_life_seconds
        holding = leg.inventory(ctx.position) > 0
        if young and not (holding and current.price > price):
            return current.price, kept
        if current.price > price:
            return None  # never hold a riskier quote than we'd choose now
        if kept.share >= score.share * (ONE - self.cfg.share_tolerance):
            return current.price, kept
        return None

    def _prevent_self_cross(
        self, ctx: MarketContext, decisions: dict[Leg, LegDecision]
    ) -> dict[Leg, LegDecision]:
        """YES bid + NO bid must stay below $1, or our two quotes would trade."""
        yes, no = decisions[Leg.YES], decisions[Leg.NO]
        if yes.leg_price is None or no.leg_price is None or yes.leg_price + no.leg_price < ONE:
            return decisions
        # Back off the leg that would add to inventory.
        leg = Leg.YES if ctx.position >= 0 else Leg.NO
        grid = ctx.market.grid if leg is Leg.YES else ctx.market.grid.mirrored()
        other = decisions[leg.opposite].leg_price or ZERO
        price = grid.tick_below(ONE - other)
        old = decisions[leg]
        if price < self.cfg.min_price or old.quote is None:
            decisions[leg] = LegDecision(leg, None, "self-cross")
        else:
            size = old.quote.size
            score = score_side(ctx.book.bids(leg), price, size, ctx.reward, grid)
            quote = Quote(old.quote.ticker, old.quote.side, leg.to_yes_price(price), size)
            decisions[leg] = LegDecision(leg, quote, old.reason + "+uncross", score, price)
        return decisions
