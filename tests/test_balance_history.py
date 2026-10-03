from decimal import Decimal

from kalshi_lp.engine.balance_history import history, listed_changes, unlisted_changes

D = Decimal


def fill(ts: float, side: str, price: str, count: int, fee: str = "0", ticker: str = "A-1") -> dict:
    return {
        "ts": ts,
        "ticker": ticker,
        "book_side": side,
        "yes_price_dollars": price,
        "count_fp": str(count),
        "fee_cost": fee,
    }


def samples(*pairs: tuple[float, str]) -> list[tuple[float, Decimal]]:
    return [(t, D(b)) for t, b in pairs]


def test_listed_changes_count_fill_cash_in_kalshis_terms() -> None:
    records = {
        "fills": [fill(10, "bid", "0.40", 10, "0.05"), fill(20, "ask", "0.30", 20)],
        "deposits": [{"created_ts": 5, "amount_cents": 25000, "status": "applied"}],
    }
    out = listed_changes(records, since=0)
    assert [(e["kind"], e["amount"]) for e in out] == [
        ("deposit", D(250)),
        ("fill", D("-4.05")),  # bought 10 YES at 0.40, plus the fee
        ("fill", D("-4.00")),  # sold the 10 (+3.00), then 10 more = bought 10 NO at 0.70 (-7.00)
    ]


def test_a_payout_shows_when_the_balance_jumps_and_holds() -> None:
    bal = samples(
        *[(t, "100") for t in range(0, 100, 10)], *[(t, "101.79") for t in range(100, 200, 10)]
    )
    [payout] = unlisted_changes(bal, [])
    assert (payout["ts"], payout["kind"], payout["amount"]) == (100, "reward", D("1.79"))


def test_a_fills_late_balance_update_is_not_a_payout() -> None:
    # The fill (-4) is at t=50; the balance only shows it at t=70: a brief +4 gap, no payout.
    listed = [{"ts": 50, "amount": D(-4)}]
    bal = samples(*[(t, "100") for t in range(0, 70, 10)], *[(t, "96") for t in range(70, 200, 10)])
    assert unlisted_changes(bal, listed) == []


def test_a_gap_while_the_bot_was_stopped_doesnt_count_as_holding() -> None:
    # Fill at t=50, the bot stops at t=60 before the balance catches up, restarts at t=3000.
    listed = [{"ts": 50, "amount": D(-5)}]
    bal = samples(
        (0, "100"),
        (40, "100"),
        (55, "100"),
        (60, "100"),
        *[(t, "95") for t in range(3000, 3100, 10)],
    )
    assert unlisted_changes(bal, listed) == []


def test_history_is_newest_first_with_the_balance_after_each_change() -> None:
    records = {"fills": [fill(50, "bid", "0.40", 10)]}
    bal = samples(*[(t, "100") for t in range(0, 60, 10)], *[(t, "96") for t in range(60, 400, 10)])
    bal += samples(*[(t, "97.50") for t in range(400, 600, 10)])  # a $1.50 payout at t=400
    out = history(records, bal, since=0)
    assert [(e["kind"], e["amount"], e["balance_after"]) for e in out] == [
        ("reward", D("1.50"), D("97.50")),
        ("fill", D("-4.00"), D("96.00")),
    ]
