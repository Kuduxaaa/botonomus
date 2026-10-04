"""Lazy element references, resolved again on every action like Playwright locators."""

from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any, Literal

from .errors import EvaluationError
from .polling import poll

if TYPE_CHECKING:
    from .page import Page

LocatorState = Literal["attached", "detached", "visible", "hidden"]
_STATE_CHECKS: dict[str, str] = {
    "attached": "return !!el;",
    "detached": "return !el;",
    "visible": "return !!el && __b.visible(el);",
    "hidden": "return !el || !__b.visible(el);",
}


class Locator:
    """A selector plus an index, evaluated in the page's isolated world.

    Args:
        page: Owning page.
        selector: CSS, ``text=``, ``xpath=`` or ``role=`` selector.
        index: Which match to use.
    """

    def __init__(self, page: "Page", selector: str, index: int = 0) -> None:
        self.page = page
        self.selector = selector
        self.index = index

    def nth(self, index: int) -> "Locator":
        """The ``index``-th match of the same selector."""
        return Locator(self.page, self.selector, index)

    @property
    def first(self) -> "Locator":
        """The first match."""
        return self.nth(0)

    def _call(self, body: str) -> Awaitable[Any]:
        # ``body`` sees ``el`` (or null) and ``all``. The selector travels as JSON and is
        # never interpolated into source.
        return self.page.evaluate(
            "(a) => { const all = __b.resolve(a.s); const el = all[a.i] ?? null; " + body + " }",
            {"s": self.selector, "i": self.index},
        )

    async def count(self) -> int:
        """Number of current matches."""
        return int(await self._call("return all.length;"))

    async def is_visible(self) -> bool:
        """Whether the element exists and has a visible, non-empty box."""
        return bool(await self._call("return !!el && __b.visible(el);"))

    async def is_enabled(self) -> bool:
        """Whether the element exists and is neither ``:disabled`` nor ``aria-disabled``."""
        return bool(
            await self._call(
                "return !!el && !el.matches(':disabled')"
                " && el.getAttribute('aria-disabled') !== 'true';"
            )
        )

    async def select_text(self) -> None:
        """Focus the element and select all of its text (input, textarea or editable)."""
        await self.focus()
        await self._call(
            "if ('select' in el) { el.select(); return true; }"
            "const range = document.createRange(); range.selectNodeContents(el);"
            "const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);"
            "return true;"
        )

    async def wait_for(
        self, *, state: LocatorState = "visible", timeout: float | None = None
    ) -> None:
        """Wait until the element reaches ``state``.

        Raises:
            ValueError: For an unknown state.
            TimeoutError_: If the state is not reached in time.
        """
        if state not in _STATE_CHECKS:
            raise ValueError(f"Unknown state {state!r}")
        await poll(
            lambda: self._call(_STATE_CHECKS[state]),
            timeout or self.page.default_timeout,
            0.1,
            f"{self.selector!r} to be {state}",
        )

    async def scroll_into_view_if_needed(self) -> None:
        """Centre the element if any part of it is outside the viewport."""
        await self.wait_for(state="attached")
        await self._call(
            "const r = el.getBoundingClientRect();"
            "if (r.top < 0 || r.left < 0 || r.bottom > innerHeight || r.right > innerWidth)"
            "  el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});"
            "return true;"
        )

    async def bounding_box(self) -> dict[str, float] | None:
        """Viewport box ``{x, y, width, height}``, or ``None`` if absent or empty."""
        box = await self._call(
            "if (!el) return null; const r = el.getBoundingClientRect();"
            "return r.width && r.height ? {x: r.x, y: r.y, width: r.width, height: r.height}"
            " : null;"
        )
        return dict(box) if box else None

    async def click(self, *, timeout: float | None = None, delay: float = 0.05) -> None:
        """Click the element centre directly.

        Use [`botonomus.Human`][botonomus.Human] (or ``humanize=True``) for human-like paths.

        Raises:
            EvaluationError: If the element has no box after scrolling.
        """
        await self.wait_for(state="visible", timeout=timeout)
        await self.scroll_into_view_if_needed()
        # Chromium drops presses that arrive before the document's first paint after a
        # navigation; two animation frames guarantee one has been presented.
        await self._call(
            "return new Promise(r => requestAnimationFrame(() => requestAnimationFrame("
            "() => r(true))));"
        )
        box = await self.bounding_box()
        if box is None:
            raise EvaluationError(f"{self.selector!r} has no box")
        await self.page.mouse.click(
            box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, delay=delay
        )

    async def focus(self) -> None:
        """Focus the element and wait one rendered frame.

        Without the frame wait the browser may not yet treat the element as the
        focused editable, and the first keystroke's text can be dropped.
        """
        await self.wait_for(state="attached")
        await self._call(
            "el.focus(); return new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)));"
        )

    async def fill(self, value: str) -> None:
        """Replace the value as one composed input (fast, not keystroke by keystroke)."""
        await self.focus()
        await self._call("if ('select' in el) el.select(); return true;")
        await self.page.keyboard.press("Backspace")
        if value:
            await self.page.keyboard.insert_text(value)

    async def type(self, text: str, *, delay: float = 0.0) -> None:
        """Focus, then type ``text`` with real key events."""
        await self.focus()
        await self.page.keyboard.type(text, delay=delay)

    async def inner_text(self) -> str:
        """The element's rendered text."""
        await self.wait_for(state="attached")
        return str(await self._call("return el.innerText;"))

    async def text_content(self) -> str | None:
        """The element's ``textContent``, or ``None`` if absent."""
        value = await self._call("return el ? el.textContent : null;")
        return None if value is None else str(value)

    async def get_attribute(self, name: str) -> str | None:
        """An attribute value, or ``None`` if the attribute is absent."""
        await self.wait_for(state="attached")
        value = await self.page.evaluate(
            "(a) => { const el = __b.resolve(a.s)[a.i]; return el.getAttribute(a.n); }",
            {"s": self.selector, "i": self.index, "n": name},
        )
        return None if value is None else str(value)

    async def input_value(self) -> str:
        """The current ``value`` of an input, textarea or select."""
        await self.wait_for(state="attached")
        return str(await self._call("return el.value;"))

    async def all_inner_texts(self) -> list[str]:
        """Rendered text of every match."""
        return list(await self._call("return all.map(e => e.innerText);"))
