from datetime import UTC, datetime, timedelta
from decimal import Decimal

from kalshi_lp.config import QuotingConfig, SelectionConfig
from kalshi_lp.exchange.models import IncentiveProgram
from kalshi_lp.strategy.quoting import QuoteEngine
from kalshi_lp.strategy.selection import MarketSelector, series_of
from tests.factories import make_book, make_market
from tests.fake_exchange import FakeExchange

D = Decimal
BOOK = make_book(yes=[("0.45", 30), ("0.44", 200)], no=[("0.45", 30), ("0.44", 200)])  # .45/.55
WIDE = make_book(yes=[("0.05", 30)], no=[("0.05", 30)])  # 0.05 / 0.95


def program(ticker: str, per_day: int) -> IncentiveProgram:
    now = datetime.now(UTC)
    return IncentiveProgram(
        id=f"p-{ticker}",
        market_ticker=ticker,
        incentive_type="liquidity",
        start=now - timedelta(days=1),
        end=now + timedelta(days=1),
        period_reward=D(per_day * 2),
        paid_out=False,
        discount_factor=D("0.5"),
        target_size=D(100),
    )


def selector(exchange: FakeExchange, **cfg) -> MarketSelector:
    quoting = QuotingConfig(size=D(10))
    return MarketSelector(
        exchange,  # type: ignore[arg-type]
        SelectionConfig(**cfg),
        quoting,
        QuoteEngine(quoting, D(50)),
    )


async def test_ranks_incentive_markets_by_estimated_reward() -> None:
    tickers = ["AAA-1", "BBB-1", "CCC-1"]
    ex = FakeExchange([make_market(t) for t in tickers], dict.fromkeys(tickers, BOOK))
    ex.programs = [program("AAA-1", 10), program("BBB-1", 100)]  # CCC has no program
    picked = await selector(ex, mode="incentives", max_markets=5).select()
    assert [c.ticker for c in picked] == ["BBB-1", "AAA-1"]
    assert picked[0].reward.reward_per_day == D(100)
    assert picked[0].est_daily_reward > picked[1].est_daily_reward > 0


async def test_falls_back_to_volume_and_skips_wide_books() -> None:
    ex = FakeExchange(
        [
            make_market("HOT-1", volume_24h_fp="5000.00"),
            make_market("WIDE-1", volume_24h_fp="9000.00"),
            make_market("COLD-1", volume_24h_fp="10.00"),
        ],
        {"HOT-1": BOOK, "WIDE-1": WIDE, "COLD-1": BOOK},
    )
    picked = await selector(ex, mode="incentives", max_markets=5).select()
    assert [c.ticker for c in picked] == ["HOT-1", "COLD-1"]


async def test_filters_markets_closing_soon() -> None:
    ex = FakeExchange([make_market("SOON-1", hours_to_close=0.5)], {"SOON-1": BOOK})
    assert await selector(ex, mode="volume", min_seconds_to_close=3600).select() == []


async def test_caps_markets_per_series() -> None:
    tickers = ["RAIN-DAL", "RAIN-LV", "RAIN-MIA", "NFL-HOU"]
    ex = FakeExchange([make_market(t) for t in tickers], dict.fromkeys(tickers, BOOK))
    picked = await selector(ex, mode="volume", max_markets=5, max_per_series=1).select()
    assert sorted(series_of(c.ticker) for c in picked) == ["NFL", "RAIN"]


async def test_skips_markets_that_cannot_reach_the_payout_minimum() -> None:
    now = datetime.now(UTC)
    ex = FakeExchange(
        [make_market("BIG-1"), make_market("SMALL-1")], {"BIG-1": BOOK, "SMALL-1": BOOK}
    )
    ex.programs = [
        IncentiveProgram(  # $200/day for a full day left
            "b",
            "BIG-1",
            "liquidity",
            now - timedelta(days=1),
            now + timedelta(days=1),
            D(400),
            False,
            D("0.5"),
            D(100),
        ),
        IncentiveProgram(  # same rate, but the period ends in 10 minutes
            "s",
            "SMALL-1",
            "liquidity",
            now - timedelta(days=1),
            now + timedelta(minutes=10),
            D(200) * D(1 + 10 / 1440),
            False,
            D("0.5"),
            D(100),
        ),
    ]
    picked = await selector(ex, mode="incentives", min_period_payout=D("1.0")).select()
    assert [c.ticker for c in picked] == ["BIG-1"]
    assert picked[0].est_period_payout >= 1


async def test_payout_projection_stops_at_market_close() -> None:
    now = datetime.now(UTC)
    # Program runs 3 more days, but the market closes in 7 hours.
    ex = FakeExchange([make_market("SOON-1", hours_to_close=7)], {"SOON-1": BOOK})
    ex.programs = [
        IncentiveProgram(
            "p",
            "SOON-1",
            "liquidity",
            now - timedelta(days=1),
            now + timedelta(days=3),
            D(800),
            False,
            D("0.5"),
            D(100),
        )
    ]
    [c] = await selector(ex, mode="incentives", min_seconds_to_close=3600).select()
    assert D("0.28") < c.earning_days_left < D("0.30")  # ~7h, not 3 days
    expected = c.est_daily_reward * D(7) / 24  # clock ticks between calls, so compare to the cent
    assert abs(c.est_period_payout - expected) < D("0.01")
