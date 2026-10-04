"""Headed browsers on Linux machines without a screen, and container-safe flags.

A headed browser on a virtual X display (Xvfb) behaves like a desktop browser: it
renders, has real window and screen geometry and never reports itself as headless.
That is the usual way to run many visible browsers on a Linux server.
"""

import asyncio
import contextlib
import os
import shutil
import sys
from collections.abc import Mapping
from typing import Final

from ..config import BrowserConfig
from ..errors import BrowserStartupError, ConfigurationError

_SMALL_SHM: Final = 512 * 2**20


def needs_virtual_display(
    config: BrowserConfig,
    platform: str = sys.platform,
    environ: Mapping[str, str] = os.environ,
) -> bool:
    """Whether a launch under ``config`` should run on a virtual display.

    Headless sessions never need one. ``virtual_display=None`` (the default) means:
    on Linux, when neither ``DISPLAY`` nor ``WAYLAND_DISPLAY`` is set.

    Args:
        config: Session configuration.
        platform: ``sys.platform`` value.
        environ: Process environment.

    Raises:
        ConfigurationError: If ``virtual_display=True`` is requested off Linux.
    """
    linux = platform.startswith("linux")
    if config.headless:
        return False  # a headless browser has no window to show
    if config.virtual_display is True:
        if not linux:
            raise ConfigurationError("virtual_display=True needs Linux with Xvfb")
        return True
    if config.virtual_display is False or not linux:
        return False
    return not (environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY"))


def container_flags(platform: str, shm_bytes: int | None, euid: int | None) -> tuple[str, ...]:
    """Flags Chrome needs in containers. None of them is visible to pages.

    Args:
        platform: ``sys.platform`` value.
        shm_bytes: Size of ``/dev/shm``, or ``None`` if unknown.
        euid: Effective user id, or ``None`` off POSIX.

    Returns:
        ``--disable-dev-shm-usage`` when ``/dev/shm`` is under 512 MB (Docker's
        default is 64 MB, which crashes renderers), and ``--no-sandbox`` when running
        as root, where Chrome refuses to start otherwise.
    """
    if not platform.startswith("linux"):
        return ()
    flags: list[str] = []
    if shm_bytes is not None and shm_bytes < _SMALL_SHM:
        flags.append("--disable-dev-shm-usage")
    if euid == 0:
        flags.append("--no-sandbox")
    return tuple(flags)


def host_container_flags() -> tuple[str, ...]:
    """`container_flags` for this machine."""
    shm: int | None = None
    with contextlib.suppress(OSError):
        shm = shutil.disk_usage("/dev/shm").total
    euid = os.geteuid() if hasattr(os, "geteuid") else None
    return container_flags(sys.platform, shm, euid)


class VirtualDisplay:
    """An Xvfb server shared by the browsers of one manager.

    Xvfb picks a free display number itself (``-displayfd``), so concurrent managers
    and processes never race for the same display.

    Args:
        width: Screen width in pixels.
        height: Screen height in pixels.
        depth: Colour depth.
        executable: The Xvfb program.
        start_timeout: Seconds to wait for the server to report its display.

    Attributes:
        display: ``":N"`` while running, else ``None``.
        width: Screen width.
        height: Screen height.
    """

    def __init__(
        self,
        *,
        width: int = 1920,
        height: int = 1080,
        depth: int = 24,
        executable: str = "Xvfb",
        start_timeout: float = 10.0,
    ) -> None:
        self.width = width
        self.height = height
        self._depth = depth
        self._executable = executable
        self._start_timeout = start_timeout
        self._process: asyncio.subprocess.Process | None = None
        self.display: str | None = None

    async def start(self) -> None:
        """Start the server. Idempotent.

        Raises:
            BrowserStartupError: If Xvfb is missing, fails or does not report a
                display in time. The server is stopped on any failure, including
                cancellation.
        """
        if self._process is not None:
            return
        read_fd, write_fd = os.pipe()
        try:
            try:
                process = await asyncio.create_subprocess_exec(
                    self._executable,
                    "-displayfd",
                    str(write_fd),
                    "-screen",
                    "0",
                    f"{self.width}x{self.height}x{self._depth}",
                    "-nolisten",
                    "tcp",
                    pass_fds=(write_fd,),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except FileNotFoundError:
                raise BrowserStartupError(
                    "Xvfb is not installed (Debian/Ubuntu: sudo apt-get install xvfb)"
                ) from None
            finally:
                os.close(write_fd)
            try:
                async with asyncio.timeout(self._start_timeout):
                    number = await asyncio.to_thread(_read_display_number, read_fd)
            except BaseException as exc:
                await _kill(process)
                if isinstance(exc, (TimeoutError, ValueError)):
                    raise BrowserStartupError("Xvfb did not report a display") from None
                raise
        finally:
            with contextlib.suppress(OSError):
                os.close(read_fd)
        self._process = process
        self.display = f":{number}"

    async def stop(self) -> None:
        """Stop the server. Idempotent."""
        process, self._process, self.display = self._process, None, None
        if process is not None:
            await _kill(process, graceful=True)


def _read_display_number(fd: int) -> int:
    """Read the line Xvfb writes to ``-displayfd``; EOF first means it failed."""
    data = b""
    while not data.endswith(b"\n"):
        chunk = os.read(fd, 16)
        if not chunk:
            break
        data += chunk
    return int(data.strip())  # ValueError when Xvfb exited without a number


async def _kill(process: asyncio.subprocess.Process, *, graceful: bool = False) -> None:
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate() if graceful else process.kill()
    try:
        await asyncio.wait_for(process.wait(), 5)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
