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

    Raises:
        ConfigurationError: If ``logical_cpus`` is not a positive integer or
            ``memory_gb`` is not a positive finite number.
    """

    logical_cpus: int
    memory_gb: float
    platform: str

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

    @classmethod
    def detect(cls) -> "HostInfo":
        """Measure the current machine.

        Blocking, but it only reads cached OS counters and returns in
        microseconds, so it is safe to call from an event loop.

        Returns:
            The detected host information.
        """
        cpus = psutil.cpu_count(logical=True) or os.cpu_count() or 1
        return cls(
            logical_cpus=cpus,
            memory_gb=psutil.virtual_memory().total / 2**30,
            platform=sys.platform,
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
