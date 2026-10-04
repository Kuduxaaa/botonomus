# botonomus

The top-level package re-exports the API most programs need. Each name is documented once, on its home module's page.

::: botonomus
    options:
      members: false
      show_root_heading: false

| Name | Documented in |
|---|---|
| `Botonomus`, `Session` | [botonomus.core](core.md) |
| `BrowserConfig`, `ProxySpec`, `parse_proxy` | [botonomus.config](config.md) |
| `Human`, `HumanConfig`, `HumanPage`, `HumanProfile` | [botonomus.human](human.md) |
| `BotonomusError`, `ConfigurationError`, `BrowserUnavailableError`, `ProfileInUseError`, `ManagerClosedError`, `BrowserStartupError`, `BrowserCleanupError`, `BinaryNotInstalledError`, `BinaryVerificationError`, `BinaryDownloadError` | [botonomus.errors](errors.md) |
| `__version__` | The installed package version string |

`PersonaUnsupportedError`, `GeoLookupError` and `GeoMismatchError` are not re-exported; import them from `botonomus.errors`.
