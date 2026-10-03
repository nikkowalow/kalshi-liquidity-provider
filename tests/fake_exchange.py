"""In-memory stand-in for :class:`KalshiClient`, for end-to-end bot tests."""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from decimal import Decimal

from kalshi_lp.core.orderbook import Orderbook
from kalshi_lp.core.types import Quote
from kalshi_lp.exchange.client import OrderResult
from kalshi_lp.exchange.models import (
    Balance,
    ExchangeStatus,
    IncentiveProgram,
    Market,
    Order,
    Position,
    Trade,
)
from kalshi_lp.feed.state import MarketState


class FakeExchange:
    def __init__(self, markets: Sequence[Market], books: dict[str, Orderbook]):
        self.markets = {m.ticker: m for m in markets}
        self.books = books
        self.orders: dict[str, Order] = {}
        self.positions: dict[str, Position] = {}
        self.programs: list[IncentiveProgram] = []
        self.balance = Decimal(1000)
        self.trading_active = True
        self.cancel_all_calls = 0
        self.exits: list[Quote] = []
        self.unwinds: list[Quote] = []  # passive exit orders placed
        self.program_calls = 0
        self.trades: dict[str, list[Trade]] = {}  # public trades per market
        self.trade_calls: list[tuple[str, float | None]] = []  # (ticker, min_ts) per get_trades
        self._ids = itertools.count(1)

    # reads
    async def get_exchange_status(self) -> ExchangeStatus:
        return ExchangeStatus(True, self.trading_active)

    async def get_balance(self) -> Balance:
        return Balance(self.balance, Decimal(0))

    async def get_resting_orders(self, ticker: str | None = None) -> list[Order]:
        return [o for o in self.orders.values() if ticker is None or o.ticker == ticker]

    async def get_positions(self, ticker: str | None = None) -> dict[str, Position]:
        return dict(self.positions)

    async def get_orderbooks(self, tickers: Sequence[str]) -> dict[str, Orderbook]:
        return {t: self._book_with_orders(t) for t in tickers if t in self.books}

    async def get_incentive_programs(self, **_: object) -> list[IncentiveProgram]:
        self.program_calls += 1
        return list(self.programs)

    async def get_markets(self, *, tickers=None, **_: object) -> list[Market]:
        if tickers is None:
            return list(self.markets.values())
        return [self.markets[t] for t in tickers if t in self.markets]

    def _book_with_orders(self, ticker: str) -> Orderbook:
        """The public book includes our resting orders, as on the real exchange."""
        book = self.books[ticker]
        yes = {lvl.price: lvl.size for lvl in book.yes}
        no = {lvl.price: lvl.size for lvl in book.no}
        for o in self.orders.values():
            if o.ticker == ticker:
                side = yes if o.leg.value == "yes" else no
                side[o.leg_price] = side.get(o.leg_price, Decimal(0)) + o.remaining
        return Orderbook.from_api(
            ticker,
            {
                "orderbook_fp": {
                    "yes_dollars": [[str(p), str(s)] for p, s in yes.items()],
                    "no_dollars": [[str(p), str(s)] for p, s in no.items()],
                }
            },
        )

    # writes
    async def batch_create_orders(
        self, orders: Sequence[tuple[Quote, str]], **_: object
    ) -> list[OrderResult]:
        results = []
        for quote, coid in orders:
            oid = f"o{next(self._ids)}"
            self.orders[oid] = Order(
                oid, coid, quote.ticker, quote.side, quote.price, quote.size, "resting"
            )
            results.append(OrderResult(oid, coid))
        return results

    async def create_order(
        self, quote: Quote, client_order_id: str, *, time_in_force: str = "", **_: object
    ) -> OrderResult:
        """Immediate-or-cancel exits fill in full at their limit (enough liquidity assumed).

        Anything else (a passive exit) rests like a quote.
        """
        if time_in_force != "immediate_or_cancel":
            oid = f"o{next(self._ids)}"
            self.orders[oid] = Order(
                oid, client_order_id, quote.ticker, quote.side, quote.price, quote.size, "resting"
            )
            self.unwinds.append(quote)
            return OrderResult(oid, client_order_id)
        self.exits.append(quote)
        signed = quote.size if quote.side.value == "bid" else -quote.size
        prev = self.positions.get(quote.ticker, Position.flat(quote.ticker))
        position = prev.position + signed
        exposure = Decimal(0) if position == 0 else prev.market_exposure
        self.positions[quote.ticker] = Position(
            quote.ticker, position, exposure, Decimal(0), Decimal(0)
        )
        return OrderResult(f"x{next(self._ids)}", client_order_id, filled=quote.size)

    async def get_trades(
        self, ticker: str, *, min_ts: float | None = None, max_items: int = 5000
    ) -> list[Trade]:
        self.trade_calls.append((ticker, min_ts))
        trades = [
            t for t in self.trades.get(ticker, []) if min_ts is None or t.ts.timestamp() >= min_ts
        ]
        return sorted(trades, key=lambda t: t.ts, reverse=True)[:max_items]

    async def batch_cancel_orders(self, orders: Sequence[Order]) -> list[OrderResult]:
        return [
            OrderResult(o.order_id, o.client_order_id)
            for o in orders
            if self.orders.pop(o.order_id, None) is not None
        ]

    async def decrease_order(self, order: Order, reduce_to: Decimal) -> OrderResult:
        o = self.orders[order.order_id]
        self.orders[o.order_id] = Order(
            o.order_id, o.client_order_id, o.ticker, o.side, o.yes_price, reduce_to, o.status
        )
        return OrderResult(o.order_id, o.client_order_id)

    async def cancel_all_orders(self) -> None:
        self.cancel_all_calls += 1
        self.orders.clear()

    async def create_order_group(self, contracts_limit: int) -> str:
        return "group-1"

    async def reset_order_group(self, order_group_id: str) -> None:
        pass

    async def delete_order_group(self, order_group_id: str) -> None:
        pass

    # test helpers
    def fill(self, order_id: str, count: Decimal) -> None:
        """Simulate a counterparty trading against one of our orders."""
        o = self.orders.pop(order_id)
        signed = count if o.side.value == "bid" else -count
        cost = count * (o.yes_price if o.side.value == "bid" else 1 - o.yes_price)
        prev = self.positions.get(o.ticker, Position.flat(o.ticker))
        self.positions[o.ticker] = Position(
            o.ticker, prev.position + signed, prev.market_exposure + cost, Decimal(0), Decimal(0)
        )
        if o.remaining > count:
            self.orders[order_id] = Order(
                o.order_id,
                o.client_order_id,
                o.ticker,
                o.side,
                o.yes_price,
                o.remaining - count,
                o.status,
            )


class FakeFeed:
    """A perfectly synced stand-in for :class:`StreamingFeed` over a :class:`FakeExchange`.

    Call :meth:`sync` to deliver what the WebSocket would have delivered:
    books (including our orders), our resting orders, and positions.
    """

    def __init__(self, exchange: FakeExchange, prefix: str = "klp"):
        self.exchange = exchange
        self.state = MarketState(prefix)
        self.tickers: set[str] = set()
        self.connected = True

    async def start(self, tickers) -> None:
        self.tickers = set(tickers)
        self.sync()

    async def stop(self) -> None:
        pass

    async def set_tickers(self, tickers) -> None:
        self.tickers = set(tickers)
        self.sync()

    def sync(self) -> None:
        for ticker in self.tickers:
            if ticker in self.exchange.books:
                book = self.exchange._book_with_orders(ticker)
                self.state.set_snapshot(
                    ticker,
                    [(lvl.price, lvl.size) for lvl in book.yes],
                    [(lvl.price, lvl.size) for lvl in book.no],
                )
        self.state.replace_orders(self.exchange.orders.values())
        self.state.replace_positions(self.exchange.positions)
