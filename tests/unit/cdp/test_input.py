import pytest

from botonomus.cdp.input import (
    DOUBLE_CLICK_TIME,
    KEY_LOCATION_LEFT,
    KEY_LOCATION_RIGHT,
    PRESSED_FORCE,
    SHIFT,
    Keyboard,
    Mouse,
    key_definition,
)


class FakeSession:
    """Captures every command instead of talking to a browser."""

    def __init__(self):
        self.sent = []

    async def send(self, method, params=None, timeout=60.0):
        self.sent.append((method, params or {}))
        return {}

    def mouse_events(self):
        return [p for m, p in self.sent if m == "Input.dispatchMouseEvent"]

    def key_events(self):
        return [p for m, p in self.sent if m == "Input.dispatchKeyEvent"]


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def mouse(session, clock):
    return Mouse(session, clock=clock)


def _summary(events):
    return [(e["type"], e["button"], e["buttons"], e["force"], e["clickCount"]) for e in events]


async def test_click_moves_first_and_sends_pressure_buttons_and_detail(mouse, session):
    await mouse.click(10, 20)
    events = session.mouse_events()
    assert _summary(events) == [
        ("mouseMoved", "none", 0, 0.0, 0),
        ("mousePressed", "left", 1, PRESSED_FORCE, 1),
        ("mouseReleased", "left", 0, 0.0, 1),
    ]
    assert all((e["x"], e["y"], e["pointerType"]) == (10, 20, "mouse") for e in events)
    # Mouse-appropriate defaults are left to Chromium (all zero).
    assert not {"tangentialPressure", "tiltX", "tiltY", "twist"} & set(events[1])


async def test_drag_moves_carry_button_mask_and_force(mouse, session):
    await mouse.move(0, 0)
    await mouse.down()
    await mouse.move(30, 0, steps=3)
    await mouse.up()
    moves = [e for e in session.mouse_events() if e["type"] == "mouseMoved"][1:]
    assert [e["x"] for e in moves] == [10, 20, 30]
    assert all(e["buttons"] == 1 and e["button"] == "left" for e in moves)
    assert all(e["force"] == PRESSED_FORCE for e in moves)
    assert mouse.buttons == 0


async def test_multiple_buttons_keep_mask_and_pressure_until_last_release(mouse, session):
    await mouse.down(button="right")
    await mouse.down(button="left")
    await mouse.move(5, 5)
    await mouse.up(button="left")
    await mouse.up(button="right")
    assert _summary(session.mouse_events()) == [
        ("mousePressed", "right", 2, PRESSED_FORCE, 1),
        ("mousePressed", "left", 3, PRESSED_FORCE, 1),
        ("mouseMoved", "left", 3, PRESSED_FORCE, 0),
        ("mouseReleased", "left", 2, PRESSED_FORCE, 1),
        ("mouseReleased", "right", 0, 0.0, 1),
    ]


async def test_quick_second_click_at_same_spot_is_a_double_click(mouse, session, clock):
    await mouse.click(50, 50)
    clock.now += 0.2
    await mouse.click(51, 49)
    clock.now += 0.2
    await mouse.click(51, 49)
    presses = [e["clickCount"] for e in session.mouse_events() if e["type"] != "mouseMoved"]
    assert presses == [1, 1, 2, 2, 3, 3]


@pytest.mark.parametrize(
    ("dt", "dx", "button"),
    [
        (DOUBLE_CLICK_TIME + 0.01, 0, "left"),  # too slow
        (0.1, 3, "left"),  # outside the 4 px rectangle
        (0.1, 0, "right"),  # different button
    ],
)
async def test_click_sequence_resets(mouse, session, clock, dt, dx, button):
    await mouse.click(50, 50)
    clock.now += dt
    await mouse.click(50 + dx, 50, button=button)
    presses = [e for e in session.mouse_events() if e["type"] == "mousePressed"]
    assert [e["clickCount"] for e in presses] == [1, 1]


async def test_dblclick_sequence(mouse, session):
    await mouse.dblclick(7, 8, interval=0)
    assert _summary(session.mouse_events()) == [
        ("mouseMoved", "none", 0, 0.0, 0),
        ("mousePressed", "left", 1, PRESSED_FORCE, 1),
        ("mouseReleased", "left", 0, 0.0, 1),
        ("mouseMoved", "none", 0, 0.0, 0),
        ("mousePressed", "left", 1, PRESSED_FORCE, 2),
        ("mouseReleased", "left", 0, 0.0, 2),
    ]


async def test_dblclick_rejects_timing_outside_double_click_time(mouse):
    with pytest.raises(ValueError):
        await mouse.dblclick(0, 0, delay=0.2, interval=0.2)


async def test_explicit_click_count_is_respected(mouse, session):
    await mouse.down(click_count=3)
    await mouse.up()
    assert [e["clickCount"] for e in session.mouse_events()] == [3, 3]


async def test_wheel_reports_position_and_buttons(mouse, session):
    await mouse.move(4, 5)
    await mouse.wheel(0, 100)
    wheel = session.mouse_events()[-1]
    assert (wheel["type"], wheel["x"], wheel["y"], wheel["deltaY"]) == ("mouseWheel", 4, 5, 100)
    assert wheel["buttons"] == 0
    assert mouse.position == (4, 5)


async def test_press_shifted_punctuation_wraps_in_shift(session):
    await Keyboard(session).press("?")
    events = session.key_events()
    assert [(e["type"], e["key"], e["modifiers"]) for e in events] == [
        ("rawKeyDown", "Shift", SHIFT),
        ("keyDown", "?", SHIFT),
        ("keyUp", "?", SHIFT),
        ("keyUp", "Shift", 0),
    ]
    down = events[1]
    assert (down["code"], down["windowsVirtualKeyCode"]) == ("Slash", 191)
    # unmodifiedText excludes every modifier except Shift, so it equals text.
    assert down["text"] == down["unmodifiedText"] == "?"
    assert events[0]["location"] == KEY_LOCATION_LEFT
    assert down["location"] == 0


async def test_uppercase_unmodified_text_keeps_shift(session):
    await Keyboard(session).press("A")
    down = session.key_events()[1]
    assert (down["text"], down["unmodifiedText"], down["code"]) == ("A", "A", "KeyA")


async def test_non_printing_keys_use_raw_key_down_without_text(session):
    await Keyboard(session).press("ArrowLeft")
    down, up = session.key_events()
    assert down["type"] == "rawKeyDown" and "text" not in down
    assert up["type"] == "keyUp"


async def test_type_inserts_unmapped_characters_as_text(session):
    await Keyboard(session).type("aქ")
    methods = [m for m, _ in session.sent]
    assert methods == [
        "Input.dispatchKeyEvent",
        "Input.dispatchKeyEvent",
        "Input.insertText",
    ]
    assert session.sent[-1][1] == {"text": "ქ"}


async def test_keyboard_down_up_validate_keys(session):
    keyboard = Keyboard(session)
    await keyboard.down("ShiftRight", modifiers=SHIFT)
    await keyboard.up("ShiftRight")
    down, up = session.key_events()
    assert (down["key"], down["code"], down["location"]) == ("Shift", "ShiftRight", 2)
    assert up["location"] == KEY_LOCATION_RIGHT
    with pytest.raises(ValueError):
        await keyboard.down("Nope")
    with pytest.raises(ValueError):
        await keyboard.up("Nope")


@pytest.mark.parametrize(
    ("key", "code", "location"),
    [
        ("Control", "ControlLeft", 1),
        ("ControlRight", "ControlRight", 2),
        ("AltRight", "AltRight", 2),
        ("MetaRight", "MetaRight", 2),
        ("Enter", "Enter", 0),
    ],
)
def test_modifier_locations(key, code, location):
    definition = key_definition(key)
    assert definition is not None
    assert (definition.code, definition.location) == (code, location)
