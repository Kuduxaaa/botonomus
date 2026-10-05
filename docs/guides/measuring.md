# Measuring: experiments, API traces and consistency checks

Three tools answer the questions a single detection run cannot:

| Question | Tool |
|---|---|
| Does configuration A pass site S more often than configuration B? | `botonomus experiment` |
| Which browser values does a site read, and which differ between two browsers? | `botonomus trace` + `botonomus trace-diff` |
| Is a persona stable and identical in every context, without touching the network? | `botonomus consistency` |

Everything here describes one machine, network, browser build and date. Nothing proves a session is undetectable.

## Verdicts: `pass`, `fail`, `unknown`, `blocked`

Every catalogue extractor first checks whether the page refused the visit. A rate-limit page (HTTP 429, "too many requests") or a challenge interstitial ("Just a moment...") is **`blocked`**: the site made no bot decision, so the run counts neither as a pass nor as a failure. Sites whose test *is* the challenge (`nowsecure`, `turnstile`) report a lingering challenge as `fail` instead.

Repeating a noisy site from one IP quickly gets you rate-limited. Before `blocked` existed, those pages were easy to misread as passes; rotate proxies (below) and read `blocked` counts.

## A/B experiments: `botonomus experiment`

An experiment file names sites, a run count and any number of **arms** (browser configurations):

```toml
# examples/experiment.toml
sites = ["fingerprint", "fingerprint-playground", "creepjs"]
runs = 10

[arms.chrome]
browser = "chrome"
persona = "off"

[arms.bn]
executable = 'C:\path\to\Botonomus Chromium\chrome.exe'
browser = "botonomus"
persona = "off"

[arms.bn-persona]
executable = 'C:\path\to\Botonomus Chromium\chrome.exe'
browser = "botonomus"
persona = "auto"
geoip = true
```

```bash
botonomus experiment examples/experiment.toml --proxies proxies.txt --seed 1
```

Arm keys map onto `BrowserConfig`: `browser`, `executable`, `persona` (`"auto"`, `"off"` or a seed), `extra_args`, `headless`, `driver`, `locale`, `timezone`, `geoip`, `humanize`, `allow_timezone_mismatch`. Unknown keys, unknown sites and invalid combinations (for example `geoip` without proxies) are rejected before any browser starts.

How it runs:

- Visits are scheduled **round by round**: each round holds every (arm, site) pair once, in shuffled order, so arms share network conditions over time.
- Visits run **one at a time** with a **fresh profile** each.
- With `--proxies`, each visit takes the **next proxy** in the list (round-robin). Reports name proxies as `host:port` only.
- `--settle` overrides each site's wait; `--no-interact` skips pointer movement.

Output (`artifacts/experiments/<UTC time>/` or `--output`): `report.json` (every visit, arm options, per-arm environment) and `report.md`, one table per site:

| arm | runs | pass | fail | blocked | unknown | errors | pass rate | 95% CI |
|---|---|---|---|---|---|---|---|---|
| chrome | 10 | 10 | 0 | 0 | 0 | 0 | 100% | 72%-100% |
| bn | 10 | 2 | 8 | 0 | 0 | 0 | 20% | 6%-51% |

The pass rate and its **Wilson 95 % interval** use decided runs (`pass + fail`) only. Overlapping intervals do not establish a difference; collect more runs before concluding anything. A handful of runs on a noisy site proves little: on 2026-10-04, three unproxied runs of one configuration on demo.fingerprint.com gave 2/3 passes that turned out to be rate-limit pages.

From Python: `botonomus.diagnostics.load_spec`, `run_experiment`.

## Fingerprinting API traces: `botonomus trace` and `trace-diff`

```bash
botonomus trace https://demo.fingerprint.com/playground --browser chrome --persona off \
    --proxy socks5://... --output traces/chrome.json
botonomus trace https://demo.fingerprint.com/playground --browser botonomus --persona off \
    --proxy socks5://... --output traces/bn.json
botonomus trace-diff traces/chrome.json traces/bn.json
```

`trace` hooks about 80 fingerprinting getters and methods (navigator, screen, canvas 2D and WebGL readback, WebGPU, audio, Intl, fonts, storage, media and EME, speech, permissions, WebRTC, performance memory) in the page, its same-process frames and out-of-process iframes. It records each distinct call once, with its arguments and its value. Long strings and buffers are stored as `h:<hash>:<length>`. `trace-diff` lists reads whose values differ, then reads only one browser made.

!!! warning "The tracer is visible to the page"
    The hooks run in the page's main world. A site can notice them, and may behave differently because of them. Use traces only to compare two browsers traced the same way, never in real sessions.

Limits:

- **Workers are not traced.** Auto-attaching a dedicated worker while the page's getters are hooked stops Chrome 154/155's renderer. Use `botonomus consistency` to compare worker values.
- **Only hooked calls are recorded, not drawing state.** A site that draws random colours and reads them back shows a "different" `getImageData` value on every visit, in every browser. Re-trace before believing a single difference, and check whether the inputs (`fillStyle`, text) were random.
- Traced sessions run with `--disable-features=LocalNetworkAccessChecks` so records can reach the loopback collector. `botonomus trace` adds it for you; from Python, `trace_flags(config.extra_args)` merges it into an existing `--disable-features`.
- Hooks keep each function's native `name` and `length`, and the tracer ignores its own reads while summarizing values. `Function.prototype.toString` is masked only within each realm, so a cross-frame check can still see a wrapper.

## Consistency checks: `botonomus consistency`

```bash
botonomus consistency --browser chrome --persona off
botonomus consistency --browser botonomus --persona 12345 --json
```

A loopback page, with no third-party traffic, runs these checks. The command exits with code 1 when any fails.

| Check | Passes when |
|---|---|
| `canvas-stable` | The same 2D drawing reads back identically twice (`toDataURL` and `getImageData`) |
| `canvas-solid` | A solid fill reads back exactly (noise must leave flat colours alone) |
| `webgl-stable` | The same WebGL draw reads back identically twice |
| `audio-stable` | The same `OfflineAudioContext` render is identical twice |
| `contexts-agree` | Page, iframe, dedicated, shared and service worker all report, and their user agent, UA-CH brands, `hardwareConcurrency`, `deviceMemory`, time zone and languages match |
| `media` | H.264 `canPlayType` is `"probably"`, and Widevine is available when UA-CH claims Google Chrome |
| `voices` | At least one speech voice (Google network voices are reported, not required: Chrome lists them only after a public page has loaded) |
| `headless-ua` | No "Headless" in the user agent or brands |
| `screen` | Outer size >= inner size, `availHeight <= height`, and CSS `device-width`/`device-height` match `screen.width/height`. With a persona screen: JS reports exactly the persona's size, `availHeight` is height minus the taskbar, and the window fits the work area |
| `gpu-real` | WebGL exists and runs on a real GPU: not WARP ("Microsoft Basic Render Driver"), SwiftShader, llvmpipe or lavapipe, which only GPU-less servers show |
| `audio-device` | `AudioContext.sampleRate` is 48000 and at least one `audiooutput` device exists (servers without audio fall back to a fake 44.1 kHz device) |
| `visibility` | The page is visible and `requestAnimationFrame` runs at 30+ frames per second (a locked or disconnected session stops frames) |
| `touch` | `navigator.maxTouchPoints` is 0, as on a desktop (RDP touch redirection reports many) |
| `notification` | `Notification.permission` is `default` or `granted` (off-the-record contexts deny it without asking) |
| `accept-header` | The server saw the page's and an image's `Accept` headers, and neither advertises `image/jxl` while UA-CH claims Google Chrome (Chrome stable does not support JPEG XL; a Chromium build with it enabled does) |

From Python: `botonomus.diagnostics.consistency.run_consistency(config)` returns a `ConsistencyReport`.
