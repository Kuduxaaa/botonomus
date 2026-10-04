"""Polling and expression helpers shared by pages and locators."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .errors import EvaluationError, TimeoutError_


async def poll(
    check: Callable[[], Awaitable[Any]], timeout: float, interval: float, what: str
) -> Any:
    """Await ``check`` repeatedly until it returns a truthy value.

    Evaluation errors (for example a navigation destroying the context mid-check)
    count as "not yet".

    Args:
        check: Coroutine factory evaluated each round.
        timeout: Seconds before giving up.
        interval: Seconds between rounds.
        what: Description used in the timeout message.

    Returns:
        The first truthy value.

    Raises:
        TimeoutError_: If ``timeout`` elapses first.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            value = await check()
            if value:
                return value
        except EvaluationError:
            pass
        if time.monotonic() >= deadline:
            raise TimeoutError_(f"Timed out after {timeout}s waiting for {what}")
        await asyncio.sleep(interval)


def looks_like_function(source: str) -> bool:
    """Whether ``source`` is a function expression rather than a plain expression."""
    head = source.split("=>", 1)[0]
    return source.startswith(("function", "async function", "async (")) or (
        "=>" in source and (head.strip().startswith("(") or head.strip().isidentifier())
    )
