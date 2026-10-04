"""An auto-humanizing page wrapper.

`HumanPage` wraps a native [`botonomus.cdp.Page`][botonomus.cdp.Page] or a Playwright page
and routes the interactive methods through [`Human`][botonomus.human.Human].

Humanized on `HumanPage`: ``click``, ``fill``, ``type``, ``press``, ``hover``,
``scroll``; ``locator`` and the ``get_by_*`` factories return `HumanLocator`.

Humanized on `HumanLocator`: ``click``, ``fill``, ``type``, ``press``,
``hover``; ``nth`` and ``first`` stay wrapped.

Everything else (``goto``, ``evaluate``, ``title``, ``screenshot``, ``mouse``,
``keyboard`` …) is delegated unchanged to the wrapped object and is **not**
humanized. ``.raw`` returns the wrapped page or locator to bypass humanizing.
"""

from typing import Any

from .config import HumanConfig
from .human import Human

_LOCATOR_FACTORIES = frozenset(
    {
        "get_by_role",
        "get_by_text",
        "get_by_label",
        "get_by_placeholder",
        "get_by_alt_text",
        "get_by_title",
        "get_by_test_id",
    }
)


class HumanLocator:
    """A locator whose interactions go through `Human`.

    Args:
        human: The input engine (shared with the owning `HumanPage`).
        locator: The driver's locator.
    """

    def __init__(self, human: Human, locator: Any) -> None:
        self._human = human
        self._locator = locator

    @property
    def raw(self) -> Any:
        """The wrapped driver locator, for un-humanized access."""
        return self._locator

    def nth(self, index: int) -> "HumanLocator":
        """The ``index``-th match, still humanized."""
        return HumanLocator(self._human, self._locator.nth(index))

    @property
    def first(self) -> "HumanLocator":
        """The first match, still humanized."""
        return HumanLocator(self._human, self._locator.first)

    async def click(self, *, button: str = "left") -> None:
        """Move along a human path and click; see `Human.click`."""
        await self._human.click(self._locator, button=button)

    async def hover(self) -> None:
        """Move the pointer over the element; see `Human.hover`."""
        await self._human.hover(self._locator)

    async def fill(self, value: str, *, sensitive: bool = False) -> None:
        """Click, clear and type ``value`` keystroke by keystroke.

        Args:
            value: The value to leave in the field.
            sensitive: Disable mistakes (passwords, codes); see `Human.type`.
        """
        await self._human.fill(self._locator, value, sensitive=sensitive)

    async def type(self, text: str, *, sensitive: bool = False) -> None:
        """Click the element, then type ``text`` humanly after its current content.

        Args:
            text: Text to type.
            sensitive: Disable mistakes (passwords, codes); see `Human.type`.
        """
        await self._human.type(text, self._locator, sensitive=sensitive)

    async def press(self, key: str) -> None:
        """Focus the element (without clicking it) and press ``key``.

        Focus is moved programmatically, as Playwright's ``press`` does, so pressing
        a key on a button never also clicks it.
        """
        await self._locator.focus()
        await self._human.press(key)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            # Private names are never delegated; this also avoids recursion before
            # __init__ has set the wrapped object (copy, pickle).
            raise AttributeError(name)
        return getattr(self._locator, name)

    def __repr__(self) -> str:
        return f"HumanLocator({self._locator!r})"


class HumanPage:
    """A page wrapper that makes interactions human-like.

    Selectors are resolved with the wrapped page's own ``locator()``, so every
    selector the driver supports works here. See the module docstring for which
    methods are humanized; all other attributes are delegated unchanged.

    Args:
        page: A native [`botonomus.cdp.Page`][botonomus.cdp.Page] or a Playwright page.
        human: An existing input engine for ``page``; created when omitted.
        config: Behaviour for a newly created `Human`.
        seed: Seed for a newly created `Human`.

    Raises:
        ValueError: If ``human`` is given together with ``config`` or ``seed``.
    """

    def __init__(
        self,
        page: Any,
        *,
        human: Human | None = None,
        config: HumanConfig | None = None,
        seed: int | None = None,
    ) -> None:
        if human is not None and (config is not None or seed is not None):
            raise ValueError("Pass human, or config/seed for a new one, not both")
        self._page = page
        self._human = human or Human(page, config=config, seed=seed)

    @property
    def raw(self) -> Any:
        """The wrapped page, for un-humanized access."""
        return self._page

    @property
    def human(self) -> Human:
        """The input engine, for direct use (``idle``, ``move_to`` …)."""
        return self._human

    def locator(self, selector: str) -> HumanLocator:
        """A humanized locator for ``selector``."""
        return HumanLocator(self._human, self._page.locator(selector))

    async def click(self, selector: str, *, button: str = "left") -> None:
        """Click the element matching ``selector`` with a human pointer path."""
        await self._human.click(self._page.locator(selector), button=button)

    async def hover(self, selector: str) -> None:
        """Move the pointer over the element matching ``selector``."""
        await self._human.hover(self._page.locator(selector))

    async def fill(self, selector: str, value: str, *, sensitive: bool = False) -> None:
        """Click, select all, and type ``value`` keystroke by keystroke.

        Unlike the driver's ``fill``, text is never inserted in one go.

        Args:
            selector: The field.
            value: The value to leave in the field.
            sensitive: Disable mistakes (passwords, codes); see `Human.type`.
        """
        await self._human.fill(self._page.locator(selector), value, sensitive=sensitive)

    async def type(self, selector: str, text: str, *, sensitive: bool = False) -> None:
        """Click the element, then type ``text`` humanly after its current content.

        Args:
            selector: The field.
            text: Text to type.
            sensitive: Disable mistakes (passwords, codes); see `Human.type`.
        """
        await self._human.type(text, self._page.locator(selector), sensitive=sensitive)

    async def press(self, key_or_selector: str, key: str | None = None) -> None:
        """Press a key on the focused element, or on ``selector`` first.

        Both ``press("Enter")`` and the Playwright form ``press("#q", "Enter")`` work.

        Args:
            key_or_selector: The key, or a selector when ``key`` is given.
            key: The key to press on that selector's element.
        """
        if key is None:
            await self._human.press(key_or_selector)
        else:
            await self.locator(key_or_selector).press(key)

    async def scroll(self, delta_y: float, *, delta_x: float = 0.0) -> None:
        """Wheel-scroll by ``delta_y`` (and ``delta_x``) CSS pixels in eased ticks."""
        await self._human.scroll(delta_y, delta_x=delta_x)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            # Private names are never delegated; this also avoids recursion before
            # __init__ has set the wrapped object (copy, pickle).
            raise AttributeError(name)
        attribute = getattr(self._page, name)
        if name in _LOCATOR_FACTORIES and callable(attribute):
            factory = attribute

            def humanized(*args: Any, **kwargs: Any) -> HumanLocator:
                return HumanLocator(self._human, factory(*args, **kwargs))

            return humanized
        return attribute

    def __repr__(self) -> str:
        return f"HumanPage({self._page!r})"
