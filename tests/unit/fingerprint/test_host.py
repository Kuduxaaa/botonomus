import math

import pytest

from botonomus.errors import ConfigurationError
from botonomus.fingerprint import HostInfo


def test_detect_reports_this_machine():
    host = HostInfo.detect()
    assert host.logical_cpus >= 1
    assert host.memory_gb > 0
    assert host.platform


@pytest.mark.parametrize(
    ("memory_gb", "expected"),
    [
        (0.5, 2),
        (1.9, 2),
        (3.8, 4),
        (7.85, 8),
        (11.9, 8),
        (12.0, 8),  # exact tie between 8 and 16 rounds down, like Chromium
        (12.1, 16),
        (15.7, 16),
        (31.7, 32),
        (64.0, 32),
        (128.0, 32),
    ],
)
def test_device_memory_matches_chromium_approximation(memory_gb, expected):
    assert HostInfo(4, memory_gb, "win32").device_memory == expected


@pytest.mark.parametrize("cpus", [0, -1, True, 2.0, "4", None])
def test_invalid_cpu_count_rejected(cpus):
    with pytest.raises(ConfigurationError):
        HostInfo(cpus, 8.0, "win32")


@pytest.mark.parametrize("memory", [0, -1.0, math.inf, math.nan, True, "8", None])
def test_invalid_memory_rejected(memory):
    with pytest.raises(ConfigurationError):
        HostInfo(4, memory, "win32")


@pytest.mark.parametrize("platform", ["", None, 3])
def test_invalid_platform_rejected(platform):
    with pytest.raises(ConfigurationError):
        HostInfo(4, 8.0, platform)
