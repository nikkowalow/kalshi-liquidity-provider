from datetime import UTC, datetime, timedelta
from decimal import Decimal

from kalshi_lp.config import QuotingConfig, SelectionConfig
from kalshi_lp.exchange.models import IncentiveProgram
from kalshi_lp.strategy.estimator import RewardEstimator
from kalshi_lp.strategy.quoting import QuoteEngine
from kalshi_lp.strategy.selection import MarketSelector
from tests.factories import make_book, make_market
from tests.fake_exchange import FakeExchange

D = Decimal
BOOK = make_book(yes=[("0.45", 30), ("0.44", 200)], no=[("0.45", 30), ("0.44", 200)])


async def test_estimates_scale_with_size_and_rank_by_reward() -> None:
    now = datetime.now(UTC)
    ex = FakeExchange([make_market("A-1"), make_market("B-1")], {"A-1": BOOK, "B-1": BOOK})
    ex.programs = [
        IncentiveProgram(
            f"p{t}",
            t,
            "liquidity",
            now - timedelta(days=1),
            now + timedelta(days=1),
            D(reward),
            False,
            D("0.5"),
            D(100),
        )
        for t, reward in (("A-1", 20), ("B-1", 200))
    ]
    quoting = QuotingConfig()
    selector = MarketSelector(
        ex,
        SelectionConfig(),
        quoting,
        QuoteEngine(quoting, D(50)),  # type: ignore[arg-type]
    )
    results = await RewardEstimator(ex, selector, quoting).estimate(  # type: ignore[arg-type]
        [D(10), D(50)], samples=2, interval=0
    )
    assert [r.ticker for r in results] == ["B-1", "A-1"]
    top = results[0]
    assert top.sizes[D(10)].samples == 2
    assert 0 < top.sizes[D(10)].daily < top.sizes[D(50)].daily <= top.reward.reward_per_day
