"""Keystroke rhythm and right-skewed interval sampling.

Human inter-key intervals are right-skewed (roughly lognormal), depend on the letter
pair being typed, lengthen at word and sentence boundaries, and drift slowly over a
session. Flat ``uniform(a, b)`` delays have hard edges and zero skew, which is easy to
separate from human timing statistically, so nothing here uses them.
"""

import math
import random

from .config import HumanConfig, HumanProfile

__all__ = [
    "COMMON_BIGRAMS",
    "HumanProfile",
    "KeystrokeRhythm",
    "bounded_lognormal",
    "key_delays",
]

# The most frequent English letter pairs; typists roll these with overlapping
# keystrokes, so the gap between the two letters is shorter than average.
COMMON_BIGRAMS = frozenset(
    "th he in er an re on at en nd ti es or te of ed is it al ar st to nt ng se ha as ou "
    "io le ve co me de hi ri ro ic ne ea ra ce li ch ll be ma si om ur".split()
)

_PUNCTUATION = frozenset(".,;:!?\n")


def bounded_lognormal(rng: random.Random, low: float, high: float) -> float:
    """A lognormal draw whose central ~95% falls in ``[low, high]``.

    The median is the geometric mean of the bounds, so the distribution is skewed
    to the right like human reaction times. Draws outside the range are clamped.

    Args:
        rng: Random source.
        low: Lower typical bound, greater than zero.
        high: Upper typical bound.

    Returns:
        Seconds (or any positive quantity) in ``[low, high]``.
    """
    if high <= low:
        return low
    mu = (math.log(low) + math.log(high)) / 2
    sigma = (math.log(high) - math.log(low)) / 4
    return min(high, max(low, rng.lognormvariate(mu, sigma)))


class KeystrokeRhythm:
    """Stateful keystroke-gap generator for one typing session.

    A tempo factor is drawn once per instance (some sessions type faster than
    others) and drifts slowly as a mean-reverting random walk between keystrokes.

    Args:
        config: Timing parameters.
        rng: Random source.
    """

    def __init__(self, config: HumanConfig, rng: random.Random) -> None:
        self._config = config
        self._rng = rng
        self._tempo = rng.lognormvariate(0.0, config.key_drift)
        self._walk = 0.0

    def gap(self, char: str, next_char: str | None = None) -> float:
        """Seconds to wait after typing ``char`` before the next keystroke.

        Args:
            char: The character just typed.
            next_char: The character about to be typed, if known.

        Returns:
            The gap in seconds, at most ``config.max_key_gap``.
        """
        config, rng = self._config, self._rng
        self._walk = 0.85 * self._walk + rng.gauss(0.0, config.key_drift * 0.25)
        median = config.key_delay * self._tempo * math.exp(self._walk)
        gap = rng.lognormvariate(math.log(median), config.key_jitter)
        if next_char is not None and (char + next_char).lower() in COMMON_BIGRAMS:
            gap *= config.digraph_speedup
        if char == " ":
            gap *= rng.lognormvariate(math.log(config.word_pause), 0.25)
        elif char in _PUNCTUATION:
            gap *= rng.lognormvariate(math.log(config.punctuation_pause), 0.3)
        if next_char is not None and next_char.isupper():
            gap *= 1.2  # Reaching for Shift.
        if rng.random() < config.hesitation_rate:
            gap += rng.lognormvariate(math.log(0.45), 0.4)
        return min(gap, config.max_key_gap)

    def correction_pause(self) -> float:
        """Seconds between making a slip and starting to correct it."""
        return min(self._rng.lognormvariate(math.log(0.32), 0.35), self._config.max_key_gap)

    def backspace_gap(self) -> float:
        """Seconds between repeated backspaces, which are faster than typing."""
        return self._rng.lognormvariate(math.log(self._config.key_delay * 0.7), 0.25)


def key_delays(text: str, profile: HumanConfig, rng: random.Random) -> list[float]:
    """Gaps after each character of ``text`` from a fresh `KeystrokeRhythm`.

    Args:
        text: Text to be typed.
        profile: Timing parameters.
        rng: Random source.

    Returns:
        One delay in seconds per character, each at most ``profile.max_key_gap``.
    """
    rhythm = KeystrokeRhythm(profile, rng)
    return [
        rhythm.gap(char, text[i + 1] if i + 1 < len(text) else None) for i, char in enumerate(text)
    ]
