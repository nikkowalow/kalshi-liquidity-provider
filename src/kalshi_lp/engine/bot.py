"""The bot: event-driven quoting off the WebSocket feed.

Three concurrent loops share one :class:`MarketState`:

* **Quoting** wakes whenever a book, one of our orders, or a position changes
  (debounced by ``requote_min_interval_seconds``), and at least every
  ``heartbeat_seconds``. It requotes only the markets that changed: strip our
  own orders from the live book, compute quotes, diff against what's resting,
  and send the minimal set of cancels/decreases/creates over REST.
* **Maintenance** runs the slow periodic work: exchange status, a REST
  cross-check of orders, positions and balance (to correct any drift in the
  streamed state), queue positions, market reselection, and reward reports.
* **Reward sampling** replays the incentive program's once-per-second
  snapshot scoring on the live book (see :mod:`engine.reward_tracker`).

Safety: the bot never quotes from a book it can't trust. If the WebSocket
drops or skips a sequence number, the affected books are invalidated and our
quotes there are pulled until a fresh snapshot arrives. Trading actions are
serialized by a lock so REST reconciliation and quoting never interleave.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Awaitable, Callable, Iterable
from decimal import Decimal
from typing import Protocol

import httpx

from kalshi_lp.config import Settings
from kalshi_lp.core.types import ZERO, Leg, Quote
from kalshi_lp.engine.executor import ExecutionReport, OrderExecutor
from kalshi_lp.engine.reconciler import Plan, reconcile
from kalshi_lp.engine.reward_tracker import RewardTracker, own_orders_by_leg, strip_own
from kalshi_lp.engine.risk import RiskManager, RiskView
from kalshi_lp.exchange.auth import Signer
from kalshi_lp.exchange.client import KalshiClient
from kalshi_lp.exchange.errors import KalshiError
from kalshi_lp.exchange.models import Market, Order
from kalshi_lp.feed.state import MarketState
from kalshi_lp.feed.stream import StreamingFeed
from kalshi_lp.strategy.quoting import LegDecision, MarketContext, QuoteEngine
from kalshi_lp.strategy.rewards import RewardParams, expected_daily_reward
from kalshi_lp.strategy.selection import MarketSelector

log = logging.getLogger(__name__)

TRANSIENT_ERRORS = (KalshiError, httpx.HTTPError)


class Feed(Protocol):
    state: MarketState

    @property
    def connected(self) -> bool: ...

    async def start(self, tickers: Iterable[str]) -> None: ...
    async def stop(self) -> None: ...
    async def set_tickers(self, tickers: Iterable[str]) -> None: ...


class LiquidityBot:
    def __init__(
        self,
        settings: Settings,
        client: KalshiClient,
        *,
        signer: Signer | None = None,
        feed: Feed | None = None,
    ):
        self.settings = settings
        self.client = client
        if feed is None:
            feed = StreamingFeed(settings.ws_url, signer, MarketState(settings.client_order_prefix))
        self.feed = feed
        self.state = feed.state
        self.engine = QuoteEngine(settings.quoting, settings.risk.max_position_per_market)
        self.selector = MarketSelector(client, settings.selection, settings.quoting, self.engine)
        self.risk = RiskManager(settings.risk)
        self.tracker = RewardTracker()
        self.executor = OrderExecutor(
            client,
            prefix=settings.client_order_prefix,
            dry_run=settings.dry_run,
            post_only=settings.quoting.post_only,
            max_batch=int(settings.rate_limits.write_per_sec // 10),
        )
        self.markets: dict[str, Market] = {}
        self.rewards: dict[str, RewardParams] = {}
        self.reduce_only: set[str] = set()
        self.trading_active = True
        self.requotes = 0
        self._desired: dict[str, list[Quote]] = {}  # latest quotes per market (for dry-run)
        self._dry_logged: dict[str, tuple[str, ...]] = {}
        self._group_needs_reset = False
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()

    def stop(self) -> None:
        log.info("stop requested")
        self._stop.set()
        self.state.changed.set()  # wake the quoting loop

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    async def _sleep(self, seconds: float) -> None:
        """Sleep, waking early on stop()."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=max(seconds, 0.0))

    # --------------------------------------------------------------- lifecycle

    async def run(self, max_requotes: int | None = None) -> None:
        tasks: list[asyncio.Task[None]] = []
        try:
            await self.startup()
            if self.stopping:
                return
            tasks = [
                asyncio.create_task(self._maintenance_loop(), name="maintenance"),
                asyncio.create_task(self._reward_loop(), name="rewards"),
            ]
            await self._quote_loop(max_requotes)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.shutdown()

    async def startup(self) -> None:
        s = self.settings
        mode = "DRY-RUN" if s.dry_run else "LIVE"
        log.info("starting in %s environment (%s) against %s", s.environment.value, mode, s.api_url)
        await self._wait_for_exchange()
        if self.stopping:
            return
        await self.sync_from_rest()
        log.info("exchange is up; balance=$%.2f", self.state.balance or ZERO)

        orphans = self.state.our_orders()
        if orphans:
            log.warning("found %d resting orders from a previous run; cancelling", len(orphans))
            self._apply(await self.executor.execute(Plan(cancels=orphans)))

        if not s.dry_run and s.risk.order_group_contracts_limit > 0:
            group = await self.client.create_order_group(s.risk.order_group_contracts_limit)
            self.executor.order_group_id = group
            log.info(
                "order group %s: exchange cancels all bot orders if >%d contracts fill in 15s",
                group,
                s.risk.order_group_contracts_limit,
            )

        await self.reselect()
        await self.feed.start(self.markets)

    async def _wait_for_exchange(self) -> None:
        """Block until the exchange accepts trading (e.g. through maintenance), or stop()."""
        interval = self.settings.loop.status_check_interval_seconds
        while not self.stopping:
            status = await self.client.get_exchange_status()
            if status.exchange_active and status.trading_active:
                return
            log.warning(
                "exchange not open (exchange_active=%s trading_active=%s); rechecking in %.0fs",
                status.exchange_active,
                status.trading_active,
                interval,
            )
            await self._sleep(interval)

    async def shutdown(self) -> None:
        log.info("shutting down: cancelling bot orders")
        with contextlib.suppress(Exception):
            await self.feed.stop()
        try:
            await self.cancel_all_ours()
        except TRANSIENT_ERRORS as exc:
            log.error(
                "could not cancel bot orders cleanly (%s); cancelling ALL account orders", exc
            )
            if not self.settings.dry_run:
                try:
                    await self.client.cancel_all_orders()
                except TRANSIENT_ERRORS as exc2:
                    log.critical(
                        "COULD NOT CANCEL ORDERS (%s). Check open orders and run `klp cancel` "
                        "once the exchange is reachable.",
                        exc2,
                    )
        group = self.executor.order_group_id
        if group:
            try:
                await self.client.delete_order_group(group)
            except TRANSIENT_ERRORS as exc:
                log.warning("could not delete order group %s: %s", group, exc)
        for line in self.tracker.report_lines():
            log.info(line)

    async def cancel_all_ours(self) -> None:
        orders = [o for o in await self.client.get_resting_orders() if self.executor.is_ours(o)]
        if orders:
            self._apply(await self.executor.execute(Plan(cancels=orders)))
        log.info("cancelled %d bot orders", len(orders))

    async def sync_from_rest(self) -> None:
        """Replace streamed state with a REST snapshot: the source of truth for drift."""
        orders = await self.client.get_resting_orders()
        positions = await self.client.get_positions()
        balance = await self.client.get_balance()
        self.state.replace_orders(o for o in orders if self.executor.is_ours(o))
        self.state.replace_positions(positions)
        self.state.balance = balance.balance

    # -------------------------------------------------------------- selection

    async def reselect(self) -> None:
        candidates = await self.selector.select()
        chosen = {c.ticker: c for c in candidates}

        # Keep deselected markets where we still hold a position, quoting only
        # the side that works the position down.
        leftovers = [
            t
            for t, p in self.state.positions.items()
            if p.position != 0 and t in self.markets and t not in chosen
        ]
        refreshed = await self.client.get_markets(tickers=leftovers) if leftovers else []

        self.markets = {c.ticker: c.market for c in candidates}
        self.rewards = {c.ticker: c.reward for c in candidates}
        self.reduce_only = set()
        for market in refreshed:
            if market.is_tradable:
                self.markets[market.ticker] = market
                self.rewards[market.ticker] = self.selector.default_reward()
                self.reduce_only.add(market.ticker)

        log.info("selected %d markets:", len(candidates))
        for c in candidates:
            log.info(
                "  %-44s est $%.2f/day  (program $%.2f/day, target %s, df %s)",
                c.ticker,
                c.est_daily_reward,
                c.reward.reward_per_day,
                f"{c.reward.target_size.normalize():f}",
                c.reward.discount_factor,
            )
        if self.reduce_only:
            log.info("reduce-only (holding inventory): %s", ", ".join(sorted(self.reduce_only)))

    # ----------------------------------------------------------------- quoting

    async def _quote_loop(self, max_requotes: int | None) -> None:
        cfg = self.settings.loop
        last_requote = last_full = float("-inf")
        while not self.stopping:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.state.changed.wait(), timeout=cfg.heartbeat_seconds)
            if self.stopping:
                break
            since = time.monotonic() - last_requote
            if since < cfg.requote_min_interval_seconds:
                await self._sleep(cfg.requote_min_interval_seconds - since)

            tickers = self.state.take_dirty()
            now = time.monotonic()
            if now - last_full >= cfg.heartbeat_seconds:
                tickers |= set(self.markets)
                last_full = now
            tickers |= {o.ticker for o in self.state.our_orders()} - set(self.markets)
            if not tickers:
                continue

            last_requote = time.monotonic()
            try:
                async with self._lock:
                    await self.requote(tickers)
            except TRANSIENT_ERRORS as exc:
                log.error("requote failed: %s", exc)
                self.risk.record_cycle(False)
            self.requotes += 1
            if max_requotes is not None and self.requotes >= max_requotes:
                break

    def _risk_view(self) -> RiskView:
        marks: dict[str, Decimal | None] = {}
        for ticker in self.markets:
            book = self.state.book(ticker)
            marks[ticker] = book.mid if book else None
        return self.risk.update(
            set(self.markets),
            self.state.positions,
            marks,
            self.state.balance if self.state.balance is not None else ZERO,
            live_positions={t: self.state.position(t) for t in self.markets},
        )

    async def requote(self, tickers: Iterable[str]) -> None:
        """Recompute and send quotes for ``tickers``. Caller holds ``self._lock``."""
        tickers = sorted(tickers)
        if not self.trading_active:
            await self._pull(self.state.our_orders())
            return
        view = self._risk_view()
        if view.halted:
            await self._pull(self.state.our_orders())
            self.stop()
            return
        if self._group_needs_reset and not view.globally_paused and self.executor.order_group_id:
            await self.client.reset_order_group(self.executor.order_group_id)
            self._group_needs_reset = False
            log.info("order group reset; resuming")

        plan = Plan()
        for ticker in tickers:
            own = self.state.our_orders(ticker)
            decisions = self._decide(ticker, view)
            if decisions is None:
                plan.cancels += own
                self._desired.pop(ticker, None)
                continue
            quotes = [d.quote for d in decisions.values() if d.quote]
            if self.settings.dry_run:
                self._log_dry_run(ticker, decisions, quotes)
            else:
                plan.extend(reconcile(quotes, own))

        if not plan:
            return
        report = await self.executor.execute(plan)
        self._apply(report)
        self.risk.record_cycle(report.errors == 0)
        if report.group_blocked:
            self.risk.pause_all("exchange order group tripped (fill limit hit)")
            self._group_needs_reset = True
        log.info(
            "requote %s: pnl=%+.2f exposure=$%.2f | cancel=%d decrease=%d place=%d "
            "rejected=%d errors=%d",
            ",".join(tickers) if len(tickers) <= 3 else f"{len(tickers)} markets",
            view.session_pnl,
            view.total_exposure,
            report.cancelled,
            report.decreased,
            report.created,
            report.rejected,
            report.errors,
        )

    def _decide(self, ticker: str, view: RiskView) -> dict[Leg, LegDecision] | None:
        """Quotes for one market, or None if the market must not be quoted right now."""
        market = self.markets.get(ticker)
        book = self.state.book(ticker)
        if (
            market is None
            or book is None
            or not self.feed.connected
            or view.globally_paused
            or self.risk.market_paused(ticker)
            or self.risk.near_close(market)
        ):
            return None
        own = own_orders_by_leg(self.state.our_orders(ticker), self.state.queue_ahead)
        resting = {leg: max(orders, key=lambda o: o.size) for leg, orders in own.items() if orders}
        ctx = MarketContext(
            market=market,
            book=strip_own(book, own),
            position=self.state.position(ticker),
            reward=self.rewards.get(ticker, self.selector.default_reward()),
            allow_increase=view.allow_increase and ticker not in self.reduce_only,
            resting=resting,
        )
        decisions = self.engine.quote(ctx)
        self._desired[ticker] = [d.quote for d in decisions.values() if d.quote]
        return decisions

    def _log_dry_run(
        self, ticker: str, decisions: dict[Leg, LegDecision], quotes: list[Quote]
    ) -> None:
        signature = tuple(str(q) for q in quotes)
        if self._dry_logged.get(ticker) == signature:
            return  # only log when the quote changes
        self._dry_logged[ticker] = signature
        yes, no = decisions[Leg.YES], decisions[Leg.NO]
        est = (
            expected_daily_reward(yes.score, no.score, self.rewards[ticker])
            if yes.score and no.score and ticker in self.rewards
            else ZERO
        )
        log.info(
            "[dry-run] %s pos=%s | YES %s (%s) | NO %s (%s) | est $%.2f/day",
            ticker,
            self.state.position(ticker).normalize(),
            yes.quote or "-",
            yes.reason,
            no.quote or "-",
            no.reason,
            est,
        )

    async def _pull(self, orders: list[Order]) -> None:
        if orders:
            self._apply(await self.executor.execute(Plan(cancels=orders)))

    def _apply(self, report: ExecutionReport) -> None:
        """Reflect REST results in live state right away (the stream confirms later)."""
        for order in report.gone:
            self.state.remove_order(order.order_id, order.ticker)
        for order in (*report.placed, *report.resized):
            self.state.upsert_order(order)

    # ------------------------------------------------------------- maintenance

    async def _maintenance_loop(self) -> None:
        cfg = self.settings.loop
        jobs: list[tuple[float, Callable[[], Awaitable[None]]]] = [
            (cfg.status_check_interval_seconds, self._check_status),
            (cfg.reconcile_interval_seconds, self._reconcile),
            (cfg.queue_refresh_seconds, self._refresh_queue),
            (cfg.reselect_interval_seconds, self._reselect_job),
            (cfg.reward_report_seconds, self._report_rewards),
        ]
        start = time.monotonic()
        last = {job: start for _, job in jobs}
        while not self.stopping:
            await self._sleep(1.0)
            now = time.monotonic()
            for interval, job in jobs:
                if now - last[job] >= interval:
                    last[job] = now
                    try:
                        await job()
                    except TRANSIENT_ERRORS as exc:
                        log.warning("%s failed: %s", job.__name__.strip("_"), exc)

    async def _check_status(self) -> None:
        status = await self.client.get_exchange_status()
        active = status.exchange_active and status.trading_active
        if active != self.trading_active:
            log.warning("exchange trading active: %s", active)
            self.trading_active = active
            if not active:
                async with self._lock:
                    await self._pull(self.state.our_orders())
            self.state.changed.set()

    async def _reconcile(self) -> None:
        async with self._lock:
            await self.sync_from_rest()

    async def _refresh_queue(self) -> None:
        tickers = sorted({o.ticker for o in self.state.our_orders()})
        if tickers:
            self.state.queue_ahead = await self.client.get_queue_positions(tickers)

    async def _reselect_job(self) -> None:
        async with self._lock:
            await self.reselect()
        await self.feed.set_tickers(self.markets)
        self.state.changed.set()

    async def _report_rewards(self) -> None:
        if self.tracker.stats:
            for line in self.tracker.report_lines():
                log.info(line)

    # ----------------------------------------------------------------- rewards

    async def _reward_loop(self) -> None:
        """Score one snapshot per second, at a random moment within the second, like Kalshi."""
        while not self.stopping:
            await self._sleep(1.0 - time.time() % 1.0 + random.random())
            self.sample_rewards()

    def sample_rewards(self) -> None:
        for ticker, market in self.markets.items():
            params = self.rewards.get(ticker)
            book = self.state.book(ticker)
            if params is None or book is None or params.reward_per_day <= 0:
                continue
            if self.settings.dry_run:
                # Score the quotes we *would* have resting, at the back of their queues.
                hypothetical = [
                    Order(f"dry-{i}", "", ticker, q.side, q.price, q.size, "resting")
                    for i, q in enumerate(self._desired.get(ticker, []))
                ]
                self.tracker.sample(
                    ticker, book, hypothetical, {}, params, market.grid, in_book=False
                )
            else:
                self.tracker.sample(
                    ticker,
                    book,
                    self.state.our_orders(ticker),
                    self.state.queue_ahead,
                    params,
                    market.grid,
                )
