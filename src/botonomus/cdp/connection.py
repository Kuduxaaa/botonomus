"""CDP message routing over one websocket with flattened target sessions."""

import asyncio
import itertools
import json
import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from .errors import ProtocolError, TargetClosedError
from .websocket import WebSocket, WebSocketClosed

_log = logging.getLogger("botonomus")

Handler = Callable[[dict[str, Any]], None]
"""Event callback receiving the event's ``params``."""


class Connection:
    """A browser-level CDP connection multiplexing every attached target session.

    Commands are matched to responses by id; events are dispatched to handlers keyed
    by ``(session_id, method)``. Commands pending on a session fail with
    `TargetClosedError` as soon as that session detaches.

    Attributes:
        closed: Set once the socket has closed.
    """

    def __init__(self, socket: WebSocket) -> None:
        self._socket = socket
        self._ids = itertools.count(1)
        self._pending: dict[int, tuple[str, str | None, asyncio.Future[dict[str, Any]]]] = {}
        self._handlers: dict[tuple[str | None, str], list[Handler]] = defaultdict(list)
        self._waiters: dict[str | None, set[asyncio.Future[dict[str, Any]]]] = defaultdict(set)
        self._reader: asyncio.Task[None] | None = None
        self.closed = asyncio.Event()

    @classmethod
    async def connect(cls, url: str) -> "Connection":
        """Open the websocket at ``url`` and start the reader task."""
        connection = cls(await WebSocket.connect(url))
        connection._reader = asyncio.create_task(connection._read_loop())
        return connection

    def session(self, session_id: str | None = None) -> "CDPSession":
        """A view scoped to one target session, or the browser when ``None``."""
        return CDPSession(self, session_id)

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        session_id: str | None = None,
        timeout: float | None = 60.0,
    ) -> dict[str, Any]:
        """Send a command and await its result.

        Raises:
            ProtocolError: If the browser returns an error.
            TargetClosedError: If the connection or session closes first.
            TimeoutError: If ``timeout`` seconds elapse.
        """
        if self.closed.is_set():
            raise TargetClosedError("Browser connection is closed")
        message_id = next(self._ids)
        message: dict[str, Any] = {"id": message_id, "method": method, "params": params or {}}
        if session_id is not None:
            message["sessionId"] = session_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = (method, session_id, future)
        try:
            await self._socket.send(json.dumps(message))
            async with asyncio.timeout(timeout):
                return await future
        finally:
            self._pending.pop(message_id, None)

    def on(self, event: str, handler: Handler, session_id: str | None = None) -> None:
        """Register ``handler`` for ``event`` on ``session_id``."""
        self._handlers[(session_id, event)].append(handler)

    def off(self, event: str, handler: Handler, session_id: str | None = None) -> None:
        """Remove a previously registered handler; unknown handlers are ignored."""
        handlers = self._handlers.get((session_id, event), [])
        if handler in handlers:
            handlers.remove(handler)
            if not handlers:
                del self._handlers[(session_id, event)]

    async def wait_for(
        self,
        event: str,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        session_id: str | None = None,
        timeout: float | None = 30.0,
    ) -> dict[str, Any]:
        """Await the next ``event`` whose params satisfy ``predicate``.

        Raises:
            TimeoutError: If no matching event arrives within ``timeout`` seconds.
            TargetClosedError: If the session detaches or the connection closes first.
        """
        if self.closed.is_set():
            raise TargetClosedError("Browser connection is closed")
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._waiters[session_id].add(future)

        def handler(params: dict[str, Any]) -> None:
            if not future.done() and (predicate is None or predicate(params)):
                future.set_result(params)

        self.on(event, handler, session_id)
        try:
            async with asyncio.timeout(timeout):
                return await future
        finally:
            self.off(event, handler, session_id)
            waiters = self._waiters.get(session_id)
            if waiters is not None:
                waiters.discard(future)
                if not waiters:
                    del self._waiters[session_id]

    async def close(self) -> None:
        """Close the socket, stop the reader and fail pending commands."""
        await self._socket.close()
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):
                pass
        self._fail_pending()

    async def _read_loop(self) -> None:
        try:
            while True:
                message = json.loads(await self._socket.recv())
                if "id" in message:
                    entry = self._pending.get(message["id"])
                    if entry is None:
                        continue
                    method, _, future = entry
                    if future.done():
                        continue
                    if "error" in message:
                        future.set_exception(ProtocolError(method, message["error"]))
                    else:
                        future.set_result(message.get("result", {}))
                elif "method" in message:
                    self._dispatch(message)
        except (WebSocketClosed, ConnectionError, OSError):
            pass
        finally:
            self.closed.set()
            self._fail_pending()

    def _dispatch(self, message: dict[str, Any]) -> None:
        key = (message.get("sessionId"), message["method"])
        if message["method"] == "Target.detachedFromTarget":
            self._fail_session(message.get("params", {}).get("sessionId"))
        for handler in tuple(self._handlers.get(key, ())):
            try:
                handler(message.get("params", {}))
            except Exception:
                _log.exception("cdp_event_handler_failed")

    def _fail_session(self, session_id: str | None) -> None:
        # Commands sent to a detached session never get answers; fail them now.
        if session_id is None:
            return
        for _, session, future in self._pending.values():
            if session == session_id and not future.done():
                future.set_exception(TargetClosedError("Target closed"))
        for waiter in tuple(self._waiters.get(session_id, ())):
            if not waiter.done():
                waiter.set_exception(TargetClosedError("Target closed"))

    def _fail_pending(self) -> None:
        for method, _, future in self._pending.values():
            if not future.done():
                future.set_exception(TargetClosedError(f"{method}: connection closed"))
        for waiters in tuple(self._waiters.values()):
            for waiter in tuple(waiters):
                if not waiter.done():
                    waiter.set_exception(TargetClosedError("Connection closed"))


class CDPSession:
    """Commands and events scoped to one attached target (or the browser if ``None``).

    Attributes:
        connection: The shared connection.
        session_id: Flattened session id, or ``None`` for browser-level commands.
    """

    def __init__(self, connection: Connection, session_id: str | None) -> None:
        self.connection = connection
        self.session_id = session_id

    async def send(
        self, method: str, params: dict[str, Any] | None = None, timeout: float | None = 60.0
    ) -> dict[str, Any]:
        """Send a command on this session. See `Connection.send`."""
        return await self.connection.send(method, params, self.session_id, timeout)

    def on(self, event: str, handler: Handler) -> None:
        """Register an event handler on this session."""
        self.connection.on(event, handler, self.session_id)

    def off(self, event: str, handler: Handler) -> None:
        """Remove an event handler from this session."""
        self.connection.off(event, handler, self.session_id)

    async def wait_for(
        self,
        event: str,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        timeout: float | None = 30.0,
    ) -> dict[str, Any]:
        """Await a matching event on this session. See `Connection.wait_for`."""
        return await self.connection.wait_for(event, predicate, self.session_id, timeout)
