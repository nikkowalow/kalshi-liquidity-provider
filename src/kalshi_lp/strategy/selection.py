"""Choose which markets to quote.

In ``incentives`` mode we pull every active liquidity program, simulate the
quotes we would post in each market, estimate our daily reward from the
program's scoring rules, and keep the best ``max_markets``. ``volume`` mode
(handy in demo, where programs may not exist) takes the most-traded markets,
and ``tickers`` mode quotes exactly the configured list.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, TypeVar

from kalshi_lp.config import QuotingConfig, SelectionConfig
from kalshi_lp.core.orderbook import Orderbook
from kalshi_lp.core.types import ZERO, Leg
from kalshi_lp.exchange.client import KalshiClient
from kalshi_lp.exchange.models import IncentiveProgram, Market
from kalshi_lp.strategy.quoting import MarketContext, QuoteEngine
from kalshi_lp.strategy.rewards import RewardParams, expected_daily_reward

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

    @property
    def ticker(self) -> str:
        return self.market.ticker


class MarketSelector:
    def __init__(
        self,
        client: KalshiClient,
        selection: SelectionConfig,
        quoting: QuotingConfig,
        engine: QuoteEngine,
    ):
        self.client = client
        self.cfg = selection
        self.quoting = quoting
        self.engine = engine

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
            )
        return params

    # ----------------------------------------------------------- filtering

    def is_eligible(self, market: Market) -> bool:
        if not market.is_tradable or market.market_type != "binary":
            return False
        if market.ticker in self.cfg.exclude_tickers:
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

    def estimate(self, market: Market, book: Orderbook, reward: RewardParams) -> Decimal:
        """Estimated daily reward if we posted our standard quotes into ``book`` now."""
        if reward.reward_per_day <= 0:
            return ZERO
        decisions = self.engine.quote(MarketContext(market, book, ZERO, reward))
        yes, no = decisions[Leg.YES].score, decisions[Leg.NO].score
        if yes is None or no is None:
            return ZERO
        return expected_daily_reward(yes, no, reward)

    # ------------------------------------------------------------ selection

    async def _markets(self, tickers: Sequence[str]) -> list[Market]:
        markets: list[Market] = []
        for i in range(0, len(tickers), _TICKERS_PER_MARKETS_CALL):
            chunk = tickers[i : i + _TICKERS_PER_MARKETS_CALL]
            markets += await self.client.get_markets(tickers=chunk, exclude_multivariate=False)
        return markets

    async def select(self) -> list[Candidate]:
        programs = await self.client.get_incentive_programs()
        rewards = self.reward_params(programs)
        log.info("active liquidity programs: %d across %d markets", len(programs), len(rewards))

        if self.cfg.mode == "tickers":
            return await self._rank(await self._markets(self.cfg.tickers), rewards, by_reward=False)

        if self.cfg.mode == "incentives" and rewards:
            ranked = await self._rank(await self._markets(list(rewards)), rewards, by_reward=True)
            ranked = [c for c in ranked if c.est_daily_reward >= self.cfg.min_daily_reward]
            if ranked or not self.cfg.fallback_to_volume:
                return ranked[: self.cfg.max_markets]
            log.warning("no incentive market passed filters; falling back to volume ranking")
        elif self.cfg.mode == "incentives":
            if not self.cfg.fallback_to_volume:
                return []
            log.warning("no active liquidity programs; falling back to volume ranking")

        markets = await self.client.get_markets(status="open", max_items=self.cfg.candidate_pool)
        return await self._rank(markets, rewards, by_reward=False)

    async def _rank(
        self, markets: Sequence[Market], rewards: dict[str, RewardParams], *, by_reward: bool
    ) -> list[Candidate]:
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
            est = self.estimate(market, book, reward)
            rank = est if by_reward else market.volume_24h
            candidates.append(Candidate(market, reward, est, rank))
        candidates.sort(key=lambda c: c.rank_score, reverse=True)
        return self._diversify(candidates, self.cfg.max_markets)

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
