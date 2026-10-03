"""Dashboard API: HTTP endpoints, the live WebSocket, and the control buttons."""

import asyncio
import stat
from decimal import Decimal

import aiohttp
import pytest
from aiohttp import WSCloseCode, WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from kalshi_lp import api as api_module
from kalshi_lp.api import DashboardApi, _Client
from kalshi_lp.config import ApiConfig
from kalshi_lp.engine.controls import ControlError
from kalshi_lp.journal import RunJournal


class FakeControls:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.release = asyncio.Event()
        self.release.set()

    async def _act(self, name: str, arg: object = None) -> str:
        self.calls.append((name, arg))
        await self.release.wait()
        return f"did {name}"

    async def pause(self) -> str:
        return await self._act("pause")

    async def resume(self) -> str:
        return await self._act("resume")

    async def flatten(self) -> str:
        return await self._act("flatten")

    async def rescan(self) -> str:
        return await self._act("rescan")

    async def set_budget(self, max_capital: Decimal) -> str:
        if max_capital > 1000:
            raise ControlError("too much")
        return await self._act("budget", max_capital)

    async def stop(self) -> str:
        return await self._act("stop")

    def config(self) -> dict[str, object]:
        return {"values": {"risk": {"max_capital": 250}}}

    async def set_config(self, changes: dict[str, object]) -> str:
        return await self._act("config", changes)

    async def restart(self) -> str:
        return await self._act("restart")

    async def balance_history(self) -> list[dict[str, object]]:
        return [{"ts": 1.0, "kind": "reward", "amount": Decimal("1.79")}]


@pytest.fixture
def journal(tmp_path):
    j = RunJournal(tmp_path / "runs", "demo", "dry")
    yield j
    j.close()


@pytest.fixture
def dist(tmp_path):
    path = tmp_path / "dist"
    (path / "assets").mkdir(parents=True)
    (path / "index.html").write_text("<title>KLP Terminal</title>")
    (path / "assets" / "app.js").write_text("console.log(1)")
    return path


def serve(api: DashboardApi) -> TestClient:
    return TestClient(TestServer(api.app))


async def get_json(client: TestClient, path: str):
    resp = await client.get(path)
    assert resp.status == 200, await resp.text()
    return await resp.json()


async def test_http_endpoints_serve_the_live_state(journal, dist) -> None:
    api = DashboardApi(journal, ApiConfig(port=0, static_dir=dist))
    journal.write_state({"status": "running", "markets": [{"ticker": "A"}], "orders": [{"id": 1}]})
    journal.event("fill", ticker="A")
    journal.event("order", action="place")
    journal.event("metrics", rewards_earned=1)
    async with serve(api) as client:
        assert (await get_json(client, "/api/state"))["status"] == "running"
        assert await get_json(client, "/api/markets") == [{"ticker": "A"}]
        assert await get_json(client, "/api/orders") == [{"id": 1}]
        events = await get_json(client, "/api/events")
        assert [e["type"] for e in events] == ["fill", "order"]  # metrics: see /api/metrics
        assert [e["type"] for e in await get_json(client, "/api/events?type=fill")] == ["fill"]
        assert len(await get_json(client, "/api/events?limit=1")) == 1
        assert [m["rewards_earned"] for m in await get_json(client, "/api/metrics")] == [1]
        health = await get_json(client, "/api/health")
        assert health["status"] == "running" and health["run_id"] == journal.run_id
        assert "/api/ws" in (await get_json(client, "/api"))["endpoints"]
        assert (await client.get("/api/nope")).status == 404
        assert (await client.get("/api/events?limit=lots")).status == 400


async def test_history_from_earlier_sessions_is_served(journal) -> None:
    journal.event("fill", ticker="OLD")
    journal.write_state({"status": "ended", "session": 1})
    api = DashboardApi(journal, ApiConfig(port=0, static_dir=None))  # a restarted bot
    async with serve(api) as client:
        assert [e["ticker"] for e in await get_json(client, "/api/events")] == ["OLD"]
        assert (await get_json(client, "/api/state"))["status"] == "ended"


async def test_every_fill_is_served_even_beyond_the_tail(journal, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "TAIL_BYTES", 200)  # only the last few lines
    journal.event("fill", ticker="OLDEST")
    for i in range(20):
        journal.event("log", msg=f"filler {i}")
    journal.event("fill", ticker="NEWEST")
    api = DashboardApi(journal, ApiConfig(port=0, static_dir=None))
    async with serve(api) as client:
        fills = await get_json(client, "/api/events?type=fill")
        assert [e["ticker"] for e in fills] == ["OLDEST", "NEWEST"]  # each once, in order


async def test_a_markets_full_order_history_is_served(journal, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "TAIL_BYTES", 200)  # the hello only has the last lines
    journal.event("order", action="place", ticker="A-1", price=0.4)
    for i in range(30):
        journal.event("order", action="place", ticker="B-1", price=i)
    journal.event("order", action="cancel", ticker="A-1", price=0.4)
    journal.event("fill", ticker="A-1")
    api = DashboardApi(journal, ApiConfig(port=0, static_dir=None))
    async with serve(api) as client:
        history = await get_json(client, "/api/history/A-1")
        assert [e["action"] for e in history] == ["place", "cancel"]  # orders only, all of them
        assert await get_json(client, "/api/history/NOPE-1") == []


async def test_running_bots_register_and_list_each_other(tmp_path) -> None:
    prod = RunJournal(tmp_path / "runs", "prod", "live")
    demo = RunJournal(tmp_path / "runs", "demo", "live")
    a = DashboardApi(prod, ApiConfig(port=0, static_dir=None))
    b = DashboardApi(demo, ApiConfig(port=0, static_dir=None))
    assert await a.start() and await b.start()
    try:
        async with aiohttp.ClientSession() as http, http.get(f"{a.url}/api/bots") as resp:
            bots = {x["run_id"]: x for x in await resp.json()}
        assert bots["prod-live"]["url"] == a.url and bots["prod-live"]["self"]
        assert bots["demo-live"]["url"] == b.url and not bots["demo-live"]["self"]
    finally:
        await b.stop()
        await a.stop()
        prod.close()
        demo.close()
    assert not list((tmp_path / "runs").glob("*/api-url"))  # unregistered on stop


async def test_another_local_dashboard_may_call_in_but_other_sites_may_not(journal) -> None:
    controls = FakeControls()
    api = DashboardApi(journal, ApiConfig(port=0, static_dir=None), controls)
    local = {"Origin": "http://127.0.0.1:8050"}  # the prod bot's page, calling this bot
    async with serve(api) as client:
        resp = await client.get("/api/health", headers=local)
        assert resp.status == 200
        assert resp.headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:8050"
        pre = await client.options("/api/control/pause", headers=local)
        assert pre.status == 204 and "Authorization" in pre.headers["Access-Control-Allow-Headers"]
        ok = await client.post("/api/control/pause", headers={**local, **auth(api)})
        assert ok.status == 200 and ok.headers["Access-Control-Allow-Origin"]
        evil = await client.get("/api/health", headers={"Origin": "https://evil.example"})
        assert evil.status == 403
        same = await client.get("/api/health")  # same origin: no CORS headers needed
        assert "Access-Control-Allow-Origin" not in same.headers


async def test_websocket_says_hello_then_streams_events_and_snapshots(journal) -> None:
    journal.event("order", action="place")
    for i in range(3):
        journal.event("metrics", rewards_earned=i)
    journal.write_state({"status": "running", "n": 1})
    api = DashboardApi(journal, ApiConfig(port=0, static_dir=None))
    async with serve(api) as client:
        ws = await client.ws_connect("/api/ws?points=2")
        hello = await ws.receive_json()
        assert hello["channel"] == "hello" and hello["run_id"] == journal.run_id
        assert hello["state"]["n"] == 1
        assert [e["action"] for e in hello["events"]] == ["place"]
        assert [m["rewards_earned"] for m in hello["metrics"]] == [0, 2]  # thinned

        journal.event("fill", ticker="A")
        journal.event("order", action="cancel")
        journal.write_state({"status": "running", "n": 2})
        journal.write_state({"status": "running", "n": 3})
        batch = await ws.receive_json()
        assert batch["channel"] == "events"
        assert [e["type"] for e in batch["data"]] == ["fill", "order"]  # one frame, in order
        state = await ws.receive_json()
        assert state == {"channel": "state", "data": {"status": "running", "n": 3}}  # newest only
        assert (await get_json(client, "/api/health"))["dashboards_connected"] == 1
        await ws.close()


async def test_other_sites_and_host_names_are_refused(journal) -> None:
    api = DashboardApi(journal, ApiConfig(port=0, static_dir=None))
    async with serve(api) as client:
        evil = {"Origin": "https://evil.example"}
        assert (await client.get("/api/state", headers=evil)).status == 403
        with pytest.raises(aiohttp.WSServerHandshakeError) as err:
            await client.ws_connect("/api/ws", origin="https://evil.example")
        assert err.value.status == 403
        # DNS rebinding: a page on evil.example whose name now points at 127.0.0.1.
        rebound = {"Host": "evil.example:8050"}
        assert (await client.get("/api/state", headers=rebound)).status == 403
        # The Vite dev server (another local port) and plain curl are fine.
        dev = {"Origin": "http://localhost:5173"}
        assert (await client.get("/api/state", headers=dev)).status == 200
        ws = await client.ws_connect("/api/ws", origin="http://localhost:5173")
        assert (await ws.receive_json())["channel"] == "hello"
        await ws.close()


async def test_serves_the_built_dashboard(journal, dist, tmp_path) -> None:
    (tmp_path / "secret.txt").write_text("secret")
    api = DashboardApi(journal, ApiConfig(port=0, static_dir=dist))
    async with serve(api) as client:
        assert "KLP Terminal" in await (await client.get("/")).text()
        assert await (await client.get("/assets/app.js")).text() == "console.log(1)"
        assert "KLP Terminal" in await (await client.get("/some/route")).text()  # SPA
        for path in ("/..%2Fsecret.txt", "/assets/..%2F..%2Fsecret.txt"):
            assert "secret" not in await (await client.get(path)).text()


async def test_unbuilt_dashboard_says_how_to_build_it(journal, tmp_path) -> None:
    api = DashboardApi(journal, ApiConfig(port=0, static_dir=tmp_path / "missing"))
    async with serve(api) as client:
        assert "make dashboard" in await (await client.get("/")).text()
        assert (await client.get("/api/state")).status == 200


def test_client_queue_keeps_order_and_only_the_newest_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(api_module, "MAX_PENDING", 2)
    client = _Client(ws=None)  # type: ignore[arg-type]
    client.push("event", '{"a":1}')
    client.push("state", '{"s":1}')
    client.push("event", '{"a":2}')
    client.push("state", '{"s":2}')
    assert client.take() == [
        '{"channel":"events","data":[{"a":1},{"a":2}]}',
        '{"channel":"state","data":{"s":2}}',
    ]
    assert client.take() == []
    for _ in range(3):
        client.push("event", "{}")
    assert client.stuck


async def test_a_client_that_falls_behind_is_disconnected(journal, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "MAX_PENDING", 1)
    api = DashboardApi(journal, ApiConfig(port=0, static_dir=None))
    async with serve(api) as client:
        ws = await client.ws_connect("/api/ws")
        await ws.receive_json()
        for _ in range(3):  # all before the sender gets to run
            journal.event("log", msg="x")
        msg = await ws.receive()
        assert msg.type == WSMsgType.CLOSE and msg.data == WSCloseCode.TRY_AGAIN_LATER


async def test_stopping_delivers_the_final_snapshot_then_closes(journal) -> None:
    api = DashboardApi(journal, ApiConfig(port=0, static_dir=None))
    assert await api.start()
    async with aiohttp.ClientSession() as session:
        ws = await session.ws_connect(f"{api.url}/api/ws")
        await ws.receive_json()
        journal.write_state({"status": "ended"})
        stopping = asyncio.create_task(api.stop())
        assert (await ws.receive_json())["data"]["status"] == "ended"
        msg = await ws.receive()
        assert msg.type == WSMsgType.CLOSE and msg.data == WSCloseCode.GOING_AWAY
        await stopping
    assert api._publish not in journal.subscribers


async def test_port_in_use_leaves_the_bot_running_without_the_api(journal) -> None:
    first = DashboardApi(journal, ApiConfig(port=0, static_dir=None))
    assert await first.start()
    port = int(first.url.rsplit(":", 1)[1])
    second = DashboardApi(journal, ApiConfig(port=port, static_dir=None))
    assert await second.start() is False
    assert second._publish not in journal.subscribers
    await first.stop()


# ---------------------------------------------------------------- controls


def auth(api: DashboardApi) -> dict[str, str]:
    return {"Authorization": f"Bearer {api.token}"}


async def test_buttons_need_the_token(journal) -> None:
    controls = FakeControls()
    api = DashboardApi(journal, ApiConfig(port=0, static_dir=None), controls)
    async with serve(api) as client:
        assert (await client.post("/api/control/pause")).status == 401
        wrong = {"Authorization": "Bearer nope"}
        assert (await client.post("/api/control/pause", headers=wrong)).status == 401
        assert controls.calls == []
        resp = await client.post("/api/control/pause", headers=auth(api))
        assert resp.status == 200
        assert await resp.json() == {"ok": True, "message": "did pause"}
        assert (await client.post("/api/control/explode", headers=auth(api))).status == 404
        assert (await client.get("/api/control/pause")).status == 404  # GET never acts
        # Another site can't press them, token or not.
        evil = {**auth(api), "Origin": "https://evil.example"}
        assert (await client.post("/api/control/stop", headers=evil)).status == 403
    assert controls.calls == [("pause", None)]


async def test_budget_takes_json_and_reports_refusals(journal) -> None:
    controls = FakeControls()
    api = DashboardApi(journal, ApiConfig(port=0, static_dir=None), controls)
    async with serve(api) as client:
        ok = await client.post("/api/control/budget", headers=auth(api), json={"max_capital": 100})
        assert (await ok.json())["message"] == "did budget"
        assert controls.calls == [("budget", Decimal(100))]
        for body in ({"max_capital": "lots"}, {"budget": 5}, [1]):
            bad = await client.post("/api/control/budget", headers=auth(api), json=body)
            assert bad.status == 400 and "max_capital" in (await bad.json())["error"]
        refused = await client.post(
            "/api/control/budget", headers=auth(api), json={"max_capital": 5000}
        )
        assert refused.status == 400
        assert await refused.json() == {"ok": False, "error": "too much"}


async def test_settings_are_read_freely_but_saved_with_the_token(journal) -> None:
    controls = FakeControls()
    api = DashboardApi(journal, ApiConfig(port=0, static_dir=None), controls)
    async with serve(api) as client:
        assert (await get_json(client, "/api/config"))["values"]["risk"]["max_capital"] == 250
        body = {"changes": {"risk.max_capital": 300}}
        assert (await client.post("/api/control/config", json=body)).status == 401
        ok = await client.post("/api/control/config", headers=auth(api), json=body)
        assert ok.status == 200
        bad = await client.post("/api/control/config", headers=auth(api), json={"x": 1})
        assert bad.status == 400 and "changes" in (await bad.json())["error"]
        assert (await client.post("/api/control/restart", headers=auth(api))).status == 200
    assert controls.calls == [("config", {"risk.max_capital": 300}), ("restart", None)]
    off = DashboardApi(journal, ApiConfig(port=0, static_dir=None))  # controls switched off
    async with serve(off) as client:
        assert (await client.get("/api/config")).status == 404


async def test_balance_history_is_served(journal) -> None:
    api = DashboardApi(journal, ApiConfig(port=0, static_dir=None), FakeControls())
    async with serve(api) as client:
        assert await get_json(client, "/api/balance") == [
            {"ts": 1.0, "kind": "reward", "amount": 1.79}
        ]


async def test_one_action_at_a_time(journal) -> None:
    controls = FakeControls()
    controls.release.clear()  # the first action hangs (a slow flatten)
    api = DashboardApi(journal, ApiConfig(port=0, static_dir=None), controls)
    async with serve(api) as client:
        first = asyncio.create_task(client.post("/api/control/flatten", headers=auth(api)))
        while not controls.calls:
            await asyncio.sleep(0.01)
        second = await client.post("/api/control/flatten", headers=auth(api))
        assert second.status == 409
        controls.release.set()
        assert (await first).status == 200
    assert controls.calls == [("flatten", None)]


async def test_hello_hands_the_dashboard_its_token(journal) -> None:
    api = DashboardApi(journal, ApiConfig(port=0, static_dir=None), FakeControls())
    async with serve(api) as client:
        ws = await client.ws_connect("/api/ws")
        controls = (await ws.receive_json())["controls"]
        assert controls["token"] == api.token and "budget" in controls["actions"]
        await ws.close()
    off = DashboardApi(journal, ApiConfig(port=0, static_dir=None))  # api.controls: false
    async with serve(off) as client:
        ws = await client.ws_connect("/api/ws")
        assert (await ws.receive_json())["controls"] is None
        await ws.close()
        resp = await client.post("/api/control/pause", headers=auth(off))
        assert resp.status == 404


async def test_token_file_is_private_and_removed_on_stop(journal) -> None:
    api = DashboardApi(journal, ApiConfig(port=0, static_dir=None), FakeControls())
    assert await api.start()
    token_file = journal.dir / "api-token"
    assert token_file.read_text().strip() == api.token
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            f"{api.url}/api/control/rescan",
            headers={"Authorization": f"Bearer {token_file.read_text().strip()}"},
        )
        assert resp.status == 200  # how a script uses it
    await api.stop()
    assert not token_file.exists()
    assert DashboardApi(journal, ApiConfig()).token != api.token  # new every start


async def test_scanner_reports_are_served_and_pushed(journal) -> None:
    journal.write_scan({"size": 10, "rows": [{"ticker": "OLD"}]})  # from before a restart
    api = DashboardApi(journal, ApiConfig(port=0, static_dir=None))
    async with serve(api) as client:
        assert (await get_json(client, "/api/scan"))["rows"] == [{"ticker": "OLD"}]
        ws = await client.ws_connect("/api/ws")
        assert (await ws.receive_json())["scan"]["rows"] == [{"ticker": "OLD"}]
        journal.write_scan({"size": 10, "rows": [{"ticker": "NEW"}]})
        pushed = await ws.receive_json()
        assert pushed == {"channel": "scan", "data": {"size": 10, "rows": [{"ticker": "NEW"}]}}
        assert (await get_json(client, "/api/scan"))["rows"] == [{"ticker": "NEW"}]
        await ws.close()
