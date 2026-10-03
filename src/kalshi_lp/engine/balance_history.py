"""Every change to the account balance, with when it happened.

Kalshi lists fills, settlements, deposits and withdrawals, each with its time: those
are exact. Reward payouts aren't listed, so they're found in the balance itself: the
journal samples it every ~10 seconds (metrics.jsonl), and a jump the listed records
don't explain, that holds rather than catching up a few seconds later (the balance
lags fills a little), is a payout or another credit. Its time is the sample where the
jump first shows, so it's accurate to about 10 seconds while the bot is running; one
that happened while the bot was stopped shows at the first sample after it restarted.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from kalshi_lp.core.types import ZERO

NOISE = Decimal("0.01")  # rounding: smaller unexplained moves are ignored
HOLD_SECONDS = 60.0  # an unexplained jump must hold this long to count (fills post late)
HOLD_SAMPLES = 6  # ...and through this many later samples


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


def listed_changes(
    records: Mapping[str, Sequence[Mapping[str, Any]]], since: float
) -> list[dict[str, Any]]:
    """The balance changes Kalshi lists: one per fill, settlement, deposit and withdrawal.

    A fill's cash is counted in Kalshi's terms: buying YES pays the YES price; selling YES
    you hold receives it; selling past zero buys NO, paying 1 - price (and selling NO back
    later returns it). Its fee comes off the same change.
    """
    out: list[dict[str, Any]] = []
    positions: dict[str, Decimal] = {}
    # created_time has microseconds: an entry and its exits in the same second stay in order.
    for f in sorted(records.get("fills", []), key=lambda r: _ts(r, "created_time", "ts") or 0):
        ts = _ts(f, "created_time", "ts")
        ticker = str(f.get("ticker") or f.get("market_ticker"))
        price, count = _d(f.get("yes_price_dollars")), _d(f.get("count_fp") or f.get("count"))
        before = positions.get(ticker, ZERO)
        bid = f.get("book_side") == "bid"
        after = before + (count if bid else -count)
        cash = -price * count if bid else price * count
        cash -= max(-after, ZERO) - max(-before, ZERO)  # NO held costs $1 more, returned on sale
        positions[ticker] = after
        fee = _d(f.get("fee_cost"))
        if ts is None or ts < since:
            continue
        # Name it as Kalshi books it, at the price of the side that traded.
        if bid:
            side, leg_price = ("sell NO", 1 - price) if before < 0 else ("buy YES", price)
        else:
            side, leg_price = ("sell YES", price) if before > 0 else ("buy NO", 1 - price)
        out.append(
            {
                "ts": ts,
                "kind": "fill",
                "ticker": ticker,
                "amount": cash - fee,
                "detail": f"{side} {count.normalize():f} @ {leg_price.normalize():f}"
                + (f", fee ${fee:.4f}" if fee else "")
                + (" (taker)" if f.get("is_taker") else ""),
            }
        )
    for s in records.get("settlements", []):
        ts = _ts(s, "settled_time")
        revenue = _d(s.get("revenue")) / 100
        if ts is None or ts < since or not revenue:
            continue
        out.append(
            {
                "ts": ts,
                "kind": "settlement",
                "ticker": s.get("ticker"),
                "amount": revenue,
                "detail": f"settled {s.get('market_result') or ''}".strip(),
            }
        )
    for kind, sign in (("deposits", 1), ("withdrawals", -1)):
        for d in records.get(kind, []):
            ts = _ts(d, "created_ts", "finalized_ts", "created_time")
            if ts is None or ts < since or d.get("status", "applied") != "applied":
                continue
            amount = (_d(d.get("amount_cents")) - _d(d.get("fee_cents"))) / 100
            out.append(
                {
                    "ts": ts,
                    "kind": kind[:-1],
                    "ticker": None,
                    "amount": amount * sign,
                    "detail": str(d.get("type") or ""),
                }
            )
    return sorted(out, key=lambda e: e["ts"])


def unlisted_changes(
    samples: Iterable[tuple[float, Decimal]], listed: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Balance jumps the listed changes don't explain and that hold: payouts and the like."""
    points = sorted(samples)
    if not points:
        return []
    start_ts, start = points[0]
    events = sorted((e["ts"], Decimal(str(e["amount"]))) for e in listed if e["ts"] > start_ts)
    residual: list[tuple[float, Decimal]] = []  # (ts, balance moves not explained so far)
    explained, i = ZERO, 0
    for ts, balance in points:
        while i < len(events) and events[i][0] <= ts:
            explained += events[i][1]
            i += 1
        residual.append((ts, balance - start - explained))
    out: list[dict[str, Any]] = []
    level = ZERO  # unexplained total already reported
    for k, (ts, r) in enumerate(residual):
        step = r - level
        if abs(step) < NOISE:
            continue
        # It must survive the next few samples, however far apart (a stop in between doesn't
        # count as holding), and at least HOLD_SECONDS: a fill's late balance update reverts.
        later = residual[k : k + HOLD_SAMPLES + 1]
        holds = len(later) > HOLD_SAMPLES and later[-1][0] - ts >= HOLD_SECONDS
        if holds and all(abs(x - r) < NOISE for _, x in later):
            gap = ts - residual[k - 1][0] if k else 0
            out.append(
                {
                    "ts": ts,
                    "kind": "reward" if step > 0 else "unexplained",
                    "ticker": None,
                    "amount": step,
                    "detail": (
                        "not in Kalshi's records: a liquidity reward payout (or another credit)"
                        if step > 0
                        else "a debit not in Kalshi's records"
                    )
                    + (" · happened while the bot was stopped" if gap > 300 else ""),
                }
            )
            level = r
    return out


def history(
    records: Mapping[str, Sequence[Mapping[str, Any]]],
    samples: Sequence[tuple[float, Decimal]],
    since: float,
) -> list[dict[str, Any]]:
    """All balance changes since ``since``, newest first, with the balance after each."""
    listed = listed_changes(records, since)
    changes = sorted(listed + unlisted_changes(samples, listed), key=lambda e: e["ts"])
    if samples:
        balance = sorted(samples)[0][1]
        for e in changes:
            balance += Decimal(str(e["amount"]))
            e["balance_after"] = balance
    return changes[::-1]
