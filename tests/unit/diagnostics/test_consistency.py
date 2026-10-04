import pytest

from botonomus.diagnostics.consistency import report_from_raw

GOOD_CTX = {
    "ua": "Mozilla/5.0 Chrome/155.0.0.0",
    "brands": "Google Chrome/155,Chromium/155",
    "hc": 8,
    "dm": 8,
    "tz": "Europe/Berlin",
    "langs": "en-US,en",
}


CHROME_DOCUMENT_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
    "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
)
CHROME_IMAGE_ACCEPT = "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"


def raw(**over):
    base = {
        "accept": {"document": CHROME_DOCUMENT_ACCEPT, "image": CHROME_IMAGE_ACCEPT},
        "canvas": ["h1", "h1"],
        "canvasSolid": True,
        "webgl": ["g1", "g1"],
        "audio": [1.5, 1.5],
        "contexts": {
            "page": GOOD_CTX,
            "frame": GOOD_CTX,
            "worker": GOOD_CTX,
            "shared": GOOD_CTX,
            "service": GOOD_CTX,
        },
        "h264": "probably",
        "widevine": True,
        "voices": ["Google US English*", "Microsoft Zira"],
        "screen": {
            "outerW": 1000,
            "innerW": 990,
            "outerH": 800,
            "innerH": 700,
            "h": 1080,
            "availH": 1040,
            "w": 1920,
            "availW": 1920,
            "cssDevice": True,
        },  # fmt: skip
    }
    base.update(over)
    return base


def by_name(report):
    return {c.name: c for c in report.checks}


def test_all_good_is_ok():
    report = report_from_raw(raw(), "Chrome/155")
    assert report.ok, [c for c in report.checks if not c.passed]


def test_unstable_canvas_and_noisy_solid_fail():
    checks = by_name(report_from_raw(raw(canvas=["a", "b"], canvasSolid=False), "p"))
    assert not checks["canvas-stable"].passed and not checks["canvas-solid"].passed


def test_context_mismatch_names_field_and_context():
    worker = {**GOOD_CTX, "hc": 16}
    contexts = {"page": GOOD_CTX, "worker": worker}
    check = by_name(report_from_raw(raw(contexts=contexts), "p"))["contexts-agree"]
    assert not check.passed
    assert check.observed["mismatches"] == [
        {"field": "hc", "context": "worker", "page": 8, "value": 16}
    ]


def test_google_brand_without_widevine_fails():
    checks = by_name(report_from_raw(raw(widevine=False), "p"))
    assert not checks["media"].passed


def test_google_voices_are_reported_not_required():
    # Chrome lists its network voices only after a public page loads in the session,
    # so a loopback-only check cannot require them.
    checks = by_name(report_from_raw(raw(voices=["Microsoft Zira"]), "p"))
    assert checks["voices"].passed
    assert checks["voices"].observed["google_network_voice"] is False
    assert not by_name(report_from_raw(raw(voices=[]), "p"))["voices"].passed


def test_chromium_brand_does_not_require_google_extras():
    ctx = {**GOOD_CTX, "brands": "Chromium/155"}
    contexts = {"page": ctx, "worker": ctx}
    report = report_from_raw(raw(contexts=contexts, voices=["Microsoft Zira"], widevine=False), "p")
    checks = by_name(report)
    assert checks["voices"].passed and checks["media"].passed


def test_headless_ua_fails():
    ctx = {**GOOD_CTX, "ua": "Mozilla/5.0 HeadlessChrome/155.0.0.0"}
    assert not by_name(report_from_raw(raw(contexts={"page": ctx}), "p"))["headless-ua"].passed


def test_consistency_report_from_raw_marks_missing_values_fail():
    checks = by_name(report_from_raw({"contexts": {}}, "p"))
    assert set(checks) == {
        "canvas-stable",
        "canvas-solid",
        "webgl-stable",
        "audio-stable",
        "contexts-agree",
        "media",
        "voices",
        "headless-ua",
        "screen",
        "accept-header",
    }
    assert not checks["canvas-stable"].passed and not checks["contexts-agree"].passed
    assert not checks["headless-ua"].passed and not checks["screen"].passed


def test_contexts_agree_requires_every_context():
    contexts = {"page": GOOD_CTX, "frame": GOOD_CTX}
    check = by_name(report_from_raw(raw(contexts=contexts), "p"))["contexts-agree"]
    assert not check.passed
    assert check.observed["missing"] == ["service", "shared", "worker"]


def test_probe_errors_fail_their_check_with_the_error():
    report = report_from_raw(raw(canvas="error:TypeError", widevine="error:TypeError"), "p")
    checks = by_name(report)
    assert not checks["canvas-stable"].passed
    assert checks["canvas-stable"].observed["reads"] == "error:TypeError"
    assert not checks["media"].passed


async def test_no_result_is_a_botonomus_error(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    import pytest

    import botonomus.core
    from botonomus import BrowserConfig
    from botonomus.diagnostics import consistency
    from botonomus.errors import BotonomusError

    class Page:
        async def goto(self, url):
            pass

    class Manager:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        @asynccontextmanager
        async def open(self, *, profile):
            yield SimpleNamespace(page=Page())

    async def product(page):
        return "Chrome/1"

    monkeypatch.setattr(botonomus.core, "Botonomus", Manager)
    monkeypatch.setattr(consistency, "browser_product", product, raising=False)
    from botonomus.diagnostics import detection

    monkeypatch.setattr(detection, "browser_product", product)
    with pytest.raises(BotonomusError, match="consistency page"):
        await consistency.run_consistency(BrowserConfig(profile_root=tmp_path), timeout=0.2)


def test_page_script_is_valid_javascript(tmp_path):
    import json
    import re
    import shutil
    import subprocess

    import pytest

    from botonomus.diagnostics.consistency import CONTEXT_EXPRESSION

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    from importlib.resources import files

    html = files("botonomus.diagnostics").joinpath("assets/consistency.html").read_text("utf-8")
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    path = tmp_path / "page.js"
    path.write_text(script.replace("__CTX_JSON__", json.dumps(CONTEXT_EXPRESSION)), "utf-8")
    assert subprocess.run([node, "--check", str(path)], capture_output=True).returncode == 0


def test_probe_error_strings_never_crash_the_report():
    checks = by_name(report_from_raw(raw(screen="error:TypeError", voices="error:X"), "p"))
    assert not checks["screen"].passed and checks["screen"].observed == {"error": "error:TypeError"}
    assert not checks["voices"].passed


async def test_each_run_uses_a_fresh_profile(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    import botonomus.core
    from botonomus import BrowserConfig
    from botonomus.diagnostics import consistency, detection

    profiles = []

    class Page:
        async def goto(self, url):
            pass

    class Manager:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        @asynccontextmanager
        async def open(self, *, profile):
            profiles.append(profile)
            yield SimpleNamespace(page=Page())

    async def product(page):
        return "Chrome/1"

    monkeypatch.setattr(botonomus.core, "Botonomus", Manager)
    monkeypatch.setattr(detection, "browser_product", product)
    monkeypatch.setattr(consistency.ConsistencyServer, "receive", lambda self, timeout: raw())
    config = BrowserConfig(profile_root=tmp_path)
    await consistency.run_consistency(config)
    await consistency.run_consistency(config)
    assert profiles[0] != profiles[1] and all(p.startswith("consistency-") for p in profiles)


def test_jxl_in_accept_fails_for_google_chrome():
    jxl = {"document": CHROME_DOCUMENT_ACCEPT, "image": "image/jxl," + CHROME_IMAGE_ACCEPT}
    check = by_name(report_from_raw(raw(accept=jxl), "p"))["accept-header"]
    assert not check.passed
    assert check.observed["jxl"] is True and check.observed["image"].startswith("image/jxl")


def test_missing_accept_headers_fail():
    assert not by_name(report_from_raw(raw(accept={}), "p"))["accept-header"].passed
    assert not by_name(report_from_raw(raw(accept=None), "p"))["accept-header"].passed


def test_chromium_brand_may_advertise_jxl():
    ctx = {**GOOD_CTX, "brands": "Chromium/155"}
    contexts = dict.fromkeys(("page", "frame", "worker", "shared", "service"), ctx)
    jxl = {"document": CHROME_DOCUMENT_ACCEPT, "image": "image/jxl," + CHROME_IMAGE_ACCEPT}
    check = by_name(report_from_raw(raw(contexts=contexts, accept=jxl), "p"))["accept-header"]
    assert check.passed


SCREEN = {"outerW": 1366, "innerW": 1350, "outerH": 728, "innerH": 640, "w": 1366, "h": 768,
          "availW": 1366, "availH": 728, "cssDevice": True}  # fmt: skip
PERSONA_SCREEN = {"w": 1366, "h": 768, "taskbar": 40}


def test_persona_screen_matching_everywhere_passes():
    report = report_from_raw(raw(screen=SCREEN, persona_screen=PERSONA_SCREEN), "p")
    assert by_name(report)["screen"].passed


@pytest.mark.parametrize(
    "change",
    [
        {"w": 1920},
        {"h": 1080},
        {"availH": 768},
        {"outerW": 1400},
        {"outerH": 760},
        {"cssDevice": False},
    ],  # fmt: skip
)
def test_persona_screen_contradictions_fail(change):
    report = report_from_raw(raw(screen={**SCREEN, **change}, persona_screen=PERSONA_SCREEN), "p")
    check = by_name(report)["screen"]
    assert not check.passed
    assert check.observed["persona"] == PERSONA_SCREEN


def test_css_device_size_must_match_js_screen():
    screen = {**raw()["screen"], "cssDevice": False}
    assert not by_name(report_from_raw(raw(screen=screen), "p"))["screen"].passed
