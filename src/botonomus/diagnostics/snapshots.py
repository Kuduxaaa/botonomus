"""Capturing probe snapshots from a normally launched and an automated browser.

Both browsers load the identical local probe page, which posts its own snapshot back
to [`ProbeServer`][botonomus.diagnostics.ProbeServer], so collection works with any driver
and needs no page evaluation.
"""

import asyncio
from pathlib import Path
from typing import Any

import psutil

from ..browser.process import descendants, stop_owned
from ..config import BrowserConfig
from ..core import Botonomus
from .probe import ProbeServer


async def automated_snapshot(
    config: BrowserConfig, server: ProbeServer, *, profile: str = "probe", timeout: float = 30
) -> dict[str, Any]:
    """Open one session under ``config``, load the probe and return its snapshot.

    Args:
        config: Session configuration (driver, executable, proxy, ...).
        server: A running probe server.
        profile: Profile name under ``config.profile_root``.
        timeout: Seconds to wait for the snapshot after navigation.

    Returns:
        The version-1 snapshot the probe posted.

    Raises:
        queue.Empty: If no snapshot arrives in time.
        BotonomusError: If the session cannot be opened.
    """
    async with Botonomus(config=config) as bot, bot.open(profile=profile) as session:
        await session.page.goto(server.url)
        return await asyncio.to_thread(server.receive, timeout)


async def normal_snapshot(
    executable: Path, profile_dir: Path, server: ProbeServer, *, timeout: float = 30
) -> dict[str, Any]:
    """Launch ``executable`` like a user would and return the probe's snapshot.

    The browser gets no debugging port and receives no automation commands; it only
    gets a throwaway profile and the probe URL.

    Args:
        executable: Browser executable.
        profile_dir: Empty directory used as ``--user-data-dir``.
        server: A running probe server.
        timeout: Seconds to wait for the snapshot.

    Returns:
        The version-1 snapshot the probe posted.

    Raises:
        queue.Empty: If no snapshot arrives in time.
        OSError: If the browser cannot be started.
    """
    process = await asyncio.create_subprocess_exec(
        str(executable),
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        server.url,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    identity = psutil.Process(process.pid)
    identity.create_time()  # cache identity before any later PID reuse
    try:
        return await asyncio.to_thread(server.receive, timeout)
    finally:
        children = await asyncio.to_thread(descendants, identity)
        await asyncio.to_thread(stop_owned, [*children, identity], 5)
        await asyncio.wait_for(process.wait(), 5)
