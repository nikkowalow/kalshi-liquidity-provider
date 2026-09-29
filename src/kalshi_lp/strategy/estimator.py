"""Pre-trade reward estimates across live incentive programs.

For every active liquidity program, and for each candidate order size, we
take several samples of the order book. For each sample we place the quotes
the bot would actually post (same quote engine and settings), score them with
the program's rules at the back of the queue, and average the results into an
expected $/day.

Assumptions to keep in mind when reading the numbers:

* Other traders don't react to us. In practice competitors may add size and
  dilute our share.
* We join the back of the queue. Over time, queue position improves as orders
  ahead fill or cancel, so this errs low for a patient quoter.
* Fills are ignored. Rewards are only half the P&L; adverse selection on fills
  is the other half.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from kalshi_lp.config import QuotingConfig
from kalshi_lp.core.types import ZERO, Leg
from kalshi_lp.exchange.client import KalshiClient
from kalshi_lp.exchange.models import Market
from kalshi_lp.strategy.quoting import MarketContext, QuoteEngine
from kalshi_lp.strategy.rewards import RewardParams, expected_daily_reward
from kalshi_lp.strategy.selection import MarketSelector

log = logging.getLogger(__name__)

_UNLIMITED = Decimal(10**9)


@dataclass(slots=True)
class SizeEstimate:
    size: Decimal
    daily_sum: Decimal = ZERO
    share_sum: Decimal = ZERO  # average side share (0..1), summed over samples
    samples: int = 0
    bid: Decimal | None = None  # last quoted YES bid / ask
    ask: Decimal | None = None

    @property
    def daily(self) -> Decimal:
        return self.daily_sum / self.samples if self.samples else ZERO

    @property
    def share(self) -> Decimal:
        return self.share_sum / self.samples if self.samples else ZERO


@dataclass(slots=True)
class MarketEstimate:
    market: Market
    reward: RewardParams
    sizes: dict[Decimal, SizeEstimate] = field(default_factory=dict)
    quotable_samples: int = 0
    total_samples: int = 0

    @property
    def ticker(self) -> str:
        return self.market.ticker


class RewardEstimator:
    def __init__(self, client: KalshiClient, selector: MarketSelector, quoting: QuotingConfig):
        self.client = client
        self.selector = selector
        self.quoting = quoting

    async def estimate(
        self, sizes: Sequence[Decimal], *, samples: int = 3, interval: float = 2.0
    ) -> list[MarketEstimate]:
        programs = await self.client.get_incentive_programs()
        rewards = self.selector.reward_params(programs)
        log.info("%d active liquidity programs across %d markets", len(programs), len(rewards))
        if not rewards:
            return []

        markets = [
            m
            for m in await self.selector.fetch_markets(list(rewards))
            if self.selector.is_eligible(m)
        ]
        engines = {
            s: QuoteEngine(self.quoting.model_copy(update={"size": s}), _UNLIMITED) for s in sizes
        }
        results = {
            m.ticker: MarketEstimate(m, rewards[m.ticker], {s: SizeEstimate(s) for s in sizes})
            for m in markets
        }
        tickers = [m.ticker for m in markets]

        for i in range(samples):
            if i:
                await asyncio.sleep(interval)
            books = await self.client.get_orderbooks(tickers)
            for market in markets:
                result = results[market.ticker]
                result.total_samples += 1
                book = books.get(market.ticker)
                if book is None or not self.selector.book_is_quotable(book):
                    for est in result.sizes.values():
                        est.samples += 1  # counts as $0: the bot wouldn't quote it
                    continue
                result.quotable_samples += 1
                for size, engine in engines.items():
                    decisions = engine.quote(MarketContext(market, book, ZERO, result.reward))
                    yes, no = decisions[Leg.YES], decisions[Leg.NO]
                    est = result.sizes[size]
                    est.samples += 1
                    if yes.score and no.score:
                        est.daily_sum += expected_daily_reward(yes.score, no.score, result.reward)
                        est.share_sum += (yes.score.share + no.score.share) / 2
                    est.bid = yes.quote.price if yes.quote else None
                    est.ask = no.quote.price if no.quote else None

        ranked = sorted(results.values(), key=lambda r: r.sizes[sizes[0]].daily, reverse=True)
        return ranked
