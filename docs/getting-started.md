# Getting started

## Requirements

- Python 3.12 or newer.
- Google Chrome, stable channel, installed in a standard location or on `PATH`. Botonomus never downloads Chrome and never substitutes unbranded Chromium silently. Otherwise pass `BrowserConfig(executable_path=...)`.
- Windows is the validated platform. The SDK is pure Python and the unit tests run elsewhere, but real-browser behaviour has been measured on Windows 11.

Avoid Chrome for Testing and Playwright's downloaded browsers: they show a permanent "only for automated testing" infobar, which also shrinks the viewport, and they are not what ordinary users run. Botonomus logs `testing_build_executable` when it sees one.

## Install

```bash
pip install "git+https://github.com/Kuduxaaa/botonomus"
```

Optional extras add alternative drivers:

```bash
pip install "botonomus[patchright] @ git+https://github.com/Kuduxaaa/botonomus"   # Patchright: Playwright API, no Runtime.enable
pip install "botonomus[playwright] @ git+https://github.com/Kuduxaaa/botonomus"   # stock Playwright
```

Check what Botonomus can find:

```bash
botonomus info
```

## Your first session

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

- `Botonomus` is the session manager. Enter it once, in one event loop.
- `bot.open(profile=...)` launches a browser with the profile directory `.botonomus/profiles/acct-01`, attaches the driver, and yields a [`Session`](reference/core.md). The browser closes when the block exits, including on errors and cancellation.
- The profile keeps cookies and storage, so the next run with `profile="acct-01"` is still logged in.

## The session object

| Attribute | Meaning |
|---|---|
| `session.page` | The initial page: a native `botonomus.cdp.Page`, a Playwright `Page`, or a `HumanPage` wrapper when `humanize` is on |
| `session.context` | The profile-backed browser context (cookies, new pages) |
| `session.profile` | The normalized profile name |
| `session.executable` | The browser executable that was launched |
| `session.persona` | The applied persona, or `None` |
| `session.exit` | The proxy exit used for geo alignment, or `None` |
| `await session.close()` | Close early; the context exit then does nothing |

## The native page

With the default `native` driver, `session.page` offers a Playwright-like subset with **timeouts in seconds**:

```python
page = session.page
await page.goto("https://example.com", wait_until="domcontentloaded", timeout=20)
await page.locator("text=More information").click()
await page.get_by_role("link", name="More information").click()
heading = await page.locator("xpath=//h1").inner_text()
png = await page.screenshot(path="page.png", full_page=True)

# Runs in a private isolated world: sees the DOM, not page globals, invisible to the page.
links = await page.evaluate("() => document.links.length")

# Reaches page globals, at the cost of being more observable.
flag = await page.evaluate("window.someGlobal", isolated_context=False)

second = await session.context.new_page("https://example.org")
cookies = await session.context.cookies()
```

Locators accept CSS, `text=`, `xpath=` and role selectors (`get_by_role`, `get_by_text`). Mouse and keyboard input (`page.mouse`, `page.keyboard`) are trusted browser events. See [`botonomus.cdp`](reference/cdp.md) for the full API.

Need the full Playwright API? Choose another driver:

```python
from botonomus import BrowserConfig

BrowserConfig(driver="native")  # default, stdlib only, never sends Runtime.enable
BrowserConfig(driver="patchright")  # pip install "botonomus[patchright]"
BrowserConfig(driver="playwright")  # pip install "botonomus[playwright]"
```

With `patchright` or `playwright`, `session.page` is a Playwright `Page` and timeouts are in milliseconds. Stock Playwright is measurably more detectable (see the README's measured results).

## Configuration

Settings shared by every session go in a [`BrowserConfig`](reference/config.md):

```python
from pathlib import Path
from botonomus import Botonomus, BrowserConfig

config = BrowserConfig(
    profile_root=Path(".botonomus/profiles"),
    executable_path=Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    locale="en-GB",
    launch_timeout=30.0,
)
bot = Botonomus(max_instances=10, config=config)
```

Per-launch flags go in `open(..., args=[...])`. Flags Botonomus owns (`--user-data-dir`, `--remote-debugging-*`, `--headless`, `--enable-automation`, `--proxy-server`, `--lang`, `--accept-lang`, `--user-agent`, `--bn-*`) are rejected with `ConfigurationError`. The [README's configuration table](https://github.com/Kuduxaaa/botonomus#configuration-reference) lists every field.

Invalid values fail at construction:

```pycon
>>> from botonomus import BrowserConfig
>>> BrowserConfig(geoip=True)
Traceback (most recent call last):
  ...
botonomus.errors.ConfigurationError: geoip=True requires a proxy
>>> BrowserConfig(extra_args=("--proxy-server=x",))
Traceback (most recent call last):
  ...
botonomus.errors.ConfigurationError: --proxy-server is managed by Botonomus and cannot be overridden
```

## Errors

Catch [`BotonomusError`](reference/errors.md) for anything the SDK raises; causes are preserved as `__cause__`. The common ones:

```python
from botonomus import BrowserUnavailableError, ProfileInUseError

try:
    async with bot.open(profile="acct-01") as session:
        ...
except ProfileInUseError:
    ...  # another session, maybe in another process, owns this profile
except BrowserUnavailableError:
    ...  # Chrome not found: set executable_path
```

## Logging

Botonomus logs to the `botonomus` logger with event names (`session_opened`, `session_closed`, `timezone_mismatch_allowed`, ...) and profile and timing fields. Logs never contain page contents, cookies, navigation URLs or credentials.

```python
import logging

logging.basicConfig(level=logging.INFO)
```
