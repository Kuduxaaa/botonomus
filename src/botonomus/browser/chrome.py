"""Chrome backend: native process launch, then loopback CDP attachment.

The browser starts exactly as a user would start it, plus a dedicated profile and
an explicit loopback debugging port. The driver attaches afterwards without
applying context overrides, so pages observe an ordinary browser.
"""

import asyncio
import logging
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

import psutil

from ..config import BrowserConfig, DriverName
from ..drivers import Attachment, Driver, create_driver
from ..errors import BrowserCleanupError, BrowserStartupError
from ..network import ProxyForwarder
from .arguments import launch_arguments
from .discovery import find_chrome, is_testing_build
from .display import VirtualDisplay, host_container_flags, needs_virtual_display
from .process import available_port, descendants, owns_listener, stop_owned

_log = logging.getLogger("botonomus")


class ChromeHandle:
    """One launched browser process, its attachment and its proxy forwarder.

    Args:
        process: The browser's root process.
        timeout: Seconds for the whole close sequence (graceful, then forced).
    """

    def __init__(self, process: asyncio.subprocess.Process, timeout: float) -> None:
        self.process = process
        self.identity = psutil.Process(process.pid)
        self.identity.create_time()  # Cache identity before any later PID reuse.
        self.attachment: Attachment | None = None
        self.forwarder: ProxyForwarder | None = None
        self._timeout = timeout
        self._close_task: asyncio.Task[None] | None = None

    @property
    def context(self) -> Any:
        """The driver's browser context.

        Raises:
            BrowserStartupError: If the driver has not attached yet.
        """
        if self.attachment is None:
            raise BrowserStartupError("Browser context is not ready")
        return self.attachment.context

    @property
    def page(self) -> Any:
        """The driver's initial page.

        Raises:
            BrowserStartupError: If the driver has not attached yet.
        """
        if self.attachment is None:
            raise BrowserStartupError("Browser page is not ready")
        return self.attachment.page

    async def close(self) -> None:
        """Close gracefully, then terminate owned processes. Idempotent and shielded.

        Raises:
            BrowserCleanupError: If owned processes could not be confirmed stopped.
        """
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        children = await asyncio.to_thread(descendants, self.identity)
        try:
            if self.attachment is not None:
                with suppress(TimeoutError):
                    async with asyncio.timeout(self._timeout / 2):
                        await self.attachment.request_close()
                        await self.process.wait()
            await asyncio.to_thread(stop_owned, [*children, self.identity], self._timeout / 2)
            await asyncio.wait_for(self.process.wait(), self._timeout / 2)
            if self.attachment is not None:
                with suppress(TimeoutError):
                    await asyncio.wait_for(self.attachment.disconnect(), self._timeout / 2)
            if self.forwarder is not None:
                await asyncio.wait_for(self.forwarder.close(), self._timeout / 2)
        except Exception as exc:
            raise BrowserCleanupError("Could not confirm browser process cleanup") from exc


class ChromeBackend:
    """Launches Chrome (or a named Chromium build) and attaches a driver.

    Args:
        driver: A driver name, or a [`Driver`][botonomus.drivers.Driver] instance.
    """

    def __init__(self, driver: DriverName | Driver = "native") -> None:
        self._driver: Driver = create_driver(driver) if isinstance(driver, str) else driver
        self._started = False
        self._handles: set[ChromeHandle] = set()
        self._display: VirtualDisplay | None = None
        self._display_lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the driver runtime. Idempotent.

        Raises:
            BrowserStartupError: If the driver cannot start.
        """
        if not self._started:
            await self._driver.start()
            self._started = True

    async def launch(self, profile_path: Path, config: BrowserConfig) -> ChromeHandle:
        """Launch a browser for ``profile_path`` and attach the driver.

        Raises:
            BrowserUnavailableError: If no executable is found.
            ConfigurationError: If ``virtual_display=True`` is requested off Linux.
            BrowserStartupError: If launch, attachment or the virtual display fails or
                times out.
            BrowserCleanupError: If a failed launch could not be cleaned up.
        """
        executable = find_chrome(config.executable_path)
        if is_testing_build(executable):
            _log.warning("testing_build_executable")
        if not self._started:
            raise BrowserStartupError("Backend is not started")
        # Outside the try below, so display errors keep their own message and type.
        platform_args, env = await self._platform(config)
        port = available_port()
        handle: ChromeHandle | None = None
        forwarder: ProxyForwarder | None = None
        try:
            proxy_server = None
            if (proxy := config.proxy_spec) is not None:
                proxy_server = proxy.server
                if proxy.has_credentials:
                    forwarder = ProxyForwarder(proxy)
                    await forwarder.start()
                    proxy_server = forwarder.server
            arguments = launch_arguments(
                executable,
                profile_path,
                port,
                config,
                proxy_server=proxy_server,
                platform_args=platform_args,
            )
            process = await asyncio.create_subprocess_exec(
                *arguments,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
            )
            handle = ChromeHandle(process, config.close_timeout)
            handle.forwarder, forwarder = forwarder, None
            self._handles.add(handle)
            async with asyncio.timeout(config.launch_timeout):
                while not await asyncio.to_thread(owns_listener, handle.identity, port):
                    if process.returncode is not None:
                        raise BrowserStartupError("Browser exited before its endpoint was ready")
                    await asyncio.sleep(0.05)
                handle.attachment = await self._driver.attach(port, config.launch_timeout)
            return handle
        except BaseException as exc:
            if handle is not None:
                await handle.close()
                self._handles.discard(handle)
            if forwarder is not None:
                await forwarder.close()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise BrowserStartupError("Browser launch or attachment failed") from exc

    async def _platform(
        self, config: BrowserConfig
    ) -> tuple[tuple[str, ...], dict[str, str] | None]:
        """Container flags, plus the shared virtual display when ``config`` needs one.

        Returns:
            Extra launch flags and the environment for the browser process (``None``
            inherits this process's environment).
        """
        flags = host_container_flags()
        if not needs_virtual_display(config):
            return flags, None
        async with self._display_lock:
            if self._display is None:
                display = VirtualDisplay()
                await display.start()
                self._display = display
        assert self._display.display is not None
        geometry = (
            # Chrome would otherwise prefer a Wayland session it inherited.
            "--ozone-platform=x11",
            "--window-position=0,0",
            f"--window-size={self._display.width},{self._display.height}",
        )
        env = {k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY"}
        env["DISPLAY"] = self._display.display
        return (*flags, *geometry), env

    async def close(self) -> None:
        """Stop every owned browser, then the virtual display and the driver runtime.

        Raises:
            BrowserCleanupError: If any owned browser could not be stopped; the driver
                is left running and ownership retained. The virtual display is stopped
                regardless, so no Xvfb server outlives the manager.
        """
        results = await asyncio.gather(*(h.close() for h in self._handles), return_exceptions=True)
        if self._display is not None:
            display, self._display = self._display, None
            await display.stop()
        if any(isinstance(result, BaseException) for result in results):
            raise BrowserCleanupError("Some owned browsers could not be stopped")
        self._handles.clear()
        await self._driver.stop()
        self._started = False
