"""Profile warm-up: browse common sites humanly so a profile accumulates history.

A fresh profile with no cookies or history is itself a signal. `warm_up`
visits a curated list of benign, high-traffic sites, reads (dwells and scrolls) and
follows a few same-site links with human input. It never types, never submits a
form, and never clicks anything but an ordinary same-site link, so consent banners
and dialogs are left untouched. It is opt-in and never runs automatically.

Logs name a failure category (the exception type) and the visit's position, never a
URL, since a profile's browsing can identify its owner.
"""

import asyncio
import logging
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, TypedDict

from ..human import Human, HumanConfig

logger = logging.getLogger(__name__)

DEFAULT_SITES: tuple[str, ...] = (
    "https://en.wikipedia.org/wiki/Main_Page",
    "https://www.bbc.com/news",
    "https://www.theguardian.com/international",
    "https://www.reuters.com/",
    "https://apnews.com/",
    "https://weather.com/",
    "https://www.accuweather.com/",
    "https://www.amazon.com/",
    "https://www.ebay.com/",
    "https://www.imdb.com/",
    "https://www.allrecipes.com/",
)
"""News, encyclopedia, weather, shopping and entertainment home pages."""

# Below this many seconds a visit cannot load a page and read it, so it is skipped.
_MIN_VISIT = 3.0
# Dwell ends this long before a visit's deadline so the visit's timeout never
# interrupts a sleep that was about to finish anyway.
_MARGIN = 0.5
_NAVIGATION_WAIT = 8.0

_LINKS_SCRIPT = """() => {
  const host = location.hostname.replace(/^www\\./, '');
  const avoid = /(log-?in|log-?out|sign-?in|sign-?up|sign-?out|register|account|auth|cart|\
basket|checkout|subscribe|newsletter|consent|cookie|privacy|preference|setting|password|\
download|print|share|\\.(pdf|zip|exe|dmg|apk)$)/i;
  const out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    if (out.length >= 80) break;
    const raw = a.getAttribute('href');
    if (!raw || raw.startsWith('#') || a.target === '_blank' || a.hasAttribute('download'))
      continue;
    let url;
    try { url = new URL(a.href); } catch (e) { continue; }
    if (url.protocol !== 'https:' && url.protocol !== 'http:') continue;
    const h = url.hostname.replace(/^www\\./, '');
    if (h !== host && !h.endsWith('.' + host)) continue;
    if (url.pathname === location.pathname && url.search === location.search) continue;
    if (avoid.test(url.pathname + url.search)) continue;
    const r = a.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    out.push({href: url.href, raw: raw});
  }
  return out;
}"""

_HIT_SCRIPT = """(p) => {
  const el = document.elementFromPoint(p.x, p.y);
  const a = el && el.closest('a');
  return !!a && a.getAttribute('href') === p.raw;
}"""


class _Link(TypedDict):
    href: str
    raw: str


@dataclass(frozen=True, slots=True)
class WarmupReport:
    """What a warm-up did.

    Attributes:
        sites_visited: Sites loaded and browsed without error.
        sites_failed: Sites skipped after an error or a per-site timeout.
        links_followed: Same-site links followed across all sites.
        elapsed: Seconds spent, measured on the `Human`'s clock.
    """

    sites_visited: int
    sites_failed: int
    links_followed: int
    elapsed: float


async def warm_up(
    page: Any,
    *,
    sites: Sequence[str] = DEFAULT_SITES,
    duration: float,
    human: Human | None = None,
    rng_seed: int | None = None,
    max_links_per_site: int = 3,
) -> WarmupReport:
    """Browse ``sites`` humanly for at most ``duration`` seconds.

    Sites are visited in a random order. Each gets a share of the remaining time,
    in which the page is read (dwell and wheel-scroll) and up to
    ``max_links_per_site`` same-site links are followed. A link is clicked only
    after a hit test confirms the pointer is over that link; otherwise it is
    navigated to directly. Login, account, cart, checkout, consent and download
    links are never followed, nothing is typed and no form is submitted.

    A failing site (DNS, timeout, script error) is logged by category and skipped;
    it never aborts the warm-up.

    Args:
        page: A native [`botonomus.cdp.Page`][botonomus.cdp.Page] or a Playwright page.
        sites: Start URLs.
        duration: Upper bound on the warm-up in seconds.
        human: Input engine for ``page``; a default one is created when omitted.
        rng_seed: Seed for the site order, dwell and link choices (and for the
            created `Human`).
        max_links_per_site: Most links followed on one site.

    Returns:
        A `WarmupReport`.

    Raises:
        ValueError: If ``duration`` is not positive or ``max_links_per_site`` is
            negative.
    """
    if duration <= 0:
        raise ValueError("duration must be positive")
    if max_links_per_site < 0:
        raise ValueError("max_links_per_site must not be negative")
    human = human or Human(page, config=HumanConfig.preset("default"), seed=rng_seed)
    clock = human.clock
    rng = random.Random(rng_seed)
    order = list(sites)
    rng.shuffle(order)
    start = clock.now()
    deadline = start + duration
    visited = failed = followed = 0
    for index, url in enumerate(order):
        remaining = deadline - clock.now()
        if remaining < _MIN_VISIT:
            break
        budget = max(_MIN_VISIT, remaining / (len(order) - index))
        budget = min(budget, remaining)
        visit = _Visit(page, human, rng, clock.now() + budget - _MARGIN)
        try:
            async with asyncio.timeout(budget):
                followed += await visit.run(url, rng.randint(0, max_links_per_site))
            visited += 1
        except Exception as exc:
            failed += 1
            logger.info("Warm-up visit %d skipped: %s", index + 1, type(exc).__name__)
    return WarmupReport(visited, failed, followed, clock.now() - start)


class _Visit:
    """One site: load, read, follow a few links, all before ``deadline``."""

    def __init__(self, page: Any, human: Human, rng: random.Random, deadline: float) -> None:
        self.page = page
        self.human = human
        self.rng = rng
        self.deadline = deadline

    def remaining(self) -> float:
        return self.deadline - self.human.clock.now()

    async def run(self, url: str, links: int) -> int:
        await self.page.goto(url)
        await self.read(pages_left=links + 1)
        followed = 0
        for left in range(links, 0, -1):
            if self.remaining() < _MIN_VISIT:
                break
            candidates: list[_Link] = list(await self.page.evaluate(_LINKS_SCRIPT) or [])
            if not candidates:
                break
            await self.follow(self.rng.choice(candidates))
            followed += 1
            await self.read(pages_left=left)
        return followed

    async def read(self, pages_left: int) -> None:
        """Dwell on the page, mostly scrolling down, for this page's share of time."""
        clock, rng, human = self.human.clock, self.rng, self.human
        share = self.remaining() / max(1, pages_left)
        until = clock.now() + min(share, share * rng.lognormvariate(math.log(0.8), 0.2))
        while clock.now() < until:
            await clock.sleep(min(until - clock.now(), rng.lognormvariate(math.log(1.6), 0.6)))
            if clock.now() >= until:
                break
            gesture = rng.random()
            if gesture < 0.75:
                await human.scroll(rng.lognormvariate(math.log(380), 0.45))
            elif gesture < 0.88:
                await human.scroll(-rng.lognormvariate(math.log(250), 0.4))
            else:
                await human.idle(min(1.0, max(0.0, until - clock.now())))

    async def follow(self, link: _Link) -> None:
        """Click ``link`` if the pointer can be confirmed over it, else navigate."""
        before = self.page.url
        clicked = False
        try:
            async with asyncio.timeout(min(_NAVIGATION_WAIT, max(0.1, self.remaining()))):
                clicked = await self._click(link)
        except Exception:
            clicked = False
        if clicked and await self._navigated_from(before):
            await self._wait_loaded()
            return
        await self.page.goto(link["href"])

    async def _click(self, link: _Link) -> bool:
        locator = self.page.locator(f"a[href={_css_string(link['raw'])}]").first
        x, y = await self.human.move_to_locator(locator)
        on_link = await self.page.evaluate(_HIT_SCRIPT, {"x": x, "y": y, "raw": link["raw"]})
        if not on_link:
            return False  # Something (a banner, an overlay) covers it: never click that.
        await self.human.click((x, y))
        return True

    async def _navigated_from(self, before: str) -> bool:
        clock = self.human.clock
        until = clock.now() + min(_NAVIGATION_WAIT, max(0.0, self.remaining()))
        while clock.now() < until:
            if self.page.url != before:
                return True
            await clock.sleep(0.1)
        return bool(self.page.url != before)

    async def _wait_loaded(self) -> None:
        waiter = getattr(self.page, "wait_for_load_state", None) or getattr(
            self.page, "wait_for_load", None
        )
        if waiter is None:
            return
        try:
            async with asyncio.timeout(min(_NAVIGATION_WAIT, max(0.1, self.remaining()))):
                await waiter("load")
        except Exception:
            pass  # A slow subresource is no reason to stop reading.


def _css_string(value: str) -> str:
    """``value`` as a double-quoted CSS string literal."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\a ").replace("\r", "\\d ")
    return f'"{escaped}"'
