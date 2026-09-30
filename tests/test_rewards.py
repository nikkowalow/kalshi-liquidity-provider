from decimal import Decimal

from kalshi_lp.core.orderbook import Level
from kalshi_lp.core.pricing import PriceGrid
from kalshi_lp.strategy.rewards import (
    OwnOrder,
    RewardParams,
    SideScore,
    earned_per_snapshot,
    expected_daily_reward,
    reference_price,
    score_side,
    score_snapshot_side,
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


def test_queue_position_matters(reward: RewardParams) -> None:
    # 150 others at 0.50: at the back of the queue, 100 of Target Size fills first.
    others = ladder(("0.50", 150))
    back = score_side(others, D("0.50"), D(10), reward, GRID)
    front = score_side(others, D("0.50"), D(10), reward, GRID, ahead=D(0))
    assert back.share == 0
    assert front.share == D(10) / D(100)


def test_multiple_own_orders_in_queue(reward: RewardParams) -> None:
    others = ladder(("0.50", 150))
    score = score_snapshot_side(
        others,
        [OwnOrder(D("0.50"), D(10), D(0)), OwnOrder(D("0.50"), D(10), D(85))],
        reward,
        GRID,
    )
    # First order fully in; second has 85 ahead + our 10 = 95, so 5 of it counts.
    assert score.share == D(15) / D(100)


def test_earned_per_snapshot_matches_program_formula(reward: RewardParams) -> None:
    # Holding the whole book on both sides (score 2) for every second of a day
    # earns the full daily reward.
    full_day = earned_per_snapshot(D(2), reward) * 86_400
    assert full_day.quantize(D("0.01")) == reward.reward_per_day


def test_competition_measures_room_below_the_best_bid() -> None:
    from kalshi_lp.strategy.rewards import RewardParams, competition
    from tests.factories import make_book

    params = RewardParams(target_size=Decimal(100), discount_factor=Decimal("0.5"))
    crowded = make_book(yes=[("0.45", 150)], no=[("0.50", 30), ("0.40", 200)])
    spacious = make_book(yes=[("0.45", 30), ("0.40", 200)], no=[("0.50", 30), ("0.44", 200)])
    middling = make_book(yes=[("0.45", 30), ("0.43", 200)], no=[("0.50", 30), ("0.44", 200)])
    thin = make_book(yes=[("0.45", 30)], no=[("0.50", 30)])
    assert competition(crowded, params).level == "high"  # YES: all 100 at the top price
    assert competition(crowded, params).room == 0
    assert competition(spacious, params).level == "low"
    assert competition(spacious, params).room == Decimal("0.05")
    assert competition(middling, params).level == "medium"
    assert competition(thin, params).room is None
