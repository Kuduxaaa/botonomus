import asyncio
import base64
import hashlib
import json
import struct

import pytest

from botonomus.cdp import Connection, ProtocolError, TargetClosedError
from botonomus.cdp.input import key_definition
from botonomus.cdp.polling import looks_like_function as _looks_like_function
from botonomus.cdp.websocket import WebSocket, _apply_mask

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


async def _read_frame(reader):
    first, second = await reader.readexactly(2)
    length = second & 0x7F
    if length == 126:
        (length,) = struct.unpack("!H", await reader.readexactly(2))
    elif length == 127:
        (length,) = struct.unpack("!Q", await reader.readexactly(8))
    assert second & 0x80, "client frames must be masked"
    mask = await reader.readexactly(4)
    return first & 0x0F, _apply_mask(await reader.readexactly(length), mask)


def _frame(opcode, payload, fin=True):
    length = len(payload)
    head = bytes([(0x80 if fin else 0) | opcode])
    if length < 126:
        head += bytes([length])
    elif length < 65536:
        head += bytes([126]) + struct.pack("!H", length)
    else:
        head += bytes([127]) + struct.pack("!Q", length)
    return head + payload


class FakeDevTools:
    """Loopback websocket server that answers CDP commands with a handler."""

    def __init__(self, respond):
        self.respond = respond
        self.received = []
        self.writers = []

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/devtools/browser/x"
        return self

    async def __aexit__(self, *exc):
        for writer in self.writers:
            writer.close()
        self.server.close()

    async def _client(self, reader, writer):
        self.writers.append(writer)
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        key = next(
            line.split(":", 1)[1].strip()
            for line in head.split("\r\n")
            if line.lower().startswith("sec-websocket-key")
        )
        accept = base64.b64encode(hashlib.sha1(key.encode() + GUID).digest()).decode()
        writer.write(
            "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode()
        )
        try:
            while True:
                opcode, payload = await _read_frame(reader)
                if opcode == 0x8:
                    writer.write(_frame(0x8, payload))
                    break
                message = json.loads(payload)
                self.received.append(message)
                for reply in self.respond(message):
                    writer.write(_frame(0x1, json.dumps(reply).encode()))
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass


async def test_websocket_roundtrip_large_and_fragmented():
    big = "x" * 70000

    def respond(message):
        return [{"id": message["id"], "result": {"echo": message["params"]["v"]}}]

    async with FakeDevTools(respond) as server:
        socket = await WebSocket.connect(server.url)
        await socket.send(json.dumps({"id": 1, "method": "Echo", "params": {"v": big}}))
        assert json.loads(await socket.recv())["result"]["echo"] == big
        await socket.close()


async def test_websocket_reassembles_fragments_and_answers_ping():
    async def handler(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 101 OK\r\nSec-WebSocket-Accept: bad\r\n\r\n")

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    with pytest.raises(ConnectionError):
        await WebSocket.connect(f"ws://127.0.0.1:{port}/")
    server.close()

    reader = asyncio.StreamReader()
    reader.feed_data(_frame(0x9, b"hi") + _frame(0x1, b"he", fin=False) + _frame(0x0, b"llo"))
    sent = []

    class Writer:
        def write(self, data):
            sent.append(data)

        async def drain(self):
            pass

    socket = WebSocket(reader, Writer())
    assert await socket.recv() == "hello"
    assert sent and sent[0][0] == 0x8A  # pong


def test_websocket_rejects_non_loopback():
    with pytest.raises(ValueError):
        asyncio.run(WebSocket.connect("ws://example.com/devtools"))


async def test_connection_routes_results_errors_and_session_events():
    def respond(message):
        if message["method"] == "Fail.me":
            return [{"id": message["id"], "error": {"code": -32000, "message": "nope"}}]
        replies = [{"id": message["id"], "result": {"ok": message.get("sessionId")}}]
        if message["method"] == "Page.navigate":
            replies.insert(
                0, {"method": "Page.loadEventFired", "params": {"t": 1}, "sessionId": "S1"}
            )
        return replies

    async with FakeDevTools(respond) as server:
        connection = await Connection.connect(server.url)
        session = connection.session("S1")
        assert await connection.send("Browser.getVersion") == {"ok": None}
        waiter = asyncio.create_task(session.wait_for("Page.loadEventFired", timeout=2))
        await asyncio.sleep(0)
        assert await session.send("Page.navigate", {"url": "x"}) == {"ok": "S1"}
        assert (await waiter) == {"t": 1}
        with pytest.raises(ProtocolError) as error:
            await connection.send("Fail.me")
        assert error.value.code == -32000
        await connection.close()
        with pytest.raises(TargetClosedError):
            await connection.send("Browser.getVersion")


async def test_connection_fails_pending_when_socket_drops():
    async with FakeDevTools(lambda message: []) as server:
        connection = await Connection.connect(server.url)
        pending = asyncio.create_task(connection.send("Never.answered", timeout=5))
        await asyncio.sleep(0.1)
        for writer in server.writers:
            writer.close()
        with pytest.raises(TargetClosedError):
            await pending


@pytest.mark.parametrize(
    ("key", "code", "vk", "shift"),
    [
        ("a", "KeyA", 65, False),
        ("A", "KeyA", 65, True),
        ("7", "Digit7", 55, False),
        ("&", "Digit7", 55, True),
        ("?", "Slash", 191, True),
        ("Enter", "Enter", 13, False),
        (" ", "Space", 32, False),
    ],
)
def test_key_definitions(key, code, vk, shift):
    definition = key_definition(key)
    assert (definition.code, definition.key_code, definition.shift) == (code, vk, shift)


def test_unmapped_characters_fall_back_to_composed_text():
    assert key_definition("ქ") is None
    assert key_definition("NotAKey") is None


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("() => 1", True),
        ("(a) => a.x", True),
        ("x => x", True),
        ("async () => 1", True),
        ("function () { return 1 }", True),
        ("document.title", False),
        ("navigator.webdriver", False),
        ("[1,2].map(x => x)", False),
    ],
)
def test_function_detection(source, expected):
    assert _looks_like_function(source) is expected


async def test_event_waiters_fail_when_their_session_detaches():
    def respond(message):
        return [{"id": message["id"], "result": {}}]

    async with FakeDevTools(respond) as server:
        connection = await Connection.connect(server.url)
        waiter = asyncio.create_task(connection.session("S9").wait_for("Page.lifecycleEvent"))
        other = asyncio.create_task(connection.session("S8").wait_for("Page.lifecycleEvent"))
        await asyncio.sleep(0)
        connection._dispatch({"method": "Target.detachedFromTarget", "params": {"sessionId": "S9"}})
        with pytest.raises(TargetClosedError):
            await asyncio.wait_for(waiter, 1)
        assert not other.done()
        await connection.close()
        with pytest.raises(TargetClosedError):
            await asyncio.wait_for(other, 1)
