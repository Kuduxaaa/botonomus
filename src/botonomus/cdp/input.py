"""Trusted mouse and keyboard input through the CDP ``Input`` domain.

Events dispatched with ``Input.dispatchMouseEvent`` / ``Input.dispatchKeyEvent`` are
routed through the browser's input pipeline, so page scripts see ``isTrusted ===
true``. The CDP parameter defaults, however, do not match a real device: Chromium
fills a missing ``force`` with 0 (``PointerEvent.pressure`` 0 while a button is held,
where a real mouse reports 0.5), a missing ``clickCount`` with 0 (``MouseEvent.detail``
0) and a missing ``buttons`` with 0 (``MouseEvent.buttons`` 0 mid-drag). `Mouse`
always sends those fields with the values a Windows mouse would produce.

Key definitions follow the US keyboard layout. Characters outside it are delivered
as composed text (``Input.insertText``), like an IME would.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from .connection import CDPSession

Button = Literal["left", "middle", "right"]
"""A mouse button name as used by CDP."""

_BUTTON_BITS = {"left": 1, "right": 2, "middle": 4}
# Order in which Windows reports a held button on WM_MOUSEMOVE (MK_LBUTTON first).
_MOVE_BUTTON_ORDER: tuple[Button, ...] = ("left", "middle", "right")

ALT = 1
"""CDP modifier bit for Alt."""
CONTROL = 2
"""CDP modifier bit for Control."""
META = 4
"""CDP modifier bit for Meta (Windows key / Command)."""
SHIFT = 8
"""CDP modifier bit for Shift."""

PRESSED_FORCE = 0.5
"""``force`` sent while a button is held: what Blink reports for a real mouse."""

DOUBLE_CLICK_TIME = 0.5
"""Seconds between presses that still count as a multi-click (Windows default)."""

DOUBLE_CLICK_SIZE = 4.0
"""Width and height in pixels of the multi-click rectangle centred on the previous
press (Windows ``SM_CXDOUBLECLK`` / ``SM_CYDOUBLECLK`` default)."""

KEY_LOCATION_STANDARD = 0
"""``KeyboardEvent.location`` for keys with a single position."""
KEY_LOCATION_LEFT = 1
"""``KeyboardEvent.location`` for the left-hand copy of a modifier key."""
KEY_LOCATION_RIGHT = 2
"""``KeyboardEvent.location`` for the right-hand copy of a modifier key."""


@dataclass(frozen=True, slots=True)
class KeyDefinition:
    """How one key is described to ``Input.dispatchKeyEvent``.

    Attributes:
        key: DOM ``KeyboardEvent.key`` value.
        code: DOM ``KeyboardEvent.code`` (physical key) value.
        key_code: Windows virtual key code (``KeyboardEvent.keyCode``).
        text: Text the key produces, empty for non-printing keys.
        shift: Whether Shift must be held to produce ``key``.
        location: DOM ``KeyboardEvent.location``.
    """

    key: str
    code: str
    key_code: int
    text: str = ""
    shift: bool = False
    location: int = KEY_LOCATION_STANDARD


_NAMED = {
    "Enter": KeyDefinition("Enter", "Enter", 13, "\r"),
    "Tab": KeyDefinition("Tab", "Tab", 9),
    "Backspace": KeyDefinition("Backspace", "Backspace", 8),
    "Delete": KeyDefinition("Delete", "Delete", 46),
    "Escape": KeyDefinition("Escape", "Escape", 27),
    "ArrowLeft": KeyDefinition("ArrowLeft", "ArrowLeft", 37),
    "ArrowUp": KeyDefinition("ArrowUp", "ArrowUp", 38),
    "ArrowRight": KeyDefinition("ArrowRight", "ArrowRight", 39),
    "ArrowDown": KeyDefinition("ArrowDown", "ArrowDown", 40),
    "Home": KeyDefinition("Home", "Home", 36),
    "End": KeyDefinition("End", "End", 35),
    "PageUp": KeyDefinition("PageUp", "PageUp", 33),
    "PageDown": KeyDefinition("PageDown", "PageDown", 34),
    "Shift": KeyDefinition("Shift", "ShiftLeft", 16, location=KEY_LOCATION_LEFT),
    "Control": KeyDefinition("Control", "ControlLeft", 17, location=KEY_LOCATION_LEFT),
    "Alt": KeyDefinition("Alt", "AltLeft", 18, location=KEY_LOCATION_LEFT),
    "Meta": KeyDefinition("Meta", "MetaLeft", 91, location=KEY_LOCATION_LEFT),
    "ShiftLeft": KeyDefinition("Shift", "ShiftLeft", 16, location=KEY_LOCATION_LEFT),
    "ShiftRight": KeyDefinition("Shift", "ShiftRight", 16, location=KEY_LOCATION_RIGHT),
    "ControlLeft": KeyDefinition("Control", "ControlLeft", 17, location=KEY_LOCATION_LEFT),
    "ControlRight": KeyDefinition("Control", "ControlRight", 17, location=KEY_LOCATION_RIGHT),
    "AltLeft": KeyDefinition("Alt", "AltLeft", 18, location=KEY_LOCATION_LEFT),
    "AltRight": KeyDefinition("Alt", "AltRight", 18, location=KEY_LOCATION_RIGHT),
    "MetaLeft": KeyDefinition("Meta", "MetaLeft", 91, location=KEY_LOCATION_LEFT),
    "MetaRight": KeyDefinition("Meta", "MetaRight", 92, location=KEY_LOCATION_RIGHT),
    " ": KeyDefinition(" ", "Space", 32, " "),
}
_PUNCTUATION = {
    # unshifted, shifted, code, Windows virtual key
    ("-", "_", "Minus", 189),
    ("=", "+", "Equal", 187),
    ("[", "{", "BracketLeft", 219),
    ("]", "}", "BracketRight", 221),
    ("\\", "|", "Backslash", 220),
    (";", ":", "Semicolon", 186),
    ("'", '"', "Quote", 222),
    (",", "<", "Comma", 188),
    (".", ">", "Period", 190),
    ("/", "?", "Slash", 191),
    ("`", "~", "Backquote", 192),
}
_SHIFTED_DIGITS = dict(zip(")!@#$%^&*(", "0123456789", strict=True))


def key_definition(key: str) -> KeyDefinition | None:
    """Look up the US-layout definition of a named key or a single character.

    Args:
        key: A DOM key name such as ``"Enter"`` or ``"ShiftRight"``, or one character.

    Returns:
        The definition, or ``None`` if the key is not on a US keyboard.
    """
    if key in _NAMED:
        return _NAMED[key]
    if len(key) != 1:
        return None
    if "a" <= key <= "z":
        return KeyDefinition(key, f"Key{key.upper()}", ord(key.upper()), key)
    if "A" <= key <= "Z":
        return KeyDefinition(key, f"Key{key}", ord(key), key, shift=True)
    if key.isdigit() and key.isascii():
        return KeyDefinition(key, f"Digit{key}", ord(key), key)
    if key in _SHIFTED_DIGITS:
        digit = _SHIFTED_DIGITS[key]
        return KeyDefinition(key, f"Digit{digit}", ord(digit), key, shift=True)
    for plain, shifted, code, key_code in _PUNCTUATION:
        if key == plain:
            return KeyDefinition(key, code, key_code, key)
        if key == shifted:
            return KeyDefinition(key, code, key_code, key, shift=True)
    if key == "\n":
        return _NAMED["Enter"]
    return None


class Mouse:
    """A virtual mouse whose events match a real Windows mouse field for field.

    The mouse remembers its position and held buttons. Every event carries the
    ``buttons`` bitmask; every event sent while a button is held carries
    ``force`` 0.5, so ``PointerEvent.pressure`` is 0.5 exactly as for hardware.
    Presses are counted like Windows does: a press of the same button within
    `DOUBLE_CLICK_TIME` seconds and inside a `DOUBLE_CLICK_SIZE`-pixel
    rectangle around the previous press continues the sequence (``detail`` 2, 3,
    ...); anything else starts again at 1.

    ``tangentialPressure``, ``tiltX``, ``tiltY`` and ``twist`` are left to
    Chromium's defaults (all 0), which are the values a mouse reports.

    Args:
        session: The page session to dispatch events on.
        clock: Monotonic clock in seconds, used for multi-click timing.
    """

    def __init__(self, session: CDPSession, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._session = session
        self._clock = clock
        self._x = 0.0
        self._y = 0.0
        self._buttons = 0
        self._click_count = 0
        self._last_press: tuple[Button, float, float, float] | None = None

    @property
    def position(self) -> tuple[float, float]:
        """The current pointer position in CSS pixels relative to the viewport."""
        return self._x, self._y

    @property
    def buttons(self) -> int:
        """Bitmask of held buttons (1 left, 2 right, 4 middle), as in ``MouseEvent``."""
        return self._buttons

    async def move(self, x: float, y: float, *, steps: int = 1) -> None:
        """Move the pointer in a straight line, dispatching ``steps`` move events.

        While a button is held the moves are drag moves: they carry the button,
        the ``buttons`` mask and ``force`` 0.5.

        Args:
            x: Destination x in CSS pixels.
            y: Destination y in CSS pixels.
            steps: Number of intermediate events; values below 1 count as 1.
        """
        steps = max(1, steps)
        start_x, start_y = self._x, self._y
        for i in range(1, steps + 1):
            self._x = start_x + (x - start_x) * i / steps
            self._y = start_y + (y - start_y) * i / steps
            await self._dispatch("mouseMoved", button=self._move_button(), clickCount=0)

    async def down(self, *, button: Button = "left", click_count: int | None = None) -> None:
        """Press a button at the current position.

        Args:
            button: Button to press.
            click_count: Explicit ``MouseEvent.detail``. ``None`` (default) derives it
                from the previous press using the double-click time and rectangle.
        """
        now = self._clock()
        if click_count is None:
            click_count = self._next_click_count(button, now)
        self._click_count = click_count
        self._last_press = (button, now, self._x, self._y)
        self._buttons |= _BUTTON_BITS[button]
        await self._dispatch("mousePressed", button=button, clickCount=click_count)

    async def up(self, *, button: Button = "left", click_count: int | None = None) -> None:
        """Release a button at the current position.

        Args:
            button: Button to release.
            click_count: Explicit ``MouseEvent.detail``. ``None`` (default) reuses the
                count of the matching press, which is what the OS reports.
        """
        if click_count is None:
            click_count = max(1, self._click_count)
        self._buttons &= ~_BUTTON_BITS[button]
        await self._dispatch("mouseReleased", button=button, clickCount=click_count)

    async def click(
        self,
        x: float,
        y: float,
        *,
        button: Button = "left",
        delay: float = 0.0,
        click_count: int | None = None,
    ) -> None:
        """Move to a point, then press and release a button there.

        The move always comes first so the press never appears without a
        preceding ``mousemove`` (which would make ``movementX/Y`` implausible).

        Args:
            x: Target x in CSS pixels.
            y: Target y in CSS pixels.
            button: Button to click.
            delay: Seconds to hold the button down.
            click_count: Explicit ``detail``; ``None`` derives it automatically, so a
                second click soon after the first at the same spot is a double click.
        """
        await self.move(x, y)
        await self.down(button=button, click_count=click_count)
        if delay:
            await asyncio.sleep(delay)
        await self.up(button=button)

    async def dblclick(
        self,
        x: float,
        y: float,
        *,
        button: Button = "left",
        delay: float = 0.0,
        interval: float = 0.08,
    ) -> None:
        """Double-click a point: two clicks with ``detail`` 1 then 2.

        The browser fires ``dblclick`` after the second release.

        Args:
            x: Target x in CSS pixels.
            y: Target y in CSS pixels.
            button: Button to click.
            delay: Seconds to hold the button down on each click.
            interval: Seconds between the first release and the second press.

        Raises:
            ValueError: If the clicks could not fall inside the double-click time.
        """
        if delay * 2 + interval >= DOUBLE_CLICK_TIME:
            raise ValueError("delay and interval exceed the double-click time")
        await self.click(x, y, button=button, delay=delay, click_count=1)
        if interval:
            await asyncio.sleep(interval)
        await self.click(x, y, button=button, delay=delay, click_count=2)

    async def wheel(self, delta_x: float, delta_y: float) -> None:
        """Scroll with the wheel at the current position.

        Args:
            delta_x: Horizontal scroll delta in CSS pixels.
            delta_y: Vertical scroll delta in CSS pixels.
        """
        await self._session.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseWheel",
                "x": self._x,
                "y": self._y,
                "deltaX": delta_x,
                "deltaY": delta_y,
                "buttons": self._buttons,
                "pointerType": "mouse",
            },
        )

    def _next_click_count(self, button: Button, now: float) -> int:
        if self._last_press is None:
            return 1
        last_button, last_time, last_x, last_y = self._last_press
        half = DOUBLE_CLICK_SIZE / 2
        if (
            last_button == button
            and now - last_time <= DOUBLE_CLICK_TIME
            and abs(self._x - last_x) <= half
            and abs(self._y - last_y) <= half
        ):
            return self._click_count + 1
        return 1

    def _move_button(self) -> str:
        # Blink only starts drags and text selection when a move names the held
        # button, and Windows reports the left button first when several are down.
        for name in _MOVE_BUTTON_ORDER:
            if self._buttons & _BUTTON_BITS[name]:
                return name
        return "none"

    async def _dispatch(self, kind: str, **extra: object) -> None:
        await self._session.send(
            "Input.dispatchMouseEvent",
            {
                "type": kind,
                "x": self._x,
                "y": self._y,
                "buttons": self._buttons,
                # Blink derives pressure from force whenever buttons != 0; force 0
                # outside a press keeps pressure 0 as for a real mouse.
                "force": PRESSED_FORCE if self._buttons else 0.0,
                "pointerType": "mouse",
                **extra,
            },
        )


class Keyboard:
    """A virtual US-layout keyboard dispatching trusted key events.

    Args:
        session: The page session to dispatch events on.
    """

    def __init__(self, session: CDPSession) -> None:
        self._session = session

    async def down(self, key: str, *, modifiers: int = 0) -> None:
        """Press a key without releasing it.

        Printing keys send ``keyDown`` with ``text`` (which also produces the
        ``keypress``/``input``); others send ``rawKeyDown``.

        Args:
            key: Key name or single character, see `key_definition`.
            modifiers: CDP modifier bitmask (`ALT`, `CONTROL`,
                `META`, `SHIFT`) reported on the event.

        Raises:
            ValueError: If the key is not on a US keyboard.
        """
        definition = _require(key)
        await self._key_event("keyDown" if definition.text else "rawKeyDown", definition, modifiers)

    async def up(self, key: str, *, modifiers: int = 0) -> None:
        """Release a key.

        Args:
            key: Key name or single character, see `key_definition`.
            modifiers: CDP modifier bitmask reported on the event.

        Raises:
            ValueError: If the key is not on a US keyboard.
        """
        await self._key_event("keyUp", _require(key), modifiers)

    async def press(self, key: str, *, delay: float = 0.0) -> None:
        """Press and release a key, holding Shift around it when the key needs it.

        Args:
            key: Key name or single character, see `key_definition`.
            delay: Seconds to hold the key down.

        Raises:
            ValueError: If the key is not on a US keyboard.
        """
        definition = _require(key)
        modifiers = SHIFT if definition.shift else 0
        if definition.shift:
            await self._key_event("rawKeyDown", _NAMED["Shift"], SHIFT)
        await self._key_event("keyDown" if definition.text else "rawKeyDown", definition, modifiers)
        if delay:
            await asyncio.sleep(delay)
        await self._key_event("keyUp", definition, modifiers)
        if definition.shift:
            await self._key_event("keyUp", _NAMED["Shift"], 0)

    async def type(self, text: str, *, delay: float = 0.0) -> None:
        """Type text character by character.

        US-layout characters are typed as real key presses. Others (for example
        Georgian or emoji) are inserted as composed text, which is how an IME
        delivers them, rather than being forced onto a wrong physical key.

        Args:
            text: Text to type.
            delay: Seconds to wait after each character.
        """
        for char in text:
            if key_definition(char) is not None:
                await self.press(char)
            else:
                await self.insert_text(char)
            if delay:
                await asyncio.sleep(delay)

    async def insert_text(self, text: str) -> None:
        """Insert text as one composition, without key events.

        Args:
            text: Text to insert into the focused element.
        """
        await self._session.send("Input.insertText", {"text": text})

    async def _key_event(self, kind: str, definition: KeyDefinition, modifiers: int) -> None:
        params: dict[str, object] = {
            "type": kind,
            "key": definition.key,
            "code": definition.code,
            "windowsVirtualKeyCode": definition.key_code,
            "nativeVirtualKeyCode": definition.key_code,
            "modifiers": modifiers,
            "location": definition.location,
        }
        if kind == "keyDown" and definition.text:
            params["text"] = definition.text
            # CDP defines unmodifiedText as the text without modifiers *except Shift*,
            # and Chromium on Windows fills it with the same character as text.
            params["unmodifiedText"] = definition.text
        await self._session.send("Input.dispatchKeyEvent", params)


def _require(key: str) -> KeyDefinition:
    definition = key_definition(key)
    if definition is None:
        raise ValueError(f"Unknown key {key!r}")
    return definition
