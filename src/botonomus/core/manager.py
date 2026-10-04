"""The session manager: bounded admission and cancellation-safe ownership.

A manager admits at most ``max_instances`` sessions at a time. Each admitted session
holds a cross-process profile lease and a slot until its browser is confirmed
stopped; failed or cancelled launches give both back only after cleanup.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from types import TracebackType
from typing import Self

from ..browser import Backend, BrowserHandle, ChromeBackend
from ..config import BrowserConfig, validate_args
from ..errors import (
    BotonomusError,
    BrowserCleanupError,
    BrowserStartupError,
    ConfigurationError,
    ManagerClosedError,
)
from ..profiles import ProfileLease
from .identity import IdentityResolver, LaunchIdentity
from .session import Session

_log = logging.getLogger("botonomus")


@dataclass(eq=False)
class _Owned:
    """Bookkeeping for one admitted session, from admission to confirmed release."""

    lease: ProfileLease
    config: BrowserConfig
    handle: BrowserHandle | None = None
    cleanup: asyncio.Task[None] | None = None
    released: bool = False
    quarantined: bool = False


class Botonomus:
    """Opens browser sessions with dedicated profiles under a concurrency limit.

    Use as an async context manager. A manager can be entered once and belongs to the
    event loop that entered it.

    Args:
        max_instances: Maximum simultaneous sessions. Requests above the limit wait;
            cancelling a waiting request does not consume capacity.
        config: Settings for every session. Defaults to ``BrowserConfig()``.
        backend: Browser backend. Defaults to `ChromeBackend` with
            ``config.driver``.
        identity: Resolves executable, persona, locale and time zone per launch.
            Defaults to an `IdentityResolver` when ``backend`` is the default,
            and to none (the configuration is passed through unchanged) when a
            custom backend is supplied.

    Raises:
        ConfigurationError: If ``max_instances`` is not a positive integer.

    Example:
        >>> async with Botonomus(max_instances=10) as bot:  # doctest: +SKIP
        ...     async with bot.open(profile="acct-01") as session:
        ...         await session.page.goto("https://example.com")
    """

    def __init__(
        self,
        max_instances: int = 1,
        *,
        config: BrowserConfig | None = None,
        backend: Backend | None = None,
        identity: IdentityResolver | None = None,
    ) -> None:
        if type(max_instances) is not int or max_instances < 1:
            raise ConfigurationError("max_instances must be a positive integer")
        self.config = config or BrowserConfig()
        self._limit = max_instances
        self._backend: Backend = (
            backend if backend is not None else ChromeBackend(self.config.driver)
        )
        self._identity = (
            identity if identity is not None or backend is not None else (IdentityResolver())
        )
        self._condition = asyncio.Condition()
        self._state = "new"
        self._active = 0
        self._owned: set[_Owned] = set()
        self._pending: set[asyncio.Task[tuple[Session, _Owned]]] = set()
        self._abandoned: set[asyncio.Task[None]] = set()
        self._closing: asyncio.Task[None] | None = None

    @property
    def active_count(self) -> int:
        """Reserved slots, including launches and unconfirmed cleanup."""
        return self._active

    async def __aenter__(self) -> Self:
        """Start the backend.

        Raises:
            ManagerClosedError: If the manager was already entered, or closed while
                the backend was starting.
        """
        if self._state != "new":
            raise ManagerClosedError("Manager can only be entered once")
        self._state = "starting"
        try:
            await self._backend.start()
        except BaseException:
            self._state = "closed"
            await self._backend.close()
            raise
        if self._state != "starting":
            # close() ran while the backend was starting; it must not reopen.
            await self._backend.close()
            raise ManagerClosedError("Manager closed during startup")
        self._state = "open"
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the manager; a cleanup failure is attached to an in-flight error."""
        try:
            await self.close()
        except Exception:
            if exc is None:
                raise
            exc.add_note("Botonomus shutdown also failed; inspect manager cleanup separately.")
            _log.error("manager_cleanup_failed")

    @asynccontextmanager
    async def open(self, *, profile: str, args: Sequence[str] = ()) -> AsyncIterator[Session]:
        """Open a browser for a dedicated profile and yield its session.

        The browser closes when the context exits, including on error or cancellation.

        Args:
            profile: 1-64 ASCII letters, digits, ``_`` or ``-``, starting with a letter
                or digit. Normalized to lowercase.
            args: Extra ``--flag[=value]`` arguments for this launch only.

        Yields:
            The open `Session`.

        Raises:
            ConfigurationError: For an invalid profile name or argument.
            ProfileInUseError: If another session, in any process, holds the profile.
            ManagerClosedError: If the manager is not open or closes during launch.
            BrowserUnavailableError: If no browser executable is found.
            PersonaUnsupportedError: If a persona is requested for stock Chrome.
            GeoLookupError: If ``geoip=True`` and the proxy exit cannot be determined.
            GeoMismatchError: If Chrome cannot present the exit's time zone.
            BrowserStartupError: If the browser fails to start or attach.
            BrowserCleanupError: If the session could not be confirmed closed.
        """
        lease = ProfileLease(self.config.profile_root, profile)
        config = self.config
        if args:
            extra = validate_args(list(args))
            config = replace(config, extra_args=config.extra_args + extra)
        async with self._condition:
            await self._condition.wait_for(
                lambda: self._state != "open" or self._active < self._limit
            )
            if self._state != "open":
                raise ManagerClosedError("Manager is not open")
            self._active += 1
            owned = _Owned(lease, config)
            self._owned.add(owned)
            task = asyncio.create_task(self._launch(owned))
            self._pending.add(task)
        try:
            session, owned = await asyncio.shield(task)
        except asyncio.CancelledError:
            cleanup = asyncio.create_task(self._discard_launch(task))
            self._abandoned.add(cleanup)
            cleanup.add_done_callback(self._abandoned.discard)
            raise
        finally:
            if task.done():
                self._pending.discard(task)
        if self._state != "open":
            await self._release(owned)
            raise ManagerClosedError("Manager closed while browser was launching")
        try:
            yield session
        except BaseException as exc:
            try:
                await self._release(owned)
            except Exception:
                exc.add_note("Session cleanup failed; profile retained until backend shutdown.")
                _log.error("session_cleanup_failed", extra={"profile": lease.name})
            raise
        else:
            await self._release(owned)

    async def _launch(self, owned: _Owned) -> tuple[Session, _Owned]:
        started = time.monotonic()
        try:
            path = owned.lease.acquire()
            identity: LaunchIdentity | None = None
            if self._identity is not None:
                identity = await self._identity.resolve(owned.config, owned.lease.name)
                owned.config = identity.config
            owned.handle = await self._backend.launch(path, owned.config)
            session = Session(
                owned.lease.name,
                owned.handle,
                lambda: self._release(owned),
                identity=identity,
                human=owned.config.human_config,
            )
            _log.info(
                "session_opened",
                extra={"profile": owned.lease.name, "elapsed": time.monotonic() - started},
            )
            return session, owned
        except BrowserCleanupError:
            # Backend still owns uncertain processes: quarantine the profile and capacity.
            owned.quarantined = True
            raise
        except BaseException as exc:
            await self._finish_release(owned)
            if isinstance(exc, (BotonomusError, asyncio.CancelledError)):
                raise
            raise BrowserStartupError("Browser startup failed") from exc

    async def _discard_launch(self, task: asyncio.Task[tuple[Session, _Owned]]) -> None:
        try:
            _, owned = await task
            await self._release(owned)
        except Exception:
            _log.error("abandoned_launch_cleanup_failed")
        finally:
            self._pending.discard(task)

    async def _finish_release(self, owned: _Owned) -> None:
        async with self._condition:
            if not owned.released:
                owned.lease.release()
                owned.released = True
                self._owned.discard(owned)
                self._active -= 1
                self._condition.notify_all()

    async def _cleanup(self, owned: _Owned) -> None:
        if owned.quarantined:
            raise BrowserCleanupError("Profile is held until backend shutdown is confirmed")
        try:
            if owned.handle is not None:
                await asyncio.wait_for(owned.handle.close(), self.config.close_timeout * 2)
        except Exception as exc:
            raise BrowserCleanupError("Session could not be confirmed closed") from exc
        await self._finish_release(owned)
        _log.info("session_closed", extra={"profile": owned.lease.name})

    async def _release(self, owned: _Owned) -> None:
        if owned.released:
            return
        if owned.cleanup is None:
            owned.cleanup = asyncio.create_task(self._cleanup(owned))
        await asyncio.shield(owned.cleanup)

    async def close(self) -> None:
        """Reject new requests, wake waiters and close every owned session.

        Idempotent and shielded from cancellation.

        Raises:
            BrowserCleanupError: If the backend cannot confirm shutdown; affected
                profiles stay locked.
        """
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        await asyncio.shield(self._closing)

    async def _close(self) -> None:
        async with self._condition:
            self._state = "closing"
            self._condition.notify_all()
        if self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)
        await asyncio.gather(
            *(self._release(item) for item in tuple(self._owned)), return_exceptions=True
        )
        if self._abandoned:
            await asyncio.gather(*tuple(self._abandoned), return_exceptions=True)
        try:
            await asyncio.wait_for(self._backend.close(), self.config.close_timeout * 2)
        except Exception as exc:
            raise BrowserCleanupError("Backend shutdown failed; profiles remain locked") from exc
        for item in tuple(self._owned):
            await self._finish_release(item)
        self._state = "closed"
