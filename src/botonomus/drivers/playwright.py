"""Playwright and Patchright adapters (optional extras).

Both share one API. Attachment uses ``connect_over_cdp(no_defaults=True)`` so the
driver applies no context overrides of its own. Patchright additionally avoids
``Runtime.enable``; plain Playwright does not and is measurably detectable.
"""

from contextlib import suppress
from typing import Any, Literal

from ..errors import BrowserStartupError

PlaywrightFlavor = Literal["playwright", "patchright"]


class PlaywrightAttachment:
    """A Playwright-compatible connection to one browser.

    Attributes:
        browser: The Playwright ``Browser``.
        context: Its default ``BrowserContext``.
        page: The initial ``Page``.
    """

    def __init__(self, browser: Any, page: Any) -> None:
        self.browser = browser
        self.context: Any = browser.contexts[0]
        self.page: Any = page

    async def request_close(self) -> None:
        """Send ``Browser.close`` through a browser CDP session, ignoring failures."""
        if self.browser.is_connected():
            with suppress(Exception):
                cdp = await self.browser.new_browser_cdp_session()
                await cdp.send("Browser.close")

    async def disconnect(self) -> None:
        """Close the Playwright connection, ignoring failures."""
        with suppress(Exception):
            await self.browser.close()


class PlaywrightDriver:
    """Runs one Playwright (or Patchright) runtime shared by all attachments.

    Args:
        flavor: ``"playwright"`` or ``"patchright"``.
    """

    def __init__(self, flavor: PlaywrightFlavor) -> None:
        self.flavor = flavor
        self._runtime: Any = None

    async def start(self) -> None:
        """Start the runtime once.

        Raises:
            BrowserStartupError: If the package is not installed or fails to start.
        """
        if self._runtime is not None:
            return
        try:
            factory: Any
            if self.flavor == "patchright":
                from patchright.async_api import async_playwright as factory
            else:
                from playwright.async_api import async_playwright as factory
            self._runtime = await factory().start()
        except ImportError as exc:
            raise BrowserStartupError(
                f"driver={self.flavor!r} needs: pip install botonomus[{self.flavor}]"
            ) from exc
        except Exception as exc:
            raise BrowserStartupError("Could not start browser driver") from exc

    async def attach(self, port: int, timeout: float) -> PlaywrightAttachment:
        """Connect with ``connect_over_cdp`` and return the default context's page."""
        if self._runtime is None:
            raise BrowserStartupError("Driver is not started")
        browser = await self._runtime.chromium.connect_over_cdp(
            f"http://127.0.0.1:{port}", no_defaults=True, timeout=timeout * 1000
        )
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()
        return PlaywrightAttachment(browser, page)

    async def stop(self) -> None:
        """Stop the runtime if running."""
        if self._runtime is not None:
            await self._runtime.stop()
            self._runtime = None
