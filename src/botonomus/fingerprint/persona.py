"""Seeded, internally consistent browser personas and their Chromium switches."""

import dataclasses
import functools
import hashlib
import re
import zoneinfo
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from ..errors import ConfigurationError
from . import switches
from .gpus import GpuOverride
from .host import HostInfo
from .seed import persona_seed

HARDWARE_CONCURRENCY_WEIGHTS: Final[tuple[tuple[int, int], ...]] = (
    (4, 12),
    (6, 8),
    (8, 20),
    (12, 18),
    (16, 22),
    (20, 8),
    (24, 12),
)
"""``(logical processors, relative weight)`` for Windows desktop Chrome.

An estimate, not a measurement: shaped by the Steam Hardware & Software Survey's
physical-core shares (6 and 8 cores most common) mapped to logical counts with
SMT / Hyper-Threading, plus Intel 12th-14th gen hybrid parts at 16, 20 and 24
threads. Replace with BrowserForge / apify fingerprint-suite data (Apache-2.0)
when a measured distribution is imported.
"""

DEVICE_MEMORY_WEIGHTS: Final[tuple[tuple[int, int], ...]] = (
    (4, 6),
    (8, 30),
    (16, 44),
    (32, 20),
)
"""``(navigator.deviceMemory GB, relative weight)`` for Windows desktop Chrome.

An estimate shaped by the Steam Hardware & Software Survey's RAM shares (16 GB
the plurality, 32 GB second and growing, 8 GB declining, 4 GB rare), expressed
as the power-of-two values Chromium reports.
"""

SCREEN_WEIGHTS: Final[tuple[tuple[tuple[int, int], int], ...]] = (
    ((1920, 1080), 40),
    ((2560, 1440), 12),
    ((1366, 768), 10),
    ((1600, 900), 6),
    ((1440, 900), 5),
    ((1920, 1200), 5),
    ((1680, 1050), 3),
    ((1280, 1024), 3),
    ((3840, 2160), 6),
    ((2560, 1080), 2),
)
"""``((width, height) physical pixels, relative weight)`` for Windows desktop monitors.

An estimate shaped by StatCounter and Steam survey resolution shares. A persona picks
a physical resolution no larger than the host's and divides it by the host's scale, so
``screen x devicePixelRatio`` is always a real monitor size.
"""

TASKBAR_WEIGHTS: Final[tuple[tuple[int, int], ...]] = ((48, 70), (40, 30))
"""``(taskbar height in CSS pixels, relative weight)``: Windows 11 (48) and 10 (40)."""

SERVER_PLATFORM_VERSION: Final = "19.0.0"
"""UA-CH ``platformVersion`` of Windows 11 24H2, presented by Windows Server hosts.

Server 2019 reports 7.0.0 and Server 2022 an uncommon 14.0.0 (consumer releases
reached end of service); Server 2025 already reports 19.0.0.
"""

_PLATFORM_VERSION: Final = re.compile(r"\d{1,2}\.\d{1,2}\.\d{1,2}")
_SCREEN_MIN: Final = (640, 480)
_SCREEN_MAX: Final = (7680, 4320)
_TASKBAR_MAX: Final = 200
_SEED_LIMIT: Final = 1 << 64
_MAX_CONCURRENCY: Final = 1024
_PERSON: Final = b"bn-persona-v1"
_ZONE_NAME: Final = re.compile(r"[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+)*")
_MAX_ZONE_LENGTH: Final = 64


@functools.cache
def _tz_database_available() -> bool:
    # Windows Python ships no zone database; without the optional ``tzdata``
    # package ZoneInfo cannot tell an unknown zone from a missing database.
    return bool(zoneinfo.available_timezones())


def _validate_timezone(name: object) -> str:
    if not isinstance(name, str) or len(name) > _MAX_ZONE_LENGTH or not _ZONE_NAME.fullmatch(name):
        raise ConfigurationError("Time zone must be an IANA name such as 'Europe/Berlin'")
    try:
        zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError) as error:
        if _tz_database_available():
            raise ConfigurationError(f"Unknown IANA time zone {name!r}") from error
    return name


@dataclass(frozen=True, slots=True)
class Persona:
    """The hardware and locale identity a Botonomus Chromium profile presents.

    Build one with `from_seed` (or `resolve_persona`) so its values
    are realistic and within the host's capabilities; direct construction is for
    explicit overrides and is validated but not capped.

    Attributes:
        seed: 64-bit key for canvas, WebGL and audio noise. C++ derives per-site
            keys from it and the top-level eTLD+1, so one profile is stable per
            site and unlinkable across profiles.
        hardware_concurrency: ``navigator.hardwareConcurrency`` (1-1024).
        device_memory: ``navigator.deviceMemory`` in GB, one of 2, 4, 8, 16, 32.
        timezone: IANA time zone, or ``None`` to keep the host's zone.
        gpu: WebGL vendor/renderer override, or ``None`` to keep the real GPU.
        noise: Whether readback noise is on; ``False`` is for measurement only.
        screen: Screen size in CSS pixels, or ``None`` to keep the real screen.
        taskbar: Taskbar height in CSS pixels (``availHeight = height - taskbar``);
            used only with ``screen``.
        platform_version: UA-CH ``platformVersion`` override (``MAJOR.MINOR.PATCH``),
            or ``None`` for the host's.

    Raises:
        ConfigurationError: If any field is out of range or malformed. The time
            zone is checked against the IANA database when one is installed
            (``tzdata``); otherwise only its syntax is checked.
    """

    seed: int
    hardware_concurrency: int
    device_memory: int
    timezone: str | None = None
    gpu: GpuOverride | None = None
    noise: bool = True
    screen: tuple[int, int] | None = None
    taskbar: int = 48
    platform_version: str | None = None

    def __post_init__(self) -> None:
        _check_seed(self.seed)
        concurrency = self.hardware_concurrency
        if (
            isinstance(concurrency, bool)
            or not isinstance(concurrency, int)
            or not 1 <= concurrency <= _MAX_CONCURRENCY
        ):
            raise ConfigurationError(
                f"hardware_concurrency must be an integer from 1 to {_MAX_CONCURRENCY}"
            )
        memory = self.device_memory
        if (
            isinstance(memory, bool)
            or not isinstance(memory, int)
            or memory not in switches.DEVICE_MEMORY_VALUES
        ):
            raise ConfigurationError("device_memory must be one of 2, 4, 8, 16, 32")
        if self.timezone is not None:
            _validate_timezone(self.timezone)
        if self.gpu is not None and not isinstance(self.gpu, GpuOverride):
            raise ConfigurationError("gpu must be a GpuOverride or None")
        if not isinstance(self.noise, bool):
            raise ConfigurationError("noise must be a bool")
        if self.screen is not None and (
            not isinstance(self.screen, tuple)
            or len(self.screen) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) for v in self.screen)
            or not all(
                low <= v <= high
                for v, low, high in zip(self.screen, _SCREEN_MIN, _SCREEN_MAX, strict=True)
            )
        ):
            raise ConfigurationError("screen must be (width, height) within 640x480-7680x4320")
        taskbar = self.taskbar
        if (
            isinstance(taskbar, bool)
            or not isinstance(taskbar, int)
            or not 0 <= taskbar <= _TASKBAR_MAX
        ):
            raise ConfigurationError(f"taskbar must be an integer from 0 to {_TASKBAR_MAX}")
        if self.platform_version is not None and (
            not isinstance(self.platform_version, str)
            or not _PLATFORM_VERSION.fullmatch(self.platform_version)
        ):
            raise ConfigurationError("platform_version must look like '19.0.0'")

    @classmethod
    def from_seed(
        cls,
        seed: int,
        host: HostInfo,
        *,
        timezone: str | None = None,
        gpu: GpuOverride | None = None,
    ) -> "Persona":
        """Derive a realistic persona deterministically from a seed.

        Each value is drawn from its weighted distribution after removing
        candidates above the host's capability, using BLAKE2b of the seed as the
        random source so the result is identical on every Python version and
        platform. A host below the smallest candidate reports its own value.

        Args:
            seed: 64-bit seed, in ``[0, 2**64)``.
            host: Host capabilities that cap the claimed hardware.
            timezone: IANA zone to present, or ``None`` for the host zone.
            gpu: Explicit GPU override; see the GPU table in
                `botonomus.fingerprint.gpus`.

        Returns:
            The persona.

        Raises:
            ConfigurationError: For an invalid seed, host, time zone or GPU.
        """
        _check_seed(seed)
        if not isinstance(host, HostInfo):
            raise ConfigurationError("host must be a HostInfo")
        return cls(
            seed=seed,
            hardware_concurrency=_choose(
                seed, "hardware-concurrency", HARDWARE_CONCURRENCY_WEIGHTS, host.logical_cpus
            ),
            device_memory=_choose(seed, "device-memory", DEVICE_MEMORY_WEIGHTS, host.device_memory),
            timezone=timezone,
            gpu=gpu,
            screen=_choose_screen(seed, host),
            taskbar=_choose(seed, "taskbar", TASKBAR_WEIGHTS, _TASKBAR_MAX),
            platform_version=SERVER_PLATFORM_VERSION if host.windows_server else None,
        )

    def to_switches(self) -> tuple[str, ...]:
        """Render the Botonomus Chromium command-line switches for this persona.

        Returns:
            Sorted ``--bn-*=value`` switches. GPU switches appear only with a GPU
            override, ``--bn-timezone`` only with a time zone and ``--bn-noise=0``
            only when noise is disabled.
        """
        values = [
            f"{switches.SEED}={self.seed:016x}",
            f"{switches.HARDWARE_CONCURRENCY}={self.hardware_concurrency}",
            f"{switches.DEVICE_MEMORY}={self.device_memory}",
        ]
        if self.timezone is not None:
            values.append(f"{switches.TIMEZONE}={self.timezone}")
        if self.gpu is not None:
            values.append(f"{switches.GPU_VENDOR}={self.gpu.vendor}")
            values.append(f"{switches.GPU_RENDERER}={self.gpu.renderer}")
        if not self.noise:
            values.append(f"{switches.NOISE}=0")
        if self.screen is not None:
            values.append(f"{switches.SCREEN}={self.screen[0]}x{self.screen[1]}")
            values.append(f"{switches.TASKBAR}={self.taskbar}")
        if self.platform_version is not None:
            values.append(f"{switches.PLATFORM_VERSION}={self.platform_version}")
        return tuple(sorted(values))


type PersonaSpec = Literal["auto", "off"] | int | Persona
"""``"auto"`` (seed from the profile), ``"off"``, an explicit seed, or a persona."""


def resolve_persona(
    spec: PersonaSpec,
    profile_root: Path,
    profile: str,
    host: HostInfo,
    *,
    timezone: str | None = None,
) -> Persona | None:
    """Turn a configuration value into the persona for one session.

    Args:
        spec: ``"auto"`` derives the seed from the profile (see
            [`persona_seed`][botonomus.fingerprint.persona_seed]); ``"off"`` disables the
            persona; an int is used as the seed; a `Persona` is used as is.
        profile_root: Profile root directory holding the persona key.
        profile: Profile name.
        host: Host capabilities.
        timezone: Time zone to present (for example from a geo lookup). It fills
            in an explicit persona's missing time zone but never replaces one.

    Returns:
        The persona, or ``None`` for ``"off"``.

    Raises:
        ConfigurationError: For an unknown spec, invalid seed, profile or time
            zone, or an explicit persona that claims more cores or memory than
            the host has.
    """
    if isinstance(spec, Persona):
        if spec.hardware_concurrency > host.logical_cpus or spec.device_memory > host.device_memory:
            raise ConfigurationError("Persona claims more cores or memory than the host has")
        if spec.screen is not None and host.screen is not None:
            limit = host_dips(host)
            if spec.screen[0] > limit[0] or spec.screen[1] > limit[1]:
                raise ConfigurationError("Persona screen is larger than the host's screen")
        if spec.timezone is None and timezone is not None:
            return dataclasses.replace(spec, timezone=timezone)
        return spec
    if spec == "off":
        return None
    if spec == "auto":
        return Persona.from_seed(persona_seed(profile_root, profile), host, timezone=timezone)
    if isinstance(spec, int) and not isinstance(spec, bool):
        return Persona.from_seed(spec, host, timezone=timezone)
    raise ConfigurationError("persona must be 'auto', 'off', a 64-bit seed or a Persona")


def host_dips(host: HostInfo) -> tuple[int, int]:
    """The host screen in CSS pixels (physical size divided by the scale)."""
    if host.screen is None:
        raise ConfigurationError("Host screen is unknown")
    return round(host.screen[0] / host.scale), round(host.screen[1] / host.scale)


def _choose_screen(seed: int, host: HostInfo) -> tuple[int, int] | None:
    """A weighted real resolution no larger than the host's, in the host's CSS pixels."""
    if host.screen is None:
        return None
    width, height = host.screen
    candidates = [(size, w) for size, w in SCREEN_WEIGHTS if size[0] <= width and size[1] <= height]
    physical = host.screen
    if candidates:
        point = _draw(seed, "screen") % sum(w for _, w in candidates)
        for size, w in candidates:
            if point < w:
                physical = size
                break
            point -= w
    dips = [round(physical[0] / host.scale), round(physical[1] / host.scale)]
    for index in (0, 1):
        dips[index] = min(max(dips[index], _SCREEN_MIN[index]), _SCREEN_MAX[index])
    return dips[0], dips[1]


def _check_seed(seed: object) -> None:
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < _SEED_LIMIT:
        raise ConfigurationError("Persona seed must be an integer in [0, 2**64)")


def _draw(seed: int, label: str) -> int:
    # One independent stream per field, so capping one value never shifts another.
    data = seed.to_bytes(8, "big") + label.encode("ascii")
    return int.from_bytes(hashlib.blake2b(data, digest_size=8, person=_PERSON).digest(), "big")


def _choose(seed: int, label: str, weights: Sequence[tuple[int, int]], cap: int) -> int:
    candidates = [(value, weight) for value, weight in weights if value <= cap]
    if not candidates:
        return cap
    point = _draw(seed, label) % sum(weight for _, weight in candidates)
    for value, weight in candidates:
        if point < weight:
            return value
        point -= weight
    raise AssertionError("unreachable")  # pragma: no cover
