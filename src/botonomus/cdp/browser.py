"""Browser-level connection and the default (profile-backed) context."""

from typing import Any
from urllib.parse import urlsplit

from .connection import Connection
from .errors import ProtocolError, TargetClosedError
from .page import Page


class Context:
    """The browser's default context: pages and cookies share the dedicated profile.

    Attributes:
        pages: Pages attached so far, in attachment order.
    """

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self.pages: list[Page] = []

    async def new_page(self, url: str = "about:blank") -> Page:
        """Open a new tab at ``url`` and attach to it."""
        created = await self._connection.send("Target.createTarget", {"url": url})
        page = await Page.attach(self._connection, created["targetId"])
        self.pages.append(page)
        return page

    async def cookies(self) -> list[dict[str, Any]]:
        """All cookies in the profile, in CDP ``Network.Cookie`` form."""
        result = await self._connection.send("Storage.getCookies")
        return list(result.get("cookies", []))

    @property
    def connection(self) -> Connection:
        """The browser-level connection this context belongs to."""
        return self._connection

    async def add_cookies(self, cookies: list[dict[str, Any]]) -> None:
        """Set cookies. A ``url`` key is expanded to ``domain``, ``path`` and ``secure``."""
        await self._connection.send("Storage.setCookies", {"cookies": _prepare(cookies)})

    async def clear_cookies(self) -> None:
        """Delete every cookie in the profile."""
        await self._connection.send("Storage.clearCookies")


def _prepare(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = []
    for cookie in cookies:
        item = dict(cookie)
        if "url" in item and "domain" not in item:
            parts = urlsplit(item.pop("url"))
            item.setdefault("domain", parts.hostname)
            item.setdefault("path", "/")
            item.setdefault("secure", parts.scheme == "https")
        prepared.append(item)
    return prepared


class IsolatedContext:
    """An in-memory browser context: its own cookies, storage and cache, no profile on disk.

    Contexts share the browser process (and its GPU and network processes) but never a
    renderer, so they are isolated like separate browsers at a fraction of the memory.
    Chrome treats them as off-the-record.

    Attributes:
        id: The CDP ``browserContextId``.
        pages: Open pages of this context, in opening order (closed ones are dropped).
        closed: Whether the context has been disposed.
    """

    def __init__(self, connection: Connection, context_id: str) -> None:
        self._connection = connection
        self.id = context_id
        self.pages: list[Page] = []
        self.closed = False

    @classmethod
    async def create(
        cls,
        connection: Connection,
        *,
        proxy_server: str | None = None,
        proxy_bypass: str | None = None,
    ) -> "IsolatedContext":
        """Create a context, optionally with its own proxy.

        Args:
            connection: The browser-level connection.
            proxy_server: ``scheme://host:port`` without credentials; ``None`` uses the
                browser's proxy settings.
            proxy_bypass: Comma-separated hosts that bypass ``proxy_server``.
        """
        params: dict[str, Any] = {"disposeOnDetach": True}
        if proxy_server is not None:
            params["proxyServer"] = proxy_server
        if proxy_bypass is not None:
            params["proxyBypassList"] = proxy_bypass
        created = await connection.send("Target.createBrowserContext", params)
        return cls(connection, created["browserContextId"])

    @property
    def connection(self) -> Connection:
        """The browser-level connection this context belongs to."""
        return self._connection

    async def new_page(self, url: str = "about:blank") -> Page:
        """Open a tab in this context at ``url`` and attach to it."""
        created = await self._connection.send(
            "Target.createTarget", {"url": url, "browserContextId": self.id}
        )
        page = await Page.attach(self._connection, created["targetId"])
        self.pages = [item for item in self.pages if not item.closed]
        self.pages.append(page)
        return page

    async def cookies(self) -> list[dict[str, Any]]:
        """This context's cookies, in CDP ``Network.Cookie`` form."""
        result = await self._connection.send("Storage.getCookies", {"browserContextId": self.id})
        return list(result.get("cookies", []))

    async def add_cookies(self, cookies: list[dict[str, Any]]) -> None:
        """Set cookies. A ``url`` key is expanded to ``domain``, ``path`` and ``secure``."""
        await self._connection.send(
            "Storage.setCookies", {"browserContextId": self.id, "cookies": _prepare(cookies)}
        )

    async def clear_cookies(self) -> None:
        """Delete every cookie in this context."""
        await self._connection.send("Storage.clearCookies", {"browserContextId": self.id})

    async def close(self) -> None:
        """Dispose of the context and every tab in it. Idempotent."""
        if self.closed:
            return
        self.closed = True
        try:
            await self._connection.send(
                "Target.disposeBrowserContext", {"browserContextId": self.id}
            )
        except (ProtocolError, TargetClosedError):
            pass
        for page in self.pages:
            page.closed = True


class Browser:
    """A connected browser.

    Attributes:
        connection: The browser-level CDP connection.
        context: The default context.
    """

    def __init__(self, connection: Connection, context: Context) -> None:
        self.connection = connection
        self.context = context

    @classmethod
    async def connect(cls, websocket_url: str) -> "Browser":
        """Connect and attach to every existing (non-DevTools) page."""
        connection = await Connection.connect(websocket_url)
        context = Context(connection)
        targets = await connection.send("Target.getTargets")
        for info in targets["targetInfos"]:
            if info["type"] == "page" and not info["url"].startswith("devtools://"):
                context.pages.append(await Page.attach(connection, info["targetId"]))
        return cls(connection, context)

    async def version(self) -> dict[str, Any]:
        """``Browser.getVersion`` result (product, revision, user agent, protocol)."""
        return await self.connection.send("Browser.getVersion")

    async def close(self) -> None:
        """Ask the browser to exit, then drop the connection."""
        try:
            await self.connection.send("Browser.close", timeout=5)
        except (ProtocolError, TargetClosedError, TimeoutError):
            pass
        await self.connection.close()

    async def disconnect(self) -> None:
        """Drop the connection and leave the browser running."""
        await self.connection.close()
