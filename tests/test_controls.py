"""The dashboard's control buttons, against a bot on the fake exchange."""

import asyncio
from decimal import Decimal

import pytest

from kalshi_lp.core.types import Side
from kalshi_lp.engine.controls import BotControls, ControlError
from tests.factories import make_book, make_market
from tests.fake_exchange import FakeExchange
from tests.test_bot import T, started, step

D = Decimal


@pytest.fixture
def exchange() -> FakeExchange:
    book = make_book(yes=[("0.40", 15), ("0.38", 100)], no=[("0.50", 15), ("0.48", 100)])
    return FakeExchange([make_market(T)], {T: book})


async def test_pause_pulls_every_order_until_resume(exchange: FakeExchange) -> None:
    bot, feed = await started(exchange)
    await step(bot, feed)
    assert len(exchange.orders) == 2
    controls = BotControls(bot)

    assert await controls.pause() == "paused: 2 orders cancelled, nothing quoted until you resume"
    assert not exchange.orders
    await step(bot, feed)
    assert not exchange.orders  # stays dark
    assert bot._decide(T, bot._risk_view()) == "paused from the dashboard"
    assert bot.snapshot()["held_since"] is not None
    assert await controls.pause() == "already paused"

    assert await controls.resume() == "resumed: quoting again"
    assert T in bot.state.dirty  # requoted at once, not at the next heartbeat
    await step(bot, feed)
    assert len(exchange.orders) == 2
    assert bot.snapshot()["held_since"] is None
    assert await controls.resume() == "not paused"


async def test_paused_bot_still_closes_a_filled_position(exchange: FakeExchange) -> None:
    bot, feed = await started(exchange, risk={"flatten_on_fill": True})
    await step(bot, feed)
    bid = next(o for o in exchange.orders.values() if o.side is Side.BID)
    exchange.fill(bid.order_id, D(10))
    await BotControls(bot).pause()
    await step(bot, feed)
    assert exchange.positions[T].position == 0
    assert not exchange.orders


async def test_flatten_closes_positions_even_without_flatten_on_fill(
    exchange: FakeExchange,
) -> None:
    bot, feed = await started(exchange)  # flatten_on_fill off: fills are normally held
    await step(bot, feed)
    bid = next(o for o in exchange.orders.values() if o.side is Side.BID)
    exchange.fill(bid.order_id, D(10))
    controls = BotControls(bot)
    assert await controls.flatten() == "closed 1 position(s)"
    assert exchange.positions[T].position == 0
    assert exchange.exits[-1].side is Side.ASK
    assert await controls.flatten() == "no open positions"


async def test_flatten_in_dry_run_says_there_is_nothing_real(exchange: FakeExchange) -> None:
    bot, _ = await started(exchange, dry_run=True)
    with pytest.raises(ControlError, match="dry run"):
        await BotControls(bot).flatten()


async def test_budget_change_resizes_quotes_and_rescans(exchange: FakeExchange) -> None:
    # Book 0.40 / 0.50: a contract per side locks at most $0.90.
    bot, feed = await started(
        exchange,
        quoting={"auto_size": True, "capital_utilization": 1},
        risk={"max_capital": 9, "max_position_per_market": 100},
    )
    await step(bot, feed)
    assert {o.remaining for o in exchange.orders.values()} == {D(10)}
    controls = BotControls(bot)

    message = await controls.set_budget(D(18))
    assert message.startswith("budget $9 -> $18.00 until restart")
    assert bot.settings.risk.max_capital == bot.selector.max_capital == D("18.00")
    assert bot.snapshot()["totals"]["max_capital"] == D("18.00")
    assert bot.snapshot()["config"]["risk"]["max_capital"] == "18.00"
    assert bot._rescan_requested
    await step(bot, feed)
    # Each side tops its resting 10 up to 20 with a second order (keeping queue position).
    for side in Side:
        assert sum(o.remaining for o in exchange.orders.values() if o.side is side) == 20


async def test_budget_rejects_nonsense(exchange: FakeExchange) -> None:
    bot, _ = await started(exchange, risk={"max_capital": 9})
    controls = BotControls(bot)
    for bad in (D(0), D(-5), D("NaN")):
        with pytest.raises(ControlError, match="more than \\$0"):
            await controls.set_budget(bad)
    with pytest.raises(ControlError, match="more than the account holds"):
        await controls.set_budget(D(10_000))  # the fake account holds $1000: a typo
    assert bot.settings.risk.max_capital == 9


async def test_rescan_runs_within_a_second(exchange: FakeExchange) -> None:
    bot, _ = await started(exchange)
    scans = []

    async def scan() -> None:
        scans.append(1)
        bot.stop()

    bot._reselect_job = scan  # type: ignore[method-assign]
    loop = asyncio.create_task(bot._maintenance_loop())
    assert (await BotControls(bot).rescan()).startswith("re-ranking markets now")
    await asyncio.wait_for(loop, timeout=3)
    assert scans == [1]


async def test_a_crashing_maintenance_job_does_not_stop_scans(
    exchange: FakeExchange, caplog: pytest.LogCaptureFixture
) -> None:
    bot, _ = await started(exchange, loop={"queue_refresh_seconds": 0.5})

    async def broken() -> None:
        raise KeyError("an odd API response")

    scans = []

    async def scan() -> None:
        scans.append(1)
        bot.stop()

    bot._refresh_queue = broken  # type: ignore[method-assign]
    bot._reselect_job = scan  # type: ignore[method-assign]
    loop = asyncio.create_task(bot._maintenance_loop())
    await asyncio.sleep(1.3)  # the broken job has run, and failed
    assert "broken crashed" in caplog.text
    bot.request_rescan()
    await asyncio.wait_for(loop, timeout=3)  # still alive: the rescan ran
    assert scans == [1]


async def test_stop_shuts_the_bot_down(exchange: FakeExchange) -> None:
    bot, _ = await started(exchange)
    assert (await BotControls(bot).stop()).startswith("stopping")
    assert bot.stopping
