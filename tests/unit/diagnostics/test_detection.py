import hashlib
import json

import pytest

from botonomus.browser import BUILD_MARKER
from botonomus.cdp import EvaluationError, NavigationError, TimeoutError_
from botonomus.config import BrowserConfig
from botonomus.core import identity, resolve_executable
from botonomus.diagnostics import (
    CATALOGUE,
    SITES,
    DetectionSite,
    RunResult,
    aggregate,
    classify_error,
    detection,
    normalise_verdict,
    run_detection,
)
from botonomus.errors import (
    BrowserCleanupError,
    BrowserStartupError,
    ConfigurationError,
    ProfileInUseError,
)

from .fakes import FakeOpener, FakePage

SITE_A = DetectionSite("alpha", "https://a.example/", settle=0, extractor="() => 1")
SITE_B = DetectionSite("beta", "https://b.example/", settle=0)


@pytest.fixture(autouse=True)
def no_executable(monkeypatch):
    def missing(config):
        raise detection.BrowserUnavailableError("none")

    monkeypatch.setattr(detection, "resolve_executable", missing)


def run(site, number, verdict="unknown", error=None, duration=1.0):
    return RunResult(
        site=site, run=number, profile="p", verdict=verdict, error=error, duration=duration
    )


def test_aggregate_counts_each_run_once_and_computes_rates():
    runs = [
        run("alpha", 1, "pass", duration=1.0),
        run("alpha", 2, "pass", duration=2.0),
        run("alpha", 3, "fail", duration=3.0),
        run("alpha", 4, "unknown", duration=4.0),
        run("alpha", 5, "unknown", error="timeout", duration=5.0),
        run("alpha", 6, "pass", error="navigation", duration=6.0),
        run("alpha", 7, "unknown", error="timeout", duration=7.0),
        run("beta", 1, "fail"),
    ]
    alpha, beta = aggregate([SITE_A, SITE_B], runs)
    assert (alpha.runs, alpha.passed, alpha.failed, alpha.unknown, alpha.errors) == (7, 2, 1, 1, 3)
    assert alpha.passed + alpha.failed + alpha.unknown + alpha.errors == alpha.runs
    assert alpha.pass_rate == round(2 / 7, 4)
    assert alpha.fail_rate == round(1 / 7, 4)
    assert alpha.unknown_rate == round(1 / 7, 4)
    assert alpha.error_rate == round(3 / 7, 4)
    assert alpha.mean_duration == 4.0
    assert alpha.error_categories == {"timeout": 2, "navigation": 1}
    assert (beta.runs, beta.failed, beta.fail_rate, beta.pass_rate) == (1, 1, 1.0, 0.0)


def test_aggregate_without_runs_has_no_rates():
    (summary,) = aggregate([SITE_A], [])
    assert summary.runs == 0
    assert summary.pass_rate is None and summary.mean_duration is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"verdict": "pass", "details": {"x": 1}}, ("pass", {"x": 1})),
        ({"verdict": "fail"}, ("fail", {})),
        ({"verdict": "PASS"}, ("unknown", {})),
        ({"verdict": "bot", "details": {"y": 2}}, ("unknown", {"y": 2})),
        ({"verdict": True}, ("unknown", {})),
        ({"verdict": "pass", "details": [1]}, ("pass", {})),
        ({"verdict": "pass", "details": {"big": "x" * 5000}}, ("pass", {"truncated": True})),
        (None, ("unknown", {})),
        ("pass", ("unknown", {})),
        ([], ("unknown", {})),
    ],
)
def test_normalise_verdict_never_invents_a_verdict(raw, expected):
    assert normalise_verdict(raw) == expected


@pytest.mark.parametrize(
    ("exc", "category"),
    [
        (TimeoutError(), "timeout"),
        (TimeoutError_("slow"), "timeout"),
        (type("TimeoutError", (Exception,), {})("pw"), "timeout"),
        (NavigationError("net::ERR_NAME_NOT_RESOLVED at x"), "navigation"),
        (Exception("page.goto: net::ERR_CONNECTION_RESET"), "navigation"),
        (BrowserStartupError("x"), "startup"),
        (ProfileInUseError("x"), "profile_in_use"),
        (BrowserCleanupError("x"), "cleanup"),
        (EvaluationError("x"), "extractor"),
        (RuntimeError("x"), "other"),
    ],
)
def test_classify_error(exc, category):
    assert classify_error(exc) == category


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "Bad Name", "url": "https://x/"},
        {"name": "x" * 33, "url": "https://x/"},
        {"name": "ok", "url": "file:///etc/passwd"},
        {"name": "ok", "url": "https://x/", "settle": -1},
        {"name": "ok", "url": "https://x/", "wait_until": "networkidle"},
    ],
)
def test_site_validation(kwargs):
    with pytest.raises(ConfigurationError):
        DetectionSite(**kwargs)


def test_catalogue_is_valid_and_indexed():
    assert len(SITES) == len(CATALOGUE) >= 8
    assert {"sannysoft", "creepjs", "recaptcha-score", "nowsecure"} <= set(SITES)
    for site in CATALOGUE:
        assert site.url.startswith("https://")
        if site.extractor is not None:
            assert site.extractor.lstrip().startswith("() =>")


async def test_run_detection_records_runs_and_aggregates(tmp_path):
    def factory(profile):
        if profile.startswith("detect-alpha"):
            run_number = int(profile.rsplit("-", 1)[1])
            verdict = "pass" if run_number < 3 else "fail"
            return FakePage({"verdict": verdict, "details": {"run": run_number}})
        return FakePage(goto_error=NavigationError("net::ERR_FAILED at b"))

    opener = FakeOpener(tmp_path / "profiles", factory)
    (tmp_path / "profiles" / "detect-alpha-1").mkdir(parents=True)
    output = tmp_path / "out"
    report = await run_detection(opener, [SITE_A, SITE_B], runs=3, output=output, interact=False)

    assert opener.opened.count("detect-alpha-1") == 1
    assert sorted(p for p in opener.opened if "alpha" in p) == [
        "detect-alpha-1",
        "detect-alpha-2",
        "detect-alpha-3",
    ]
    assert opener.existed[opener.opened.index("detect-alpha-1")] is False  # fresh profile
    alpha, beta = report.sites
    assert (alpha.passed, alpha.failed, alpha.errors) == (2, 1, 0)
    assert alpha.pass_rate == round(2 / 3, 4)
    assert (beta.errors, beta.error_categories) == (3, {"navigation": 3})
    first = next(r for r in report.runs if r.site == "alpha" and r.run == 1)
    assert first.details == {"run": 1}
    assert first.text_excerpt == "Hello page"
    assert first.screenshot == "screenshots/alpha-01.png"
    assert (output / first.screenshot).is_file()
    assert (output / "text" / "alpha-01.txt").read_text(encoding="utf-8") == "Hello   page"
    failed = next(r for r in report.runs if r.site == "beta")
    assert (failed.error, failed.error_type, failed.verdict) == (
        "navigation",
        "NavigationError",
        "unknown",
    )
    saved = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert saved == json.loads(report.to_json())
    assert set(saved) == {"environment", "sites", "runs", "limitation"}


async def test_site_without_extractor_is_unknown_and_extractor_errors_are_recorded(tmp_path):
    pages = {
        "detect-beta": FakePage({"verdict": "pass"}),
        "detect-alpha": FakePage(eval_error=EvaluationError("boom")),
    }
    opener = FakeOpener(tmp_path, lambda profile: pages[profile])
    report = await run_detection(
        opener, [SITE_A, SITE_B], runs=2, fresh_profiles=False, interact=False
    )
    assert opener.opened.count("detect-alpha") == 2  # reused, not recreated
    assert pages["detect-beta"].evaluated == []  # no extractor means no evaluation
    alpha, beta = report.sites
    assert alpha.error_categories == {"extractor": 2}
    assert (beta.unknown, beta.passed) == (2, 0)
    assert all(r.screenshot is None for r in report.runs)


async def test_ready_expression_is_polled_before_extracting(tmp_path):
    site = DetectionSite("ready", "https://r/", ready="window.done", settle=0, extractor="() => 0")
    page = FakePage({"verdict": "pass"})
    report = await run_detection(FakeOpener(tmp_path, lambda p: page), [site], interact=False)
    assert page.evaluated == ["window.done", "() => 0"]
    assert report.sites[0].passed == 1


async def test_environment_has_proxy_scheme_only(tmp_path):
    opener = FakeOpener(
        tmp_path,
        lambda p: FakePage({"verdict": "pass"}),
        proxy="socks5://alice:hunter2@proxy.internal:1080",
        driver="patchright",
    )
    report = await run_detection(opener, [SITE_A], interact=False)
    text = report.to_json()
    assert report.environment.proxy_type == "socks5"
    assert report.environment.driver == "patchright"
    assert report.environment.browser_product == "unknown"
    for secret in ("alice", "hunter2", "proxy.internal", "1080"):
        assert secret not in text
    assert report.environment.captured_at.endswith("+00:00")


@pytest.mark.parametrize(
    "kwargs",
    [{"runs": 0}, {"runs": True}, {"profile_prefix": "Bad"}, {"profile_prefix": "x" * 21}],
)
async def test_run_detection_rejects_bad_arguments(tmp_path, kwargs):
    with pytest.raises(ConfigurationError):
        await run_detection(FakeOpener(tmp_path, FakePage), [SITE_A], **kwargs)


async def test_duplicate_sites_rejected(tmp_path):
    with pytest.raises(ConfigurationError):
        await run_detection(FakeOpener(tmp_path, FakePage), [SITE_A, SITE_A])


async def test_browser_product_native_and_playwright_pages():
    class Session:
        async def send(self, method):
            assert method == "Browser.getVersion"
            return {"product": "Chrome/141.0.1.2"}

    class NativePage:
        session = Session()

    class CDP:
        detached = False

        async def send(self, method):
            return {"product": "HeadlessChrome/1"}

        async def detach(self):
            CDP.detached = True

    class Context:
        async def new_cdp_session(self, page):
            return CDP()

    class PlaywrightPage:
        context = Context()

    assert await detection.browser_product(NativePage()) == "Chrome/141.0.1.2"
    assert await detection.browser_product(PlaywrightPage()) == "HeadlessChrome/1"
    assert CDP.detached
    assert await detection.browser_product(object()) == "unknown"


async def test_environment_hashes_the_executable_auto_would_launch(tmp_path, monkeypatch):
    build = tmp_path / "build" / "chrome.exe"
    build.parent.mkdir()
    build.write_bytes(b"botonomus")
    (build.parent / BUILD_MARKER).write_text("{}", encoding="utf-8")
    chrome = tmp_path / "chrome.exe"
    chrome.write_bytes(b"chrome")
    monkeypatch.setattr(detection, "resolve_executable", resolve_executable)
    monkeypatch.setattr(identity, "find_botonomus_chromium", lambda: build)
    monkeypatch.setattr(identity, "find_chrome", lambda explicit=None: explicit or chrome)

    auto = await detection.collect_environment(BrowserConfig(profile_root=tmp_path))
    assert auto.executable_name == "chrome.exe"
    assert auto.executable_sha256 == hashlib.sha256(b"botonomus").hexdigest()
    assert auto.botonomus_build is True

    stock = await detection.collect_environment(
        BrowserConfig(profile_root=tmp_path, browser="chrome")
    )
    assert stock.executable_sha256 == hashlib.sha256(b"chrome").hexdigest()
    assert stock.botonomus_build is False


async def test_environment_without_a_build_for_browser_botonomus(tmp_path, monkeypatch):
    monkeypatch.setattr(detection, "resolve_executable", resolve_executable)
    monkeypatch.setattr(identity, "find_botonomus_chromium", lambda: None)
    env = await detection.collect_environment(
        BrowserConfig(profile_root=tmp_path, browser="botonomus")
    )
    assert (env.executable_name, env.executable_sha256, env.botonomus_build) == (None, None, None)


def test_normalise_verdict_accepts_blocked():
    assert normalise_verdict({"verdict": "blocked", "details": {"reason": "rate_limited"}}) == (
        "blocked",
        {"reason": "rate_limited"},
    )


def test_aggregate_counts_blocked_separately():
    runs = [run("alpha", 1, "pass"), run("alpha", 2, "blocked"), run("alpha", 3, "blocked")]
    (alpha,) = aggregate([SITE_A], runs)
    assert (alpha.passed, alpha.blocked, alpha.failed, alpha.unknown) == (1, 2, 0, 0)
    assert alpha.passed + alpha.failed + alpha.unknown + alpha.blocked + alpha.errors == alpha.runs
    assert alpha.blocked_rate == round(2 / 3, 4)
