"""Botonomus: stealth browser automation on real Chrome.

Typical use:

    import asyncio
    from botonomus import Botonomus, BrowserConfig

    async def main() -> None:
        async with Botonomus(max_instances=10, config=BrowserConfig()) as bot:
            async with bot.open(profile="acct-01") as session:
                await session.page.goto("https://example.com")

    asyncio.run(main())
"""

from ._version import __version__
from .config import BrowserConfig, ProxySpec, parse_proxy
from .core import Botonomus, Session
from .errors import (
    BinaryDownloadError,
    BinaryNotInstalledError,
    BinaryVerificationError,
    BotonomusError,
    BrowserCleanupError,
    BrowserStartupError,
    BrowserUnavailableError,
    ConfigurationError,
    GeoLookupError,
    GeoMismatchError,
    ManagerClosedError,
    PersonaUnsupportedError,
    ProfileInUseError,
)
from .human import Human, HumanConfig, HumanPage, HumanProfile

__all__ = [
    "PersonaUnsupportedError",
    "GeoMismatchError",
    "GeoLookupError",
    "BinaryVerificationError",
    "BinaryNotInstalledError",
    "BinaryDownloadError",
    "BinaryDownloadError",
    "BinaryNotInstalledError",
    "BinaryVerificationError",
    "Botonomus",
    "BotonomusError",
    "BrowserCleanupError",
    "BrowserConfig",
    "BrowserStartupError",
    "BrowserUnavailableError",
    "ConfigurationError",
    "Human",
    "HumanConfig",
    "HumanPage",
    "HumanProfile",
    "ManagerClosedError",
    "ProfileInUseError",
    "ProxySpec",
    "Session",
    "__version__",
    "parse_proxy",
]
