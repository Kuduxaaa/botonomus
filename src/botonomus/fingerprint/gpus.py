"""Opt-in WebGL GPU string overrides and a curated table of real Windows values.

Overriding the GPU only changes the strings ``WEBGL_debug_renderer_info``
reports. Every rendered pixel still comes from the real GPU, so render-hash
checks (Picasso-style canvases, CreepJS ``hasBadWebGL``) catch a claim from a
different vendor family. Pick an override from `same_family_gpus` for the
host's actual vendor, or keep the default (no override).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from ..errors import ConfigurationError

type GpuFamily = Literal["nvidia", "amd", "intel"]
"""GPU vendor family."""

_MAX_LENGTH: Final = 256
# Quotes and backslashes are rewritten by Windows argv quoting rules and '=' is
# never part of a real ANGLE string; refusing them keeps the switch value
# byte-identical on the C++ side.
_FORBIDDEN: Final = frozenset('"\\=')


def _check_gpu_string(value: object, field: str) -> None:
    if not isinstance(value, str) or not 0 < len(value) <= _MAX_LENGTH:
        raise ConfigurationError(f"GPU {field} must be a string of 1-{_MAX_LENGTH} characters")
    if value != value.strip():
        raise ConfigurationError(f"GPU {field} must not start or end with whitespace")
    if any(not " " <= char <= "~" or char in _FORBIDDEN for char in value):
        raise ConfigurationError(
            f'GPU {field} must be printable ASCII without ", \\ or = characters'
        )


@dataclass(frozen=True, slots=True)
class GpuOverride:
    """Unmasked WebGL vendor and renderer strings to report.

    Rules (enforced on construction): each string is 1-256 printable ASCII
    characters (``0x20``-``0x7E``) with no leading or trailing whitespace and no
    ``"``, ``\\`` or ``=``.

    Attributes:
        vendor: ``UNMASKED_VENDOR_WEBGL``, for example ``"Google Inc. (NVIDIA)"``.
        renderer: ``UNMASKED_RENDERER_WEBGL``, for example
            ``"ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002504) Direct3D11
            vs_5_0 ps_5_0, D3D11)"``.

    Raises:
        ConfigurationError: If either string breaks the rules above.
    """

    vendor: str
    renderer: str

    def __post_init__(self) -> None:
        _check_gpu_string(self.vendor, "vendor")
        _check_gpu_string(self.renderer, "renderer")


def _angle(family: str, device: str, device_id: int) -> GpuOverride:
    return GpuOverride(
        vendor=f"Google Inc. ({family})",
        renderer=(
            f"ANGLE ({family}, {device} (0x{device_id:08X}) Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ),
    )


GPU_TABLE: Final[Mapping[GpuFamily, tuple[GpuOverride, ...]]] = MappingProxyType(
    {
        "nvidia": (
            _angle("NVIDIA", "NVIDIA GeForce GTX 1060 6GB", 0x1C03),
            _angle("NVIDIA", "NVIDIA GeForce GTX 1650", 0x1F82),
            _angle("NVIDIA", "NVIDIA GeForce GTX 1660 SUPER", 0x21C4),
            _angle("NVIDIA", "NVIDIA GeForce RTX 2060", 0x1F08),
            _angle("NVIDIA", "NVIDIA GeForce RTX 3050", 0x2507),
            _angle("NVIDIA", "NVIDIA GeForce RTX 3060", 0x2504),
            _angle("NVIDIA", "NVIDIA GeForce RTX 3070", 0x2484),
            _angle("NVIDIA", "NVIDIA GeForce RTX 4060", 0x2882),
            _angle("NVIDIA", "NVIDIA GeForce RTX 4070", 0x2786),
        ),
        "amd": (
            _angle("AMD", "Radeon RX 580 Series", 0x67DF),
            _angle("AMD", "AMD Radeon RX 6600", 0x73FF),
            _angle("AMD", "AMD Radeon RX 6700 XT", 0x73DF),
            _angle("AMD", "AMD Radeon RX 7600", 0x7480),
            _angle("AMD", "AMD Radeon(TM) Graphics", 0x1638),
        ),
        "intel": (
            _angle("Intel", "Intel(R) UHD Graphics 620", 0x5917),
            _angle("Intel", "Intel(R) UHD Graphics 630", 0x3E92),
            _angle("Intel", "Intel(R) UHD Graphics 770", 0x4680),
            _angle("Intel", "Intel(R) Iris(R) Xe Graphics", 0x9A49),
        ),
    }
)
"""Real Windows Chrome WebGL strings (ANGLE on Direct3D 11) grouped by vendor family.

The format matches what stock Chrome reports on Windows; device IDs are the PCI
IDs of the listed parts. Every value is a plausible desktop or laptop GPU, but
only values from the host's own family are safe to claim.
"""


def gpu_family(vendor: str) -> GpuFamily | None:
    """Classify a vendor or renderer string into a GPU family.

    Args:
        vendor: Any vendor-identifying string, for example ``"NVIDIA"``,
            ``"Google Inc. (AMD)"`` or a full ANGLE renderer string.

    Returns:
        The family, or ``None`` when the string names no known family (including
        software renderers such as SwiftShader or Microsoft Basic Render Driver).
    """
    text = vendor.lower()
    if "nvidia" in text:
        return "nvidia"
    if "amd" in text or "radeon" in text or "ati technologies" in text:
        return "amd"
    if "intel" in text:
        return "intel"
    return None


def same_family_gpus(host_vendor: str) -> tuple[GpuOverride, ...]:
    """Overrides that stay within the host GPU's vendor family.

    Args:
        host_vendor: The host's real vendor or renderer string (see
            `gpu_family` for accepted forms).

    Returns:
        The curated overrides for that family; empty when the family is unknown,
        because no override can be backed by the rendered output.
    """
    family = gpu_family(host_vendor)
    return GPU_TABLE[family] if family is not None else ()
