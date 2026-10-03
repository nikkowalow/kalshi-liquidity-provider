from decimal import Decimal

from kalshi_lp.core.pricing import PriceGrid
from kalshi_lp.core.types import Side
from kalshi_lp.engine.unwind import Entry, plan_exit
from tests.factories import make_book

D = Decimal
GRID = PriceGrid.linear_cent()
OPENED = 1000.0


def plan(position, entry, book, now=OPENED + 10, **kw):
    cfg = {"seconds": 900, "stop": D("0.05"), "giveup": D(0)} | kw
    return plan_exit(D(position), entry, book, GRID, now, **cfg)


def test_rests_at_the_entry_when_a_sweep_emptied_the_bid() -> None:
    # Bought YES at 0.45; the sweep left a 0.11 bid, but others still offer at 0.47.
    book = make_book(yes=[("0.11", 5)], no=[("0.53", 50)])
    exit_ = plan(22, Entry(D("0.45"), OPENED), book)
    assert (exit_.action, exit_.side, exit_.price) == ("rest", Side.ASK, D("0.45"))


def test_crosses_when_the_market_offers_below_entry_minus_the_stop() -> None:
    # Now others sell YES at 0.38: the market, not just an emptied bid, values it lower.
    book = make_book(yes=[("0.11", 5)], no=[("0.62", 50)])
    exit_ = plan(22, Entry(D("0.45"), OPENED), book)
    assert exit_.action == "cross" and "stop" in exit_.why


def test_crosses_when_the_bid_is_back_at_the_entry() -> None:
    book = make_book(yes=[("0.45", 30)], no=[("0.53", 50)])
    assert plan(22, Entry(D("0.45"), OPENED), book).action == "cross"


def test_crosses_once_the_window_is_over() -> None:
    book = make_book(yes=[("0.11", 5)], no=[("0.53", 50)])
    exit_ = plan(22, Entry(D("0.45"), OPENED), book, now=OPENED + 901)
    assert exit_.action == "cross" and "not out after" in exit_.why


def test_emergency_or_unknown_entry_crosses() -> None:
    book = make_book(yes=[("0.11", 5)], no=[("0.53", 50)])
    assert plan(22, Entry(D("0.45"), OPENED), book, emergency="closing").action == "cross"
    assert plan(22, None, book).action == "cross"


def test_long_no_rests_as_a_yes_bid_at_one_minus_its_entry() -> None:
    # Sold YES at 0.60 from flat = bought NO at 0.40. A sweep emptied the NO bids (0.10);
    # others still sell NO at 0.42 (a 0.58 YES bid). Rest: sell NO at 0.40 = YES bid 0.60.
    book = make_book(yes=[("0.58", 50)], no=[("0.10", 5)])
    exit_ = plan(-10, Entry(D("0.60"), OPENED), book)
    assert (exit_.action, exit_.side, exit_.price) == ("rest", Side.BID, D("0.60"))


def test_giveup_rests_below_the_entry() -> None:
    book = make_book(yes=[("0.11", 5)], no=[("0.53", 50)])
    exit_ = plan(22, Entry(D("0.45"), OPENED), book, giveup=D("0.02"))
    assert exit_.price == D("0.43")
