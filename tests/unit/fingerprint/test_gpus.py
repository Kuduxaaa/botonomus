import re

import pytest

from botonomus.errors import ConfigurationError
from botonomus.fingerprint import GPU_TABLE, GpuOverride, gpu_family, same_family_gpus

ANGLE = re.compile(
    r"ANGLE \((NVIDIA|AMD|Intel), .+ \(0x[0-9A-F]{8}\) Direct3D11 vs_5_0 ps_5_0, D3D11\)"
)


def test_table_covers_each_family_with_real_angle_strings():
    assert set(GPU_TABLE) == {"nvidia", "amd", "intel"}
    for family, gpus in GPU_TABLE.items():
        assert gpus
        assert len(set(gpus)) == len(gpus)
        for gpu in gpus:
            match = ANGLE.fullmatch(gpu.renderer)
            assert match is not None
            assert gpu.vendor == f"Google Inc. ({match.group(1)})"
            assert gpu_family(gpu.renderer) == family
            assert gpu_family(gpu.vendor) == family


def test_table_is_read_only():
    with pytest.raises(TypeError):
        GPU_TABLE["nvidia"] = ()


@pytest.mark.parametrize(
    ("vendor", "family"),
    [
        ("NVIDIA", "nvidia"),
        ("Google Inc. (NVIDIA)", "nvidia"),
        ("Google Inc. (AMD)", "amd"),
        ("ATI Technologies Inc.", "amd"),
        ("ANGLE (AMD, AMD Radeon RX 6600 (0x000073FF) Direct3D11 vs_5_0 ps_5_0, D3D11)", "amd"),
        ("intel", "intel"),
        ("Google Inc. (Intel)", "intel"),
    ],
)
def test_same_family_gpus(vendor, family):
    assert gpu_family(vendor) == family
    assert same_family_gpus(vendor) == GPU_TABLE[family]


@pytest.mark.parametrize(
    "vendor",
    ["", "Google Inc. (Google)", "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device))", "Microsoft"],
)
def test_unknown_family_has_no_safe_overrides(vendor):
    assert gpu_family(vendor) is None
    assert same_family_gpus(vendor) == ()


@pytest.mark.parametrize(
    "value",
    [
        "",
        " leading",
        "trailing ",
        "line\nbreak",
        "tab\there",
        "nul\x00",
        "del\x7f",
        "a=b",
        'quote"',
        "back\\slash",
        "non-ascii é",
        "x" * 257,
        None,
        42,
    ],
)
def test_invalid_gpu_strings_rejected(value):
    with pytest.raises(ConfigurationError):
        GpuOverride(vendor=value, renderer="ANGLE (NVIDIA, x)")
    with pytest.raises(ConfigurationError):
        GpuOverride(vendor="Google Inc. (NVIDIA)", renderer=value)


def test_maximum_length_accepted():
    GpuOverride(vendor="v" * 256, renderer="r" * 256)
