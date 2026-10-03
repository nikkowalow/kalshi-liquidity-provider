"""Choose which markets to quote.

In ``incentives`` mode we pull every active liquidity program, simulate the
quotes we would post in each market, estimate our daily reward from the
program's scoring rules, and keep the best ``max_markets``. With
``fill_risk`` on, the best candidates are then re-ranked by reward minus the
expected cost of being filled, replayed from their recent public trades (see
:mod:`strategy.fill_risk`). ``volume`` mode (handy in demo, where programs may
not exist) takes the most-traded markets, and ``tickers`` mode quotes exactly
the configured list.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Protocol, TypeVar

import httpx

from kalshi_lp.config import QuotingConfig, SelectionConfig
from kalshi_lp.core.orderbook import Orderbook
from kalshi_lp.core.types import ZERO, Leg
from kalshi_lp.exchange.client import KalshiClient
from kalshi_lp.exchange.errors import KalshiError
from kalshi_lp.exchange.models import IncentiveProgram, Market, Trade
from kalshi_lp.strategy.fill_risk import FillRisk, PlannedQuote, depth_ahead, estimate_fill_risk
from kalshi_lp.strategy.quoting import MarketContext, QuoteEngine
from kalshi_lp.strategy.rewards import (
    Competition,
    RewardParams,
    competition,
    expected_daily_reward,
)

log = logging.getLogger(__name__)

_TICKERS_PER_MARKETS_CALL = 100


class _HasTicker(Protocol):
    @property
    def ticker(self) -> str: ...


_T = TypeVar("_T", bound=_HasTicker)


@dataclass(frozen=True, slots=True)
class Candidate:
    market: Market
    reward: RewardParams
    est_daily_reward: Decimal  # 0 when the market has no program
    rank_score: Decimal
    earned: Decimal = ZERO  # already earned here (incumbents), counts toward the payout
    competition: Competition | None = None
    quotes: Mapping[Leg, PlannedQuote] = field(default_factory=dict)  # what we'd post
    fill_risk: FillRisk | None = None  # set for the candidates whose trades were checked
    rank_multiplier: Decimal = Decimal(1)  # competition and incumbency factors
    unpaid_bonus: Decimal = ZERO  # $/day: what leaving would forfeit (see protect_unpaid)

    @property
    def pair_cost(self) -> Decimal:
        """Cash one contract on each leg locks (YES bid price + NO bid price)."""
        return sum((q.price for q in self.quotes.values()), ZERO) or Decimal(1)

    @property
    def size(self) -> Decimal:
        """Contracts per side the estimate assumes."""
        return max((q.size for q in self.quotes.values()), default=ZERO)

    @property
    def capital_needed(self) -> Decimal:
        """Cash the planned quotes lock: each leg's own size (after the loss cap) x its price.

        Not size x pair_cost: with max_loss_per_fill, the expensive leg is often
        far smaller than the cheap one (30 YES at 5c but 3 NO at 90c locks $4.20,
        not 30 x $0.95 = $28.50).
        """
        return sum((q.size * q.price for q in self.quotes.values()), ZERO)

    @property
    def fill_cost_per_day(self) -> Decimal:
        return self.fill_risk.cost_per_day if self.fill_risk else ZERO

    @property
    def net_daily_reward(self) -> Decimal:
        """Estimated reward minus the expected cost of getting filled."""
        return self.est_daily_reward - self.fill_cost_per_day

    @property
    def value(self) -> Decimal:
        """What the ranking compares, $/day: net reward plus what leaving would forfeit."""
        return self.net_daily_reward + self.unpaid_bonus

    @property
    def earning_days_left(self) -> Decimal:
        """Days we can still earn: until the program period ends or the market closes."""
        days = self.reward.days_left()
        to_close = self.market.seconds_to_close()
        if to_close is not None:
            days = min(days, Decimal(max(to_close, 0.0)) / 86_400)
        return days

    @property
    def est_period_payout(self) -> Decimal:
        """Projected payout for the program period: earned so far plus the rest at the estimate."""
        return self.earned + self.est_daily_reward * self.earning_days_left

    @property
    def ticker(self) -> str:
        return self.market.ticker


@dataclass(frozen=True, slots=True)
class Catalog:
    """Programs and their markets: slow-changing, so reused between scans."""

    rewards: dict[str, RewardParams]
    markets: list[Market]
    programs: int
    fetched_at: float


@dataclass(slots=True)
class _TradeHistory:
    trades: dict[str, Trade]  # by trade id
    covered_from: float  # unix seconds: complete from here on
    fetched_at: float


class MarketSelector:
    def __init__(
        self,
        client: KalshiClient,
        selection: SelectionConfig,
        quoting: QuotingConfig,
        engine: QuoteEngine,
        *,
        max_capital: Decimal | None = None,
    ):
        self.client = client
        self.cfg = selection
        self.quoting = quoting
        self.engine = engine
        self.max_capital = max_capital  # risk.max_capital: with auto_size, sets how many markets
        self._catalog: Catalog | None = None
        self._incumbents: dict[str, Decimal] = {}
        self._exclude: set[str] = set()
        self._trades: dict[str, _TradeHistory] = {}

    # ------------------------------------------------------------- rewards

    def default_reward(self) -> RewardParams:
        return RewardParams(
            target_size=self.quoting.default_target_size,
            discount_factor=self.quoting.default_discount_factor,
            full_credit_fraction=self.quoting.full_credit_fraction,
        )

    def reward_params(self, programs: Iterable[IncentiveProgram]) -> dict[str, RewardParams]:
        """Merge programs per market: rewards add up; scoring uses the richest program."""
        by_ticker: dict[str, list[IncentiveProgram]] = {}
        for p in programs:
            if p.incentive_type == "liquidity" and p.is_active() and not p.paid_out:
                by_ticker.setdefault(p.market_ticker, []).append(p)
        params = {}
        for ticker, progs in by_ticker.items():
            main = max(progs, key=lambda p: p.reward_per_day)
            params[ticker] = RewardParams(
                target_size=main.target_size or self.quoting.default_target_size,
                discount_factor=main.discount_factor or self.quoting.default_discount_factor,
                full_credit_fraction=self.quoting.full_credit_fraction,
                reward_per_day=sum((p.reward_per_day for p in progs), ZERO),
                period_end=main.end,
                period_start=main.start,
                period_reward=sum((p.period_reward for p in progs), ZERO),
            )
        return params

    # ----------------------------------------------------------- filtering

    def is_eligible(self, market: Market) -> bool:
        if not market.is_tradable or market.market_type != "binary":
            return False
        if market.ticker in self.cfg.exclude_tickers or market.ticker in self._exclude:
            return False
        if series_of(market.ticker) in self.cfg.exclude_series:
            return False
        to_close = market.seconds_to_close()
        return to_close is None or to_close >= self.cfg.min_seconds_to_close

    def book_is_quotable(self, book: Orderbook) -> bool:
        """Two-sided, tight enough to trust the mid, and away from the tails."""
        bid, ask, mid = book.best_yes_bid, book.best_yes_ask, book.mid
        if bid is None or ask is None or mid is None:
            return False
        return (
            ask - bid <= self.cfg.max_spread
            and self.cfg.min_mid_price <= mid <= self.cfg.max_mid_price
        )

    @property
    def auto_sizing(self) -> bool:
        return self.quoting.auto_size and self.max_capital is not None

    @property
    def planned_size(self) -> Decimal:
        """Contracts per side the bot will quote in a selected market. With auto_size the
        capital goes to the best markets at full size, so that's the size to estimate at."""
        if self.auto_sizing:
            return min(self.quoting.max_size or self.engine.max_position, self.engine.max_position)
        return self.quoting.size

    def estimate(self, market: Market, book: Orderbook, reward: RewardParams) -> Decimal:
        """Estimated daily reward if we posted our standard quotes into ``book`` now."""
        return self.plan(market, book, reward)[0]

    def plan(
        self, market: Market, book: Orderbook, reward: RewardParams
    ) -> tuple[Decimal, dict[Leg, PlannedQuote]]:
        """The quotes we'd post into ``book`` now, and their estimated daily reward."""
        if reward.reward_per_day <= 0:
            return ZERO, {}
        decisions = self.engine.quote(
            MarketContext(market, book, ZERO, reward, size=self.planned_size)
        )
        quotes = {
            leg: PlannedQuote(d.leg_price, d.quote.size, depth_ahead(book.bids(leg), d.leg_price))
            for leg, d in decisions.items()
            if d.quote is not None and d.leg_price is not None
        }
        yes, no = decisions[Leg.YES].score, decisions[Leg.NO].score
        if yes is None or no is None:
            return ZERO, quotes
        return expected_daily_reward(yes, no, reward), quotes

    # ------------------------------------------------------------ selection

    async def fetch_markets(self, tickers: Sequence[str]) -> list[Market]:
        markets: list[Market] = []
        for i in range(0, len(tickers), _TICKERS_PER_MARKETS_CALL):
            chunk = tickers[i : i + _TICKERS_PER_MARKETS_CALL]
            markets += await self.client.get_markets(tickers=chunk, exclude_multivariate=False)
        return markets

    async def select(
        self,
        incumbents: Mapping[str, Decimal] | None = None,
        exclude: Iterable[str] = (),
        keep: Iterable[str] = (),
    ) -> list[Candidate]:
        """Pick markets. ``incumbents`` maps markets we already hold a stake in (quoting now,
        or rewards earned) to the rewards earned there; they get ``incumbent_bonus``.
        ``exclude`` sits markets out of this round (e.g. paused ones). ``keep`` (markets
        within min_hold_seconds of being selected) keep their slots, first in line for
        capital, as long as they still pass the filters."""
        self._incumbents = dict(incumbents or {})
        self._exclude = set(exclude)
        catalog = await self.catalog()
        rewards = catalog.rewards

        if self.cfg.mode == "tickers":
            return await self._rank(
                await self.fetch_markets(self.cfg.tickers), rewards, by_reward=False
            )

        if self.cfg.mode == "incentives" and rewards:
            ranked = await self._rank(catalog.markets, rewards, by_reward=True, limit=None)
            worth_it = self._explain_and_filter(ranked)
            held = set(keep)
            worth_it.sort(key=lambda c: c.ticker not in held)  # stable: rank order otherwise
            if self.cfg.fill_risk and worth_it:
                # Held markets go first so they're always checked, however they rank.
                worth_it = await self._apply_fill_risk(worth_it)
                worth_it.sort(key=lambda c: c.ticker not in held)
            if worth_it or not self.cfg.fallback_to_volume:
                return self._fit_capital(self._diversify(worth_it, self.cfg.max_markets))
            log.warning("no incentive market passed filters; falling back to volume ranking")
        elif self.cfg.mode == "incentives":
            if not self.cfg.fallback_to_volume:
                return []
            log.warning("no active liquidity programs; falling back to volume ranking")

        markets = await self.client.get_markets(status="open", max_items=self.cfg.candidate_pool)
        return await self._rank(markets, rewards, by_reward=False)

    async def _rank(
        self,
        markets: Sequence[Market],
        rewards: dict[str, RewardParams],
        *,
        by_reward: bool,
        limit: int | None = -1,
    ) -> list[Candidate]:
        """Rank quotable markets. ``limit=-1`` means max_markets; None means no limit."""
        if limit == -1:
            limit = self.cfg.max_markets
        eligible = [m for m in markets if self.is_eligible(m)]
        if not by_reward:
            # Pre-filter on the market summaries before spending order book reads.
            eligible = [
                m for m in eligible if (s := m.spread) is not None and s <= self.cfg.max_spread
            ]
            eligible.sort(key=lambda m: m.volume_24h, reverse=True)
            eligible = self._diversify(eligible, self.cfg.max_markets * 4)
        books = await self.client.get_orderbooks([m.ticker for m in eligible]) if eligible else {}

        candidates = []
        for market in eligible:
            book = books.get(market.ticker)
            if book is None or not self.book_is_quotable(book):
                continue
            reward = rewards.get(market.ticker, self.default_reward())
            est, quotes = self.plan(market, book, reward)
            earned = self._incumbents.get(market.ticker)
            comp = competition(book, reward)
            multiplier = Decimal(1)
            if by_reward:
                multiplier *= self.competition_factor(comp)
                if earned is not None:
                    multiplier *= 1 + self.cfg.incumbent_bonus
            c = Candidate(market, reward, est, ZERO, earned or ZERO, comp, quotes, None, multiplier)
            c = replace(c, unpaid_bonus=self._unpaid_bonus(c) if earned is not None else ZERO)
            candidates.append(
                replace(c, rank_score=c.value * multiplier if by_reward else market.volume_24h)
            )
        candidates.sort(key=lambda c: c.rank_score, reverse=True)
        if limit is None:
            # Unlimited: the caller filters and re-ranks, then diversifies at the end, so a
            # second market in a series still gets its chance (e.g. a safer one).
            return candidates
        return self._diversify(candidates, limit)

    def competition_factor(self, comp: Competition) -> Decimal:
        w = self.cfg.competition_weight
        return {"low": 1 + w, "high": 1 - w}.get(comp.level, Decimal(1))

    async def _apply_fill_risk(self, ranked: list[Candidate]) -> list[Candidate]:
        """Re-rank the best candidates by reward minus expected fill cost; drop the losers.

        Checks the top ``fill_risk_pool`` (more if too few survive to fill
        ``max_markets``, up to twice that). Only checked markets are returned.
        """
        cfg = self.cfg
        now = time.time()
        stale = now - cfg.trade_lookback_hours * 3600
        self._trades = {t: h for t, h in self._trades.items() if h.fetched_at >= stale}
        kept: list[Candidate] = []
        dropped: list[Candidate] = []
        too_busy: list[Candidate] = []
        too_costly: list[Candidate] = []
        checked = 0
        for c in ranked:
            enough = len(self._diversify(kept, cfg.max_markets)) >= cfg.max_markets
            if checked >= 2 * cfg.fill_risk_pool or (checked >= cfg.fill_risk_pool and enough):
                break
            checked += 1
            try:
                trades, hours = await self.recent_trades(c.ticker, now)
            except (KalshiError, httpx.HTTPError) as exc:
                if cfg.max_fills_per_day is not None:
                    log.warning("no trade history for %s (%s); skipping it", c.ticker, exc)
                    too_busy.append(c)
                    continue
                log.warning(
                    "no trade history for %s (%s); ranking it without fill risk", c.ticker, exc
                )
                kept.append(c)
                continue
            risk = estimate_fill_risk(
                trades,
                c.quotes,
                window_hours=hours,
                sweep_window_seconds=cfg.sweep_window_seconds,
                fee_rate=cfg.taker_fee_rate,
                adverse_move=cfg.adverse_move,
                queue_factor=cfg.fill_risk_queue_factor,
            )
            c = replace(c, fill_risk=risk)
            c = replace(c, rank_score=c.value * c.rank_multiplier)
            if cfg.max_fills_per_day is not None and risk.fills_per_day > cfg.max_fills_per_day:
                too_busy.append(c)
            elif (
                cfg.max_fill_cost_share is not None
                and c.fill_cost_per_day > c.est_daily_reward * cfg.max_fill_cost_share
            ):
                too_costly.append(c)
            elif c.net_daily_reward <= cfg.min_net_daily_reward:
                dropped.append(c)
            else:
                kept.append(c)
        kept.sort(key=lambda c: c.rank_score, reverse=True)
        log.info(
            "%d of %d checked markets still pay after expected fill costs "
            "(replaying up to %gh of trades)",
            len(kept),
            checked,
            cfg.trade_lookback_hours,
        )
        if dropped:
            log.info("  skipped %4d: fills would cost more than the rewards", len(dropped))
        if too_busy:
            log.info(
                "  skipped %4d: over %s expected fills/day (or no trade history)",
                len(too_busy),
                cfg.max_fills_per_day,
            )
        if too_costly and cfg.max_fill_cost_share is not None:
            log.info(
                "  skipped %4d: fills would eat over %s%% of the rewards",
                len(too_costly),
                f"{(cfg.max_fill_cost_share * Decimal(100)).normalize():f}",
            )
        risky = sorted(
            (
                c
                for c in (*kept, *dropped, *too_busy, *too_costly)
                if c.fill_risk and c.fill_risk.fills_per_day > 0
            ),
            key=lambda c: c.fill_cost_per_day,
            reverse=True,
        )
        for c in risky[:5]:
            assert c.fill_risk is not None
            log.info(
                "  fill risk: %-44s est $%.2f/day, ~%.0f fills/day costing $%.2f/day "
                "-> net $%.2f/day",
                c.ticker,
                c.est_daily_reward,
                c.fill_risk.fills_per_day,
                c.fill_cost_per_day,
                c.net_daily_reward,
            )
        return kept

    def _unpaid_bonus(self, c: Candidate) -> Decimal:
        """$/day of earnings leaving ``c`` would forfeit (protect_unpaid), else 0.

        Kalshi pays nothing below payout_minimum, so an incumbent that has earned
        some but not enough loses it all if we leave. That's worth protecting only
        if staying would reach the minimum; it is spread over the remaining time so
        it compares with $/day rates.
        """
        minimum = self.cfg.payout_minimum
        if not self.cfg.protect_unpaid or not (ZERO < c.earned < minimum):
            return ZERO
        if c.est_period_payout < minimum:
            return ZERO  # lost either way
        return c.earned / max(c.earning_days_left, Decimal(1) / 24)

    def _fit_capital(self, ranked: list[Candidate]) -> list[Candidate]:
        """With auto_size: the best markets at full size until the capital runs out.

        Rewards scale with order size, so spreading the budget thin over more
        markets only averages in worse ones. A last market that gets a partial
        size is kept only if its (proportionally smaller) payout still passes
        min_period_payout.
        """
        if not self.auto_sizing or self.max_capital is None:
            return ranked
        budget = self.max_capital * self.quoting.capital_utilization
        chosen: list[Candidate] = []
        for c in ranked:
            need = c.capital_needed
            if need <= 0:
                continue
            if need <= budget:
                chosen.append(c)
                budget -= need
                continue
            fraction = budget / need
            partial = c.earned + c.est_daily_reward * fraction * c.earning_days_left
            if budget > 0 and partial >= self._payout_bar(c):
                chosen.append(c)
            break
        if len(chosen) < len(ranked):
            log.info(
                "capital: $%s funds %d market(s) at up to %s contracts per side; "
                "%d lower-ranked left out",
                self.max_capital,
                len(chosen),
                self.planned_size,
                len(ranked) - len(chosen),
            )
        return chosen

    async def catalog(self) -> Catalog:
        """Programs and (in incentives mode) their markets, refreshed every
        catalog_refresh_seconds; order books are fetched fresh by every scan."""
        now = time.time()
        cached = self._catalog
        if cached is not None and now - cached.fetched_at < self.cfg.catalog_refresh_seconds:
            return cached
        programs = await self.client.get_incentive_programs()
        rewards = self.reward_params(programs)
        markets = (
            await self.fetch_markets(list(rewards))
            if self.cfg.mode == "incentives" and rewards
            else []
        )
        log.info("active liquidity programs: %d across %d markets", len(programs), len(rewards))
        self._catalog = Catalog(rewards, markets, len(programs), now)
        return self._catalog

    def keep_trades_for(self, tickers: Iterable[str]) -> None:
        """Drop cached trade history for every market not in ``tickers``."""
        keep = set(tickers)
        self._trades = {t: h for t, h in self._trades.items() if t in keep}

    async def recent_trades(self, ticker: str, now: float) -> tuple[list[Trade], float]:
        """The last ``trade_lookback_hours`` of trades (cached, fetched incrementally),
        and how many hours of history they actually cover."""
        cfg = self.cfg
        since = now - cfg.trade_lookback_hours * 3600
        history = self._trades.get(ticker)
        if history is None or history.fetched_at < since:
            history = _TradeHistory({}, since, now)
            fetch_from = since
        else:
            fetch_from = history.fetched_at - 5  # small overlap; duplicates merge by id
        new = await self.client.get_trades(
            ticker, min_ts=fetch_from, max_items=cfg.max_trades_per_market
        )
        if len(new) >= cfg.max_trades_per_market:
            # Truncated: complete only back to the oldest trade returned, and there may be
            # a gap before it, so start over from this batch.
            history = _TradeHistory({}, min(t.ts.timestamp() for t in new), now)
        history.trades.update({t.trade_id or f"{t.ts.isoformat()}|{t.count}": t for t in new})
        history.fetched_at = now
        history.covered_from = max(history.covered_from, since)
        history.trades = {
            k: t for k, t in history.trades.items() if t.ts.timestamp() >= history.covered_from
        }
        self._trades[ticker] = history
        return list(history.trades.values()), (now - history.covered_from) / 3600

    def _explain_and_filter(self, ranked: list[Candidate]) -> list[Candidate]:
        """Apply the payout filters, logging why markets were skipped and the near misses."""
        kept: list[Candidate] = []
        reasons: dict[str, int] = {}
        misses: list[Candidate] = []
        for c in ranked:
            if c.est_daily_reward <= 0:
                reason = "earns nothing at our size/price (thin book, deep queue, or no cushion)"
            elif c.est_daily_reward < self.cfg.min_daily_reward:
                reason = f"under ${self.cfg.min_daily_reward}/day"
            elif c.est_period_payout < self._payout_bar(c):
                reason = f"under ${self.cfg.min_period_payout} before the period ends/market closes"
            else:
                kept.append(c)
                continue
            reasons[reason] = reasons.get(reason, 0) + 1
            if c.est_daily_reward > 0:
                misses.append(c)
        log.info(
            "%d of %d quotable rewarded markets pass the payout filters", len(kept), len(ranked)
        )
        for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            log.info("  skipped %4d: %s", n, reason)
        for c in sorted(misses, key=lambda c: c.est_period_payout, reverse=True)[:5]:
            log.info(
                "  near miss: %-44s est $%.2f/day x %.2f days = $%.2f",
                c.ticker,
                c.est_daily_reward,
                c.earning_days_left,
                c.est_period_payout,
            )
        return kept

    def _payout_bar(self, c: Candidate) -> Decimal:
        """Projected payout ``c`` needs: newcomers min_period_payout, incumbents the minimum."""
        if c.ticker in self._incumbents:
            return min(self.cfg.min_period_payout, self.cfg.payout_minimum)
        return self.cfg.min_period_payout

    def _diversify(self, ranked: Sequence[_T], limit: int) -> list[_T]:
        """Take the best-ranked items, at most ``max_per_series`` per series."""
        per_series: dict[str, int] = {}
        chosen: list[_T] = []
        for item in ranked:
            series = series_of(item.ticker)
            if per_series.get(series, 0) < self.cfg.max_per_series:
                per_series[series] = per_series.get(series, 0) + 1
                chosen.append(item)
                if len(chosen) >= limit:
                    break
        return chosen


def series_of(ticker: str) -> str:
    """Series prefix of a market ticker, e.g. ``KXRAINSHARD2-26SEP30-DAL -> KXRAINSHARD2``."""
    return ticker.split("-", 1)[0]
