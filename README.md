# Botonomus

[![CI](https://github.com/Kuduxaaa/botonomus/actions/workflows/ci.yml/badge.svg)](https://github.com/Kuduxaaa/botonomus/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)

Botonomus is an async Python SDK for browser automation that looks like an ordinary browser. It launches real Google Chrome, or the Botonomus Chromium build, as a normal process with its own profile, then attaches a native Chrome DevTools Protocol (CDP) driver that never sends `Runtime.enable` and does its DOM work in an isolated world that page scripts cannot see. Fingerprint personas (hardware, time zone, canvas/WebGL/audio noise) are applied inside Botonomus Chromium at the C++ level from command-line switches. Nothing is spoofed in JavaScript. Around that sit a bounded concurrency pool, persistent profiles with cross-process locks, authenticated proxies, geo consistency, human-like input and a reproducible detection runner.

Botonomus does not claim to be undetectable. The [measured results](#measured-results) below show what was checked, when and under which conditions.

## Install

```bash
pip install "git+https://github.com/Kuduxaaa/botonomus"
```

Not on PyPI yet. Requires Python 3.12+ and an installed Google Chrome (stable channel). Windows is the most-tested platform; Linux servers are supported with a virtual display (see [Linux servers](#linux-servers)). No browser is downloaded automatically.

Optional drivers (the default `native` driver needs neither):

```bash
pip install "botonomus[patchright] @ git+https://github.com/Kuduxaaa/botonomus"   # Patchright, a Playwright fork without Runtime.enable
pip install "botonomus[playwright] @ git+https://github.com/Kuduxaaa/botonomus"   # stock Playwright
```

## Quickstart

```python
import asyncio
from botonomus import Botonomus


async def main():
    async with Botonomus(max_instances=5) as bot:
        async with bot.open(profile="acct-01") as session:
            await session.page.goto("https://example.com")
            print(await session.page.title())


asyncio.run(main())
```

The browser opens visibly, the profile in `.botonomus/profiles/acct-01` keeps its cookies and storage, and the browser closes when the `async with` block exits.

## Why Botonomus

A comparison of approaches, limited to properties that can be checked from the code or the vendors' public documentation. Closed anti-detect browsers vary; the column describes the common pattern.

| | Stock Playwright | Patchright | Camoufox | Closed anti-detect browsers | Botonomus |
|---|---|---|---|---|---|
| Browser engine | Bundled Chromium / Chrome for Testing, or system Chrome | Same as Playwright | Patched Firefox | Patched Chromium (usually) | Real Google Chrome, or Botonomus Chromium |
| `Runtime.enable` sent | Yes | No | n/a (Juggler protocol) | Varies | No |
| `navigator.webdriver` on default launch | `true` | `false` | `false` | `false` | `false` |
| Fingerprint changes applied in | n/a | n/a | C++ (Firefox) | Engine and/or injected JS | C++ (Botonomus Chromium only) |
| JavaScript fingerprint spoofing | No | No | No | Often | Never |
| Driver dependencies | Node.js driver process | Node.js driver process | Playwright | Vendor app / API | Python stdlib (native driver) |
| Concurrency pool, persistent locked profiles | Build it yourself | Build it yourself | Build it yourself | Usually, in a GUI | Built in |
| Source licence | Apache-2.0 | Apache-2.0 | MPL-2.0 | Proprietary | MIT (SDK) |

With stock Chrome, Botonomus offers no fingerprint diversity: every session presents the host's real hardware. Personas need Botonomus Chromium.

## Features

### Concurrency pool

`max_instances` caps simultaneous browsers. Extra `open()` requests wait; cancelling a waiting request does not consume capacity.

```python
async with Botonomus(max_instances=10) as bot:

    async def work(n: int) -> str:
        async with bot.open(profile=f"worker-{n}") as session:
            await session.page.goto("https://example.com")
            return await session.page.title()

    titles = await asyncio.gather(*(work(i) for i in range(40)))
```

Each browser is a full process, so measure your machine (`botonomus benchmark`) before choosing 20, 40 or 80. A manager belongs to one event loop and can be entered once. Do not hold more nested sessions than the limit: the inner request would wait for the outer slot forever.

### Profiles

A profile is a directory under `BrowserConfig.profile_root` that keeps cookies, storage and history between runs. Names are 1-64 ASCII letters, digits, `_` or `-`, start with a letter or digit, and are lowercased. A cross-process file lock prevents two sessions, in any process, from using the same profile (`ProfileInUseError`).

```python
from pathlib import Path
from botonomus.profiles import list_profiles, remove_profile, warm_up

for info in list_profiles(Path(".botonomus/profiles")):
    print(info.name, info.in_use, info.modified)

# Opt-in: browse common sites humanly so a fresh profile gains history.
report = await warm_up(session.page, duration=120)
```

Use a dedicated profile root, never your everyday Chrome profile. Profiles hold sensitive browsing state.

### Proxies

```python
from botonomus import BrowserConfig

config = BrowserConfig(proxy="http://user:pass@proxy.example:8080")  # http, https or socks5
```

- Proxies without credentials go straight to Chrome's `--proxy-server`.
- With credentials, Botonomus starts a loopback SOCKS5 forwarder per browser and tunnels each connection through the upstream (HTTP `CONNECT` or SOCKS5 auth). Chrome never sees the credentials, no auth prompt is answered over CDP, and hostnames resolve at the proxy. Credentials never appear in command lines, `repr()` or logs.
- Any proxy also sets `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`, so WebRTC cannot reveal the direct address.

Check a list of proxies (exit IP, country, data-centre flag) before use:

```bash
botonomus proxy-check proxies.txt --parallel 16
```

### Geo consistency

`geoip=True` looks up the proxy's exit through the proxy (ip-api.com, then ipinfo.io over TLS), caches it per proxy for an hour, and aligns the browser locale and time zone with it before launch.

```python
config = BrowserConfig(proxy="socks5://user:pass@proxy.example:1080", geoip=True)
async with Botonomus(config=config) as bot, bot.open(profile="de-01") as session:
    print(session.exit.country_code, session.exit.timezone)
```

- Locale comes from the exit country (`--lang`, `--accept-lang`) unless you set `locale`.
- Botonomus Chromium presents the exit's time zone with `--bn-timezone`.
- Stock Chrome always uses the host time zone. If its UTC offset differs from the exit's, the launch raises `GeoMismatchError` rather than ship a contradiction. Pass `allow_timezone_mismatch=True` to accept the risk.
- If every lookup fails, the launch raises `GeoLookupError`.

### Personas (Botonomus Chromium)

A persona is a seeded, internally consistent identity: `navigator.hardwareConcurrency`, `navigator.deviceMemory`, time zone, an optional WebGL vendor/renderer override, and a seed for canvas, WebGL readback and audio noise. Values follow weighted real-world distributions and never exceed the host's hardware. The C++ patches derive per-site noise keys from the seed and the top-level site, so a profile is stable per site and unlinkable across profiles.

```python
from botonomus import BrowserConfig
from botonomus.fingerprint import HostInfo, Persona

BrowserConfig(persona="auto")  # default: stable seed per profile on Botonomus Chromium
BrowserConfig(persona="off")  # no persona
BrowserConfig(persona=12345)  # explicit 64-bit seed (requires Botonomus Chromium)

host = HostInfo(logical_cpus=16, memory_gb=32.0, platform="win32")
Persona.from_seed(42, host).to_switches()
# ('--bn-device-memory=16', '--bn-hardware-concurrency=12', '--bn-seed=000000000000002a')
```

Personas require Botonomus Chromium, a separately licensed Chromium build. With stock Chrome, `persona="auto"` does nothing and an explicit seed or `Persona` raises `PersonaUnsupportedError`; there is no JavaScript fallback. Builds are installed with `botonomus.browser.install()`, which verifies an Ed25519-signed manifest and the archive's SHA-256 before unpacking (see [Personas](docs/guides/personas.md)). `botonomus install` does the same from the command line. The release host and signing key are not yet live, so installs cannot complete until the first Botonomus Chromium release.

### Linux servers

Headed Chrome on a virtual X display behaves like a desktop browser (real window and screen geometry, never "headless"), which is the usual way to run many visible browsers on a server. On Linux, when no `DISPLAY` is set, Botonomus starts one Xvfb screen (1920x1080) per manager and runs every headed browser on it (when neither `DISPLAY` nor `WAYLAND_DISPLAY` is set; headless sessions never use it):

```bash
sudo apt-get install -y xvfb          # plus Google Chrome: https://www.google.com/chrome/
```

```python
BrowserConfig()  # virtual_display=None: automatic on Linux without DISPLAY
BrowserConfig(virtual_display=True)  # always (Linux only)
BrowserConfig(virtual_display=False)  # never
```

In containers Botonomus adds `--disable-dev-shm-usage` when `/dev/shm` is under 512 MB (Docker's default is 64 MB) and `--no-sandbox` only when running as root. Pages cannot see the first; the second makes Chrome show an "unsupported command-line flag" bar that shrinks the viewport, so run as a non-root user. Use a machine with a GPU for WebGL-sensitive sites: without one, WebGL is missing or software-rendered, which detection services treat as a server. Any local user can connect to the Xvfb display, so use it on single-user machines.

Every Chrome on one machine presents the same device fingerprint. Spread large fleets over several machines, or use Botonomus Chromium personas.

### Human-like input

`humanize=True` wraps `session.page` in a `HumanPage`: `click`, `fill`, `type`, `press`, `hover`, `scroll`, and the same methods on locators, move the pointer along eased Bézier paths with Fitts's-law timing, hold buttons briefly, and type key by key with a lognormal rhythm, occasional neighbouring-key slips that are corrected, and longer pauses after words and punctuation. Elements are waited on until visible, enabled and stable.

```python
from botonomus import BrowserConfig, HumanConfig

config = BrowserConfig(humanize=HumanConfig.preset("careful"))  # or True, "default", "fast"
async with Botonomus(config=config) as bot, bot.open(profile="p1") as session:
    page = session.page
    await page.goto("https://example.com/login")
    await page.fill("#email", "user@example.com")
    await page.fill("#password", "correct horse", sensitive=True)  # no slips in secrets
    await page.press("Enter")
    await page.raw.evaluate("document.title")  # .raw bypasses humanizing
```

All events are trusted browser input sent through CDP `Input`; nothing is injected into the page. This shapes timing and trajectories only. Behavioural classifiers can still tell automation apart.

### Detection runner

```bash
botonomus detect --runs 3
```

Visits 13 public bot-detection pages (deviceandbrowserinfo, sannysoft, browserscan, fingerprint, fingerprint-playground, creepjs, incolumitas, nowsecure, recaptcha-score, pixelscan, fingerprint-scan, rebrowser, turnstile), each `--runs` times with a fresh profile per run, and writes screenshots, page text and `report.json` with per-run verdicts, pass rates, error categories and the environment (date, OS, executable SHA-256, browser version, driver, proxy scheme only). Verdicts come only from per-site extractor scripts; anything else is `unknown`, and rate-limit or challenge pages are `blocked`. Add your own sites from Python with `botonomus.diagnostics.DetectionSite` and `run_detection`.

### Measuring

```bash
botonomus experiment examples/experiment.toml --proxies proxies.txt   # A/B with 95% intervals
botonomus trace URL --output a.json && botonomus trace-diff a.json b.json  # which values differ
botonomus consistency --browser botonomus --persona 12345            # local, no network
```

`experiment` interleaves browser configurations over the same sites with a fresh profile and the next proxy per visit, and reports pass rates with Wilson 95 % intervals. `trace` records which fingerprinting APIs a page reads and what they return (diagnostic only: the hooks are visible to the page). `consistency` checks readback stability and identity agreement across page, iframe and workers. See [Measuring](docs/guides/measuring.md).

## CLI

Installing the package adds `botonomus` (also `python -m botonomus.cli`).

| Command | Purpose |
|---|---|
| `botonomus info [--browser B] [--json]` | Versions, platform, discovered Chrome, installed Botonomus Chromium, which executable a launch would use, available drivers |
| `botonomus install [--version V] [--manifest-url URL] [--json]` | Download, verify and install Botonomus Chromium (progress on stderr) |
| `botonomus uninstall VERSION` / `botonomus binaries [--json]` | Remove / list installed Botonomus Chromium builds |
| `botonomus open --profile P [--url U]` | Visible session until Enter or Ctrl+C |
| `botonomus probe [--normal \| --baseline FILE \| --serve]` | Local probe snapshot, optionally compared with an ordinary launch |
| `botonomus detect [--sites ...] [--runs N] [--parallel N]` | Public detection pages, aggregated report |
| `botonomus experiment ARMS.toml [--runs N] [--proxies FILE] [--seed S]` | Interleaved A/B runs with Wilson 95 % intervals (`report.json`, `report.md`) |
| `botonomus trace URL --output FILE` / `botonomus trace-diff A B` | Fingerprinting API reads of a page, and the values two browsers report differently |
| `botonomus consistency` | Local readback and cross-context identity checks; exit 1 on any failure |
| `botonomus proxy-check FILE [--parallel N] [--timeout S]` | Exit IP and location per proxy; never prints credentials |
| `botonomus profiles list \| remove NAME [--root DIR]` | List profiles; remove refuses profiles in use |
| `botonomus profiles warmup NAME [--duration S] [--sites URL ...]` | Browse common sites humanly so a profile accumulates history |
| `botonomus benchmark --levels 1,2,5` | Held-open concurrency on a local page |

`open`, `probe`, `detect`, `trace`, `consistency`, `profiles warmup` and `benchmark` also accept `--executable`, `--browser {auto,botonomus,chrome}`, `--persona {auto,off,SEED}`, `--proxy`, `--geoip`, `--timezone ZONE`, `--allow-timezone-mismatch`, `--locale`, `--headless` and `--driver`; `open`, `detect` and `profiles warmup` also accept `--humanize {off,default,careful,fast}`. Exit codes: 0 success, 1 runtime failure, 2 usage or configuration error.

## Configuration reference

`BrowserConfig` is a frozen dataclass validated on construction (`ConfigurationError`). Pass it as `Botonomus(config=...)`.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `profile_root` | `Path` | `.botonomus/profiles` | Directory with one subdirectory per profile; resolved to an absolute path |
| `executable_path` | `Path \| None` | `None` | Browser executable; `None` chooses according to `browser` |
| `headless` | `bool` | `False` | Run without a window (visible is the validated mode) |
| `launch_timeout` | `float` | `30.0` | Seconds for the process to start and accept CDP |
| `close_timeout` | `float` | `10.0` | Seconds for a graceful close before termination |
| `locale` | `str \| None` | `None` | BCP 47 tag applied with `--lang` and `--accept-lang` |
| `proxy` | `str \| None` | `None` | `http`, `https` or `socks5` URL, credentials allowed; hidden from `repr` |
| `extra_args` | `tuple[str, ...]` | `()` | Extra `--flag[=value]` for every session; owned flags are rejected |
| `driver` | `"native" \| "patchright" \| "playwright"` | `"native"` | Page driver attached over CDP |
| `render_when_occluded` | `bool` | `True` | Keep rendering covered windows (avoids stalled input on Windows) |
| `browser` | `"auto" \| "botonomus" \| "chrome"` | `"auto"` | With no `executable_path`: prefer Botonomus Chromium, require it, or use Chrome |
| `persona` | `"auto" \| "off" \| int \| Persona` | `"auto"` | Fingerprint identity (Botonomus Chromium) |
| `geoip` | `bool` | `False` | Align locale and time zone with the proxy exit; requires `proxy` |
| `timezone` | `str \| None` | `None` | IANA zone to present; overrides the exit's zone |
| `allow_timezone_mismatch` | `bool` | `False` | Launch Chrome even if the host zone differs from the wanted zone |
| `humanize` | `bool \| HumanConfig` | `False` | Wrap `session.page` in `HumanPage` |
| `virtual_display` | `bool \| None` | `None` | Xvfb for headed browsers on Linux: `None` when no display is set, `True` always, `False` never |
| `persona_switches` | `tuple[str, ...]` | `()` | Internal: filled in per launch by the manager; leave empty |

Flags Botonomus owns and rejects in `extra_args` or `open(..., args=...)`: `--user-data-dir`, `--remote-debugging-port`, `--remote-debugging-address`, `--remote-debugging-pipe`, `--headless`, `--enable-automation`, `--proxy-server`, `--lang`, `--accept-lang`, `--user-agent`, and every `--bn-*` persona switch.

## Errors

Every SDK error derives from `botonomus.BotonomusError`; the low-level cause is kept as `__cause__`, and messages never contain credentials, cookies, page contents or URLs.

| Error | Raised when |
|---|---|
| `ConfigurationError` (also a `ValueError`) | Invalid configuration, argument or profile name |
| `PersonaUnsupportedError` (a `ConfigurationError`) | Explicit persona requested on stock Chrome |
| `BrowserUnavailableError` | No browser executable found |
| `BinaryNotInstalledError` (a `BrowserUnavailableError`) | `browser="botonomus"` but no build installed |
| `BinaryDownloadError` / `BinaryVerificationError` | Botonomus Chromium download or signature/hash check failed |
| `ProfileInUseError` | Another session, in any process, holds the profile |
| `ManagerClosedError` | The manager is not open for new sessions |
| `BrowserStartupError` | Process, driver, context or page could not start |
| `BrowserCleanupError` | Shutdown could not be confirmed; profile and slot stay reserved |
| `GeoLookupError` | `geoip=True` and every exit lookup failed |
| `GeoMismatchError` | Chrome cannot present the required time zone |

`PersonaUnsupportedError`, `GeoLookupError` and `GeoMismatchError` are imported from `botonomus.errors`; the others are also exported from `botonomus`. The native driver's own page errors (`botonomus.cdp.TimeoutError_`, `NavigationError`, `EvaluationError`, `ProtocolError`) are not `BotonomusError` subclasses; `TimeoutError_` subclasses the built-in `TimeoutError`.

## Measured results

Measured 2026-10-04, Windows 11, Google Chrome 154 stable through the `native` driver, `--persona off`, fresh profile per visit, rotating datacenter proxies (one per visit), with `botonomus experiment` and `botonomus consistency`:

| Check | Result |
|---|---|
| demo.fingerprint.com scraping demo (bot and tampering decision) | **3/3 passed** (n=3; rate-limited visits reported as `blocked`, not counted) |
| `botonomus consistency` (readback stability, page/iframe/worker/shared/service-worker agreement, media, voices, geometry) | **all 9 checks pass** |
| TLS JA4 and HTTP/2 fingerprint | identical to a manual Chrome launch |

Unproxied repeats of the same site from one IP were rate-limited within minutes; earlier tooling misread those pages as passes, which is why `blocked` exists.

Measured 2026-10-03, Windows 11, Google Chrome 154 stable, visible window, residential ISP, fresh profile:

| Check | Ordinary Chrome (no automation) | Botonomus `native` | `playwright` driver |
|---|---|---|---|
| Local probe, all observations | - | identical to ordinary launch | - |
| deviceandbrowserinfo.com true flags | 0 | **0** | 4 (`isBot`, CDP, CDP-in-worker, timing) |
| bot.sannysoft.com failed rows | - | **0** | timed out |
| browserscan.net bot detection | - | **Normal** | - |
| FingerprintJS scraping demo | - | **data served** | - |
| Cloudflare challenge (nowsecure.nl) | - | **passed** | - |
| reCAPTCHA v3 (antcpt.com) | 0.1 | 0.1 | - |
| incolumitas legacy `WEBDRIVER` | FAIL | FAIL | FAIL |

On the last two rows ordinary Chrome scores the same. The legacy test flags `'webdriver' in navigator`, which is true in every current Chrome. reCAPTCHA v3 scored this IP and a history-free profile at 0.1 with or without automation, so improving it is a matter of IP reputation and profile age, not the browser.

Earlier local measurement on the same machine with Chromium 153.0.8010.12: default Playwright launch reported `navigator.webdriver` `true`, Botonomus `false`; an ordinary launch and a Botonomus launch matched on every captured probe field except viewport height (929x917 ordinary, 929x861 attached).

Load: the initial `botonomus benchmark` run completed 1 session in 1.125 s and 2 simultaneously active sessions in 3.031 s, including local navigation, with zero failures. Levels 10-80 have not been benchmarked, and no capacity or throughput figure is promised.

These results apply to one machine, network, browser build and date. They are not a guarantee that any site will treat a session as human: network, account and behavioural signals remain. Reproduce them on your own setup with `botonomus detect` and `botonomus probe --normal`. Measurements of Botonomus Chromium will be published separately with the same method.

## Responsible use

Botonomus is for authorised work: testing and monitoring your own sites, QA, research, accessibility checks, and automation the target site permits. Respect each site's terms of service, robots directives and rate limits, and the laws that apply to you, including data-protection and computer-misuse laws. Using Botonomus for credential stuffing, account takeover, fake account creation, ad or payment fraud, ticket scalping, evading bans, or any other abuse is prohibited. You are responsible for how you use it.

## Documentation and project

- Full documentation: the MkDocs site under [`docs/`](docs/README.md) (`pip install -e ".[docs]"`, then `mkdocs serve`).
- [CHANGELOG](CHANGELOG.md), [CONTRIBUTING](CONTRIBUTING.md), [SECURITY](SECURITY.md), [CODE_OF_CONDUCT](CODE_OF_CONDUCT.md).

## Licence

The `botonomus` Python SDK is released under the [MIT licence](LICENSE). The Botonomus Chromium binary is distributed separately under its own licence and is not covered by the MIT licence of this repository.
