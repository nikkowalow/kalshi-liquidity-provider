"""Run journal and dashboard server."""

import http.client
import json
import logging
import threading
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
    assert snap["markets"][0]["ticker"] == T
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
