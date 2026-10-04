# Troubleshooting on Windows

Windows is the most-tested platform. These are the problems seen there, with their causes. For Linux see [Linux servers](linux.md).

## Chrome is not found

`BrowserUnavailableError: Chrome not found` means no Google Chrome was found in the standard install locations or on `PATH`. Run `botonomus info` to see what is discovered, then either install stable Chrome or pass the path:

```python
from pathlib import Path
from botonomus import BrowserConfig

BrowserConfig(executable_path=Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"))
```

On the command line, quote paths with spaces: `--executable "C:\Program Files\Google\Chrome\Application\chrome.exe"`. Unbranded Chromium is never substituted silently; name it explicitly if you want it.

## "Only for automated testing" infobar, smaller viewport

You are running Chrome for Testing or a Playwright-downloaded browser (`botonomus info` marks it `[testing build]`, and Botonomus logs `testing_build_executable`). It shows a permanent infobar that shrinks the viewport, and ordinary users do not run it. Use stable Google Chrome.

## Clicks hang when windows overlap

Chrome on Windows marks fully covered windows as hidden and pauses `requestAnimationFrame`, so input that waits for a frame never completes once many sessions overlap. `BrowserConfig(render_when_occluded=True)`, the default, adds `--disable-backgrounding-occluded-windows` to prevent this. Only set it to `False` if you need Chrome's native occlusion behaviour.

## Time zone does not follow the proxy

Stock Chrome on Windows uses the system time zone and ignores the `TZ` environment variable, and Botonomus avoids CDP time-zone emulation because it is itself an inconsistency. With `geoip=True` or `timezone=...`, a mismatch raises `GeoMismatchError`. Options: use Botonomus Chromium, change the Windows time zone to match the exit, or pass `allow_timezone_mismatch=True` to accept the risk.

## Unknown time zone errors

Windows Python ships without the IANA time-zone database. Botonomus depends on `tzdata` on Windows, which `pip install botonomus` installs. If you installed with `--no-deps`, install `tzdata` too; otherwise zone names cannot be validated or compared.

## Event loop errors with subprocesses

Botonomus launches browsers as subprocesses, which needs the proactor event loop. `asyncio.run()` uses it by default on Windows. Do not switch to `WindowsSelectorEventLoopPolicy` for Botonomus code.

## Session shutdown hung with an authenticated proxy (fixed in 1.0.0)

On the proactor loop (CPython 3.12), a browser killed mid-connection could reset a socket in a way that made `asyncio.Server.wait_closed()` never return, so closing the proxy forwarder hung and leaked the socket. Since 1.0.0 the forwarder runs its own accept loop and closes every accepted socket itself. If you still see a hang at close, open an issue with the log output from the `botonomus` logger.

## Garbled characters or crashes when printing

Detection page text and ISP names are arbitrary Unicode, and a legacy `cp1252` console cannot show all of it. The CLI reconfigures stdout and stderr with `errors="backslashreplace"` so it never crashes; in your own scripts, use Windows Terminal or set `PYTHONIOENCODING=utf-8`.

## Profile is locked after a crash

Locks are released when a session exits. If Python was killed (Task Manager, power loss), a browser process can outlive it and keep the profile in use. Close leftover `chrome.exe` processes started with that profile, then retry. `botonomus profiles list` shows which profiles are in use. A context manager cannot make a forced termination cleanup-safe.

## Uninstalling a Botonomus Chromium build fails

`botonomus.browser.uninstall(version)` raises `OSError` while a browser from that build is running, because Windows does not delete files in use. Close those sessions first.

## First launch times out

On one test machine the first ordinary-launch baseline timed out because of a Chromium sandbox/network-service error; an unchanged rerun completed. If launches time out, retry, raise `launch_timeout`, and check antivirus or endpoint-protection software that inspects new processes. Do not disable Chrome's sandbox.

## Do not pass `--remote-debugging-port=0`

With port `0`, Chromium reports `navigator.webdriver=true` (measured). Botonomus always chooses an explicit port and rejects `--remote-debugging-*` flags in `extra_args`.

## Debugging port security

While a session runs, any local process that can reach its loopback debugging port can control it. Run Botonomus on a machine you trust.
