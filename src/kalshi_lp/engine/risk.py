"""Risk controls.

Layers, from local to global:

* **Per-market position limit**: enforced in quote sizing.
* **Fill-burst breaker**: if a market fills more than ``fill_burst_contracts``
  inside the window, we are probably being picked off by informed flow, so
  that market pauses for ``cooldown_seconds``.
* **Close buffer**: stop quoting shortly before a market closes, when news
  risk is highest.
* **Exposure / balance gates**: past ``max_total_exposure`` or below
  ``min_balance``, only position-reducing quotes are allowed.
* **Error breaker**: after ``max_consecutive_errors`` failed cycles, pull all
  quotes and pause globally.
* **Session loss limit**: once mark-to-market P&L since startup falls below
  ``-max_session_loss``, halt for good (requires a restart).
* **Exchange order group** (set up by the bot): Kalshi itself cancels all our
  orders if too many contracts match within 15 seconds.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from kalshi_lp.config import RiskConfig
from kalshi_lp.core.types import ONE, ZERO
from kalshi_lp.exchange.models import Market, Position

log = logging.getLogger(__name__)


# (best YES bid, best YES ask); either side may be missing. A bare Decimal means a mid.
Mark = tuple[Decimal | None, Decimal | None] | Decimal


def mark_value(position: Decimal, mark: Mark) -> Decimal:
    """What a position is worth at ``mark``.

    With both sides of the book, value at the mid. With a side missing, use
    what the position could actually be sold for: a long YES needs a YES bid
    to sell into and a long NO needs a YES ask. If that side is empty, the
    position is marked at zero. After a crash empties the bid side, the loss
    shows in full instead of hiding behind the old price.
    """
    bid: Decimal | None
    ask: Decimal | None
    if isinstance(mark, Decimal):
        bid = ask = mark
    else:
        bid, ask = mark
    if bid is not None and ask is not None:
        yes = (bid + ask) / 2
        return position * yes if position > 0 else -position * (ONE - yes)
    if position > 0:
        return position * bid if bid is not None else ZERO
    return -position * (ONE - ask) if ask is not None else ZERO


def position_pnl(pos: Position, mark: Mark | None) -> Decimal:
    """Lifetime P&L in a market: realized minus fees plus unrealized at ``mark``.

    With no mark at all (never seen the book), the position is valued at cost.
    Fees are subtracted even if Kalshi's realized figure already nets them,
    which errs toward reporting a larger loss.
    """
    if mark is None or pos.position == 0:
        value = pos.market_exposure
    else:
        value = mark_value(pos.position, mark)
    return pos.realized_pnl - pos.fees_paid + value - pos.market_exposure


@dataclass(frozen=True, slots=True)
class RiskView:
    """Per-cycle risk decisions consumed by the bot."""

    halted: bool
    globally_paused: bool
    allow_increase: bool
    session_pnl: Decimal
    total_exposure: Decimal


class RiskManager:
    def __init__(self, cfg: RiskConfig, clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self._clock = clock
        self.halted = False
        self.halt_reason = ""
        self._pause_until = 0.0
        self._market_pause_until: dict[str, float] = {}
        self.pause_reasons: dict[str, str] = {}  # ticker -> why it is paused
        self.global_pause_reason = ""
        self._consecutive_errors = 0
        self._baseline: dict[str, Decimal] = {}
        self._last_pnl: dict[str, Decimal] = {}
        self._history: dict[str, deque[tuple[float, Decimal]]] = {}
        self._reduce_only_reason = ""

    # ----------------------------------------------------------------- state

    def halt(self, reason: str) -> None:
        if not self.halted:
            log.critical("HALT: %s", reason)
        self.halted = True
        self.halt_reason = reason

    def record_cycle(self, ok: bool) -> None:
        if ok:
            self._consecutive_errors = 0
            return
        self._consecutive_errors += 1
        if self._consecutive_errors >= self.cfg.max_consecutive_errors:
            self.pause_all(f"{self._consecutive_errors} consecutive failed cycles")
            self._consecutive_errors = 0

    def pause_all(self, reason: str) -> None:
        log.error("%s; pausing all quoting for %.0fs", reason, self.cfg.cooldown_seconds)
        self.global_pause_reason = reason
        self._pause_until = self._clock() + self.cfg.cooldown_seconds

    @property
    def globally_paused(self) -> bool:
        return self._clock() < self._pause_until

    def pause_market(self, ticker: str, reason: str) -> None:
        if not self.market_paused(ticker):
            log.warning(
                "%s: %s; pausing market for %.0fs", ticker, reason, self.cfg.cooldown_seconds
            )
        self._market_pause_until[ticker] = self._clock() + self.cfg.cooldown_seconds
        self.pause_reasons[ticker] = reason

    def market_paused(self, ticker: str) -> bool:
        return self._clock() < self._market_pause_until.get(ticker, 0.0)

    def pause_left(self, ticker: str) -> float:
        """Seconds until ``ticker`` resumes (0 if not paused)."""
        return max(self._market_pause_until.get(ticker, 0.0) - self._clock(), 0.0)

    def near_close(self, market: Market) -> bool:
        to_close = market.seconds_to_close()
        return to_close is not None and to_close < self.cfg.close_buffer_seconds

    # ---------------------------------------------------------------- update

    def _track_fills(self, ticker: str, position: Decimal, now: float) -> None:
        history = self._history.setdefault(ticker, deque())
        if not history or history[-1][1] != position:
            history.append((now, position))
        while history and now - history[0][0] > self.cfg.fill_burst_window_seconds:
            history.popleft()
        traded = sum(
            (abs(b[1] - a[1]) for a, b in zip(history, list(history)[1:], strict=False)), ZERO
        )
        if traded >= self.cfg.fill_burst_contracts and not self.market_paused(ticker):
            log.warning(
                "%s: %s contracts filled in %.0fs; pausing market for %.0fs",
                ticker,
                traded,
                self.cfg.fill_burst_window_seconds,
                self.cfg.cooldown_seconds,
            )
            self._market_pause_until[ticker] = now + self.cfg.cooldown_seconds
            self.pause_reasons[ticker] = (
                f"{traded:f} contracts filled in {self.cfg.fill_burst_window_seconds:.0f}s"
            )
            history.clear()
            history.append((now, position))

    def update(
        self,
        tickers: set[str],
        positions: Mapping[str, Position],
        marks: Mapping[str, Mark | None],
        balance: Decimal,
        live_positions: Mapping[str, Decimal] | None = None,
    ) -> RiskView:
        """Recompute risk. ``live_positions`` (instant, from fills) drive the fill-burst breaker."""
        now = self._clock()
        session_pnl = ZERO
        for ticker in tickers:
            pos = positions.get(ticker, Position.flat(ticker))
            live = live_positions.get(ticker, pos.position) if live_positions else pos.position
            self._track_fills(ticker, live, now)
            mark = marks.get(ticker)
            if mark is None and ticker in self._last_pnl:
                pass  # book unavailable (blind): keep the last valuation rather than cost
            elif ticker in positions:
                self._last_pnl[ticker] = position_pnl(pos, mark)
            pnl = self._last_pnl.get(ticker, ZERO)
            baseline = self._baseline.setdefault(ticker, pnl)
            session_pnl += pnl - baseline

        total_exposure = sum((p.market_exposure for p in positions.values()), ZERO)
        if session_pnl <= -self.cfg.max_session_loss:
            self.halt(f"session loss {session_pnl:.2f} exceeds limit {self.cfg.max_session_loss}")

        reasons = []
        if total_exposure >= self.cfg.max_total_exposure:
            reasons.append(f"exposure ${total_exposure:.2f} at limit")
        if balance < self.cfg.min_balance:
            reasons.append(f"balance ${balance:.2f} below minimum")
        reduce_only = "; ".join(reasons)
        if reduce_only != self._reduce_only_reason:
            if reduce_only:
                log.warning("reduce-only: %s", reduce_only)
            else:
                log.info("reduce-only lifted")
            self._reduce_only_reason = reduce_only
        allow_increase = not reasons

        return RiskView(
            halted=self.halted,
            globally_paused=self.globally_paused,
            allow_increase=allow_increase,
            session_pnl=session_pnl,
            total_exposure=total_exposure,
        )
