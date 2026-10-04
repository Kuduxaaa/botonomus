"""Time source used by human input and warm-up, replaceable for simulation and tests."""

import asyncio
from typing import Protocol


class Clock(Protocol):
    """Monotonic time and sleeping."""

    def now(self) -> float:
        """Monotonic seconds."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Wait for ``seconds``."""
        ...


class RealClock:
    """The running event loop's clock and `asyncio.sleep`."""

    def now(self) -> float:
        """The event loop's monotonic time in seconds."""
        return asyncio.get_running_loop().time()

    async def sleep(self, seconds: float) -> None:
        """Sleep for ``seconds`` (non-positive values just yield)."""
        await asyncio.sleep(max(0.0, seconds))
