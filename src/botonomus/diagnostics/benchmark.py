"""Held-open concurrency measurement with resource guards.

Larger levels are an explicit opt-in: a level stops early when host memory runs low
or launches keep failing, and later levels are skipped after that.
"""

import asyncio
import time
from contextlib import AsyncExitStack
from typing import Any

import psutil

from ..config import BrowserConfig
from ..core import Botonomus


def parse_levels(value: str) -> list[int]:
    """Parse a comma-separated list of positive concurrency levels.

    Args:
        value: Text such as ``"1,2,5,10"``.

    Returns:
        The levels in the given order.

    Raises:
        ValueError: If any part is not a positive integer.
    """
    levels = [int(part.strip()) for part in value.split(",")]
    if not levels or any(level < 1 for level in levels):
        raise ValueError("Levels must be positive integers")
    return levels


def should_stop(available_bytes: int, total_bytes: int, failures: int) -> bool:
    """Whether to stop opening sessions: under 15% memory free, or two failures.

    Args:
        available_bytes: Host memory currently available.
        total_bytes: Total host memory.
        failures: Launch failures so far at this level.
    """
    return total_bytes <= 0 or available_bytes / total_bytes < 0.15 or failures >= 2


async def measure_level(level: int, config: BrowserConfig, url: str) -> dict[str, Any]:
    """Open up to ``level`` sessions one after another and hold them open together.

    Each session navigates to ``url``. Host memory is sampled throughout.

    Args:
        level: Sessions to hold simultaneously.
        config: Configuration for every session.
        url: Page each session loads; a local page keeps the network out of it.

    Returns:
        A JSON-serialisable report with ``requested``, ``active``, ``failures``,
        ``stopped_early``, ``peak_host_used_bytes`` and ``startup_seconds``, plus
        ``error_categories`` when launches failed.
    """
    memory = psutil.virtual_memory()
    report: dict[str, Any] = {
        "requested": level,
        "active": 0,
        "failures": 0,
        "stopped_early": False,
        "peak_host_used_bytes": memory.total - memory.available,
        "launch_mode": "sequential launch, simultaneous active sessions",
    }
    done = asyncio.Event()

    async def sample() -> None:
        while not done.is_set():
            current = psutil.virtual_memory()
            report["peak_host_used_bytes"] = max(
                report["peak_host_used_bytes"], current.total - current.available
            )
            try:
                await asyncio.wait_for(done.wait(), 0.1)
            except TimeoutError:
                pass

    sampler = asyncio.create_task(sample())
    started = time.monotonic()
    try:
        async with Botonomus(level, config=config) as bot, AsyncExitStack() as stack:
            for index in range(level):
                current = psutil.virtual_memory()
                if should_stop(current.available, current.total, report["failures"]):
                    report["stopped_early"] = True
                    break
                try:
                    session = await stack.enter_async_context(bot.open(profile=f"bench-{index}"))
                    await session.page.goto(url)
                    report["active"] += 1
                except Exception as exc:  # recorded per launch; the level continues
                    report["failures"] += 1
                    report.setdefault("error_categories", []).append(type(exc).__name__)
            report["startup_seconds"] = round(time.monotonic() - started, 3)
            await asyncio.sleep(0.2)
    finally:
        done.set()
        await sampler
    return report


async def run_benchmark(levels: list[int], config: BrowserConfig, url: str) -> list[dict[str, Any]]:
    """Measure each level in turn, stopping after a level that hit a guard.

    Args:
        levels: Concurrency levels, measured in order.
        config: Configuration for every session.
        url: Page each session loads.

    Returns:
        One `measure_level` report per measured level.
    """
    reports = []
    for level in levels:
        report = await measure_level(level, config, url)
        reports.append(report)
        if report["stopped_early"] or report["failures"] >= 2:
            break
    return reports
