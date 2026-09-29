"""Turn desired quotes plus current resting orders into a minimal set of actions.

Queue position is worth money (earlier orders at a price fill first, and
program credit is by size, not time), so we avoid touching orders that are
already right:

* Resting at the desired price with at least the desired size: keep; shrink
  any excess with ``decrease`` (preserves priority).
* Resting at the desired price but short from partial fills: keep, and add a
  top-up order for the difference once it exceeds the tolerance.
* Resting at any other price, or on a side we no longer want: cancel.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from kalshi_lp.core.types import ZERO, Quote, Side
from kalshi_lp.exchange.models import Order


@dataclass(slots=True)
class Plan:
    cancels: list[Order] = field(default_factory=list)
    decreases: list[tuple[Order, Decimal]] = field(default_factory=list)
    creates: list[Quote] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.cancels or self.decreases or self.creates)

    def extend(self, other: Plan) -> None:
        self.cancels += other.cancels
        self.decreases += other.decreases
        self.creates += other.creates


def reconcile(
    desired: Iterable[Quote],
    resting: Sequence[Order],
    *,
    min_topup: Decimal = Decimal(1),
) -> Plan:
    """Plan the actions to move ``resting`` orders (one market) to ``desired``."""
    plan = Plan()
    wanted = {q.side: q for q in desired}

    for side in Side:
        orders = [o for o in resting if o.side is side]
        quote = wanted.get(side)
        if quote is None:
            plan.cancels += orders
            continue

        at_price = sorted(
            (o for o in orders if o.yes_price == quote.price),
            key=lambda o: o.remaining,
            reverse=True,
        )
        plan.cancels += [o for o in orders if o.yes_price != quote.price]

        still_needed = quote.size
        for order in at_price:
            if still_needed <= 0:
                plan.cancels.append(order)
            elif order.remaining > still_needed:
                plan.decreases.append((order, still_needed))
                still_needed = ZERO
            else:
                still_needed -= order.remaining

        if still_needed >= min_topup or (not at_price and still_needed > 0):
            plan.creates.append(Quote(quote.ticker, side, quote.price, still_needed))

    return plan
