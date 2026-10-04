"""Pages driven without ``Runtime.enable``.

DOM work runs in a private isolated world that shares the DOM with the page but not
its JavaScript globals, so page scripts cannot observe or tamper with helper code.
Main-world evaluation is available explicitly and is more observable.
"""

import asyncio
import base64
import json
import secrets
from pathlib import Path
from typing import Any, Literal

from .connection import CDPSession, Connection
from .errors import (
    EvaluationError,
    NavigationError,
    ProtocolError,
    TargetClosedError,
    TimeoutError_,
)
from .input import Keyboard, Mouse
from .locator import Locator, LocatorState
from .polling import looks_like_function, poll
from .scripts import HELPER

WaitUntil = Literal["load", "domcontentloaded", "commit"]
_LIFECYCLE = {"load": "load", "domcontentloaded": "DOMContentLoaded"}


class Page:
    """One browser tab, attached through a flattened CDP session.

    Timeouts are in seconds (Playwright uses milliseconds).

    Attributes:
        target_id: CDP target identifier.
        session: The page's CDP session.
        mouse: Trusted mouse input.
        keyboard: Trusted keyboard input.
        viewport_size: Always ``None``: the native window size is never emulated.
        default_timeout: Seconds used when an operation receives no timeout.
        url: The main frame's current URL.
        closed: Whether the target has been closed or detached.
    """

    def __init__(self, connection: Connection, target_id: str, session: CDPSession) -> None:
        self._connection = connection
        self.target_id = target_id
        self.session = session
        self.mouse = Mouse(session)
        self.keyboard = Keyboard(session)
        self.viewport_size: dict[str, int] | None = None
        self.default_timeout = 30.0
        self._main_frame = ""
        self.url = "about:blank"
        self._world: int | None = None
        self._world_name = "__" + secrets.token_hex(6)
        self._world_lock = asyncio.Lock()
        self.closed = False

    @classmethod
    async def attach(cls, connection: Connection, target_id: str) -> "Page":
        """Attach to an existing page target and initialize it."""
        attached = await connection.send(
            "Target.attachToTarget", {"targetId": target_id, "flatten": True}
        )
        page = cls(connection, target_id, connection.session(attached["sessionId"]))
        await page._initialize()
        return page

    async def _initialize(self) -> None:
        # Page.enable gives lifecycle events; Runtime.enable is deliberately never sent.
        await self.session.send("Page.enable")
        await self.session.send("Page.setLifecycleEventsEnabled", {"enabled": True})
        tree = await self.session.send("Page.getFrameTree")
        self._main_frame = tree["frameTree"]["frame"]["id"]
        self.url = tree["frameTree"]["frame"]["url"]
        self.session.on("Page.frameNavigated", self._on_frame_navigated)
        self.session.on("Page.navigatedWithinDocument", self._on_same_document)
        self._connection.on("Target.detachedFromTarget", self._on_detached)

    @property
    def main_frame_id(self) -> str:
        """The CDP frame id of the main frame."""
        return self._main_frame

    def _on_frame_navigated(self, params: dict[str, Any]) -> None:
        if params["frame"]["id"] == self._main_frame:
            self.url = params["frame"]["url"]
            self._world = None  # The isolated world dies with the document.

    def _on_same_document(self, params: dict[str, Any]) -> None:
        if params["frameId"] == self._main_frame:
            self.url = params["url"]

    def _on_detached(self, params: dict[str, Any]) -> None:
        if params.get("sessionId") == self.session.session_id:
            self.closed = True
            self._release_handlers()

    def _release_handlers(self) -> None:
        # The connection outlives tabs (a Client keeps one for hours): drop our handlers.
        self.session.off("Page.frameNavigated", self._on_frame_navigated)
        self.session.off("Page.navigatedWithinDocument", self._on_same_document)
        self._connection.off("Target.detachedFromTarget", self._on_detached)

    async def goto(
        self, url: str, *, wait_until: WaitUntil = "load", timeout: float | None = None
    ) -> None:
        """Navigate the main frame.

        Args:
            url: Destination URL.
            wait_until: ``load``, ``domcontentloaded`` or ``commit``.
            timeout: Seconds; defaults to `default_timeout`.

        Raises:
            NavigationError: If the browser reports a navigation error.
            TimeoutError_: If the wanted lifecycle event does not arrive in time.
        """
        timeout = timeout or self.default_timeout
        waiter = None
        if wait_until != "commit":
            name = _LIFECYCLE[wait_until]
            loader: dict[str, str] = {}
            waiter = asyncio.create_task(
                self.session.wait_for(
                    "Page.lifecycleEvent",
                    lambda p: (
                        p["name"] == name
                        and p["frameId"] == self._main_frame
                        and p.get("loaderId") == loader.get("id", p.get("loaderId"))
                    ),
                    timeout=timeout,
                )
            )
        try:
            result = await self.session.send(
                "Page.navigate", {"url": url, "frameId": self._main_frame}, timeout=timeout
            )
            if result.get("errorText"):
                raise NavigationError(f"{result['errorText']} at {url}")
            if waiter is not None:
                if "loaderId" not in result:
                    waiter.cancel()  # Same-document navigation: no new lifecycle.
                    return
                loader["id"] = result["loaderId"]
                await waiter
        except TimeoutError as exc:
            raise TimeoutError_(f"Navigation timed out after {timeout}s") from exc
        finally:
            if waiter is not None and not waiter.done():
                waiter.cancel()

    async def reload(self, *, timeout: float | None = None) -> None:
        """Reload and wait for the main frame's ``load`` event."""
        load = asyncio.create_task(
            self.session.wait_for(
                "Page.lifecycleEvent",
                lambda p: p["name"] == "load" and p["frameId"] == self._main_frame,
                timeout=timeout or self.default_timeout,
            )
        )
        await self.session.send("Page.reload")
        await load

    async def set_content(self, html: str) -> None:
        """Replace the main frame's document with ``html``."""
        await self.session.send(
            "Page.setDocumentContent", {"frameId": self._main_frame, "html": html}
        )
        self._world = None

    async def title(self) -> str:
        """The document title."""
        return str(await self.evaluate("document.title"))

    async def content(self) -> str:
        """The serialized document HTML."""
        return str(await self.evaluate("document.documentElement.outerHTML"))

    async def evaluate(
        self, expression: str, arg: Any = None, *, isolated_context: bool = True
    ) -> Any:
        """Evaluate an expression, or call a function source with ``arg``.

        Args:
            expression: A JavaScript expression, or a function expression which is
                called with ``arg`` (JSON-serialized, never interpolated raw).
            arg: Argument for a function expression.
            isolated_context: ``True`` (default) runs in the private isolated world,
                which sees the DOM but not page globals and is invisible to the page.
                ``False`` runs in the page's main world, which is more observable.

        Returns:
            The JSON-serializable result.

        Raises:
            EvaluationError: If the script throws.
        """
        source = expression.strip()
        if looks_like_function(source):
            source = f"({source})({json.dumps(arg)})"
        params: dict[str, Any] = {
            "expression": source,
            "returnByValue": True,
            "awaitPromise": True,
            "userGesture": False,
        }
        if isolated_context:
            params["contextId"] = await self._isolated_world()
        try:
            result = await self.session.send("Runtime.evaluate", params)
        except ProtocolError as exc:
            if isolated_context and "context" in str(exc).lower():
                self._world = None  # Navigation raced us; retry once in a fresh world.
                params["contextId"] = await self._isolated_world()
                result = await self.session.send("Runtime.evaluate", params)
            else:
                raise
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            message = details.get("exception", {}).get("description") or details.get("text")
            raise EvaluationError(str(message))
        return result["result"].get("value")

    async def _isolated_world(self) -> int:
        async with self._world_lock:
            if self._world is None:
                created = await self.session.send(
                    "Page.createIsolatedWorld",
                    {"frameId": self._main_frame, "worldName": self._world_name},
                )
                self._world = int(created["executionContextId"])
                await self.session.send(
                    "Runtime.evaluate",
                    {"expression": f"globalThis.__b = {HELPER}", "contextId": self._world},
                )
            return self._world

    def locator(self, selector: str) -> Locator:
        """A lazy locator for ``selector`` (CSS, ``text=``, ``xpath=`` or ``role=``)."""
        return Locator(self, selector)

    def get_by_role(self, role: str, *, name: str | None = None, exact: bool = False) -> Locator:
        """A locator by ARIA role and accessible name (implicit roles included)."""
        return Locator(self, "role=" + json.dumps({"role": role, "name": name, "exact": exact}))

    def get_by_text(self, text: str) -> Locator:
        """A locator for elements whose own text contains ``text`` (case-insensitive)."""
        return Locator(self, "text=" + text)

    async def wait_for_selector(
        self, selector: str, *, state: LocatorState = "visible", timeout: float | None = None
    ) -> Locator:
        """Wait until ``selector`` reaches ``state`` and return its locator."""
        locator = self.locator(selector)
        await locator.wait_for(state=state, timeout=timeout)
        return locator

    async def wait_for_function(
        self, expression: str, *, timeout: float | None = None, interval: float = 0.1
    ) -> Any:
        """Poll ``expression`` in the isolated world until it is truthy; return it."""
        return await poll(
            lambda: self.evaluate(expression),
            timeout or self.default_timeout,
            interval,
            "condition",
        )

    async def wait_for_load(self, state: WaitUntil = "load", timeout: float | None = None) -> None:
        """Wait until ``document.readyState`` reaches ``state``."""
        wanted = "complete" if state == "load" else "interactive"
        await poll(
            lambda: self.evaluate(
                f"document.readyState === 'complete' || document.readyState === '{wanted}'"
            ),
            timeout or self.default_timeout,
            0.1,
            "load state",
        )

    async def screenshot(
        self, *, path: str | Path | None = None, full_page: bool = False, quality: int | None = None
    ) -> bytes:
        """Capture PNG (or JPEG when ``quality`` is set) and optionally write it.

        Args:
            path: File to write, if any.
            full_page: Capture the whole scrollable page instead of the viewport.
            quality: JPEG quality 0-100; PNG when ``None``.

        Returns:
            The encoded image.
        """
        params: dict[str, Any] = {"format": "jpeg" if quality else "png"}
        if quality:
            params["quality"] = quality
        if full_page:
            metrics = await self.session.send("Page.getLayoutMetrics")
            size = metrics["cssContentSize"]
            params["clip"] = {
                "x": 0,
                "y": 0,
                "width": size["width"],
                "height": size["height"],
                "scale": 1,
            }
            params["captureBeyondViewport"] = True
        data = base64.b64decode((await self.session.send("Page.captureScreenshot", params))["data"])
        if path is not None:
            await asyncio.to_thread(Path(path).write_bytes, data)
        return data

    async def bring_to_front(self) -> None:
        """Activate this tab."""
        await self.session.send("Page.bringToFront")

    async def close(self) -> None:
        """Close the tab. Idempotent."""
        if not self.closed:
            try:
                await self._connection.send("Target.closeTarget", {"targetId": self.target_id})
            except (ProtocolError, TargetClosedError):
                pass
            self.closed = True
            self._release_handlers()


__all__ = ["Page", "TimeoutError_", "WaitUntil"]
