"""What the bot thought a market's fill risk was, recorded with each fill.

Every fill gets a ``fill_risk`` journal event with two estimates:

* ``entry``: when the bot picked the market (its selection estimate then);
* ``at_fill``: right before the fill (the live estimate of the orders resting then, or
  failing that, the latest selection estimate).

Each holds the replayed rates per day (fill events, contracts, cost) and the order size
they assumed. The dashboard turns events per day into a daily fill chance:
1 - e^(-events per day).

Fills from before these records existed are backfilled from the journal's ``markets``
events (what each selection estimated), so they show what the bot believed at the time,
not a re-run of today's model. Old selections only logged contracts per day; their events
per day is then contracts / order size, a lower bound (a sweep can fill part of an order),
and ``approx`` is set.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

_MARKETS = b'"type": "markets"'
_FILL = b'"type": "fill"'
_RECORD = b'"type": "fill_risk"'


def estimate(entry: Mapping[str, Any], ts: float, basis: str) -> dict[str, Any] | None:
    """One estimate from a selection (``markets`` event) entry, or None if it had none."""
    fills = entry.get("est_fills_per_day")
    if fills is None:
        return None
    events = entry.get("est_fill_events_per_day")
    size = entry.get("size")
    approx = events is None
    if approx:
        events = float(fills) / float(size) if size else None
    return {
        "ts": ts,
        "basis": basis,
        "events_per_day": events,
        "fills_per_day": fills,
        "cost_per_day": entry.get("fill_cost_per_day"),
        "size": size,
        "approx": approx,
    }


def _lines(path: Path, *marks: bytes) -> Iterator[dict[str, Any]]:
    with path.open("rb") as f:
        for line in f:
            head = line[:80]
            if any(m in head for m in marks):
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def backfill(path: Path) -> list[dict[str, Any]]:
    """``fill_risk`` records for the journal's maker fills that don't have one yet."""
    if not path.exists():
        return []
    selections: list[tuple[float, dict[str, dict[str, Any]]]] = []  # ts -> ticker -> entry
    fills: list[dict[str, Any]] = []
    done: set[tuple[str, str]] = set()
    for e in _lines(path, _MARKETS, _FILL, _RECORD):
        kind = e.get("type")
        if kind == "markets":
            selections.append((e["ts"], {m["ticker"]: m for m in e.get("markets") or []}))
        elif kind == "fill" and not e.get("is_taker"):
            fills.append(e)
        elif kind == "fill_risk":
            done.add((str(e.get("order_id")), str(e.get("ticker"))))
    records = []
    for f in fills:
        ticker, order_id = f.get("ticker"), str(f.get("order_id"))
        if (order_id, str(ticker)) in done:
            continue
        before = [(ts, s) for ts, s in selections if ts <= f["ts"]]
        if not before or ticker not in before[-1][1]:
            continue  # not a market the bot was quoting: a manual trade
        start = len(before) - 1  # back to the first selection of this stint in the market
        while start > 0 and ticker in before[start - 1][1]:
            start -= 1
        entry_ts, entry_sel = before[start]
        last_ts, last_sel = before[-1]
        records.append(
            {
                "fill_ts": f["ts"],
                "order_id": f.get("order_id"),
                "ticker": ticker,
                "entry": estimate(entry_sel[ticker], entry_ts, "selection"),
                "at_fill": estimate(last_sel[ticker], last_ts, "selection"),
                "backfilled": True,
            }
        )
        done.add((order_id, str(ticker)))
    return records
