"""Human-like input over the page's own trusted input events.

Nothing is injected into the page: movement, clicks, keys and wheel events are
dispatched by the browser through CDP ``Input``. This shapes timing and trajectories
only; behavioural classifiers can still distinguish automation.
"""

import math
import random
from typing import Any, Literal, cast

from .actionability import Actionable, NotActionableError, wait_until_actionable
from .clock import Clock, RealClock
from .config import HumanConfig
from .keyboard import Keystroke, plan_keystrokes
from .paths import Point, bezier_path, eased_weights, movement_duration, target_point
from .timing import KeystrokeRhythm, bounded_lognormal

MouseButton = Literal["left", "middle", "right"]

_MAX_WHEEL_ATTEMPTS = 4


class Human:
    """Human-like input for one page. Remembers the pointer position between actions.

    Works with native [`botonomus.cdp.Page`][botonomus.cdp.Page] and Playwright-compatible pages:
    it only uses ``page.mouse`` (``move``, ``down``, ``up``, ``wheel``),
    ``page.keyboard`` (``type``, ``press``) and ``page.evaluate``. Targets are
    locators from either driver, or ``(x, y)`` points.

    Before acting on a locator, `Human` waits until it is visible, enabled
    and has a stable box, and brings it into view with wheel scrolling.

    Args:
        page: The page to drive.
        config: Behaviour parameters; ``HumanConfig()`` by default.
        profile: Deprecated name for ``config``.
        seed: Seed for reproducible paths and timing.
        clock: Time source; replace it to simulate time in tests.

    Raises:
        ValueError: If both ``config`` and ``profile`` are given.
    """

    def __init__(
        self,
        page: Any,
        *,
        config: HumanConfig | None = None,
        profile: HumanConfig | None = None,
        seed: int | None = None,
        clock: Clock | None = None,
    ) -> None:
        if config is not None and profile is not None:
            raise ValueError("Pass config or profile, not both")
        self.page = page
        self.config: HumanConfig = config or profile or HumanConfig()
        self.clock: Clock = clock or RealClock()
        self._rng = random.Random(seed)
        self._rhythm = KeystrokeRhythm(self.config, self._rng)
        self._position: Point | None = None
        self._viewport: tuple[float, float] | None = None

    @property
    def profile(self) -> HumanConfig:
        """Deprecated alias of `config`."""
        return self.config

    @property
    def position(self) -> Point | None:
        """Last pointer position this instance moved to, if any."""
        return self._position

    @property
    def rng(self) -> random.Random:
        """The random source, for callers that want correlated choices."""
        return self._rng

    async def pause(self, low: float | None = None, high: float | None = None) -> None:
        """Sleep for a right-skewed "thinking" interval; sends no CDP traffic.

        Args:
            low: Lower typical bound in seconds; ``config.think[0]`` by default.
            high: Upper typical bound in seconds; ``config.think[1]`` by default.
        """
        default_low, default_high = self.config.think
        low = default_low if low is None else low
        high = default_high if high is None else high
        await self.clock.sleep(bounded_lognormal(self._rng, low, max(low, high)))

    async def idle(self, seconds: float | None = None) -> None:
        """Small, low-amplitude pointer drift around the current position.

        Does nothing until the pointer has a known position. Never runs longer than
        ``seconds`` (by default a draw from ``config.idle_duration``).

        Args:
            seconds: Upper bound on the drift's duration.
        """
        if self._position is None:
            return
        if seconds is None:
            seconds = bounded_lognormal(self._rng, *self.config.idle_duration)
        deadline = self.clock.now() + seconds
        anchor_x, anchor_y = self._position
        amplitude = self.config.idle_amplitude
        x, y = self._position
        while self.clock.now() < deadline:
            limit = 3 * amplitude
            goal_x = anchor_x + max(-limit, min(limit, self._rng.gauss(0, amplitude)))
            goal_y = anchor_y + max(-limit, min(limit, self._rng.gauss(0, amplitude)))
            steps = self._rng.randint(2, 4)
            for i in range(1, steps + 1):
                if self.clock.now() >= deadline:
                    break
                px, py = x + (goal_x - x) * i / steps, y + (goal_y - y) * i / steps
                await self.page.mouse.move(px, py)
                self._position = (px, py)
                await self._sleep_until(deadline, bounded_lognormal(self._rng, 0.015, 0.05))
            x, y = self._position
            await self._sleep_until(deadline, bounded_lognormal(self._rng, 0.06, 0.3))

    async def move_to(self, x: float, y: float, *, target_width: float = 40.0) -> None:
        """Move the pointer to ``(x, y)`` along a curved, eased path.

        Long, fast moves sometimes overshoot the target slightly and correct back
        (``config.overshoot_probability``), as aimed human movements do.

        Args:
            x: Destination x in CSS pixels.
            y: Destination y in CSS pixels.
            target_width: Size of the target, which sets the Fitts's-law duration.
        """
        start = self._position or await self._initial_position()
        distance = math.hypot(x - start[0], y - start[1])
        if distance < 1:
            return
        config = self.config
        if (
            distance >= config.overshoot_min_distance
            and self._rng.random() < config.overshoot_probability
        ):
            over = await self._overshoot_point(start, (x, y), distance)
            # The ballistic phase aims at a larger, nearer target: it is fast.
            await self._trace(start, over, target_width * 2)
            await self.clock.sleep(bounded_lognormal(self._rng, 0.04, 0.16))
            await self._trace(over, (x, y), target_width)
        else:
            await self._trace(start, (x, y), target_width)
        self._position = (x, y)

    async def move_to_locator(self, locator: Any) -> Point:
        """Bring ``locator`` into view, wait until it is actionable, and move into it.

        Returns:
            The point reached.

        Raises:
            NotActionableError: If the element does not become visible, enabled and
                stable within ``config.actionability_timeout`` (a ``ValueError``).
        """
        box = await self._actionable_box(locator)
        x, y = target_point(box, self._rng)
        await self.move_to(x, y, target_width=min(box["width"], box["height"]))
        return x, y

    async def hover(self, target: Any) -> None:
        """Move the pointer over ``target`` (a locator or a point) without clicking.

        Raises:
            NotActionableError: If a locator target never becomes actionable.
        """
        await self._between_actions()
        if isinstance(target, tuple):
            await self.move_to(*target)
        else:
            await self.move_to_locator(target)

    async def click(self, target: Any, *, button: str = "left") -> None:
        """Move to ``target`` (a locator or a point) and click with a natural hold.

        Raises:
            ValueError: For an unknown button.
            NotActionableError: If a locator target never becomes actionable.
        """
        mouse_button = _button(button)
        await self.hover(target)
        await self.clock.sleep(bounded_lognormal(self._rng, *self.config.aim_pause))
        await self.page.mouse.down(button=mouse_button)
        await self.clock.sleep(bounded_lognormal(self._rng, *self.config.click_hold))
        await self.page.mouse.up(button=mouse_button)

    async def type(self, text: str, target: Any | None = None, *, sensitive: bool = False) -> None:
        """Type ``text`` key by key into the focused element; clicks ``target`` first.

        Gaps follow a lognormal rhythm with per-session drift, faster common letter
        pairs and longer pauses after words and punctuation. With
        ``config.mistype_rate`` per letter a neighbouring key is hit, noticed a
        character or two later, and corrected with backspaces; the field always ends
        up containing exactly ``text`` appended to what was there.

        Args:
            text: Text to type.
            target: Optional locator or point to click first.
            sensitive: Disable mistakes, for passwords, codes and other secrets
                where a transient wrong character is unacceptable (it can trigger
                validation, lockouts or be captured by key loggers on the page).
        """
        if target is not None:
            await self.click(target)
            await self.pause(0.1, 0.3)
        await self._type_text(text, sensitive=sensitive)

    async def fill(self, target: Any, value: str, *, sensitive: bool = False) -> None:
        """Click ``target``, clear it, and type ``value`` humanly.

        Clearing selects the current text (``select_text`` on the locator) and
        presses Backspace; without ``select_text`` it presses End and one Backspace
        per character. Unlike a driver's ``fill``, the text arrives as keystrokes.

        Args:
            target: A locator for an input, textarea or editable element.
            value: The value to leave in the field.
            sensitive: Disable mistakes; see `type`.
        """
        await self.click(target)
        await self.pause(0.08, 0.25)
        await self._clear(target)
        await self._type_text(value, sensitive=sensitive)

    async def press(self, key: str) -> None:
        """Press ``key`` (for example ``"Enter"``) after a short reaction pause.

        Args:
            key: A key name understood by the page's keyboard.
        """
        await self.clock.sleep(bounded_lognormal(self._rng, 0.05, 0.2))
        await self.page.keyboard.press(key)

    async def scroll(self, delta_y: float, *, delta_x: float = 0.0) -> None:
        """Wheel-scroll in several eased increments, like a physical wheel."""
        if self._position is None:
            await self.move_to(*await self._initial_position())
        ticks = max(3, min(25, int(abs(delta_y) / 90) + self._rng.randint(2, 5)))
        for weight in eased_weights(ticks):
            await self.page.mouse.wheel(delta_x * weight, delta_y * weight)
            await self.clock.sleep(bounded_lognormal(self._rng, 0.012, 0.045))

    async def _type_text(self, text: str, *, sensitive: bool) -> None:
        rate = 0.0 if sensitive else self.config.mistype_rate
        plan = plan_keystrokes(
            text,
            self.config.keyboard_layout,
            rate,
            self._rng,
            max_notice_delay=self.config.mistype_notice,
        )
        keyboard = self.page.keyboard
        for index, stroke in enumerate(plan):
            if stroke.kind == "backspace":
                await keyboard.press("Backspace")
            else:
                await keyboard.type(stroke.text)
            following = plan[index + 1] if index + 1 < len(plan) else None
            await self.clock.sleep(self._gap_after(stroke, following))

    def _gap_after(self, stroke: Keystroke, following: Keystroke | None) -> float:
        rhythm = self._rhythm
        if stroke.kind == "char":
            if following is not None and following.kind == "backspace":
                return rhythm.correction_pause()
            next_char = following.text if following is not None else None
            return rhythm.gap(stroke.text, next_char)
        if following is not None and following.kind == "backspace":
            return rhythm.backspace_gap()
        return rhythm.backspace_gap() * 1.6

    async def _clear(self, target: Any) -> None:
        current: str | None = None
        reader = getattr(target, "input_value", None)
        if reader is not None:
            try:
                current = str(await reader())
            except Exception:  # Not a form control (e.g. contenteditable).
                current = None
        if current == "":
            return
        select = getattr(target, "select_text", None)
        if select is not None:
            await select()
            await self.clock.sleep(bounded_lognormal(self._rng, 0.05, 0.18))
            await self.page.keyboard.press("Backspace")
        elif current:
            await self.page.keyboard.press("End")
            for _ in current:
                await self.page.keyboard.press("Backspace")
                await self.clock.sleep(self._rhythm.backspace_gap())
        await self.clock.sleep(bounded_lognormal(self._rng, 0.08, 0.25))

    async def _actionable_box(self, locator: Any) -> dict[str, float]:
        if not isinstance(locator, Actionable):
            raise TypeError("Target must be a locator with bounding_box() and is_visible()")
        timeout = self.config.actionability_timeout
        box = await wait_until_actionable(locator, timeout=timeout, clock=self.clock)
        width, height = await self._viewport_size()
        for _ in range(_MAX_WHEEL_ATTEMPTS):
            if _in_view(box, width, height):
                break
            await self.scroll(box["y"] + box["height"] / 2 - height / 2)
            await self.clock.sleep(bounded_lognormal(self._rng, 0.08, 0.3))
            box = await wait_until_actionable(locator, timeout=timeout, clock=self.clock)
        if not _in_view(box, width, height):
            # Nested scroll containers or horizontal overflow: wheel input over the
            # pointer did not reach the element's scroller.
            scroll = getattr(locator, "scroll_into_view_if_needed", None)
            if scroll is None:
                raise NotActionableError("Element is outside the viewport")
            await scroll()
            box = await wait_until_actionable(locator, timeout=timeout, clock=self.clock)
        return box

    async def _between_actions(self) -> None:
        if self.config.idle_moves:
            await self.idle()

    async def _trace(self, start: Point, end: Point, width: float) -> None:
        distance = math.hypot(end[0] - start[0], end[1] - start[1])
        if distance < 1:
            return
        path = bezier_path(start, end, self._rng)
        total = movement_duration(distance, width, self.config.move_speed, self._rng)
        for px, py in path:
            await self.page.mouse.move(px, py)
            await self.clock.sleep(total / len(path))

    async def _overshoot_point(self, start: Point, end: Point, distance: float) -> Point:
        ux, uy = (end[0] - start[0]) / distance, (end[1] - start[1]) / distance
        amount = max(6.0, min(60.0, distance * self._rng.lognormvariate(math.log(0.05), 0.35)))
        side = self._rng.gauss(0, amount * 0.3)
        width, height = await self._viewport_size()
        x = end[0] + ux * amount - uy * side
        y = end[1] + uy * amount + ux * side
        return (max(1.0, min(width - 1, x)), max(1.0, min(height - 1, y)))

    async def _viewport_size(self) -> tuple[float, float]:
        if self._viewport is None:
            size = getattr(self.page, "viewport_size", None)
            if size is None:
                size = await self.page.evaluate("({width: innerWidth, height: innerHeight})")
            self._viewport = (float(size["width"]), float(size["height"]))
        return self._viewport

    async def _initial_position(self) -> Point:
        width, height = await self._viewport_size()
        return (self._rng.uniform(0.3, 0.7) * width, self._rng.uniform(0.3, 0.7) * height)

    async def _sleep_until(self, deadline: float, seconds: float) -> None:
        await self.clock.sleep(min(seconds, max(0.0, deadline - self.clock.now())))


def _in_view(box: dict[str, float], width: float, height: float) -> bool:
    centre_x, centre_y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    return 0 <= centre_x <= width and 0 <= centre_y <= height


def _button(name: str) -> MouseButton:
    if name not in ("left", "middle", "right"):
        raise ValueError("button must be 'left', 'middle' or 'right'")
    return cast(MouseButton, name)
