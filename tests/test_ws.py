"""KalshiStream against a real local WebSocket server."""

import asyncio
import json

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from websockets.asyncio.server import ServerConnection, serve

from kalshi_lp.exchange.auth import Signer
from kalshi_lp.exchange.ws import KalshiStream


class FakeKalshiWS:
    """Answers subscribe commands and lets the test push messages."""

    def __init__(self) -> None:
        self.connections: list[ServerConnection] = []
        self.headers: list[dict[str, str]] = []
        self.commands: list[dict] = []
        self.next_sid = 1

    async def handler(self, ws: ServerConnection) -> None:
        self.connections.append(ws)
        self.headers.append(dict(ws.request.headers))
        async for raw in ws:
            cmd = json.loads(raw)
            self.commands.append(cmd)
            if cmd["cmd"] == "subscribe":
                sid, self.next_sid = self.next_sid, self.next_sid + 1
                await ws.send(
                    json.dumps({"id": cmd["id"], "type": "subscribed", "msg": {"sid": sid}})
                )
            else:
                await ws.send(json.dumps({"id": cmd["id"], "type": "ok"}))

    async def push(self, message: dict) -> None:
        await self.connections[-1].send(json.dumps(message))


async def wait_until(predicate, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.fixture
async def server():
    fake = FakeKalshiWS()
    async with serve(fake.handler, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        yield fake, f"ws://127.0.0.1:{port}/trade-api/ws/v2"


async def test_signed_connect_subscribe_and_dispatch(server) -> None:
    fake, url = server
    received: list[dict] = []
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    stream = KalshiStream(url, Signer("kid", key), received.append)
    task = asyncio.create_task(stream.run())
    try:
        await asyncio.wait_for(stream.connected.wait(), 3)
        assert fake.headers[0]["kalshi-access-key"] == "kid"
        sid = await stream.subscribe(["orderbook_delta"], ["MKT"])
        assert sid == 1
        assert fake.commands[0]["params"] == {
            "channels": ["orderbook_delta"],
            "market_tickers": ["MKT"],
        }
        await fake.push({"type": "orderbook_delta", "sid": 1, "seq": 1, "msg": {"x": 1}})
        await wait_until(lambda: received)
        assert received[0]["msg"] == {"x": 1}
    finally:
        await stream.close()
        await task


async def test_sequence_gap_triggers_callback(server) -> None:
    fake, url = server
    received: list[dict] = []
    gaps: list[int] = []

    async def on_gap(sid: int) -> None:
        gaps.append(sid)

    stream = KalshiStream(url, None, received.append, on_gap=on_gap)
    task = asyncio.create_task(stream.run())
    try:
        await asyncio.wait_for(stream.connected.wait(), 3)
        for seq in (1, 2, 4):  # 3 is missing
            await fake.push({"type": "orderbook_delta", "sid": 7, "seq": seq, "msg": {}})
        await wait_until(lambda: gaps)
        assert gaps == [7]
        assert len(received) == 2  # the message after the gap is not applied
    finally:
        await stream.close()
        await task


async def test_reconnects_and_resubscribes(server) -> None:
    fake, url = server
    connects = disconnects = 0

    async def on_connect() -> None:
        nonlocal connects
        connects += 1

    async def on_disconnect() -> None:
        nonlocal disconnects
        disconnects += 1

    stream = KalshiStream(
        url,
        None,
        lambda m: None,
        on_connect=on_connect,
        on_disconnect=on_disconnect,
        max_backoff=0.05,
    )
    task = asyncio.create_task(stream.run())
    try:
        await wait_until(lambda: connects == 1)
        await fake.connections[-1].close()  # server drops us
        await wait_until(lambda: connects == 2)
        assert disconnects == 1
        assert len(fake.connections) == 2
    finally:
        await stream.close()
        await task
