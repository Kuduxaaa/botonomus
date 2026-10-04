"""How many tabs a machine can keep loading at once."""

import os

import psutil

_MB = 1024 * 1024
_BROWSER_BASE = 300 * _MB  # browser, GPU and network processes, shared by every tab
_PER_TAB = 150 * _MB  # one renderer on a typical page
_TABS_PER_CPU = 4


def auto_tabs(available: int, cpus: int) -> int:
    """Concurrent tabs for ``available`` bytes of free memory and ``cpus`` CPUs (at least 1)."""
    by_memory = (available - _BROWSER_BASE) // _PER_TAB
    return int(max(1, min(by_memory, cpus * _TABS_PER_CPU)))


def default_tabs() -> int:
    """`auto_tabs` for this machine right now."""
    return auto_tabs(psutil.virtual_memory().available, os.cpu_count() or 1)
