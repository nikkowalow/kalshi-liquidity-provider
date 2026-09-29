"""The main quoting loop.

Each cycle (every ``loop.interval_seconds``):

1. Periodically check exchange status and re-select markets.
2. Snapshot state with four reads: our resting orders, positions, balance,
   and every quoted market's order book in one batched call.
3. Update risk (P&L, exposure, fill bursts).
4. For each market: strip our own orders from the book, compute quotes,
   and reconcile them against what is resting.
5. Execute the combined plan.

REST polling keeps the bot simple and debuggable. The cost is reaction time
of about one cycle, which is why the default ``reward`` placement prefers the
deepest price that still earns nearly full reward over the top of the book.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import defaultdict
from decimal import Decimal

import httpx

from kalshi_lp.config import Settings
from kalshi_lp.core.types import ZERO, Leg
from kalshi_lp.engine.executor import OrderExecutor
from kalshi_lp.engine.reconciler import Plan, reconcile
from kalshi_lp.engine.risk import RiskManager
from kalshi_lp.exchange.client import KalshiClient
from kalshi_lp.exchange.errors import KalshiError
from kalshi_lp.exchange.models import Market, Order, Position
from kalshi_lp.strategy.quoting import MarketContext, QuoteEngine
from kalshi_lp.strategy.rewards import RewardParams, expected_daily_reward
from kalshi_lp.strategy.selection import MarketSelector

log = logging.getLogger(__name__)


class LiquidityBot:
    def __init__(self, settings: Settings, client: KalshiClient):
        self.settings = settings
        self.client = client
        self.engine = QuoteEngine(settings.quoting, settings.risk.max_position_per_market)
        self.selector = MarketSelector(client, settings.selection, settings.quoting, self.engine)
        self.risk = RiskManager(settings.risk)
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
        self._group_needs_reset = False
        self._stop = asyncio.Event()
        self._last_select = float("-inf")
        self._last_status = float("-inf")
        self.cycles = 0

    def stop(self) -> None:
        log.info("stop requested")
        self._stop.set()

    # --------------------------------------------------------------- lifecycle

    async def run(self, max_cycles: int | None = None) -> None:
        await self.startup()
        interval = self.settings.loop.interval_seconds
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                try:
                    await self.cycle()
                except (KalshiError, httpx.HTTPError) as exc:
                    log.error("cycle failed: %s", exc)
                    self.risk.record_cycle(False)
                self.cycles += 1
                if max_cycles is not None and self.cycles >= max_cycles:
                    break
                delay = max(0.0, interval - (time.monotonic() - started))
                with contextlib.suppress(TimeoutError):  # sleep, but wake early on stop()
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
        finally:
            await self.shutdown()

    async def startup(self) -> None:
        s = self.settings
        mode = "DRY-RUN" if s.dry_run else "LIVE"
        log.info("starting in %s environment (%s) against %s", s.environment.value, mode, s.api_url)
        status = await self.client.get_exchange_status()
        balance = await self.client.get_balance()
        log.info(
            "exchange_active=%s trading_active=%s balance=$%.2f",
            status.exchange_active,
            status.trading_active,
            balance.balance,
        )
        orphans = await self._our_resting_orders()
        if orphans:
            log.warning("found %d resting orders from a previous run; cancelling", len(orphans))
            await self.executor.execute(Plan(cancels=orphans))
        if not s.dry_run and s.risk.order_group_contracts_limit > 0:
            group = await self.client.create_order_group(s.risk.order_group_contracts_limit)
            self.executor.order_group_id = group
            log.info(
                "order group %s: exchange cancels all bot orders if >%d contracts fill in 15s",
                group,
                s.risk.order_group_contracts_limit,
            )

    async def shutdown(self) -> None:
        log.info("shutting down: cancelling bot orders")
        try:
            await self.cancel_all_ours()
        except (KalshiError, httpx.HTTPError) as exc:
            log.error(
                "could not cancel bot orders cleanly (%s); cancelling ALL account orders", exc
            )
            if not self.settings.dry_run:
                await self.client.cancel_all_orders()
        group = self.executor.order_group_id
        if group:
            try:
                await self.client.delete_order_group(group)
            except KalshiError as exc:
                log.warning("could not delete order group %s: %s", group, exc)

    async def cancel_all_ours(self) -> None:
        orders = await self._our_resting_orders()
        if orders:
            await self.executor.execute(Plan(cancels=orders))
        log.info("cancelled %d bot orders", len(orders))

    async def _our_resting_orders(self) -> list[Order]:
        return [o for o in await self.client.get_resting_orders() if self.executor.is_ours(o)]

    # ---------------------------------------------------------------- periodic

    async def _check_status(self) -> None:
        status = await self.client.get_exchange_status()
        active = status.exchange_active and status.trading_active
        if active != self.trading_active:
            log.warning("exchange trading_active changed to %s", active)
        self.trading_active = active
        self._last_status = time.monotonic()

    async def _reselect(self, positions: dict[str, Position]) -> None:
        candidates = await self.selector.select()
        chosen = {c.ticker: c for c in candidates}

        # Keep deselected markets where we still hold a position, quoting only
        # the side that works the position down.
        leftovers = [
            t
            for t, p in positions.items()
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
        self._last_select = time.monotonic()

        log.info("selected %d markets:", len(candidates))
        for c in candidates:
            log.info(
                "  %-40s est $%.2f/day  (program $%.2f/day, target %s, df %s)",
                c.ticker,
                c.est_daily_reward,
                c.reward.reward_per_day,
                c.reward.target_size.normalize(),
                c.reward.discount_factor,
            )
        if self.reduce_only:
            log.info("reduce-only (holding inventory): %s", ", ".join(sorted(self.reduce_only)))

    # ------------------------------------------------------------------- cycle

    async def cycle(self) -> None:
        loop_cfg = self.settings.loop
        now = time.monotonic()
        if now - self._last_status >= loop_cfg.status_check_interval_seconds:
            await self._check_status()
        if not self.trading_active:
            await self.cancel_all_ours()
            return

        positions = await self.client.get_positions()
        if now - self._last_select >= loop_cfg.reselect_interval_seconds:
            await self._reselect(positions)

        orders = await self._our_resting_orders()
        balance = await self.client.get_balance()
        tickers = list(self.markets)
        books = await self.client.get_orderbooks(tickers) if tickers else {}

        view = self.risk.update(
            set(tickers),
            positions,
            {t: b.mid for t, b in books.items()},
            balance.balance,
        )
        if view.halted:
            await self.cancel_all_ours()
            self.stop()
            return
        if self._group_needs_reset and not view.globally_paused and self.executor.order_group_id:
            await self.client.reset_order_group(self.executor.order_group_id)
            self._group_needs_reset = False
            log.info("order group reset; resuming")

        ours: dict[str, list[Order]] = defaultdict(list)
        for order in orders:
            ours[order.ticker].append(order)

        plan = Plan()
        for ticker, stale in ours.items():
            if ticker not in self.markets:
                plan.cancels += stale

        est_total = ZERO
        for ticker, market in self.markets.items():
            own = ours.get(ticker, [])
            book = books.get(ticker)
            position = positions.get(ticker, Position.flat(ticker)).position
            if (
                view.globally_paused
                or book is None
                or self.risk.market_paused(ticker)
                or self.risk.near_close(market)
            ):
                plan.cancels += own
                continue

            own_levels: dict[Leg, dict[Decimal, Decimal]] = {Leg.YES: {}, Leg.NO: {}}
            for o in own:
                levels = own_levels[o.leg]
                levels[o.leg_price] = levels.get(o.leg_price, ZERO) + o.remaining

            reward = self.rewards.get(ticker, self.selector.default_reward())
            ctx = MarketContext(
                market=market,
                book=book.without(own_levels),
                position=position,
                reward=reward,
                allow_increase=view.allow_increase and ticker not in self.reduce_only,
            )
            decisions = self.engine.quote(ctx)
            plan.extend(reconcile([d.quote for d in decisions.values() if d.quote], own))

            yes, no = decisions[Leg.YES], decisions[Leg.NO]
            if yes.score and no.score:
                est_total += expected_daily_reward(yes.score, no.score, reward)
            log.log(
                logging.INFO if self.settings.dry_run else logging.DEBUG,
                "%s pos=%s mid=%s | YES %s (%s) | NO %s (%s)",
                ticker,
                position.normalize(),
                book.mid,
                yes.quote or "-",
                yes.reason,
                no.quote or "-",
                no.reason,
            )

        report = await self.executor.execute(plan)
        self.risk.record_cycle(report.errors == 0)
        if report.group_blocked:
            self.risk.pause_all("exchange order group tripped (fill limit hit)")
            self._group_needs_reset = True
        if plan or self.cycles % 30 == 0:
            log.info(
                "cycle %d: pnl=%+.2f exposure=$%.2f est_reward=$%.2f/day | "
                "cancel=%d decrease=%d place=%d rejected=%d errors=%d",
                self.cycles,
                view.session_pnl,
                view.total_exposure,
                est_total,
                report.cancelled,
                report.decreased,
                report.created,
                report.rejected,
                report.errors,
            )
