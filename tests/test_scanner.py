"""Market scanner: every rewarded market's $/day at a fixed size (read-only)."""

import json
from decimal import Decimal

from kalshi_lp.config import QuotingConfig, ScannerConfig, SelectionConfig
from kalshi_lp.journal import RunJournal
from kalshi_lp.strategy.quoting import QuoteEngine
from kalshi_lp.strategy.scanner import MarketScanner
from kalshi_lp.strategy.selection import MarketSelector
from tests.factories import make_book, make_market
from tests.fake_exchange import FakeExchange
from tests.test_selection import BOOK, program, sweep_trades

D = Decimal


def scanner(ex: FakeExchange, quoting: QuotingConfig | None = None, **cfg) -> MarketScanner:
    """A scanner beside a bot that quotes 4 contracts with a $1 loss cap (the scan ignores both)."""
    quoting = quoting or QuotingConfig(size=D(4), max_loss_per_fill=D(1))
    selection = SelectionConfig(mode="incentives", exclude_series=["SKIP"])
    bot_selector = MarketSelector(
        ex,  # type: ignore[arg-type]
        selection,
        quoting,
        QuoteEngine(quoting, D(50)),
    )
    return MarketScanner(ex, bot_selector, ScannerConfig(pace_seconds=0, **cfg))  # type: ignore[arg-type]


async def test_ranks_every_rewarded_market_by_dollars_per_day_at_ten_contracts() -> None:
    tickers = ["LOW-1", "HIGH-1", "MID-1", "NONE-1"]
    ex = FakeExchange([make_market(t) for t in tickers], dict.fromkeys(tickers, BOOK))
    ex.programs = [program("LOW-1", 50), program("HIGH-1", 300), program("MID-1", 100)]
    report = await scanner(ex, top=2).scan(trading=["MID-1"])

    assert report["size"] == 10 and report["markets"] == 3 and report["earning"] == 3
    assert [r["ticker"] for r in report["rows"]] == ["HIGH-1", "MID-1"]  # top 2, NONE-1 unpaid
    high, mid = report["rows"]
    assert high["rank"] == 1 and high["est_daily"] > mid["est_daily"] > 0
    assert high["est_hourly"] == high["est_daily"] / 24
    # 10 contracts on each side, whatever the bot's own size and loss cap.
    assert high["capital"] == 10 * (high["yes_price"] + high["no_price"])
    assert high["return_daily"] == high["est_daily"] / high["capital"]
    assert high["period_payout"] > 0 and high["net_daily"] == high["est_daily"]  # no trades
    assert mid["trading"] and not high["trading"]
    assert not ex.orders and not ex.exits  # research only: nothing is ever placed


async def test_fill_cost_and_filter_reasons_are_reported() -> None:
    tickers = ["CALM-1", "SWEPT-1", "SKIP-1"]
    closing = make_market("SOON-1", hours_to_close=0.5)
    ex = FakeExchange(
        [*(make_market(t) for t in tickers), closing], dict.fromkeys([*tickers, "SOON-1"], BOOK)
    )
    ex.programs = [program(t, 100) for t in [*tickers, "SOON-1"]]
    ex.trades["SWEPT-1"] = sweep_trades("SWEPT-1", n=20, volume=5000)
    rows = {r["ticker"]: r for r in (await scanner(ex).scan())["rows"]}

    assert rows["SWEPT-1"]["fills_per_day"] > 0
    assert rows["SWEPT-1"]["net_daily"] < rows["SWEPT-1"]["est_daily"]
    assert rows["CALM-1"]["fills_per_day"] == 0 and rows["CALM-1"]["skip"] is None
    assert rows["SKIP-1"]["skip"] == "excluded series"
    assert rows["SOON-1"]["skip"].startswith("closes within")


async def test_fill_risk_can_be_skipped() -> None:
    ex = FakeExchange([make_market("A-1")], {"A-1": BOOK})
    ex.programs = [program("A-1", 100)]
    [row] = (await scanner(ex, fill_risk=False).scan())["rows"]
    assert row["fills_per_day"] is None and row["net_daily"] is None
    assert not ex.trade_calls


def test_journal_keeps_the_latest_scan_and_publishes_it(tmp_path) -> None:
    j = RunJournal(tmp_path, "demo", "dry")
    got = []
    j.subscribers.append(lambda channel, text: got.append((channel, json.loads(text))))
    j.write_scan({"rows": [{"ticker": "A", "est_daily": D("1.5")}]})
    j.write_scan({"rows": []})
    j.close()
    assert json.loads((j.dir / "scan.json").read_text()) == {"rows": []}  # replaced, not appended
    assert [c for c, _ in got] == ["scan", "scan"] and got[0][1]["rows"][0]["est_daily"] == 1.5


async def test_book_that_earns_nothing_is_left_out() -> None:
    thin = make_book(yes=[("0.45", 5)], no=[("0.45", 5)])  # never reaches Target Size
    ex = FakeExchange([make_market("THIN-1")], {"THIN-1": thin})
    ex.programs = [program("THIN-1", 100)]
    report = await scanner(ex).scan()
    assert report["markets"] == 1 and report["earning"] == 0 and report["rows"] == []
