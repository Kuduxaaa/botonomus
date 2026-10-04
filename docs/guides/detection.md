# Detection runner and probes

Botonomus ships two kinds of evidence about what a page can see: a **local probe** that compares a Botonomus launch with an ordinary one, and a **detection runner** that visits public bot-detection pages. Both describe one machine, network, browser build and date. Neither proves that a session is undetectable.

## Public detection pages: `botonomus detect`

```bash
botonomus detect --runs 3
botonomus detect --sites sannysoft deviceandbrowserinfo --runs 5 --driver patchright
botonomus detect --proxy socks5://user:pass@proxy.example:1080 --json
```

!!! warning
    `detect` contacts third-party websites. Run it deliberately, not in CI.

Built-in sites, in default order:

| Name | Page | What it reports |
|---|---|---|
| `deviceandbrowserinfo` | deviceandbrowserinfo.com/are_you_a_bot | Bot/human statement and the `true` flags behind it |
| `sannysoft` | bot.sannysoft.com | Classic headless and webdriver property table |
| `browserscan` | browserscan.net/bot-detection | Bot detection summary (Normal / Robot) |
| `fingerprint` | demo.fingerprint.com/web-scraping | Fingerprint Pro bot and tampering decision |
| `fingerprint-playground` | demo.fingerprint.com/playground | Smart Signals: tampering, anti-detect browser, suspect score, ML scores |
| `creepjs` | abrahamjuliot.github.io/creepjs | Headless, like-headless and stealth percentages (fail when headless or stealth > 0 %) |
| `incolumitas` | bot.incolumitas.com | OK/FAIL test tables; the spec-level `WEBDRIVER` row, which stock Chrome also fails, is ignored |
| `nowsecure` | nowsecure.nl | Cloudflare challenge page |
| `recaptcha-score` | antcpt.com/score_detector | reCAPTCHA v3 score (pass >= 0.7, fail <= 0.3) |
| `pixelscan` | pixelscan.net/fingerprint-check | Fingerprint consistency and masking |
| `fingerprint-scan` | fingerprint-scan.com | Bot score out of 100 (pass <= 30, fail >= 60) |
| `rebrowser` | bot-detector.rebrowser.net | Automation leak tests; any red row fails |
| `turnstile` | nopecha.com/demo/turnstile | Cloudflare Turnstile demo |
| `apivoid` | apivoid.com/tools/bot-detection-test | Server-side risk score and tampering verdict (pass <= 30, fail >= 70) |
| `donutbrowser` | donutbrowser.com/tools/bot-detection | Automation and spoofing checks, bot score out of 100 |
| `cleantalk` | cleantalk.org/am-i-a-bot | Human score out of 100 (pass >= 80, fail < 50) |
| `pixelscan-bot` | pixelscan.net/bot-check | Navigator, webdriver, CDP and user-agent checks |
| `recaptcha-google` | recaptcha-demo.appspot.com | Google's reCAPTCHA v3 demo score (pass >= 0.7, fail <= 0.3) |
| `recaptcha-2captcha` | 2captcha.com/demo/recaptcha-v3 | reCAPTCHA v3 score after clicking Check |
| `turnstile-capskip` | capskip.com Turnstile demo | Cloudflare Turnstile widget verification |

Pages that compute their result on demand set `DetectionSite(click_button="...")`: the runner clicks that button (trusted input) after loading and before waiting for `ready`.

How a run works:

- Each site is visited `--runs` times, one run after another; different sites run concurrently up to `--parallel` (default 2).
- Each run uses a fresh profile unless `--reuse-profiles` keeps one profile per site.
- After loading, the runner moves and scrolls with `Human` (skip with `--no-interact`), waits for the page's result, and captures a screenshot and page text.
- **Verdicts come only from per-site extractor scripts.** Results an extractor cannot decide are recorded as `unknown`; nothing is inferred. A rate-limit page or challenge interstitial is `blocked` and counts neither as a pass nor as a failure (see [Measuring](measuring.md)).
- Failures are categorised (timeouts, navigation, startup, ...) and never stop the other runs.

Output goes to `artifacts/detection/<UTC time>/` (or `--output`): screenshots, page text, and `report.json` with per-run verdicts and details, per-site pass/fail/blocked/unknown/error counts and pass rate, error categories, and the environment: capture date, OS, executable name and SHA-256, `Browser.getVersion` product, driver, Botonomus version and proxy **scheme only**.

!!! note
    The environment's executable name and SHA-256 are resolved from `--executable` or, when it is omitted, from the discovered Google Chrome. If Botonomus Chromium is installed, sessions may launch it under the default `browser="auto"` while the report hashes Chrome. Pass `--executable` explicitly when the hash matters; `browser_product` always comes from the running browser.

Every result in the README was produced this way. To reproduce it on your setup, run `botonomus detect` with the same driver and browser and compare the reports.

## Your own detection pages

```python
import asyncio
from pathlib import Path

from botonomus import Botonomus, BrowserConfig
from botonomus.diagnostics import SITES, DetectionSite, run_detection

MY_SITE = DetectionSite(
    "my-check",
    "https://deviceandbrowserinfo.com/are_you_a_bot",
    ready="document.body.innerText.includes('You are')",
    extractor="""() => {
      const text = document.body.innerText;
      if (text.includes('You are human')) return {verdict: 'pass', details: {}};
      if (text.includes('You are a bot')) return {verdict: 'fail', details: {}};
      return {verdict: 'unknown', details: {}};
    }""",
)


async def main() -> None:
    output = Path("artifacts/detection/example")
    config = BrowserConfig(profile_root=output / "profiles")
    async with Botonomus(max_instances=2, config=config) as bot:
        report = await run_detection(bot, [SITES["sannysoft"], MY_SITE], runs=2, output=output)
    for site in report.sites:
        print(f"{site.site}: {site.passed}/{site.runs} pass, {site.errors} errors")


asyncio.run(main())
```

`ready` is a JavaScript expression polled until truthy; `extractor` is a function that returns `{"verdict": "pass" | "fail" | "unknown", "details": {...}}`. Both run in the isolated world with the native and Patchright drivers. Any other return shape is recorded as `unknown`. This example is `examples/detection_sites.py` in the repository.

## Local probe: `botonomus probe`

The probe serves a local page that records automation-relevant observations: `navigator.webdriver`, CDP console serialization side effects, `window.chrome`, injected `cdc_` or Playwright globals, plugins, notification-permission consistency, Client Hints, the WebGL renderer and window chrome size.

```bash
botonomus probe                      # Botonomus launch only
botonomus probe --normal --output artifacts/comparison
botonomus probe --serve              # serve the page for a manual baseline
botonomus probe --baseline manual-browser.json
```

- `--normal` first launches an ordinary browser **without** a debugging connection and collects the page's own report, then runs the identical page in Botonomus and compares every field. The baseline is a subprocess launch, not a claim that a human navigated. Files: `automated.json`, `baseline.json`, `comparison.json` under `--output`; `--json` prints them instead.
- `--serve` prints a launch command; open the page yourself, download the JSON with the page's button, then compare with `--baseline`.
- Diagnostic profiles are temporary.

## Reading results honestly

- Some checks fail for ordinary Chrome too. The legacy incolumitas `WEBDRIVER` test flags `'webdriver' in navigator`, which is true in every current Chrome.
- reCAPTCHA v3 scores mainly reflect IP reputation and profile history. In the published measurement, ordinary Chrome without automation scored 0.1 on the same IP and fresh profile.
- A clean report on one date says nothing about a site's next deployment. Re-run after browser updates.
