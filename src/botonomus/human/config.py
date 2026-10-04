"""Behaviour parameters for human-like input, with named presets."""

import dataclasses
from dataclasses import dataclass
from typing import Any, Literal

from .keyboard import KeyboardLayout, get_layout

PresetName = Literal["default", "careful", "fast"]


@dataclass(frozen=True, slots=True)
class HumanConfig:
    """Timing and behaviour parameters for [`Human`][botonomus.human.Human].

    Every random interval is drawn from a right-skewed (lognormal) distribution;
    two-value ranges below give its typical span, not flat uniform bounds. Defaults
    are moderate and not tuned to any particular detector.

    Use `preset` for a named starting point and `replace` to adjust it.

    Attributes:
        move_speed: Pointer speed multiplier; values above 1 are faster.
        key_delay: Median seconds between keystrokes.
        key_jitter: Lognormal sigma of keystroke gaps.
        key_drift: Sigma of the per-session typing tempo (one draw per
            [`Human`][botonomus.human.Human]) and of its slow drift within a session.
        digraph_speedup: Gap multiplier for common English letter pairs, which
            practised typists roll quickly.
        word_pause: Median gap multiplier after a space.
        punctuation_pause: Median gap multiplier after punctuation or a newline.
        hesitation_rate: Probability per keystroke of an extra short hesitation.
        max_key_gap: Upper bound on one keystroke gap, in seconds.
        mistype_rate: Probability per letter of hitting a neighbouring key first and
            correcting it. Ignored when typing with ``sensitive=True``.
        mistype_notice: Most extra characters typed before a slip is noticed.
        layout: Keyboard layout (or registered layout name) for choosing
            neighbouring keys; see `botonomus.human.keyboard`.
        click_hold: Typical range of seconds a button stays pressed.
        aim_pause: Typical range of seconds between arriving and pressing.
        think: Typical range of seconds paused before acting on a target.
        overshoot_probability: Chance that a long move overshoots and corrects.
        overshoot_min_distance: Moves shorter than this many CSS pixels never
            overshoot.
        idle_moves: Whether to make small pointer drifts between actions.
        idle_duration: Typical range of seconds of one idle drift.
        idle_amplitude: Typical drift radius in CSS pixels.
        actionability_timeout: Seconds to wait for a target to become visible,
            enabled and stable before acting.
    """

    move_speed: float = 1.0
    key_delay: float = 0.11
    key_jitter: float = 0.35
    key_drift: float = 0.12
    digraph_speedup: float = 0.72
    word_pause: float = 1.5
    punctuation_pause: float = 2.1
    hesitation_rate: float = 0.03
    max_key_gap: float = 1.5
    mistype_rate: float = 0.02
    mistype_notice: int = 2
    layout: KeyboardLayout | str = "us"
    click_hold: tuple[float, float] = (0.05, 0.14)
    aim_pause: tuple[float, float] = (0.04, 0.16)
    think: tuple[float, float] = (0.15, 0.45)
    overshoot_probability: float = 0.25
    overshoot_min_distance: float = 250.0
    idle_moves: bool = False
    idle_duration: tuple[float, float] = (0.2, 0.8)
    idle_amplitude: float = 3.0
    actionability_timeout: float = 10.0

    def __post_init__(self) -> None:
        if self.move_speed <= 0 or self.key_delay <= 0:
            raise ValueError("move_speed and key_delay must be positive")
        for name in ("mistype_rate", "overshoot_probability", "hesitation_rate"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.mistype_notice < 0:
            raise ValueError("mistype_notice must not be negative")
        for name in ("click_hold", "aim_pause", "think", "idle_duration"):
            low, high = getattr(self, name)
            if not 0 < low <= high:
                raise ValueError(f"{name} must be a (low, high) pair with 0 < low <= high")
        get_layout(self.layout)  # Fail at construction, not on the first keystroke.

    @property
    def keyboard_layout(self) -> KeyboardLayout:
        """The resolved [`KeyboardLayout`][botonomus.human.keyboard.KeyboardLayout]."""
        return get_layout(self.layout)

    @classmethod
    def preset(cls, name: PresetName = "default", **overrides: Any) -> "HumanConfig":
        """A named preset, optionally with some fields overridden.

        * ``default``: moderate speed, occasional slips, no idle drift.
        * ``careful``: slower pointer and typing, longer thinking, idle drift
          between actions, more overshoot on long moves.
        * ``fast``: quick, practised input with fewer slips and pauses.

        Args:
            name: Preset name.
            **overrides: Field values replacing the preset's.

        Returns:
            The configuration.

        Raises:
            ValueError: For an unknown preset or an invalid override.
        """
        try:
            base = _PRESETS[name]
        except KeyError:
            raise ValueError(f"Unknown preset {name!r}; use one of {sorted(_PRESETS)}") from None
        return dataclasses.replace(base, **overrides) if overrides else base

    def replace(self, **changes: Any) -> "HumanConfig":
        """A copy with ``changes`` applied (validated like a new instance)."""
        return dataclasses.replace(self, **changes)


_PRESETS: dict[str, HumanConfig] = {
    "default": HumanConfig(),
    "careful": HumanConfig(
        move_speed=0.75,
        key_delay=0.16,
        key_jitter=0.4,
        mistype_rate=0.025,
        click_hold=(0.07, 0.18),
        aim_pause=(0.08, 0.3),
        think=(0.35, 1.1),
        overshoot_probability=0.35,
        idle_moves=True,
        idle_duration=(0.3, 1.2),
    ),
    "fast": HumanConfig(
        move_speed=1.6,
        key_delay=0.075,
        key_jitter=0.3,
        word_pause=1.3,
        punctuation_pause=1.7,
        hesitation_rate=0.015,
        mistype_rate=0.01,
        mistype_notice=1,
        click_hold=(0.04, 0.1),
        aim_pause=(0.025, 0.09),
        think=(0.08, 0.25),
        overshoot_probability=0.15,
    ),
}

HumanProfile = HumanConfig
"""Deprecated alias of `HumanConfig`, kept for 0.1 code; use ``HumanConfig``."""
