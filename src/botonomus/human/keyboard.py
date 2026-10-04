"""Keyboard layouts and mistype-and-correct keystroke planning.

A `KeyboardLayout` describes the physical key rows of a layout so that a
plausible slip of the finger (a *neighbouring* key) can be chosen for any letter.
Layouts are pluggable: build one with `KeyboardLayout` and add it with
`register_layout`, then select it by name in
[`HumanConfig`][botonomus.human.HumanConfig].

US QWERTY (``"us"``), German QWERTZ (``"de"``) and French AZERTY (``"fr"``) ship
built in.
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

# Horizontal stagger of each row, in key widths, measured from the number row. These
# are the usual offsets of a staggered (non-ortholinear) keyboard.
_ANSI_OFFSETS = (0.0, 0.5, 0.75, 1.25)
_ISO_OFFSETS = (0.0, 0.5, 0.75, 0.25)

# Characters after which a mistake is never left pending: typing past them could
# submit a form or move focus before the slip is corrected.
_COMMIT_CHARS = frozenset("\n\r\t")


@dataclass(frozen=True, slots=True)
class KeyboardLayout:
    """Physical key rows of a keyboard layout, used to pick neighbouring keys.

    Rows are given unshifted, top (number row) to bottom. Two keys are neighbours
    when they sit next to each other on one row, or overlap horizontally on an
    adjacent row once the row offsets are applied.

    Attributes:
        name: Registry name, for example ``"us"``.
        rows: Unshifted characters of each key row, top to bottom.
        offsets: Horizontal offset of each row in key widths.
    """

    name: str
    rows: tuple[str, ...]
    offsets: tuple[float, ...]
    _neighbours: dict[str, str] = field(init=False, repr=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        if len(self.rows) != len(self.offsets):
            raise ValueError("rows and offsets must have the same length")
        positions: list[tuple[str, int, float]] = []
        for index, (keys, offset) in enumerate(zip(self.rows, self.offsets, strict=True)):
            positions.extend((char, index, offset + col) for col, char in enumerate(keys))
        table: dict[str, str] = {}
        for char, row, x in positions:
            near = [
                other
                for other, other_row, other_x in positions
                if other != char
                and (
                    (other_row == row and abs(other_x - x) == 1)
                    or (abs(other_row - row) == 1 and abs(other_x - x) < 1)
                )
            ]
            table[char] = "".join(near)
        object.__setattr__(self, "_neighbours", table)

    def neighbours(self, char: str) -> str:
        """Letters physically adjacent to ``char``, in the same case as ``char``.

        Only letters below the number row are returned: a slip onto a digit or
        symbol key (or an AZERTY number-row accent) is rarer and could change the
        meaning of structured input.

        Args:
            char: A single character.

        Returns:
            The neighbouring letters, or an empty string if ``char`` is not a letter
            on this layout.
        """
        if not char.isalpha():
            return ""
        near = self._neighbours.get(char.lower(), "")
        number_row = self.rows[0] if self.rows else ""
        letters = "".join(c for c in near if c.isalpha() and c not in number_row)
        return letters.upper() if char.isupper() else letters


US_QWERTY = KeyboardLayout(
    "us",
    ("1234567890-=", "qwertyuiop[]", "asdfghjkl;'", "zxcvbnm,./"),
    _ANSI_OFFSETS,
)
DE_QWERTZ = KeyboardLayout(
    "de",
    ("1234567890ß", "qwertzuiopü+", "asdfghjklöä#", "<yxcvbnm,.-"),
    _ISO_OFFSETS,
)
FR_AZERTY = KeyboardLayout(
    "fr",
    ("&é\"'(-è_çà)=", "azertyuiop^$", "qsdfghjklmù*", "<wxcvbn,;:!"),
    _ISO_OFFSETS,
)

_LAYOUTS: dict[str, KeyboardLayout] = {
    layout.name: layout for layout in (US_QWERTY, DE_QWERTZ, FR_AZERTY)
}


def register_layout(layout: KeyboardLayout) -> None:
    """Make ``layout`` available by name, replacing any layout of the same name.

    Args:
        layout: The layout to register.
    """
    _LAYOUTS[layout.name] = layout


def get_layout(layout: "KeyboardLayout | str") -> KeyboardLayout:
    """Resolve a layout name (or pass a layout through unchanged).

    Args:
        layout: A registered layout name or a `KeyboardLayout`.

    Returns:
        The layout.

    Raises:
        ValueError: If no layout of that name is registered.
    """
    if isinstance(layout, KeyboardLayout):
        return layout
    try:
        return _LAYOUTS[layout]
    except KeyError:
        raise ValueError(f"Unknown keyboard layout {layout!r}") from None


def available_layouts() -> list[str]:
    """Names of all registered layouts, sorted."""
    return sorted(_LAYOUTS)


KeystrokeKind = Literal["char", "backspace"]


@dataclass(frozen=True, slots=True)
class Keystroke:
    """One planned keyboard action.

    Attributes:
        kind: ``"char"`` types `text`; ``"backspace"`` deletes one character.
        text: The character typed, empty for a backspace.
        mistake: Whether this character is the wrong key of a slip.
        correction: Whether this keystroke is part of fixing a slip (the backspaces
            and the first one after them), which is typed after a noticing pause.
    """

    kind: KeystrokeKind
    text: str = ""
    mistake: bool = False
    correction: bool = False


def plan_keystrokes(
    text: str,
    layout: KeyboardLayout,
    mistype_rate: float,
    rng: random.Random,
    *,
    max_notice_delay: int = 2,
) -> list[Keystroke]:
    """Plan the keystrokes for ``text``, including occasional corrected slips.

    With probability ``mistype_rate`` per letter, a neighbouring key is typed
    instead. The slip is noticed after 0 to ``max_notice_delay`` further characters,
    deleted with the matching number of backspaces, and the intended characters are
    typed again. Replaying the plan on an empty field always yields exactly ``text``.

    A slip is never left pending across a newline, carriage return or tab, since
    those could submit a form or move focus first.

    Args:
        text: The text that must end up typed.
        layout: Layout used to choose the neighbouring key.
        mistype_rate: Probability of a slip per letter, in ``[0, 1]``.
        rng: Random source.
        max_notice_delay: Most extra characters typed before the slip is noticed.

    Returns:
        The keystrokes in order.
    """
    plan: list[Keystroke] = []
    for index, char in enumerate(text):
        near = layout.neighbours(char) if mistype_rate > 0 else ""
        if near and rng.random() < mistype_rate:
            plan.append(Keystroke("char", rng.choice(near), mistake=True))
            extra = _run_before_commit(text, index + 1, rng.randint(0, max_notice_delay))
            plan.extend(Keystroke("char", c) for c in extra)
            plan.extend(Keystroke("backspace", correction=True) for _ in range(len(extra) + 1))
            plan.append(Keystroke("char", char, correction=True))
            # The extra characters were deleted; the loop types them again next.
        else:
            plan.append(Keystroke("char", char))
    return plan


def replay(plan: Sequence[Keystroke], initial: str = "") -> str:
    """Apply ``plan`` to ``initial`` as a text field would (append and backspace).

    Args:
        plan: Keystrokes from `plan_keystrokes`.
        initial: Text already in the field.

    Returns:
        The resulting field text.
    """
    buffer = list(initial)
    for stroke in plan:
        if stroke.kind == "backspace":
            if buffer:
                buffer.pop()
        else:
            buffer.append(stroke.text)
    return "".join(buffer)


def _run_before_commit(text: str, start: int, count: int) -> str:
    run = text[start : start + count]
    for offset, char in enumerate(run):
        if char in _COMMIT_CHARS:
            return run[:offset]
    return run
