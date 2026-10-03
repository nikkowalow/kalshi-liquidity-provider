"""End-to-end bot behaviour against an in-memory exchange and a synced fake feed."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from kalshi_lp.config import Settings
from kalshi_lp.core.types import Side
from kalshi_lp.engine.bot import LiquidityBot
from kalshi_lp.exchange.models import IncentiveProgram
from tests.factories import make_book, make_market, make_order
from tests.fake_exchange import FakeExchange, FakeFeed

D = Decimal
T = "TEST-MKT"


def settings(**overrides) -> Settings:
    base = {
        "dry_run": False,
        "selection": {"mode": "tickers", "tickers": [T], "max_markets": 1},
        "quoting": {"placement": "reward", "size": 10, "default_target_size": 100},
        "risk": {"max_position_per_market": 20, "order_group_contracts_limit": 0},
        "loop": {"heartbeat_seconds": 0.05, "requote_min_interval_seconds": 0},
    }
    for key, value in overrides.items():
        base[key] = {**base.get(key, {}), **value} if isinstance(value, dict) else value
    return Settings.model_validate(base)


def program(ticker: str = T) -> IncentiveProgram:
    now = datetime.now(UTC)
    return IncentiveProgram(
        "p",
        ticker,
        "liquidity",
        now - timedelta(days=1),
        now + timedelta(days=1),
        D(200),
        False,
        D("0.5"),
        D(100),
    )


@pytest.fixture
def exchange() -> FakeExchange:
    book = make_book(yes=[("0.40", 15), ("0.38", 100)], no=[("0.50", 15), ("0.48", 100)])
    return FakeExchange([make_market(T)], {T: book})


async def started(exchange: FakeExchange, **overrides) -> tuple[LiquidityBot, FakeFeed]:
    feed = FakeFeed(exchange)
    bot = LiquidityBot(settings(**overrides), exchange, feed=feed)  # type: ignore[arg-type]
    await bot.startup()
    return bot, feed


async def step(bot: LiquidityBot, feed: FakeFeed) -> None:
    """Deliver stream updates, then run one requote of every market."""
    feed.sync()
    await bot.requote(set(bot.markets))


def resting(exchange: FakeExchange, side: Side | None = None):
    return sorted(
        (o.side, o.yes_price, o.remaining)
        for o in exchange.orders.values()
        if side is None or o.side is side
    )


async def test_places_two_sided_quotes(exchange: FakeExchange) -> None:
    bot, feed = await started(exchange)
    await step(bot, feed)
    assert resting(exchange) == [(Side.ASK, D("0.50"), D(10)), (Side.BID, D("0.40"), D(10))]
    assert all(o.client_order_id.startswith("klp-") for o in exchange.orders.values())
    # REST results are reflected in live state immediately, before the stream confirms.
    assert len(bot.state.our_orders(T)) == 2


async def test_steady_state_is_idempotent(exchange: FakeExchange) -> None:
    bot, feed = await started(exchange)
    await step(bot, feed)
    before = dict(exchange.orders)
    for _ in range(3):  # our own orders are in the book now; must not chase them
        await step(bot, feed)
    assert exchange.orders == before


async def test_fill_skews_and_limits_position(exchange: FakeExchange) -> None:
    bot, feed = await started(exchange)
    await step(bot, feed)
    bid = next(o for o in exchange.orders.values() if o.side is Side.BID)
    exchange.fill(bid.order_id, D(10))
    await step(bot, feed)
    assert sum(r[2] for r in resting(exchange, Side.BID)) == D(10)  # room left: 20 - 10
    bid = next(o for o in exchange.orders.values() if o.side is Side.BID)
    exchange.fill(bid.order_id, D(10))
    await step(bot, feed)
    assert not resting(exchange, Side.BID)  # at the position limit
    assert resting(exchange, Side.ASK)  # still offering out


async def test_leaves_foreign_orders_alone_and_cleans_orphans(exchange: FakeExchange) -> None:
    manual = make_order(Side.BID, "0.1000", 5, order_id="manual", client_order_id="my-own")
    orphan = make_order(Side.BID, "0.2000", 5, order_id="orphan", client_order_id="klp-old")
    exchange.orders.update({"manual": manual, "orphan": orphan})
    bot, feed = await started(exchange)
    assert "manual" in exchange.orders
    assert "orphan" not in exchange.orders
    await step(bot, feed)
    await bot.shutdown()
    assert list(exchange.orders) == ["manual"]


async def test_trading_halt_pulls_quotes(exchange: FakeExchange) -> None:
    bot, feed = await started(exchange)
    await step(bot, feed)
    assert exchange.orders
    exchange.trading_active = False
    await bot._check_status()
    assert not exchange.orders
    await step(bot, feed)
    assert not exchange.orders  # and doesn't requote while halted


async def test_blind_book_pulls_quotes(exchange: FakeExchange) -> None:
    bot, feed = await started(exchange)
    await step(bot, feed)
    assert exchange.orders
    bot.state.invalidate([T])  # e.g. a sequence gap: we can't trust the book
    await bot.requote({T})
    assert not exchange.orders
    await step(bot, feed)  # fresh snapshot arrives
    assert len(exchange.orders) == 2


async def test_disconnected_feed_pulls_quotes(exchange: FakeExchange) -> None:
    bot, feed = await started(exchange)
    await step(bot, feed)
    feed.connected = False
    await bot.requote({T})
    assert not exchange.orders


async def test_dry_run_sends_nothing_but_estimates(exchange: FakeExchange) -> None:
    exchange.programs = [program()]
    bot, feed = await started(exchange, dry_run=True, selection={"mode": "incentives"})
    await step(bot, feed)
    assert not exchange.orders
    assert bot._desired[T]  # what it would quote
    bot.sample_rewards()
    assert bot.tracker.total_earned > 0  # hypothetical earnings from those quotes


async def test_reward_tracker_credits_live_orders(exchange: FakeExchange) -> None:
    exchange.programs = [program()]
    bot, feed = await started(exchange, selection={"mode": "incentives"})
    await step(bot, feed)
    for _ in range(5):
        bot.sample_rewards()
    stats = bot.tracker.stats[T]
    assert stats.snapshots == 5 and stats.scored == 5
    assert bot.tracker.total_earned > 0


async def test_session_loss_halts_and_cancels(exchange: FakeExchange) -> None:
    bot, feed = await started(exchange, risk={"max_session_loss": 1})
    await step(bot, feed)
    bid = next(o for o in exchange.orders.values() if o.side is Side.BID)
    exchange.fill(bid.order_id, D(10))  # long 10 @ 0.40
    exchange.books[T] = make_book(yes=[("0.05", 200)], no=[("0.93", 200)])  # collapse
    await step(bot, feed)
    assert bot.risk.halted
    assert bot.stopping
    assert not exchange.orders


async def test_startup_waits_out_maintenance(exchange: FakeExchange) -> None:
    exchange.trading_active = False
    checks = 0
    original = exchange.get_exchange_status

    async def status():
        nonlocal checks
        checks += 1
        if checks == 3:
            exchange.trading_active = True
        return await original()

    exchange.get_exchange_status = status  # type: ignore[method-assign]
    bot, feed = await started(exchange, loop={"status_check_interval_seconds": 0.01})
    assert checks == 3
    await step(bot, feed)
    assert exchange.orders


async def test_run_loop_quotes_and_cleans_up(exchange: FakeExchange) -> None:
    feed = FakeFeed(exchange)
    bot = LiquidityBot(settings(), exchange, feed=feed)  # type: ignore[arg-type]
    await bot.run(max_requotes=3)
    assert bot.requotes == 3
    assert not exchange.orders  # shutdown cancelled everything


async def test_max_capital_caps_positions_plus_resting_orders(exchange: FakeExchange) -> None:
    # Quotes are 10 @ 0.40 bid ($4.00) and 10 @ 0.50 ask ($5.00): $9 wanted, $5 allowed.
    bot, feed = await started(exchange, risk={"max_capital": 5})
    await step(bot, feed)
    assert bot.capital_in_use() <= 5
    assert resting(exchange) == [(Side.ASK, D("0.50"), D(2)), (Side.BID, D("0.40"), D(10))]

    # Fills convert locked cash into position cost; replacements must still fit.
    for _ in range(3):
        bid = next((o for o in exchange.orders.values() if o.side is Side.BID), None)
        if bid is None:
            break
        exchange.fill(bid.order_id, bid.remaining)
        await step(bot, feed)
        assert bot.capital_in_use() <= 5
    # Position-reducing asks are still allowed beyond the budget, up to the position size.
    long = exchange.positions[T].position
    assert sum(o.remaining for o in exchange.orders.values() if o.side is Side.ASK) <= long + 2


async def test_move_guard_pauses_fast_markets(exchange: FakeExchange) -> None:
    bot, feed = await started(exchange, risk={"max_mid_move": 0.04})
    await step(bot, feed)
    assert exchange.orders
    exchange.books[T] = make_book(
        yes=[("0.30", 15), ("0.28", 100)], no=[("0.60", 15), ("0.58", 100)]
    )
    await step(bot, feed)  # mid 0.45 -> 0.35 in no time
    assert bot.risk.market_paused(T)
    assert not exchange.orders


async def test_cushion_config_flows_through_bot(exchange: FakeExchange) -> None:
    # Target 1000 -> cushion may go up to 200; the book is only 115 deep.
    bot, feed = await started(exchange, quoting={"min_cushion": 200, "default_target_size": 1000})
    await step(bot, feed)
    assert not exchange.orders


# ------------------------------------------------------------ never hold shares


async def test_fill_is_flattened_immediately(exchange: FakeExchange) -> None:
    bot, feed = await started(
        exchange,
        risk={"flatten_on_fill": True, "fill_burst_contracts": 5, "flatten_retry_seconds": 0.01},
    )
    await step(bot, feed)
    bid = next(o for o in exchange.orders.values() if o.side is Side.BID)
    exchange.fill(bid.order_id, D(10))  # bought 10 YES at 0.40
    await step(bot, feed)
    # Quotes pulled and an exit sells 10 YES into the best bid (0.40), up to 2c through it.
    [exit_] = exchange.exits
    assert (exit_.side, exit_.price, exit_.size) == (Side.ASK, D("0.38"), D(10))
    assert not exchange.orders
    assert exchange.positions[T].position == 0
    # A fill of 10 >= fill_burst_contracts pauses the market, so it stays dark (but flat).
    assert bot.risk.market_paused(T)
    await step(bot, feed)
    assert len(exchange.exits) == 1  # flat now: no more exits


async def test_flatten_works_while_paused_and_short(exchange: FakeExchange) -> None:
    bot, feed = await started(exchange, risk={"flatten_on_fill": True})
    await step(bot, feed)
    ask = next(o for o in exchange.orders.values() if o.side is Side.ASK)
    exchange.fill(ask.order_id, D(10))  # sold 10 YES (= long 10 NO)
    bot.risk.pause_market(T, "test pause")
    await step(bot, feed)
    [exit_] = exchange.exits
    assert (exit_.side, exit_.price) == (Side.BID, D("0.52"))  # buy back at best ask 0.50 + 2c
    assert exchange.positions[T].position == 0


async def test_auto_size_spends_the_budget(exchange: FakeExchange) -> None:
    # Book 0.40 / 0.50: a contract per side locks at most 0.40 + 0.50 = $0.90.
    bot, feed = await started(
        exchange,
        quoting={"auto_size": True, "capital_utilization": 1},
        risk={"max_capital": 18, "max_position_per_market": 100},
    )
    await step(bot, feed)
    assert {o.remaining for o in exchange.orders.values()} == {D(20)}  # $18 / $0.90
    assert bot.capital_in_use() <= 18
    capped, feed2 = await started(
        FakeExchange([make_market(T)], {T: exchange.books[T]}),
        quoting={"auto_size": True, "max_size": 7},
        risk={"max_capital": 18},
    )
    await step(capped, feed2)
    assert {o.remaining for o in capped.executor.client.orders.values()} == {D(7)}  # type: ignore[attr-defined]


async def test_paused_market_slot_goes_to_the_next_best_market() -> None:
    book = make_book(yes=[("0.40", 15), ("0.38", 100)], no=[("0.50", 15), ("0.48", 100)])
    ex = FakeExchange([make_market("AAA-1"), make_market("BBB-1")], {"AAA-1": book, "BBB-1": book})
    ex.programs = [program("AAA-1"), program("BBB-1")]
    bot, _ = await started(ex, selection={"mode": "incentives", "max_markets": 1})
    first = next(iter(bot.markets))
    other = ({"AAA-1", "BBB-1"} - {first}).pop()
    bot.risk.pause_market(first, "test pause")
    await bot.reselect()
    assert list(bot.markets) == [other]  # the free slot went to the other market


async def test_paused_market_keeps_its_slot_when_nothing_better_exists(
    exchange: FakeExchange,
) -> None:
    exchange.programs = [program()]
    bot, _ = await started(exchange, selection={"mode": "incentives", "max_markets": 1})
    bot.risk.pause_market(T, "test pause")
    await bot.reselect()
    assert list(bot.markets) == [T]  # not forced out: resumes when the pause ends


# ------------------------------------------------------- scanning for better markets


async def test_market_scan_runs_without_blocking_quoting(exchange: FakeExchange) -> None:
    bot, _ = await started(exchange)
    locked_during_scan = []
    real_select = bot.selector.select

    async def watching_select(*args, **kwargs):
        locked_during_scan.append(bot._lock.locked())
        return await real_select(*args, **kwargs)

    bot.selector.select = watching_select  # type: ignore[method-assign]
    await bot._reselect_job()
    assert locked_during_scan == [False]


async def test_capital_fills_the_best_market_first() -> None:
    book = make_book(yes=[("0.40", 15), ("0.38", 100)], no=[("0.50", 15), ("0.48", 100)])
    ex = FakeExchange([make_market("AAA-1"), make_market("BBB-1")], {"AAA-1": book, "BBB-1": book})
    # A contract per side locks at most 0.40 + 0.50 = $0.90: $13.50 = 10 + 5 contracts.
    bot, feed = await started(
        ex,
        selection={"tickers": ["AAA-1", "BBB-1"], "max_markets": 2},
        quoting={"auto_size": True, "max_size": 10, "capital_utilization": 1},
        risk={"max_capital": "13.5"},
    )
    await step(bot, feed)
    first, second = bot.markets  # selection order: best first
    assert bot._sizes == {first: D(10), second: D(5)}
    sizes = {(o.ticker, o.remaining) for o in ex.orders.values()}
    assert sizes == {(first, D(10)), (second, D(5))}


async def test_market_without_capital_left_is_not_quoted() -> None:
    book = make_book(yes=[("0.40", 15), ("0.38", 100)], no=[("0.50", 15), ("0.48", 100)])
    ex = FakeExchange([make_market("AAA-1"), make_market("BBB-1")], {"AAA-1": book, "BBB-1": book})
    bot, feed = await started(
        ex,
        selection={"tickers": ["AAA-1", "BBB-1"], "max_markets": 2},
        quoting={"auto_size": True, "max_size": 10, "capital_utilization": 1},
        risk={"max_capital": 9},
    )
    await step(bot, feed)
    first, second = bot.markets
    assert {o.ticker for o in ex.orders.values()} == {first}
    assert bot._decide(second, bot._risk_view()) == (
        "no capital left: higher-paying markets use the budget"
    )


async def test_stopping_keeps_positions_but_a_halt_closes_them(exchange: FakeExchange) -> None:
    bot, feed = await started(exchange, risk={"flatten_on_fill": True})
    await step(bot, feed)
    bid = next(o for o in exchange.orders.values() if o.side is Side.BID)
    exchange.fill(bid.order_id, D(10))
    await bot.shutdown()  # Ctrl+C or the dashboard's stop: a restart picks it up
    assert exchange.positions[T].position == 10 and not exchange.exits
    assert not exchange.orders  # quotes are still pulled
    bot.risk.halt("session loss")
    await bot.shutdown()  # a risk halt does close it
    assert exchange.positions[T].position == 0
    assert exchange.exits[-1].side is Side.ASK


async def test_start_up_keeps_positions_from_the_last_run_and_exits_them(
    exchange: FakeExchange,
) -> None:
    from kalshi_lp.exchange.models import Position

    exchange.positions[T] = Position(T, D(-8), D("4.00"), D(0), D(0))  # 8 NO at 0.50
    feed = FakeFeed(exchange)
    bot = LiquidityBot(
        settings(risk={"flatten_on_fill": True, "exit_mode": "passive"}), exchange, feed=feed
    )  # type: ignore[arg-type]
    bot.tracker.restore({T: {"earned": 0.1}})  # the ledger says we quoted T before
    await bot.startup()
    assert exchange.positions[T].position == -8 and not exchange.exits  # not dumped
    await step(bot, feed)
    # The usual exit takes over. The NO bid (0.50) is back at the NO's cost (0.50): take it.
    assert exchange.exits[-1].side is Side.BID and exchange.positions[T].position == 0


# ------------------------------------------------- capital, churn, and switching


async def test_auto_size_charges_loss_capped_legs_only_what_they_lock(
    exchange: FakeExchange,
) -> None:
    # Quotes rest at 0.40 (YES) and 0.49 (NO, a 0.51 YES ask). A $2 cap allows 5 YES and 4
    # NO, which lock 5 x 0.40 + 4 x 0.49 = $3.96 at any size: $5 funds the full 20, not
    # $5 / $0.90 = 5.
    bot, feed = await started(
        exchange,
        quoting={
            "auto_size": True,
            "capital_utilization": 1,
            "max_size": 20,
            "max_loss_per_fill": 2,
        },
        risk={"max_capital": 5, "max_position_per_market": 100},
    )
    await step(bot, feed)
    assert bot._sizes[T] == 20
    assert resting(exchange) == [(Side.ASK, D("0.51"), D(4)), (Side.BID, D("0.40"), D(5))]
    assert bot.capital_in_use() <= 5


async def test_auto_size_ignores_small_growth_but_always_shrinks(exchange: FakeExchange) -> None:
    # $9 at $0.90 a pair funds 10 contracts.
    bot, feed = await started(
        exchange,
        quoting={"auto_size": True, "capital_utilization": 1},
        risk={"max_capital": 9, "max_position_per_market": 100},
    )
    feed.sync()
    for previous, expected in ((9, 9), (8, 10), (12, 10), (0, 10)):
        bot._sizes = {T: D(previous)}
        assert bot._auto_sizes()[T] == expected, previous


async def test_recently_selected_market_keeps_its_slot() -> None:
    now = datetime.now(UTC)

    def paying(ticker: str, period_reward: int) -> IncentiveProgram:
        return IncentiveProgram(
            "p-" + ticker,
            ticker,
            "liquidity",
            now - timedelta(days=1),
            now + timedelta(days=1),
            D(period_reward),
            False,
            D("0.5"),
            D(100),
        )

    book = make_book(yes=[("0.40", 15), ("0.38", 100)], no=[("0.50", 15), ("0.48", 100)])
    ex = FakeExchange([make_market("AAA-1"), make_market("BBB-1")], {"AAA-1": book, "BBB-1": book})
    ex.programs = [paying("AAA-1", 400), paying("BBB-1", 200)]
    bot, _ = await started(
        ex, selection={"mode": "incentives", "max_markets": 1, "catalog_refresh_seconds": 0}
    )
    assert list(bot.markets) == ["AAA-1"]
    ex.programs = [paying("AAA-1", 200), paying("BBB-1", 800)]  # BBB now pays 4x as much
    await bot.reselect()
    assert list(bot.markets) == ["AAA-1"]  # within min_hold_seconds (15 min): stays
    bot._selected_at["AAA-1"] -= bot.settings.selection.min_hold_seconds
    await bot.reselect()
    assert list(bot.markets) == ["BBB-1"]  # hold over: the better market takes the slot


async def test_cant_flatten_warning_is_not_repeated_every_requote(
    exchange: FakeExchange, caplog: pytest.LogCaptureFixture
) -> None:
    from kalshi_lp.engine.reconciler import Plan

    bot, _ = await started(exchange)
    for _ in range(5):
        assert not bot._add_exit(Plan(), T, D(8), None, D("0.02"))
    assert caplog.text.count("can't flatten") == 1


async def test_only_the_running_period_counts_as_a_stake(exchange: FakeExchange) -> None:
    # Kalshi's $1 minimum applies per program period: earnings from a period that has
    # ended can't help a new one reach it, so they give no stake or head start.
    bot, _ = await started(exchange)
    now = datetime.now(UTC)
    ended, running = now - timedelta(hours=2), now + timedelta(hours=5)
    bot.tracker.restore(
        {
            "OLD-1": {"earned": 3, "periods": {ended.isoformat(): 3}},
            "NOW-1": {"earned": 0.7, "periods": {ended.isoformat(): 0.5, running.isoformat(): 0.2}},
        }
    )
    scan = await bot._scan()
    assert "OLD-1" not in scan.incumbents
    assert scan.incumbents["NOW-1"] == Decimal("0.2")


# ------------------------------------------------------------ passive exits


def swept(exchange: FakeExchange) -> None:
    """The sweep that filled our YES bid also took the bids under it; offers stay."""
    exchange.books[T] = make_book(yes=[("0.30", 50)], no=[("0.50", 15), ("0.48", 100)])


async def test_passive_exit_rests_at_entry_instead_of_crossing(exchange: FakeExchange) -> None:
    bot, feed = await started(exchange, risk={"flatten_on_fill": True, "exit_mode": "passive"})
    await step(bot, feed)
    bid = next(o for o in exchange.orders.values() if o.side is Side.BID)
    exchange.fill(bid.order_id, D(10))  # bought 10 YES at 0.40
    swept(exchange)
    await step(bot, feed)
    assert not exchange.exits  # didn't sell into the 0.30 bid
    assert resting(exchange) == [(Side.ASK, D("0.40"), D(10))]  # only the exit, at the entry
    assert bot.snapshot()["markets"][0]["unwind"]["price"] == D("0.40")
    await step(bot, feed)
    assert len(exchange.unwinds) == 1  # kept, not re-placed every requote
    exit_order = next(iter(exchange.orders.values()))
    exchange.fill(exit_order.order_id, D(10))  # someone bought it back from us at 0.40
    await step(bot, feed)
    assert exchange.positions[T].position == 0 and not exchange.exits
    assert bot.snapshot()["markets"][0]["unwind"] is None
    assert any(o.side is Side.BID for o in exchange.orders.values())  # quoting again


async def test_passive_exit_crosses_when_the_market_moves_against_it(
    exchange: FakeExchange,
) -> None:
    bot, feed = await started(exchange, risk={"flatten_on_fill": True, "exit_mode": "passive"})
    await step(bot, feed)
    bid = next(o for o in exchange.orders.values() if o.side is Side.BID)
    exchange.fill(bid.order_id, D(10))
    swept(exchange)
    await step(bot, feed)
    assert len(exchange.unwinds) == 1
    exchange.books[T] = make_book(yes=[("0.30", 50)], no=[("0.68", 100)])  # offered at 0.32 now
    await step(bot, feed)
    [exit_] = exchange.exits
    assert exit_.side is Side.ASK and exchange.positions[T].position == 0
    assert not exchange.orders  # the resting exit was cancelled first


async def test_passive_exit_crosses_after_its_window(exchange: FakeExchange) -> None:
    bot, feed = await started(
        exchange,
        risk={"flatten_on_fill": True, "exit_mode": "passive", "unwind_seconds": 0.05},
    )
    await step(bot, feed)
    bid = next(o for o in exchange.orders.values() if o.side is Side.BID)
    exchange.fill(bid.order_id, D(10))
    swept(exchange)
    await step(bot, feed)
    assert not exchange.exits
    await asyncio.sleep(0.06)
    await step(bot, feed)
    assert len(exchange.exits) == 1 and exchange.positions[T].position == 0


async def test_positions_in_markets_the_bot_doesnt_trade_are_left_alone(
    exchange: FakeExchange,
) -> None:
    from kalshi_lp.exchange.models import Position

    bot, feed = await started(exchange, risk={"flatten_on_fill": True})
    exchange.positions["MANUAL-1"] = Position("MANUAL-1", D(22), D("19.58"), D(0), D(0))
    feed.sync()
    await bot.requote({T, "MANUAL-1"})  # e.g. a fill there marked it for a requote
    assert not exchange.exits
    assert exchange.positions["MANUAL-1"].position == 22
