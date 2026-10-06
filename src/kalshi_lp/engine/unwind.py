"""How to get out of a position after a fill (risk.flatten_on_fill).

A fill usually comes from a sweep that just ate the bids we were resting in.
Crossing the book right then sells into whatever the sweep left, which is the
worst price of the whole episode: in the bot's first week, prices an hour after
a fill were back near the entry far more often than not. So in ``passive`` mode
an exit order rests at the entry price instead, and the bot only crosses when:

* the price we could buy the position back at (the other side's offer) has
  dropped ``unwind_stop`` below our entry: the market, not just an emptied bid,
  now values it lower;
* the bid comes back to the entry: take it;
* ``unwind_seconds`` have passed;
* the caller says it's an emergency (market about to close, no entry price known).

Everything here is in the held leg's terms: holding YES means selling into the
YES bids; holding NO means selling into the NO bids (a YES bid at 1 - price).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from kalshi_lp.core.orderbook import Orderbook
from kalshi_lp.core.pricing import PriceGrid
from kalshi_lp.core.types import ONE, Leg, Side

# Off: every fill crosses the book at once, whatever risk.exit_mode says. On Oct 5 resting
# at the entry never filled after a real move: the bot waited out unwind_seconds and then
# crossed at a worse price (KXTRUMPPHOTO-26OCT11-5: 0.23 -> 0.16). Set True to bring it back.
PASSIVE_UNWIND_ENABLED = False


@dataclass(frozen=True, slots=True)
class Entry:
    yes_price: Decimal  # average YES price of the position (a long NO's is 1 - its NO price)
    opened: float  # monotonic time the position was opened


@dataclass(frozen=True, slots=True)
class ExitPlan:
    action: Literal["cross", "rest"]
    side: Side  # YES-terms order side that reduces the position
    price: Decimal | None  # rest: the resting YES price; cross: None (caller prices it)
    why: str


def plan_exit(
    position: Decimal,
    entry: Entry | None,
    book: Orderbook | None,
    grid: PriceGrid,
    now: float,
    *,
    seconds: float,
    stop: Decimal,
    giveup: Decimal,
    emergency: str | None = None,
) -> ExitPlan:
    """Cross the book now, or rest an exit order (and at what price)."""
    leg = Leg.YES if position > 0 else Leg.NO
    side = Side.ASK if position > 0 else Side.BID
    if emergency:
        return ExitPlan("cross", side, None, emergency)
    if entry is None:
        return ExitPlan("cross", side, None, "entry price unknown")
    held = entry.yes_price if leg is Leg.YES else ONE - entry.yes_price
    age = now - entry.opened
    if age >= seconds:
        return ExitPlan("cross", side, None, f"not out after {seconds / 60:g}m")
    bid = book.best_bid(leg) if book else None  # where we could sell now
    other = book.best_bid(leg.opposite) if book else None
    offer = None if other is None else ONE - other  # where others sell our leg
    target = held - giveup
    if bid is not None and bid >= target:
        return ExitPlan("cross", side, None, f"bid {bid} is back at the entry {held}")
    if offer is not None and offer <= held - stop:
        return ExitPlan("cross", side, None, f"stop: offered at {offer}, entry {held}")
    leg_grid = grid if leg is Leg.YES else grid.mirrored()
    price = leg_grid.round_up(target)
    if bid is not None and price <= bid:  # rounding met the bid: post-only can't rest there
        return ExitPlan("cross", side, None, f"bid {bid} is back at the entry {held}")
    yes_price = price if leg is Leg.YES else ONE - price
    left = (seconds - age) / 60
    return ExitPlan(
        "rest", side, yes_price, f"unwind at entry {held} ({left:.0f}m left, stop {held - stop})"
    )
