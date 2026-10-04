"""Scripted pages and a session opener for detection runs without a browser."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from botonomus.config import BrowserConfig


class FakeLocator:
    def __init__(self, text):
        self.text = text

    async def inner_text(self):
        return self.text


class FakePage:
    def __init__(self, result=None, *, goto_error=None, eval_error=None, text="Hello   page"):
        self.result = result
        self.goto_error = goto_error
        self.eval_error = eval_error
        self.text = text
        self.visited = []
        self.evaluated = []

    async def goto(self, url, wait_until="load"):
        self.visited.append((url, wait_until))
        if self.goto_error is not None:
            raise self.goto_error

    async def evaluate(self, expression):
        self.evaluated.append(expression)
        if self.eval_error is not None:
            raise self.eval_error
        return self.result

    async def screenshot(self, *, path=None, full_page=False):
        await asyncio.to_thread(Path(path).write_bytes, b"\x89PNG")
        return b"\x89PNG"

    def locator(self, selector):
        return FakeLocator(self.text)


class FakeOpener:
    """Opens fake sessions; ``factory(profile)`` returns the page for that profile."""

    def __init__(self, root, factory, **config):
        self.config = BrowserConfig(profile_root=root, **config)
        self.factory = factory
        self.opened = []
        self.existed = []

    @asynccontextmanager
    async def open(self, *, profile):
        path = self.config.profile_root / profile
        self.existed.append(path.exists())
        path.mkdir(parents=True, exist_ok=True)
        self.opened.append(profile)
        yield SimpleNamespace(page=self.factory(profile), profile=profile)
