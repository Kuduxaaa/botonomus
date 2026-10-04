"""In-memory page, input devices, locators and a virtual clock for human-input tests."""

import asyncio


class VirtualClock:
    """Time advances only when something sleeps, so tests run instantly."""

    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += max(0.0, seconds)
        await asyncio.sleep(0)


class FakeKeyboard:
    """Maintains the text of a single focused field, like a real input would."""

    def __init__(self):
        self.buffer = []
        self.events = []
        self.selected = False  # Whole field selected: next key replaces it.

    @property
    def text(self):
        return "".join(self.buffer)

    async def type(self, text):
        self.events.append(("type", text))
        if self.selected:
            self.buffer, self.selected = [], False
        self.buffer.extend(text)

    async def press(self, key):
        self.events.append(("press", key))
        if key == "Backspace" and self.selected:
            self.buffer, self.selected = [], False
        elif key == "Backspace" and self.buffer:
            self.buffer.pop()


class FakeMouse:
    def __init__(self, page):
        self.page = page
        self.moves = []
        self.events = []
        self.x = 0.0
        self.y = 0.0

    async def move(self, x, y):
        self.x, self.y = x, y
        self.moves.append((x, y))
        self.events.append(("move", x, y))

    async def down(self, button="left"):
        self.events.append(("down", button, self.x, self.y))

    async def up(self, button="left"):
        self.events.append(("up", button, self.x, self.y))
        await self.page.on_click(self.x, self.y)

    async def wheel(self, dx, dy):
        self.events.append(("wheel", dx, dy))
        self.page.scroll_y += dy


class FakeLocator:
    """A locator whose box is fixed in document coordinates and moves with scrolling.

    ``boxes`` (optional) is a list of viewport boxes returned in order, the last one
    repeating, to simulate animation.
    """

    def __init__(
        self,
        page,
        box=None,
        *,
        boxes=None,
        visible=True,
        enabled=True,
        value="",
        selectable=True,
        href=None,
    ):
        self.page = page
        self.doc_box = box or {"x": 100.0, "y": 100.0, "width": 120.0, "height": 30.0}
        self.boxes = list(boxes) if boxes else None
        self.visible = visible
        self.enabled = enabled
        self.value = value
        self.href = href
        self.calls = []
        self.box_calls = 0
        if selectable:
            self.select_text = self._select_text

    async def is_visible(self):
        return self.visible

    async def is_enabled(self):
        return self.enabled

    async def bounding_box(self):
        self.box_calls += 1
        if not self.visible:
            return None
        if self.boxes:
            return self.boxes.pop(0) if len(self.boxes) > 1 else dict(self.boxes[0])
        return {**self.doc_box, "y": self.doc_box["y"] - self.page.scroll_y}

    async def scroll_into_view_if_needed(self):
        self.calls.append("scroll_into_view")

    async def input_value(self):
        return self.value

    async def _select_text(self):
        self.calls.append("select_text")
        self.page.keyboard.buffer = list(self.value)
        self.page.keyboard.selected = True

    async def focus(self):
        self.calls.append("focus")

    def nth(self, index):
        self.calls.append(("nth", index))
        return self

    @property
    def first(self):
        return self


class FakePage:
    """A page with one viewport, scroll offset and registered locators."""

    def __init__(self, width=1280, height=800):
        self.viewport_size = {"width": width, "height": height}
        self.inner_size = (width, height)
        self.mouse = FakeMouse(self)
        self.keyboard = FakeKeyboard()
        self.scroll_y = 0.0
        self.locators = {}
        self.url = "about:blank"

    def add(self, selector, locator):
        self.locators[selector] = locator
        return locator

    def locator(self, selector):
        return self.locators[selector]

    async def evaluate(self, expression, arg=None):
        return {"width": self.inner_size[0], "height": self.inner_size[1]}

    async def on_click(self, x, y):
        pass

    async def title(self):
        return "Fake"
