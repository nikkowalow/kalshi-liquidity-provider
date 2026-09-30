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
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from decimal import Decimal
from typing import Any, Protocol

import httpx

from kalshi_lp.config import Settings
from kalshi_lp.core.orderbook import Orderbook
from kalshi_lp.core.types import ZERO, Leg, Quote
from kalshi_lp.engine import budget
from kalshi_lp.engine.executor import ExecutionReport, OrderExecutor
from kalshi_lp.engine.queue import queue_report
from kalshi_lp.engine.reconciler import Plan, reconcile
from kalshi_lp.engine.reward_tracker import RewardTracker, own_orders_by_leg, strip_own
from kalshi_lp.engine.risk import Mark, RiskManager, RiskView
from kalshi_lp.exchange.auth import Signer
from kalshi_lp.exchange.client import KalshiClient
from kalshi_lp.exchange.errors import KalshiError
from kalshi_lp.exchange.models import Market, Order
from kalshi_lp.feed.state import MarketState
from kalshi_lp.feed.stream import StreamingFeed
from kalshi_lp.journal import JournalLogHandler, NullJournal, RunJournal
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
        journal: RunJournal | None = None,
    ):
        self.settings = settings
        self.journal = journal or NullJournal()
        self.client = client
        if feed is None:
            feed = StreamingFeed(settings.ws_url, signer, MarketState(settings.client_order_prefix))
        self.feed = feed
        self.state = feed.state
        self.state.listeners.append(self.journal.listener)
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
        self._quote_sig: dict[str, tuple[str, ...]] = {}
        self._decisions: dict[str, dict[Leg, LegDecision]] = {}
        self._last_view: RiskView | None = None
        self._mids: dict[str, deque[tuple[float, Decimal]]] = {}
        self.started_at = time.time()
        # Carry the history of earlier runs in this journal (rewards, markets, counters).
        prev = self.journal.previous
        self.tracker.restore(prev.get("ledger") or {})
        self.titles: dict[str, str] = {}
        self.fills_total = int(prev.get("fills_total") or 0)
        self._requotes_before = int(prev.get("requotes_total") or 0)
        self.first_started_at = float(prev.get("first_started_at") or self.started_at)
        self.state.listeners.append(self._count_fills)
        self._config_json = settings.model_dump(mode="json")
        self._group_needs_reset = False
        self._budget_binding = False
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()

    def _count_fills(self, kind: str, data: dict[str, Any]) -> None:
        if kind == "fill":
            self.fills_total += 1

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
        handler = JournalLogHandler(self.journal)
        logging.getLogger("kalshi_lp").addHandler(handler)
        s = self.settings
        self.journal.event(
            "run_start",
            run_id=self.journal.run_id,
            session=self.journal.session,
            environment=s.environment.value,
            mode="dry-run" if s.dry_run else "live",
            api_url=s.api_url,
            config=s.model_dump(mode="json"),
        )
        try:
            await self.startup()
            if self.stopping:
                return
            tasks = [
                asyncio.create_task(self._maintenance_loop(), name="maintenance"),
                asyncio.create_task(self._reward_loop(), name="rewards"),
                asyncio.create_task(self._journal_loop(), name="journal"),
            ]
            await self._quote_loop(max_requotes)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.shutdown()
            self.journal.event(
                "run_end", halted=self.risk.halted, reason=self.risk.halt_reason or "stopped"
            )
            self.journal.write_state(
                self.snapshot(status="halted" if self.risk.halted else "ended")
            )
            logging.getLogger("kalshi_lp").removeHandler(handler)
            self.journal.close()

    async def startup(self) -> None:
        s = self.settings
        mode = "DRY-RUN" if s.dry_run else "LIVE"
        log.info("starting in %s environment (%s) against %s", s.environment.value, mode, s.api_url)
        await self._wait_for_exchange()
        if self.stopping:
            return
        await self.sync_from_rest()
        log.info("exchange is up; balance=$%.2f", self.state.balance or ZERO)
        if s.risk.max_capital is not None:
            log.info("capital budget: $%.2f (positions + resting orders)", s.risk.max_capital)
            # A bid plus an ask of N contracts locks about N dollars.
            wanted = s.selection.max_markets * s.quoting.size
            if wanted > s.risk.max_capital:
                log.warning(
                    "budget spread thin: %d markets x %s contracts wants ~$%s but max_capital is "
                    "$%s; some quotes will be shrunk or skipped. Lower selection.max_markets or "
                    "quoting.size.",
                    s.selection.max_markets,
                    s.quoting.size,
                    wanted,
                    s.risk.max_capital,
                )

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

        self.journal.event(
            "markets",
            markets=[
                {
                    "ticker": c.ticker,
                    "title": c.market.title,
                    "reward_per_day": c.reward.reward_per_day,
                    "target_size": c.reward.target_size,
                    "discount_factor": c.reward.discount_factor,
                    "est_daily_reward": c.est_daily_reward,
                    "close_time": c.market.close_time,
                }
                for c in candidates
            ],
            reduce_only=sorted(self.reduce_only),
        )
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
        marks: dict[str, Mark | None] = {}
        for ticker in self.markets:
            book = self.state.book(ticker)
            marks[ticker] = (book.best_yes_bid, book.best_yes_ask) if book else None
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
        self._last_view = view
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
                self._decisions.pop(ticker, None)  # dashboard: no stale quotes on paused markets
                continue
            quotes = [d.quote for d in decisions.values() if d.quote]
            self._note_quotes(ticker, decisions, quotes)
            if not self.settings.dry_run:
                plan.extend(reconcile(quotes, own))

        if plan.creates and self.settings.risk.max_capital is not None:
            self._fit_budget(plan, self.settings.risk.max_capital)
        if not plan:
            return
        report = await self.executor.execute(plan)
        self._apply(report)
        self.risk.record_cycle(report.errors == 0)
        if report.group_blocked:
            self.risk.pause_all("exchange order group tripped (fill limit hit)")
            self._group_needs_reset = True
        log.info(
            "requote %s: pnl=%+.2f capital=$%.2f | cancel=%d decrease=%d place=%d "
            "rejected=%d errors=%d",
            ",".join(tickers) if len(tickers) <= 3 else f"{len(tickers)} markets",
            view.session_pnl,
            self.capital_in_use(),
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
        if self._moved_too_fast(ticker, book.mid):
            return None
        orders = self.state.our_orders(ticker)
        own = own_orders_by_leg(orders, self.state.queue_ahead)
        resting = {leg: max(os, key=lambda o: o.size) for leg, os in own.items() if os}
        ages = {}
        for leg in resting:
            leg_orders = [o for o in orders if o.leg is leg]
            biggest = max(leg_orders, key=lambda o: o.remaining)
            ages[leg] = self.state.order_age(biggest.order_id)
        ctx = MarketContext(
            market=market,
            book=strip_own(book, own),
            position=self.state.position(ticker),
            reward=self.rewards.get(ticker, self.selector.default_reward()),
            allow_increase=view.allow_increase and ticker not in self.reduce_only,
            resting=resting,
            resting_age=ages,
        )
        decisions = self.engine.quote(ctx)
        self._desired[ticker] = [d.quote for d in decisions.values() if d.quote]
        self._decisions[ticker] = decisions
        return decisions

    def _moved_too_fast(self, ticker: str, mid: Decimal | None) -> bool:
        """Move guard: pause a market whose mid moved ``max_mid_move`` within the window."""
        limit = self.settings.risk.max_mid_move
        if limit is None or mid is None:
            return False
        now = time.monotonic()
        window = self.settings.risk.mid_move_window_seconds
        history = self._mids.setdefault(ticker, deque())
        history.append((now, mid))
        while history and now - history[0][0] > window:
            history.popleft()
        mids = [m for _, m in history]
        if max(mids) - min(mids) >= limit:
            self.risk.pause_market(
                ticker, f"mid moved {max(mids) - min(mids):.2f} within {window:.0f}s"
            )
            history.clear()
            return True
        return False

    def _note_quotes(
        self, ticker: str, decisions: dict[Leg, LegDecision], quotes: list[Quote]
    ) -> None:
        """Journal (and in dry-run, log) the desired quotes whenever they change."""
        signature = tuple(str(q) for q in quotes)
        if self._quote_sig.get(ticker) == signature:
            return
        self._quote_sig[ticker] = signature
        yes, no = decisions[Leg.YES], decisions[Leg.NO]
        est = (
            expected_daily_reward(yes.score, no.score, self.rewards[ticker])
            if yes.score and no.score and ticker in self.rewards
            else ZERO
        )
        self.journal.event(
            "quote",
            ticker=ticker,
            position=self.state.position(ticker),
            yes=_leg_json(yes),
            no=_leg_json(no),
            est_daily_reward=est,
            dry_run=self.settings.dry_run,
        )
        if not self.settings.dry_run:
            return
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

    def capital_in_use(self, excluding: Iterable[Order] = ()) -> Decimal:
        """Position cost plus cash locked by resting orders, across the bot's markets."""
        skip = {o.order_id for o in excluding}
        orders = [o for o in self.state.our_orders() if o.order_id not in skip]
        tickers = set(self.markets) | {o.ticker for o in orders}
        live = {t: self.state.position(t) for t in tickers}
        return budget.capital_in_use(tickers, self.state.positions, live, orders)

    def _fit_budget(self, plan: Plan, max_capital: Decimal) -> None:
        """Shrink or drop new orders so capital in use stays under ``max_capital``."""
        shrunk = [
            Order(o.order_id, o.client_order_id, o.ticker, o.side, o.yes_price, size, o.status)
            for o, size in plan.decreases
        ]
        in_use = self.capital_in_use(excluding=[*plan.cancels, *(o for o, _ in plan.decreases)])
        in_use += sum(
            (budget.unit_collateral(o.side, o.yes_price) * o.remaining for o in shrunk), ZERO
        )
        live = {q.ticker: self.state.position(q.ticker) for q in plan.creates}
        wanted = plan.creates
        plan.creates, _ = budget.fit_to_budget(wanted, max_capital - in_use, live)
        binding = plan.creates != wanted
        if binding and not self._budget_binding:
            log.warning(
                "capital budget reached: $%.2f of $%.2f in use; new orders shrunk or skipped",
                in_use,
                max_capital,
            )
        self._budget_binding = binding

    async def _pull(self, orders: list[Order]) -> None:
        if orders:
            self._apply(await self.executor.execute(Plan(cancels=orders)))

    def _apply(self, report: ExecutionReport) -> None:
        """Reflect REST results in live state right away (the stream confirms later)."""
        for order in report.gone:
            self.state.remove_order(order.order_id, order.ticker)
            self.journal.event("order", action="cancel", **_order_json(order))
        for order in report.placed:
            self.state.upsert_order(order)
            self.journal.event("order", action="place", **_order_json(order))
        for order in report.resized:
            self.state.upsert_order(order)
            self.journal.event("order", action="decrease", **_order_json(order))
        for quote, error in report.rejections:
            self.journal.event(
                "order",
                action="reject",
                ticker=quote.ticker,
                side=quote.side.value,
                price=quote.price,
                size=quote.size,
                error=error,
            )

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

    # ----------------------------------------------------------------- journal

    async def _journal_loop(self) -> None:
        """Rewrite the state snapshot every second; journal metrics every 10 seconds."""
        ticks = 0
        while not self.stopping:
            await self._sleep(1.0)
            snap = self.snapshot()
            self.journal.write_state(snap)
            if ticks % 10 == 0:
                self.journal.event("metrics", **snap["totals"])
            ticks += 1

    def snapshot(self, status: str | None = None) -> dict[str, Any]:
        """Everything the dashboard shows, as plain JSON-able data."""
        now = time.monotonic()
        view = self._last_view
        rewards_total = self.tracker.total_earned
        rate = self.tracker.total_hourly_rate()
        markets = []
        self.titles.update((t, m.title) for t, m in self.markets.items())
        live = list(self.markets) + sorted(
            {o.ticker for o in self.state.our_orders()} - set(self.markets)
        )
        # Markets from earlier in this run or earlier runs, most earned first.
        past = sorted(
            set(self.tracker.stats) - set(live),
            key=lambda t: -self.tracker.stats[t].earned,
        )
        for ticker in live + past:
            market = self.markets.get(ticker)
            book = self.state.book(ticker)
            pos = self.state.positions.get(ticker)
            params = self.rewards.get(ticker)
            stats = self.tracker.stats.get(ticker)
            decisions = self._decisions.get(ticker, {})
            markets.append(
                {
                    "ticker": ticker,
                    "title": market.title
                    if market
                    else self.titles.get(ticker) or (stats.title if stats else ""),
                    "inactive": ticker in past,
                    "close_time": market.close_time if market else None,
                    "reduce_only": ticker in self.reduce_only,
                    "paused": self.risk.market_paused(ticker),
                    "near_close": bool(market and self.risk.near_close(market)),
                    "healthy": self.state.is_healthy(ticker),
                    "book": _book_json(book),
                    "position": self.state.position(ticker),
                    "exposure": pos.market_exposure if pos else 0,
                    "realized_pnl": pos.realized_pnl if pos else 0,
                    "fees": pos.fees_paid if pos else 0,
                    "quotes": {leg.value: _leg_json(d) for leg, d in decisions.items()},
                    "reward": {
                        "per_day": params.reward_per_day if params else 0,
                        "target_size": params.target_size if params else None,
                        "discount_factor": params.discount_factor if params else None,
                    },
                    "earned": stats.earned if stats else 0,
                    "rate_per_hour": stats.hourly_rate(now) if stats else 0,
                    "avg_score": stats.avg_score if stats else 0,
                    "snapshots": stats.snapshots if stats else 0,
                    "paying_snapshots": stats.scored if stats else 0,
                    "last_earned_at": stats.last_earned_at if stats else None,
                }
            )
        s = self.settings
        return {
            "run_id": self.journal.run_id,
            "session": self.journal.session,
            "first_started_at": self.first_started_at,
            "status": status or ("halted" if self.risk.halted else "running"),
            "environment": s.environment.value,
            "mode": "dry-run" if s.dry_run else "live",
            "started_at": self.started_at,
            "updated_at": time.time(),
            "ws_connected": self.feed.connected,
            "trading_active": self.trading_active,
            "globally_paused": self.risk.globally_paused,
            "halt_reason": self.risk.halt_reason,
            "budget_binding": self._budget_binding,
            "totals": {
                "balance": self.state.balance,
                "capital_in_use": self.capital_in_use(),
                "max_capital": s.risk.max_capital,
                "session_pnl": view.session_pnl if view else 0,
                "exposure": view.total_exposure if view else 0,
                "rewards_earned": rewards_total,  # all runs in this journal
                "rewards_session": self.tracker.session_earned,
                "rewards_per_hour": rate,
                "requotes": self.requotes,
                "fills": self.fills_total,
                "resting_orders": len(self.state.our_orders()),
            },
            "markets": markets,
            "orders": self._orders_with_queue(),
            "config": self._config_json,
            # Persisted for the next run (see RunJournal.previous).
            "ledger": self.tracker.ledger(self.titles),
            "fills_total": self.fills_total,
            "requotes_total": self._requotes_before + self.requotes,
        }

    def _orders_with_queue(self) -> list[dict[str, Any]]:
        """Resting orders plus their rank in the book (see :mod:`engine.queue`)."""
        rows = []
        stripped: dict[str, Orderbook | None] = {}
        for order in self.state.our_orders():
            book = self.state.book(order.ticker)
            if order.ticker not in stripped:
                own = own_orders_by_leg(self.state.our_orders(order.ticker), self.state.queue_ahead)
                stripped[order.ticker] = strip_own(book, own) if book else None
            report = queue_report(
                order,
                book,
                stripped[order.ticker],
                self.state.queue_ahead,
                self.rewards.get(order.ticker),
            )
            rows.append(
                {**_order_json(order), **report, "age": self.state.order_age(order.order_id)}
            )
        return rows

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


def _leg_json(d: LegDecision) -> dict[str, Any]:
    return {
        "side": d.quote.side.value if d.quote else None,
        "price": d.quote.price if d.quote else None,
        "size": d.quote.size if d.quote else None,
        "reason": d.reason,
        "share": d.score.share if d.score else None,
    }


def _order_json(o: Order) -> dict[str, Any]:
    return {
        "order_id": o.order_id,
        "ticker": o.ticker,
        "side": o.side.value,
        "price": o.yes_price,
        "size": o.remaining,
    }


def _book_json(book: Orderbook | None) -> dict[str, Any] | None:
    if book is None:
        return None
    return {
        "bid": book.best_yes_bid,
        "ask": book.best_yes_ask,
        "mid": book.mid,
        "bid_size": book.yes[0].size if book.yes else None,
        "ask_size": book.no[0].size if book.no else None,
        "yes_depth": book.depth(Leg.YES),
        "no_depth": book.depth(Leg.NO),
    }
