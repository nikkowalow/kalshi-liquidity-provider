from decimal import Decimal

from kalshi_lp.core.types import Quote, Side
from kalshi_lp.engine.reconciler import reconcile
from tests.factories import make_order

D = Decimal
T = "TEST-MKT"


def q(side: Side, price: str, size: int) -> Quote:
    return Quote(T, side, D(price), D(size))


def test_places_missing_quotes() -> None:
    plan = reconcile([q(Side.BID, "0.40", 10), q(Side.ASK, "0.60", 10)], [])
    assert plan.creates == [q(Side.BID, "0.40", 10), q(Side.ASK, "0.60", 10)]
    assert not plan.cancels and not plan.decreases


def test_keeps_matching_order() -> None:
    plan = reconcile([q(Side.BID, "0.40", 10)], [make_order(Side.BID, "0.4000", 10)])
    assert not plan


def test_replaces_order_at_wrong_price() -> None:
    stale = make_order(Side.BID, "0.3900", 10)
    plan = reconcile([q(Side.BID, "0.40", 10)], [stale])
    assert plan.cancels == [stale]
    assert plan.creates == [q(Side.BID, "0.40", 10)]


def test_decreases_oversized_order_to_keep_priority() -> None:
    big = make_order(Side.BID, "0.4000", 15)
    plan = reconcile([q(Side.BID, "0.40", 10)], [big])
    assert plan.decreases == [(big, D(10))]
    assert not plan.cancels and not plan.creates


def test_tops_up_partially_filled_order() -> None:
    partial = make_order(Side.BID, "0.4000", 6)
    plan = reconcile([q(Side.BID, "0.40", 10)], [partial])
    assert plan.creates == [q(Side.BID, "0.40", 4)]
    assert not plan.cancels


def test_ignores_topup_below_tolerance() -> None:
    partial = make_order(Side.BID, "0.4000", 9.5)
    assert not reconcile([q(Side.BID, "0.40", 10)], [partial])


def test_cancels_unwanted_side_and_duplicates() -> None:
    a = make_order(Side.BID, "0.4000", 10, order_id="a")
    b = make_order(Side.BID, "0.4000", 10, order_id="b")
    ask = make_order(Side.ASK, "0.6000", 10)
    plan = reconcile([q(Side.BID, "0.40", 10)], [a, b, ask])
    assert set(o.order_id for o in plan.cancels) == {"b", ask.order_id}
    assert not plan.creates


def test_every_action_has_a_reason() -> None:
    stale = make_order(Side.BID, "0.3800", 10, order_id="stale")
    unwanted = make_order(Side.ASK, "0.6000", 10, order_id="ask")
    plan = reconcile(
        [q(Side.BID, "0.40", 10)],
        [stale, unwanted],
        reasons={Side.BID: "reward, share 1.3%", Side.ASK: "position limit"},
    )
    assert plan.reason_for_order(stale) == "reprice 0.38 -> 0.40 (reward, share 1.3%)"
    assert plan.reason_for_order(unwanted) == "stop quoting ask: position limit"
    [new] = plan.creates
    assert plan.reason_for_quote(new.ticker, new.side, new.price) == (
        "reprice from 0.38 (reward, share 1.3%)"
    )
    fresh = reconcile([q(Side.ASK, "0.60", 10)], [], reasons={Side.ASK: "reward"})
    [ask] = fresh.creates
    assert fresh.reason_for_quote(ask.ticker, ask.side, ask.price) == "new quote (reward)"
    big = make_order(Side.BID, "0.4000", 25, order_id="big")
    shrink = reconcile([q(Side.BID, "0.40", 10)], [big])
    assert shrink.reason_for_order(big) == "shrink 25 -> 10"
