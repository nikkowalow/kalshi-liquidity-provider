"""Turn desired quotes plus current resting orders into a minimal set of actions.

Queue position is worth money (earlier orders at a price fill first, and
program credit is by size, not time), so we avoid touching orders that are
already right:

* Resting at the desired price with at least the desired size: keep; shrink
  any excess with ``decrease`` (preserves priority).
* Resting at the desired price but short from partial fills: keep, and add a
  top-up order for the difference once it exceeds the tolerance.
* Resting at any other price, or on a side we no longer want: cancel.

Every action carries a short human reason (``Plan.why``), which the journal
records and the dashboard blotter shows.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from kalshi_lp.core.types import ZERO, Quote, Side
from kalshi_lp.exchange.models import Order


@dataclass(slots=True)
class Plan:
    cancels: list[Order] = field(default_factory=list)
    decreases: list[tuple[Order, Decimal]] = field(default_factory=list)
    creates: list[Quote] = field(default_factory=list)
    # Immediate-or-cancel orders that cross the book to close a position (see bot flattening).
    exits: list[Quote] = field(default_factory=list)
    # Resting (post-only) orders that close a position at a set price (passive exits).
    unwinds: list[Quote] = field(default_factory=list)
    # Why each action happens: by order id (cancels, decreases) or quote key (creates).
    why: dict[str | tuple[str, Side, Decimal], str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.cancels or self.decreases or self.creates or self.exits or self.unwinds)

    def extend(self, other: Plan) -> None:
        self.cancels += other.cancels
        self.decreases += other.decreases
        self.creates += other.creates
        self.exits += other.exits
        self.unwinds += other.unwinds
        self.why.update(other.why)

    def cancel(self, orders: Iterable[Order], reason: str) -> None:
        for order in orders:
            self.cancels.append(order)
            self.why[order.order_id] = reason

    def reason_for_order(self, order: Order) -> str:
        return self.why.get(order.order_id, "")

    def reason_for_quote(self, ticker: str, side: Side, price: Decimal) -> str:
        return self.why.get((ticker, side, price), "")


def _px(price: Decimal) -> str:
    return f"{price:.2f}" if price == price.quantize(Decimal("0.01")) else f"{price.normalize():f}"


def reconcile(
    desired: Iterable[Quote],
    resting: Sequence[Order],
    *,
    min_topup: Decimal = Decimal(1),
    reasons: Mapping[Side, str] | None = None,
) -> Plan:
    """Plan the actions to move ``resting`` orders (one market) to ``desired``.

    ``reasons`` explains, per side, why the strategy wants what it wants (its
    quote reason, e.g. "reward", or why that side isn't quoted, e.g. "position
    limit"); it is folded into each action's reason.
    """
    plan = Plan()
    wanted = {q.side: q for q in desired}
    reasons = reasons or {}

    for side in Side:
        orders = [o for o in resting if o.side is side]
        quote = wanted.get(side)
        because = reasons.get(side, "")
        if quote is None:
            plan.cancel(orders, f"stop quoting {side.value}" + (f": {because}" if because else ""))
            continue

        at_price = sorted(
            (o for o in orders if o.yes_price == quote.price),
            key=lambda o: o.remaining,
            reverse=True,
        )
        moved = [o for o in orders if o.yes_price != quote.price]
        for order in moved:
            plan.cancel(
                [order],
                f"reprice {_px(order.yes_price)} -> {_px(quote.price)}"
                + (f" ({because})" if because else ""),
            )

        still_needed = quote.size
        for order in at_price:
            if still_needed <= 0:
                plan.cancel([order], "duplicate order at the same price")
            elif order.remaining > still_needed:
                plan.decreases.append((order, still_needed))
                plan.why[order.order_id] = f"shrink {order.remaining:f} -> {still_needed:f}" + (
                    f" ({because})" if because else ""
                )
                still_needed = ZERO
            else:
                still_needed -= order.remaining

        if still_needed >= min_topup or (not at_price and still_needed > 0):
            plan.creates.append(Quote(quote.ticker, side, quote.price, still_needed))
            if at_price:  # a partial fill, or a bigger size (e.g. a larger budget)
                what = f"top up {quote.size - still_needed:f} -> {quote.size:f}"
            elif moved:
                what = f"reprice from {_px(max(o.yes_price for o in moved))}"
            else:
                what = "new quote"
            plan.why[(quote.ticker, side, quote.price)] = what + (
                f" ({because})" if because else ""
            )

    return plan
