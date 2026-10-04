"""Loopback SOCKS5 forwarder that adds upstream proxy credentials.

Chrome cannot take credentials in ``--proxy-server``, and answering proxy auth
prompts through CDP is observable. Instead Chrome connects to this unauthenticated
loopback SOCKS5 server, and each connection is tunnelled through the upstream
proxy. Nothing is injected into pages and credentials are never logged.

The forwarder runs its own accept loop instead of ``asyncio.start_server``. On the
Windows proactor loop (CPython 3.12) a transport whose peer reset the connection
raises from ``socket.shutdown`` inside ``_call_connection_lost``; that skips both the
socket close and ``Server._detach``, so ``Server.wait_closed`` never returns and the
socket leaks. A reset during ``accept`` also makes ``asyncio.Server`` close its
listening socket for good. Owning the listener and every accepted socket avoids
both: each socket is shut down and closed by this module, and nothing waits on
the server's connection count.
"""

import asyncio
import logging
import socket
import struct
from contextlib import suppress
from typing import Final

from ..config import ProxySpec
from .tunnel import UpstreamError, open_tunnel, pipe, read_address

_log = logging.getLogger("botonomus")
CONNECT_TIMEOUT: Final = 30.0

_REPLY_OK: Final = b"\x05\x00\x00\x01" + bytes(6)
_REPLY_REFUSED: Final = b"\x05\x05\x00\x01" + bytes(6)
_REPLY_UNSUPPORTED: Final = b"\x05\x07\x00\x01" + bytes(6)
_ACCEPT_RETRY_DELAY: Final = 0.01


class _OwnedSocketProtocol(asyncio.StreamReaderProtocol):
    """A stream protocol that shuts down and closes its accepted socket itself."""

    def __init__(self, sock: socket.socket, reader: asyncio.StreamReader) -> None:
        super().__init__(reader)
        self._owned_sock = sock

    def connection_lost(self, exc: Exception | None) -> None:
        """Deliver EOF or the error to the stream, then close the socket.

        Args:
            exc: The error that ended the connection, or ``None`` on a clean close.
        """
        try:
            super().connection_lost(exc)
        finally:
            # Closing here, before the transport's own cleanup, means the
            # transport sees fileno() == -1 and skips the shutdown() call that
            # raises on reset sockets under the proactor loop.
            with suppress(OSError):
                self._owned_sock.shutdown(socket.SHUT_RDWR)
            self._owned_sock.close()


class ProxyForwarder:
    """A per-browser loopback SOCKS5 server tunnelling through one upstream proxy.

    Every accepted connection is registered synchronously, as soon as its
    transport exists, so `close` can abort all of them, including ones
    accepted in the instant before the listener closed.

    Args:
        upstream: The authenticated upstream proxy.

    Attributes:
        port: The bound loopback port, valid after `start`.
    """

    def __init__(self, upstream: ProxySpec) -> None:
        self._upstream = upstream
        self._listener: socket.socket | None = None
        self._accept_task: asyncio.Task[None] | None = None
        self._closing = False
        self._connections: dict[asyncio.StreamWriter, asyncio.Task[None]] = {}
        self.port = 0

    @property
    def server(self) -> str:
        """The ``--proxy-server`` value pointing Chrome at this forwarder."""
        return f"socks5://127.0.0.1:{self.port}"

    @property
    def active_connections(self) -> int:
        """Client connections accepted and not yet finished."""
        return len(self._connections)

    async def start(self) -> None:
        """Bind to an ephemeral loopback port and start accepting connections.

        Raises:
            OSError: If the loopback socket cannot be bound.
        """
        listener = socket.create_server(("127.0.0.1", 0), backlog=128)
        listener.setblocking(False)
        self.port = int(listener.getsockname()[1])
        self._listener = listener
        self._closing = False
        self._accept_task = asyncio.create_task(self._accept_loop(listener))

    async def close(self) -> None:
        """Stop accepting, abort every client connection and wait for them. Idempotent.

        Client transports are aborted rather than closed gracefully: a graceful
        close waits for buffered writes, which never drain once the peer has
        stopped reading.
        """
        self._closing = True
        accept_task, self._accept_task = self._accept_task, None
        if accept_task is not None:
            accept_task.cancel()
            await asyncio.gather(accept_task, return_exceptions=True)
        listener, self._listener = self._listener, None
        if listener is not None:
            # The accept loop closes it too, but not if it was cancelled before
            # its first step.
            listener.close()
        connections = tuple(self._connections.items())
        for writer, task in connections:
            writer.transport.abort()
            task.cancel()
        if connections:
            await asyncio.gather(*(task for _, task in connections), return_exceptions=True)

    async def _accept_loop(self, listener: socket.socket) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                try:
                    conn, _ = await loop.sock_accept(listener)
                except OSError as exc:
                    if listener.fileno() == -1:
                        return
                    # A client that resets before accept completes fails only
                    # this accept; the listener stays usable. The delay stops a
                    # persistent error (out of descriptors) from spinning.
                    _log.debug("proxy_accept_failed", extra={"category": type(exc).__name__})
                    await asyncio.sleep(_ACCEPT_RETRY_DELAY)
                    continue
                await self._attach(loop, conn)
        finally:
            listener.close()

    async def _attach(self, loop: asyncio.AbstractEventLoop, conn: socket.socket) -> None:
        reader = asyncio.StreamReader()
        try:
            transport, protocol = await loop.connect_accepted_socket(
                lambda: _OwnedSocketProtocol(conn, reader), conn
            )
        except OSError as exc:
            conn.close()
            _log.debug("proxy_accept_failed", extra={"category": type(exc).__name__})
            return
        except BaseException:
            conn.close()
            raise
        writer = asyncio.StreamWriter(transport, protocol, reader, loop)
        if self._closing:
            transport.abort()
            return
        task = asyncio.create_task(self._handle(reader, writer))
        self._connections[writer] = task
        # A done callback rather than a finally clause: a task cancelled before its
        # first step never runs its body.
        task.add_done_callback(lambda _: self._connections.pop(writer, None))

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await self._serve(reader, writer)
        except (OSError, asyncio.IncompleteReadError, UpstreamError, TimeoutError) as exc:
            _log.debug("proxy_connection_failed", extra={"category": type(exc).__name__})
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        version, count = await reader.readexactly(2)
        methods = await reader.readexactly(count)
        if version != 5 or 0 not in methods:
            writer.write(b"\x05\xff")
            return
        writer.write(b"\x05\x00")
        version, command, _, address_type = await reader.readexactly(4)
        if version != 5 or command != 1:
            writer.write(_REPLY_UNSUPPORTED)
            return
        host = await read_address(reader, address_type)
        (port,) = struct.unpack("!H", await reader.readexactly(2))
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                upstream_reader, upstream_writer = await self._open_upstream(host, port)
        except (OSError, UpstreamError, TimeoutError, asyncio.IncompleteReadError):
            writer.write(_REPLY_REFUSED)
            raise
        writer.write(_REPLY_OK)
        try:
            await asyncio.gather(pipe(reader, upstream_writer), pipe(upstream_reader, writer))
        finally:
            upstream_writer.close()

    async def _open_upstream(
        self, host: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Open the upstream tunnel for one client request.

        Args:
            host: Destination requested by the client.
            port: Destination port.

        Returns:
            The tunnelled ``(reader, writer)`` pair.
        """
        return await open_tunnel(self._upstream, host, port)
