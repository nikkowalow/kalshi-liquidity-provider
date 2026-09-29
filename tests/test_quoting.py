from decimal import Decimal

from kalshi_lp.config import QuotingConfig
from kalshi_lp.core.types import Leg, Side
from kalshi_lp.strategy.quoting import MarketContext, QuoteEngine
from kalshi_lp.strategy.rewards import RewardParams
from tests.factories import make_book, make_market

D = Decimal
MAX_POS = D(50)


def quote(cfg: QuotingConfig, reward: RewardParams, book, position=0, allow_increase=True):
    engine = QuoteEngine(cfg, MAX_POS)
    ctx = MarketContext(make_market(), book, D(position), reward, allow_increase)
    return engine.quote(ctx)


# A wide book: YES bids 0.40 (15) / 0.38 (100); NO bids 0.50 (15) / 0.48 (100).
# Best YES bid 0.40, best YES ask 0.50, mid 0.45. Reference depth = 100 / 5 = 20.
WIDE = make_book(yes=[("0.40", 15), ("0.38", 100)], no=[("0.50", 15), ("0.48", 100)])


def test_reward_placement_joins_when_deeper_queue_is_full(quoting_cfg, reward) -> None:
    # The reference price is 0.38, but 115 contracts rest ahead of an order
    # there, more than the Target Size of 100, so it would earn nothing.
    # Joining the best bid earns the most.
    d = quote(quoting_cfg, reward, WIDE)
    yes, no = d[Leg.YES].quote, d[Leg.NO].quote
    assert yes is not None and no is not None
    assert yes.side is Side.BID and yes.price == D("0.40")
    assert no.side is Side.ASK and no.price == D("0.50")  # NO bid 0.50
    assert yes.size == no.size == D(10)
    assert d[Leg.YES].score.share > D("0.2")


def test_reward_placement_backs_off_when_share_barely_changes(quoting_cfg, reward) -> None:
    # Thin top of book, then a wall at 0.30. From 0.35 up, our order earns
    # near-full credit, so the engine takes the deepest price within 10% of
    # the best share instead of sitting at the top.
    book = make_book(yes=[("0.40", 5), ("0.39", 10), ("0.30", 200)], no=[("0.50", 200)])
    d = quote(quoting_cfg, reward, book)
    assert d[Leg.YES].quote.price == D("0.35")
    at_top = quote(quoting_cfg.model_copy(update={"placement": "join"}), reward, book)
    assert d[Leg.YES].score.share >= at_top[Leg.YES].score.share * D("0.9")


def test_join_and_improve(quoting_cfg, reward) -> None:
    join = quote(quoting_cfg.model_copy(update={"placement": "join"}), reward, WIDE)
    assert join[Leg.YES].quote.price == D("0.40")
    assert join[Leg.NO].quote.price == D("0.50")
    improve = quote(quoting_cfg.model_copy(update={"placement": "improve"}), reward, WIDE)
    assert improve[Leg.YES].quote.price == D("0.41")
    assert improve[Leg.NO].quote.price == D("0.49")


def test_never_crosses_or_quotes_inside_min_edge(quoting_cfg, reward) -> None:
    # 1c-wide market: 0.49 / 0.50. Improve would cross; min_edge 0.005 allows joining.
    tight = make_book(yes=[("0.49", 50)], no=[("0.50", 50)])
    d = quote(quoting_cfg.model_copy(update={"placement": "improve"}), reward, tight)
    assert d[Leg.YES].quote.price == D("0.49")
    assert d[Leg.NO].quote.price == D("0.50")
    # A wider min_edge pushes both sides back a tick.
    d = quote(quoting_cfg.model_copy(update={"min_edge": D("0.01")}), reward, tight)
    assert d[Leg.YES].quote.price == D("0.48")
    assert d[Leg.NO].quote.price == D("0.51")


def test_inventory_skew_shifts_both_quotes_down_when_long(quoting_cfg, reward) -> None:
    cfg = quoting_cfg.model_copy(update={"placement": "join", "skew_per_contract": D("0.001")})
    flat = quote(cfg, reward, WIDE)
    long = quote(cfg, reward, WIDE, position=20)  # 20 * 0.001 = 2c of skew
    assert long[Leg.YES].quote.price == flat[Leg.YES].quote.price - D("0.02")
    # Selling YES gets more aggressive (lower ask) but may not cross.
    assert long[Leg.NO].quote.price < flat[Leg.NO].quote.price


def test_position_limit_stops_the_increasing_side(quoting_cfg, reward) -> None:
    d = quote(quoting_cfg, reward, WIDE, position=50)
    assert d[Leg.YES].quote is None
    assert d[Leg.YES].reason == "position limit"
    assert d[Leg.NO].quote is not None

    d = quote(quoting_cfg, reward, WIDE, position=45)
    assert d[Leg.YES].quote.size == D(5)


def test_reduce_only(quoting_cfg, reward) -> None:
    d = quote(quoting_cfg, reward, WIDE, position=4, allow_increase=False)
    assert d[Leg.YES].quote is None
    assert d[Leg.NO].quote.size == D(4)

    d = quote(quoting_cfg, reward, WIDE, position=0, allow_increase=False)
    assert d[Leg.YES].quote is None and d[Leg.NO].quote is None


def test_price_floor(quoting_cfg, reward) -> None:
    # YES trades near 0.02: the YES leg would bid below min_price.
    cheap = make_book(yes=[("0.02", 100)], no=[("0.96", 100)])
    d = quote(quoting_cfg, reward, cheap)
    assert d[Leg.YES].quote is None
    assert "floor" in d[Leg.YES].reason


def test_one_sided_book_prices_off_the_other_side(quoting_cfg, reward) -> None:
    book = make_book(yes=[], no=[("0.50", 100)])  # best YES ask 0.50
    d = quote(quoting_cfg, reward, book)
    assert d[Leg.YES].quote.price == D("0.45")  # 0.50 - one_sided_edge


def test_empty_book_no_quotes(quoting_cfg, reward) -> None:
    d = quote(quoting_cfg, reward, make_book(yes=[], no=[]))
    assert d[Leg.YES].quote is None and d[Leg.NO].quote is None


def test_quotes_never_self_cross(quoting_cfg, reward) -> None:
    cfg = quoting_cfg.model_copy(update={"min_edge": D(0), "placement": "improve"})
    book = make_book(yes=[("0.49", 50)], no=[("0.50", 50)])
    d = quote(cfg, reward, book)
    yes, no = d[Leg.YES].quote, d[Leg.NO].quote
    assert yes is not None and no is not None
    assert yes.price < no.price  # bid below ask
