"""Human-like pointer, keyboard and wheel input.

`Human` drives one page; `HumanPage` wraps a page so its ``click``,
``fill``, ``type``, ``press``, ``hover`` and ``scroll`` (and locator equivalents) are
humanized automatically. `HumanConfig` holds the behaviour parameters, with
presets ``default``, ``careful`` and ``fast``.
"""

from .actionability import Actionable, NotActionableError, wait_until_actionable
from .clock import Clock, RealClock
from .config import HumanConfig, HumanProfile
from .human import Human, MouseButton
from .keyboard import (
    DE_QWERTZ,
    FR_AZERTY,
    US_QWERTY,
    KeyboardLayout,
    Keystroke,
    available_layouts,
    get_layout,
    plan_keystrokes,
    register_layout,
)
from .page import HumanLocator, HumanPage
from .paths import bezier_path, eased_weights, movement_duration, target_point
from .timing import KeystrokeRhythm, bounded_lognormal, key_delays

__all__ = [
    "DE_QWERTZ",
    "FR_AZERTY",
    "US_QWERTY",
    "Actionable",
    "Clock",
    "Human",
    "HumanConfig",
    "HumanLocator",
    "HumanPage",
    "HumanProfile",
    "KeyboardLayout",
    "KeystrokeRhythm",
    "Keystroke",
    "MouseButton",
    "NotActionableError",
    "RealClock",
    "available_layouts",
    "bezier_path",
    "bounded_lognormal",
    "eased_weights",
    "get_layout",
    "key_delays",
    "movement_duration",
    "plan_keystrokes",
    "register_layout",
    "target_point",
    "wait_until_actionable",
]
