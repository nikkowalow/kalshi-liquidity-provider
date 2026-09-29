"""Capital budget: one dollar cap on everything the bot has at work.

    capital in use = cost of positions + cash locked by resting orders

A resting bid for ``n`` YES at ``p`` locks ``n * p``; a resting ask (a NO bid)
locks ``n * (1 - p)``. When an order fills, its locked cash becomes position
cost of the same amount, so fills don't grow the total. Only placing orders
does. Checking every placement against the budget therefore keeps capital in
use at or under ``max_capital`` (plus fees).

Orders that shrink an existing position are exempt up to the position's size:
Kalshi nets YES against NO, so selling what you hold reduces risk rather than
adding it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from decimal import ROUND_FLOOR, Decimal

from kalshi_lp.core.types import ONE, ZERO, Quote, Side
from kalshi_lp.exchange.models import Order, Position

log = logging.getLogger(__name__)


def unit_collateral(side: Side, yes_price: Decimal) -> Decimal:
    """Cash one contract of a resting order locks."""
    return yes_price if side is Side.BID else ONE - yes_price


def reduces_position(side: Side, position: Decimal) -> bool:
    return (side is Side.ASK and position > 0) or (side is Side.BID and position < 0)


def position_cost(position: Position | None, live_contracts: Decimal) -> Decimal:
    """Cost of a position, conservatively including fills whose cost update hasn't arrived.

    The fill stream updates the contract count before the cost basis. Any
    extra contracts beyond the last full position record are charged $1
    each, the most a contract can cost.
    """
    recorded = position.position if position else ZERO
    cost = position.market_exposure if position else ZERO
    unrecorded = abs(live_contracts) - abs(recorded)
    return cost + max(unrecorded, ZERO)


def capital_in_use(
    tickers: Iterable[str],
    positions: Mapping[str, Position],
    live_positions: Mapping[str, Decimal],
    orders: Iterable[Order],
) -> Decimal:
    total = sum(
        (position_cost(positions.get(t), live_positions.get(t, ZERO)) for t in set(tickers)), ZERO
    )
    exempt_left = {t: abs(p) for t, p in live_positions.items()}
    for o in orders:
        charged = o.remaining
        if reduces_position(o.side, live_positions.get(o.ticker, ZERO)):
            free = min(charged, exempt_left.get(o.ticker, ZERO))
            exempt_left[o.ticker] = exempt_left.get(o.ticker, ZERO) - free
            charged -= free
        total += unit_collateral(o.side, o.yes_price) * charged
    return total


def fit_to_budget(
    creates: list[Quote], available: Decimal, live_positions: Mapping[str, Decimal]
) -> tuple[list[Quote], Decimal]:
    """Trim new orders so their locked cash fits in ``available``.

    Position-reducing orders go first and are free up to the position's size.
    The rest are filled in order and shrunk to whole contracts that fit;
    anything that can't fit even one contract is dropped. Returns the kept
    quotes and the budget left over.
    """
    exempt_left = {t: abs(p) for t, p in live_positions.items()}
    ordered = sorted(
        creates,
        key=lambda q: not reduces_position(q.side, live_positions.get(q.ticker, ZERO)),
    )
    kept: list[Quote] = []
    for quote in ordered:
        unit = unit_collateral(quote.side, quote.price)
        free = ZERO
        if reduces_position(quote.side, live_positions.get(quote.ticker, ZERO)):
            free = min(quote.size, exempt_left.get(quote.ticker, ZERO))
            exempt_left[quote.ticker] = exempt_left.get(quote.ticker, ZERO) - free
        paid = quote.size - free
        affordable = (available / unit).to_integral_value(ROUND_FLOOR) if unit > 0 else paid
        paid = min(paid, max(affordable, ZERO))
        size = free + paid
        if size <= 0:
            continue
        available -= paid * unit
        kept.append(
            quote if size == quote.size else Quote(quote.ticker, quote.side, quote.price, size)
        )
    return kept, available
