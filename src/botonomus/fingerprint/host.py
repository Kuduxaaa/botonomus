"""Facts about the machine a persona must stay within."""

import math
import os
import sys
from dataclasses import dataclass

import psutil

from ..errors import ConfigurationError

_MIN_DEVICE_MEMORY = 2
_MAX_DEVICE_MEMORY = 32


@dataclass(frozen=True, slots=True)
class HostInfo:
    """Hardware of the host that will run the browser.

    A persona never claims more than the host can back up: detectors measure
    parallel speed-up and proof-of-work timing, so extra cores or memory show.

    Attributes:
        logical_cpus: Logical processor count (what stock Chrome reports as
            ``navigator.hardwareConcurrency``).
        memory_gb: Total physical memory in GiB.
        platform: ``sys.platform`` of the host, for example ``"win32"``.
        screen: Primary display resolution in physical pixels, or ``None`` when
            unknown (a persona then keeps the real screen).
        scale: Display scale factor (``devicePixelRatio`` at 100% zoom).
        windows_server: Whether the host runs Windows Server, whose UA-CH
            ``platformVersion`` differs from consumer Windows.

    Raises:
        ConfigurationError: If ``logical_cpus`` is not a positive integer,
            ``memory_gb`` or ``scale`` is not a positive finite number, or
            ``screen`` is not two positive integers.
    """

    logical_cpus: int
    memory_gb: float
    platform: str
    screen: tuple[int, int] | None = None
    scale: float = 1.0
    windows_server: bool = False

    def __post_init__(self) -> None:
        cpus = self.logical_cpus
        if isinstance(cpus, bool) or not isinstance(cpus, int) or cpus < 1:
            raise ConfigurationError("Host logical CPU count must be a positive integer")
        memory = self.memory_gb
        if (
            isinstance(memory, bool)
            or not isinstance(memory, int | float)
            or not math.isfinite(memory)
            or memory <= 0
        ):
            raise ConfigurationError("Host memory must be a positive number of GB")
        if not isinstance(self.platform, str) or not self.platform:
            raise ConfigurationError("Host platform must be a non-empty string")
        if self.screen is not None and (
            not isinstance(self.screen, tuple)
            or len(self.screen) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in self.screen)
        ):
            raise ConfigurationError("Host screen must be (width, height) in pixels")
        scale = self.scale
        if (
            isinstance(scale, bool)
            or not isinstance(scale, int | float)
            or not math.isfinite(scale)
            or scale <= 0
        ):
            raise ConfigurationError("Host scale must be a positive number")
        if not isinstance(self.windows_server, bool):
            raise ConfigurationError("windows_server must be a bool")

    @classmethod
    def detect(cls) -> "HostInfo":
        """Measure the current machine.

        Blocking, but it only reads cached OS counters and returns in
        microseconds, so it is safe to call from an event loop.

        Returns:
            The detected host information.
        """
        cpus = psutil.cpu_count(logical=True) or os.cpu_count() or 1
        screen, scale = _windows_display() if sys.platform == "win32" else (None, 1.0)
        return cls(
            logical_cpus=cpus,
            memory_gb=psutil.virtual_memory().total / 2**30,
            platform=sys.platform,
            screen=screen,
            scale=scale,
            windows_server=sys.platform == "win32" and _is_windows_server(),
        )

    @property
    def device_memory(self) -> int:
        """The ``navigator.deviceMemory`` value stock Chrome reports on this host.

        Mirrors Chromium's ``ApproximatedDeviceMemory``: physical memory in MB
        is rounded to the nearest power of two (ties round down), converted to
        GB and clamped to the desktop range 2-32. A "16 GB" machine with 15.8 GiB
        usable therefore reports 16, not 8.
        """
        megabytes = max(1, round(self.memory_gb * 1024))
        lower = 1 << (megabytes.bit_length() - 1)
        upper = lower << 1
        nearest = lower if megabytes - lower <= upper - megabytes else upper
        gigabytes = nearest // 1024
        return min(_MAX_DEVICE_MEMORY, max(_MIN_DEVICE_MEMORY, gigabytes))


def _windows_display() -> tuple[tuple[int, int] | None, float]:
    """The primary display's physical resolution and scale factor, or ``(None, 1.0)``."""
    try:
        import ctypes
        from ctypes import wintypes

        class DEVMODEW(ctypes.Structure):
            _fields_ = [
                ("dmDeviceName", wintypes.WCHAR * 32),
                ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD),
                ("dmSize", wintypes.WORD),
                ("dmDriverExtra", wintypes.WORD),
                ("dmFields", wintypes.DWORD),
                ("dmPositionX", wintypes.LONG),
                ("dmPositionY", wintypes.LONG),
                ("dmDisplayOrientation", wintypes.DWORD),
                ("dmDisplayFixedOutput", wintypes.DWORD),
                ("dmColor", ctypes.c_short),
                ("dmDuplex", ctypes.c_short),
                ("dmYResolution", ctypes.c_short),
                ("dmTTOption", ctypes.c_short),
                ("dmCollate", ctypes.c_short),
                ("dmFormName", wintypes.WCHAR * 32),
                ("dmLogPixels", wintypes.WORD),
                ("dmBitsPerPel", wintypes.DWORD),
                ("dmPelsWidth", wintypes.DWORD),
                ("dmPelsHeight", wintypes.DWORD),
                ("dmDisplayFlags", wintypes.DWORD),
                ("dmDisplayFrequency", wintypes.DWORD),
            ]

        user32 = getattr(ctypes, "windll").user32  # noqa: B009 (absent from non-Windows stubs)
        mode = DEVMODEW()
        mode.dmSize = ctypes.sizeof(DEVMODEW)
        if not user32.EnumDisplaySettingsW(None, -1, ctypes.byref(mode)):  # current mode
            return None, 1.0
        width, height = int(mode.dmPelsWidth), int(mode.dmPelsHeight)
        if width < 1 or height < 1:
            return None, 1.0
        # A DPI-unaware process sees scaled metrics; an aware one sees physical pixels
        # and the real DPI. Either way physical / logical is the scale.
        logical = int(user32.GetSystemMetrics(0))
        if 0 < logical != width:
            scale = width / logical
        else:
            scale = int(user32.GetDpiForSystem()) / 96 or 1.0
        return (width, height), round(scale, 2)
    except (AttributeError, OSError, ValueError):
        return None, 1.0


def _is_windows_server() -> bool:
    """Whether this Windows installation is a Server edition (``InstallationType``)."""
    try:
        import winreg

        key = winreg.OpenKey(  # type: ignore[attr-defined,unused-ignore]
            winreg.HKEY_LOCAL_MACHINE,  # type: ignore[attr-defined,unused-ignore]
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
        )
        with key:
            value, _ = winreg.QueryValueEx(key, "InstallationType")  # type: ignore[attr-defined,unused-ignore]
        return str(value).lower().startswith("server")
    except (ImportError, OSError):
        return False
