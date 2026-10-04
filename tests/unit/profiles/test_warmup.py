import asyncio
import logging

import pytest

from botonomus.human import Human, HumanConfig
from botonomus.profiles import DEFAULT_SITES, WarmupReport, warm_up
from botonomus.profiles import warmup as warmup_module
from tests.unit.human.fakes import FakeLocator, FakePage, VirtualClock

SITES = {
    "https://news.test/": ["/world", "/sport", "/tech"],
    "https://wiki.test/": ["/wiki/Cat", "/wiki/Dog"],
    "https://shop.test/": ["/deals", "/books"],
}


class BrowsingPage(FakePage):
    """Fake site graph: every page links to its site's links; clicks follow the hit link."""

    def __init__(self, *, failing=(), hanging=(), covered=False):
        super().__init__()
        self.failing = set(failing)
        self.hanging = set(hanging)
        self.covered = covered
        self.history = []
        self.gotos = []
        self.armed = None

    def _site(self):
        return next(s for s in SITES if self.url.startswith(s))

    async def goto(self, url):
        self.gotos.append(url)
        if url in self.hanging:
            await asyncio.Event().wait()
        if url in self.failing:
            raise OSError(f"DNS failure for {url}")
        self.url = url
        self.history.append(url)

    async def evaluate(self, expression, arg=None):
        if "querySelectorAll('a[href]')" in expression:
            site = self._site()
            links = [{"href": site.rstrip("/") + raw, "raw": raw} for raw in SITES[site]]
            return [link for link in links if link["href"] != self.url]
        if "elementFromPoint" in expression:
            self.armed = None if self.covered else arg["raw"]
            return not self.covered
        return await super().evaluate(expression, arg)

    def locator(self, selector):
        return FakeLocator(self, {"x": 200.0, "y": 300.0, "width": 140.0, "height": 18.0})

    async def on_click(self, x, y):
        if self.armed is not None:
            self.url = self._site().rstrip("/") + self.armed
            self.history.append(self.url)
            self.armed = None

    async def wait_for_load_state(self, state):
        pass


def human_for(page, seed=1):
    return Human(page, config=HumanConfig(), seed=seed, clock=VirtualClock())


async def test_warm_up_browses_sites_follows_links_and_respects_duration():
    page = BrowsingPage()
    human = human_for(page)
    report = await warm_up(page, sites=list(SITES), duration=120, human=human, rng_seed=4)
    assert isinstance(report, WarmupReport)
    assert report.sites_visited == 3 and report.sites_failed == 0
    assert report.links_followed >= 1
    assert report.elapsed <= 120 + 2
    assert set(SITES) <= set(page.history)
    followed = [u for u in page.history if u not in SITES]
    assert len(followed) == report.links_followed
    assert any(e[0] == "down" for e in page.mouse.events)  # Links are clicked...
    assert any(e[0] == "wheel" for e in page.mouse.events)  # ...after scrolling.
    assert page.keyboard.events == []  # Nothing is ever typed.


async def test_short_duration_bounds_and_skips_visits_that_cannot_fit():
    page = BrowsingPage()
    report = await warm_up(page, sites=list(SITES), duration=7, human=human_for(page), rng_seed=1)
    assert report.elapsed <= 7 + 2
    page = BrowsingPage()
    report = await warm_up(page, sites=list(SITES), duration=2, human=human_for(page))
    assert report == WarmupReport(0, 0, 0, 0.0)
    assert page.gotos == []


async def test_failed_site_is_skipped_and_logged_without_url(caplog):
    page = BrowsingPage(failing={"https://wiki.test/"})
    with caplog.at_level(logging.INFO, logger="botonomus.profiles.warmup"):
        report = await warm_up(
            page, sites=list(SITES), duration=90, human=human_for(page), rng_seed=2
        )
    assert report.sites_failed == 1 and report.sites_visited == 2
    assert "OSError" in caplog.text
    assert "wiki.test" not in caplog.text and "http" not in caplog.text


async def test_hanging_site_is_bounded(monkeypatch):
    monkeypatch.setattr(warmup_module, "_MIN_VISIT", 0.05)
    monkeypatch.setattr(warmup_module, "_MARGIN", 0.01)
    page = BrowsingPage(hanging={"https://news.test/"})
    report = await asyncio.wait_for(
        warm_up(page, sites=["https://news.test/"], duration=0.2, human=human_for(page)),
        timeout=5,
    )
    assert report.sites_failed == 1 and report.sites_visited == 0


async def test_covered_links_are_never_clicked():
    page = BrowsingPage(covered=True)
    report = await warm_up(
        page, sites=["https://news.test/"], duration=60, human=human_for(page), rng_seed=3
    )
    assert report.links_followed >= 1
    assert not any(e[0] == "down" for e in page.mouse.events)
    assert len(page.gotos) == 1 + report.links_followed


async def test_seeded_runs_are_reproducible():
    histories = []
    for _ in range(2):
        page = BrowsingPage()
        await warm_up(page, sites=list(SITES), duration=100, human=human_for(page), rng_seed=9)
        histories.append(page.history)
    assert histories[0] == histories[1]


async def test_default_human_and_validation():
    page = BrowsingPage()
    with pytest.raises(ValueError):
        await warm_up(page, duration=0)
    with pytest.raises(ValueError):
        await warm_up(page, duration=10, max_links_per_site=-1)
    report = await warm_up(page, sites=[], duration=10)
    assert (report.sites_visited, report.sites_failed, report.links_followed) == (0, 0, 0)
    assert 0 <= report.elapsed < 1  # real clock: exact 0.0 only on coarse Windows timers
    assert all(site.startswith("https://") for site in DEFAULT_SITES)
