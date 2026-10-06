from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from kalshi_lp.config import QuotingConfig, SelectionConfig
from kalshi_lp.core.types import Leg
from kalshi_lp.exchange.models import IncentiveProgram, Trade
from kalshi_lp.strategy.quoting import QuoteEngine
from kalshi_lp.strategy.rewards import RewardParams
from kalshi_lp.strategy.selection import Candidate, MarketSelector, series_of
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


async def test_expected_resolution_counts_as_closing() -> None:
    # Trading closes in 30 days, but Kalshi expects it to resolve in 1: that's what counts.
    soon = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    market = make_market("LATE-1", hours_to_close=720, expected_expiration_time=soon)
    ex = FakeExchange([market], {"LATE-1": BOOK})
    s = selector(ex, mode="volume", min_seconds_to_close=172800)
    assert await s.select() == []
    assert (s.ineligible_because(market) or "").startswith("closes or resolves in 24.0h")


async def test_a_date_in_the_event_ticker_counts_as_resolving() -> None:
    # KXFEAR-26OCT09: close_time a week later, but Oct 9's reading decides it.
    day = datetime.now(UTC) + timedelta(days=2)
    code = day.strftime("%y%b%d").upper()
    market = make_market("FEAR-1", hours_to_close=720, event_ticker=f"KXFEAR-{code}")
    assert market.seconds_to_resolve() < 2 * 86400
    old = make_market("OLD-1", hours_to_close=720, event_ticker="KXMILEAD-26AUG17")
    assert old.seconds_to_resolve() > 700 * 3600  # a past date isn't the resolution day


async def test_markets_already_quoted_stay_until_the_held_threshold() -> None:
    # 4 days left: too little to enter (7 days), enough to stay in (48h).
    ex = FakeExchange([make_market("MID-1", hours_to_close=96)], {"MID-1": BOOK})
    ex.programs = [program("MID-1", 100)]
    cfg = {
        "mode": "incentives",
        "min_seconds_to_close": 7 * 86400,
        "min_seconds_to_close_held": 2 * 86400,
    }
    assert await selector(ex, **cfg).select() == []
    [c] = await selector(ex, **cfg).select(quoting=["MID-1"])
    assert c.ticker == "MID-1"


async def test_programs_ending_soon_are_left() -> None:
    ex = FakeExchange([make_market("END-1", hours_to_close=720)], {"END-1": BOOK})
    ending = program("END-1", 100)
    ex.programs = [replace(ending, end=datetime.now(UTC) + timedelta(hours=1))]
    s = selector(ex, mode="incentives", fallback_to_volume=False, min_program_seconds_left=7200)
    assert await s.select(quoting=["END-1"]) == []  # even a market we're quoting
    [params] = s.reward_params(ex.programs).values()
    assert "period ends in 1.0h" in (s.program_ending_because(params) or "")
    ex.programs = [replace(ending, end=datetime.now(UTC) + timedelta(hours=3))]
    s = selector(ex, mode="incentives", fallback_to_volume=False, min_program_seconds_left=7200)
    assert [c.ticker for c in await s.select()] == ["END-1"]  # 3h left: fine


async def test_short_program_periods_are_skipped() -> None:
    tickers = ["DAY-1", "WEEK-1"]
    ex = FakeExchange(
        [make_market(t, hours_to_close=720) for t in tickers], dict.fromkeys(tickers, BOOK)
    )
    weekly = program("WEEK-1", 100)
    weekly = replace(weekly, end=weekly.start + timedelta(days=7))
    ex.programs = [program("DAY-1", 500), weekly]  # 2-day period vs 7-day period
    s = selector(ex, mode="incentives", min_program_period_days=7)
    assert [c.ticker for c in await s.select()] == ["WEEK-1"]
    [day] = s.reward_params([program("DAY-1", 500)]).values()
    assert "runs 2.0-day periods" in (s.short_program_because(day) or "")


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


async def test_incumbent_keeps_its_slot_against_a_slightly_better_newcomer() -> None:
    # Same series, so only one can be picked. NEW pays 20% more, less than a 50% bonus.
    tickers = ["APPROVE-OLD", "APPROVE-NEW"]
    ex = FakeExchange([make_market(t) for t in tickers], dict.fromkeys(tickers, BOOK))
    ex.programs = [program("APPROVE-OLD", 100), program("APPROVE-NEW", 120)]
    sel = selector(ex, mode="incentives", max_per_series=1, incumbent_bonus=D("0.5"))
    [fresh] = await sel.select()
    assert fresh.ticker == "APPROVE-NEW"
    [kept] = await sel.select({"APPROVE-OLD": D("0.29")})
    assert kept.ticker == "APPROVE-OLD" and kept.earned == D("0.29")
    [switched] = await selector(ex, mode="incentives", max_per_series=1, incumbent_bonus=0).select(
        {"APPROVE-OLD": D("0.29")}
    )
    assert switched.ticker == "APPROVE-NEW"


async def test_earnings_so_far_count_toward_the_payout_minimum() -> None:
    now = datetime.now(UTC)
    ex = FakeExchange([make_market("ENDING-1")], {"ENDING-1": BOOK})
    ex.programs = [  # a tiny program ending in 10 minutes: can't reach $1 from scratch
        IncentiveProgram(
            "e",
            "ENDING-1",
            "liquidity",
            now - timedelta(days=1),
            now + timedelta(minutes=10),
            D(20),
            False,
            D("0.5"),
            D(100),
        )
    ]
    sel = selector(ex, mode="incentives", min_period_payout=D("1.0"), fallback_to_volume=False)
    assert await sel.select() == []
    [c] = await sel.select({"ENDING-1": D("0.99")})  # but we are nearly there
    assert c.est_period_payout >= 1


async def test_incumbents_only_need_the_payout_minimum() -> None:
    ex = FakeExchange([make_market("EDGE-1")], {"EDGE-1": BOOK})
    ex.programs = [program("EDGE-1", 100)]
    [c] = await selector(ex, mode="incentives").select()
    payout = c.est_period_payout
    sel = selector(
        ex,
        mode="incentives",
        fallback_to_volume=False,
        min_period_payout=payout + 1,  # newcomers need more than it projects...
        payout_minimum=payout - 1,  # ...but a market we're in only needs the minimum
    )
    assert await sel.select() == []
    assert [c.ticker for c in await sel.select({"EDGE-1": D(0)})] == ["EDGE-1"]


async def test_competition_is_neutral_by_default() -> None:
    ex = FakeExchange([make_market("PACKED-1")], {"PACKED-1": BOOK})
    ex.programs = [program("PACKED-1", 100)]
    [c] = await selector(ex, mode="incentives").select()
    assert c.competition and c.competition.level == "high"
    assert c.rank_score == c.est_daily_reward  # ranked purely by estimated $/day


async def test_uncrowded_markets_rank_higher_when_weighted() -> None:
    spacious = make_book(yes=[("0.45", 30), ("0.40", 200)], no=[("0.45", 30), ("0.40", 200)])
    ex = FakeExchange(
        [make_market("ROOMY-1"), make_market("PACKED-1")], {"ROOMY-1": spacious, "PACKED-1": BOOK}
    )  # BOOK: Target Size within 1c
    ex.programs = [program("ROOMY-1", 100), program("PACKED-1", 100)]
    picked = {
        c.ticker: c
        for c in await selector(ex, mode="incentives", competition_weight=D("0.25")).select()
    }
    roomy, packed = picked["ROOMY-1"], picked["PACKED-1"]
    assert roomy.competition and roomy.competition.level == "low"
    assert packed.competition and packed.competition.level == "high"
    assert roomy.rank_score == roomy.est_daily_reward * D("1.25")
    assert packed.rank_score == packed.est_daily_reward * D("0.75")


# ------------------------------------------------------------------ fill risk


def sweep_trades(ticker: str, n: int, volume: int, leg: Leg = Leg.YES) -> list[Trade]:
    """``n`` well-separated bursts of ``volume`` contracts over the last few hours."""
    now = datetime.now(UTC)
    return [
        Trade(f"{ticker}-{i}", ticker, D("0.45"), D(volume), leg, now - timedelta(minutes=5 * i))
        for i in range(1, n + 1)
    ]


async def test_markets_whose_fills_cost_more_than_they_pay_are_dropped(caplog) -> None:
    tickers = ["SAFE-1", "SWEPT-1"]
    ex = FakeExchange([make_market(t) for t in tickers], dict.fromkeys(tickers, BOOK))
    ex.programs = [program("SAFE-1", 100), program("SWEPT-1", 100)]
    ex.trades["SWEPT-1"] = sweep_trades("SWEPT-1", n=200, volume=5000)  # every burst reaches us
    caplog.set_level("INFO", logger="kalshi_lp.strategy.selection")
    picked = await selector(ex, mode="incentives", max_markets=5).select()
    assert [c.ticker for c in picked] == ["SAFE-1"]
    assert picked[0].fill_risk is not None and picked[0].fill_risk.fills_per_day == 0
    assert "fills would cost more than the rewards" in caplog.text
    assert "fill risk: SWEPT-1" in caplog.text


async def test_fill_cost_is_subtracted_before_ranking() -> None:
    tickers = ["CALM-1", "BUSY-1"]
    ex = FakeExchange([make_market(t) for t in tickers], dict.fromkeys(tickers, BOOK))
    ex.programs = [program("CALM-1", 100), program("BUSY-1", 120)]  # BUSY pays 20% more...
    sel = selector(ex, mode="incentives", max_markets=5)
    gross = {c.ticker: c for c in await sel.select()}
    assert [*gross] == ["BUSY-1", "CALM-1"]
    busy = gross["BUSY-1"]
    per_fill = busy.quotes[Leg.YES].size * (
        D("0.07") * busy.quotes[Leg.YES].price * (1 - busy.quotes[Leg.YES].price) + D("0.02")
    )
    # ...but enough sweeps through our quote to eat half its reward.
    n = int(busy.est_daily_reward / 2 / per_fill) + 1
    ex.trades["BUSY-1"] = sweep_trades("BUSY-1", n=n, volume=5000)
    net = {c.ticker: c for c in await selector(ex, mode="incentives", max_markets=5).select()}
    assert [*net] == ["CALM-1", "BUSY-1"]
    b = net["BUSY-1"]
    assert b.fill_risk is not None and b.fill_risk.fills_per_day == n * busy.quotes[Leg.YES].size
    assert b.net_daily_reward == b.est_daily_reward - b.fill_cost_per_day
    assert b.rank_score == b.net_daily_reward


async def test_trade_history_is_cached_and_fetched_incrementally() -> None:
    ex = FakeExchange([make_market("AAA-1")], {"AAA-1": BOOK})
    ex.programs = [program("AAA-1", 100)]
    ex.trades["AAA-1"] = sweep_trades("AAA-1", n=3, volume=10)
    sel = selector(ex, mode="incentives")
    await sel.select()
    await sel.select()
    (_, first), (_, second) = ex.trade_calls
    assert first is not None and second is not None
    assert second - first > 23 * 3600  # the second call only asks for what's new


async def test_truncated_history_is_scaled_to_what_it_covers() -> None:
    ex = FakeExchange([make_market("HOT-1")], {"HOT-1": BOOK})
    ex.programs = [program("HOT-1", 100)]
    now = datetime.now(UTC)
    ex.trades["HOT-1"] = [  # 300 prints in the last 5 minutes: more than the fetch cap
        Trade(f"h{i}", "HOT-1", D("0.45"), D(1), Leg.YES, now - timedelta(seconds=i))
        for i in range(300)
    ]
    sel = selector(ex, mode="incentives", max_trades_per_market=100, fallback_to_volume=False)
    await sel.select()
    history = sel._trades["HOT-1"]
    assert (now.timestamp() - history.covered_from) < 3600  # not the full 24h


async def test_markets_with_any_expected_fills_are_skipped_at_zero(caplog) -> None:
    tickers = ["SAFE-1", "BUSY-1"]
    ex = FakeExchange([make_market(t) for t in tickers], dict.fromkeys(tickers, BOOK))
    ex.programs = [program("SAFE-1", 100), program("BUSY-1", 120)]
    ex.trades["BUSY-1"] = sweep_trades("BUSY-1", n=1, volume=5000)  # one sweep: still pays
    caplog.set_level("INFO", logger="kalshi_lp.strategy.selection")
    assert [c.ticker for c in await selector(ex, mode="incentives").select()] == [
        "BUSY-1",
        "SAFE-1",
    ]
    picked = await selector(ex, mode="incentives", max_fills_per_day=D(0)).select()
    assert [c.ticker for c in picked] == ["SAFE-1"]
    assert "over 0 expected fills/day" in caplog.text


async def test_illiquid_markets_are_skipped() -> None:
    tickers = ["QUIET-1", "BUSY-1"]
    ex = FakeExchange(
        [make_market("QUIET-1", volume_24h_fp="40.00"), make_market("BUSY-1")],
        dict.fromkeys(tickers, BOOK),
    )
    ex.programs = [program("QUIET-1", 500), program("BUSY-1", 100)]
    s = selector(ex, mode="incentives", fallback_to_volume=False, min_volume_24h=D(1000))
    assert [c.ticker for c in await s.select(quoting=["QUIET-1"])] == ["BUSY-1"]
    assert "too illiquid" in (s.ineligible_because(ex.markets["QUIET-1"]) or "")


async def test_no_trade_history_is_unknown_risk_not_zero(caplog) -> None:
    tickers = ["SILENT-1", "TRADED-1"]
    ex = FakeExchange([make_market(t) for t in tickers], dict.fromkeys(tickers, BOOK))
    ex.programs = [program("SILENT-1", 500), program("TRADED-1", 100)]
    ex.trades["TRADED-1"] = sweep_trades("TRADED-1", n=25, volume=1)  # small: never reach us
    caplog.set_level("INFO", logger="kalshi_lp.strategy.selection")
    cfg = {"mode": "incentives", "fallback_to_volume": False, "max_fills_per_day": D(0)}
    assert [c.ticker for c in await selector(ex, **cfg).select()] == ["SILENT-1", "TRADED-1"]
    picked = await selector(ex, **cfg, min_fill_risk_trades=20).select()
    assert [c.ticker for c in picked] == ["TRADED-1"]
    assert "fill risk unknown" in caplog.text


def test_fill_caps_need_fill_risk() -> None:
    with pytest.raises(ValueError, match="max_fills_per_day"):
        SelectionConfig(fill_risk=False, max_fills_per_day=D(0))
    with pytest.raises(ValueError, match="max_fill_cost_share"):
        SelectionConfig(fill_risk=False, max_fill_cost_share=D("0.5"))


async def test_markets_whose_fills_eat_too_much_of_the_reward_are_skipped(caplog) -> None:
    tickers = ["CALM-1", "BUSY-1"]
    ex = FakeExchange([make_market(t) for t in tickers], dict.fromkeys(tickers, BOOK))
    ex.programs = [program("CALM-1", 100), program("BUSY-1", 100)]
    [busy] = [c for c in await selector(ex, mode="incentives").select() if c.ticker == "BUSY-1"]
    per_fill = busy.quotes[Leg.YES].size * (
        D("0.07") * busy.quotes[Leg.YES].price * (1 - busy.quotes[Leg.YES].price) + D("0.02")
    )
    n = int(busy.est_daily_reward / 2 / per_fill) + 1  # fills eat just over half the reward
    ex.trades["BUSY-1"] = sweep_trades("BUSY-1", n=n, volume=5000)
    caplog.set_level("INFO", logger="kalshi_lp.strategy.selection")
    loose = await selector(ex, mode="incentives", max_fill_cost_share=D("0.6")).select()
    assert sorted(c.ticker for c in loose) == ["BUSY-1", "CALM-1"]
    strict = await selector(ex, mode="incentives", max_fill_cost_share=D("0.5")).select()
    assert [c.ticker for c in strict] == ["CALM-1"]
    assert "fills would eat over 50% of the rewards" in caplog.text


async def test_fill_risk_can_be_turned_off() -> None:
    ex = FakeExchange([make_market("AAA-1")], {"AAA-1": BOOK})
    ex.programs = [program("AAA-1", 100)]
    [c] = await selector(ex, mode="incentives", fill_risk=False).select()
    assert c.fill_risk is None and not ex.trade_calls


# --------------------------------------------------- best $/h, capital, switching


def capital_selector(exchange: FakeExchange, max_capital: str, **cfg) -> MarketSelector:
    quoting = QuotingConfig(size=D(4), auto_size=True, max_size=D(10), capital_utilization=D(1))
    return MarketSelector(
        exchange,  # type: ignore[arg-type]
        SelectionConfig(**cfg),
        quoting,
        QuoteEngine(quoting, D(50)),
        max_capital=D(max_capital),
    )


async def test_estimates_use_the_size_the_bot_will_quote() -> None:
    ex = FakeExchange([make_market("AAA-1")], {"AAA-1": BOOK})
    ex.programs = [program("AAA-1", 100)]
    [small] = await selector(ex, mode="incentives").select()  # fixed size 10
    [auto] = await capital_selector(ex, "1000", mode="incentives").select()  # auto: max_size 10
    assert auto.size == 10 and small.size == 10
    quoting = QuotingConfig(size=D(4))
    [four] = await MarketSelector(
        ex,  # type: ignore[arg-type]
        SelectionConfig(mode="incentives"),
        quoting,
        QuoteEngine(quoting, D(50)),
    ).select()
    assert four.size == 4 and four.est_daily_reward < auto.est_daily_reward


async def test_capital_goes_to_the_best_markets_at_full_size() -> None:
    tickers = ["TOP-1", "MID-1", "LOW-1"]
    ex = FakeExchange([make_market(t) for t in tickers], dict.fromkeys(tickers, BOOK))
    ex.programs = [program("TOP-1", 300), program("MID-1", 200), program("LOW-1", 100)]
    everything = await capital_selector(ex, "1000", mode="incentives").select()
    assert [c.ticker for c in everything] == tickers  # best $/h first
    two = sum((c.capital_needed for c in everything[:2]), D(0))
    picked = await capital_selector(ex, str(two), mode="incentives").select()
    assert [c.ticker for c in picked] == ["TOP-1", "MID-1"]  # no budget left for LOW-1


async def test_capital_counts_what_loss_capped_orders_actually_lock() -> None:
    # YES bids ~0.20, NO bids ~0.78. A $3 loss cap allows 15 YES but only 3 NO at 0.78, so
    # a market locks ~10 x 0.20 + 3 x 0.78 = $4.34, not 10 contracts x $0.98 a pair.
    skewed = make_book(yes=[("0.20", 30), ("0.19", 200)], no=[("0.78", 30), ("0.77", 200)])
    tickers = ["TOP-1", "MID-1", "LOW-1"]
    ex = FakeExchange([make_market(t) for t in tickers], dict.fromkeys(tickers, skewed))
    ex.programs = [program("TOP-1", 300), program("MID-1", 200), program("LOW-1", 100)]
    quoting = QuotingConfig(
        size=D(4),
        auto_size=True,
        max_size=D(10),
        capital_utilization=D(1),
        max_loss_per_fill=D(3),
    )

    def capped(max_capital: Decimal) -> MarketSelector:
        return MarketSelector(
            ex,  # type: ignore[arg-type]
            SelectionConfig(mode="incentives"),
            quoting,
            QuoteEngine(quoting, D(50)),
            max_capital=max_capital,
        )

    everything = await capped(D(1000)).select()
    top = everything[0]
    assert {q.size for q in top.quotes.values()} == {D(10), D(3)}  # the NO leg is capped
    assert top.capital_needed < D(5) < top.size * top.pair_cost
    budget = sum((c.capital_needed for c in everything[:2]), D(0))
    picked = await capped(budget).select()
    assert [c.ticker for c in picked] == ["TOP-1", "MID-1"]  # size x pair cost: TOP-1 only


async def test_recently_selected_market_keeps_its_slot_while_it_qualifies() -> None:
    tickers = ["APPROVE-OLD", "APPROVE-NEW"]
    ex = FakeExchange([make_market(t) for t in tickers], dict.fromkeys(tickers, BOOK))
    ex.programs = [program("APPROVE-OLD", 100), program("APPROVE-NEW", 200)]
    held = {"APPROVE-OLD": D(0)}
    pick = selector(ex, mode="incentives", max_per_series=1)
    [c] = await pick.select(held)
    assert c.ticker == "APPROVE-NEW"  # twice the reward beats the switching margin...
    [c] = await selector(ex, mode="incentives", max_per_series=1).select(held, keep=held)
    assert c.ticker == "APPROVE-OLD"  # ...but not a market still within its hold time
    ex.programs = [program("APPROVE-NEW", 200)]  # OLD's program ended: it no longer qualifies
    [c] = await selector(ex, mode="incentives", max_per_series=1).select(held, keep=held)
    assert c.ticker == "APPROVE-NEW"


async def test_held_market_is_checked_for_fill_risk_however_it_ranks() -> None:
    tickers = ["TOP-1", "MID-1", "LOW-1"]
    ex = FakeExchange([make_market(t) for t in tickers], dict.fromkeys(tickers, BOOK))
    ex.programs = [program("TOP-1", 300), program("MID-1", 200), program("LOW-1", 100)]
    sel = selector(ex, mode="incentives", max_markets=1, fill_risk_pool=1)
    assert [c.ticker for c in await sel.select()] == ["TOP-1"]
    [kept] = await sel.select({"LOW-1": D(0)}, keep=["LOW-1"])  # outside the checked pool
    assert kept.ticker == "LOW-1" and kept.fill_risk is not None


async def test_default_margin_switches_to_a_meaningfully_better_market() -> None:
    tickers = ["APPROVE-OLD", "APPROVE-NEW"]
    ex = FakeExchange([make_market(t) for t in tickers], dict.fromkeys(tickers, BOOK))
    ex.programs = [program("APPROVE-OLD", 100), program("APPROVE-NEW", 120)]
    [c] = await selector(ex, mode="incentives", max_per_series=1).select({"APPROVE-OLD": D(0)})
    assert c.ticker == "APPROVE-NEW"  # 20% better beats the 10% switching margin
    ex.programs = [program("APPROVE-OLD", 100), program("APPROVE-NEW", 105)]
    [c] = await selector(ex, mode="incentives", max_per_series=1).select({"APPROVE-OLD": D(0)})
    assert c.ticker == "APPROVE-OLD"  # 5% better is within estimate noise: stay


def test_unpaid_earnings_count_only_if_staying_reaches_the_minimum() -> None:
    ex = FakeExchange([], {})
    sel = selector(ex, mode="incentives")
    now = datetime.now(UTC)
    reward = RewardParams(
        D(100), D("0.5"), reward_per_day=D(10), period_end=now + timedelta(days=1)
    )

    def cand(earned: str, est: str) -> Candidate:
        return Candidate(make_market("A-1", hours_to_close=48), reward, D(est), D(0), D(earned))

    assert abs(sel._unpaid_bonus(cand("0.90", "2")) - D("0.90")) < D("0.01")  # ~1 day left
    assert sel._unpaid_bonus(cand("0.90", "0.01")) == 0  # can't reach $1 anyway
    assert sel._unpaid_bonus(cand("1.20", "2")) == 0  # already over the minimum: paid either way
    assert sel._unpaid_bonus(cand("0", "2")) == 0


async def test_program_list_is_cached_between_scans() -> None:
    ex = FakeExchange([make_market("AAA-1")], {"AAA-1": BOOK})
    ex.programs = [program("AAA-1", 100)]
    sel = selector(ex, mode="incentives")
    await sel.select()
    await sel.select()
    assert ex.program_calls == 1
    await selector(ex, mode="incentives", catalog_refresh_seconds=0).select()
    assert ex.program_calls == 2


async def test_excluded_series_are_never_picked() -> None:
    tickers = ["KXBIGGESTQUAKE-30SEP26-5.6", "KXCALM-1"]
    ex = FakeExchange([make_market(t) for t in tickers], dict.fromkeys(tickers, BOOK))
    ex.programs = [program(tickers[0], 500), program(tickers[1], 100)]
    picked = await selector(ex, mode="incentives", exclude_series=["KXBIGGESTQUAKE"]).select()
    assert [c.ticker for c in picked] == ["KXCALM-1"]


# ------------------------------------------------------------------ verdicts


async def test_verdicts_say_why_a_held_market_was_dropped() -> None:
    tickers = ["SAFE-1", "SWEPT-1", "RAIN-A", "RAIN-B", "GONE-1"]
    ex = FakeExchange([make_market(t) for t in tickers], dict.fromkeys(tickers, BOOK))
    ex.programs = [program(t, 100) for t in ["SAFE-1", "SWEPT-1", "RAIN-A"]]
    ex.programs.append(program("RAIN-B", 50))  # RAIN-A outranks it in the series; GONE-1: none
    ex.trades["SWEPT-1"] = sweep_trades("SWEPT-1", n=200, volume=5000)
    sel = selector(ex, mode="incentives", max_markets=5, max_per_series=1)
    held = {t: D(0) for t in ["SAFE-1", "SWEPT-1", "RAIN-B", "GONE-1"]}
    picked = await sel.select(held)
    assert "SAFE-1" in {c.ticker for c in picked}
    v = sel.verdicts
    assert v["SAFE-1"]["stage"] == "selected"
    assert v["SWEPT-1"]["stage"] == "fill_risk"
    assert "after fill costs" in v["SWEPT-1"]["reason"]
    swept = v["SWEPT-1"]["figures"]
    assert swept["fill_cost_per_day"] > swept["est_daily_reward"]
    assert v["RAIN-B"]["stage"] == "diversify" and v["RAIN-B"]["rival"]["ticker"] == "RAIN-A"
    assert v["GONE-1"]["stage"] == "program"
    assert "RAIN-A" not in v  # only markets we were in get a verdict


async def test_verdicts_name_the_filter_and_its_numbers() -> None:
    ex = FakeExchange(
        [make_market("WIDE-1"), make_market("SOON-1", hours_to_close=0.5)],
        {"WIDE-1": WIDE, "SOON-1": BOOK},
    )
    ex.programs = [program("WIDE-1", 100), program("SOON-1", 100)]
    sel = selector(ex, mode="incentives", fallback_to_volume=False, min_seconds_to_close=3600)
    assert await sel.select({"WIDE-1": D(0), "SOON-1": D(0)}) == []
    assert sel.verdicts["WIDE-1"]["stage"] == "book"
    assert "max_spread" in sel.verdicts["WIDE-1"]["reason"]
    assert sel.verdicts["SOON-1"]["stage"] == "eligibility"
    assert "min_seconds_to_close" in sel.verdicts["SOON-1"]["reason"]


@pytest.mark.parametrize(
    ("condition", "skipped"),
    [
        ("This market will close and expire early if the economic data is released.", True),
        (
            "This market may expire early after the specified value is available, per Rule 7.2.",
            True,
        ),
        (
            "It will close and expire early when the manufacturer officially publishes it.",
            True,
        ),
        (
            "The Last Trading Time will be 11:59 PM local time on October 5, 2026 regardless of "
            "any data releases or events occurring.",
            False,
        ),
        ("This market will close and expire after a winner is declared.", False),
        ("", False),
    ],
)
async def test_markets_that_close_on_a_data_release_are_skipped(condition, skipped) -> None:
    m = make_market("ADS-1", can_close_early=True, early_close_condition=condition)
    ex = FakeExchange([m], {"ADS-1": BOOK})
    ex.programs = [program("ADS-1", 100)]
    sel = selector(ex, mode="incentives", fallback_to_volume=False)
    picked = await sel.select({"ADS-1": D(0)})
    assert (picked == []) is skipped
    if skipped:
        assert sel.verdicts["ADS-1"]["stage"] == "eligibility"
        assert "exclude_data_releases" in sel.verdicts["ADS-1"]["reason"]
    off = selector(ex, mode="incentives", fallback_to_volume=False, exclude_data_releases=False)
    assert [c.ticker for c in await off.select()] == ["ADS-1"]
