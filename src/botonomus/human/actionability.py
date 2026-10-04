"""Wait until an element can be acted on, as a person would before clicking.

Works with any locator satisfying `Actionable`: the native
[`botonomus.cdp.Locator`][botonomus.cdp.Locator] and Playwright locators both do.
"""

import math
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from ..errors import BotonomusError
from .clock import Clock, RealClock

# Two frames at 60 Hz: a box measured this far apart is not mid-animation.
FRAME_INTERVAL = 2 / 60
_STABLE_TOLERANCE = 0.5


class NotActionableError(BotonomusError, ValueError):
    """An element did not become visible, enabled and stable in time.

    Subclasses `ValueError` because 0.1 raised ``ValueError`` for an element
    without a visible box.
    """


@runtime_checkable
class Actionable(Protocol):
    """The locator surface needed for actionability checks."""

    async def bounding_box(self) -> Mapping[str, float] | None:
        """Viewport box ``{x, y, width, height}`` or ``None``."""
        ...

    async def is_visible(self) -> bool:
        """Whether the element is attached and rendered with a non-empty box."""
        ...


@runtime_checkable
class SupportsEnabled(Protocol):
    """Optional: locators that can report whether the element is enabled."""

    async def is_enabled(self) -> bool:
        """Whether the element accepts input."""
        ...


async def wait_until_actionable(
    locator: Any,
    *,
    timeout: float = 10.0,
    clock: Clock | None = None,
    require_enabled: bool = True,
) -> dict[str, float]:
    """Wait until ``locator`` is attached, visible, enabled and has a stable box.

    Stability means two bounding boxes measured `FRAME_INTERVAL` apart agree
    within half a pixel, so an element still sliding or growing is not targeted.
    The enabled check is skipped for locators without ``is_enabled``.

    Args:
        locator: An `Actionable` locator.
        timeout: Seconds before giving up.
        clock: Time source; the event loop clock by default.
        require_enabled: Whether a disabled element should be waited on.

    Returns:
        The last measured box.

    Raises:
        NotActionableError: If the element is not actionable within ``timeout``;
            the message names the failing condition.
    """
    clock = clock or RealClock()
    deadline = clock.now() + timeout
    check_enabled = require_enabled and isinstance(locator, SupportsEnabled)
    reason = "visible"
    while True:
        box = await _stable_box(locator, clock, check_enabled)
        if isinstance(box, dict):
            return box
        reason = box
        if clock.now() >= deadline:
            raise NotActionableError(f"Element did not become {reason} within {timeout}s")
        await clock.sleep(0.05)


async def _stable_box(locator: Any, clock: Clock, check_enabled: bool) -> dict[str, float] | str:
    if not await locator.is_visible():
        return "visible"
    if check_enabled and not await locator.is_enabled():
        return "enabled"
    first = await locator.bounding_box()
    await clock.sleep(FRAME_INTERVAL)
    second = await locator.bounding_box()
    if not first or not second:
        return "visible"
    if any(
        not math.isclose(first[k], second[k], abs_tol=_STABLE_TOLERANCE)
        for k in ("x", "y", "width", "height")
    ):
        return "stable"
    return dict(second)
