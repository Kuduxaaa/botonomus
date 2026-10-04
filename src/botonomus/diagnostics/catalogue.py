"""Built-in public detection sites and the scripts that read their verdicts.

Every extractor reports only what its page states. A shared prelude first checks
whether the page refused the visit (rate limit or a challenge interstitial) and
returns ``"blocked"``; such runs never count as passes or failures.
"""

from typing import Final

from .detection import DetectionSite

_PRELUDE = """
  const text = document.body ? (document.body.innerText || '') : '';
  const title = document.title || '';
  // Rate-limit phrasing only: a bare "429" also appears in prices and timings.
  const limited = (s) => /too many requests|rate limit(ed)?\\b/i.test(s)
    || /\\b(error|status|code)\\s*:?\\s*429\\b|"?429"?\\s*:/i.test(s);
  if ((text.length < 5000 && limited(text)) || /\\b429\\b|too many requests/i.test(title))
    return {verdict: 'blocked', details: {reason: 'rate_limited'}};
"""

_CHALLENGE = """
  if (/just a moment|attention required|checking your browser/i.test(title))
    return {verdict: 'blocked', details: {reason: 'challenge'}};
"""


def extractor(body: str, *, challenge_blocks: bool = True) -> str:
    """Wrap an extractor body with the shared blocked-page prelude.

    Args:
        body: JavaScript statements that may use ``text`` (the body's visible text)
            and ``title`` and must return ``{verdict, details}``.
        challenge_blocks: Treat a challenge interstitial title as ``"blocked"``.
            Off for sites whose test *is* the challenge.

    Returns:
        A function expression suitable for `DetectionSite.extractor`.
    """
    return "() => {" + _PRELUDE + (_CHALLENGE if challenge_blocks else "") + body + "\n}"


_SANNYSOFT = extractor("""
  const cells = [...document.querySelectorAll('td')];
  const count = (name) => cells.filter((c) => c.classList.contains(name)).length;
  const passed = count('passed'), failed = count('failed'), warn = count('warn');
  if (!passed && !failed) return {verdict: 'unknown', details: {reason: 'no result cells'}};
  return {verdict: failed ? 'fail' : 'pass', details: {passed, failed, warn}};
""")

_DEVICEANDBROWSERINFO = extractor("""
  const bot = /you are a bot/i.test(text), human = /you are (a )?human/i.test(text);
  if (bot === human) return {verdict: 'unknown', details: {}};
  const flags = [...text.matchAll(/"(\\w+)":\\s*true/g)]
    .map((m) => m[1]).filter((k) => k !== 'isBot');
  return {verdict: bot ? 'fail' : 'pass', details: {statement: bot ? 'bot' : 'human', flags}};
""")

_BROWSERSCAN = extractor("""
  const m = /Test Results:\\s*(\\w+)/i.exec(text);
  if (!m) return {verdict: 'unknown', details: {}};
  const status = m[1].toLowerCase();
  if (status === 'normal') return {verdict: 'pass', details: {status}};
  if (/abnormal|robot|bot/.test(status)) return {verdict: 'fail', details: {status}};
  return {verdict: 'unknown', details: {status}};
""")

_FINGERPRINT = extractor("""
  if (/tampering detected/i.test(text)) return {verdict: 'fail', details: {reason: 'tampering'}};
  if (/bot detected|access denied/i.test(text)) return {verdict: 'fail', details: {reason: 'bot'}};
  if (/\\$\\s?\\d/.test(text)) return {verdict: 'pass', details: {}};
  return {verdict: 'unknown', details: {}};
""")

_FINGERPRINT_PLAYGROUND = extractor("""
  const get = (key) => {
    const m = new RegExp('^\\\\s*' + key + ':\\\\s*(.+)$', 'm').exec(text);
    return m ? m[1].trim().replace(/,$/, '').replace(/^"|"$/g, '') : null;
  };
  const bool = (v) => v === null ? null : v === 'true';
  const num = (v) => v === null || isNaN(parseFloat(v)) ? null : parseFloat(v);
  const tampering = bool(get('tampering')), anti = bool(get('anti_detect_browser'));
  if (tampering === null && anti === null) return {verdict: 'unknown', details: {}};
  const bot = get('bot');
  const details = {
    tampering, anti_detect_browser: anti, bot,
    tampering_ml_score: num(get('tampering_ml_score')), suspect_score: num(get('suspect_score')),
    vpn: bool(get('vpn')), virtual_machine: bool(get('virtual_machine')),
    incognito: bool(get('incognito')), developer_tools: bool(get('developer_tools')),
    browser_name: get('browser_name'), browser_major_version: get('browser_major_version'),
  };
  const flagged = tampering || anti || (bot !== null && bot !== 'not_detected');
  return {verdict: flagged ? 'fail' : 'pass', details};
""")

_CREEPJS = extractor("""
  const pct = (label) => {
    const m = new RegExp('(\\\\d+)% ' + label + ':', 'i').exec(text);
    return m ? parseInt(m[1], 10) : null;
  };
  const headless = pct('headless'), like = pct('like headless'), stealth = pct('stealth');
  if (headless === null || stealth === null) return {verdict: 'unknown', details: {}};
  const details = {headless, like_headless: like, stealth};
  return {verdict: headless > 0 || stealth > 0 ? 'fail' : 'pass', details};
""")

# WEBDRIVER fails on stock Chrome too ('webdriver' in navigator is true by spec).
_INCOLUMITAS = extractor("""
  const ignoredKeys = ['WEBDRIVER'];
  let ok = 0, fail = 0;
  const failed = [], ignored = [];
  for (const pre of document.querySelectorAll('pre')) {
    let data;
    try { data = JSON.parse(pre.textContent); } catch (e) { continue; }
    if (!data || typeof data !== 'object') continue;
    for (const [key, value] of Object.entries(data)) {
      if (value === 'OK') ok++;
      else if (value === 'FAIL') {
        if (ignoredKeys.includes(key)) { ignored.push(key); continue; }
        fail++;
        if (failed.length < 20) failed.push(key);
      }
    }
  }
  if (!ok && !fail) return {verdict: 'unknown', details: {reason: 'no test results found'}};
  return {verdict: fail ? 'fail' : 'pass', details: {ok, fail, failed, ignored}};
""")

_NOWSECURE = extractor(
    """
  if (/you passed/i.test(text)) return {verdict: 'pass', details: {}};
  if (/just a moment|attention required|checking your browser/i.test(title))
    return {verdict: 'fail', details: {reason: 'challenge page still shown'}};
  return {verdict: 'unknown', details: {}};
""",
    challenge_blocks=False,
)

# reCAPTCHA v3 scores run 0.0-1.0; Google suggests 0.5 as a default threshold,
# so only clear ends of the range are reported as a verdict.
_ANTCPT = extractor("""
  const match = /score is:?\\s*([01](?:\\.\\d+)?)/i.exec(text);
  if (!match) return {verdict: 'unknown', details: {}};
  const score = parseFloat(match[1]);
  const verdict = score >= 0.7 ? 'pass' : score <= 0.3 ? 'fail' : 'unknown';
  return {verdict, details: {score}};
""")

_PIXELSCAN = extractor("""
  const consistent = /your browser fingerprint is consistent/i.test(text);
  const inconsistent = /your browser fingerprint is inconsistent/i.test(text);
  if (!consistent && !inconsistent) return {verdict: 'unknown', details: {}};
  const noMasking = /no masking detected/i.test(text);
  const masking = !noMasking && /masking detected/i.test(text);
  const details = {
    consistent, masking,
    proxy: /no proxy detected/i.test(text) ? false : /proxy detected/i.test(text) ? true : null,
    automated: /no automated behavior detected/i.test(text) ? false
      : /automated behavior detected/i.test(text) ? true : null,
  };
  return {verdict: inconsistent || masking || details.automated ? 'fail' : 'pass', details};
""")

_FINGERPRINT_SCAN = extractor("""
  const m = /Bot score\\s*(\\d+)\\s*\\/\\s*100/i.exec(text);
  if (!m) return {verdict: 'unknown', details: {}};
  const score = parseInt(m[1], 10);
  const verdict = score <= 30 ? 'pass' : score >= 60 ? 'fail' : 'unknown';
  return {verdict, details: {bot_score: score}};
""")

_REBROWSER = extractor("""
  const rows = text.split('\\n').map((l) => l.trim())
    .filter((l) => /^(\\u{1F7E2}|\\u{1F534}|\\u{1F7E1}|\\u26AA)/u.test(l));
  if (!rows.length) return {verdict: 'unknown', details: {}};
  const named = (mark) => rows.filter((l) => l.startsWith(mark))
    .map((l) => l.replace(/^\\S+\\s+/u, '').split(/\\s+/)[0]);
  const red = named('\\u{1F534}'), yellow = named('\\u{1F7E1}');
  return {verdict: red.length ? 'fail' : 'pass', details: {red, yellow}};
""")

_TURNSTILE = extractor(
    """
  if (document.querySelector('#success')) return {verdict: 'pass', details: {}};
  if (document.querySelector('#fail') || document.querySelector('#challenge-error'))
    return {verdict: 'fail', details: {}};
  return {verdict: 'unknown', details: {}};
""",
    challenge_blocks=False,
)

_BODY_TEXT = "(document.body ? document.body.innerText : '')"

CATALOGUE: Final[tuple[DetectionSite, ...]] = (
    DetectionSite(
        "deviceandbrowserinfo",
        "https://deviceandbrowserinfo.com/are_you_a_bot",
        ready=f"/you are (a bot|(a )?human)/i.test({_BODY_TEXT})",
        extractor=_DEVICEANDBROWSERINFO,
        description="Bot/human statement from fingerprint and CDP side-effect checks",
    ),
    DetectionSite(
        "sannysoft",
        "https://bot.sannysoft.com/",
        ready="document.querySelectorAll('td.passed, td.failed').length > 10",
        extractor=_SANNYSOFT,
        description="Classic headless and webdriver property table",
    ),
    DetectionSite(
        "browserscan",
        "https://www.browserscan.net/bot-detection",
        settle=10.0,
        extractor=_BROWSERSCAN,
        description="Bot detection summary (Normal / Robot)",
    ),
    DetectionSite(
        "fingerprint",
        "https://demo.fingerprint.com/web-scraping",
        settle=12.0,
        extractor=_FINGERPRINT,
        description="Fingerprint Pro bot and tampering decision on a scraping demo",
    ),
    DetectionSite(
        "fingerprint-playground",
        "https://demo.fingerprint.com/playground",
        settle=15.0,
        extractor=_FINGERPRINT_PLAYGROUND,
        description="Fingerprint Pro Smart Signals: tampering, anti-detect browser, scores",
    ),
    DetectionSite(
        "creepjs",
        "https://abrahamjuliot.github.io/creepjs/",
        settle=20.0,
        extractor=_CREEPJS,
        description="Headless, like-headless and stealth percentages",
    ),
    DetectionSite(
        "incolumitas",
        "https://bot.incolumitas.com/",
        settle=12.0,
        extractor=_INCOLUMITAS,
        description="OK/FAIL detection test tables (spec-level WEBDRIVER ignored)",
    ),
    DetectionSite(
        "nowsecure",
        "https://nowsecure.nl/",
        extractor=_NOWSECURE,
        description="Cloudflare challenge page",
    ),
    DetectionSite(
        "recaptcha-score",
        "https://antcpt.com/score_detector/",
        ready=f"/score is:?\\s*[01]/i.test({_BODY_TEXT})",
        extractor=_ANTCPT,
        description="reCAPTCHA v3 score (pass >= 0.7, fail <= 0.3)",
    ),
    DetectionSite(
        "pixelscan",
        "https://pixelscan.net/fingerprint-check",
        settle=20.0,
        extractor=_PIXELSCAN,
        description="Fingerprint consistency and masking",
    ),
    DetectionSite(
        "fingerprint-scan",
        "https://fingerprint-scan.com/",
        settle=15.0,
        extractor=_FINGERPRINT_SCAN,
        description="Bot score out of 100 (pass <= 30, fail >= 60)",
    ),
    DetectionSite(
        "rebrowser",
        "https://bot-detector.rebrowser.net/",
        settle=10.0,
        extractor=_REBROWSER,
        description="Automation leak tests (Runtime.enable, init scripts, UA)",
    ),
    DetectionSite(
        "turnstile",
        "https://nopecha.com/demo/turnstile",
        settle=15.0,
        extractor=_TURNSTILE,
        description="Cloudflare Turnstile demo",
    ),
)
"""Built-in public detection sites, in default run order."""

SITES: Final[dict[str, DetectionSite]] = {site.name: site for site in CATALOGUE}
"""The built-in catalogue by name."""
