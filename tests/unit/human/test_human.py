import math
import statistics

import pytest

from botonomus.human import (
    Human,
    HumanConfig,
    HumanProfile,
    NotActionableError,
    wait_until_actionable,
)

from .fakes import FakeLocator, FakePage, VirtualClock

QUIET = HumanConfig(idle_moves=False, overshoot_probability=0.0, mistype_rate=0.0)


def make(config=QUIET, seed=1, page=None):
    page = page or FakePage()
    clock = VirtualClock()
    return Human(page, config=config, seed=seed, clock=clock), page, clock


def inside(point, box):
    x, y = point
    return box["x"] <= x <= box["x"] + box["width"] and box["y"] <= y <= box["y"] + box["height"]


async def test_typing_with_mistakes_always_yields_exact_text():
    text = "Hello there, General Kenobi.\nYou are a bold one!"
    corrected = 0
    for seed in range(60):
        human, page, _ = make(HumanConfig(mistype_rate=0.15), seed=seed)
        await human.type(text)
        assert page.keyboard.text == text
        corrected += any(e == ("press", "Backspace") for e in page.keyboard.events)
    assert corrected > 50


async def test_mistype_rate_is_approximately_honoured_through_human():
    rate = 0.05
    letters = mistakes = 0
    for seed in range(40):
        human, page, _ = make(HumanConfig(mistype_rate=rate, mistype_notice=0), seed=seed)
        text = "typing practice makes progress " * 10
        await human.type(text)
        letters += sum(c.isalpha() for c in text)
        # With notice delay 0 every slip is exactly one Backspace.
        mistakes += page.keyboard.events.count(("press", "Backspace"))
        assert page.keyboard.text == text
    assert abs(mistakes / letters - rate) < 0.012


async def test_sensitive_typing_never_mistypes():
    human, page, _ = make(HumanConfig(mistype_rate=1.0))
    await human.type("hunter2-Secret", sensitive=True)
    assert page.keyboard.text == "hunter2-Secret"
    assert all(kind == "type" for kind, _ in page.keyboard.events)


async def test_keystroke_gaps_recorded_on_clock_are_not_uniform():
    human, _, clock = make()
    await human.type("a fairly ordinary sentence to type " * 8)
    gaps = clock.sleeps
    cv = statistics.pstdev(gaps) / statistics.fmean(gaps)
    assert 0.3 < cv < 1.5
    assert statistics.fmean(gaps) > statistics.median(gaps)


async def test_click_waits_for_stable_box_then_lands_inside():
    page = FakePage()
    final = {"x": 300.0, "y": 200.0, "width": 80.0, "height": 24.0}
    moving = [{**final, "x": 300.0 - 40 * i} for i in range(4, 0, -1)] + [final]
    page.add("#go", FakeLocator(page, boxes=moving))
    human, page, _ = make(page=page)
    await human.click(page.locator("#go"))
    kinds = [e[0] for e in page.mouse.events]
    assert kinds[-2:] == ["down", "up"]
    assert inside(page.mouse.events[-1][2:], final)
    assert human.position == page.mouse.moves[-1]


async def test_invisible_element_raises_not_actionable_value_error():
    human, page, clock = make()
    page.add("#ghost", FakeLocator(page, visible=False))
    with pytest.raises(NotActionableError, match="visible") as info:
        await human.click(page.locator("#ghost"))
    assert isinstance(info.value, ValueError)
    assert clock.now() >= QUIET.actionability_timeout
    assert page.mouse.events == []


async def test_disabled_element_is_waited_on():
    page = FakePage()
    locator = FakeLocator(page, enabled=False)
    clock = VirtualClock()
    with pytest.raises(NotActionableError, match="enabled"):
        await wait_until_actionable(locator, timeout=1.0, clock=clock)
    box = await wait_until_actionable(locator, timeout=1.0, clock=clock, require_enabled=False)
    assert box["width"] == 120.0


async def test_locator_without_is_enabled_is_still_actionable():
    class Minimal:
        async def is_visible(self):
            return True

        async def bounding_box(self):
            return {"x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0}

    assert await wait_until_actionable(Minimal(), clock=VirtualClock()) == {
        "x": 1.0,
        "y": 2.0,
        "width": 3.0,
        "height": 4.0,
    }


async def test_offscreen_element_is_reached_by_wheel_scrolling():
    page = FakePage(height=800)
    target = page.add(
        "#far", FakeLocator(page, {"x": 50.0, "y": 2400.0, "width": 90, "height": 20})
    )
    human, page, _ = make(page=page)
    await human.click(target)
    assert any(e[0] == "wheel" for e in page.mouse.events)
    assert "scroll_into_view" not in target.calls
    assert 0 <= page.mouse.events[-1][3] <= 800


async def test_long_moves_can_overshoot_and_correct():
    config = QUIET.replace(overshoot_probability=1.0, overshoot_min_distance=200)
    human, page, _ = make(config)
    human._position = (100.0, 400.0)
    await human.move_to(900.0, 400.0)
    assert max(x for x, _ in page.mouse.moves) > 900.0 + 5
    assert page.mouse.moves[-1] == (900.0, 400.0)
    page.mouse.moves.clear()
    await human.move_to(980.0, 400.0)  # Short: never overshoots.
    assert max(x for x, _ in page.mouse.moves) <= 980.0 + 1e-9


async def test_moves_without_overshoot_stay_on_the_near_side():
    human, page, _ = make()
    human._position = (100.0, 400.0)
    await human.move_to(900.0, 400.0)
    assert max(x for x, _ in page.mouse.moves) <= 900.0 + 1e-9


async def test_idle_drift_is_small_and_time_bounded():
    config = QUIET.replace(idle_amplitude=3.0)
    human, page, clock = make(config)
    await human.idle(1.0)
    assert page.mouse.moves == []  # No known position yet.
    human._position = (400.0, 300.0)
    start = clock.now()
    await human.idle(1.5)
    assert clock.now() - start <= 1.5 + 1e-9
    assert page.mouse.moves
    assert all(math.dist(p, (400.0, 300.0)) <= 9 * math.sqrt(2) + 1e-6 for p in page.mouse.moves)


async def test_careful_preset_idles_between_actions():
    page = FakePage()
    page.add("#a", FakeLocator(page))
    human, page, clock = make(HumanConfig.preset("careful", overshoot_probability=0), page=page)
    await human.click(page.locator("#a"))
    moves_after_first = len(page.mouse.moves)
    await human.click(page.locator("#a"))
    assert len(page.mouse.moves) > moves_after_first


async def test_fill_selects_clears_and_types():
    page = FakePage()
    page.add("#name", FakeLocator(page, value="old value"))
    human, page, _ = make(page=page)
    page.keyboard.buffer = list("old value")
    await human.fill(page.locator("#name"), "new")
    assert page.keyboard.text == "new"
    assert "select_text" in page.locator("#name").calls


async def test_fill_without_select_text_uses_end_and_backspaces():
    page = FakePage()
    page.add("#name", FakeLocator(page, value="abc", selectable=False))
    human, page, _ = make(page=page)
    page.keyboard.buffer = list("abc")
    await human.fill(page.locator("#name"), "xy")
    assert page.keyboard.text == "xy"
    assert page.keyboard.events[0] == ("press", "End")


async def test_fill_on_empty_field_just_types():
    page = FakePage()
    page.add("#name", FakeLocator(page, value=""))
    human, page, _ = make(page=page)
    await human.fill(page.locator("#name"), "v")
    assert page.keyboard.events == [("type", "v")]


async def test_type_with_target_clicks_first_and_press_and_scroll():
    page = FakePage()
    page.add("#q", FakeLocator(page))
    human, page, _ = make(page=page)
    await human.type("hi", page.locator("#q"))
    assert [e[0] for e in page.mouse.events][-2:] == ["down", "up"]
    assert page.keyboard.text == "hi"
    await human.press("Enter")
    assert page.keyboard.events[-1] == ("press", "Enter")
    await human.scroll(500)
    assert sum(e[2] for e in page.mouse.events if e[0] == "wheel") == pytest.approx(500)


async def test_backwards_compatible_constructor_and_pause():
    clock = VirtualClock()
    human = Human(FakePage(), profile=HumanProfile(think=(0.2, 0.3)), seed=3, clock=clock)
    assert human.profile is human.config
    for _ in range(50):
        await human.pause()
        await human.pause(0.01, 0.02)
    assert all(0.01 <= s <= 0.3 for s in clock.sleeps)
    with pytest.raises(ValueError):
        Human(FakePage(), config=HumanConfig(), profile=HumanConfig())
    with pytest.raises(ValueError):
        await human.click((1.0, 1.0), button="thumb")


async def test_click_on_point_and_seeded_reproducibility():
    runs = []
    for _ in range(2):
        human, page, _ = make(HumanConfig(), seed=11)
        await human.click((640.0, 360.0))
        runs.append(page.mouse.events)
    assert runs[0] == runs[1]
    assert runs[0][-1][2:] == (640.0, 360.0)


async def test_viewport_falls_back_to_evaluate_for_native_pages():
    page = FakePage()
    page.viewport_size = None
    human, page, _ = make(page=page)
    await human.scroll(100)
    x, y = page.mouse.moves[-1]
    assert 0 < x < 1280 and 0 < y < 800
