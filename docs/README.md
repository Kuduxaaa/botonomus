# Botonomus

Botonomus is an async Python SDK (Python 3.12+, MIT licence) for browser automation that looks like an ordinary browser.

- **Real browsers.** It launches installed Google Chrome, or the Botonomus Chromium build, as a normal process with a dedicated profile and an explicit loopback debugging port. Nothing about the launch sets `navigator.webdriver`.
- **Native CDP driver.** The default driver speaks the Chrome DevTools Protocol over a stdlib websocket, never sends `Runtime.enable`, and does DOM work in a private isolated world that page scripts cannot see. Patchright and Playwright are optional alternatives.
- **C++ level personas.** On Botonomus Chromium, a seeded persona (hardware, time zone, canvas/WebGL/audio noise) is applied by command-line switches inside the browser. Botonomus never spoofs fingerprints in JavaScript.
- **An httpx-like client.** `await botonomus.get(url)` loads the page in a real browser tab. A `Client` serves many requests from one shared Chrome through in-memory contexts, with an optional HTTP fast path and persistent identities.
- **Operations built in.** A bounded concurrency pool, persistent profiles with cross-process locks, authenticated proxies through a loopback forwarder, geo consistency between proxy exit, locale and time zone, human-like input, and a reproducible detection runner.

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

!!! note "What Botonomus does not claim"
    Botonomus is not undetectable, and no tool is. Its measured results describe specific pages on one machine, network, browser build and date. Network reputation, account history and behaviour remain signals that no browser can hide. Reproduce any claim on your own setup with [`botonomus detect`](guides/detection.md).

## Where to go next

| If you want to | Read |
|---|---|
| Install and open your first session | [Getting started](getting-started.md) |
| Call a real browser like `httpx`, light enough for small servers | [Client](guides/client.md) |
| Run many sessions at once | [Concurrency](guides/concurrency.md) |
| Keep logins between runs | [Profiles](guides/profiles.md) |
| Use authenticated proxies and match their location | [Proxies and geo](guides/proxies-and-geo.md) |
| Present distinct hardware identities | [Personas](guides/personas.md) |
| Make input look human | [Human-like input](guides/humanize.md) |
| Measure what detection pages see | [Detection runner](guides/detection.md) |
| Use the `botonomus` command | [Command line](guides/cli.md) |
| Fix Windows-specific problems | [Troubleshooting on Windows](guides/troubleshooting-windows.md) |
| Look up a class or function | [API reference](reference/index.md) |

## Responsible use

Botonomus is for authorised work: testing and monitoring your own sites, QA, research, accessibility checks, and automation the target site permits. Respect each site's terms of service, robots directives and rate limits, and the laws that apply to you, including data-protection and computer-misuse laws. Credential stuffing, account takeover, fake account creation, ad or payment fraud, ticket scalping, ban evasion and similar abuse are prohibited.

## Licence

The `botonomus` SDK is MIT licensed. The Botonomus Chromium binary is distributed separately under its own licence.
