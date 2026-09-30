"""Run journal and dashboard server."""

import http.client
import json
import logging
import os
import threading
import time
import urllib.request
from decimal import Decimal
from http.server import ThreadingHTTPServer

import pytest
from dashboard.server import Handler

from kalshi_lp.journal import JournalLogHandler, RunJournal
from tests.factories import make_book, make_market
from tests.fake_exchange import FakeExchange, FakeFeed
from tests.test_bot import T, program, settings, step


def read_events(journal: RunJournal) -> list[dict]:
    return [json.loads(line) for line in (journal.dir / "events.jsonl").read_text().splitlines()]


def test_journal_writes_events_and_state(tmp_path) -> None:
    j = RunJournal(tmp_path, "demo", "dry")
    j.event("order", action="place", price=Decimal("0.45"))
    j.write_state({"status": "running", "x": Decimal("1.5")})
    j.close()
    [event] = read_events(j)
    assert event["type"] == "order" and event["price"] == 0.45 and "ts" in event
    assert json.loads((j.dir / "state.json").read_text()) == {"status": "running", "x": 1.5}


def test_log_handler_mirrors_bot_logs_only(tmp_path) -> None:
    j = RunJournal(tmp_path, "demo", "dry")
    handler = JournalLogHandler(j)
    logging.getLogger("kalshi_lp.test").addHandler(handler)
    logging.getLogger("kalshi_lp.test").setLevel(logging.INFO)
    logging.getLogger("kalshi_lp.test").warning("hello %s", "world")
    handler.emit(logging.LogRecord("httpx", logging.INFO, "", 0, "noise", (), None))
    logging.getLogger("kalshi_lp.test").removeHandler(handler)
    j.close()
    assert [e["msg"] for e in read_events(j)] == ["hello world"]


async def test_bot_journals_orders_quotes_and_snapshot(tmp_path) -> None:
    book = make_book(yes=[("0.40", 15), ("0.38", 100)], no=[("0.50", 15), ("0.48", 100)])
    exchange = FakeExchange([make_market(T)], {T: book})
    exchange.programs = [program()]
    journal = RunJournal(tmp_path, "demo", "live")
    feed = FakeFeed(exchange)
    from kalshi_lp.engine.bot import LiquidityBot

    bot = LiquidityBot(
        settings(selection={"mode": "incentives"}),
        exchange,
        feed=feed,
        journal=journal,  # type: ignore[arg-type]
    )
    await bot.startup()
    await step(bot, feed)
    bot.sample_rewards()
    snap = bot.snapshot()
    await bot.shutdown()
    journal.close()

    kinds = [(e["type"], e.get("action")) for e in read_events(journal)]
    assert ("markets", None) in kinds
    assert ("quote", None) in kinds
    assert kinds.count(("order", "place")) == 2
    assert kinds.count(("order", "cancel")) == 2  # shutdown
    reasons = {(e["action"], e.get("reason")) for e in read_events(journal) if e["type"] == "order"}
    assert ("cancel", "bot shutting down") in reasons
    assert all(r for _, r in reasons)  # every place/cancel says why
    assert any(a == "place" and r.startswith("new quote (reward") for a, r in reasons)
    assert snap["markets"][0]["ticker"] == T
    [selection] = [e for e in read_events(journal) if e["type"] == "markets"]
    assert selection["markets"][0]["est_fills_per_day"] == 0  # no trades in the fake
    assert snap["markets"][0]["net_daily_reward"] == snap["markets"][0]["est_daily_reward"]
    assert snap["markets"][0]["book"]["bid"] == Decimal("0.40")
    assert len(snap["orders"]) == 2
    assert snap["totals"]["rewards_earned"] > 0
    json.dumps(snap, default=str)  # serialisable


@pytest.fixture
def dashboard(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    Handler.runs_dir = runs
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<title>KLP Terminal</title>")
    (static / "assets" / "app.js").write_text("console.log(1)")
    Handler.static_dir = static
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield runs, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def get(url: str):
    with urllib.request.urlopen(url) as r:
        body = r.read()
        return (
            json.loads(body) if r.headers["Content-Type"].startswith("application/json") else body
        )


def test_dashboard_serves_runs_state_and_incremental_events(dashboard) -> None:
    root, base = dashboard
    j = RunJournal(root, "demo", "dry")
    j.write_state({"status": "running"})
    j.event("order", action="place")
    assert b"KLP Terminal" in get(base + "/")  # the built React app
    assert get(base + "/assets/app.js") == b"console.log(1)"
    assert b"KLP Terminal" in get(base + "/some/client/route")  # SPA fallback
    [run] = get(base + "/api/runs")
    assert run["id"] == j.run_id and run["status"] == "running"
    assert get(f"{base}/api/runs/{j.run_id}/state")["status"] == "running"
    first = get(f"{base}/api/runs/{j.run_id}/events?offset=0")
    assert [e["action"] for e in first["events"]] == ["place"]
    j.event("order", action="cancel")
    j.close()
    second = get(f"{base}/api/runs/{j.run_id}/events?offset={first['offset']}")
    assert [e["action"] for e in second["events"]] == ["cancel"]  # only what's new


def test_dashboard_rejects_path_traversal(dashboard) -> None:
    _, base = dashboard
    with pytest.raises(urllib.error.HTTPError) as err:
        get(base + "/api/runs/..%2F..%2Fetc/state")
    assert err.value.code == 404
    # Static files can't escape the build directory either: never serve a file outside dist.
    host, port = base.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port))
    conn.request("GET", "/assets/../../../pyproject.toml")  # sent verbatim, not normalized
    resp = conn.getresponse()
    assert resp.status == 404 and b"[project]" not in resp.read()
    assert b"[project]" not in get(base + "/assets/..%2F..%2F..%2Fpyproject.toml")


def test_journal_is_shared_across_runs(tmp_path) -> None:
    first = RunJournal(tmp_path, "prod", "live")
    first.event("order", action="place")
    first.write_state({"status": "ended", "session": 1, "ledger": {"X": {"earned": 0.5}}})
    first.close()
    second = RunJournal(tmp_path, "prod", "live")
    second.event("order", action="cancel")
    second.close()
    assert second.dir == first.dir == tmp_path / "prod-live"
    assert second.session == 2
    assert second.previous["ledger"] == {"X": {"earned": 0.5}}
    assert [e["action"] for e in read_events(second)] == ["place", "cancel"]
    assert RunJournal(tmp_path, "prod", "live", name="fresh").session == 1


def test_metrics_also_go_to_their_own_file(tmp_path) -> None:
    j = RunJournal(tmp_path, "demo", "dry")
    j.event("metrics", rewards_earned=1)
    j.event("order", action="place")
    j.close()
    lines = (j.dir / "metrics.jsonl").read_text().splitlines()
    assert [json.loads(line)["type"] for line in lines] == ["metrics"]


def _legacy_run(root, name, earned, markets, events, updated=1000.0, status="ended"):
    path = root / name
    path.mkdir()
    (path / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    state = {
        "status": status,
        "started_at": updated - 100,
        "updated_at": updated,
        "totals": {"rewards_earned": earned, "requotes": 10},
        "markets": markets,
    }
    (path / "state.json").write_text(json.dumps(state))
    os.utime(path / "state.json", (updated, updated))


def test_legacy_runs_are_imported_once(tmp_path) -> None:
    row = {
        "ticker": "A",
        "title": "Alpha",
        "snapshots": 10,
        "paying_snapshots": 5,
        "avg_score": 0.2,
    }
    _legacy_run(
        tmp_path,
        "20260929-100000-prod-live",
        0.3,
        [{**row, "earned": 0.3}],
        [{"ts": 1, "type": "metrics", "rewards_earned": 0.3}, {"ts": 2, "type": "fill"}],
    )
    _legacy_run(
        tmp_path,
        "20260929-110000-prod-live",
        0.25,  # includes $0.05 from a market dropped before the run ended
        [{**row, "earned": 0.2}],
        [{"ts": 3, "type": "metrics", "rewards_earned": 0.2}],
    )
    _legacy_run(tmp_path, "20260929-120000-demo-live", 9.0, [], [])  # other env: untouched

    j = RunJournal(tmp_path, "prod", "live")
    j.close()
    assert j.session == 3
    ledger = j.previous["ledger"]["A"]
    assert ledger["earned"] == pytest.approx(0.5)
    assert ledger["snapshots"] == 20 and ledger["scored"] == 10
    assert ledger["score_sum"] == pytest.approx(4.0) and ledger["title"] == "Alpha"
    assert j.previous["fills_total"] == 1 and j.previous["requotes_total"] == 20
    assert j.previous["rewards_unattributed"] == pytest.approx(0.05)
    metrics = [json.loads(line) for line in (j.dir / "metrics.jsonl").read_text().splitlines()]
    assert [m["rewards_earned"] for m in metrics] == pytest.approx([0.3, 0.5])  # cumulative
    assert len(read_events(j)) == 3
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "20260929-120000-demo-live",
        "_imported",
        "prod-live",
    ]
    assert len(list((tmp_path / "_imported").iterdir())) == 2


def test_legacy_run_still_being_written_is_left_alone(tmp_path) -> None:
    now = time.time()
    _legacy_run(tmp_path, "20260929-100000-prod-live", 0.3, [], [], now, status="running")
    _legacy_run(tmp_path, "20260929-090000-prod-live", 0.1, [], [], now)  # just ended: import
    j = RunJournal(tmp_path, "prod", "live")
    j.close()
    assert j.session == 2 and (tmp_path / "20260929-100000-prod-live").exists()
    assert (tmp_path / "_imported" / "20260929-090000-prod-live").exists()


async def test_restarted_bot_carries_rewards_and_fills(tmp_path) -> None:
    from kalshi_lp.engine.bot import LiquidityBot

    async def run_once() -> dict:
        book = make_book(yes=[("0.40", 15), ("0.38", 100)], no=[("0.50", 15), ("0.48", 100)])
        exchange = FakeExchange([make_market(T)], {T: book})
        exchange.programs = [program()]
        feed = FakeFeed(exchange)
        journal = RunJournal(tmp_path, "demo", "live")
        bot = LiquidityBot(
            settings(selection={"mode": "incentives"}),
            exchange,
            feed=feed,
            journal=journal,  # type: ignore[arg-type]
        )
        await bot.startup()
        await step(bot, feed)
        bot.sample_rewards()
        bot.state.emit("fill", {"ticker": T})
        snap = bot.snapshot(status="ended")
        journal.write_state(snap)
        await bot.shutdown()
        journal.close()
        return snap

    first = await run_once()
    second = await run_once()
    earned = first["totals"]["rewards_earned"]
    assert earned > 0
    assert second["session"] == 2
    assert second["totals"]["rewards_earned"] == pytest.approx(2 * earned)
    assert second["totals"]["rewards_session"] == pytest.approx(earned)
    assert second["totals"]["fills"] == 2
    assert second["ledger"][T]["snapshots"] == 2
    assert second["first_started_at"] == first["first_started_at"]


def test_dashboard_tail_and_metrics(dashboard) -> None:
    root, base = dashboard
    j = RunJournal(root, "demo", "dry")
    for i in range(50):
        j.event("metrics", rewards_earned=i)
    j.close()
    size = (j.dir / "events.jsonl").stat().st_size
    tail = get(f"{base}/api/runs/{j.run_id}/events?tail=200")
    assert tail["offset"] == size
    assert 0 < len(tail["events"]) < 50
    assert tail["events"][-1]["rewards_earned"] == 49
    thinned = get(f"{base}/api/runs/{j.run_id}/metrics?points=10")
    assert len(thinned) == 10
    assert thinned[0]["rewards_earned"] == 0 and thinned[-1]["rewards_earned"] == 49


def test_dashboard_lists_most_recent_first_and_hides_archive(dashboard) -> None:
    root, base = dashboard
    for name, mtime in (("old", 100.0), ("new", 200.0)):
        (root / name).mkdir()
        (root / name / "state.json").write_text("{}")
        os.utime(root / name / "state.json", (mtime, mtime))
    (root / "_imported").mkdir()
    assert [r["id"] for r in get(base + "/api/runs")] == ["new", "old"]


def test_older_import_is_repaired_with_unattributed_rewards(tmp_path) -> None:
    archive = tmp_path / "_imported"
    archive.mkdir()
    _legacy_run(archive, "20260929-100000-prod-live", 0.4, [{"ticker": "A", "earned": 0.1}], [])
    (tmp_path / "prod-live").mkdir()
    (tmp_path / "prod-live" / "state.json").write_text(json.dumps({"session": 3, "ledger": {}}))
    j = RunJournal(tmp_path, "prod", "live")
    j.close()
    assert j.previous["rewards_unattributed"] == pytest.approx(0.3)


def test_tracker_total_includes_unattributed() -> None:
    from kalshi_lp.engine.reward_tracker import RewardTracker

    tracker = RewardTracker()
    tracker.restore({"A": {"earned": 0.5, "snapshots": 3}}, 0.25)
    assert tracker.total_earned == Decimal("0.75")
    assert tracker.session_earned == 0


async def test_unchanged_scans_are_not_journaled_again(tmp_path) -> None:
    from kalshi_lp.engine.bot import LiquidityBot

    exchange = FakeExchange(
        [make_market(T)], {T: make_book(yes=[("0.40", 150)], no=[("0.50", 150)])}
    )
    exchange.programs = [program()]
    journal = RunJournal(tmp_path, "demo", "live")
    bot = LiquidityBot(
        settings(selection={"mode": "incentives"}),
        exchange,
        feed=FakeFeed(exchange),
        journal=journal,  # type: ignore[arg-type]
    )
    await bot.startup()
    await bot._reselect_job()
    await bot._reselect_job()  # same market wins every minute
    journal.close()
    assert sum(e["type"] == "markets" for e in read_events(journal)) == 1
