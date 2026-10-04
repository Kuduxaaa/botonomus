"""One Chrome shared by every context of a `Client`, relaunched if it dies."""

import asyncio
import re
import secrets
import shutil
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from typing import Any

from ..cdp import Connection
from ..config import BrowserConfig
from ..core import Botonomus, Session
from ..errors import ConfigurationError, ManagerClosedError

# A per-context proxy cannot cover WebRTC's UDP, so Client browsers never send UDP
# outside a proxy (the SDK does the same for browser-level proxies).
WEBRTC_POLICY = "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"
_MAJOR = re.compile(r"/(\d+)\.")


class SharedBrowser:
    """Lazily launches one browser through `Botonomus` with a throwaway profile.

    Attributes:
        generation: Incremented on every launch; contexts from an older generation are
            gone and must be recreated.
    """

    def __init__(self, config: BrowserConfig) -> None:
        if config.driver != "native":
            raise ConfigurationError("Client requires driver='native'")
        args = (
            config.extra_args
            if WEBRTC_POLICY in config.extra_args
            else (*config.extra_args, WEBRTC_POLICY)
        )
        self.config = replace(config, extra_args=tuple(args))
        self.generation = 0
        self.major = 0
        self.session: Session | None = None
        self._lock = asyncio.Lock()
        self._manager: Botonomus | None = None
        self._opener: AbstractAsyncContextManager[Session] | None = None
        self._profile = ""
        self._closed = False

    @property
    def alive(self) -> bool:
        """Whether the browser is launched and its connection is open."""
        return self.session is not None and not self.connection_of(self.session).closed.is_set()

    @staticmethod
    def connection_of(session: Session) -> Connection:
        context: Any = session.context
        connection: Connection = context.connection
        return connection

    async def connection(self) -> Connection:
        """The browser connection, launching or relaunching the browser if needed."""
        async with self._lock:
            if self._closed:
                raise ManagerClosedError("Client is closed")
            if not self.alive:
                await self._shutdown()
                await self._launch()
            assert self.session is not None
            return self.connection_of(self.session)

    async def _launch(self) -> None:
        self._manager = Botonomus(1, config=self.config)
        await self._manager.__aenter__()
        self._profile = "client-" + secrets.token_hex(6)
        self._opener = self._manager.open(profile=self._profile)
        try:
            self.session = await self._opener.__aenter__()
            version = await self.connection_of(self.session).send("Browser.getVersion")
        except BaseException:
            await self._shutdown()
            raise
        match = _MAJOR.search(str(version.get("product", "")))
        self.major = int(match.group(1)) if match else 0
        self.generation += 1

    async def _shutdown(self) -> None:
        opener, manager = self._opener, self._manager
        self._opener = self._manager = None
        self.session = None
        try:
            if opener is not None:
                await opener.__aexit__(None, None, None)
        finally:
            if manager is not None:
                try:
                    await manager.close()
                finally:
                    shutil.rmtree(self.config.profile_root / self._profile, ignore_errors=True)

    async def close(self) -> None:
        """Close the browser and delete its throwaway profile."""
        async with self._lock:
            self._closed = True
            await self._shutdown()
