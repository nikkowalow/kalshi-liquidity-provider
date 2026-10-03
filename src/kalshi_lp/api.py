"""Dashboard API: the running bot's state over HTTP and a WebSocket.

``klp run`` serves it (``api:`` in the config; http://127.0.0.1:8050 by default)
together with the built dashboard. The dashboard holds a WebSocket open and
gets every journal event and snapshot as the bot writes it. Scripts can use
the HTTP endpoints, e.g. ``curl -s localhost:8050/api/markets | jq``:

    GET /api              this list
    GET /api/health       status, session, exchange feed, connected dashboards
    GET /api/state        the latest snapshot: totals, markets, resting orders, config
    GET /api/markets      the snapshot's markets
    GET /api/orders       the snapshot's resting orders
    GET /api/events       recent journal events, oldest first (?type=fill&limit=100)
    GET /api/metrics      totals history, evenly thinned (?points=2000)
    GET /api/scan         the market scanner: the best markets by $/day at a fixed size
    GET /api/ws           WebSocket, below
    POST /api/control/<action>   pause, resume, flatten, rescan, budget, stop
                                 (see engine.controls; needs the token, below)

WebSocket messages, server to client, JSON:

    {"channel": "hello", "run_id": ..., "state": {...}, "events": [...], "metrics": [...],
     "scan": {...} or null, "controls": {"token": ..., "actions": [...]} or null}
        first message: the latest snapshot, recent events and the totals history
    {"channel": "events", "data": [...]}   new journal events, in order
    {"channel": "state", "data": {...}}    each new snapshot, about once a second
    {"channel": "scan", "data": {...}}     each market scanner report (the hello has the last)

The journal files stay the record (they carry history across restarts); this
module only delivers it. It runs in the bot's event loop but can't hold the bot
up: publishing never waits on a client, and a client that falls too far behind
is disconnected (it reconnects and starts again from a fresh hello).

It binds to localhost and refuses requests from other sites (``Origin``) and,
on localhost, for other host names (``Host``, against DNS rebinding), so a web
page you visit can't read your positions or press the buttons. Controls also
need ``Authorization: Bearer <token>``: a new random token each start, sent to
the dashboard in the hello and written to ``<journal>/api-token`` (owner-only)
for scripts:

    curl -X POST -H "Authorization: Bearer $(cat runs/prod-live/api-token)" \\
        localhost:8050/api/control/pause
    curl -X POST -H "Authorization: Bearer $(cat runs/prod-live/api-token)" \\
        -H 'Content-Type: application/json' -d '{"max_capital": 100}' \\
        localhost:8050/api/control/budget
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import heapq
import hmac
import ipaddress
import json
import logging
import os
import secrets
import threading
from collections import deque
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from aiohttp import WSCloseCode, web
from aiohttp.typedefs import Handler

from kalshi_lp.config import ApiConfig
from kalshi_lp.engine.controls import ControlError
from kalshi_lp.journal import RunJournal

log = logging.getLogger(__name__)

KEEP_PER_TYPE = 5000  # recent events of each type kept for new clients (the dashboard's limit)
TAIL_BYTES = 8_000_000  # of events.jsonl read at start-up to fill them
MAX_PENDING = 20_000  # events queued for one client before it counts as stuck
DEFAULT_POINTS = 2000
MAX_POINTS = 5000
FLUSH_SECONDS = 2.0  # on shutdown: time to deliver the final snapshot before closing

ENDPOINTS = {
    "/api": "this list",
    "/api/health": "status, session, exchange feed, connected dashboards",
    "/api/state": "the latest snapshot: totals, markets, resting orders, config",
    "/api/markets": "the snapshot's markets",
    "/api/orders": "the snapshot's resting orders",
    "/api/events": "recent journal events, oldest first (?type=fill&limit=100)",
    "/api/metrics": "totals history, evenly thinned (?points=2000)",
    "/api/scan": "the market scanner: the best markets by $/day at a fixed size (read-only)",
    "/api/ws": "WebSocket: a hello (state, events, metrics), then events and snapshots live",
    "/api/bots": "every bot running from this runs folder (the dashboard's bot switcher)",
    "/api/balance": "every balance change (fills, settlements, deposits, payouts) with its time",
    "/api/config": "the editable settings: file values, running values, schema",
    "/api/history/<ticker>": "every order action in one market, from the whole journal",
    "/api/control/<action>": "POST with the token: pause, resume, flatten, rescan, budget, stop, "
    "config ({changes: {section.setting: value}}), restart",
}
ACTIONS = ("pause", "resume", "flatten", "rescan", "budget", "stop", "config", "restart")
TOKEN_FILE = "api-token"
SESSION_COOKIE = "klp_session"
URL_FILE = "api-url"  # where a running bot's API listens, for other dashboards to find it


class Controls(Protocol):
    """What the buttons do: :class:`kalshi_lp.engine.controls.BotControls`."""

    async def pause(self) -> str: ...
    async def resume(self) -> str: ...
    async def flatten(self) -> str: ...
    async def rescan(self) -> str: ...
    async def set_budget(self, max_capital: Decimal) -> str: ...
    async def stop(self) -> str: ...
    def config(self) -> dict[str, Any]: ...
    async def set_config(self, changes: dict[str, Any]) -> str: ...
    async def restart(self) -> str: ...
    async def balance_history(self) -> list[dict[str, Any]]: ...


NOT_BUILT = """<!doctype html><title>KLP Terminal</title>
<body style="background:#000;color:#ffa200;font:14px monospace;padding:20px">
<p>The bot is running, but the dashboard app isn't built yet. Run:</p>
<pre>make dashboard</pre>
<p>then reload this page. The API is up: <a href="/api">/api</a></p></body>"""


class _Client:
    """One dashboard connection: what's waiting to be sent to it."""

    def __init__(self, ws: web.WebSocketResponse):
        self.ws = ws
        self.events: list[str] = []
        self.state: str | None = None  # only the newest snapshot matters
        self.scan: str | None = None  # likewise the newest scanner report
        self.stuck = False
        self.wake = asyncio.Event()
        self.sender: asyncio.Task[None] | None = None

    def push(self, channel: str, text: str) -> None:
        if channel == "state":
            self.state = text
        elif channel == "scan":
            self.scan = text
        elif len(self.events) < MAX_PENDING:
            self.events.append(text)
        else:
            self.stuck = True
        self.wake.set()

    def take(self) -> list[str]:
        """Frames to send now. Events go first: the snapshot already reflects them."""
        frames = []
        if self.events:
            frames.append('{"channel":"events","data":[' + ",".join(self.events) + "]}")
            self.events = []
        if self.state is not None:
            frames.append('{"channel":"state","data":' + self.state + "}")
            self.state = None
        if self.scan is not None:
            frames.append('{"channel":"scan","data":' + self.scan + "}")
            self.scan = None
        return frames


class _Recent:
    """The latest events of each type, as JSON lines, so fills aren't crowded out by logs.

    Metrics are left out: the history comes thinned from the metrics file instead.
    """

    def __init__(self, per_type: int):
        self.per_type = per_type
        self._by_type: dict[str, deque[tuple[float, str]]] = {}

    def add(self, line: str) -> None:
        record = json.loads(line)
        kind = record.get("type") if isinstance(record, dict) else None
        if not isinstance(kind, str) or kind == "metrics":
            return
        recent = self._by_type.setdefault(kind, deque(maxlen=self.per_type))
        recent.append((float(record.get("ts") or 0), line))

    def lines(self, kind: str | None = None, limit: int | None = None) -> list[str]:
        """Oldest first: one type, or all of them merged by time."""
        if kind is not None:
            items = list(self._by_type.get(kind, ()))
        else:
            items = list(heapq.merge(*self._by_type.values(), key=lambda item: item[0]))
        if limit is not None:
            items = items[-limit:] if limit > 0 else []
        return [line for _, line in items]


class DashboardApi:
    """Serves one journal: what the bot writes to it goes out to every dashboard."""

    def __init__(self, journal: RunJournal, cfg: ApiConfig, controls: Controls | None = None):
        self.journal = journal
        self.cfg = cfg
        self.controls = controls
        self.token = secrets.token_urlsafe(24)  # for the buttons; new every start
        self._session = secrets.token_urlsafe(24)  # the password's session cookie; new every start
        self.url: str | None = None
        self.recent = _Recent(KEEP_PER_TYPE)
        events = journal.dir / "events.jsonl"
        # Every fill ever (they're few, and the fill history needs them all); the rest from
        # the tail, so a large journal doesn't slow start-up.
        for line in _fill_lines(events):
            with contextlib.suppress(ValueError):
                self.recent.add(line)
        for line in _tail_lines(events, TAIL_BYTES):
            if not _is_fill(line):
                with contextlib.suppress(ValueError):
                    self.recent.add(line)
        # The previous session's last snapshot until the bot writes its first.
        self.state: str | None = None
        with contextlib.suppress(OSError):
            self.state = (journal.dir / "state.json").read_text() or None
        self.scan: str | None = None  # the market scanner's last report (survives restarts)
        with contextlib.suppress(OSError):
            self.scan = (journal.dir / "scan.json").read_text() or None
        self._clients: set[_Client] = set()
        self._closing = False
        self._loop_thread: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: web.AppRunner | None = None
        self._acting = asyncio.Lock()  # one control action at a time
        journal.subscribers.append(self._publish)
        self.app = self._build_app()

    @property
    def token_file(self) -> Path:
        return self.journal.dir / TOKEN_FILE

    # --------------------------------------------------------------- lifecycle

    async def start(self) -> bool:
        """Start serving. If the port is taken, warn and return False: the bot runs on without."""
        runner = web.AppRunner(self.app, access_log=None, shutdown_timeout=FLUSH_SECONDS)
        await runner.setup()
        try:
            await web.TCPSite(runner, self.cfg.host, self.cfg.port).start()
        except OSError as exc:
            log.warning(
                "dashboard API not started: can't listen on %s:%s (%s). Is another bot running? "
                "Set api.port to another port.",
                self.cfg.host,
                self.cfg.port,
                exc,
            )
            await runner.cleanup()
            self._unsubscribe()
            return False
        self._runner = runner
        host, port = runner.addresses[0][:2]
        self.url = f"http://{f'[{host}]' if ':' in host else host}:{port}"
        log.info("dashboard: %s  (API: %s/api)", self.url, self.url)
        with contextlib.suppress(OSError):
            (self.journal.dir / URL_FILE).write_text(self.url)
        if self.controls is not None:
            _write_private(self.token_file, self.token)
            log.info("dashboard controls on; token for scripts in %s", self.token_file)
        return True

    async def stop(self) -> None:
        """Deliver the final snapshot to connected dashboards, then close."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            with contextlib.suppress(FileNotFoundError):
                (self.journal.dir / URL_FILE).unlink()
            if self.controls is not None:
                with contextlib.suppress(FileNotFoundError):
                    self.token_file.unlink()
        self._unsubscribe()

    def _unsubscribe(self) -> None:
        with contextlib.suppress(ValueError):
            self.journal.subscribers.remove(self._publish)

    async def _on_startup(self, _: web.Application) -> None:
        self._loop = asyncio.get_running_loop()
        self._loop_thread = threading.get_ident()

    async def _on_shutdown(self, _: web.Application) -> None:
        self._closing = True
        for client in self._clients:
            client.wake.set()
        senders = [c.sender for c in self._clients if c.sender is not None]
        if senders:
            await asyncio.wait(senders, timeout=FLUSH_SECONDS)
        for client in list(self._clients):  # still greeting, or too slow to flush
            await client.ws.close(code=WSCloseCode.GOING_AWAY, message=b"bot stopped")

    # ---------------------------------------------------------------- publish

    def _publish(self, channel: str, text: str) -> None:
        """Journal subscriber: keep the latest, and queue it for every dashboard. Never blocks."""
        if self._loop is not None and threading.get_ident() != self._loop_thread:
            self._loop.call_soon_threadsafe(self._publish, channel, text)
            return
        if channel == "state":
            self.state = text
        elif channel == "scan":
            self.scan = text
        else:
            try:
                self.recent.add(text)
            except ValueError:
                return
        for client in self._clients:
            client.push(channel, text)

    # ------------------------------------------------------------------ routes

    def _build_app(self) -> web.Application:
        app = web.Application(middlewares=[self._password, self._guard, self._cors])
        app.router.add_get("/api", self._index)
        app.router.add_get("/api/health", self._health)
        app.router.add_get("/api/state", self._state)
        app.router.add_get("/api/markets", self._markets)
        app.router.add_get("/api/orders", self._orders)
        app.router.add_get("/api/events", self._events)
        app.router.add_get("/api/metrics", self._metrics)
        app.router.add_get("/api/scan", self._scan)
        app.router.add_get("/api/config", self._config)
        app.router.add_get("/api/bots", self._bots)
        app.router.add_get("/api/balance", self._balance)
        app.router.add_get("/api/history/{ticker}", self._history)
        app.router.add_get("/api/ws", self._ws)
        app.router.add_post("/api/control/{action}", self._control)
        app.router.add_get("/api/{rest:.*}", self._not_found)
        app.router.add_get("/{path:.*}", self._static)
        app.on_startup.append(self._on_startup)
        app.on_shutdown.append(self._on_shutdown)
        return app

    @web.middleware
    async def _guard(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        """Only this origin or a local one (the Vite dev server); on localhost, only local names."""
        origin = request.headers.get("Origin")
        if origin is not None:
            parts = urlsplit(origin)
            if parts.netloc != request.host and not _is_local(parts.hostname):
                raise web.HTTPForbidden(text="cross-origin requests are not allowed")
        if _is_local(self.cfg.host) and not _is_local(urlsplit(f"//{request.host}").hostname):
            raise web.HTTPForbidden(text="unexpected Host header")
        return await handler(request)

    @web.middleware
    async def _password(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        """With api.password (serving beyond localhost): ask for it before serving anything.

        The browser asks once (HTTP basic auth, any user name); a session cookie then covers
        the page's own requests and its WebSocket. Scripts may send the control token instead.
        """
        password = self.cfg.password
        if not password or request.path == "/api/health":  # the host's health check: status only
            return await handler(request)
        cookie = request.cookies.get(SESSION_COOKIE, "")
        bearer = request.headers.get("Authorization", "")
        if hmac.compare_digest(cookie, self._session) or (
            bearer.startswith("Bearer ") and hmac.compare_digest(bearer[7:], self.token)
        ):
            return await handler(request)
        if bearer.startswith("Basic "):
            try:
                _, _, given = base64.b64decode(bearer[6:]).decode().partition(":")
            except ValueError:
                given = ""
            if hmac.compare_digest(given.encode(), password.encode()):
                response = await handler(request)
                response.set_cookie(
                    SESSION_COOKIE,
                    self._session,
                    httponly=True,
                    samesite="Strict",
                    secure=request.secure or request.headers.get("X-Forwarded-Proto") == "https",
                )
                return response
        return web.Response(
            status=401,
            text="password required",
            headers={"WWW-Authenticate": 'Basic realm="kalshi-lp", charset="UTF-8"'},
        )

    @web.middleware
    async def _cors(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        """Let a dashboard served by another local bot read and drive this one (the switcher).

        _guard already refused other sites, so any Origin that gets here is local.
        """
        origin = request.headers.get("Origin")
        if origin is None or urlsplit(origin).netloc == request.host:
            return await handler(request)
        headers = {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Headers": "Authorization, Content-Type",
            "Access-Control-Allow-Methods": "GET, POST",
            "Vary": "Origin",
        }
        if request.method == "OPTIONS":  # preflight for the buttons' POSTs
            return web.Response(status=204, headers=headers)
        response = await handler(request)
        response.headers.update(headers)
        return response

    async def _balance(self, _: web.Request) -> web.Response:
        if self.controls is None:
            return _refuse(404, "the bot's controls are switched off (api.controls)")
        try:
            changes = await self.controls.balance_history()
        except ControlError as exc:
            return _refuse(400, str(exc))
        except Exception as exc:  # Kalshi unreachable, say: report it, don't crash the page
            log.warning("balance history failed: %s", exc)
            return _refuse(502, f"couldn't read the account from Kalshi: {exc}")
        return web.Response(
            text=json.dumps(changes, default=_json_default), content_type="application/json"
        )

    async def _bots(self, _: web.Request) -> web.Response:
        """Bots that registered an API in this runs folder (each journal's api-url)."""
        bots = []
        for path in sorted(self.journal.dir.parent.glob(f"*/{URL_FILE}")):
            with contextlib.suppress(OSError):
                url = path.read_text().strip()
                bots.append({"run_id": path.parent.name, "url": url, "self": url == self.url})
        return web.json_response(bots)

    async def _index(self, _: web.Request) -> web.Response:
        return web.json_response({"run_id": self.journal.run_id, "endpoints": ENDPOINTS})

    async def _health(self, _: web.Request) -> web.Response:
        state = self._state_dict()
        return web.json_response(
            {
                "status": state.get("status", "starting"),
                "run_id": self.journal.run_id,
                "session": self.journal.session,
                "updated_at": state.get("updated_at"),
                "exchange_feed_connected": state.get("ws_connected"),
                "dashboards_connected": len(self._clients),
            }
        )

    async def _state(self, _: web.Request) -> web.Response:
        return web.Response(
            text=self.state or '{"status":"starting"}', content_type="application/json"
        )

    async def _markets(self, _: web.Request) -> web.Response:
        return web.json_response(self._state_dict().get("markets", []))

    async def _orders(self, _: web.Request) -> web.Response:
        return web.json_response(self._state_dict().get("orders", []))

    async def _events(self, request: web.Request) -> web.Response:
        limit = _int_param(request, "limit", 500, 1, KEEP_PER_TYPE * 10)
        lines = self.recent.lines(request.query.get("type"), limit)
        return web.Response(text="[" + ",".join(lines) + "]", content_type="application/json")

    async def _history(self, request: web.Request) -> web.Response:
        """All of one market's order events (the market popup's full history)."""
        ticker = request.match_info["ticker"]
        lines = await asyncio.to_thread(_market_orders, self.journal.dir / "events.jsonl", ticker)
        return web.Response(text="[" + ",".join(lines) + "]", content_type="application/json")

    async def _config(self, _: web.Request) -> web.Response:
        if self.controls is None:
            return _refuse(404, "the bot's controls are switched off (api.controls)")
        try:
            return web.json_response(self.controls.config())
        except ControlError as exc:
            return _refuse(400, str(exc))

    async def _scan(self, _: web.Request) -> web.Response:
        return web.Response(text=self.scan or "null", content_type="application/json")

    async def _metrics(self, request: web.Request) -> web.Response:
        points = _int_param(request, "points", DEFAULT_POINTS, 2, MAX_POINTS)
        lines = await asyncio.to_thread(_metric_lines, self.journal.dir / "metrics.jsonl", points)
        return web.Response(text="[" + ",".join(lines) + "]", content_type="application/json")

    async def _not_found(self, _: web.Request) -> web.Response:
        return web.json_response({"error": "not found", "endpoints": list(ENDPOINTS)}, status=404)

    def _controls_info(self) -> dict[str, Any] | None:
        if self.controls is None:
            return None
        return {"token": self.token, "actions": list(ACTIONS)}

    async def _control(self, request: web.Request) -> web.Response:
        """A button press: ``POST /api/control/<action>`` with the token."""
        if self.controls is None:
            return _refuse(404, "controls are off (api.controls in the config)")
        given = request.headers.get("Authorization", "")
        if not hmac.compare_digest(given.encode(), f"Bearer {self.token}".encode()):
            return _refuse(401, "missing or wrong token (see <journal>/api-token)")
        action = request.match_info["action"]
        if action not in ACTIONS:
            return _refuse(404, f"unknown action; one of: {', '.join(ACTIONS)}")
        if self._acting.locked():
            return _refuse(409, "another action is still running")
        async with self._acting:
            log.info("control: %s (from %s)", action, request.remote)
            try:
                if action == "budget":
                    message = await self.controls.set_budget(await _max_capital(request))
                elif action == "config":
                    message = await self.controls.set_config(await _changes(request))
                else:
                    message = await getattr(self.controls, action)()
            except ControlError as exc:
                return _refuse(400, str(exc))
        return web.json_response({"ok": True, "message": message})

    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        points = _int_param(request, "points", DEFAULT_POINTS, 2, MAX_POINTS)
        ws = web.WebSocketResponse(heartbeat=20, compress=False)
        await ws.prepare(request)
        client = _Client(ws)
        # Taken in the same instant the client starts collecting: nothing missed or sent twice.
        events, state, scan = self.recent.lines(), self.state, self.scan
        self._clients.add(client)
        try:
            metrics = await asyncio.to_thread(
                _metric_lines, self.journal.dir / "metrics.jsonl", points
            )
            await ws.send_str(
                '{"channel":"hello","run_id":'
                + json.dumps(self.journal.run_id)
                + ',"state":'
                + (state or "null")
                + ',"events":['
                + ",".join(events)
                + '],"metrics":['
                + ",".join(metrics)
                + '],"scan":'
                + (scan or "null")
                + ',"controls":'
                + json.dumps(self._controls_info())
                + "}"
            )
            client.sender = asyncio.create_task(self._send_loop(client), name="dashboard-ws")
            async for _ in ws:  # nothing to receive yet; this answers pings and sees the close
                pass
        except ConnectionError:
            pass
        finally:
            self._clients.discard(client)
            if client.sender is not None and not client.sender.done():
                client.sender.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await client.sender
        return ws

    async def _send_loop(self, client: _Client) -> None:
        ws = client.ws
        try:
            while True:
                await client.wake.wait()
                client.wake.clear()
                if client.stuck:
                    log.warning("a dashboard fell too far behind; disconnecting it")
                    await ws.close(code=WSCloseCode.TRY_AGAIN_LATER, message=b"too far behind")
                    return
                for frame in client.take():
                    await ws.send_str(frame)
                if self._closing:
                    await ws.close(code=WSCloseCode.GOING_AWAY, message=b"bot stopped")
                    return
        except ConnectionError:
            return  # the dashboard went away
        except Exception:
            log.exception("dashboard connection failed")
            await ws.close(code=WSCloseCode.INTERNAL_ERROR)

    def _state_dict(self) -> dict[str, Any]:
        if not self.state:
            return {}
        with contextlib.suppress(ValueError):
            data = json.loads(self.state)
            if isinstance(data, dict):
                return data
        return {}

    async def _static(self, request: web.Request) -> web.StreamResponse:
        """The built dashboard. Unknown paths get the app (it's a single page)."""
        if self.cfg.static_dir is None or not (self.cfg.static_dir / "index.html").is_file():
            return web.Response(text=NOT_BUILT, content_type="text/html")
        root = self.cfg.static_dir.resolve()
        index = root / "index.html"
        path = (root / request.match_info["path"]).resolve()
        if not path.is_relative_to(root):  # no escaping the build directory
            raise web.HTTPNotFound()
        if not path.is_file():
            path = index
        headers = {"Cache-Control": "no-store"} if path == index else None
        return web.FileResponse(path, headers=headers)


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"not JSON-able: {type(value).__name__}")


def _is_local(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _refuse(status: int, error: str) -> web.Response:
    return web.json_response({"ok": False, "error": error}, status=status)


async def _changes(request: web.Request) -> dict[str, Any]:
    """The settings from a ``{"changes": {"risk.max_capital": 300}}`` body."""
    try:
        changes = (await request.json())["changes"]
    except (ValueError, KeyError, TypeError):
        raise ControlError('send JSON like {"changes": {"risk.max_capital": 300}}') from None
    if not isinstance(changes, dict):
        raise ControlError('"changes" must map "section.setting" to a value')
    return changes


async def _max_capital(request: web.Request) -> Decimal:
    """The budget from a ``{"max_capital": 100}`` body."""
    try:
        body = await request.json()
        return Decimal(str(body["max_capital"]))
    except (ValueError, KeyError, TypeError, InvalidOperation):
        raise ControlError('send JSON like {"max_capital": 100}') from None


def _write_private(path: Path, text: str) -> None:
    """Write ``text`` readable by this user only."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        os.fchmod(f.fileno(), 0o600)  # also if the file already existed
        f.write(text + "\n")


def _int_param(request: web.Request, name: str, default: int, low: int, high: int) -> int:
    raw = request.query.get(name)
    if raw is None:
        return default
    try:
        return min(max(int(raw), low), high)
    except ValueError:
        raise web.HTTPBadRequest(text=f"{name} must be a whole number") from None


def _tail_lines(path: Path, max_bytes: int) -> list[str]:
    """The complete lines in about the last ``max_bytes`` of a file."""
    try:
        with path.open("rb") as f:
            start = max(f.seek(0, os.SEEK_END) - max_bytes, 0)
            f.seek(start)
            data = f.read()
    except FileNotFoundError:
        return []
    if start > 0:  # the first line is probably cut
        cut = data.find(b"\n")
        data = data[cut + 1 :] if cut >= 0 else b""
    end = data.rfind(b"\n") + 1  # a line still being written waits for next time
    return [line for line in data[:end].decode(errors="replace").splitlines() if line]


_KEEP_ALL = ('"type": "fill"', '"type": "fill_risk"')  # few, and needed in full


def _is_fill(line: str) -> bool:
    head = line[:80]  # the journal writes ts and type first
    return any(mark in head for mark in _KEEP_ALL)


def _fill_lines(path: Path) -> list[str]:
    """Every fill (and fill_risk record) in the journal file, oldest first."""
    marks = [m.encode() for m in _KEEP_ALL]
    try:
        with path.open("rb") as f:
            return [
                line.decode(errors="replace").rstrip("\n")
                for line in f
                if any(m in line[:80] for m in marks) and line.endswith(b"\n")
            ]
    except FileNotFoundError:
        return []


def _market_orders(path: Path, ticker: str) -> list[str]:
    """Every order event for ``ticker`` in the journal file, oldest first (a substring scan)."""
    kind = b'"type": "order"'
    needle = json.dumps({"ticker": ticker})[1:-1].encode()  # '"ticker": "..."', as written
    try:
        with path.open("rb") as f:
            return [
                line.decode(errors="replace").rstrip("\n")
                for line in f
                if kind in line[:80] and needle in line and line.endswith(b"\n")
            ]
    except FileNotFoundError:
        return []


def _metric_lines(path: Path, points: int) -> list[str]:
    """The metrics history, evenly thinned to at most ``points`` lines (always keeping the last)."""
    lines = _tail_lines(path, 1 << 62)
    if len(lines) <= points:
        return lines
    step = len(lines) / (points - 1)
    return [lines[int(i * step)] for i in range(points - 1)] + [lines[-1]]
