"""Live state and WebSocket message handling."""

from decimal import Decimal

from kalshi_lp.core.types import Side
from kalshi_lp.feed.state import MarketState
from kalshi_lp.feed.stream import StreamingFeed
from tests.factories import make_order, make_position

D = Decimal
T = "TEST-MKT"


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


def feed() -> StreamingFeed:
    f = StreamingFeed("wss://unused.test/trade-api/ws/v2", None, MarketState("klp"))
    f.tickers = {T}
    return f


def msg(kind: str, **body) -> dict:
    return {"type": kind, "sid": 1, "msg": body}


# ------------------------------------------------------------------- books


def test_snapshot_then_deltas() -> None:
    f = feed()
    f._on_message(
        msg(
            "orderbook_snapshot",
            market_ticker=T,
            yes_dollars_fp=[["0.4000", "10.00"]],
            no_dollars_fp=[["0.5500", "5.00"]],
        )
    )
    f._on_message(
        msg("orderbook_delta", market_ticker=T, side="yes", price_dollars="0.4100", delta_fp="3.00")
    )
    f._on_message(
        msg("orderbook_delta", market_ticker=T, side="no", price_dollars="0.5500", delta_fp="-5.00")
    )
    book = f.state.book(T)
    assert book is not None
    assert book.best_yes_bid == D("0.41")
    assert book.no == ()  # level removed when it hits zero
    assert T in f.state.take_dirty()


def test_book_untrusted_until_snapshot_and_after_invalidate() -> None:
    f = feed()
    f._on_message(
        msg("orderbook_delta", market_ticker=T, side="yes", price_dollars="0.40", delta_fp="3")
    )
    assert f.state.book(T) is None  # delta before snapshot is ignored
    f._on_message(msg("orderbook_snapshot", market_ticker=T, yes_dollars_fp=[], no_dollars_fp=[]))
    assert f.state.book(T) is not None
    f.state.invalidate()
    assert f.state.book(T) is None


def test_snapshot_for_unfollowed_market_ignored() -> None:
    f = feed()
    f._on_message(msg("orderbook_snapshot", market_ticker="OTHER", yes_dollars_fp=[]))
    assert f.state.book("OTHER") is None


# ------------------------------------------------------------------ orders


def user_order(status: str, remaining: str = "10.00", coid: str = "klp-1") -> dict:
    return msg(
        "user_order",
        order_id="o1",
        client_order_id=coid,
        ticker=T,
        status=status,
        book_side="bid",
        yes_price_dollars="0.4000",
        remaining_count_fp=remaining,
    )


def test_user_orders_track_our_orders_only() -> None:
    f = feed()
    f._on_message(user_order("resting", coid="someone-else"))
    assert not f.state.orders
    f._on_message(user_order("resting"))
    assert f.state.orders["o1"].remaining == D(10)
    f._on_message(user_order("resting", remaining="4.00"))  # partial fill
    assert f.state.orders["o1"].remaining == D(4)
    f._on_message(user_order("executed", remaining="0.00"))
    assert not f.state.orders


def test_tombstones_block_stale_resurrection() -> None:
    state = MarketState("klp", clock=Clock())
    order = make_order(Side.BID, "0.4000", 10, order_id="o1", client_order_id="klp-1")
    state.upsert_order(order)
    state.remove_order("o1")  # e.g. the stream said it was cancelled
    state.replace_orders([order])  # a REST snapshot taken just before still lists it
    assert not state.orders
    state.upsert_order(order)  # a late REST create response
    assert not state.orders


# --------------------------------------------------------------- positions


def test_fill_updates_live_position_only() -> None:
    f = feed()
    f.state.replace_positions({T: make_position(position=0, exposure="0")})
    f._on_message(
        msg(
            "fill",
            market_ticker=T,
            count_fp="5.00",
            post_position_fp="5.00",
            yes_price_dollars="0.40",
            book_side="bid",
        )
    )
    assert f.state.position(T) == D(5)  # quoting sees the fill instantly
    assert f.state.positions[T].position == 0  # risk waits for a consistent cost update


def test_market_position_updates_both_views() -> None:
    f = feed()
    f._on_message(
        msg(
            "market_position",
            market_ticker=T,
            position_fp="5.00",
            position_cost_dollars="2.0000",
            realized_pnl_dollars="0.5000",
            fees_paid_dollars="0.0100",
        )
    )
    pos = f.state.positions[T]
    assert (pos.position, pos.market_exposure, pos.realized_pnl) == (D(5), D(2), D("0.5"))
    assert f.state.position(T) == D(5)
