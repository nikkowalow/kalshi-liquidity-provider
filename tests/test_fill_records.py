import json
import math
from pathlib import Path

from kalshi_lp.engine.fill_records import backfill


def write(path: Path, events: list[dict]) -> None:
    path.write_text("".join(json.dumps(e) + "\n" for e in events))


def sel(ts: float, **markets: dict) -> dict:
    return {
        "ts": ts,
        "type": "markets",
        "markets": [{"ticker": t, **m} for t, m in markets.items()],
    }


def fill(ts: float, ticker: str, order_id: str, taker: bool = False) -> dict:
    return {"ts": ts, "type": "fill", "ticker": ticker, "order_id": order_id, "is_taker": taker}


def test_backfill_takes_entry_and_latest_estimates_from_the_selections(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    early = {"est_fills_per_day": 2.0, "size": 10}  # old selections: contracts only
    later = {"est_fills_per_day": 30.0, "est_fill_events_per_day": 3.0, "size": 10}
    write(
        path,
        [
            sel(100, OTHER={}),
            sel(200, A=early, OTHER={}),  # A enters here
            sel(300, A=later),
            fill(400, "A", "o1"),
            fill(401, "A", "x1", taker=True),  # our exit: no record
            fill(402, "MANUAL", "m1"),  # never selected: a manual trade, no record
        ],
    )
    [rec] = backfill(path)
    assert (rec["ticker"], rec["order_id"], rec["backfilled"]) == ("A", "o1", True)
    assert rec["entry"]["ts"] == 200 and rec["entry"]["approx"]
    assert rec["entry"]["events_per_day"] == 0.2  # 2 contracts / 10 per order: a lower bound
    assert rec["at_fill"]["ts"] == 300 and rec["at_fill"]["events_per_day"] == 3.0
    assert not rec["at_fill"]["approx"]
    assert 1 - math.exp(-3.0) > 0.95  # what the dashboard shows as the daily fill chance


def test_backfill_skips_fills_already_recorded(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    write(
        path,
        [
            sel(100, A={"est_fills_per_day": 1.0, "size": 5}),
            fill(200, "A", "o1"),
            {"ts": 201, "type": "fill_risk", "ticker": "A", "order_id": "o1"},
        ],
    )
    assert backfill(path) == []
