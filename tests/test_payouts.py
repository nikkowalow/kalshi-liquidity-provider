from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from kalshi_lp.engine.payouts import CashFlows, PayoutTracker
from kalshi_lp.engine.reward_tracker import RewardTracker
from kalshi_lp.strategy.rewards import RewardParams

D = Decimal
T0 = 1_000_000.0


def fill(i: int, side: str, price: str, count: int, fee: str = "0") -> dict[str, Any]:
    return {
        "fill_id": f"f{i}",
        "ticker": "MKT-1",
        "book_side": side,
        "yes_price_dollars": price,
        "count_fp": str(count),
        "fee_cost": fee,
    }


class FakeKalshi:
    def __init__(self) -> None:
        self.records: dict[str, list[dict[str, Any]]] = {
            "fills": [],
            "settlements": [],
            "deposits": [],
            "withdrawals": [],
        }

    async def get_cash_records(
        self, kind: str, *, min_ts: float | None = None
    ) -> list[dict[str, Any]]:
        return list(self.records[kind])


def test_buying_and_selling_yes_is_plain_cash() -> None:
    flows = CashFlows()
    flows.add_fill(fill(1, "bid", "0.40", 10))  # buy 10 YES: -$4
    flows.add_fill(fill(2, "ask", "0.35", 10, fee="0.10"))  # sell them: +$3.50
    assert flows.fill_cash == D("-0.50") and flows.fees == D("0.10")


def test_selling_yes_from_flat_buys_no_at_one_minus_price() -> None:
    flows = CashFlows()
    flows.add_fill(fill(1, "ask", "0.30", 10))  # Kalshi: buy 10 NO at 0.70 = -$7
    assert flows.fill_cash == D("-7.00")
    flows.add_fill(fill(2, "bid", "0.40", 10))  # sell the NO at 0.60 = +$6
    assert flows.fill_cash == D("-1.00")


async def test_unexplained_balance_change_is_a_payout() -> None:
    kalshi = FakeKalshi()
    tracker = PayoutTracker(T0, D(100))
    # A round trip that lost $0.50 plus a $0.10 fee, and a $250 deposit.
    kalshi.records["fills"] = [fill(1, "bid", "0.40", 10), fill(2, "ask", "0.35", 10, "0.10")]
    kalshi.records["deposits"] = [
        {"amount_cents": 25000, "created_ts": T0 + 10, "status": "applied"}
    ]
    assert await tracker.refresh(kalshi, D("349.40"), {}, T0 + 60) is None  # all explained
    payout = await tracker.refresh(kalshi, D("351.04"), {}, T0 + 120)  # $1.64 more appeared
    assert payout is not None and payout.amount == D("1.64")
    assert tracker.paid == D("1.64")
    assert await tracker.refresh(kalshi, D("351.04"), {}, T0 + 180) is None  # counted once


async def test_payout_is_split_over_ended_periods_that_reached_the_minimum() -> None:
    kalshi = FakeKalshi()
    tracker = PayoutTracker(T0, D(100), minimum=D(1))
    ended = {"A-1|p1": D("1.50"), "B-1|p1": D("0.50"), "C-1|p1": D("0.10")}  # C can't be paid
    payout = await tracker.refresh(kalshi, D(102), ended, T0 + 60)
    assert payout is not None
    assert payout.split == {"A-1|p1": D("1.5"), "B-1|p1": D("0.5")}
    assert tracker.by_market() == {"A-1": D("1.5"), "B-1": D("0.5")}
    # A later payout doesn't land on periods already matched.
    later = await tracker.refresh(kalshi, D(103), {**ended, "A-1|p2": D(1)}, T0 + 120)
    assert later is not None and later.split == {"A-1|p2": D(1)}
    assert tracker.unmatched == 0


async def test_restored_tracker_does_not_count_old_payouts_again() -> None:
    kalshi = FakeKalshi()
    tracker = PayoutTracker(T0, D(100))
    await tracker.refresh(kalshi, D(102), {"A-1|p1": D(2)}, T0 + 60)
    again = PayoutTracker.restore(tracker.export())
    assert again.paid == D(2) and again.by_market() == {"A-1": D(2)}
    assert await again.refresh(kalshi, D(102), {"A-1|p1": D(2)}, T0 + 120) is None


def test_earnings_are_kept_per_program_period() -> None:
    tracker = RewardTracker()
    now = datetime.now(UTC)
    past = RewardParams(
        D(100), D("0.5"), reward_per_day=D(86400), period_end=now - timedelta(hours=1)
    )
    live = RewardParams(
        D(100), D("0.5"), reward_per_day=D(86400), period_end=now + timedelta(hours=1)
    )
    tracker.record("MKT-1", D(2), past)  # earns $1 per snapshot at a full score of 2
    tracker.record("MKT-1", D(2), live)
    ended = tracker.ended_periods(now.timestamp())
    assert ended == {f"MKT-1|{past.period_end.isoformat()}": D(1)}  # only the finished one
    restored = RewardTracker()
    restored.restore(tracker.ledger())
    assert restored.stats["MKT-1"].periods == tracker.stats["MKT-1"].periods


def test_open_period_earnings_leave_out_ended_periods() -> None:
    tracker = RewardTracker()
    now = datetime.now(UTC)
    tracker.restore(
        {
            "MKT-1": {
                "earned": 1.5,
                "periods": {
                    (now - timedelta(hours=1)).isoformat(): 1.2,
                    (now + timedelta(hours=1)).isoformat(): 0.3,
                },
            }
        }
    )
    assert tracker.stats["MKT-1"].open_period_earned(now.timestamp()) == D("0.3")
