"""End-to-end bot behaviour against an in-memory exchange and a synced fake feed."""

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
    bot, feed = await started(exchange, quoting={"min_cushion": 200})  # book is only 115 deep
    await step(bot, feed)
    assert not exchange.orders
