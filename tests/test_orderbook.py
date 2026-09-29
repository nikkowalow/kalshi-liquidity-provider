from decimal import Decimal

from kalshi_lp.core.types import Leg
from tests.factories import make_book

D = Decimal


def test_parses_and_sorts_best_first() -> None:
    book = make_book(yes=[("0.40", 10), ("0.45", 5)], no=[("0.50", 7), ("0.52", 3)])
    assert book.best_yes_bid == D("0.45")
    assert book.best_bid(Leg.NO) == D("0.52")
    assert book.best_yes_ask == D("0.48")
    assert book.mid == D("0.465")
    assert book.depth(Leg.YES) == D(15)


def test_empty_sides() -> None:
    book = make_book(yes=[], no=[("0.50", 7)])
    assert book.best_yes_bid is None
    assert book.mid is None
    assert book.best_yes_ask == D("0.50")


def test_without_removes_own_size() -> None:
    book = make_book(yes=[("0.45", 10), ("0.44", 5)], no=[("0.50", 7)])
    stripped = book.without({Leg.YES: {D("0.45"): D(10), D("0.44"): D(2)}, Leg.NO: {}})
    assert stripped.best_yes_bid == D("0.44")
    assert stripped.yes[0].size == D(3)
    assert stripped.no == book.no
