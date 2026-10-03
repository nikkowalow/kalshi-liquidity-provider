"""Reward payouts Kalshi actually made, recovered from the account's cash flows.

Kalshi's API lists fills, settlements, deposits and withdrawals, but not
liquidity-incentive payouts: those just land in the balance. So whatever moved
the balance that the other records don't explain is a payout:

    paid = (balance - start balance) - deposits + withdrawals
           - fill cash + fill fees - settlement revenue

Fill cash is counted in Kalshi's terms. A YES bid fill buys YES at the YES
price. A YES ask fill sells YES we hold, or, past zero, buys NO at 1 - price:
Kalshi holds no short YES, so each such contract costs $1 more than a plain
sale of YES would have brought in (and pays its $1 back when sold or settled).

Per market: Kalshi pays each program period separately, some time after it
ends, and only if it reaches the $1 minimum. When a payout shows up it is
split over the periods that ended before it, haven't been matched to a payout
yet and plausibly reached the minimum, in proportion to what the bot estimated
each one earned. That
split is the bot's best guess; only the total is Kalshi's own number. Payouts
no tracked period can take (e.g. for earnings from before periods were
tracked) stay in the total as "unmatched".
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from kalshi_lp.core.types import ZERO

log = logging.getLogger(__name__)

PAYOUT_NOISE = Decimal("0.01")  # balance rounding: smaller residual moves aren't payouts


class CashSource(Protocol):
    async def get_cash_records(
        self, kind: str, *, min_ts: float | None = None
    ) -> list[dict[str, Any]]: ...


def _d(value: object) -> Decimal:
    return Decimal(str(value)) if value not in (None, "") else ZERO


def _ts(record: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str) and value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
    return None


@dataclass(slots=True)
class CashFlows:
    """Everything since the baseline that moved the balance, except payouts."""

    fill_cash: Decimal = ZERO
    fees: Decimal = ZERO
    revenue: Decimal = ZERO
    deposits: Decimal = ZERO
    withdrawals: Decimal = ZERO
    positions: dict[str, Decimal] = field(default_factory=dict)  # YES terms, from fills

    def add_fill(self, f: Mapping[str, Any]) -> None:
        price = _d(f.get("yes_price_dollars"))
        count = _d(f.get("count_fp") or f.get("count"))
        ticker = str(f.get("ticker") or f.get("market_ticker"))
        before = self.positions.get(ticker, ZERO)
        if f.get("book_side") == "bid":  # buys YES (closing NO first, if we hold NO)
            after = before + count
            self.fill_cash -= price * count
        else:  # sells YES (then buys NO at 1 - price)
            after = before - count
            self.fill_cash += price * count
        # NO held (negative YES): each contract into it costs $1 extra, each out returns $1.
        self.fill_cash -= max(-after, ZERO) - max(-before, ZERO)
        self.positions[ticker] = after
        self.fees += _d(f.get("fee_cost"))

    def add_settlement(self, s: Mapping[str, Any]) -> None:
        self.revenue += _d(s.get("revenue")) / 100  # cents

    def unexplained(self, balance_change: Decimal) -> Decimal:
        return (
            balance_change
            - self.deposits
            + self.withdrawals
            - self.fill_cash
            + self.fees
            - self.revenue
        )


def _applied(records: Iterable[Mapping[str, Any]], since: float) -> Decimal:
    total = ZERO
    for r in records:
        ts = _ts(r, "created_ts", "finalized_ts", "created_time")
        if ts is not None and ts >= since and r.get("status", "applied") == "applied":
            total += _d(r.get("amount_cents")) / 100 - _d(r.get("fee_cents")) / 100
    return total


@dataclass(slots=True)
class Payout:
    ts: float
    amount: Decimal
    split: dict[str, Decimal]  # "ticker|period end" -> dollars


class PayoutTracker:
    """Reconciles the balance against Kalshi's records; keeps the payouts it finds."""

    def __init__(self, since: float, start_balance: Decimal, minimum: Decimal = Decimal(1)):
        self.since = since
        self.minimum = minimum  # Kalshi's payout minimum per market and period
        self.start_balance = start_balance
        self.flows = CashFlows()
        self.paid = ZERO  # total payouts found so far
        self.payouts: list[Payout] = []
        self.period_paid: dict[str, Decimal] = {}  # "ticker|period end" -> dollars
        self._fill_ids: set[str] = set()
        self._settlement_ids: set[str] = set()
        self._cursor_ts = since  # fetch fills/settlements from here (overlap, deduped by id)

    async def refresh(
        self, client: CashSource, balance: Decimal, ended: Mapping[str, Decimal], now: float
    ) -> Payout | None:
        """Fetch new records and return the payout found since the last call, if any.

        ``ended``: estimated earnings of program periods that are over, by
        "ticker|period end"; a new payout is split over the unmatched ones.
        """
        start = self._cursor_ts - 60  # a little overlap: records can land late
        for f in await client.get_cash_records("fills", min_ts=start):
            key = str(f.get("fill_id") or f.get("trade_id"))
            if key not in self._fill_ids:
                self._fill_ids.add(key)
                self.flows.add_fill(f)
        for s in await client.get_cash_records("settlements", min_ts=start):
            key = f"{s.get('ticker')}|{s.get('settled_time')}"
            if key not in self._settlement_ids:
                self._settlement_ids.add(key)
                self.flows.add_settlement(s)
        self.flows.deposits = _applied(await client.get_cash_records("deposits"), self.since)
        self.flows.withdrawals = _applied(await client.get_cash_records("withdrawals"), self.since)
        self._cursor_ts = now
        total = self.flows.unexplained(balance - self.start_balance)
        new = total - self.paid
        if new < PAYOUT_NOISE:
            if new < -PAYOUT_NOISE:
                log.debug("payout reconciliation off by %s (records still arriving?)", new)
            return None
        self.paid = total
        payout = Payout(now, new, self._split(new, ended))
        self.payouts.append(payout)
        for key, dollars in payout.split.items():
            self.period_paid[key] = self.period_paid.get(key, ZERO) + dollars
        return payout

    def _split(self, amount: Decimal, ended: Mapping[str, Decimal]) -> dict[str, Decimal]:
        # Only periods that plausibly reached the minimum get paid (half: estimate error).
        floor = self.minimum / 2
        open_ = {k: e for k, e in ended.items() if e >= floor and k not in self.period_paid}
        weight = sum(open_.values(), ZERO)
        if weight <= 0:
            return {}
        return {k: amount * e / weight for k, e in open_.items()}

    def by_market(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for key, dollars in self.period_paid.items():
            ticker = key.split("|", 1)[0]
            out[ticker] = out.get(ticker, ZERO) + dollars
        return out

    @property
    def unmatched(self) -> Decimal:
        """Payouts found that no ended period could take (e.g. from before the bot's ledger)."""
        return self.paid - sum(self.period_paid.values(), ZERO)

    # Persisted in the run journal so the baseline and matches survive restarts.

    def export(self) -> dict[str, Any]:
        return {
            "since": self.since,
            "start_balance": self.start_balance,
            "paid": self.paid,
            "period_paid": self.period_paid,
            "payouts": [
                {"ts": p.ts, "amount": p.amount, "split": p.split} for p in self.payouts[-200:]
            ],
        }

    @classmethod
    def restore(cls, saved: Mapping[str, Any], minimum: Decimal = Decimal(1)) -> PayoutTracker:
        """Back from :meth:`export`. Records are re-read from Kalshi; matches are kept."""
        tracker = cls(float(saved["since"]), _d(saved["start_balance"]), minimum)
        tracker.paid = _d(saved.get("paid"))
        tracker.period_paid = {k: _d(v) for k, v in (saved.get("period_paid") or {}).items()}
        tracker.payouts = [
            Payout(float(p["ts"]), _d(p["amount"]), {k: _d(v) for k, v in p["split"].items()})
            for p in saved.get("payouts") or []
        ]
        return tracker
