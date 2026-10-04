"""Seeded personas and the Botonomus Chromium switches that apply them.

A seed (by default derived from the profile name and a per-installation key)
becomes a `Persona` whose values are realistic for Windows desktops and
never exceed the host's hardware; `Persona.to_switches` renders it as
``--bn-*`` command-line switches for Botonomus Chromium.
"""

from . import switches
from .gpus import GPU_TABLE, GpuFamily, GpuOverride, gpu_family, same_family_gpus
from .host import HostInfo
from .persona import (
    DEVICE_MEMORY_WEIGHTS,
    HARDWARE_CONCURRENCY_WEIGHTS,
    Persona,
    PersonaSpec,
    resolve_persona,
)
from .seed import persona_seed

__all__ = [
    "DEVICE_MEMORY_WEIGHTS",
    "GPU_TABLE",
    "HARDWARE_CONCURRENCY_WEIGHTS",
    "GpuFamily",
    "GpuOverride",
    "HostInfo",
    "Persona",
    "PersonaSpec",
    "gpu_family",
    "persona_seed",
    "resolve_persona",
    "same_family_gpus",
    "switches",
]
