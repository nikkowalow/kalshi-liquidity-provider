from decimal import Decimal

from kalshi_lp.config import RiskConfig
from kalshi_lp.engine.risk import RiskManager, position_pnl
from tests.factories import make_market, make_position

D = Decimal
T = "TEST-MKT"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_position_pnl_marks_yes_and_no() -> None:
    long_yes = make_position(position=10, exposure="4.00")
    assert position_pnl(long_yes, D("0.50")) == D("1.00")
    long_no = make_position(position=-10, exposure="4.00")  # 10 NO bought at 0.40
    assert position_pnl(long_no, D("0.50")) == D("1.00")
    assert position_pnl(long_yes, None) == 0


def test_session_loss_halts(risk_cfg: RiskConfig) -> None:
    risk = RiskManager(risk_cfg, Clock())
    start = {T: make_position(position=100, exposure="50.00")}
    view = risk.update({T}, start, {T: D("0.50")}, D(1000))
    assert view.session_pnl == 0 and not view.halted
    # Mark drops 25c on 100 contracts: -$25 vs $20 limit.
    view = risk.update({T}, start, {T: D("0.25")}, D(1000))
    assert view.session_pnl == D(-25)
    assert view.halted and risk.halted


def test_fill_burst_pauses_market(risk_cfg: RiskConfig) -> None:
    clock = Clock()
    risk = RiskManager(risk_cfg, clock)
    for pos in (0, 10, 25, 35):  # 35 contracts traded within the window
        risk.update({T}, {T: make_position(position=pos)}, {}, D(1000))
        clock.now += 5
    assert risk.market_paused(T)
    clock.now += risk_cfg.cooldown_seconds
    assert not risk.market_paused(T)


def test_slow_fills_do_not_pause(risk_cfg: RiskConfig) -> None:
    clock = Clock()
    risk = RiskManager(risk_cfg, clock)
    for pos in range(0, 60, 10):
        risk.update({T}, {T: make_position(position=pos)}, {}, D(1000))
        clock.now += 61  # outside the window each time
    assert not risk.market_paused(T)


def test_exposure_and_balance_gates(risk_cfg: RiskConfig) -> None:
    risk = RiskManager(risk_cfg, Clock())
    big = {T: make_position(position=10, exposure="150.00")}
    assert not risk.update({T}, big, {}, D(1000)).allow_increase
    assert not risk.update({T}, {}, {}, D(5)).allow_increase
    assert risk.update({T}, {}, {}, D(1000)).allow_increase


def test_consecutive_errors_pause_globally(risk_cfg: RiskConfig) -> None:
    clock = Clock()
    risk = RiskManager(risk_cfg, clock)
    for _ in range(risk_cfg.max_consecutive_errors):
        risk.record_cycle(False)
    assert risk.globally_paused
    clock.now += risk_cfg.cooldown_seconds + 1
    assert not risk.globally_paused


def test_near_close(risk_cfg: RiskConfig) -> None:
    risk = RiskManager(risk_cfg, Clock())
    assert risk.near_close(make_market(hours_to_close=0.1))
    assert not risk.near_close(make_market(hours_to_close=5))


def test_crash_that_empties_the_bid_shows_the_full_loss() -> None:
    # Long 2 YES bought at 0.11 ($0.22). The market crashes and nobody bids for YES.
    pos = make_position(position=2, exposure="0.22")
    assert position_pnl(pos, (None, D("0.13"))) == D("-0.22")  # can't sell: worth $0
    assert position_pnl(pos, (D("0.05"), None)) == D("-0.12")  # sellable at the 0.05 bid
    assert position_pnl(pos, (D("0.05"), D("0.07"))) == D("-0.10")  # both sides: mid 0.06
    # Long NO (position -2) needs a YES ask to sell into.
    no = make_position(position=-2, exposure="1.60")
    assert position_pnl(no, (D("0.10"), None)) == D("-1.60")


def test_blind_book_keeps_last_valuation(risk_cfg: RiskConfig) -> None:
    risk = RiskManager(risk_cfg, Clock())
    pos = {T: make_position(position=10, exposure="5.00")}
    risk.update({T}, pos, {T: (D("0.50"), D("0.50"))}, D(1000))
    view = risk.update({T}, pos, {T: (D("0.30"), D("0.30"))}, D(1000))
    assert view.session_pnl == D(-2)
    view = risk.update({T}, pos, {T: None}, D(1000))  # feed blind: don't reset to cost
    assert view.session_pnl == D(-2)
