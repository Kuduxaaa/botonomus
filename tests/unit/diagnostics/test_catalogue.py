import pytest

from botonomus.diagnostics import CATALOGUE, SITES
from botonomus.diagnostics.catalogue import extractor

from .jsrun import fixture, needs_node, run_extractor

pytestmark = needs_node


def verdict(site, text, **kw):
    return run_extractor(SITES[site].extractor, text, **kw)


def test_every_site_has_an_extractor_and_unique_name():
    assert len({s.name for s in CATALOGUE}) == len(CATALOGUE)
    assert all(s.extractor for s in CATALOGUE)
    new = {"fingerprint-playground", "pixelscan", "fingerprint-scan", "rebrowser", "turnstile"}
    assert new <= set(SITES)


@pytest.mark.parametrize("site", [s.name for s in CATALOGUE])
def test_rate_limit_is_blocked_for_every_extractor(site):
    result = verdict(site, fixture("rate-limited.txt"))
    assert result["verdict"] == "blocked"
    assert result["details"]["reason"] == "rate_limited"


def test_challenge_title_is_blocked_except_on_challenge_sites():
    challenge = {"title": "Just a moment..."}
    assert verdict("pixelscan", "Checking your browser", **challenge)["verdict"] == "blocked"
    assert verdict("nowsecure", "Checking your browser", **challenge)["verdict"] == "fail"


def test_extractor_wraps_body_with_prelude():
    source = extractor("return {verdict: 'pass', details: {n: text.length}};")
    assert run_extractor(source, "abc") == {"verdict": "pass", "details": {"n": 3}}


def test_fingerprint_demo_tampering_is_fail():
    r = verdict("fingerprint", fixture("fingerprint-botonomus.txt"))
    assert r == {"verdict": "fail", "details": {"reason": "tampering"}}


def test_fingerprint_demo_prices_are_pass():
    r = verdict("fingerprint", "Search for today's flights\nPrague to Rome  $123  Book")
    assert r["verdict"] == "pass"


def test_fingerprint_playground_reads_smart_signals():
    flagged = verdict("fingerprint-playground", fixture("fingerprint-playground-flagged.txt"))
    assert flagged["verdict"] == "fail"
    d = flagged["details"]
    assert d["tampering"] is True and d["anti_detect_browser"] is True
    assert d["suspect_score"] == 35 and d["tampering_ml_score"] == 0.4833
    clean = verdict("fingerprint-playground", fixture("fingerprint-playground-clean.txt"))
    assert clean["verdict"] == "pass"
    assert clean["details"]["tampering"] is False
    assert clean["details"]["anti_detect_browser"] is False


def test_creepjs_percentages():
    r = verdict("creepjs", fixture("creepjs-botonomus.txt"))
    assert r == {"verdict": "pass", "details": {"headless": 0, "like_headless": 19, "stealth": 0}}
    failing = "33% like headless: x\n12% headless: y\n0% stealth: z"
    assert verdict("creepjs", failing)["verdict"] == "fail"


def test_pixelscan_consistency_and_masking():
    assert verdict("pixelscan", fixture("pixelscan-botonomus.txt"))["verdict"] == "pass"
    r = verdict("pixelscan", fixture("pixelscan-cloak.txt"))
    assert r["verdict"] == "fail"
    assert r["details"]["consistent"] is False and r["details"]["masking"] is True


def test_fingerprint_scan_bot_score_thresholds():
    assert verdict("fingerprint-scan", fixture("fingerprint-scan-botonomus.txt")) == {
        "verdict": "pass",
        "details": {"bot_score": 15},
    }
    cloak = verdict("fingerprint-scan", fixture("fingerprint-scan-cloak.txt"))
    assert cloak["verdict"] == "unknown"
    assert verdict("fingerprint-scan", "Bot score 75/100")["verdict"] == "fail"


def test_rebrowser_rows():
    r = verdict("rebrowser", fixture("rebrowser-botonomus.txt"))
    assert r["verdict"] == "pass"
    assert r["details"]["red"] == [] and r["details"]["yellow"] == ["useragent"]
    red = "🔴 runtimeEnableLeak\t1 ms\tLeak\n🟢 navigatorWebdriver\t2 ms\tok"
    assert verdict("rebrowser", red) == {
        "verdict": "fail",
        "details": {"red": ["runtimeEnableLeak"], "yellow": []},
    }


def test_browserscan_normal_is_pass():
    assert verdict("browserscan", fixture("browserscan-botonomus.txt")) == {
        "verdict": "pass",
        "details": {"status": "normal"},
    }
    assert verdict("browserscan", "Test Results:\nRobot\n")["verdict"] == "fail"


def test_deviceandbrowserinfo_statement():
    r = verdict("deviceandbrowserinfo", fixture("deviceandbrowserinfo-cloak.txt"))
    assert r["verdict"] == "fail" and r["details"]["statement"] == "bot"
    human = verdict("deviceandbrowserinfo", fixture("deviceandbrowserinfo-botonomus.txt"))
    assert human["verdict"] == "pass" and human["details"]["flags"] == []


def test_deviceandbrowserinfo_lists_true_flags():
    text = 'You are a bot!\n"isBot": true,\n"hasWebdriverTrue": true,\n"isPlaywright": false'
    assert verdict("deviceandbrowserinfo", text)["details"]["flags"] == ["hasWebdriverTrue"]


def test_recaptcha_score():
    assert verdict("recaptcha-score", fixture("recaptcha-botonomus.txt")) == {
        "verdict": "fail",
        "details": {"score": 0.1},
    }


def test_incolumitas_ignores_webdriver_spec_check():
    pre = [{"textContent": '{"WEBDRIVER": "FAIL", "HEADCHR_UA": "OK"}'}]
    r = verdict("incolumitas", "results", elements={"pre": pre})
    assert r == {
        "verdict": "pass",
        "details": {"ok": 1, "fail": 0, "failed": [], "ignored": ["WEBDRIVER"]},
    }
    pre = [{"textContent": '{"WEBDRIVER": "FAIL", "PHANTOM_UA": "FAIL"}'}]
    assert verdict("incolumitas", "results", elements={"pre": pre})["verdict"] == "fail"


def test_sannysoft_cells():
    cells = [{"className": "passed"}] * 3 + [{"className": "failed"}]
    assert verdict("sannysoft", "x", elements={"td": cells})["verdict"] == "fail"


def test_turnstile_elements():
    assert verdict("turnstile", "x", elements={"#success": [{}]})["verdict"] == "pass"
    assert verdict("turnstile", "x", elements={"#fail": [{}]})["verdict"] == "fail"
    assert verdict("turnstile", "x")["verdict"] == "unknown"


def test_fixtures_carry_no_real_ip_addresses():
    # Captures came from a real machine; only documentation addresses may remain.
    import ipaddress
    import re

    from .jsrun import FIXTURES

    allowed = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24",
                                                  "203.0.113.0/24", "127.0.0.0/8")]  # fmt: skip
    found = set()
    for path in FIXTURES.glob("*.txt"):
        for text in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", path.read_text(encoding="utf-8")):
            try:
                address = ipaddress.ip_address(text)
            except ValueError:
                continue
            if not any(address in network for network in allowed):
                found.add((path.name, text))
    assert found == set()


@pytest.mark.parametrize(
    ("site", "text", "expected"),
    [
        ("fingerprint", "Search for today's flights\nPrague to Rome  $429  Book", "pass"),
        (
            "creepjs",
            "12.10ms\n429.10ms\n0% like headless: x\n0% headless: y\n0% stealth: z",
            "pass",
        ),
        ("creepjs", "429 Too Many Requests", "blocked"),
        ("creepjs", "Error 429", "blocked"),
        ("creepjs", "Rate limit exceeded. Try again later.", "blocked"),
    ],
)
def test_number_429_alone_is_not_a_rate_limit(site, text, expected):
    assert verdict(site, text)["verdict"] == expected


def test_rate_limit_title_is_blocked():
    assert verdict("creepjs", "", title="429 Too Many Requests")["verdict"] == "blocked"


def test_fingerprint_playground_reports_browser():
    r = verdict("fingerprint-playground", fixture("fingerprint-playground-flagged.txt"))
    assert r["details"]["browser_name"] == "Chrome"
    assert r["details"]["browser_major_version"] == "155"


NEW_SITES = {
    "apivoid": ("site-apivoid.txt", {"risk_score": 0, "tampered": False, "rules": 0}),
    "donutbrowser": ("site-donutbrowser.txt", {"bot_score": 0, "flagged": 0}),
    "cleantalk": ("site-cleantalk.txt", {"human_score": 100, "flagged": 0}),
    "pixelscan-bot": ("site-pixelscan-bot.txt", {"statement": "human"}),
    "recaptcha-google": ("site-recaptcha-google.txt", {"score": 0.9}),
    "recaptcha-2captcha": ("site-recaptcha-2captcha.txt", {"score": 0.7}),
    "turnstile-capskip": ("site-turnstile-capskip.txt", {"success": True}),
}


@pytest.mark.parametrize("site", sorted(NEW_SITES))
def test_new_sites_pass_on_captured_stock_chrome(site):
    name, details = NEW_SITES[site]
    assert verdict(site, fixture(name)) == {"verdict": "pass", "details": details}


@pytest.mark.parametrize(
    ("site", "text"),
    [
        ("apivoid", "7\nRISK SCORE\n Likely Bot\n Tampered: Yes\n Triggered rules: 3"),
        ("donutbrowser", "Looks automated\nBot score\n85 / 100\nChecks run\n11\nFlagged\n4"),
        ("cleantalk", "Human Score\n20\nLooks Like a Bot\nSignals Flagged: 5"),
        ("pixelscan-bot", "Bot Detection Test\nYou're Definitely a Bot\nRestart"),
        ("recaptcha-google", 'Received response\n{\n  "success": true,\n  "score": 0.1,\n}'),
        ("recaptcha-2captcha", 'Captcha is passed successfully!\n{\n  "score": 0.3,\n}'),
        ("turnstile-capskip", "VERIFICATION RESPONSE\nSuccess\nfalse\nAction"),
    ],
)
def test_new_sites_fail_when_the_page_says_so(site, text):
    assert verdict(site, text)["verdict"] == "fail"


def test_2captcha_ignores_the_php_sample_score():
    sample = "Check\n$data = array(\n        'score'   => 0.9,\n);"
    assert verdict("recaptcha-2captcha", sample)["verdict"] == "unknown"


def test_2captcha_site_clicks_check():
    assert SITES["recaptcha-2captcha"].click_button == "Check"
