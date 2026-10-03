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


def test_sticky_quote_keeps_queue_position(quoting_cfg, reward) -> None:
    from kalshi_lp.strategy.rewards import OwnOrder

    engine = QuoteEngine(quoting_cfg, MAX_POS)
    book = make_book(yes=[("0.40", 5), ("0.39", 10), ("0.30", 200)], no=[("0.50", 200)])
    fresh = engine.quote(MarketContext(make_market(), book, D(0), reward))[Leg.YES]
    assert fresh.quote.price == D("0.35")

    # Resting at 0.34 with 0 ahead: a tick deeper, share within tolerance -> keep it.
    kept = engine.quote(
        MarketContext(
            make_market(), book, D(0), reward, resting={Leg.YES: OwnOrder(D("0.34"), D(10), D(0))}
        )
    )[Leg.YES]
    assert kept.quote.price == D("0.34") and "kept" in kept.reason

    # Resting at 0.31: share has fallen too far -> move.
    moved = engine.quote(
        MarketContext(
            make_market(), book, D(0), reward, resting={Leg.YES: OwnOrder(D("0.31"), D(10), D(0))}
        )
    )[Leg.YES]
    assert moved.quote.price == D("0.35")

    # Never keep a price more aggressive than we'd choose now.
    riskier = engine.quote(
        MarketContext(
            make_market(), book, D(0), reward, resting={Leg.YES: OwnOrder(D("0.39"), D(10), D(0))}
        )
    )[Leg.YES]
    assert riskier.quote.price == D("0.35")


def test_cushion_keeps_others_ahead_of_us(quoting_cfg) -> None:
    reward = RewardParams(target_size=D(1000), discount_factor=D("0.5"))  # full credit to 200
    cfg = quoting_cfg.model_copy(update={"min_cushion": D(50), "placement": "join"})
    # YES bids: 20 @ 0.40, 40 @ 0.39, 100 @ 0.38 -> 50 contracts ahead first reached at 0.39.
    book = make_book(yes=[("0.40", 20), ("0.39", 40), ("0.38", 100)], no=[("0.55", 200)])
    d = quote(cfg, reward, book)
    assert d[Leg.YES].quote.price == D("0.39")  # joins behind 60 contracts, not at the top
    thin = make_book(yes=[("0.40", 20)], no=[("0.55", 200)])
    d = quote(cfg, reward, thin)
    assert d[Leg.YES].quote is None and "cushion" in d[Leg.YES].reason


def test_young_orders_are_not_moved_for_reward_but_are_for_safety(quoting_cfg, reward) -> None:
    from kalshi_lp.strategy.rewards import OwnOrder

    cfg = quoting_cfg.model_copy(update={"min_quote_life_seconds": 10})
    engine = QuoteEngine(cfg, MAX_POS)
    book = make_book(yes=[("0.40", 5), ("0.39", 10), ("0.30", 200)], no=[("0.50", 200)])

    def at(price: str, age: float, position: int = 0):
        ctx = MarketContext(
            make_market(),
            book,
            D(position),
            reward,
            resting={Leg.YES: OwnOrder(D(price), D(10), D(0))},
            resting_age={Leg.YES: age},
        )
        return engine.quote(ctx)[Leg.YES].quote.price

    assert at("0.31", age=2) == D("0.31")  # young: stays even though 0.35 earns more
    assert at("0.31", age=20) == D("0.35")  # old enough: moves
    assert at("0.39", age=2) == D("0.39")  # young and flat: stays
    assert at("0.39", age=2, position=10) < D("0.39")  # holding inventory: back off now
    assert at("0.46", age=2) != D("0.46")  # above the cap (inside min_edge): unsafe, moves


def test_cushion_never_exceeds_full_credit_depth(quoting_cfg) -> None:
    # Target 300 -> full credit for the first 60 contracts. A 100 cushion would
    # force a discounted price; it's capped at 60 instead.
    small = RewardParams(target_size=D(300), discount_factor=D("0.5"), reward_per_day=D(50))
    cfg = quoting_cfg.model_copy(update={"min_cushion": D(100), "placement": "join"})
    book = make_book(yes=[("0.40", 30), ("0.39", 40), ("0.38", 400)], no=[("0.55", 400)])
    d = quote(cfg, small, book)
    assert d[Leg.YES].quote.price == D("0.39")  # 70 ahead >= 60; not pushed down to 0.38
    assert d[Leg.YES].score.share > 0


def test_loss_cap_limits_what_one_fill_can_lose(quoting_cfg, reward) -> None:
    # YES bid at 0.40: a fill loses at most 0.40/contract, so $2 allows 5 contracts.
    # NO bid at 0.49 (a YES ask of 0.51): at most 0.49/contract, so 4. (Four contracts can't
    # lift the full-credit price the way 20 would, so the deeper 0.49 earns as much as 0.50.)
    cfg = quoting_cfg.model_copy(update={"max_loss_per_fill": D(2)})
    d = quote(cfg, reward, WIDE)
    yes, no = d[Leg.YES].quote, d[Leg.NO].quote
    assert yes is not None and no is not None
    assert (yes.price, yes.size) == (D("0.40"), D(5))
    assert (no.price, no.size) == (D("0.51"), D(4))
    tiny = quoting_cfg.model_copy(update={"max_loss_per_fill": D("0.2")})
    assert quote(tiny, reward, WIDE)[Leg.YES].reason == "loss cap"  # not even one contract


def test_loss_cap_doesnt_limit_buying_back_a_short(quoting_cfg, reward) -> None:
    cfg = quoting_cfg.model_copy(update={"max_loss_per_fill": D(2)})
    d = quote(cfg, reward, WIDE, position=-10)  # short YES: the YES bid reduces it
    yes = d[Leg.YES].quote
    assert yes is not None and yes.size == D(10)


# ------------------------------------------------------- staying inside Target Size

BIG = RewardParams(target_size=D(1000), discount_factor=D("0.5"), reward_per_day=D(50))
# Like KXGRUBHUBAPP: 101 contracts at 0.46, then a wall of 1271 at 0.45.
WALL = make_book(yes=[("0.46", 101), ("0.45", 1271), ("0.44", 500)], no=[("0.50", 1200)])


def prod_like(quoting_cfg: QuotingConfig) -> QuotingConfig:
    """Sits a tick back behind a 100-contract cushion, like config/prod.yaml."""
    return quoting_cfg.model_copy(
        update={"offset_ticks": 1, "min_cushion": D(100), "share_tolerance": D("0.4")}
    )


def yes_leg(cfg: QuotingConfig, book, **ctx):
    return QuoteEngine(cfg, MAX_POS).quote(MarketContext(make_market(), book, reward=BIG, **ctx))[
        Leg.YES
    ]


def test_offset_that_steps_outside_target_size_is_pulled_back_in(quoting_cfg) -> None:
    # The best reward price is 0.46; a tick back is 0.45, behind 1372 contracts: past the
    # first 1000 the program counts, so it would earn nothing. Climb back to 0.46.
    d = yes_leg(prod_like(quoting_cfg), WALL, position=D(0))
    assert d.quote is not None and d.quote.price == D("0.46")
    assert "into target" in d.reason and d.score.share > 0
    no_offset = yes_leg(quoting_cfg.model_copy(update={"min_cushion": D(100)}), WALL, position=D(0))
    assert no_offset.quote.price == D("0.46") and "into target" not in no_offset.reason


def test_leg_is_pulled_when_target_size_is_out_of_reach(quoting_cfg) -> None:
    # 1200 contracts at the best bid alone fill the 1000 counted: getting in would mean bidding
    # above the best, with no cushion ahead, which the safety cap forbids. It would earn nothing,
    # so it isn't quoted.
    book = make_book(yes=[("0.46", 1200), ("0.45", 100)], no=[("0.50", 300)])
    d = yes_leg(prod_like(quoting_cfg), book, position=D(0))
    assert d.quote is None and "outside Target Size" in d.reason


def test_resting_order_that_counts_by_queue_priority_is_kept(quoting_cfg) -> None:
    from kalshi_lp.strategy.rewards import OwnOrder

    # Same wall at the top, but our order was there first: 300 ahead of it, not 1200.
    book = make_book(yes=[("0.46", 1200), ("0.45", 100)], no=[("0.50", 300)])
    resting = {Leg.YES: OwnOrder(D("0.46"), D(10), D(300))}
    d = yes_leg(prod_like(quoting_cfg), book, position=D(0), resting=resting)
    assert d.quote is not None and d.quote.price == D("0.46")
    assert "in target" in d.reason and d.score.share > 0


def test_resting_order_outside_target_size_moves_in_even_when_young(quoting_cfg) -> None:
    from kalshi_lp.strategy.rewards import OwnOrder

    cfg = prod_like(quoting_cfg).model_copy(update={"min_quote_life_seconds": 10})
    d = yes_leg(
        cfg,
        WALL,
        position=D(0),
        resting={Leg.YES: OwnOrder(D("0.45"), D(10), D(1271))},  # behind the whole wall
        resting_age={Leg.YES: 2.0},
    )
    assert d.quote.price == D("0.46")


def test_holding_the_leg_never_climbs_into_target(quoting_cfg) -> None:
    # Long 10 YES: the skew lowers the YES bid to buy less; don't raise it to earn rewards.
    d = yes_leg(prod_like(quoting_cfg), WALL, position=D(10))
    assert d.quote is not None and d.quote.price < D("0.46")
    assert "into target" not in d.reason


def test_thin_book_is_left_alone(quoting_cfg) -> None:
    # Only 100 contracts on the side: the 1000-contract window doesn't bind anywhere.
    thin = make_book(yes=[("0.46", 50), ("0.45", 50)], no=[("0.50", 1200)])
    d = yes_leg(prod_like(quoting_cfg), thin, position=D(0))
    assert d.quote is not None and "into target" not in d.reason


def test_prices_are_compared_at_the_loss_capped_size() -> None:
    # A 150-contract bid at 0.59 would lift the full-credit price above the 597 resting at
    # 0.57 and halve their credit; the 16 contracts the $10 loss cap allows can't. At that
    # size 0.57 earns as much as 0.59, so the bot takes the deeper price (0.56 after the
    # one-tick offset), not 0.59.
    cfg = QuotingConfig(
        placement="reward",
        auto_size=True,
        max_loss_per_fill=D(10),
        max_size=D(150),
        offset_ticks=1,
        share_tolerance=D("0.4"),
        min_cushion=D(100),
    )
    book = make_book(
        yes=[("0.82", 1), ("0.80", 100), ("0.60", 60), ("0.57", 597), ("0.41", 2160)],
        no=[("0.12", 50), ("0.11", 106), ("0.07", 68), ("0.05", 48), ("0.04", 15887)],
    )
    ctx = MarketContext(
        market=make_market(),
        book=book,
        position=D(0),
        reward=RewardParams(D(1000), D("0.5"), reward_per_day=D(100)),
        size=D(150),
    )
    yes = QuoteEngine(cfg, D(150)).quote(ctx)[Leg.YES].quote
    assert yes is not None and yes.price == D("0.56") and yes.size == 17
