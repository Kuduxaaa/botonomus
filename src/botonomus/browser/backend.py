"""The contract between the session manager and a browser backend.

The backend owns operating-system processes; the manager owns admission and
profile leases. A backend may only report successful cleanup once it has confirmed
that everything it started has stopped.
"""

from pathlib import Path
from typing import Any, Protocol

from ..config import BrowserConfig


class BrowserHandle(Protocol):
    """One running browser attached to a dedicated profile."""

    @property
    def context(self) -> Any:
        """The browser context (native ``Context`` or a Playwright ``BrowserContext``)."""
        ...

    @property
    def page(self) -> Any:
        """The initial page (native ``Page`` or a Playwright ``Page``)."""
        ...

    async def close(self) -> None:
        """Stop the browser.

        Returns only after every owned process is confirmed stopped.

        Raises:
            BrowserCleanupError: If that cannot be confirmed.
        """
        ...


class Backend(Protocol):
    """Starts browsers and guarantees their shutdown."""

    async def start(self) -> None:
        """Prepare shared resources such as a driver runtime. Idempotent."""
        ...

    async def launch(self, profile_path: Path, config: BrowserConfig) -> BrowserHandle:
        """Launch a browser for ``profile_path`` and attach to it.

        Raises:
            BrowserUnavailableError: If no executable is available.
            BrowserStartupError: If the process or attachment fails; anything started
                has been cleaned up.
            BrowserCleanupError: If a failed launch left processes that could not be
                confirmed stopped.
        """
        ...

    async def close(self) -> None:
        """Stop every browser this backend owns and release shared resources.

        Raises:
            BrowserCleanupError: If any owned browser could not be confirmed stopped;
                ownership is retained.
        """
        ...
