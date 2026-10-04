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

    async def add_cookies(self, cookies: list[dict[str, Any]]) -> None:
        """Set cookies. A ``url`` key is expanded to ``domain``, ``path`` and ``secure``."""
        prepared = []
        for cookie in cookies:
            item = dict(cookie)
            if "url" in item and "domain" not in item:
                parts = urlsplit(item.pop("url"))
                item.setdefault("domain", parts.hostname)
                item.setdefault("path", "/")
                item.setdefault("secure", parts.scheme == "https")
            prepared.append(item)
        await self._connection.send("Storage.setCookies", {"cookies": prepared})

    async def clear_cookies(self) -> None:
        """Delete every cookie in the profile."""
        await self._connection.send("Storage.clearCookies")


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
