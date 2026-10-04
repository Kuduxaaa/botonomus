"""Botonomus Chromium persona switch names.

These names are the contract with the C++ patch set, which reads exactly these
switches. This module is the only place they are spelled on the Python side.
"""

from typing import Final

SEED: Final = "--bn-seed"
"""64-bit noise key as 16 lowercase hex digits; C++ derives per-site keys from it."""

TIMEZONE: Final = "--bn-timezone"
"""IANA time zone applied to ICU in every process."""

HARDWARE_CONCURRENCY: Final = "--bn-hardware-concurrency"
"""Value of ``navigator.hardwareConcurrency`` in windows and workers."""

DEVICE_MEMORY: Final = "--bn-device-memory"
"""Value of ``navigator.deviceMemory`` in GB; one of `DEVICE_MEMORY_VALUES`."""

GPU_VENDOR: Final = "--bn-gpu-vendor"
"""``UNMASKED_VENDOR_WEBGL`` override; only emitted for an explicit GPU override."""

GPU_RENDERER: Final = "--bn-gpu-renderer"
"""``UNMASKED_RENDERER_WEBGL`` override; only emitted for an explicit GPU override."""

NOISE: Final = "--bn-noise"
"""``--bn-noise=0`` disables canvas, WebGL and audio noise (debugging, measurement)."""

SCREEN: Final = "--bn-screen"
"""Screen size in CSS pixels as ``WxH`` (JS ``screen``, CSS media queries, every frame)."""

TASKBAR: Final = "--bn-taskbar"
"""Taskbar height in CSS pixels; ``screen.availHeight`` is the screen height minus it."""

DEVICE_MEMORY_VALUES: Final = frozenset({2, 4, 8, 16, 32})
"""Values Chromium 155 can report on desktop (clamped to 2-32 GB, crbug 454354290)."""

PERSONA_SWITCHES: Final = frozenset(
    {
        SEED,
        TIMEZONE,
        HARDWARE_CONCURRENCY,
        DEVICE_MEMORY,
        GPU_VENDOR,
        GPU_RENDERER,
        NOISE,
        SCREEN,
        TASKBAR,
    }
)
"""Every persona switch name, for callers that must refuse user-supplied duplicates."""
