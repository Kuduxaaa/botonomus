import math
import random
import statistics

import pytest

from botonomus.human import HumanConfig, KeystrokeRhythm, bounded_lognormal, key_delays


def _skewness(values):
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values)
    return statistics.fmean([((v - mean) / sd) ** 3 for v in values])


def test_keystroke_gaps_are_right_skewed_not_uniform():
    text = "the rain in spain stays mainly in the plain " * 40
    gaps = key_delays(text, HumanConfig(), random.Random(5))
    letters = [g for g, c in zip(gaps, text, strict=True) if c.isalpha()]
    cv = statistics.pstdev(letters) / statistics.fmean(letters)
    assert 0.25 < cv < 1.0
    # A uniform distribution has zero skew and mean == median; human gaps do not.
    assert _skewness(letters) > 0.5
    assert statistics.fmean(letters) > statistics.median(letters)


def test_common_bigrams_are_faster_than_rare_pairs():
    config = HumanConfig(key_drift=0.0, hesitation_rate=0.0)
    rhythm = KeystrokeRhythm(config, random.Random(1))
    common = [rhythm.gap("t", "h") for _ in range(3000)]
    rare = [rhythm.gap("q", "z") for _ in range(3000)]
    ratio = statistics.median(common) / statistics.median(rare)
    assert ratio == pytest.approx(config.digraph_speedup, rel=0.1)


def test_word_and_punctuation_boundaries_pause_longer():
    config = HumanConfig(key_drift=0.0, hesitation_rate=0.0)
    rhythm = KeystrokeRhythm(config, random.Random(2))
    letter = statistics.median(rhythm.gap("k", "x") for _ in range(2000))
    space = statistics.median(rhythm.gap(" ", "x") for _ in range(2000))
    period = statistics.median(rhythm.gap(".", "x") for _ in range(2000))
    assert letter < space < period


def test_sessions_drift_in_tempo():
    config = HumanConfig(hesitation_rate=0.0, key_drift=0.2)
    medians = []
    for seed in range(30):
        rhythm = KeystrokeRhythm(config, random.Random(seed))
        medians.append(statistics.median(rhythm.gap("k", "x") for _ in range(300)))
    spread = statistics.pstdev(math.log(m) for m in medians)
    assert 0.08 < spread < 0.5


def test_gaps_are_capped():
    config = HumanConfig(max_key_gap=0.3, key_delay=0.25, hesitation_rate=1.0)
    assert all(g <= 0.3 for g in key_delays("hello, world." * 20, config, random.Random(0)))


def test_bounded_lognormal_stays_in_range_and_is_skewed():
    rng = random.Random(9)
    draws = [bounded_lognormal(rng, 0.05, 0.5) for _ in range(5000)]
    assert min(draws) >= 0.05 and max(draws) <= 0.5
    assert statistics.median(draws) == pytest.approx(math.sqrt(0.05 * 0.5), rel=0.1)
    assert _skewness(draws) > 0.3
    assert bounded_lognormal(rng, 0.2, 0.2) == 0.2
