"""Live market and account state, maintained from the WebSocket feed.

The bot reads everything it quotes from here: order books, the bot's own
resting orders, positions, recent fills, and queue positions. Writers are the
stream handlers (real-time) plus the bot itself (REST order responses and
periodic REST reconciliation, which corrects any drift).

Every change marks the affected market *dirty* and sets :attr:`changed`, so
the quoting loop can wake up and requote only what moved.

Trust: a market's book is *healthy* only between an ``orderbook_snapshot`` and
the next disconnect or sequence gap. The bot never quotes from an unhealthy
book.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable, Mapping
from decimal import Decimal
from typing import Any

from kalshi_lp.core.orderbook import Level, Orderbook
from kalshi_lp.core.types import ZERO, to_decimal
from kalshi_lp.exchange.models import Order, Position

_TOMBSTONE_TTL = 120.0  # seconds to remember orders we know are gone


class LiveBook:
    """Mutable price -> size ladders for one market."""

    __slots__ = ("no", "yes")

    def __init__(self) -> None:
        self.yes: dict[Decimal, Decimal] = {}
        self.no: dict[Decimal, Decimal] = {}

    @classmethod
    def from_levels(cls, yes: Iterable[Any] | None, no: Iterable[Any] | None) -> LiveBook:
        book = cls()
        for ladder, raw in ((book.yes, yes), (book.no, no)):
            for price, size in raw or []:
                ladder[to_decimal(price)] = to_decimal(size)
        return book

    def apply(self, side: str, price: Decimal, delta: Decimal) -> None:
        ladder = self.yes if side == "yes" else self.no
        size = ladder.get(price, ZERO) + delta
        if size > 0:
            ladder[price] = size
        else:
            ladder.pop(price, None)

    def freeze(self, ticker: str) -> Orderbook:
        def levels(ladder: dict[Decimal, Decimal]) -> tuple[Level, ...]:
            return tuple(Level(p, s) for p, s in sorted(ladder.items(), reverse=True) if s > 0)

        return Orderbook(ticker, levels(self.yes), levels(self.no))


class MarketState:
    def __init__(self, prefix: str, clock: Callable[[], float] = time.monotonic):
        self.prefix = f"{prefix}-"
        self._clock = clock
        self._books: dict[str, LiveBook] = {}
        self._healthy: set[str] = set()
        self.orders: dict[str, Order] = {}  # the bot's resting orders by order_id
        self.first_seen: dict[str, float] = {}  # order_id -> when we first knew of it
        self._tombstones: dict[str, float] = {}
        self.positions: dict[str, Position] = {}
        self._live_position: dict[str, Decimal] = {}
        self.queue_ahead: dict[str, Decimal] = {}  # order_id -> contracts ahead in queue
        self.balance: Decimal | None = None
        self.dirty: set[str] = set()
        self.changed = asyncio.Event()
        # Observers of notable feed events (fills, connection changes), e.g. the run journal.
        self.listeners: list[Callable[[str, dict[str, Any]], None]] = []

    def emit(self, kind: str, data: dict[str, Any]) -> None:
        for listener in self.listeners:
            listener(kind, data)

    # ------------------------------------------------------------ notification

    def _touch(self, ticker: str) -> None:
        self.dirty.add(ticker)
        self.changed.set()

    def mark_dirty(self, tickers: Iterable[str]) -> None:
        """Have the quoting loop requote ``tickers`` now, though nothing changed in them."""
        self.dirty.update(tickers)
        self.changed.set()

    def take_dirty(self) -> set[str]:
        dirty, self.dirty = self.dirty, set()
        self.changed.clear()
        return dirty

    # ------------------------------------------------------------------ books

    def set_snapshot(
        self, ticker: str, yes: Iterable[Any] | None, no: Iterable[Any] | None
    ) -> None:
        self._books[ticker] = LiveBook.from_levels(yes, no)
        self._healthy.add(ticker)
        self._touch(ticker)

    def apply_delta(self, ticker: str, side: str, price: Decimal, delta: Decimal) -> None:
        book = self._books.get(ticker)
        if book is None or ticker not in self._healthy:
            return  # deltas before a snapshot are meaningless
        book.apply(side, price, delta)
        self._touch(ticker)

    def invalidate(self, tickers: Iterable[str] | None = None) -> None:
        """Mark books untrustworthy (disconnect, sequence gap) until the next snapshot."""
        targets = set(self._healthy if tickers is None else tickers)
        self._healthy -= targets
        for ticker in targets:
            self._touch(ticker)

    def forget(self, tickers: Iterable[str]) -> None:
        for ticker in tickers:
            self._books.pop(ticker, None)
            self._healthy.discard(ticker)

    def is_healthy(self, ticker: str) -> bool:
        return ticker in self._healthy

    def book(self, ticker: str) -> Orderbook | None:
        live = self._books.get(ticker)
        if live is None or ticker not in self._healthy:
            return None
        return live.freeze(ticker)

    # ----------------------------------------------------------------- orders

    def is_ours(self, client_order_id: str | None) -> bool:
        return bool(client_order_id) and str(client_order_id).startswith(self.prefix)

    def _is_tombstoned(self, order_id: str) -> bool:
        now = self._clock()
        self._tombstones = {k: t for k, t in self._tombstones.items() if now - t < _TOMBSTONE_TTL}
        return order_id in self._tombstones

    def order_age(self, order_id: str) -> float:
        """Seconds since we first knew of this order."""
        return self._clock() - self.first_seen.get(order_id, self._clock())

    def our_orders(self, ticker: str | None = None) -> list[Order]:
        return [o for o in self.orders.values() if ticker is None or o.ticker == ticker]

    def upsert_order(self, order: Order) -> None:
        if not self.is_ours(order.client_order_id) or self._is_tombstoned(order.order_id):
            return
        if order.remaining <= 0 or order.status in ("canceled", "executed"):
            self.remove_order(order.order_id, order.ticker)
            return
        self.orders[order.order_id] = order
        self.first_seen.setdefault(order.order_id, self._clock())
        self._touch(order.ticker)

    def remove_order(self, order_id: str, ticker: str | None = None) -> None:
        self._tombstones[order_id] = self._clock()
        self.queue_ahead.pop(order_id, None)
        self.first_seen.pop(order_id, None)
        gone = self.orders.pop(order_id, None)
        target = ticker or (gone.ticker if gone else None)
        if target:
            self._touch(target)

    def now(self) -> float:
        """This state's clock: pass it as ``as_of`` when starting a REST snapshot."""
        return self._clock()

    def replace_orders(self, orders: Iterable[Order], as_of: float | None = None) -> None:
        """Adopt a REST snapshot of resting orders, ignoring any we know are gone.

        ``as_of`` is when the snapshot request started (:meth:`now`). Orders we
        placed after that can't be in it, so they are kept: dropping them would
        make the bot place them a second time, doubling its orders until the
        next reconcile.
        """
        fresh = {
            o.order_id: o
            for o in orders
            if self.is_ours(o.client_order_id) and not self._is_tombstoned(o.order_id)
        }
        if as_of is not None:
            for oid, order in self.orders.items():
                if oid not in fresh and self.first_seen.get(oid, as_of) > as_of:
                    fresh[oid] = order  # placed after the snapshot was taken
        now = self._clock()
        for ticker in {o.ticker for o in (*self.orders.values(), *fresh.values())}:
            self._touch(ticker)
        self.first_seen = {oid: self.first_seen.get(oid, now) for oid in fresh}
        self.orders = fresh

    # -------------------------------------------------------------- positions

    # Two views of position: ``positions`` holds full records (count, cost,
    # P&L) that stay internally consistent, for risk. ``_live_position`` is the
    # contract count updated instantly by fills, for quoting. A fill arrives
    # before its cost update, and pairing a new count with an old cost would
    # show a phantom P&L swing.

    def set_position(self, position: Position) -> None:
        self.positions[position.ticker] = position
        self._live_position[position.ticker] = position.position
        self._touch(position.ticker)

    def set_live_position(self, ticker: str, contracts: Decimal) -> None:
        self._live_position[ticker] = contracts
        self._touch(ticker)

    def replace_positions(self, positions: Mapping[str, Position]) -> None:
        for ticker in set(self.positions) | set(positions):
            self._touch(ticker)
        self.positions = dict(positions)
        self._live_position = {t: p.position for t, p in positions.items()}

    def position(self, ticker: str) -> Decimal:
        """Latest contract count (positive = long YES), including fills just received."""
        return self._live_position.get(ticker, ZERO)
