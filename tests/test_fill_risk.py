from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from kalshi_lp.core.types import Leg
from kalshi_lp.exchange.models import Trade
from kalshi_lp.strategy.fill_risk import (
    PlannedQuote,
    depth_ahead,
    estimate_fill_risk,
    sweeps,
    taker_fee,
)
from tests.factories import make_book

D = Decimal
T0 = datetime(2026, 9, 29, 12, tzinfo=UTC)


def trade(leg: Leg, count: str, seconds: float, price: str = "0.40", block: bool = False) -> Trade:
    return Trade("t", "MKT", D(price), D(count), leg, T0 + timedelta(seconds=seconds), block)


def test_parses_api_trades_and_maps_the_hit_book() -> None:
    sell_yes = Trade.from_api(
        {
            "count_fp": "147.53",
            "created_time": "2026-09-30T02:31:01.613143Z",
            "yes_price_dollars": "0.6900",
            "taker_book_side": "ask",
            "taker_side": "no",
            "ticker": "KXWTI",
            "trade_id": "a",
        }
    )
    assert sell_yes.hit_leg is Leg.YES  # a taker selling YES hits the YES bids
    assert sell_yes.count == D("147.53") and sell_yes.leg_price == D("0.69")
    buy_yes = Trade.from_api(
        {"count": 5, "created_time": "2026-09-30T02:31:01Z", "yes_price": 64, "taker_side": "yes"}
    )
    assert buy_yes.hit_leg is Leg.NO  # a taker buying YES hits the NO bids...
    assert buy_yes.leg_price == D("0.36")  # ...at the NO price
    block = Trade.from_api(
        {
            "count_fp": "1",
            "created_time": "2026-09-30T02:31:01Z",
            "yes_price_dollars": "0.5",
            "is_block_trade": True,
        }
    )
    assert block.block and block.hit_leg is None
    with pytest.raises(ValueError):
        Trade.from_api({"count_fp": "1"})


def test_prints_close_together_form_one_sweep_per_book() -> None:
    trades = [
        trade(Leg.YES, "10", 0),
        trade(Leg.YES, "20", 1.5),  # within 2s of the previous print
        trade(Leg.YES, "5", 3.0),  # ...and of this one: still the same burst
        trade(Leg.YES, "7", 10),  # a new burst
        trade(Leg.NO, "4", 1),  # other book: its own burst
        trade(Leg.YES, "99", 2, block=True),  # block trades never touch the book
    ]
    got = sorted((s.leg.value, s.volume) for s in sweeps(trades, window_seconds=2))
    assert got == [("no", D(4)), ("yes", D(7)), ("yes", D(35))]


def test_depth_ahead_counts_better_prices_and_our_level() -> None:
    book = make_book(yes=[("0.45", 30), ("0.44", 200), ("0.40", 50)], no=[("0.50", 10)])
    assert depth_ahead(book.bids(Leg.YES), D("0.44")) == 230
    assert depth_ahead(book.bids(Leg.YES), D("0.43")) == 230
    assert depth_ahead(book.bids(Leg.YES), D("0.46")) == 0


def test_only_the_part_of_a_sweep_beyond_the_queue_reaches_us() -> None:
    quote = {Leg.YES: PlannedQuote(D("0.40"), D(10), ahead=D(100))}
    trades = [
        trade(Leg.YES, "90", 0),  # stops short of us
        trade(Leg.YES, "105", 100),  # 5 contracts reach us
        trade(Leg.YES, "500", 200),  # blows through: our whole order
        trade(Leg.NO, "500", 300),  # the other book: we have no quote there
    ]
    risk = estimate_fill_risk(
        trades,
        quote,
        window_hours=24,
        sweep_window_seconds=2,
        fee_rate=D("0.07"),
        adverse_move=D("0.02"),
    )
    assert risk.fills_per_day == 15
    per_contract = taker_fee(D("0.40"), D("0.07")) + D("0.02")  # 0.0168 + 0.02
    assert per_contract == D("0.0368")
    assert risk.cost_per_day == 15 * per_contract
    assert risk.cost_per_contract == per_contract
    assert risk.sweeps_per_day == 4
    assert risk.hits_per_day == 2  # two of them reached us: two fill events a day


def test_rates_scale_to_a_day() -> None:
    quote = {Leg.NO: PlannedQuote(D("0.60"), D(10), ahead=D(0))}
    trades = [trade(Leg.NO, "10", 0)]
    half_day = estimate_fill_risk(
        trades,
        quote,
        window_hours=12,
        sweep_window_seconds=2,
        fee_rate=D("0.07"),
        adverse_move=D(0),
    )
    assert half_day.fills_per_day == 20  # 10 in 12 hours
    quiet = estimate_fill_risk(
        [], quote, window_hours=24, sweep_window_seconds=2, fee_rate=D("0.07"), adverse_move=D(0)
    )
    assert quiet.fills_per_day == 0 and quiet.cost_per_day == 0 and quiet.cost_per_contract == 0


def test_counting_on_half_the_queue_lets_smaller_sweeps_reach_us() -> None:
    quote = {Leg.YES: PlannedQuote(D("0.40"), D(10), ahead=D(100))}
    trades = [trade(Leg.YES, "60", 0), trade(Leg.YES, "90", 100)]  # both stop short of 100
    kw = {"window_hours": 24, "sweep_window_seconds": 2, "fee_rate": D(0), "adverse_move": D(0)}
    full = estimate_fill_risk(trades, quote, **kw)
    half = estimate_fill_risk(trades, quote, queue_factor=D("0.5"), **kw)
    assert full.hits_per_day == 0
    assert half.hits_per_day == 2  # past the 50 we count on: 10 and 40 -> our 10 each time
    assert half.fills_per_day == 20
