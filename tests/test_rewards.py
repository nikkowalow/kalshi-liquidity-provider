from decimal import Decimal

from kalshi_lp.core.orderbook import Level
from kalshi_lp.core.pricing import PriceGrid
from kalshi_lp.strategy.rewards import (
    RewardParams,
    SideScore,
    expected_daily_reward,
    reference_price,
    score_side,
)

D = Decimal
GRID = PriceGrid.linear_cent()


def ladder(*levels: tuple[str, int]) -> list[Level]:
    return [Level(D(p), D(s)) for p, s in levels]


def test_reference_price_walks_down_to_depth() -> None:
    bids = ladder(("0.50", 5), ("0.49", 10), ("0.48", 50))
    assert reference_price(bids, D(15)) == D("0.49")
    assert reference_price(bids, D(16)) == D("0.48")
    assert reference_price(bids, D(1000)) is None


def test_full_credit_at_reference(reward: RewardParams) -> None:
    # target 100 -> reference depth 20. Others: 20 @ .50, 100 @ .45
    others = ladder(("0.50", 20), ("0.45", 100))
    score = score_side(others, D("0.50"), D(10), reward, GRID)
    # Qualifying: 20 others + our 10 @ .50 (full credit), then 70 of the .45 level.
    # .45 is 5 ticks below ref .50 -> 0.5**5.
    others_raw = D(20) + D(70) * D("0.5") ** 5
    assert score.meets_target
    assert score.reference_price == D("0.50")
    assert score.share == D(10) / (D(10) + others_raw)


def test_deep_order_is_discounted(reward: RewardParams) -> None:
    others = ladder(("0.50", 20), ("0.45", 100))
    near = score_side(others, D("0.50"), D(10), reward, GRID)
    far = score_side(others, D("0.47"), D(10), reward, GRID)
    assert far.share < near.share


def test_side_below_target_scores_zero(reward: RewardParams) -> None:
    score = score_side(ladder(("0.50", 20)), D("0.49"), D(10), reward, GRID)
    assert not score.meets_target
    assert score.share == 0


def test_order_beyond_target_does_not_qualify(reward: RewardParams) -> None:
    others = ladder(("0.50", 150))
    score = score_side(others, D("0.40"), D(10), reward, GRID)
    assert score.meets_target
    assert score.share == 0


def test_expected_daily_reward(reward: RewardParams) -> None:
    yes = SideScore(D("0.2"), True, D("0.5"))
    no = SideScore(D("0.4"), True, D("0.5"))
    assert expected_daily_reward(yes, no, reward) == D(50) * D("0.3")
    assert expected_daily_reward(yes, SideScore(D(0), False, None), reward) == 0
