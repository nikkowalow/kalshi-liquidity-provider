"""Market scanner: every rewarded market's estimated $/day, for the dashboard.

Read-only research. Every ``scanner.interval_seconds`` it estimates what
``scanner.size`` contracts per side (10 by default) would earn in each market
with an active liquidity program, quoted the bot's way (same placement,
offset, cushion and Target Size rules; no loss cap, so every market is
compared at the same size), and reports the best ``scanner.top``. It never
places orders and never changes what the bot trades: the bot's own selection
runs separately, at its own size and with its own filters.

Each reported market carries the program, the book, where the quotes would
rest and their reward share, the estimated $/day and $/h, the cash those
quotes would lock and the $/day per dollar locked, the projected payout for
the program period, the expected cost of fills replayed from recent public
trades (net $/day), and whether the bot trades it now or why its filters
would skip it.

The estimates share the selection model's assumptions: the book as it is now
persists, others don't react, and a new order joins the back of its level.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Collection
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from kalshi_lp.config import ScannerConfig
from kalshi_lp.core.orderbook import Orderbook
from kalshi_lp.core.types import ZERO, Leg
from kalshi_lp.exchange.client import KalshiClient
from kalshi_lp.exchange.errors import KalshiError
from kalshi_lp.exchange.models import Market
from kalshi_lp.strategy.fill_risk import FillRisk, PlannedQuote, depth_ahead, estimate_fill_risk
from kalshi_lp.strategy.quoting import LegDecision, MarketContext, QuoteEngine
from kalshi_lp.strategy.rewards import RewardParams, competition, expected_daily_reward
from kalshi_lp.strategy.selection import MarketSelector, series_of

log = logging.getLogger(__name__)

_UNLIMITED = Decimal(10**9)  # no position limit: every market is quoted at the full size


class MarketScanner:
    def __init__(self, client: KalshiClient, selector: MarketSelector, cfg: ScannerConfig):
        self.client = client
        self.selector = selector  # the bot's: its program catalog (shared cache) and filters
        self.cfg = cfg
        quoting = selector.quoting.model_copy(
            update={"size": cfg.size, "auto_size": False, "max_loss_per_fill": None}
        )
        self.engine = QuoteEngine(quoting, _UNLIMITED)
        # Its own selector only for the trade-history cache: the bot's is never touched.
        self._history = MarketSelector(client, selector.cfg, quoting, self.engine)

    async def scan(self, trading: Collection[str] = ()) -> dict[str, Any]:
        """Estimate every rewarded market at the scan size; report the top ones, best first."""
        started = time.time()
        catalog = await self.selector.catalog()
        markets = [
            m
            for m in catalog.markets
            if m.ticker in catalog.rewards and m.is_tradable and m.market_type == "binary"
        ]
        books = await self.client.get_orderbooks([m.ticker for m in markets]) if markets else {}

        earning: list[tuple[Decimal, Market, Orderbook, RewardParams, dict[Leg, LegDecision]]] = []
        for market in markets:
            book = books.get(market.ticker)
            reward = catalog.rewards[market.ticker]
            if book is None or reward.reward_per_day <= 0:
                continue
            decisions = self.engine.quote(MarketContext(market, book, ZERO, reward))
            yes, no = decisions[Leg.YES].score, decisions[Leg.NO].score
            est = expected_daily_reward(yes, no, reward) if yes and no else ZERO
            if est > 0:
                earning.append((est, market, book, reward, decisions))
        earning.sort(key=lambda row: row[0], reverse=True)
        top = earning[: self.cfg.top]

        now = time.time()
        if self.cfg.fill_risk:
            self._history.keep_trades_for(market.ticker for _, market, *_ in top)
        rows = []
        for rank, (est, market, book, reward, decisions) in enumerate(top, 1):
            quotes = _planned(book, decisions)
            risk = await self._fill_risk(market.ticker, quotes, now) if self.cfg.fill_risk else None
            row = self._row(rank, est, market, book, reward, decisions, quotes, risk, trading)
            rows.append(row)
        report = {
            "scanned_at": started,
            "seconds": round(time.time() - started, 1),
            "size": self.cfg.size,
            "interval": self.cfg.interval_seconds,
            "programs": catalog.programs,
            "markets": len(markets),
            "earning": len(earning),
            "rows": rows,
        }
        log.info(
            "scanner: %d of %d rewarded markets earn at %s contracts/side; best $%.2f/day (%s)",
            len(earning),
            len(markets),
            f"{self.cfg.size.normalize():f}",
            rows[0]["est_daily"] if rows else 0,
            rows[0]["ticker"] if rows else "-",
        )
        return report

    async def _fill_risk(
        self, ticker: str, quotes: dict[Leg, PlannedQuote], now: float
    ) -> FillRisk | None:
        cfg = self.selector.cfg
        try:
            trades, hours = await self._history.recent_trades(ticker, now)
        except (KalshiError, httpx.HTTPError) as exc:
            log.debug("scanner: no trade history for %s (%s)", ticker, exc)
            return None
        finally:
            await asyncio.sleep(self.cfg.pace_seconds)  # leave the read budget to trading
        return estimate_fill_risk(
            trades,
            quotes,
            window_hours=hours,
            sweep_window_seconds=cfg.sweep_window_seconds,
            fee_rate=cfg.taker_fee_rate,
            adverse_move=cfg.adverse_move,
            queue_factor=cfg.fill_risk_queue_factor,
        )

    def _row(
        self,
        rank: int,
        est: Decimal,
        market: Market,
        book: Orderbook,
        reward: RewardParams,
        decisions: dict[Leg, LegDecision],
        quotes: dict[Leg, PlannedQuote],
        risk: FillRisk | None,
        trading: Collection[str],
    ) -> dict[str, Any]:
        yes, no = decisions[Leg.YES], decisions[Leg.NO]
        capital = sum((q.size * q.price for q in quotes.values()), ZERO)
        days = _earning_days_left(market, reward)
        cost = risk.cost_per_day if risk else None
        bid, ask = book.best_yes_bid, book.best_yes_ask
        comp = competition(book, reward)
        return {
            "rank": rank,
            "ticker": market.ticker,
            "title": market.title,
            "close_time": market.close_time,
            "program_per_day": reward.reward_per_day,
            "target_size": reward.target_size,
            "bid": bid,
            "ask": ask,
            "spread": ask - bid if bid is not None and ask is not None else None,
            "yes_price": yes.leg_price if yes.quote else None,
            "no_price": no.leg_price if no.quote else None,
            "yes_share": yes.score.share if yes.score else None,
            "no_share": no.score.share if no.score else None,
            "est_daily": est,
            "est_hourly": est / 24,
            "capital": capital,
            "return_daily": est / capital if capital > 0 else None,
            "days_left": days,
            "period_payout": est * days,
            "competition": comp.level,
            "competition_room": comp.room,
            "fills_per_day": risk.fills_per_day if risk else None,
            "fill_cost_per_day": cost,
            "net_daily": est - cost if cost is not None else None,
            "trading": market.ticker in trading,
            "skip": self._skip_reason(market, book, est * days),
        }

    def _skip_reason(self, market: Market, book: Orderbook, payout: Decimal) -> str | None:
        """Why the bot's own selection would pass on this market, whatever its size."""
        cfg = self.selector.cfg
        if market.ticker in cfg.exclude_tickers:
            return "excluded ticker"
        if series_of(market.ticker) in cfg.exclude_series:
            return "excluded series"
        to_close = market.seconds_to_resolve()
        if to_close is not None and to_close < cfg.min_seconds_to_close:
            return f"closes within {cfg.min_seconds_to_close / 3600:g}h"
        if not self.selector.book_is_quotable(book):
            return "spread or price outside the filters"
        if payout < cfg.payout_minimum:
            return f"pays under ${cfg.payout_minimum} this period"
        return None


def _planned(book: Orderbook, decisions: dict[Leg, LegDecision]) -> dict[Leg, PlannedQuote]:
    return {
        leg: PlannedQuote(d.leg_price, d.quote.size, depth_ahead(book.bids(leg), d.leg_price))
        for leg, d in decisions.items()
        if d.quote is not None and d.leg_price is not None
    }


def _earning_days_left(market: Market, reward: RewardParams) -> Decimal:
    """Days left to earn: until the program period ends or the market closes."""
    days = reward.days_left(datetime.now(UTC))
    to_close = market.seconds_to_close()
    if to_close is not None:
        days = min(days, Decimal(max(to_close, 0.0)) / 86_400)
    return days
