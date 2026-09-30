"""Order executor: how exchange refusals are counted."""

from decimal import Decimal

from kalshi_lp.core.types import Side
from kalshi_lp.engine.executor import OrderExecutor
from kalshi_lp.engine.reconciler import Plan
from kalshi_lp.exchange.errors import KalshiAPIError
from kalshi_lp.exchange.models import Order

D = Decimal


class RefusingClient:
    def __init__(self, error: KalshiAPIError):
        self.error = error

    async def decrease_order(self, order: Order, reduce_to: Decimal) -> None:
        raise self.error


def order() -> Order:
    return Order("o-1", "klp-1", "T", Side.BID, D("0.40"), D(10), "resting")


async def decrease_with(error: KalshiAPIError):
    executor = OrderExecutor(RefusingClient(error), prefix="klp", dry_run=False)  # type: ignore[arg-type]
    return await executor.execute(Plan(decreases=[(order(), D(6))]))


async def test_decrease_to_the_size_it_already_has_is_not_an_error() -> None:
    # Our copy was stale: the exchange already has 6. Counting this as an error made the bot
    # retry every requote until the error breaker paused all quoting for five minutes.
    report = await decrease_with(KalshiAPIError(400, "AMEND_ORDER_NO_OP", "AMEND_ORDER_NO_OP"))
    assert report.errors == 0
    assert [(o.order_id, o.remaining) for o in report.resized] == [("o-1", D(6))]


async def test_decrease_of_an_order_that_is_gone_drops_it() -> None:
    report = await decrease_with(KalshiAPIError(404, "not_found", "order not found"))
    assert report.errors == 0
    assert [o.order_id for o in report.gone] == ["o-1"]


async def test_other_decrease_failures_still_count() -> None:
    report = await decrease_with(KalshiAPIError(400, "invalid_parameters", "bad count"))
    assert report.errors == 1 and not report.resized and not report.gone
