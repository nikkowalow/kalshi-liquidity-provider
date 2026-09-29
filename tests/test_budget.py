from decimal import Decimal

from kalshi_lp.core.types import Quote, Side
from kalshi_lp.engine.budget import capital_in_use, fit_to_budget, position_cost
from tests.factories import make_order, make_position

D = Decimal
T = "TEST-MKT"


def q(side: Side, price: str, size: int, ticker: str = T) -> Quote:
    return Quote(ticker, side, D(price), D(size))


def test_capital_counts_positions_and_order_collateral() -> None:
    orders = [
        make_order(Side.BID, "0.4000", 5),  # locks 5 * 0.40 = 2.00
        make_order(Side.ASK, "0.6000", 5, order_id="a"),  # NO @ 0.40: 5 * 0.40 = 2.00
    ]
    positions = {T: make_position(position=3, exposure="1.20")}
    # $1.20 position + $2.00 bid + ask: 3 of 5 sell held contracts (free), 2 * $0.40
    assert capital_in_use({T}, positions, {T: D(3)}, orders) == D("4.00")


def test_unrecorded_fills_charged_conservatively() -> None:
    # The stream says 5 contracts; the last cost record covers 3.
    assert position_cost(make_position(position=3, exposure="1.20"), D(5)) == D("3.20")
    assert position_cost(None, D(-2)) == D(2)


def test_fit_shrinks_and_drops_orders() -> None:
    creates = [q(Side.BID, "0.40", 10), q(Side.ASK, "0.60", 10)]  # $4 each
    kept, left = fit_to_budget(creates, D("5.00"), {T: D(0)})
    assert kept == [q(Side.BID, "0.40", 10), q(Side.ASK, "0.60", 2)]  # $4 + 2 * $0.40
    assert left == D("0.20")
    kept, _ = fit_to_budget(creates, D("0.30"), {T: D(0)})
    assert kept == []


def test_position_reducing_orders_are_free_up_to_position() -> None:
    # Long 4 YES: asking to sell 10 costs nothing for the first 4.
    creates = [q(Side.BID, "0.40", 10), q(Side.ASK, "0.60", 10)]
    kept, _ = fit_to_budget(creates, D("1.00"), {T: D(4)})
    # Reducing ask goes first: 4 free + 2 paid (2 * 0.40); bid gets the remaining 0.20 -> 0.
    assert kept == [q(Side.ASK, "0.60", 6)]


def test_selling_held_contracts_locks_nothing() -> None:
    orders = [make_order(Side.ASK, "0.6000", 6)]  # sell 6 YES while long 4
    positions = {T: make_position(position=4, exposure="1.60")}
    # $1.60 position + 2 uncovered contracts * $0.40
    assert capital_in_use({T}, positions, {T: D(4)}, orders) == D("2.40")
