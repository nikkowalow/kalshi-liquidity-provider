from decimal import Decimal

from kalshi_lp.core.types import Side
from kalshi_lp.engine.queue import queue_report
from kalshi_lp.strategy.rewards import RewardParams
from tests.factories import make_book, make_order

D = Decimal
PARAMS = RewardParams(target_size=D(100), discount_factor=D("0.5"))


def report(order, full, others, queue=None):
    return queue_report(order, full, others, queue or {}, PARAMS)


def test_rank_counts_better_prices_plus_queue_at_level() -> None:
    order = make_order(Side.BID, "0.4000", 10, order_id="o1")
    others = make_book(yes=[("0.42", 30), ("0.40", 50)], no=[])
    full = make_book(yes=[("0.42", 30), ("0.40", 60)], no=[])
    r = report(order, full, others, {"o1": D(20)})
    assert (r["ahead_better"], r["ahead_total"], r["in_target"]) == (D(30), D(50), "in")


def test_unknown_queue_position_assumes_back_of_level() -> None:
    order = make_order(Side.BID, "0.4000", 10, order_id="o1")
    others = make_book(yes=[("0.42", 30), ("0.40", 50)], no=[])
    r = report(order, others, others)
    assert r["queue_ahead"] is None and r["ahead_total"] == D(80)
    assert r["in_target"] == "in"  # 80 + 10 <= 100


def test_partial_and_out_of_target() -> None:
    order = make_order(Side.BID, "0.4000", 10, order_id="o1")
    partial = make_book(yes=[("0.42", 95)], no=[])
    assert report(order, partial, partial)["in_target"] == "partial"
    out = make_book(yes=[("0.42", 150)], no=[])
    assert report(order, out, out)["in_target"] == "out"


def test_ask_ranks_on_the_no_side() -> None:
    order = make_order(Side.ASK, "0.6000", 5, order_id="a1")  # NO bid at 0.40
    others = make_book(yes=[], no=[("0.45", 40), ("0.40", 10)])
    r = report(order, others, others, {"a1": D(0)})
    assert r["leg"] == "no" and r["ahead_total"] == D(40)


def test_ambiguous_queue_figure_never_improves_rank() -> None:
    # If Kalshi's number counted better prices too, capping at the level size keeps it conservative.
    order = make_order(Side.BID, "0.4000", 10, order_id="o1")
    others = make_book(yes=[("0.42", 30), ("0.40", 5)], no=[])
    r = report(order, others, others, {"o1": D(35)})
    assert r["ahead_total"] == D(35)  # 30 better + min(35, 5)
