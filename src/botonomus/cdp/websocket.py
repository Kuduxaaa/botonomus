"""Minimal RFC 6455 WebSocket client for the loopback DevTools endpoint.

Stdlib only, so the native driver needs no third-party transport. It supports
exactly what Chrome's DevTools server uses: text messages (fragmented or not),
ping/pong and the close handshake. Only ``ws://`` on loopback is accepted because
the DevTools port is never exposed beyond the local machine.
"""

import asyncio
import base64
import hashlib
import os
import struct
from urllib.parse import urlsplit

_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_MESSAGE = 512 * 1024 * 1024  # screenshots and large DOM reads can be big


class WebSocketClosed(ConnectionError):
    """The DevTools socket closed."""


class WebSocket:
    """A connected client WebSocket.

    Sends are serialized by a lock, so concurrent coroutines may call
    `send`; `recv` must only be awaited by one reader at a time.

    Args:
        reader: Stream positioned after the HTTP upgrade response.
        writer: Stream the frames are written to.

    Attributes:
        closed: Set once either side started the close handshake or the stream ended.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._send_lock = asyncio.Lock()
        self.closed = False

    @classmethod
    async def connect(cls, url: str, timeout: float = 10.0) -> "WebSocket":
        """Open a TCP connection and perform the WebSocket upgrade.

        Args:
            url: A ``ws://`` URL on ``127.0.0.1``, ``localhost`` or ``::1``.
            timeout: Seconds allowed for connecting and the upgrade response.

        Returns:
            The connected socket.

        Raises:
            ValueError: If ``url`` is not a loopback ``ws://`` URL.
            ConnectionError: If the server refuses the upgrade or answers with a
                wrong ``Sec-WebSocket-Accept`` key.
            TimeoutError: If ``timeout`` elapses.
        """
        parts = urlsplit(url)
        if parts.scheme != "ws" or parts.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Only loopback ws:// endpoints are supported")
        port = parts.port or 80
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_connection(parts.hostname, port, limit=_MAX_MESSAGE)
            key = base64.b64encode(os.urandom(16))
            path = parts.path or "/"
            writer.write(
                f"GET {path} HTTP/1.1\r\nHost: {parts.hostname}:{port}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key.decode()}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
            )
            await writer.drain()
            head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        if len(lines[0].split()) < 2 or lines[0].split()[1] != "101":
            writer.close()
            raise ConnectionError(f"WebSocket upgrade refused: {lines[0]!r}")
        headers = {
            name.strip().lower(): value.strip()
            for name, _, value in (line.partition(":") for line in lines[1:] if line)
        }
        expected = base64.b64encode(hashlib.sha1(key + _GUID).digest()).decode()
        if headers.get("sec-websocket-accept") != expected:
            writer.close()
            raise ConnectionError("WebSocket accept key mismatch")
        return cls(reader, writer)

    async def send(self, text: str) -> None:
        """Send one text message as a single masked frame.

        Args:
            text: The message.

        Raises:
            WebSocketClosed: If the socket is closed.
        """
        await self._send_frame(0x1, text.encode())

    async def recv(self) -> str:
        """Receive the next complete message, reassembling fragments.

        Pings are answered and pongs skipped transparently.

        Returns:
            The message decoded as UTF-8.

        Raises:
            WebSocketClosed: If the peer closes or the stream ends.
            ConnectionError: On protocol violations or oversized messages.
        """
        chunks: list[bytes] = []
        size = 0
        while True:
            fin, opcode, payload = await self._read_frame()
            if opcode == 0x8:
                await self._close_reply(payload)
                raise WebSocketClosed("DevTools socket closed")
            if opcode == 0x9:
                await self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2) and chunks:
                raise ConnectionError("Unexpected new message inside a fragmented message")
            if opcode == 0x0 and not chunks:
                raise ConnectionError("Continuation frame without a message")
            chunks.append(payload)
            size += len(payload)
            if size > _MAX_MESSAGE:
                raise ConnectionError("WebSocket message too large")
            if fin:
                return b"".join(chunks).decode()

    async def close(self) -> None:
        """Send a normal close frame and close the stream; safe to call twice."""
        if not self.closed:
            self.closed = True
            try:
                await self._send_frame(0x8, struct.pack("!H", 1000), force=True)
            except (ConnectionError, OSError):
                pass
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _close_reply(self, payload: bytes) -> None:
        if not self.closed:
            try:
                await self._send_frame(0x8, payload[:2], force=True)
            except (ConnectionError, OSError):
                pass
        self.closed = True
        self._writer.close()

    async def _read_frame(self) -> tuple[bool, int, bytes]:
        try:
            first, second = await self._reader.readexactly(2)
            length = second & 0x7F
            if length == 126:
                (length,) = struct.unpack("!H", await self._reader.readexactly(2))
            elif length == 127:
                (length,) = struct.unpack("!Q", await self._reader.readexactly(8))
            if length > _MAX_MESSAGE:
                raise ConnectionError("WebSocket frame too large")
            mask = await self._reader.readexactly(4) if second & 0x80 else None
            payload = await self._reader.readexactly(length)
        except asyncio.IncompleteReadError as exc:
            self.closed = True
            raise WebSocketClosed("DevTools socket ended") from exc
        if mask is not None:
            payload = _apply_mask(payload, mask)
        return bool(first & 0x80), first & 0x0F, payload

    async def _send_frame(self, opcode: int, payload: bytes, force: bool = False) -> None:
        if self.closed and not force:
            raise WebSocketClosed("DevTools socket closed")
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", 0x80 | opcode, 0x80 | length)
        elif length < 1 << 16:
            header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, length)
        async with self._send_lock:
            self._writer.write(header + mask + _apply_mask(payload, mask))
            await self._writer.drain()


def _apply_mask(data: bytes, mask: bytes) -> bytes:
    # XOR via big integers is far faster than a Python byte loop for large payloads.
    if not data:
        return data
    repeated = (mask * (len(data) // 4 + 1))[: len(data)]
    value = int.from_bytes(data, "big") ^ int.from_bytes(repeated, "big")
    return value.to_bytes(len(data), "big")
