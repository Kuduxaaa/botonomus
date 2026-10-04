"""Public session handles. Cleanup is owned by the manager that opened them."""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..browser import BrowserHandle
from ..fingerprint import Persona
from ..human import HumanConfig, HumanPage
from ..network import ExitInfo

if TYPE_CHECKING:
    from .identity import LaunchIdentity


class Session:
    """One open browser with its dedicated profile.

    Obtained from `Botonomus.open`; do not construct directly.

    Attributes:
        profile: The normalized profile name.
    """

    def __init__(
        self,
        profile: str,
        handle: BrowserHandle,
        close: Callable[[], Awaitable[None]],
        *,
        identity: "LaunchIdentity | None" = None,
        human: HumanConfig | None = None,
    ) -> None:
        self.profile = profile
        self._handle = handle
        self._close = close
        self._identity = identity
        self._human = human
        self._page: Any = None

    @property
    def page(self) -> Any:
        """The initial page.

        A [`botonomus.cdp.Page`][botonomus.cdp.Page] or a Playwright ``Page``. With ``humanize``
        enabled it is wrapped in [`HumanPage`][botonomus.human.HumanPage], whose ``raw``
        attribute returns the unwrapped page.
        """
        if self._page is None:
            page = self._handle.page
            self._page = HumanPage(page, config=self._human) if self._human else page
        return self._page

    @property
    def context(self) -> Any:
        """The profile-backed browser context."""
        return self._handle.context

    @property
    def persona(self) -> Persona | None:
        """The fingerprint persona applied at launch, if any."""
        return self._identity.persona if self._identity else None

    @property
    def exit(self) -> ExitInfo | None:
        """The proxy exit used to align locale and time zone, if ``geoip`` was on."""
        return self._identity.exit if self._identity else None

    @property
    def executable(self) -> Path | None:
        """The browser executable, when resolved by the manager."""
        return self._identity.executable if self._identity else None

    async def close(self) -> None:
        """Close the browser early. The manager's context exit then does nothing."""
        await self._close()
