"""Driver adapters: how page automation attaches to an already running browser.

The backend launches the browser process itself; a driver only connects to its
loopback DevTools endpoint. Keeping launch and attachment separate is what lets
every driver share the same unobservable native launch.
"""

from typing import Any, Protocol


class Attachment(Protocol):
    """A driver's live connection to one browser."""

    context: Any
    """The default browser context, backed by the dedicated profile."""

    page: Any
    """The initial page."""

    async def request_close(self) -> None:
        """Ask the browser to exit gracefully. Never raises on an already-gone browser."""
        ...

    async def disconnect(self) -> None:
        """Drop the driver connection without closing the browser."""
        ...


class Driver(Protocol):
    """Factory for attachments; may hold a shared runtime (e.g. Playwright's)."""

    async def start(self) -> None:
        """Start the driver runtime.

        Raises:
            BrowserStartupError: If the driver package is missing or fails to start.
        """
        ...

    async def attach(self, port: int, timeout: float) -> Attachment:
        """Connect to the browser listening on loopback ``port``.

        Args:
            port: The browser's ``--remote-debugging-port``.
            timeout: Seconds allowed for the connection.
        """
        ...

    async def stop(self) -> None:
        """Release the driver runtime. Idempotent."""
        ...
