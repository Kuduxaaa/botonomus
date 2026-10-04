import pytest

from botonomus import HumanPage as TopLevelHumanPage
from botonomus.human import Human, HumanConfig, HumanLocator, HumanPage

from .fakes import FakeLocator, FakePage, VirtualClock


class SpyHuman(Human):
    def __init__(self, page):
        super().__init__(page, config=HumanConfig(mistype_rate=0.0), clock=VirtualClock())
        self.calls = []

    async def click(self, target, *, button="left"):
        self.calls.append(("click", target, button))

    async def hover(self, target):
        self.calls.append(("hover", target))

    async def fill(self, target, value, *, sensitive=False):
        self.calls.append(("fill", target, value, sensitive))

    async def type(self, text, target=None, *, sensitive=False):
        self.calls.append(("type", target, text, sensitive))

    async def press(self, key):
        self.calls.append(("press", key))

    async def scroll(self, delta_y, *, delta_x=0.0):
        self.calls.append(("scroll", delta_y, delta_x))


class RolePage(FakePage):
    def get_by_role(self, role, *, name=None):
        return self.locators[f"role={role}"]


@pytest.fixture
def setup():
    page = RolePage()
    field = page.add("#f", FakeLocator(page))
    page.add("role=button", FakeLocator(page))
    human = SpyHuman(page)
    return HumanPage(page, human=human), page, human, field


async def test_page_methods_route_through_human(setup):
    wrapped, page, human, field = setup
    await wrapped.click("#f", button="right")
    await wrapped.hover("#f")
    await wrapped.fill("#f", "secret", sensitive=True)
    await wrapped.type("#f", "abc")
    await wrapped.press("Enter")
    await wrapped.scroll(300, delta_x=10)
    assert human.calls == [
        ("click", field, "right"),
        ("hover", field),
        ("fill", field, "secret", True),
        ("type", field, "abc", False),
        ("press", "Enter"),
        ("scroll", 300, 10),
    ]


async def test_locator_methods_route_through_human(setup):
    wrapped, page, human, field = setup
    locator = wrapped.locator("#f")
    assert isinstance(locator, HumanLocator) and locator.raw is field
    await locator.click()
    await locator.hover()
    await locator.fill("v")
    await locator.type("t", sensitive=True)
    await locator.press("Tab")
    assert human.calls == [
        ("click", field, "left"),
        ("hover", field),
        ("fill", field, "v", False),
        ("type", field, "t", True),
        ("press", "Tab"),
    ]
    assert "focus" in field.calls  # press focuses without clicking.
    assert isinstance(locator.first, HumanLocator)
    assert isinstance(locator.nth(2), HumanLocator) and ("nth", 2) in field.calls
    assert await locator.input_value() == ""  # Not humanized: delegated.


async def test_playwright_style_press_with_selector(setup):
    wrapped, page, human, field = setup
    await wrapped.press("#f", "Enter")
    assert human.calls == [("press", "Enter")]
    assert "focus" in field.calls


async def test_other_attributes_delegate_and_raw_bypasses(setup):
    wrapped, page, human, _ = setup
    assert wrapped.raw is page and wrapped.human is human
    assert await wrapped.title() == "Fake"
    assert wrapped.mouse is page.mouse
    role = wrapped.get_by_role("button", name="Go")
    assert isinstance(role, HumanLocator) and role.raw is page.locators["role=button"]
    with pytest.raises(AttributeError):
        wrapped.no_such_method  # noqa: B018
    with pytest.raises(AttributeError):
        wrapped._private  # noqa: B018
    assert TopLevelHumanPage is HumanPage
    assert "HumanPage" in repr(wrapped) and "HumanLocator" in repr(wrapped.locator("#f"))


async def test_real_human_fill_types_keystrokes(setup):
    _, page, _, field = setup
    field.value = "old"
    page.keyboard.buffer = list("old")
    wrapped = HumanPage(page, config=HumanConfig(mistype_rate=0.0, idle_moves=False), seed=1)
    wrapped.human.clock = VirtualClock()
    await wrapped.fill("#f", "fresh")
    assert page.keyboard.text == "fresh"
    assert [e for e in page.keyboard.events if e[0] == "type"] == [("type", c) for c in "fresh"]


def test_human_and_config_are_exclusive():
    page = FakePage()
    with pytest.raises(ValueError):
        HumanPage(page, human=Human(page), seed=1)
