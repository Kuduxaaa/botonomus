"""Immutable browser launch configuration.

Native browser defaults are preserved: Botonomus only sets what it must own to
keep profiles isolated and automation unobservable, and validates everything else.
"""

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from ..errors import ConfigurationError
from ..fingerprint import Persona, PersonaSpec
from ..fingerprint.switches import PERSONA_SWITCHES
from ..human import HumanConfig
from .proxy import ProxySpec, parse_proxy

DriverName = Literal["native", "patchright", "playwright"]
DRIVERS: Final = ("native", "patchright", "playwright")

BrowserChoice = Literal["auto", "botonomus", "chrome"]
BROWSER_CHOICES: Final = ("auto", "botonomus", "chrome")

OWNED_FLAGS: Final = (
    "--user-data-dir",
    "--remote-debugging-port",
    "--remote-debugging-address",
    "--remote-debugging-pipe",
    "--headless",
    "--enable-automation",
    "--proxy-server",
    "--lang",
    "--accept-lang",
    "--user-agent",
)
"""Flags Botonomus sets itself.

Overriding them would break process ownership or profile isolation, or would make
automation observable: ``--enable-automation``, ``--remote-debugging-pipe`` and
``--headless`` set ``navigator.webdriver``; ``--user-agent`` reduces User-Agent
Client Hints to their low-entropy values.
"""

_LOCALE: Final = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
_TIMEZONE: Final = re.compile(r"[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)*")


@dataclass(frozen=True, slots=True)
class BrowserConfig:
    """Settings shared by every session a manager opens.

    Attributes:
        profile_root: Directory holding one subdirectory per named profile. Never point
            this at an everyday Chrome profile.
        executable_path: Browser executable. ``None`` discovers installed Chrome.
        headless: Run without a window. Visible windows are the validated default.
        launch_timeout: Seconds allowed for the process to start and accept CDP.
        close_timeout: Seconds allowed for a graceful close before termination.
        locale: BCP 47 tag applied with ``--lang`` and ``--accept-lang``.
        proxy: Upstream proxy URL (``http``, ``https`` or ``socks5``). Credentials are
            supported through a loopback forwarder and never reach the command line.
        extra_args: Additional ``--flag[=value]`` arguments for every session.
        driver: ``native`` (Botonomus' CDP driver, never sends ``Runtime.enable``),
            ``patchright`` or ``playwright``.
        render_when_occluded: Keep rendering windows that other windows cover. Chrome on
            Windows otherwise marks them hidden and stalls ``requestAnimationFrame``,
            which hangs input when many sessions overlap.
        browser: Which browser to launch when ``executable_path`` is unset:
            ``"botonomus"`` requires an installed Botonomus Chromium build,
            ``"chrome"`` uses Google Chrome, ``"auto"`` prefers an installed Botonomus
            build and falls back to Chrome.
        persona: Fingerprint identity: ``"auto"`` derives a stable persona from the
            profile name on Botonomus Chromium and does nothing on Chrome; ``"off"``
            disables it; an int seed or a [`Persona`][botonomus.fingerprint.Persona] is
            used as given and requires Botonomus Chromium.
        geoip: Look up the proxy's exit before each launch and align locale and time
            zone with it. Requires ``proxy``.
        timezone: IANA time zone to present. Overrides the exit's zone. Applied by
            Botonomus Chromium; with Chrome it must match the host's zone.
        allow_timezone_mismatch: Launch Chrome even when the presented time zone
            cannot match the exit's. Off by default because the mismatch is a
            well-known detection signal.
        humanize: Route ``session.page`` actions through human-like input. ``True``
            uses the default preset; a [`HumanConfig`][botonomus.human.HumanConfig] customizes
            it. ``session.page.raw`` stays available for direct actions.
        virtual_display: Run headed browsers on an Xvfb virtual screen (Linux only).
            ``None`` (default) does so on Linux when no ``DISPLAY`` or
            ``WAYLAND_DISPLAY`` is set; ``True`` always; ``False`` never.
        persona_switches: Resolved persona switches for one launch. Filled in by the
            session manager from ``persona``, ``geoip`` and ``timezone``; leave empty.

    Raises:
        ConfigurationError: On construction, if any value is invalid.
    """

    profile_root: Path = Path(".botonomus/profiles")
    executable_path: Path | None = None
    headless: bool = False
    launch_timeout: float = 30.0
    close_timeout: float = 10.0
    locale: str | None = None
    proxy: str | None = field(default=None, repr=False)
    extra_args: tuple[str, ...] = ()
    driver: DriverName = "native"
    render_when_occluded: bool = True
    browser: BrowserChoice = "auto"
    persona: PersonaSpec = "auto"
    geoip: bool = False
    timezone: str | None = None
    allow_timezone_mismatch: bool = False
    humanize: bool | HumanConfig = False
    virtual_display: bool | None = None
    persona_switches: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        for value in (self.launch_timeout, self.close_timeout):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigurationError("Timeouts must be positive finite numbers")
            if not math.isfinite(value) or value <= 0:
                raise ConfigurationError("Timeouts must be positive finite numbers")
        if not isinstance(self.headless, bool) or not isinstance(self.render_when_occluded, bool):
            raise ConfigurationError("headless and render_when_occluded must be booleans")
        if self.driver not in DRIVERS:
            raise ConfigurationError("driver must be 'native', 'patchright' or 'playwright'")
        if self.locale is not None and (
            not isinstance(self.locale, str) or not _LOCALE.fullmatch(self.locale)
        ):
            raise ConfigurationError("locale must be a BCP 47 tag such as 'en-US'")
        if self.proxy is not None:
            if not isinstance(self.proxy, str):
                raise ConfigurationError("proxy must be a string")
            parse_proxy(self.proxy)
        if self.browser not in BROWSER_CHOICES:
            raise ConfigurationError("browser must be 'auto', 'botonomus' or 'chrome'")
        if not (
            self.persona in ("auto", "off")
            or isinstance(self.persona, Persona)
            or (type(self.persona) is int and 0 <= self.persona < 2**64)
        ):
            raise ConfigurationError("persona must be 'auto', 'off', a 64-bit seed or a Persona")
        for flag in ("geoip", "allow_timezone_mismatch"):
            if not isinstance(getattr(self, flag), bool):
                raise ConfigurationError(f"{flag} must be a boolean")
        if self.geoip and self.proxy is None:
            raise ConfigurationError("geoip=True requires a proxy")
        if self.timezone is not None and (
            not isinstance(self.timezone, str) or not _TIMEZONE.fullmatch(self.timezone)
        ):
            raise ConfigurationError("timezone must be an IANA name such as 'Europe/Berlin'")
        if self.virtual_display is not None and not isinstance(self.virtual_display, bool):
            raise ConfigurationError("virtual_display must be True, False or None")
        if not isinstance(self.humanize, (bool, HumanConfig)):
            raise ConfigurationError("humanize must be a boolean or a HumanConfig")
        object.__setattr__(self, "extra_args", validate_args(self.extra_args))
        if not isinstance(self.persona_switches, tuple) or any(
            not isinstance(arg, str) or arg.split("=", 1)[0] not in PERSONA_SWITCHES
            for arg in self.persona_switches
        ):
            raise ConfigurationError("persona_switches may only contain persona switches")
        object.__setattr__(self, "profile_root", Path(self.profile_root).expanduser().resolve())
        if self.executable_path is not None:
            object.__setattr__(
                self, "executable_path", Path(self.executable_path).expanduser().resolve()
            )

    @property
    def proxy_spec(self) -> ProxySpec | None:
        """The parsed proxy, or ``None`` when no proxy is configured."""
        return parse_proxy(self.proxy) if self.proxy is not None else None

    @property
    def human_config(self) -> HumanConfig | None:
        """The humanize settings in effect, or ``None`` when humanize is off."""
        if isinstance(self.humanize, HumanConfig):
            return self.humanize
        return HumanConfig() if self.humanize else None


def validate_args(args: object) -> tuple[str, ...]:
    """Validate extra browser arguments.

    Args:
        args: A list or tuple of ``--flag`` or ``--flag=value`` strings.

    Returns:
        The arguments as a tuple.

    Raises:
        ConfigurationError: If ``args`` is not a sequence of well-formed flags, or names
            a flag listed in `OWNED_FLAGS` or a persona switch.
    """
    if isinstance(args, str) or not isinstance(args, (tuple, list)):
        raise ConfigurationError("Browser arguments must be a sequence of strings")
    result = tuple(args)
    for arg in result:
        if not isinstance(arg, str) or not arg.startswith("--") or "\x00" in arg:
            raise ConfigurationError("Browser arguments must be '--flag' or '--flag=value'")
        name = arg.split("=", 1)[0].lower()
        if name in OWNED_FLAGS:
            raise ConfigurationError(f"{name} is managed by Botonomus and cannot be overridden")
        if name in PERSONA_SWITCHES:
            raise ConfigurationError(f"{name} is set from BrowserConfig(persona=...)")
    return result
