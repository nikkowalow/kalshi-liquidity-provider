"""Where each of our resting orders stands in its side's queue.

The Liquidity Incentive Program counts, per side, only the first Target Size
contracts of depth walking down from the best bid. An order's *rank* is how
many contracts rest ahead of it on its side:

    ahead = others' contracts at better prices
          + others' contracts ahead of it at its own price (time priority)

If ``ahead + size <= target`` the whole order qualifies; if ``ahead < target``
part of it does; otherwise it earns nothing that second.

Kalshi's queue-position endpoint reports "contracts preceding the order in the
queue" without saying whether better prices are included. We read it as
within the price level and cap it at the level's size from others, so an
ambiguous figure can only make the rank look worse, never better.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from kalshi_lp.core.orderbook import Orderbook
from kalshi_lp.core.types import ZERO
from kalshi_lp.exchange.models import Order
from kalshi_lp.strategy.rewards import RewardParams, reference_price


def queue_report(
    order: Order,
    book: Orderbook | None,
    others: Orderbook | None,
    queue_ahead: Mapping[str, Decimal],
    params: RewardParams | None,
) -> dict[str, Any]:
    """Rank of ``order`` in its side's book, and whether it's inside the program's Target Size.

    ``book`` is the full live book (ours included; the program's reference
    price is computed on it); ``others`` is the book with our orders removed.
    """
    report: dict[str, Any] = {
        "leg": order.leg.value,
        "leg_price": order.leg_price,
        "queue_ahead": queue_ahead.get(order.order_id),
        "ahead_better": None,
        "ahead_total": None,
        "target_size": params.target_size if params else None,
        "in_target": "unknown",
        "full_credit": None,
    }
    if book is None or others is None:
        return report

    ladder = others.bids(order.leg)
    better = sum((lvl.size for lvl in ladder if lvl.price > order.leg_price), ZERO)
    at_level = next((lvl.size for lvl in ladder if lvl.price == order.leg_price), ZERO)
    raw = report["queue_ahead"]
    # Unknown queue position (just placed, not refreshed yet): assume the back of the level.
    same_level = at_level if raw is None else min(raw, at_level)
    ahead = better + same_level
    report.update(ahead_better=better, ahead_total=ahead)

    if params is not None:
        target = params.target_size
        if ahead + order.remaining <= target:
            report["in_target"] = "in"
        elif ahead < target:
            report["in_target"] = "partial"
        else:
            report["in_target"] = "out"
        ref = reference_price(book.bids(order.leg), params.reference_depth)
        report["full_credit"] = ref is not None and order.leg_price >= ref
    return report
