# Personas

A persona is the hardware and locale identity a browser presents. Without one, every Botonomus session on a machine presents the same real hardware, so sessions can be linked to each other. Personas give each profile a distinct, stable and plausible identity.

!!! warning "Requires Botonomus Chromium"
    Personas are applied by **Botonomus Chromium**, a Chromium build with source-level patches, through `--bn-*` command-line switches. Stock Google Chrome has no such switches. Botonomus never falls back to JavaScript spoofing: injected overrides are easy for detectors to find and produce lies that contradict the rendered output.

## What a persona contains

| Field | Surface | Default source |
|---|---|---|
| `seed` | Keys canvas 2D, WebGL readback and audio noise. Per-site keys derive from the seed and the top-level site, so a profile is stable per site and unlinkable across profiles. | Per-profile seed |
| `hardware_concurrency` | `navigator.hardwareConcurrency` | Weighted draw, at most the host's logical CPUs |
| `device_memory` | `navigator.deviceMemory` (2, 4, 8, 16 or 32 GB) | Weighted draw, at most the host's memory |
| `timezone` | Time zone in the browser | `BrowserConfig.timezone`, the proxy exit (with `geoip=True`), or the host |
| `gpu` | `UNMASKED_VENDOR_WEBGL` / `UNMASKED_RENDERER_WEBGL` strings | None: keep the real GPU |
| `noise` | Canvas, WebGL and audio readback noise on or off | **Off** for derived personas (`Persona.from_seed(..., noise=True)` turns it on); on for a directly constructed `Persona` |
| `screen`, `taskbar` | `screen.width/height`, `availHeight` (height minus taskbar), CSS `device-width`/`device-height`, every frame | Weighted draw of a real monitor resolution no larger than the host's, divided by the host's scale; taskbar 48 (Windows 11) or 40 (Windows 10). `None` when the host screen is unknown (Linux, macOS) |

Values are drawn from weighted Windows desktop distributions and **never exceed the host's hardware**: detectors measure parallel speed-up and proof-of-work timing, so claimed cores or memory that do not exist show up.

The screen follows the same rule. `devicePixelRatio` stays the host's, so the persona picks a *physical* resolution and divides it by the host's scale: on a 1920x1080 monitor at 125 %, a persona can present 1536x864 (that monitor) or 1093x614 (a 1366x768 one), and `screen x devicePixelRatio` is always a real monitor size. With a persona screen the browser window opens at the origin and fills the persona's work area, because `outerWidth`/`outerHeight` come from the real window. The screen switches need patch 0012 in Botonomus Chromium; `botonomus consistency` reports the `screen` check as failed on a build without it.

### Why noise is off by default

Measured on 2026-10-05 with the official build, through rotating proxies, on fingerprint.com's playground: the tampering model scored personas **with** readback noise 0.76-0.92 and flagged most of them, scored the same personas **without** noise about 0.24, and scored the browser with no persona 0.48. Readback noise is what that detector reads as tampering. The cost of turning it off: personas on one machine share their canvas, WebGL and audio hashes, so they can be linked through those values (the rest of the persona, the proxy and the profile still differ). Turn it on with `Persona.from_seed(seed, host, noise=True)` where linkability matters more than this detector.

## Choosing a persona

```python
from botonomus import BrowserConfig

BrowserConfig(persona="auto")  # default
BrowserConfig(persona="off")
BrowserConfig(persona=0x1234ABCD)  # explicit 64-bit seed
```

| `persona` | On Botonomus Chromium | On stock Chrome |
|---|---|---|
| `"auto"` (default) | Stable persona derived from the profile | No persona; the host's real values |
| `"off"` | No persona | No persona |
| `int` seed in `[0, 2**64)` | Persona derived from the seed | `PersonaUnsupportedError` |
| `Persona(...)` | Used as given; `ConfigurationError` if it claims more cores or memory than the host has | `PersonaUnsupportedError` |

With `"auto"`, the seed is a keyed BLAKE2b hash of the lowercase profile name, keyed with a random 32-byte installation key in `profile_root/.botonomus/persona.key`. The same profile keeps the same persona across restarts; the same profile name on another machine gets an unrelated one. Deleting the key gives every profile under that root a new identity.

After launch, `session.persona` holds the applied persona (or `None`).

## Deterministic derivation

`Persona.from_seed` is pure and identical on every platform and Python version, which makes it easy to inspect:

```pycon
>>> from botonomus.fingerprint import HostInfo, Persona
>>> host = HostInfo(logical_cpus=16, memory_gb=32.0, platform="win32")  # screen unknown
>>> persona = Persona.from_seed(42, host, timezone="Europe/Berlin")
>>> persona.hardware_concurrency, persona.device_memory
(12, 16)
>>> persona.to_switches()
('--bn-device-memory=16', '--bn-hardware-concurrency=12', '--bn-seed=000000000000002a', '--bn-timezone=Europe/Berlin')
```

`HostInfo.detect()` measures the current machine. The `--bn-*` switches are reserved: passing them in `extra_args` raises `ConfigurationError`; use `persona=` instead.

## GPU overrides

Overriding the GPU changes only the strings WebGL reports; every pixel is still rendered by the real GPU. Render-hash checks catch a claim from a different vendor family, so overrides are opt-in and should stay within the host's family:

```python
from botonomus import BrowserConfig
from botonomus.fingerprint import HostInfo, Persona, same_family_gpus

host = HostInfo.detect()
choices = same_family_gpus("NVIDIA")  # curated real Windows ANGLE strings
persona = Persona.from_seed(7, host, gpu=choices[0])
config = BrowserConfig(persona=persona, browser="botonomus")
```

`same_family_gpus` returns an empty tuple for unknown or software renderers, because no override can be backed by their output.

## Installing Botonomus Chromium

```python
import asyncio
from botonomus.browser import install, installed_binaries

binary = asyncio.run(install())
print(binary.version, binary.executable)
print([b.version for b in installed_binaries()])
```

[`install()`](../reference/browser.md) fetches the channel's release manifest and its signature, verifies the Ed25519 signature against a public key embedded in the SDK **before** parsing the manifest, downloads the archive for this platform, checks its size and SHA-256 against the manifest, extracts it with path-safety checks, and renames it into place only after every check passed. Concurrent installs are serialised by a file lock. Installs live in `BOTONOMUS_HOME` (default `%LOCALAPPDATA%\botonomus` on Windows, `~/.cache/botonomus` elsewhere) under `chromium/<version>/`. `uninstall(version)` removes one.

!!! note "Availability"
    In 0.2 the release host (`BOTONOMUS_MANIFEST_URL`, default `https://releases.botonomus.dev/chromium/stable/manifest.json`) and the embedded release key are placeholders, so `install()` cannot complete until the first Botonomus Chromium release is published. The same applies to the `botonomus install` command, which wraps `install()`. `botonomus binaries` and `botonomus info` list installed builds, and `botonomus info` shows which executable `browser="auto"` would launch.

### Which browser launches

With no `executable_path`, `BrowserConfig.browser` decides:

| `browser` | Behaviour |
|---|---|
| `"auto"` (default) | The newest installed Botonomus Chromium build, else Google Chrome |
| `"botonomus"` | Installed Botonomus Chromium, else `BinaryNotInstalledError` |
| `"chrome"` | Google Chrome |

An explicit `executable_path` is used as given. It counts as Botonomus Chromium when a `botonomus-build.json` marker sits next to it or it lives inside a verified install.

## Licence

Botonomus Chromium is distributed separately under its own licence, not the SDK's MIT licence. Measurements of Botonomus Chromium against detection pages will be published separately, using the same `botonomus detect` method as the stock Chrome results.
