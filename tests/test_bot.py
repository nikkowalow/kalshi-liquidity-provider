"""End-to-end bot cycles against an in-memory exchange."""

from decimal import Decimal

import pytest

from kalshi_lp.config import Settings
from kalshi_lp.core.types import Side
from kalshi_lp.engine.bot import LiquidityBot
from tests.factories import make_book, make_market, make_order
from tests.fake_exchange import FakeExchange

D = Decimal
T = "TEST-MKT"


def settings(**overrides) -> Settings:
    base = {
        "dry_run": False,
        "selection": {"mode": "tickers", "tickers": [T], "max_markets": 1},
        "quoting": {"placement": "reward", "size": 10, "default_target_size": 100},
        "risk": {"max_position_per_market": 20, "order_group_contracts_limit": 0},
    }
    for key, value in overrides.items():
        base[key] = {**base.get(key, {}), **value} if isinstance(value, dict) else value
    return Settings.model_validate(base)


@pytest.fixture
def exchange() -> FakeExchange:
    book = make_book(yes=[("0.40", 15), ("0.38", 100)], no=[("0.50", 15), ("0.48", 100)])
    return FakeExchange([make_market(T)], {T: book})


async def test_first_cycle_places_two_sided_quotes(exchange: FakeExchange) -> None:
    bot = LiquidityBot(settings(), exchange)  # type: ignore[arg-type]
    await bot.startup()
    await bot.cycle()
    quotes = sorted((o.side, o.yes_price, o.remaining) for o in exchange.orders.values())
    assert quotes == [(Side.ASK, D("0.50"), D(10)), (Side.BID, D("0.40"), D(10))]
    assert all(o.client_order_id.startswith("klp-") for o in exchange.orders.values())


async def test_steady_state_is_idempotent(exchange: FakeExchange) -> None:
    bot = LiquidityBot(settings(), exchange)  # type: ignore[arg-type]
    await bot.startup()
    await bot.cycle()
    before = dict(exchange.orders)
    await bot.cycle()  # our own orders are in the book now; must not chase them
    assert exchange.orders == before


async def test_fill_skews_and_limits_position(exchange: FakeExchange) -> None:
    bot = LiquidityBot(settings(), exchange)  # type: ignore[arg-type]
    await bot.startup()
    await bot.cycle()
    bid = next(o for o in exchange.orders.values() if o.side is Side.BID)
    exchange.fill(bid.order_id, D(10))
    await bot.cycle()
    bids = [o for o in exchange.orders.values() if o.side is Side.BID]
    # Long 10 of max 20: bid shrinks to the remaining room.
    assert sum(o.remaining for o in bids) == D(10)
    exchange.fill(bids[0].order_id, D(10))
    await bot.cycle()
    assert not [o for o in exchange.orders.values() if o.side is Side.BID]  # at limit
    assert [o for o in exchange.orders.values() if o.side is Side.ASK]  # still offering out


async def test_leaves_foreign_orders_alone_and_cleans_orphans(exchange: FakeExchange) -> None:
    manual = make_order(Side.BID, "0.1000", 5, order_id="manual", client_order_id="my-own")
    orphan = make_order(Side.BID, "0.2000", 5, order_id="orphan", client_order_id="klp-old")
    exchange.orders.update({"manual": manual, "orphan": orphan})
    bot = LiquidityBot(settings(), exchange)  # type: ignore[arg-type]
    await bot.startup()
    assert "manual" in exchange.orders
    assert "orphan" not in exchange.orders
    await bot.shutdown()
    assert list(exchange.orders) == ["manual"]


async def test_trading_halt_pulls_quotes(exchange: FakeExchange) -> None:
    bot = LiquidityBot(settings(), exchange)  # type: ignore[arg-type]
    await bot.startup()
    await bot.cycle()
    assert exchange.orders
    exchange.trading_active = False
    bot._last_status = float("-inf")
    await bot.cycle()
    assert not exchange.orders


async def test_dry_run_sends_nothing(exchange: FakeExchange) -> None:
    cfg = settings(dry_run=True, loop={"interval_seconds": 0.01})
    bot = LiquidityBot(cfg, exchange)  # type: ignore[arg-type]
    await bot.run(max_cycles=2)
    assert not exchange.orders


async def test_session_loss_halts_and_cancels(exchange: FakeExchange) -> None:
    bot = LiquidityBot(
        settings(risk={"max_session_loss": 1}),
        exchange,  # type: ignore[arg-type]
    )
    await bot.startup()
    await bot.cycle()
    bid = next(o for o in exchange.orders.values() if o.side is Side.BID)
    exchange.fill(bid.order_id, D(10))  # long 10 @ 0.38
    # Market collapses: YES now 0.05 / 0.07.
    exchange.books[T] = make_book(yes=[("0.05", 200)], no=[("0.93", 200)])
    await bot.cycle()
    assert bot.risk.halted
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
    cfg = settings(loop={"status_check_interval_seconds": 0.01})
    bot = LiquidityBot(cfg, exchange)  # type: ignore[arg-type]
    await bot.startup()
    assert checks == 3
    await bot.cycle()
    assert exchange.orders  # quoting once the exchange is back
