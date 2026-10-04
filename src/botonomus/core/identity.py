"""Per-launch identity: which browser runs and how it presents itself.

Everything a site can cross-check is decided here, once, before the process starts:
the executable, the persona switches, the locale and the time zone. Decisions are
consistent by construction or the launch is refused; nothing is patched afterwards.
"""

import asyncio
import datetime
import logging
import zoneinfo
from dataclasses import dataclass, replace
from pathlib import Path

from ..browser import find_botonomus_chromium, find_chrome, is_botonomus_build
from ..config import BrowserConfig
from ..errors import (
    BinaryNotInstalledError,
    GeoLookupError,
    GeoMismatchError,
    PersonaUnsupportedError,
)
from ..fingerprint import HostInfo, Persona, resolve_persona, switches
from ..network import ExitCache, ExitInfo, UpstreamError

_log = logging.getLogger("botonomus")


@dataclass(frozen=True, slots=True)
class LaunchIdentity:
    """The resolved identity of one launch.

    Attributes:
        config: The session configuration with ``executable_path``, ``locale`` and
            persona switches filled in; pass it to the backend unchanged.
        executable: The browser executable.
        botonomus_build: Whether ``executable`` is a Botonomus Chromium build.
        persona: The applied persona, or ``None``.
        exit: The proxy exit used for geo alignment, or ``None``.
    """

    config: BrowserConfig
    executable: Path
    botonomus_build: bool
    persona: Persona | None
    exit: ExitInfo | None


class IdentityResolver:
    """Resolves `LaunchIdentity` for a manager's launches.

    Holds the exit cache so sessions sharing a proxy share one lookup.

    Args:
        exits: Exit lookup cache; a fresh one is created when ``None``.
    """

    def __init__(self, exits: ExitCache | None = None) -> None:
        self._exits = exits or ExitCache()
        self._host: HostInfo | None = None

    async def resolve(self, config: BrowserConfig, profile: str) -> LaunchIdentity:
        """Decide executable, persona, locale and time zone for one launch.

        Args:
            config: The session configuration.
            profile: The normalized profile name.

        Returns:
            The identity to launch with.

        Raises:
            BrowserUnavailableError: If no suitable executable is found.
            BinaryNotInstalledError: If ``browser="botonomus"`` and no build is installed.
            PersonaUnsupportedError: If an explicit persona is requested for Chrome.
            GeoLookupError: If ``geoip=True`` and the exit cannot be determined.
            GeoMismatchError: If Chrome cannot present the required time zone.
            ConfigurationError: For an invalid persona or time zone.
        """
        executable = await asyncio.to_thread(resolve_executable, config)
        botonomus_build = await asyncio.to_thread(is_botonomus_build, executable)
        exit_info = await self._exit(config)
        timezone = config.timezone or (exit_info.timezone if exit_info else None) or None
        locale = config.locale or (exit_info.locale() if exit_info else None)

        persona: Persona | None = None
        if botonomus_build:
            host = await self._host_info()
            persona = await asyncio.to_thread(
                resolve_persona,
                config.persona,
                config.profile_root,
                profile,
                host,
                timezone=timezone,
            )
        elif config.persona != "auto" and config.persona != "off":
            raise PersonaUnsupportedError(
                "persona requires Botonomus Chromium; install it with 'botonomus install'"
            )
        elif timezone is not None:
            _require_host_timezone(timezone, config.allow_timezone_mismatch)

        extra = persona.to_switches() if persona is not None else ()
        if persona is None and botonomus_build and timezone is not None:
            extra = (f"{switches.TIMEZONE}={timezone}",)
        resolved = replace(
            config,
            executable_path=executable,
            locale=locale,
            persona_switches=extra,
        )
        return LaunchIdentity(resolved, executable, botonomus_build, persona, exit_info)

    async def _exit(self, config: BrowserConfig) -> ExitInfo | None:
        spec = config.proxy_spec
        if not config.geoip or spec is None:
            return None
        try:
            return await self._exits.get(spec)
        except (UpstreamError, OSError, TimeoutError) as exc:
            raise GeoLookupError("Could not determine the proxy exit location") from exc

    async def _host_info(self) -> HostInfo:
        if self._host is None:
            self._host = await asyncio.to_thread(HostInfo.detect)
        return self._host


def resolve_executable(config: BrowserConfig) -> Path:
    """Return the browser executable a launch with ``config`` would use.

    The same rule every session launch follows: an explicit ``executable_path`` or
    ``browser="chrome"`` selects Google Chrome (or the given file); otherwise the
    newest installed Botonomus Chromium build for this platform is preferred, and
    ``browser="auto"`` falls back to Chrome when none is installed. Blocking: it
    touches the filesystem, so call it through `asyncio.to_thread` from async
    code.

    Args:
        config: The session configuration. Only ``executable_path`` and ``browser``
            are consulted.

    Returns:
        The path of an existing executable.

    Raises:
        BrowserUnavailableError: If the configured executable does not exist, or no
            Chrome is found when Chrome is required or is the fallback.
        BinaryNotInstalledError: If ``browser="botonomus"`` and no build is installed.
    """
    if config.executable_path is not None or config.browser == "chrome":
        return find_chrome(config.executable_path)
    installed = find_botonomus_chromium()
    if installed is not None:
        return installed
    if config.browser == "botonomus":
        raise BinaryNotInstalledError(
            "Botonomus Chromium is not installed; run 'botonomus install'"
        )
    return find_chrome(None)


def _require_host_timezone(timezone: str, allow_mismatch: bool) -> None:
    """Refuse a Chrome launch whose host zone disagrees with ``timezone`` right now.

    Offsets are compared rather than names, so ``Europe/Berlin`` and
    ``Europe/Paris`` agree; that is what pages can observe.
    """
    try:
        wanted = datetime.datetime.now(zoneinfo.ZoneInfo(timezone)).utcoffset()
    except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
        raise GeoMismatchError(f"Unknown time zone {timezone!r}") from exc
    if wanted == _host_utc_offset():
        return
    if allow_mismatch:
        _log.warning("timezone_mismatch_allowed")
        return
    raise GeoMismatchError(
        f"Host time zone differs from {timezone}; Chrome cannot present it. Use Botonomus "
        "Chromium, change the host time zone, or set allow_timezone_mismatch=True"
    )


def _host_utc_offset() -> datetime.timedelta | None:
    """The host's current UTC offset, which is what stock Chrome presents."""
    return datetime.datetime.now().astimezone().utcoffset()
