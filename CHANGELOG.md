# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.1] - 2026-10-04

### Added

- **Persona screen.** `Persona.screen` and `Persona.taskbar` (`--bn-screen=WxH`, `--bn-taskbar=H`, CSS pixels): a weighted real monitor resolution no larger than the host's, divided by the host's scale so `screen x devicePixelRatio` is a real monitor size. `HostInfo` gains `screen` (physical pixels) and `scale`, detected on Windows. With a persona screen the window opens at the origin and fills the persona's work area. Needs Botonomus Chromium with patch 0012.
- `consistency` `screen` check: CSS `device-width`/`device-height` must match `screen.width/height`, and a persona screen must be what JavaScript reports, with `availHeight` = height - taskbar and the window inside the work area.

- `botonomus consistency` check `accept-header`: the loopback server records the `Accept` headers of the page and of an image, and fails when they advertise `image/jxl` while UA-CH claims Google Chrome (Chrome stable does not; the current Botonomus Chromium dev build does).

### Changed

- `Client`: the shared browser's throwaway profile now lives in the system temp directory instead of `profile_root`, so a killed process leaves nothing among your named profiles.
- `Client`: restoring an identity's `localStorage` opens its tab inside a tab slot, so it never exceeds `max_tabs`.
- Identity state on Windows: the identity directory's inherited permissions are replaced with full control for the current user (POSIX modes have no effect there).

## [0.3.0] - 2026-10-04

### Added

- **`botonomus.Client`, an httpx-like layer over one shared Chrome.** `await botonomus.get(url)` and `Client.get/post/put/patch/delete/head` load each request in a tab of an in-memory browser context: isolated cookies and storage, no profile on disk, and the browser, GPU and network processes shared. `Response` has `status`, `headers`, `content`, `text`, `json()`, `html` (rendered DOM), `cookies`, `elapsed`, `via` and `raise_for_status()`. Non-GET methods, headers and bodies are applied to the navigation request itself. Options: per-client proxy (credentials through the loopback forwarder), `max_tabs="auto"` sized from free memory and CPUs, `block` for images, fonts, media or stylesheets, `fresh_context`, `wait`, `timeout`. The existing `Botonomus` API is unchanged.
- **HTTP fast path** (`pip install "botonomus[http]"`). After a tab has loaded a host without a challenge, later requests to it go through `curl_cffi` with Chrome's TLS and HTTP/2 fingerprints, the headers the browser sent and the context's cookies (written back both ways). Challenge responses fall back to a tab; a host challenged twice stays on tabs.
- **Identities.** `client.identity(name, proxy=...)` keeps cookies, `localStorage` and the proxy in an owner-only state file and restores them into a fresh context (without contacting the site). `backend="profile"` opens the full Chrome profile for sites that need IndexedDB or service workers. One live use per identity across processes.
- **Recovery.** If the shared browser dies, the client relaunches it, recreates every context from its last known state and retries the interrupted request once.
- `client.page(url)` and `identity.page(url)`: a tab in the same context for full interaction (a `HumanPage` with `humanize`).
- `botonomus.cdp.IsolatedContext` (in-memory browser contexts with an optional proxy), `Context.connection` and `Page.main_frame_id`.
- `botonomus benchmark --mode contexts` measures one browser with a context per tab; reports now include `per_instance_bytes`. On the development machine, 6 held-open tabs used about 133 MiB each as contexts and about 224 MiB each as separate browsers.
- `botonomus.HTTPStatusError`.

### Fixed

- Native driver: an event wait (for example `goto` waiting for `load`) now fails with `TargetClosedError` as soon as its tab detaches or the connection closes, instead of hanging until the timeout.
- Native driver: a closed or detached page unregisters its event handlers, so a long-lived connection does not accumulate them.

- **Seven more detection sites:** `apivoid`, `donutbrowser`, `cleantalk`, `pixelscan-bot`, `recaptcha-google`, `recaptcha-2captcha` and `turnstile-capskip`, each with an extractor tested against a captured page.
- `DetectionSite(click_button=...)`: the runner clicks a named button after loading, for pages that compute their result on demand.

## [0.2.0] - 2026-10-04

First public release on PyPI. It includes the measurement toolkit and Linux support below, and the restructure and features listed under "Included from the 1.0 development line".

### Added

- **`botonomus experiment`.** Interleaved A/B comparison of browser configurations ("arms") from a TOML file: rounds shuffled so arms share network conditions, a fresh profile and the next proxy per visit, per-(arm, site) pass rates with Wilson 95 % intervals, `report.json` and `report.md`. Python: `botonomus.diagnostics.load_spec`, `run_experiment`.
- **`botonomus trace` and `trace-diff`.** A diagnostic tracer that records which fingerprinting APIs a page reads (about 80 getters and methods) and the values returned, in the page, same-process frames and OOPIFs, and a diff of two traces. The hooks are visible to the page; workers are not traced.
- **`botonomus consistency`.** Local checks with no third-party traffic: canvas, WebGL and audio readback stability, exact solid-colour readback, identity agreement across page, iframe and dedicated, shared and service workers, media, voices, headless markers and window geometry.
- **Linux servers.** `BrowserConfig(virtual_display=None)` runs headed browsers on one shared Xvfb screen per manager when no `DISPLAY` is set (`True` forces it, `False` disables it). In containers, `--disable-dev-shm-usage` is added when `/dev/shm` is under 512 MB and `--no-sandbox` only when running as root. New `botonomus.browser.VirtualDisplay`, `needs_virtual_display`, `container_flags`.
- **Detection catalogue.** Five new sites (`fingerprint-playground`, `pixelscan`, `fingerprint-scan`, `rebrowser`, `turnstile`); `browserscan`, `fingerprint` and `creepjs` now decide verdicts. The catalogue lives in `botonomus.diagnostics.catalogue`.

### Changed

- **`blocked` verdict.** Pages that say they are rate-limiting ("too many requests", "error 429", a 429 title) and challenge interstitials are reported as `blocked` instead of `pass` or `unknown`, counted separately in `SiteSummary.blocked` / `blocked_rate`, and excluded from pass rates.
- `incolumitas` ignores its spec-level `WEBDRIVER` row, which stock Chrome fails too.
- `CATALOGUE` and `SITES` moved from `botonomus.diagnostics.detection` to `botonomus.diagnostics.catalogue`; import them from `botonomus.diagnostics`.

### Fixed

- `botonomus proxy-check` printed `?://?` and no exit details for every proxy when the shared checker was used.
- Unit tests pass on Linux (socket linger layout, real-clock warm-up timing).

### Included from the 1.0 development line

The 0.1 lifecycle guarantees (bounded admission, cancellation-safe cleanup, cross-process profile locks, typed errors, credential-free logs) are unchanged.

### Changed

- **Package restructure.** The code is split into single-responsibility packages: `config`, `core`, `browser`, `drivers`, `cdp`, `fingerprint`, `network`, `human`, `profiles`, `diagnostics` and `cli`. Every public module, class and function has a Google-style docstring. `botonomus` re-exports the main API.
- The default driver is now `native`, Botonomus' own stdlib CDP driver. `patchright` and `playwright` remain available as optional extras.
- `HumanProfile` is renamed `HumanConfig`; `HumanProfile` and `Human(profile=...)` remain as deprecated aliases.

### Added

- **Native driver mouse realism.** Mouse events match a real Windows mouse: pressure 0.5 while a button is held, the `buttons` bitmask on every event, drag moves naming the held button, and click counts tracked with the Windows double-click time and rectangle. New `Mouse.dblclick`, `Mouse.position` and `Mouse.buttons`. Keyboard events carry `KeyboardEvent.location` for left/right modifiers.
- **`Runtime.enable` regression tests.** Integration tests check that console-preview CDP probes (Proxy `ownKeys`, `Error.stack` getter, error-name counter) do not fire under the native driver, with a positive control that enables `Runtime` to prove the page can detect it.
- **Personas (`botonomus.fingerprint`).** Seeded, internally consistent `Persona` values (hardware concurrency, device memory, time zone, optional GPU override, noise seed) drawn from weighted distributions, capped at the host's hardware and rendered as `--bn-*` switches for Botonomus Chromium. `BrowserConfig(persona=...)` accepts `"auto"` (stable per-profile seed from a per-installation key), `"off"`, an int seed or a `Persona`. No JavaScript spoofing fallback: an explicit persona on stock Chrome raises `PersonaUnsupportedError`.
- **Geo consistency (`botonomus.network.geoip`).** Exit lookup through the proxy with ip-api.com and an ipinfo.io TLS fallback, a provider protocol for custom services, and a per-proxy `ExitCache` with TTL and shared in-flight lookups. `BrowserConfig(geoip=True)` aligns locale and time zone with the exit; `timezone` and `allow_timezone_mismatch` control the time zone. New `GeoLookupError` and `GeoMismatchError`.
- **Humanize v2.** `HumanConfig` presets (`default`, `careful`, `fast`), mistype-and-correct with keyboard layouts (US QWERTY, DE QWERTZ, FR AZERTY, custom), per-session typing tempo and drift, digraph speed-up, idle micro-movements, overshoot on long moves, and an actionability wait (visible, enabled, stable box) before acting. `HumanPage` and `BrowserConfig(humanize=...)` humanize `session.page` automatically; `page.raw` bypasses it. Opt-in profile warm-up with `botonomus.profiles.warm_up`.
- **Botonomus Chromium installer.** `botonomus.browser.install()` fetches a release manifest, verifies its Ed25519 signature against a key embedded in the SDK (pure-Python RFC 8032 verification), checks archive size and SHA-256, extracts safely and installs atomically under `BOTONOMUS_HOME`. `installed_binaries`, `find_installed` and `uninstall` manage installs. `BrowserConfig(browser=...)` chooses between Botonomus Chromium and Chrome. New `BinaryNotInstalledError`, `BinaryVerificationError` and `BinaryDownloadError`. The release host and signing key are placeholders until the first binary release.
- **CLI and detection runner.** The `botonomus` command with `info`, `install`, `uninstall`, `binaries`, `open`, `probe`, `detect`, `proxy-check`, `profiles list|remove|warmup` and `benchmark`, built on `argparse`. Browser commands accept `--browser`, `--persona`, `--geoip`, `--timezone`, `--allow-timezone-mismatch` and `--humanize`. `botonomus.core.resolve_executable` exposes which executable a launch uses; `info` and detection reports use it and say whether it is a Botonomus build. `botonomus detect` runs eight public detection pages repeatedly and writes screenshots, page text and an aggregated `report.json` with environment metadata. `botonomus.diagnostics.run_detection` and `DetectionSite` support custom sites.
- Proxy list loading and bulk checks: `botonomus.network.load_proxies` and `check_proxies`.

### Fixed

- **Proxy forwarder close hang on Windows.** On the proactor event loop (CPython 3.12), a client connection reset could make `asyncio.Server.wait_closed()` never return and leak the socket, and a reset during accept closed the listener for good. The forwarder now runs its own accept loop, registers every connection synchronously, closes each accepted socket itself and aborts transports on close.

## [0.1.0] - 2026-10-03

### Added

- `Botonomus` session manager with a bounded `max_instances` pool, cancellation-safe launch and cleanup, and typed errors.
- Native Chrome launch with an explicit loopback debugging port and dedicated, cross-process locked profiles that persist cookies and storage.
- Patchright and Playwright drivers attached over CDP, and a first native CDP driver that never sends `Runtime.enable` and works in an isolated world.
- Locale flags, authenticated proxies through a loopback SOCKS5 forwarder, WebRTC non-proxied UDP blocking, and per-session launch arguments.
- `Human` input: Bezier pointer paths with Fitts's-law timing, lognormal keystroke gaps and eased wheel scrolling.
- Local probe page with ordinary-launch comparison, and a held-open concurrency benchmark.

[Unreleased]: https://github.com/Kuduxaaa/botonomus/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/Kuduxaaa/botonomus/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Kuduxaaa/botonomus/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Kuduxaaa/botonomus/releases/tag/v0.2.0
