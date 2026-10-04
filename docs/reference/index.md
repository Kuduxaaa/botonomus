# API reference

Generated from the source docstrings (Google style). Each page documents one public package through its `__all__`.

| Package | Contents |
|---|---|
| [botonomus](botonomus.md) | Top-level re-exports |
| [botonomus.config](config.md) | `BrowserConfig`, `ProxySpec`, `parse_proxy`, `validate_args`, owned flags |
| [botonomus.core](core.md) | `Botonomus` manager, `Session` |
| [botonomus.client](client.md) | httpx-like `Client`, `Identity`, `Response`, `get`/`post`/... |
| [botonomus.errors](errors.md) | Exception hierarchy |
| [botonomus.browser](browser.md) | Chrome discovery, backend, launch arguments, Botonomus Chromium installer |
| [botonomus.drivers](drivers.md) | Native, Patchright and Playwright driver adapters |
| [botonomus.cdp](cdp.md) | Native CDP driver: `Page`, `Locator`, `Mouse`, `Keyboard`, `Context`, `IsolatedContext` |
| [botonomus.fingerprint](fingerprint.md) | `Persona`, `HostInfo`, GPU table, persona seeds and switches |
| [botonomus.network](network.md) | `ProxyForwarder`, exit lookup, `ExitCache`, proxy checks |
| [botonomus.human](human.md) | `Human`, `HumanPage`, `HumanConfig`, keyboard layouts, path and timing math |
| [botonomus.profiles](profiles.md) | `ProfileLease`, profile listing and removal, warm-up |
| [botonomus.diagnostics](diagnostics.md) | Probe, detection runner, benchmark |
| [botonomus.cli](cli.md) | Command-line entry point |

Timeouts in the native driver (`botonomus.cdp`) are in seconds. When you use the `patchright` or `playwright` driver, `session.page` is a Playwright `Page` and its timeouts are in milliseconds.
