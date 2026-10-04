"""Repeated runs against public bot-detection pages, with reproducible reports.

Running this contacts third-party websites. A report describes those pages on one
machine, network, browser build and date; it is not proof of undetectability.
Verdicts come only from each site's extractor script; anything an extractor does
not decide is recorded as ``"unknown"`` and never inferred by this module.
"""

import asyncio
import json
import platform
import random
import re
import time
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, Protocol

from .._version import __version__
from ..browser import executable_sha256, is_botonomus_build
from ..cdp import EvaluationError, NavigationError
from ..config import BrowserConfig
from ..core.identity import resolve_executable
from ..errors import (
    BrowserCleanupError,
    BrowserStartupError,
    BrowserUnavailableError,
    ConfigurationError,
    ProfileInUseError,
)
from ..human import Human
from ..profiles import ProfileLease, remove_profile

Verdict = Literal["pass", "fail", "unknown", "blocked"]
WaitStrategy = Literal["load", "domcontentloaded"]

VERDICTS: Final = ("pass", "fail", "unknown", "blocked")
LIMITATION: Final = (
    "Results describe these third-party pages on this machine, network, browser build "
    "and date only. They are not proof of undetectability."
)
_SITE_NAME: Final = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
_PREFIX: Final = re.compile(r"[a-z0-9][a-z0-9-]{0,19}")
_DETAILS_LIMIT: Final = 4000


@dataclass(frozen=True, slots=True)
class DetectionSite:
    """One detection page and how to read it.

    Attributes:
        name: Short identifier: lowercase letters, digits and ``-``, at most 32.
        url: Page URL (``http`` or ``https``).
        wait_until: Navigation lifecycle event to wait for.
        ready: Optional JavaScript expression polled (in the isolated world) until
            truthy, for pages that compute results after load. Not reaching it is
            not an error; the extractor then decides what it can.
        ready_timeout: Seconds to poll ``ready``.
        settle: Seconds to wait after ``ready`` before capturing, with no CDP traffic.
        extractor: Optional JavaScript function source evaluated with
            ``page.evaluate``, which runs in the isolated world on the native and
            Patchright drivers. It must return ``{"verdict": "pass"|"fail"|"unknown"|
            "blocked", "details": {...}}``, where ``"blocked"`` means the page refused
            the visit before deciding (rate limit or challenge interstitial); any other
            shape is recorded as ``"unknown"``.
        description: What the page tests, for humans.

    Raises:
        ConfigurationError: On construction, if any value is invalid.
    """

    name: str
    url: str
    wait_until: WaitStrategy = "load"
    ready: str | None = None
    ready_timeout: float = 30.0
    settle: float = 8.0
    extractor: str | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _SITE_NAME.fullmatch(self.name):
            raise ConfigurationError("Site name must be 1-32 lowercase letters, digits or '-'")
        if not isinstance(self.url, str) or not self.url.startswith(("http://", "https://")):
            raise ConfigurationError("Site URL must be http or https")
        if self.wait_until not in ("load", "domcontentloaded"):
            raise ConfigurationError("wait_until must be 'load' or 'domcontentloaded'")
        for value in (self.ready_timeout, self.settle):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ConfigurationError("ready_timeout and settle must be non-negative")


@dataclass(slots=True)
class RunResult:
    """The outcome of one visit to one site.

    Attributes:
        site: Site name.
        run: 1-based run number for this site.
        profile: Profile name used.
        verdict: What the extractor returned, or ``"unknown"``.
        details: The extractor's small JSON details object.
        duration: Seconds from profile preparation to session close.
        error: Error category (``timeout``, ``navigation``, ``startup``,
            ``profile_in_use``, ``extractor``, ``cleanup``, ``other``) or ``None``.
        error_type: Exception class name, or ``None``. Messages are never recorded.
        screenshot: Screenshot path relative to the output directory, if saved.
        text_excerpt: Start of the page's visible text, whitespace collapsed.
    """

    site: str
    run: int
    profile: str
    verdict: Verdict = "unknown"
    details: dict[str, Any] = field(default_factory=dict)
    duration: float = 0.0
    error: str | None = None
    error_type: str | None = None
    screenshot: str | None = None
    text_excerpt: str = ""


@dataclass(frozen=True, slots=True)
class SiteSummary:
    """Aggregated outcomes for one site.

    Every run lands in exactly one of ``passed``, ``failed``, ``unknown``,
    ``blocked`` or ``errors``; a run with an error counts only as an error. Rates are fractions of
    ``runs``, rounded to four places, and ``None`` when there were no runs.

    Attributes:
        site: Site name.
        url: Site URL.
        runs: Runs attempted.
        passed: Runs whose extractor returned ``pass``.
        failed: Runs whose extractor returned ``fail``.
        unknown: Error-free runs without a decided verdict.
        blocked: Error-free runs the page refused (rate limit, challenge) before any
            bot decision.
        errors: Runs that raised.
        pass_rate: ``passed / runs``.
        fail_rate: ``failed / runs``.
        unknown_rate: ``unknown / runs``.
        blocked_rate: ``blocked / runs``.
        error_rate: ``errors / runs``.
        mean_duration: Mean run duration in seconds, or ``None``.
        error_categories: Count per error category.
    """

    site: str
    url: str
    runs: int
    passed: int
    failed: int
    unknown: int
    blocked: int
    errors: int
    pass_rate: float | None
    fail_rate: float | None
    unknown_rate: float | None
    blocked_rate: float | None
    error_rate: float | None
    mean_duration: float | None
    error_categories: dict[str, int]


@dataclass(frozen=True, slots=True)
class Environment:
    """Where and how a report was produced. Never contains credentials or hosts.

    Attributes:
        captured_at: UTC ISO 8601 timestamp.
        os: ``platform.platform()``.
        python: Python version.
        botonomus_version: Package version.
        driver: Driver name.
        headless: Whether sessions ran headless.
        browser_product: ``Browser.getVersion`` product, or ``"unknown"``.
        executable_name: File name of the browser executable, or ``None``.
        executable_sha256: SHA-256 of the executable's bytes, or ``None``.
        proxy_type: Proxy scheme (``http``, ``https``, ``socks5``) or ``None``.
        botonomus_build: Whether the executable is a Botonomus Chromium build, or
            ``None`` when no executable was found.
    """

    captured_at: str
    os: str
    python: str
    botonomus_version: str
    driver: str
    headless: bool
    browser_product: str
    executable_name: str | None
    executable_sha256: str | None
    proxy_type: str | None
    botonomus_build: bool | None = None


@dataclass(frozen=True, slots=True)
class DetectionReport:
    """A complete detection run.

    Attributes:
        environment: Run metadata.
        sites: One summary per site, in request order.
        runs: Every individual run.
        limitation: How (not) to read the results.
    """

    environment: Environment
    sites: list[SiteSummary]
    runs: list[RunResult]
    limitation: str = LIMITATION

    def to_dict(self) -> dict[str, Any]:
        """The report as JSON-compatible data."""
        return asdict(self)

    def to_json(self) -> str:
        """The report as indented JSON text."""
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


class SessionOpener(Protocol):
    """What `run_detection` needs from a manager such as ``Botonomus``."""

    config: BrowserConfig

    def open(self, *, profile: str) -> AbstractAsyncContextManager[Any]:
        """Open a session for ``profile``."""
        ...


def normalise_verdict(raw: object) -> tuple[Verdict, dict[str, Any]]:
    """Validate an extractor's return value.

    Args:
        raw: Whatever the extractor returned.

    Returns:
        The verdict (``"unknown"`` unless exactly ``"pass"``, ``"fail"`` or
        ``"blocked"``) and the
        details object (empty unless a JSON-serialisable dict; replaced by
        ``{"truncated": True}`` when larger than 4000 characters as JSON).
    """
    if not isinstance(raw, dict):
        return "unknown", {}
    value = raw.get("verdict")
    verdict: Verdict = value if value in ("pass", "fail", "blocked") else "unknown"
    details = raw.get("details")
    if not isinstance(details, dict):
        return verdict, {}
    try:
        encoded = json.dumps(details)
    except (TypeError, ValueError):
        return verdict, {}
    if len(encoded) > _DETAILS_LIMIT:
        return verdict, {"truncated": True}
    return verdict, details


def classify_error(exc: BaseException) -> str:
    """Map an exception to a coarse, driver-independent category.

    Args:
        exc: The exception a run raised.

    Returns:
        One of ``timeout``, ``navigation``, ``startup``, ``profile_in_use``,
        ``extractor``, ``cleanup`` or ``other``.
    """
    name = type(exc).__name__
    if isinstance(exc, TimeoutError) or "Timeout" in name:
        return "timeout"
    if isinstance(exc, NavigationError) or "net::ERR_" in str(exc):
        return "navigation"
    if isinstance(exc, ProfileInUseError):
        return "profile_in_use"
    if isinstance(exc, (BrowserStartupError, BrowserUnavailableError)):
        return "startup"
    if isinstance(exc, BrowserCleanupError):
        return "cleanup"
    if isinstance(exc, EvaluationError):
        return "extractor"
    return "other"


def aggregate(sites: Sequence[DetectionSite], runs: Sequence[RunResult]) -> list[SiteSummary]:
    """Summarise runs per site.

    Args:
        sites: Sites in report order.
        runs: Runs for any of those sites.

    Returns:
        One `SiteSummary` per site.
    """
    summaries = []
    for site in sites:
        mine = [run for run in runs if run.site == site.name]
        total = len(mine)
        errors = [run for run in mine if run.error is not None]
        clean = [run for run in mine if run.error is None]
        counts = {v: sum(1 for run in clean if run.verdict == v) for v in VERDICTS}
        categories: dict[str, int] = {}
        for run in errors:
            assert run.error is not None
            categories[run.error] = categories.get(run.error, 0) + 1

        def rate(count: int, total: int = total) -> float | None:
            return round(count / total, 4) if total else None

        summaries.append(
            SiteSummary(
                site=site.name,
                url=site.url,
                runs=total,
                passed=counts["pass"],
                failed=counts["fail"],
                unknown=counts["unknown"],
                blocked=counts["blocked"],
                errors=len(errors),
                pass_rate=rate(counts["pass"]),
                fail_rate=rate(counts["fail"]),
                unknown_rate=rate(counts["unknown"]),
                blocked_rate=rate(counts["blocked"]),
                error_rate=rate(len(errors)),
                mean_duration=round(sum(r.duration for r in mine) / total, 3) if total else None,
                error_categories=categories,
            )
        )
    return summaries


async def browser_product(page: Any) -> str:
    """Return ``Browser.getVersion``'s product string for the page's browser.

    Works with native pages (through their CDP session) and Playwright-compatible
    pages (through a temporary CDP session).

    Args:
        page: A native or Playwright-compatible page.

    Returns:
        A product such as ``Chrome/141.0.7390.55``, or ``"unknown"``.
    """
    try:
        session = getattr(page, "session", None)
        if session is not None and hasattr(session, "send"):
            result = await session.send("Browser.getVersion")
        else:
            cdp = await page.context.new_cdp_session(page)
            try:
                result = await cdp.send("Browser.getVersion")
            finally:
                await cdp.detach()
        return str(result.get("product") or "unknown")
    except Exception:  # metadata only; a missing version must not fail the run
        return "unknown"


async def collect_environment(config: BrowserConfig, product: str = "unknown") -> Environment:
    """Describe the environment for a report.

    Args:
        config: The configuration sessions ran with.
        product: ``Browser.getVersion`` product, if known.

    Returns:
        Metadata with the executable's hash and the proxy scheme only. The
        executable is the one sessions launch with ``config``
        ([`resolve_executable`][botonomus.core.resolve_executable]), so ``browser="auto"`` and
        installed Botonomus Chromium builds are taken into account.
    """
    try:
        executable: Path | None = await asyncio.to_thread(resolve_executable, config)
    except BrowserUnavailableError:  # includes BinaryNotInstalledError
        executable = None
    digest = await asyncio.to_thread(executable_sha256, executable) if executable else None
    build = await asyncio.to_thread(is_botonomus_build, executable) if executable else None
    proxy = config.proxy_spec
    return Environment(
        captured_at=datetime.now(UTC).isoformat(),
        os=platform.platform(),
        python=platform.python_version(),
        botonomus_version=__version__,
        driver=config.driver,
        headless=config.headless,
        browser_product=product,
        executable_name=executable.name if executable else None,
        executable_sha256=digest,
        proxy_type=proxy.scheme if proxy is not None else None,
        botonomus_build=build,
    )


async def run_detection(
    bot: SessionOpener,
    sites: Sequence[DetectionSite],
    *,
    runs: int = 1,
    profile_prefix: str = "detect",
    fresh_profiles: bool = True,
    output: Path | None = None,
    interact: bool = True,
    settle: float | None = None,
    run_timeout: float = 180.0,
    text_limit: int = 500,
    seed: int | None = None,
) -> DetectionReport:
    """Visit every site ``runs`` times and aggregate the extractors' verdicts.

    Sites run concurrently, up to the manager's capacity; the runs of one site run
    one after another. A failing run is recorded and never stops the others.

    Args:
        bot: An entered manager (or anything with ``config`` and ``open``).
        sites: Sites to visit; names must be unique.
        runs: Visits per site.
        profile_prefix: Profile name prefix: 1-20 lowercase letters, digits or ``-``.
        fresh_profiles: Delete and recreate a profile per run
            (``<prefix>-<site>-<run>``). Otherwise each site reuses
            ``<prefix>-<site>``, keeping cookies between runs.
        output: Directory for screenshots, page text and ``report.json``; nothing
            is written when ``None``.
        interact: Move and scroll with [`Human`][botonomus.Human] after loading, since
            some scores weigh pointer activity.
        settle: Overrides every site's ``settle`` seconds.
        run_timeout: Seconds allowed per run, including launch and close.
        text_limit: Characters kept in ``text_excerpt``.
        seed: Seed for interaction paths.

    Returns:
        The report. When ``output`` is given it is also saved as ``report.json``.

    Raises:
        ConfigurationError: For invalid ``runs``, prefix or duplicate site names.
    """
    if type(runs) is not int or runs < 1:
        raise ConfigurationError("runs must be a positive integer")
    if not _PREFIX.fullmatch(profile_prefix):
        raise ConfigurationError("profile_prefix must be 1-20 lowercase letters, digits or '-'")
    if len({site.name for site in sites}) != len(sites):
        raise ConfigurationError("Site names must be unique")
    if output is not None:
        for sub in ("screenshots", "text"):
            (output / sub).mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    product: list[str] = []

    async def visit(site: DetectionSite, run: int) -> RunResult:
        name = (
            f"{profile_prefix}-{site.name}-{run}"
            if fresh_profiles
            else (f"{profile_prefix}-{site.name}")
        )
        return await visit_site(
            bot, site, run=run, profile=name, fresh=fresh_profiles, output=output,
            interact=interact, settle=settle, run_timeout=run_timeout,
            text_limit=text_limit, rng=rng, product=product,
        )  # fmt: skip

    async def site_runs(site: DetectionSite) -> list[RunResult]:
        return [await visit(site, run) for run in range(1, runs + 1)]

    grouped = await asyncio.gather(*(site_runs(site) for site in sites))
    results = [result for group in grouped for result in group]
    environment = await collect_environment(bot.config, product[0] if product else "unknown")
    report = DetectionReport(environment, aggregate(sites, results), results)
    if output is not None:
        await asyncio.to_thread(
            (output / "report.json").write_text, report.to_json(), encoding="utf-8"
        )
    return report


async def visit_site(
    bot: SessionOpener,
    site: DetectionSite,
    *,
    run: int,
    profile: str,
    fresh: bool,
    output: Path | None,
    interact: bool,
    settle: float | None,
    run_timeout: float,
    text_limit: int,
    rng: random.Random,
    product: list[str],
    stem: str | None = None,
) -> RunResult:
    """Visit one site once and return its recorded outcome. Never raises for the visit.

    Args:
        bot: An entered manager.
        site: The site.
        run: 1-based run number, recorded in the result.
        profile: Profile name to use.
        fresh: Delete the profile first.
        output: Directory for screenshots and text, or ``None``.
        interact: Move and scroll with [`Human`][botonomus.Human] after loading.
        settle: Overrides the site's ``settle`` seconds.
        run_timeout: Seconds for the whole visit.
        text_limit: Characters kept in ``text_excerpt``.
        rng: Source of interaction seeds.
        product: Receives the browser product string once (shared across visits).
        stem: Screenshot and text file stem; defaults to ``<site>-<run:02d>``.

    Returns:
        The result; failures are recorded in it rather than raised.
    """
    result = RunResult(site=site.name, run=run, profile=profile)
    started = time.monotonic()
    try:
        async with asyncio.timeout(run_timeout):
            if fresh:
                await asyncio.to_thread(_discard_profile, bot.config.profile_root, profile)
            async with bot.open(profile=profile) as session:
                page = session.page
                if not product:
                    product.append(await browser_product(page))
                await _capture(
                    page, site, run, result, output, interact, settle, text_limit, rng, stem
                )
                if site.extractor is not None:
                    raw = await page.evaluate(site.extractor)
                    result.verdict, result.details = normalise_verdict(raw)
    except Exception as exc:  # recorded per run; one bad run never stops the rest
        result.error = classify_error(exc)
        result.error_type = type(exc).__name__
        result.verdict, result.details = "unknown", {}
    result.duration = round(time.monotonic() - started, 3)
    return result


async def _capture(
    page: Any,
    site: DetectionSite,
    run: int,
    result: RunResult,
    output: Path | None,
    interact: bool,
    settle: float | None,
    text_limit: int,
    rng: random.Random,
    stem: str | None = None,
) -> None:
    await page.goto(site.url, wait_until=site.wait_until)
    if interact:
        human = Human(page, seed=rng.randrange(1 << 30))
        await human.pause(1.0, 2.0)
        for _ in range(3):
            await human.move_to(rng.uniform(200, 900), rng.uniform(150, 600))
            await human.pause(0.3, 0.9)
        await human.scroll(600)
    if site.ready is not None:
        await _wait_ready(page, site.ready, site.ready_timeout)
    # Plain sleep: no CDP traffic while the page measures.
    await asyncio.sleep(site.settle if settle is None else settle)
    stem = stem or f"{site.name}-{run:02d}"
    if output is not None:
        try:
            await page.screenshot(path=output / "screenshots" / f"{stem}.png", full_page=True)
            result.screenshot = f"screenshots/{stem}.png"
        except Exception:  # a huge page may not fit a full-page capture; text still counts
            result.screenshot = None
    try:
        text = str(await page.locator("body").inner_text())
    except Exception:  # pages mid-redirect may have no body; extraction still runs
        text = ""
    result.text_excerpt = " ".join(text.split())[:text_limit]
    if output is not None:
        await asyncio.to_thread(
            (output / "text" / f"{stem}.txt").write_text, text, encoding="utf-8"
        )


async def _wait_ready(page: Any, expression: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if await page.evaluate(expression):
                return True
        except Exception:  # navigation may destroy the context between polls
            pass
        await asyncio.sleep(0.5)
    return False


def _discard_profile(root: Path, name: str) -> None:
    if ProfileLease(root, name).path.is_dir():
        remove_profile(root, name)
