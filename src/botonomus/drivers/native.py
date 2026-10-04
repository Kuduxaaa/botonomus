"""Botonomus' own CDP driver: stdlib only, no Node.js process, no ``Runtime.enable``."""

import asyncio
import json
import urllib.request
from contextlib import suppress
from typing import Any

from ..cdp import Browser, Page, ProtocolError, TargetClosedError


class NativeAttachment:
    """A native CDP connection to one browser.

    Attributes:
        browser: The connected [`botonomus.cdp.Browser`][botonomus.cdp.Browser].
        context: The browser's default context.
        page: The initial page.
    """

    def __init__(self, browser: Browser, page: Page) -> None:
        self.browser = browser
        self.context: Any = browser.context
        self.page: Any = page

    async def request_close(self) -> None:
        """Send ``Browser.close``; a browser that is already gone is not an error."""
        with suppress(ProtocolError, TargetClosedError, TimeoutError):
            await self.browser.connection.send("Browser.close", timeout=5)

    async def disconnect(self) -> None:
        """Close the websocket."""
        await self.browser.disconnect()


class NativeDriver:
    """Attaches `NativeAttachment` instances. Holds no shared runtime."""

    async def start(self) -> None:
        """Nothing to start; present for the [`Driver`][botonomus.drivers.base.Driver] contract."""

    async def attach(self, port: int, timeout: float) -> NativeAttachment:
        """Connect over the browser websocket advertised at ``/json/version``."""
        async with asyncio.timeout(timeout):
            url = await asyncio.to_thread(websocket_url, port)
            browser = await Browser.connect(url)
            pages = browser.context.pages
            page = pages[0] if pages else await browser.context.new_page()
        return NativeAttachment(browser, page)

    async def stop(self) -> None:
        """Nothing to stop."""


def websocket_url(port: int) -> str:
    """Return the browser-level DevTools websocket URL for loopback ``port``. Blocking."""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=5) as response:
        return str(json.load(response)["webSocketDebuggerUrl"])
