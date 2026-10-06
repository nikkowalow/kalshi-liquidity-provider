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
import json
import logging
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any, Protocol

import httpx

from kalshi_lp.config import Settings, budget_scaled
from kalshi_lp.core.orderbook import Orderbook
from kalshi_lp.core.types import ZERO, Leg, Quote, Side
from kalshi_lp.engine import balance_history, budget, fill_records
from kalshi_lp.engine.executor import ExecutionReport, OrderExecutor
from kalshi_lp.engine.payouts import PayoutTracker
from kalshi_lp.engine.queue import queue_report
from kalshi_lp.engine.reconciler import Plan, reconcile
from kalshi_lp.engine.reward_tracker import (
    MarketRewardStats,
    RewardTracker,
    own_orders_by_leg,
    strip_own,
)
from kalshi_lp.engine.risk import Mark, RiskManager, RiskView
from kalshi_lp.engine.unwind import PASSIVE_UNWIND_ENABLED, Entry, plan_exit
from kalshi_lp.exchange.auth import Signer
from kalshi_lp.exchange.client import KalshiClient
from kalshi_lp.exchange.errors import KalshiError
from kalshi_lp.exchange.models import Market, Order
from kalshi_lp.feed.state import MarketState
from kalshi_lp.feed.stream import StreamingFeed
from kalshi_lp.journal import NullJournal, RunJournal
from kalshi_lp.strategy.fill_risk import FillRisk, PlannedQuote, estimate_fill_risk
from kalshi_lp.strategy.quoting import LegDecision, MarketContext, QuoteEngine
from kalshi_lp.strategy.rewards import (
    Competition,
    RewardParams,
    competition,
    expected_daily_reward,
)
from kalshi_lp.strategy.scanner import MarketScanner
from kalshi_lp.strategy.selection import Candidate, MarketSelector

log = logging.getLogger(__name__)

TRANSIENT_ERRORS = (KalshiError, httpx.HTTPError)
RESIZE_STEP = Decimal("0.2")  # auto sizing: grow a market's size only by 20% or more
FLATTEN_WARN_SECONDS = 300.0  # "can't flatten" is logged at most this often per market
SCANNER_FIRST_DELAY = 20.0  # seconds after start-up: the first market scan goes first
LIVE_FILL_RISK_SECONDS = 60.0  # re-estimate fill risk from the resting orders this often
PAYOUT_CHECK_SECONDS = 300.0  # reconcile the balance for reward payouts this often
BALANCE_HISTORY_SECONDS = 30.0  # reuse the balance history this long between dashboard asks
CLOSED_PROGRAMS_SECONDS = 600.0  # refresh which ended program periods still await payout
BOOK_LEVELS = 10  # bid levels per side in each snapshot's book (the market popup's ladder)


class Feed(Protocol):
    state: MarketState

    @property
    def connected(self) -> bool: ...

    async def start(self, tickers: Iterable[str]) -> None: ...
    async def stop(self) -> None: ...
    async def set_tickers(self, tickers: Iterable[str]) -> None: ...


@dataclass(frozen=True, slots=True)
class _Scan:
    """A market scan's result, ready to adopt."""

    incumbents: dict[str, Decimal]  # markets we had a stake in -> rewards earned there
    previous: list[str]  # markets being quoted before the scan
    paused: set[str]
    candidates: list[Candidate]  # best first
    kept_paused: list[str]  # paused markets that keep their slot
    refreshed: list[Market]  # deselected markets we still hold a position in
    verdicts: dict[str, dict[str, Any]]  # incumbent -> why selection kept or dropped it


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
        settings = budget_scaled(settings)  # load_settings did already; settings built in code too
        self.settings = settings
        self.engine = QuoteEngine(settings.quoting, settings.risk.max_position_per_market)
        self.selector = MarketSelector(
            client,
            settings.selection,
            settings.quoting,
            self.engine,
            max_capital=settings.risk.max_capital,
        )
        self.risk = RiskManager(settings.risk)
        # Read-only research for the dashboard: never changes what the bot trades.
        self.scanner = (
            MarketScanner(client, self.selector, settings.scanner)
            if settings.scanner.enabled
            else None
        )
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
        self.tracker.restore(prev.get("ledger") or {}, prev.get("rewards_unattributed"))
        self.titles: dict[str, str] = {}
        self.fills_total = int(prev.get("fills_total") or 0)
        self._requotes_before = int(prev.get("requotes_total") or 0)
        self.first_started_at = float(prev.get("first_started_at") or self.started_at)
        minimum = settings.selection.payout_minimum
        self.payouts: PayoutTracker | None = (
            PayoutTracker.restore(prev["payouts"], minimum) if prev.get("payouts") else None
        )
        self.state.listeners.append(self._count_fills)
        self._config_json = settings.model_dump(mode="json")
        self._group_needs_reset = False
        self._budget_binding = False
        self._last_exit: dict[str, float] = {}  # ticker -> monotonic time of the last exit order
        self._flatten_warned: dict[str, float] = {}  # ticker -> last "can't flatten" warning
        self._entries: dict[str, Entry] = {}  # open positions' average entry (from fills)
        self._unwinding: dict[str, dict[str, Any]] = {}  # passive exits resting now
        self._live_risk: dict[str, FillRisk] = {}  # fill risk of the orders resting now
        # Fill risk each market was picked with (for the fill records), carried over restarts.
        self._entry_risk: dict[str, dict[str, Any]] = dict(prev.get("entry_risk") or {})
        # Ended program periods Kalshi hasn't paid out yet ("ticker|end"); None: not fetched.
        self._closed_periods: set[str] | None = None
        self._closed_at = float("-inf")
        self._balance_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._selected_at: dict[str, float] = {}  # ticker -> monotonic time it was selected
        self._sizes: dict[str, Decimal] = {}  # auto sizing, per market
        self._reselect_soon = False  # a market got paused: re-rank early
        self._rescan_requested = False  # re-rank at once (from the dashboard)
        self.held_since: float | None = None  # paused from the dashboard (wall time), until resumed
        self._selected: dict[str, Candidate] = {}  # latest selection details per market
        self._last_reselect = float("-inf")
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self.restart_requested = False  # stopped to come back with a re-read config

    def _count_fills(self, kind: str, data: dict[str, Any]) -> None:
        if kind == "fill":
            self.fills_total += 1
            self._record_fill_risk(data)  # before the fill changes the resting orders' risk
            self._note_entry(data)

    async def _backfill_fill_records(self) -> None:
        """Give earlier fills their fill-risk record (what the bot estimated at the time)."""
        path = getattr(self.journal, "dir", None)
        if path is None:
            return
        try:
            records = await asyncio.to_thread(fill_records.backfill, path / "events.jsonl")
        except OSError as exc:
            log.warning("fill risk backfill skipped: %s", exc)
            return
        for record in records:
            self.journal.event("fill_risk", **record)
        if records:
            log.info("backfilled fill risk for %d earlier fill(s)", len(records))

    def _record_fill_risk(self, fill: dict[str, Any]) -> None:
        """Journal the fill risk this market was entered with and had right before this fill."""
        ticker = fill.get("ticker")
        if fill.get("is_taker") or ticker not in self.markets:
            return  # our own exits, or a market the bot isn't quoting (a manual trade)
        now = time.time()
        live = self._live_risk.get(ticker)
        if live is not None:
            quotes = self._resting_quotes(ticker)
            at_fill: dict[str, Any] | None = {
                "ts": now,
                "basis": "live",
                "events_per_day": live.hits_per_day,
                "fills_per_day": live.fills_per_day,
                "cost_per_day": live.cost_per_day,
                "size": sum((q.size for q in quotes.values()), ZERO) or None,
                "approx": False,
            }
        elif ticker in self._selected:
            c = self._selected[ticker]
            at_fill = fill_records.estimate(
                {**_fill_risk_json(c), "size": c.size}, now, "selection"
            )
        else:
            at_fill = None
        self.journal.event(
            "fill_risk",
            fill_ts=now,
            order_id=fill.get("order_id"),
            ticker=ticker,
            entry=self._entry_risk.get(ticker),
            at_fill=at_fill,
            backfilled=False,
        )

    def _note_entry(self, fill: dict[str, Any]) -> None:
        """Track each open position's average entry price, for passive exits."""
        if fill.get("post_position") is None or fill.get("price") is None:
            return
        ticker = fill["ticker"]
        price = Decimal(str(fill["price"]))
        after = Decimal(str(fill["post_position"]))
        count = Decimal(str(fill["count"]))
        before = after - (count if fill.get("side") == "bid" else -count)
        old = self._entries.get(ticker)
        if after == 0:
            self._entries.pop(ticker, None)
        elif old is None or before == 0 or (before > 0) != (after > 0):
            self._entries[ticker] = Entry(price, time.monotonic())  # opened (or flipped) here
        elif abs(after) > abs(before):
            added = abs(after) - abs(before)
            avg = (old.yes_price * abs(before) + price * added) / abs(after)
            self._entries[ticker] = Entry(avg, old.opened)

    def _entry(self, ticker: str, position: Decimal, now: float) -> Entry | None:
        """Average entry of the open position: from fills, else Kalshi's cost basis."""
        if ticker in self._entries:
            return self._entries[ticker]
        pos = self.state.positions.get(ticker)
        if pos is None or pos.market_exposure <= 0 or (pos.position > 0) != (position > 0):
            return None
        leg_price = pos.market_exposure / abs(pos.position)  # what each held contract cost
        entry = Entry(leg_price if position > 0 else 1 - leg_price, now)
        self._entries[ticker] = entry
        return entry

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
        # Start-up (waiting for the exchange, the first market scan) can take a minute.
        self.journal.write_state(self.snapshot(status="starting"))
        try:
            await self.startup()
            if self.stopping:
                return
            tasks = [
                asyncio.create_task(self._maintenance_loop(), name="maintenance"),
                asyncio.create_task(self._reward_loop(), name="rewards"),
                asyncio.create_task(self._journal_loop(), name="journal"),
            ]
            if self.scanner is not None:
                tasks.append(asyncio.create_task(self._scanner_loop(), name="scanner"))
            if not s.dry_run:
                tasks.append(asyncio.create_task(self._payout_loop(), name="payouts"))
                tasks.append(asyncio.create_task(self._live_risk_loop(), name="live-fill-risk"))
            for task in tasks:
                task.add_done_callback(_report_crash)
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

        if s.quoting.size > s.risk.max_position_per_market:
            log.warning(
                "quoting.size is %s but risk.max_position_per_market is %s: orders are capped "
                "at %s contracts. Raise max_position_per_market to quote the full size.",
                s.quoting.size,
                s.risk.max_position_per_market,
                s.risk.max_position_per_market,
            )
        group_limit = s.risk.order_group_contracts_limit
        if not s.dry_run and 0 < group_limit < s.quoting.size:
            log.warning(
                "risk.order_group_contracts_limit (%s) is below quoting.size (%s): one full fill "
                "trips the exchange kill switch and cancels every order. Use about 2 x size.",
                group_limit,
                s.quoting.size,
            )

        orphans = self.state.our_orders()
        if orphans:
            log.warning("found %d resting orders from a previous run; cancelling", len(orphans))
            await self._pull(orphans, "left over from a previous run")
        # Positions from the last run stay open: the first selection keeps their markets
        # (reduce-only) and the usual exit (risk.exit_mode) takes over from there.
        held = {t: p.position for t, p in self.state.positions.items() if p.position != 0}
        held = {t: n for t, n in held.items() if t in self._traded()}
        if held:
            log.warning(
                "keeping %d open position(s) from the last run (%s); exiting them %s",
                len(held),
                ", ".join(f"{t} {n:+f}" for t, n in held.items()),
                "by crossing the book" if s.risk.flatten_on_fill else "reduce-only",
            )
        if s.risk.exit_mode == "passive" and not PASSIVE_UNWIND_ENABLED:
            log.warning(
                "risk.exit_mode is passive, but passive exits are off: every fill exits at once"
            )

        if not s.dry_run and s.risk.order_group_contracts_limit > 0:
            group = await self.client.create_order_group(s.risk.order_group_contracts_limit)
            self.executor.order_group_id = group
            log.info(
                "order group %s: exchange cancels all bot orders if >%d contracts fill in 15s",
                group,
                s.risk.order_group_contracts_limit,
            )

        if not s.dry_run:
            await self._backfill_fill_records()
        await self.reselect()
        await self.feed.start(self.markets)

    def _traded(self) -> set[str]:
        """Markets the bot has quoted: now, this session, or in earlier sessions (the ledger).

        Its positions can only be in these; anything else on the account is yours.
        """
        return set(self.markets) | set(self._selected) | set(self.tracker.stats)

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
        # A stop (Ctrl+C, the dashboard) keeps positions: the next start picks them up.
        # A risk halt closes them: that's what the halt is for.
        if self.risk.halted:
            try:
                await self.flatten_all()
            except TRANSIENT_ERRORS as exc:
                log.critical("could not close positions before stopping (%s): check Kalshi", exc)
        else:
            held = [
                t for t, p in self.state.positions.items() if p.position and t in self._traded()
            ]
            if held:
                log.warning(
                    "leaving %d open position(s) for the next start: %s", len(held), ", ".join(held)
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
        await self._pull(orders, "bot shutting down")
        log.info("cancelled %d bot orders", len(orders))

    async def sync_from_rest(self) -> None:
        """Replace streamed state with a REST snapshot: the source of truth for drift."""
        as_of = self.state.now()
        orders = await self.client.get_resting_orders()
        positions = await self.client.get_positions()
        balance = await self.client.get_balance()
        self.state.replace_orders((o for o in orders if self.executor.is_ours(o)), as_of)
        self.state.replace_positions(positions)
        self.state.balance = balance.balance

    # -------------------------------------------------------------- selection

    async def reselect(self) -> None:
        """Scan for the best-paying markets and switch to them."""
        self._adopt(await self._scan())

    async def _scan(self) -> _Scan:
        """Rank markets (the slow part: order books and trades over the API).

        Reads state but changes nothing, so it runs without the quoting lock and
        the bot keeps quoting through a scan.
        """
        # Markets we have a stake in: quoting now, or rewards earned in a program period
        # that's still running. Kalshi's $1 minimum applies per period, so earnings from
        # periods that have ended count for nothing here. Selection gives these an edge.
        now_wall = time.time()
        incumbents = {
            t: earned
            for t, s in self.tracker.stats.items()
            if (earned := s.open_period_earned(now_wall)) > 0
        }
        for ticker in self.markets:
            if ticker not in self.reduce_only:
                incumbents.setdefault(ticker, ZERO)
        previous = [t for t in self.markets if t not in self.reduce_only]
        # Paused markets sit out this round so their slot can go to a better market.
        paused = {t for t in previous if self.risk.market_paused(t)}
        # Recently selected markets keep their slots a while (unless they stop qualifying).
        hold = self.settings.selection.min_hold_seconds
        now = time.monotonic()
        keep = {
            t
            for t in previous
            if t not in paused and now - self._selected_at.get(t, float("-inf")) < hold
        }
        candidates = await self.selector.select(
            incumbents, exclude=paused, keep=keep, quoting=previous
        )
        chosen = {c.ticker for c in candidates}
        # Nothing better to take their slot? Paused markets keep it (resuming after the pause).
        room = self.settings.selection.max_markets - len(candidates)
        kept_paused = [t for t in previous if t in paused][: max(room, 0)]
        # Keep deselected markets where we still hold a position, quoting only
        # the side that works the position down.
        leftovers = [
            t
            for t, p in self.state.positions.items()
            if p.position != 0 and t in self._traded() and t not in chosen and t not in kept_paused
        ]
        refreshed = await self.client.get_markets(tickers=leftovers) if leftovers else []
        return _Scan(
            incumbents,
            previous,
            paused,
            candidates,
            kept_paused,
            refreshed,
            dict(self.selector.verdicts),
        )

    def _journal_deselected(self, scan: _Scan, candidates: Sequence[Candidate]) -> None:
        """Journal why each market the bot just left was dropped, with the numbers behind it.

        The dashboard shows it when you click a "market no longer selected" cancel.
        """
        chosen = {c.ticker for c in candidates}
        selected = [
            {"ticker": c.ticker, "held": c.ticker in scan.previous, **c.figures()}
            for c in candidates
        ]
        for ticker in scan.previous:
            if ticker in chosen or ticker in scan.kept_paused:
                continue
            verdict = dict(
                scan.verdicts.get(ticker)
                or {"stage": "unknown", "reason": "selection didn't record why"}
            )
            if ticker in scan.paused:
                verdict["stage"] = "paused"
                verdict["reason"] = (
                    f"paused ({self.risk.pause_reasons.get(ticker, 'risk limit')}), so it sat "
                    "out this scan, and better markets filled all the slots"
                )
            self.journal.event(
                "deselect",
                ticker=ticker,
                **verdict,
                position=self.state.position(ticker),
                reduce_only=ticker in self.reduce_only,
                selected=selected,
            )

    def _adopt(self, scan: _Scan) -> None:
        """Switch to the markets a scan picked. Quick and synchronous: call under the lock."""
        candidates = scan.candidates
        before = dict(self._selected)
        old_markets, old_rewards = self.markets, self.rewards
        self.markets = {c.ticker: c.market for c in candidates}  # best first: capital order
        self.rewards = {c.ticker: c.reward for c in candidates}
        for ticker in scan.kept_paused:
            if ticker in old_markets:
                self.markets[ticker] = old_markets[ticker]
                self.rewards[ticker] = old_rewards.get(ticker, self.selector.default_reward())
        self.reduce_only = set()
        for market in scan.refreshed:
            if market.is_tradable:
                self.markets[market.ticker] = market
                self.rewards[market.ticker] = self.selector.default_reward()
                self.reduce_only.add(market.ticker)
        for c in candidates:
            self._selected[c.ticker] = c
            if c.ticker not in self._entry_risk:  # entering: what fill risk it was picked with
                entry = fill_records.estimate(
                    {**_fill_risk_json(c), "size": c.size}, time.time(), "selection"
                )
                if entry is not None:
                    self._entry_risk[c.ticker] = entry
        for ticker in set(self._entry_risk) - set(self.markets):
            del self._entry_risk[ticker]  # left the market: a later entry starts over
        now = time.monotonic()  # when each market got its slot (for min_hold_seconds)
        self._selected_at = {t: self._selected_at.get(t, now) for t in self.markets}
        self._journal_deselected(scan, candidates)

        changed = [c.ticker for c in candidates] != [
            t for t in scan.previous if t not in scan.paused
        ]
        if changed or not scan.previous:  # the dashboard's live figures come from snapshots
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
                        "competition": _competition_json(c.competition),
                        "size": c.size,
                        **_fill_risk_json(c),
                    }
                    for c in candidates
                ],
                reduce_only=sorted(self.reduce_only),
            )
            log.info("selected %d markets (best $/h first):", len(candidates))
            for c in candidates:
                log.info(
                    "  %-44s $%.3f/h net  (est $%.2f/day at %s/side%s; program $%.2f/day, "
                    "target %s, %s)%s",
                    c.ticker,
                    c.net_daily_reward / 24,
                    c.est_daily_reward,
                    f"{c.size.normalize():f}",
                    _fill_risk_text(c),
                    c.reward.reward_per_day,
                    f"{c.reward.target_size.normalize():f}",
                    _competition_text(c.competition),
                    _stake_text(c, scan.incumbents),
                )
        else:
            log.info(
                "scan: still in the best markets (%s)",
                ", ".join(f"{c.ticker} ${c.net_daily_reward / 24:.3f}/h" for c in candidates),
            )
        for ticker in scan.kept_paused:
            log.info("  %-44s paused; keeps its slot (no better market found)", ticker)
        for ticker in scan.previous:
            if ticker not in self.markets:
                earned = scan.incumbents.get(ticker, ZERO)
                last = before.get(ticker)
                rate = f", ${last.net_daily_reward / 24:.3f}/h at its last scan" if last else ""
                why = (
                    "paused, and a better market took its slot"
                    if ticker in scan.paused
                    else "a better-paying market took its slot (or it no longer passes filters)"
                )
                log.warning(
                    "switched out of %s ($%.2f earned there%s): %s", ticker, earned, rate, why
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
            if self.settings.risk.flatten_on_fill:
                tickers |= {t for t in self.markets if self.state.position(t) != 0}
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
            await self._pull(self.state.our_orders(), "exchange trading is closed")
            return
        view = self._risk_view()
        self._last_view = view
        if view.halted:
            await self._pull(self.state.our_orders(), f"risk halt: {self.risk.halt_reason}")
            self.stop()
            return
        if self._group_needs_reset and not view.globally_paused and self.executor.order_group_id:
            await self.client.reset_order_group(self.executor.order_group_id)
            self._group_needs_reset = False
            log.info("order group reset; resuming")

        plan = Plan()
        self._sizes = self._auto_sizes()
        paused_before = {t for t in self.markets if self.risk.market_paused(t)}
        for ticker in tickers:
            own = self.state.our_orders(ticker)
            if self._flatten(ticker, own, plan):
                continue
            decisions = self._decide(ticker, view)
            if isinstance(decisions, str):
                plan.cancel(own, decisions)
                self._desired.pop(ticker, None)
                self._decisions.pop(ticker, None)  # dashboard: no stale quotes on paused markets
                continue
            quotes = [d.quote for d in decisions.values() if d.quote]
            self._note_quotes(ticker, decisions, quotes)
            if not self.settings.dry_run:
                plan.extend(reconcile(quotes, own, reasons=_side_reasons(decisions)))

        if self.settings.loop.reselect_on_pause and any(
            self.risk.market_paused(t) for t in set(self.markets) - paused_before
        ):
            self._reselect_soon = True  # a slot just froze: look for a better market
        if plan.creates and self.settings.risk.max_capital is not None:
            self._fit_budget(plan, self.settings.risk.max_capital)
        if not plan:
            return
        report = await self.executor.execute(plan)
        self._apply(report, plan)
        self.risk.record_cycle(report.errors == 0)
        if report.group_blocked:
            self.risk.pause_all("exchange order group tripped (fill limit hit)")
            self._group_needs_reset = True
        log.info(
            "requote %s: pnl=%+.2f capital=$%.2f | cancel=%d decrease=%d place=%d "
            "exit=%d rejected=%d errors=%d",
            ",".join(tickers) if len(tickers) <= 3 else f"{len(tickers)} markets",
            view.session_pnl,
            self.capital_in_use(),
            report.cancelled,
            report.decreased,
            report.created,
            len(report.exited),
            report.rejected,
            report.errors,
        )

    def _decide(self, ticker: str, view: RiskView) -> dict[Leg, LegDecision] | str:
        """Quotes for one market, or the reason it must not be quoted right now."""
        market = self.markets.get(ticker)
        book = self.state.book(ticker)
        if market is None:
            return "market no longer selected"
        if self.held_since is not None:
            return "paused from the dashboard"
        if not self.feed.connected:
            return "market data feed disconnected"
        if book is None:
            return "order book not trusted (waiting for a fresh snapshot)"
        if view.globally_paused:
            return f"all quoting paused: {self.risk.global_pause_reason}"
        if self.risk.market_paused(ticker):
            return f"market paused: {self.risk.pause_reasons.get(ticker, 'risk limit')}"
        if self.risk.near_close(market):
            hours = self.settings.risk.close_buffer_seconds / 3600
            return f"market closes or resolves within {hours:g}h"
        if self._moved_too_fast(ticker, book.mid):
            return f"market paused: {self.risk.pause_reasons.get(ticker, 'price moved fast')}"
        if self._sizes.get(ticker) == 0:
            return "no capital left: higher-paying markets use the budget"
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
            size=self._sizes.get(ticker),
        )
        decisions = self.engine.quote(ctx)
        self._desired[ticker] = [d.quote for d in decisions.values() if d.quote]
        self._decisions[ticker] = decisions
        return decisions

    def _flatten(self, ticker: str, own: list[Order], plan: Plan) -> bool:
        """Never hold shares: if ``ticker`` has a position, pull its quotes and get out.

        Crosses the book now. (``risk.exit_mode: passive``, resting an exit at the
        entry price for a while, is switched off: see
        ``engine.unwind.PASSIVE_UNWIND_ENABLED``.) Returns True while the market holds
        a position (skip normal quoting). Runs before the pause checks on
        purpose: a fill often triggers a pause, and the position must still be closed.
        Only markets the bot trades: a position opened by hand elsewhere is left alone.
        """
        risk = self.settings.risk
        position = self.state.position(ticker)
        if position == 0:
            self._entries.pop(ticker, None)
            self._unwinding.pop(ticker, None)
        if not risk.flatten_on_fill or position == 0 or ticker not in self.markets:
            return False
        self._desired.pop(ticker, None)
        self._decisions.pop(ticker, None)
        now = time.monotonic()
        book = self.state.book(ticker)
        why = "flatten"
        if PASSIVE_UNWIND_ENABLED and risk.exit_mode == "passive":
            market = self.markets[ticker]
            closing = self.risk.near_close(market)
            exit_ = plan_exit(
                position,
                self._entry(ticker, position, now),
                book,
                market.grid,
                now,
                seconds=risk.unwind_seconds,
                stop=risk.unwind_stop,
                giveup=risk.unwind_giveup,
                emergency="market about to close" if closing else None,
            )
            if exit_.action == "rest" and exit_.price is not None:
                self._rest_exit(ticker, position, exit_.side, exit_.price, exit_.why, own, plan)
                return True
            if ticker in self._unwinding:
                log.info("%s: crossing to exit %s (%s)", ticker, position, exit_.why)
            why = f"flatten ({exit_.why})"
        self._unwinding.pop(ticker, None)
        plan.cancel(own, f"flatten: holding {position:+f} contracts")
        if now - self._last_exit.get(ticker, float("-inf")) < risk.flatten_retry_seconds:
            return True  # an exit just went out; wait for its fills to arrive
        if self._add_exit(plan, ticker, position, book, risk.flatten_slippage, why):
            self._last_exit[ticker] = now
        return True

    def _rest_exit(
        self,
        ticker: str,
        position: Decimal,
        side: Side,
        price: Decimal,
        why: str,
        own: list[Order],
        plan: Plan,
    ) -> None:
        """Keep one post-only exit order for the whole position at ``price``; cancel the rest."""
        if ticker not in self._unwinding:
            log.info(
                "%s: holding %+f; resting an exit at %s instead of crossing",
                ticker,
                position,
                price,
            )
        holding = f"holding {position:+f} contracts"
        sub = reconcile(
            [Quote(ticker, side, price, abs(position))],
            own,
            reasons={side: why, side.opposite: holding},
        )
        plan.unwinds += sub.creates  # outside the order group and the quoting budget
        sub.creates = []
        plan.extend(sub)
        entry = self._entries.get(ticker)
        self._unwinding[ticker] = {
            "side": side.value,
            "price": price,
            "size": abs(position),
            "entry": entry.yes_price if entry else None,
            "seconds_left": max(
                self.settings.risk.unwind_seconds - (time.monotonic() - entry.opened), 0.0
            )
            if entry
            else None,
            "why": why,
        }

    def _add_exit(
        self,
        plan: Plan,
        ticker: str,
        position: Decimal,
        book: Orderbook | None,
        slippage: Decimal,
        why: str = "flatten",
    ) -> bool:
        """Add an immediate-or-cancel order closing ``position`` against ``book``."""
        market = self.markets.get(ticker) or (
            self._selected[ticker].market if ticker in self._selected else None
        )
        if position > 0:  # long YES: sell into the best YES bid
            best = book.best_yes_bid if book else None
            price = None if best is None else max(best - slippage, Decimal("0.01"))
            side = Side.ASK
        else:  # long NO: buy YES from the best YES ask
            best = book.best_yes_ask if book else None
            price = None if best is None else min(best + slippage, Decimal("0.99"))
            side = Side.BID
        if price is None:
            # Retried every requote until someone bids; say so every few minutes, not each time.
            now = time.monotonic()
            if now - self._flatten_warned.get(ticker, float("-inf")) >= FLATTEN_WARN_SECONDS:
                self._flatten_warned[ticker] = now
                log.warning(
                    "%s: can't flatten %s, no price on the other side (retrying)", ticker, position
                )
            return False
        self._flatten_warned.pop(ticker, None)
        if market is not None:
            price = (
                market.grid.round_down(price) if side is Side.ASK else market.grid.round_up(price)
            )
        plan.exits.append(Quote(ticker, side, price, abs(position)))
        plan.why[(ticker, side, price)] = (
            f"{why} {position:+f} filled contracts (best {best}, limit {price})"
        )
        return True

    async def flatten_all(
        self, attempts: int = 6, *, force: bool = False
    ) -> tuple[int, dict[str, Decimal]]:
        """Close every position in markets this bot traded, retrying (on start-up and stop).

        A halt or a stop must not leave a position unmanaged: an exit that finds
        no one on the other side is retried with the latest book, reaching one
        slippage step further each time. Runs only with ``flatten_on_fill``,
        unless ``force`` (the dashboard's button). Returns how many positions
        were open and the ones still open at the end.
        """
        risk = self.settings.risk
        if not (risk.flatten_on_fill or force) or self.settings.dry_run:
            return 0, {}
        ours = self._traded()
        open_: dict[str, Decimal] = {}
        found = 0
        for attempt in range(attempts):
            positions = await self.client.get_positions()
            open_ = {t: p.position for t, p in positions.items() if p.position != 0 and t in ours}
            if not attempt:
                found = len(open_)
            if not open_:
                if attempt:
                    log.info("all positions closed")
                return found, {}
            books = await self.client.get_orderbooks(sorted(open_))
            plan = Plan()
            slippage = risk.flatten_slippage * (attempt + 1)
            for ticker, position in open_.items():
                self._add_exit(
                    plan, ticker, position, books.get(ticker), slippage, "close leftover"
                )
            if plan:
                log.warning(
                    "closing %d leftover position(s) (attempt %d)",
                    len(open_),
                    attempt + 1,
                )
                self._apply(await self.executor.execute(plan), plan)
            await asyncio.sleep(1.0)
        log.critical(
            "COULD NOT CLOSE POSITIONS: %s. Close them on Kalshi, or restart the bot "
            "(it retries on start-up).",
            ", ".join(f"{t} {p:+f}" for t, p in open_.items()),
        )
        return found, open_

    def _auto_sizes(self) -> dict[str, Decimal]:
        """Per-market size that puts the capital budget to work (quoting.auto_size).

        Best market first (self.markets is in selection order): each gets full
        size until the budget runs out, since rewards scale with size. A leg
        capped by max_loss_per_fill is charged only what the cap lets it lock.
        A market's size only grows once it can grow by RESIZE_STEP or more, so
        book jitter doesn't resize (and re-queue) its orders back and forth.
        """
        q, cap = self.settings.quoting, self.settings.risk.max_capital
        if not q.auto_size or cap is None:
            return {}
        active = [
            t
            for t, m in self.markets.items()
            if t not in self.reduce_only
            and not self.risk.market_paused(t)
            and not self.risk.near_close(m)
            and self.state.book(t) is not None
        ]
        if not active:
            return {}
        in_positions = self.capital_in_use(excluding=self.state.our_orders())
        remaining = max(cap - in_positions, ZERO) * q.capital_utilization
        limit = self.settings.risk.max_position_per_market
        if q.max_size is not None:
            limit = min(limit, q.max_size)
        sizes: dict[str, Decimal] = {}
        for ticker in active:
            book = self.state.book(ticker)
            if book is None or book.best_yes_bid is None or book.best_yes_ask is None:
                continue
            # A YES bid at the best bid and a NO bid at 1 - best ask: an upper bound on
            # each leg's price (our quotes usually rest deeper, cheaper).
            legs = [p for p in (book.best_yes_bid, 1 - book.best_yes_ask) if p > 0]
            if not legs:
                continue
            size = self._affordable(legs, limit, remaining)
            previous = self._sizes.get(ticker)
            if previous is not None and ZERO < previous < size < previous * (1 + RESIZE_STEP):
                size = previous  # a few more contracts isn't worth resizing the orders
            sizes[ticker] = size
            remaining -= self._locked(legs, size)
        return sizes

    def _locked(self, legs: Sequence[Decimal], size: Decimal) -> Decimal:
        """Cash ``size`` contracts per leg lock at these leg prices, after the loss cap."""
        total = ZERO
        for price in legs:
            cap = self.engine.loss_cap(price)
            total += (size if cap is None else min(size, cap)) * price
        return total

    def _affordable(self, legs: Sequence[Decimal], limit: Decimal, budget: Decimal) -> Decimal:
        """Largest whole size, up to ``limit``, whose locked cash fits ``budget``."""
        lo, hi = 0, int(limit.to_integral_value(rounding=ROUND_FLOOR))
        while lo < hi:  # _locked only grows with size: binary search
            mid = (lo + hi + 1) // 2
            if self._locked(legs, Decimal(mid)) <= budget:
                lo = mid
            else:
                hi = mid - 1
        return Decimal(lo)

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
        before = {(q.ticker, q.side, q.price): q.size for q in wanted}
        for q in plan.creates:
            key = (q.ticker, q.side, q.price)
            if q.size < before.get(key, q.size):
                note = f"shrunk {before[key]:f} -> {q.size:f} to fit max_capital"
                plan.why[key] = f"{plan.why[key]}; {note}" if plan.why.get(key) else note
        binding = plan.creates != wanted
        if binding and not self._budget_binding:
            log.warning(
                "capital budget reached: $%.2f of $%.2f in use; new orders shrunk or skipped",
                in_use,
                max_capital,
            )
        self._budget_binding = binding

    async def _pull(self, orders: list[Order], reason: str) -> None:
        if orders:
            plan = Plan()
            plan.cancel(orders, reason)
            self._apply(await self.executor.execute(plan), plan)

    def _apply(self, report: ExecutionReport, plan: Plan) -> None:
        """Reflect REST results in live state right away (the stream confirms later)."""
        for order in report.gone:
            self.state.remove_order(order.order_id, order.ticker)
            self.journal.event(
                "order", action="cancel", reason=plan.reason_for_order(order), **_order_json(order)
            )
        for order in report.placed:
            self.state.upsert_order(order)
            why = plan.reason_for_quote(order.ticker, order.side, order.yes_price)
            self.journal.event("order", action="place", reason=why, **_order_json(order))
        for order in report.resized:
            self.state.upsert_order(order)
            self.journal.event(
                "order",
                action="decrease",
                reason=plan.reason_for_order(order),
                **_order_json(order),
            )
        for quote, filled in report.exited:
            self.journal.event(
                "order",
                action="exit",
                ticker=quote.ticker,
                side=quote.side.value,
                price=quote.price,
                size=quote.size,
                filled=filled,
                reason=plan.reason_for_quote(quote.ticker, quote.side, quote.price),
            )
        for quote, error in report.rejections:
            self.journal.event(
                "order",
                action="reject",
                ticker=quote.ticker,
                side=quote.side.value,
                price=quote.price,
                size=quote.size,
                error=error,
                reason=plan.reason_for_quote(quote.ticker, quote.side, quote.price),
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
            gap = self.settings.loop.min_reselect_gap_seconds
            asked = self._rescan_requested
            if asked or (self._reselect_soon and now - self._last_reselect >= gap):
                self._rescan_requested = self._reselect_soon = False
                last[self._reselect_job] = now  # counts as the scheduled one too
                log.info(
                    "re-ranking markets now (asked from the dashboard)"
                    if asked
                    else "a market was paused; re-ranking early to use its slot"
                )
                await self._run_job(self._reselect_job)
            for interval, job in jobs:
                if now - last[job] >= interval:
                    last[job] = now
                    await self._run_job(job)

    async def _run_job(self, job: Callable[[], Awaitable[None]]) -> None:
        """Run one maintenance job; a failure is logged and the loop carries on.

        An unexpected error (a bug, an odd API response) must not end the loop:
        that would silently stop every market scan, REST cross-check and
        exchange-status check for the rest of the run.
        """
        name = job.__name__.strip("_")
        try:
            await job()
        except TRANSIENT_ERRORS as exc:
            log.warning("%s failed: %s", name, exc)
        except Exception:
            log.exception("%s crashed; it runs again at its next interval", name)

    async def _check_status(self) -> None:
        status = await self.client.get_exchange_status()
        active = status.exchange_active and status.trading_active
        if active != self.trading_active:
            log.warning("exchange trading active: %s", active)
            self.trading_active = active
            if not active:
                async with self._lock:
                    await self._pull(self.state.our_orders(), "exchange trading is closed")
            self.state.changed.set()

    async def _reconcile(self) -> None:
        async with self._lock:
            await self.sync_from_rest()

    async def _refresh_queue(self) -> None:
        tickers = sorted({o.ticker for o in self.state.our_orders()})
        if tickers:
            self.state.queue_ahead = await self.client.get_queue_positions(tickers)

    async def _reselect_job(self) -> None:
        self._last_reselect = time.monotonic()
        scan = await self._scan()  # no lock: the bot keeps quoting during the scan
        async with self._lock:
            self._adopt(scan)
        await self.feed.set_tickers(self.markets)
        self.state.changed.set()

    async def _scanner_loop(self) -> None:
        """Every rewarded market's $/day at a fixed size, for the dashboard (never traded on)."""
        assert self.scanner is not None
        await self._sleep(SCANNER_FIRST_DELAY)
        while not self.stopping:
            trading = [t for t in self.markets if t not in self.reduce_only]
            try:
                self.journal.write_scan(await self.scanner.scan(trading))
            except TRANSIENT_ERRORS as exc:
                log.warning("market scanner failed: %s; retrying next interval", exc)
            except Exception:
                log.exception("market scanner crashed; retrying next interval")
            await self._sleep(self.settings.scanner.interval_seconds)

    async def _live_risk_loop(self) -> None:
        """Keep the fill risk of the orders actually resting up to date (for the dashboard)."""
        while not self.stopping:
            try:
                await self.refresh_live_fill_risk()
            except TRANSIENT_ERRORS as exc:
                log.warning("live fill risk failed: %s; retrying next interval", exc)
            except Exception:
                log.exception("live fill risk crashed; retrying next interval")
            await self._sleep(LIVE_FILL_RISK_SECONDS)

    async def refresh_live_fill_risk(self) -> None:
        """Replay recent trades against our resting orders: their real size and queue spot.

        Selection estimates the order it would post fresh, at the back of its queue; this is
        what's exposed now. Markets holding a position (an exit resting) are skipped: their
        orders close a position rather than open one.
        """
        cfg = self.settings.selection
        if not cfg.fill_risk:
            return
        now = time.time()
        live: dict[str, FillRisk] = {}
        for ticker in sorted({o.ticker for o in self.state.our_orders()}):
            if self.state.position(ticker) != 0:
                continue
            quotes = self._resting_quotes(ticker)
            if not quotes:
                continue
            trades, hours = await self.selector.recent_trades(ticker, now)
            live[ticker] = estimate_fill_risk(
                trades,
                quotes,
                window_hours=hours,
                sweep_window_seconds=cfg.sweep_window_seconds,
                fee_rate=cfg.taker_fee_rate,
                adverse_move=cfg.adverse_move,
                queue_factor=cfg.fill_risk_queue_factor,
            )
        self._live_risk = live

    def _resting_quotes(self, ticker: str) -> dict[Leg, PlannedQuote]:
        """Our resting orders per leg, as the fill replay sees them: all their contracts, at
        the front-most order's price and real place in the queue (others ahead of it)."""
        orders = self.state.our_orders(ticker)
        book = self.state.book(ticker)
        if not orders or book is None:
            return {}
        others = strip_own(book, own_orders_by_leg(orders, self.state.queue_ahead))
        quotes: dict[Leg, PlannedQuote] = {}
        for leg in Leg:
            on_leg = [o for o in orders if o.leg is leg]
            if not on_leg:
                continue
            front = max(on_leg, key=lambda o: o.leg_price)
            ahead = queue_report(front, book, others, self.state.queue_ahead, None)["ahead_total"]
            size = sum((o.remaining for o in on_leg), ZERO)
            quotes[leg] = PlannedQuote(front.leg_price, size, ahead if ahead is not None else ZERO)
        return quotes

    async def _payout_loop(self) -> None:
        """Find the reward payouts Kalshi actually made (see :mod:`engine.payouts`)."""
        while not self.stopping:
            try:
                if time.monotonic() - self._closed_at >= CLOSED_PROGRAMS_SECONDS:
                    await self.refresh_closed_programs()
                await self.check_payouts()
            except TRANSIENT_ERRORS as exc:
                log.warning("payout check failed: %s; retrying next interval", exc)
            except Exception:
                log.exception("payout check crashed; retrying next interval")
            await self._sleep(PAYOUT_CHECK_SECONDS)

    async def balance_history(self) -> list[dict[str, Any]]:
        """Every balance change since the journal's first balance reading, newest first.

        Cached for BALANCE_HISTORY_SECONDS: the dashboard may ask often.
        """
        now = time.monotonic()
        if (
            self._balance_cache is not None
            and now - self._balance_cache[0] < BALANCE_HISTORY_SECONDS
        ):
            return self._balance_cache[1]
        path = getattr(self.journal, "dir", None)
        samples = await asyncio.to_thread(_balance_samples, path / "metrics.jsonl") if path else []
        since = samples[0][0] if samples else time.time()
        records = {
            kind: await self.client.get_cash_records(
                kind, min_ts=since if kind in ("fills", "settlements") else None
            )
            for kind in ("fills", "settlements", "deposits", "withdrawals")
        }
        changes = balance_history.history(records, samples, since)
        self._balance_cache = (now, changes)
        return changes

    async def refresh_closed_programs(self) -> None:
        """Which ended program periods still await payout. An ended period not among them
        has been paid out by Kalshi (status "paid_out"), whether or not it paid us."""
        programs = await self.client.get_incentive_programs(status="closed")
        self._closed_periods = {f"{p.market_ticker}|{p.end.isoformat()}" for p in programs}
        self._closed_at = time.monotonic()

    async def check_payouts(self) -> None:
        balance = (await self.client.get_balance()).balance
        now = time.time()
        if self.payouts is None:
            # Baseline: the journal's first balance reading, so payouts since the first run count.
            first = self.journal.first_metrics()
            since, start = (
                (float(first["ts"]), Decimal(str(first["balance"])))
                if first and first.get("balance") is not None
                else (now, balance)
            )
            self.payouts = PayoutTracker(since, start, self.settings.selection.payout_minimum)
        payout = await self.payouts.refresh(
            self.client, balance, self.tracker.ended_periods(now), now
        )
        if payout is None:
            return
        log.info(
            "REWARD PAYOUT found: $%.4f (total paid $%.4f); matched to %s",
            payout.amount,
            self.payouts.paid,
            ", ".join(f"{k.rstrip('|')} ${v:.2f}" for k, v in payout.split.items()) or "nothing",
        )
        self.journal.event(
            "payout", amount=payout.amount, total=self.payouts.paid, split=payout.split
        )

    async def _report_rewards(self) -> None:
        if self.tracker.stats:
            for line in self.tracker.report_lines():
                log.info(line)

    # ---------------------------------------------------------------- controls
    # What the dashboard's buttons do (kalshi_lp.api -> engine.controls).

    async def hold(self) -> int:
        """Stop quoting until :meth:`release`: cancel every bot order now; returns how many.

        Positions still get closed (flatten_on_fill runs before the pause check).
        """
        if self.held_since is None:
            self.held_since = time.time()
        async with self._lock:
            orders = self.state.our_orders()
            await self._pull(orders, "paused from the dashboard")
        return len(orders)

    def release(self) -> None:
        """Quote again after :meth:`hold`, starting now."""
        self.held_since = None
        self.state.mark_dirty(self.markets)

    def request_rescan(self) -> None:
        """Re-rank markets within a second (the maintenance loop runs it)."""
        self._rescan_requested = True

    async def close_positions(self) -> tuple[int, dict[str, Decimal]]:
        """Close every position now, whatever flatten_on_fill says. See :meth:`flatten_all`."""
        async with self._lock:  # no quoting (or its own exits) in between
            return await self.flatten_all(force=True)

    async def set_max_capital(self, value: Decimal) -> Decimal | None:
        """Change the capital budget until the bot restarts; returns the old one.

        With risk.scale_with_budget, the limits sized from the budget (loss cap per side,
        order sizes, exposure, session loss, the exchange fill limit) move with it at once.
        Re-ranks at once (the budget decides how many markets get funded) and resizes every
        market's quotes on the next requote.
        """
        old = self.settings.risk.max_capital
        risk = self.settings.risk.model_copy(update={"max_capital": value})
        self._use_settings(budget_scaled(self.settings.model_copy(update={"risk": risk})))
        group, limit = self.executor.order_group_id, self.settings.risk.order_group_contracts_limit
        if group and not self.settings.dry_run and limit > 0:
            await self.client.update_order_group_limit(group, limit)
        self._budget_binding = False
        self.request_rescan()
        self.state.mark_dirty(self.markets)
        return old

    def _use_settings(self, settings: Settings) -> None:
        """Switch the running bot to ``settings`` (same structure, new numbers)."""
        self.settings = settings
        self.engine.cfg = settings.quoting
        self.engine.max_position = settings.risk.max_position_per_market
        self.selector.quoting = settings.quoting
        self.selector.max_capital = settings.risk.max_capital
        self.risk.cfg = settings.risk
        self._config_json = settings.model_dump(mode="json")

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
        paid_by_market = self.payouts.by_market() if self.payouts else {}
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
                    "pause_reason": self.risk.pause_reasons.get(ticker)
                    if self.risk.market_paused(ticker)
                    else None,
                    "pause_left": self.risk.pause_left(ticker),
                    "flattening": self.settings.risk.flatten_on_fill
                    and self.state.position(ticker) != 0,
                    "unwind": self._unwinding.get(ticker),  # passive exit resting now
                    "size": self._sizes.get(ticker),
                    "near_close": bool(market and self.risk.near_close(market)),
                    "healthy": self.state.is_healthy(ticker),
                    "book": _book_json(book),
                    "position": self.state.position(ticker),
                    "exposure": pos.market_exposure if pos else 0,
                    "realized_pnl": pos.realized_pnl if pos else 0,
                    "fees": pos.fees_paid if pos else 0,
                    "quotes": {leg.value: _leg_json(d) for leg, d in decisions.items()},
                    "event_ticker": market.event_ticker if market else None,
                    "reward": {
                        "per_day": params.reward_per_day if params else 0,
                        "target_size": params.target_size if params else None,
                        "discount_factor": params.discount_factor if params else None,
                        "period_reward": params.period_reward if params else None,
                        "period_start": params.period_start if params else None,
                        "period_end": params.period_end if params else None,
                        "max_reward_per_account": params.max_reward_per_account if params else None,
                    },
                    "competition": _competition_json(
                        competition(
                            strip_own(
                                book,
                                own_orders_by_leg(
                                    self.state.our_orders(ticker), self.state.queue_ahead
                                ),
                            ),
                            params,
                        )
                        if book is not None and params is not None
                        else None
                    ),
                    **(_fill_risk_json(self._selected[ticker]) if ticker in self._selected else {}),
                    **_live_risk_json(self._live_risk.get(ticker)),
                    "est_daily_reward": self._selected[ticker].est_daily_reward
                    if ticker in self._selected
                    else None,
                    "earned": stats.earned if stats else 0,
                    "paid": paid_by_market.get(ticker),
                    "periods": _periods_json(ticker, stats, self.payouts, self._closed_periods),
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
            "held_since": self.held_since,  # paused from the dashboard
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
                # Kalshi's actual payouts, from the balance (None until the first check).
                "rewards_paid": self.payouts.paid if self.payouts else None,
                "requotes": self.requotes,
                "fills": self.fills_total,
                "resting_orders": len(self.state.our_orders()),
            },
            "markets": markets,
            "orders": self._orders_with_queue(),
            "config": self._config_json,
            # Persisted for the next run (see RunJournal.previous).
            "ledger": self.tracker.ledger(self.titles),
            "rewards_unattributed": self.tracker.unattributed,
            "fills_total": self.fills_total,
            "entry_risk": self._entry_risk,
            "payouts": self.payouts.export() if self.payouts else None,
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


def _report_crash(task: asyncio.Task[None]) -> None:
    """Say so at once when a background loop dies (otherwise it only surfaces at shutdown)."""
    if not task.cancelled() and (exc := task.exception()) is not None:
        log.critical("the %s loop crashed and stopped: %r", task.get_name(), exc, exc_info=exc)


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


def _side_reasons(decisions: dict[Leg, LegDecision]) -> dict[Side, str]:
    """The strategy's reason per order side, e.g. "reward, share 1.3%" or "position limit"."""
    out = {}
    for leg, d in decisions.items():
        text = d.reason
        if d.quote is not None and d.score is not None:
            text += f", share {d.score.share:.1%}"
        out[leg.order_side] = text
    return out


def _fill_risk_json(c: Candidate) -> dict[str, Any]:
    risk = c.fill_risk
    return {
        "est_fills_per_day": risk.fills_per_day if risk else None,
        "est_fill_events_per_day": risk.hits_per_day if risk else None,
        "fill_cost_per_day": risk.cost_per_day if risk else None,
        "net_daily_reward": c.net_daily_reward if risk else None,
    }


def _live_risk_json(risk: FillRisk | None) -> dict[str, Any]:
    """Fill risk of the orders resting now (None: no resting orders, or not checked yet)."""
    return {
        "live_fills_per_day": risk.fills_per_day if risk else None,
        "live_fill_events_per_day": risk.hits_per_day if risk else None,
        "live_fill_cost_per_day": risk.cost_per_day if risk else None,
    }


def _fill_risk_text(c: Candidate) -> str:
    risk = c.fill_risk
    if risk is None:
        return ""
    if not risk.fills_per_day:
        return ", no sweeps would reach us"
    return (
        f", fills ~{risk.fills_per_day:.0f}/day cost ${risk.cost_per_day:.2f}"
        f" -> net ${c.net_daily_reward:.2f}/day"
    )


def _stake_text(c: Candidate, incumbents: Mapping[str, Decimal]) -> str:
    if c.ticker not in incumbents:
        return "  [new]"
    kept = f"  [kept; ${c.earned:.2f} earned"
    if c.unpaid_bonus:
        kept += f", +${c.unpaid_bonus:.2f}/day for unpaid earnings"
    return kept + "]"


def _competition_json(comp: Competition | None) -> dict[str, Any] | None:
    return None if comp is None else {"level": comp.level, "room": comp.room}


def _competition_text(comp: Competition | None) -> str:
    if comp is None:
        return "competition ?"
    room = "Target Size not reached" if comp.room is None else f"room {comp.room * 100:.0f}c"
    return f"competition {comp.level}, {room}"


def _balance_samples(path: Path) -> list[tuple[float, Decimal]]:
    """(time, balance) from the journal's totals history (metrics.jsonl), oldest first."""
    out = []
    with contextlib.suppress(OSError), path.open() as f:
        for line in f:
            with contextlib.suppress(ValueError, KeyError):
                e = json.loads(line)
                if e.get("balance") is not None:
                    out.append((float(e["ts"]), Decimal(str(e["balance"]))))
    return out


def _period_status(ticker: str, end: str, closed: set[str] | None) -> str | None:
    """Kalshi's status of one program period: running, awaiting payout, or paid out."""
    if not end:
        return None
    if datetime.fromisoformat(end).timestamp() > time.time():
        return "running"
    if closed is None:
        return None  # not fetched yet
    return "awaiting payout" if f"{ticker}|{end}" in closed else "paid out"


def _periods_json(
    ticker: str,
    stats: MarketRewardStats | None,
    payouts: PayoutTracker | None,
    closed: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Earned (estimate) and paid (matched payouts) per program period, newest first."""
    paid = payouts.period_paid if payouts else {}
    ends = set(stats.periods) if stats else set()
    ends |= {k.split("|", 1)[1] for k in paid if k.startswith(f"{ticker}|")}
    legacy = stats.earned - sum(stats.periods.values(), ZERO) if stats else ZERO
    if legacy > 0:
        ends.add("")
    return [
        {
            "end": end or None,  # None: earned before periods were tracked (period unknown)
            "earned": (stats.periods.get(end) if end else legacy) if stats else None,
            "paid": paid.get(f"{ticker}|{end}"),
            "status": _period_status(ticker, end, closed),
        }
        for end in sorted(ends, reverse=True)
    ]


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
        # Best BOOK_LEVELS of each bid ladder, [leg price, size], for the market popup.
        "yes_levels": [[lvl.price, lvl.size] for lvl in book.yes[:BOOK_LEVELS]],
        "no_levels": [[lvl.price, lvl.size] for lvl in book.no[:BOOK_LEVELS]],
    }
