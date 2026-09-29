"""Wires the Kalshi WebSocket channels into :class:`MarketState`.

Subscriptions:

* ``orderbook_delta`` for the quoted markets: a snapshot, then deltas.
* ``user_orders``: every state change of our orders (placed, filled, cancelled).
* ``fill``: our fills, with the position after each one.
* ``market_positions``: position, cost basis, realized P&L, and fees.

On disconnect or a sequence gap, the affected books are invalidated, which
makes the bot pull quotes there, and we resubscribe to get fresh snapshots.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Iterable
from decimal import Decimal

from kalshi_lp.core.types import to_decimal
from kalshi_lp.exchange.auth import Signer
from kalshi_lp.exchange.models import Order, Position
from kalshi_lp.exchange.ws import KalshiStream, Message, StreamError
from kalshi_lp.feed.state import MarketState

log = logging.getLogger(__name__)

BOOK_CHANNEL = "orderbook_delta"
USER_CHANNELS = ["user_orders", "fill", "market_positions"]
CONNECT_TIMEOUT_SECONDS = 15.0


class StreamingFeed:
    def __init__(self, url: str, signer: Signer | None, state: MarketState):
        self.state = state
        self.tickers: set[str] = set()
        self._book_sid: int | None = None
        self._lock = asyncio.Lock()  # serialises subscription changes
        self._task: asyncio.Task[None] | None = None
        self.stream = KalshiStream(
            url,
            signer,
            self._on_message,
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
            on_gap=self._on_gap,
        )

    # ------------------------------------------------------------- lifecycle

    async def start(self, tickers: Iterable[str]) -> None:
        self.tickers = set(tickers)
        self._task = asyncio.create_task(self.stream.run(), name="kalshi-ws")
        await asyncio.wait_for(self.stream.connected.wait(), CONNECT_TIMEOUT_SECONDS)

    async def stop(self) -> None:
        await self.stream.close()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    @property
    def connected(self) -> bool:
        return self.stream.connected.is_set()

    # --------------------------------------------------------- subscriptions

    async def _on_connect(self) -> None:
        async with self._lock:
            self._book_sid = None
            await self.stream.subscribe(USER_CHANNELS)
            if self.tickers:
                self._book_sid = await self.stream.subscribe([BOOK_CHANNEL], sorted(self.tickers))
        log.info("subscribed: %d order books + account channels", len(self.tickers))

    async def _on_disconnect(self) -> None:
        self._book_sid = None
        self.state.invalidate()

    async def _on_gap(self, sid: int) -> None:
        if sid != self._book_sid:
            return  # account channels: the periodic REST reconcile covers any miss
        self.state.invalidate(self.tickers)
        async with self._lock:
            try:
                await self.stream.unsubscribe([sid])
            except StreamError as exc:
                log.warning("unsubscribe after gap failed: %s", exc)
            self._book_sid = await self.stream.subscribe([BOOK_CHANNEL], sorted(self.tickers))

    async def set_tickers(self, tickers: Iterable[str]) -> None:
        """Change which order books we follow."""
        new = set(tickers)
        added, removed = new - self.tickers, self.tickers - new
        self.tickers = new
        self.state.forget(removed)
        if not self.connected or not (added or removed):
            return
        async with self._lock:
            try:
                if self._book_sid is None:
                    if new:
                        self._book_sid = await self.stream.subscribe([BOOK_CHANNEL], sorted(new))
                    return
                if added:
                    await self.stream.update_markets(self._book_sid, sorted(added), add=True)
                if removed:
                    await self.stream.update_markets(self._book_sid, sorted(removed), add=False)
            except StreamError as exc:
                log.warning("subscription update failed (%s); resubscribing", exc)
                if self._book_sid is not None:
                    with contextlib.suppress(StreamError):
                        await self.stream.unsubscribe([self._book_sid])
                self.state.invalidate(new)
                self._book_sid = await self.stream.subscribe([BOOK_CHANNEL], sorted(new))

    # -------------------------------------------------------------- messages

    def _on_message(self, msg: Message) -> None:
        kind, body = msg.get("type"), msg.get("msg") or {}
        match kind:
            case "orderbook_snapshot":
                ticker = body["market_ticker"]
                if ticker in self.tickers:
                    self.state.set_snapshot(
                        ticker, body.get("yes_dollars_fp"), body.get("no_dollars_fp")
                    )
            case "orderbook_delta":
                self.state.apply_delta(
                    body["market_ticker"],
                    body["side"],
                    to_decimal(body["price_dollars"]),
                    to_decimal(body["delta_fp"]),
                )
            case "user_order":
                self._on_order(body)
            case "fill":
                self._on_fill(body)
            case "market_position":
                self.state.set_position(
                    Position(
                        ticker=body["market_ticker"],
                        position=to_decimal(body.get("position_fp")),
                        market_exposure=to_decimal(body.get("position_cost_dollars")),
                        realized_pnl=to_decimal(body.get("realized_pnl_dollars")),
                        fees_paid=to_decimal(body.get("fees_paid_dollars")),
                    )
                )
            case "subscribed" | "ok" | "unsubscribed":
                pass
            case _:
                log.debug("unhandled websocket message type %s", kind)

    def _on_order(self, body: Message) -> None:
        if not self.state.is_ours(body.get("client_order_id")):
            return
        order = Order.from_api(body)
        if order.status == "resting" and order.remaining > 0:
            self.state.upsert_order(order)
        else:
            self.state.remove_order(order.order_id, order.ticker)

    def _on_fill(self, body: Message) -> None:
        ticker = body["market_ticker"]
        count = to_decimal(body.get("count_fp"))
        log.info(
            "FILL %s %s %s @ %s (%s)",
            ticker,
            body.get("book_side", "?"),
            count.normalize(),
            body.get("yes_price_dollars"),
            "taker" if body.get("is_taker") else "maker",
        )
        post = body.get("post_position_fp")
        if post is not None:
            self.state.set_live_position(ticker, Decimal(post))
