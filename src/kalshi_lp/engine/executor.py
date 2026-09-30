"""Send a :class:`Plan` to the exchange (or just log it in dry-run mode).

Order of operations: cancels first (frees collateral and prevents a stale
order from trading against a new one), then decreases, then creates. Every
order the bot places carries a ``client_order_id`` with our prefix so the bot
can recognise, and clean up, its own orders across restarts.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from kalshi_lp.core.types import Quote
from kalshi_lp.engine.reconciler import Plan
from kalshi_lp.exchange.client import KalshiClient
from kalshi_lp.exchange.errors import KalshiAPIError
from kalshi_lp.exchange.models import Order

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ExecutionReport:
    cancelled: int = 0
    decreased: int = 0
    created: int = 0
    rejected: int = 0
    errors: int = 0
    group_blocked: bool = False  # the exchange order group tripped and is refusing orders
    # What changed, so the caller can update its live order state immediately.
    placed: list[Order] = field(default_factory=list)
    gone: list[Order] = field(default_factory=list)  # cancelled
    resized: list[Order] = field(default_factory=list)
    rejections: list[tuple[Quote, str]] = field(default_factory=list)
    exited: list[tuple[Quote, Decimal]] = field(
        default_factory=list
    )  # exit order, contracts filled


class OrderExecutor:
    def __init__(
        self,
        client: KalshiClient,
        *,
        prefix: str,
        dry_run: bool,
        post_only: bool = True,
        max_batch: int = 10,
    ):
        self.client = client
        self.prefix = prefix
        self.dry_run = dry_run
        self.post_only = post_only
        self.max_batch = max(1, max_batch)
        self.order_group_id: str | None = None

    def new_client_order_id(self) -> str:
        return f"{self.prefix}-{uuid.uuid4().hex[:24]}"

    def is_ours(self, order: Order) -> bool:
        return order.client_order_id.startswith(f"{self.prefix}-")

    async def execute(self, plan: Plan) -> ExecutionReport:
        report = ExecutionReport()
        if not plan:
            return report
        if self.dry_run:
            for o in plan.cancels:
                log.info(
                    "[dry-run] cancel %s %s %s @ %s", o.ticker, o.side, o.remaining, o.yes_price
                )
            for o, size in plan.decreases:
                log.info("[dry-run] decrease %s %s -> %s", o.ticker, o.order_id, size)
            for q in plan.creates:
                log.info("[dry-run] place %s %s", q.ticker, q)
            for q in plan.exits:
                log.info("[dry-run] exit %s %s", q.ticker, q)
            return report

        await self._cancel(plan.cancels, report)
        for order, size in plan.decreases:
            try:
                await self.client.decrease_order(order, size)
                report.decreased += 1
                report.resized.append(
                    Order(
                        order.order_id,
                        order.client_order_id,
                        order.ticker,
                        order.side,
                        order.yes_price,
                        size,
                        order.status,
                    )
                )
            except KalshiAPIError as exc:
                report.errors += 1
                log.warning("decrease %s failed: %s", order.order_id, exc)
        await self._exit(plan.exits, report)
        await self._create(plan, report)
        return report

    async def _exit(self, quotes: list[Quote], report: ExecutionReport) -> None:
        """Close positions now: crossing, immediate-or-cancel, outside the order group.

        Taker orders on purpose (the point is to trade), and outside the order
        group so an exit can never trip the fill kill-switch meant for quotes.
        """
        for quote in quotes:
            try:
                r = await self.client.create_order(
                    quote,
                    self.new_client_order_id(),
                    post_only=False,
                    time_in_force="immediate_or_cancel",
                )
            except KalshiAPIError as exc:
                report.errors += 1
                report.rejections.append((quote, str(exc)))
                log.warning("exit %s %s failed: %s", quote.ticker, quote, exc)
                continue
            report.exited.append((quote, r.filled))
            log.info("exit %s %s: filled %s", quote.ticker, quote, r.filled)

    async def _cancel(self, orders: list[Order], report: ExecutionReport) -> None:
        for i in range(0, len(orders), self.max_batch * 5):  # cancels are 5x cheaper
            chunk = orders[i : i + self.max_batch * 5]
            try:
                results = await self.client.batch_cancel_orders(chunk)
            except KalshiAPIError as exc:
                report.errors += 1
                log.error("batch cancel failed: %s", exc)
                continue
            by_id = {o.order_id: o for o in chunk}
            for r in results:
                if r.ok:
                    report.cancelled += 1
                    if r.order_id in by_id:
                        report.gone.append(by_id[r.order_id])
                else:  # usually "already filled/cancelled": harmless
                    log.info("cancel %s: %s", r.order_id, r.error)

    async def _create(self, plan: Plan, report: ExecutionReport) -> None:
        quotes = plan.creates
        for i in range(0, len(quotes), self.max_batch):
            chunk = [(q, self.new_client_order_id()) for q in quotes[i : i + self.max_batch]]
            try:
                results = await self.client.batch_create_orders(
                    chunk, post_only=self.post_only, order_group_id=self.order_group_id
                )
            except KalshiAPIError as exc:
                report.errors += 1
                log.error("batch create failed: %s", exc)
                continue
            for (quote, coid), r in zip(chunk, results, strict=False):
                if r.ok:
                    report.created += 1
                    log.info("placed %s %s", quote.ticker, quote)
                    if r.filled > 0:
                        log.warning("%s filled %s on placement", quote.ticker, r.filled)
                    remaining = quote.size - r.filled
                    if r.order_id and remaining > Decimal(0):
                        report.placed.append(
                            Order(
                                r.order_id,
                                coid,
                                quote.ticker,
                                quote.side,
                                quote.price,
                                remaining,
                                "resting",
                            )
                        )
                else:
                    # post-only rejections are expected when the book moves under us
                    report.rejected += 1
                    report.rejections.append((quote, r.error or "unknown"))
                    log.info("rejected %s %s: %s", quote.ticker, quote, r.error)
                    if r.error and "group" in r.error.lower():
                        report.group_blocked = True
