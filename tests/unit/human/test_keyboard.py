import random

import pytest

from botonomus.human import (
    DE_QWERTZ,
    FR_AZERTY,
    US_QWERTY,
    KeyboardLayout,
    available_layouts,
    get_layout,
    plan_keystrokes,
    register_layout,
)
from botonomus.human.keyboard import replay

SAMPLE = "The quick brown fox jumps over the lazy dog.\nSecond line, with Tabs\tand MORE text!"


def test_us_neighbours_follow_physical_rows():
    assert set(US_QWERTY.neighbours("g")) == set("tyfhvb")
    assert set(US_QWERTY.neighbours("a")) == set("qwsz")
    assert set(US_QWERTY.neighbours("m")) == set("jkn")


def test_neighbours_keep_case_and_skip_non_letters():
    assert set(US_QWERTY.neighbours("G")) == set("TYFHVB")
    assert US_QWERTY.neighbours("1") == ""
    assert US_QWERTY.neighbours("é") == ""
    assert "1" not in US_QWERTY.neighbours("q")


def test_german_and_french_layouts_differ_from_us():
    assert "y" in DE_QWERTZ.neighbours("a") and "z" not in DE_QWERTZ.neighbours("a")
    assert set(DE_QWERTZ.neighbours("ü")) == set("pöä")
    assert set(FR_AZERTY.neighbours("a")) == set("zq")
    assert "m" in FR_AZERTY.neighbours("l")
    assert "w" in FR_AZERTY.neighbours("q")


def test_registry_resolves_names_and_accepts_custom_layouts():
    assert {"us", "de", "fr"} <= set(available_layouts())
    assert get_layout("de") is DE_QWERTZ
    assert get_layout(US_QWERTY) is US_QWERTY
    dvorak = KeyboardLayout(
        "dvorak-test",
        ("1234567890[]", "',.pyfgcrl/=", "aoeuidhtns-", ";qjkxbmwvz"),
        (0.0, 0.5, 0.75, 1.25),
    )
    register_layout(dvorak)
    assert get_layout("dvorak-test").neighbours("u") != US_QWERTY.neighbours("u")
    with pytest.raises(ValueError, match="Unknown keyboard layout"):
        get_layout("klingon")
    with pytest.raises(ValueError):
        KeyboardLayout("bad", ("abc",), (0.0, 1.0))


@pytest.mark.parametrize("layout", [US_QWERTY, DE_QWERTZ, FR_AZERTY])
def test_plans_always_replay_to_the_exact_text(layout):
    for seed in range(300):
        plan = plan_keystrokes(SAMPLE, layout, 0.3, random.Random(seed))
        assert replay(plan) == SAMPLE
        assert replay(plan, "prefix ") == "prefix " + SAMPLE


def test_zero_rate_plans_no_mistakes():
    plan = plan_keystrokes(SAMPLE, US_QWERTY, 0.0, random.Random(0))
    assert [s.text for s in plan] == list(SAMPLE)
    assert not any(s.mistake or s.kind == "backspace" for s in plan)


def test_slip_is_noticed_within_notice_delay_and_never_across_commit_keys():
    delays = set()
    for seed in range(200):
        plan = plan_keystrokes(SAMPLE, US_QWERTY, 0.25, random.Random(seed))
        for i, stroke in enumerate(plan):
            if not stroke.mistake:
                continue
            after = 0
            while plan[i + 1 + after].kind == "char":
                assert plan[i + 1 + after].text not in "\n\r\t"
                after += 1
            backspaces = 0
            while plan[i + 1 + after + backspaces].kind == "backspace":
                backspaces += 1
            assert backspaces == after + 1
            assert plan[i + 1 + after + backspaces].correction
            delays.add(after)
    assert delays == {0, 1, 2}


def test_mistype_rate_is_honoured_over_many_letters():
    rng = random.Random(42)
    text = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(20000))
    for rate in (0.02, 0.05, 0.1):
        plan = plan_keystrokes(text, US_QWERTY, rate, random.Random(7))
        observed = sum(s.mistake for s in plan) / len(text)
        assert abs(observed - rate) < 4 * (rate * (1 - rate) / len(text)) ** 0.5
        assert replay(plan) == text


def test_mistakes_are_neighbouring_keys():
    plan = plan_keystrokes("asdfghjkl" * 50, US_QWERTY, 0.5, random.Random(3))
    for i, stroke in enumerate(plan):
        if stroke.mistake:
            intended = next(s.text for s in plan[i:] if s.correction and s.kind == "char")
            assert stroke.text in US_QWERTY.neighbours(intended)
