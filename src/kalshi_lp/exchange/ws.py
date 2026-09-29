"""WebSocket client for Kalshi's streaming API.

Handles the transport concerns so the feed layer only sees parsed messages:

* Signed handshake (the signature covers ``timestamp + "GET" + /trade-api/ws/v2``).
* Commands (``subscribe``, ``update_subscription``, ``unsubscribe``) sent with
  an ``id`` and awaited until the matching response arrives.
* Sequence checking: each subscription's ``seq`` must increase by one. A gap
  means a missed message, so the subscription's data can't be trusted; the
  ``on_gap`` callback lets the owner resubscribe for a fresh snapshot.
* Reconnection with exponential backoff. ``on_connect`` runs after every
  (re)connect so the owner can restore subscriptions; ``on_disconnect`` runs
  whenever the socket drops. Kalshi pings every 10s; the ``websockets``
  library answers automatically.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from kalshi_lp.exchange.auth import Signer
from kalshi_lp.exchange.errors import KalshiError

log = logging.getLogger(__name__)

Message = dict[str, Any]
Callback = Callable[[], Awaitable[None]]


class StreamError(KalshiError):
    pass


class KalshiStream:
    def __init__(
        self,
        url: str,
        signer: Signer | None,
        on_message: Callable[[Message], None],
        *,
        on_connect: Callback | None = None,
        on_disconnect: Callback | None = None,
        on_gap: Callable[[int], Awaitable[None]] | None = None,
        command_timeout: float = 10.0,
        max_backoff: float = 30.0,
    ):
        self.url = url
        self._sign_path = urlsplit(url).path
        self._signer = signer
        self._on_message = on_message
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_gap = on_gap
        self._command_timeout = command_timeout
        self._max_backoff = max_backoff
        self._ws: ClientConnection | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[Message]] = {}
        self._seq: dict[int, int] = {}
        self._closing = False
        self.connected = asyncio.Event()

    # ------------------------------------------------------------ lifecycle

    async def run(self) -> None:
        """Connect and read forever, reconnecting on failure, until :meth:`close`."""
        attempt = 0
        while not self._closing:
            try:
                headers = self._signer.headers("GET", self._sign_path) if self._signer else {}
                async with connect(self.url, additional_headers=headers) as ws:
                    self._ws = ws
                    self._seq.clear()
                    attempt = 0
                    log.info("websocket connected: %s", self.url)
                    reader = asyncio.create_task(self._read(ws))
                    self.connected.set()
                    try:
                        if self._on_connect:
                            await self._on_connect()
                        await reader
                    finally:
                        reader.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await reader
            except (OSError, ConnectionClosed, StreamError, TimeoutError) as exc:
                if not self._closing:
                    log.warning("websocket disconnected: %s", exc)
            finally:
                await self._handle_disconnect()
            if self._closing:
                break
            attempt += 1
            delay = min(self._max_backoff, 0.5 * 2 ** min(attempt, 6)) * (0.5 + random.random())
            log.info("reconnecting in %.1fs", delay)
            await asyncio.sleep(delay)

    async def close(self) -> None:
        self._closing = True
        if self._ws is not None:
            await self._ws.close()

    async def _handle_disconnect(self) -> None:
        was_connected = self.connected.is_set()
        self.connected.clear()
        self._ws = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(StreamError("connection closed"))
        self._pending.clear()
        if was_connected and self._on_disconnect:
            await self._on_disconnect()

    # --------------------------------------------------------------- reading

    async def _read(self, ws: ClientConnection) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                log.warning("unparseable websocket message: %.200s", raw)
                continue
            await self._dispatch(msg)

    async def _dispatch(self, msg: Message) -> None:
        sid, seq = msg.get("sid"), msg.get("seq")
        mid = msg.get("id")
        if mid is not None and mid in self._pending:
            if isinstance(sid, int) and isinstance(seq, int):
                self._seq[sid] = seq  # replies can consume a sequence number
            fut = self._pending.pop(mid)
            if not fut.done():
                fut.set_result(msg)
            return

        if isinstance(sid, int) and isinstance(seq, int):
            last = self._seq.get(sid)
            self._seq[sid] = seq
            if last is not None and seq != last + 1:
                log.warning("sequence gap on sid %d: %d -> %d", sid, last, seq)
                if self._on_gap:
                    await self._on_gap(sid)
                return

        if msg.get("type") == "error":
            log.error("websocket error: %s", msg.get("msg"))
            return
        try:
            self._on_message(msg)
        except Exception:  # a bad message must not kill the stream
            log.exception("error handling websocket message %s", msg.get("type"))

    # -------------------------------------------------------------- commands

    async def command(self, cmd: str, params: dict[str, Any] | None = None) -> Message:
        ws = self._ws
        if ws is None:
            raise StreamError("not connected")
        mid = next(self._ids)
        fut: asyncio.Future[Message] = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        payload: Message = {"id": mid, "cmd": cmd}
        if params is not None:
            payload["params"] = params
        await ws.send(json.dumps(payload))
        try:
            reply = await asyncio.wait_for(fut, self._command_timeout)
        finally:
            self._pending.pop(mid, None)
        if reply.get("type") == "error":
            raise StreamError(f"{cmd} failed: {reply.get('msg')}")
        return reply

    async def subscribe(self, channels: list[str], tickers: list[str] | None = None) -> int:
        params: dict[str, Any] = {"channels": channels}
        if tickers:
            params["market_tickers"] = tickers
        reply = await self.command("subscribe", params)
        return int(reply["msg"]["sid"])

    async def update_markets(self, sid: int, tickers: list[str], *, add: bool) -> None:
        await self.command(
            "update_subscription",
            {
                "sids": [sid],
                "market_tickers": tickers,
                "action": "add_markets" if add else "delete_markets",
            },
        )

    async def unsubscribe(self, sids: list[int]) -> None:
        await self.command("unsubscribe", {"sids": sids})
        for sid in sids:
            self._seq.pop(sid, None)
