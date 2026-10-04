"""Errors raised by the native CDP driver. All derive from ``BotonomusError``."""

from ..errors import BotonomusError


class ProtocolError(BotonomusError):
    """The browser rejected a CDP command.

    Attributes:
        method: The CDP method that failed.
        code: The protocol error code, if the browser supplied one.
    """

    def __init__(self, method: str, error: dict[str, object]) -> None:
        super().__init__(f"{method}: {error.get('message', 'error')} ({error.get('code')})")
        self.method = method
        self.code = error.get("code")


class TargetClosedError(BotonomusError, ConnectionError):
    """The connection or target session went away while a command was pending."""


class NavigationError(BotonomusError):
    """The browser reported a navigation failure."""


class EvaluationError(BotonomusError):
    """JavaScript threw during evaluation."""


class TimeoutError_(BotonomusError, TimeoutError):
    """A page operation did not complete within its timeout.

    Subclasses the built-in `TimeoutError`, so ``except TimeoutError`` works.
    """
