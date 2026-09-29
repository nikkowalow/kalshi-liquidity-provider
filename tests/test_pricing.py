from decimal import Decimal

import pytest

from kalshi_lp.core.pricing import PriceBand, PriceGrid
from kalshi_lp.core.types import fmt_count, fmt_price

D = Decimal


@pytest.fixture
def cent() -> PriceGrid:
    return PriceGrid.linear_cent()


@pytest.fixture
def mixed() -> PriceGrid:
    # 0.1c ticks in the tails, 1c ticks in the middle.
    return PriceGrid(
        [
            PriceBand(D("0"), D("0.10"), D("0.001")),
            PriceBand(D("0.10"), D("0.90"), D("0.01")),
            PriceBand(D("0.90"), D("1"), D("0.001")),
        ]
    )


def test_rounding_on_cent_grid(cent: PriceGrid) -> None:
    assert cent.round_down(D("0.456")) == D("0.45")
    assert cent.round_up(D("0.451")) == D("0.46")
    assert cent.round_down(D("0.45")) == D("0.45")


def test_tick_steps(cent: PriceGrid) -> None:
    assert cent.tick_below(D("0.45")) == D("0.44")
    assert cent.tick_above(D("0.45")) == D("0.46")
    assert cent.tick_below(D("0.455")) == D("0.45")
    assert cent.ticks_between(D("0.40"), D("0.45")) == 5
    assert cent.ticks_between(D("0.45"), D("0.40")) == 0


def test_validity_and_bounds(cent: PriceGrid) -> None:
    assert cent.is_valid(D("0.01"))
    assert not cent.is_valid(D("0"))
    assert not cent.is_valid(D("1"))
    assert not cent.is_valid(D("0.505"))
    assert cent.min_price == D("0.01")
    assert cent.max_price == D("0.99")


def test_mixed_grid_band_boundaries(mixed: PriceGrid) -> None:
    assert mixed.round_down(D("0.0955")) == D("0.095")
    assert mixed.round_down(D("0.555")) == D("0.55")
    assert mixed.tick_below(D("0.10")) == D("0.099")  # step of the lower band
    assert mixed.tick_above(D("0.10")) == D("0.11")  # step of the upper band
    assert mixed.tick_above(D("0.90")) == D("0.901")


def test_mirrored_grid(mixed: PriceGrid) -> None:
    no = mixed.mirrored()
    # YES 0.905 (fine tail) is NO 0.095.
    assert no.is_valid(D("0.095"))
    assert not no.is_valid(D("0.555"))


def test_from_api_defaults_to_cent() -> None:
    grid = PriceGrid.from_api(None)
    assert grid.tick_above(D("0.50")) == D("0.51")


def test_formatting() -> None:
    assert fmt_price(D("0.56")) == "0.5600"
    assert fmt_count(D(10)) == "10.00"
    assert fmt_count(D("3.999")) == "3.99"
