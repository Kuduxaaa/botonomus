"""Driver adapters that attach page automation to a launched browser."""

from ..config import DriverName
from ..errors import ConfigurationError
from .base import Attachment, Driver
from .native import NativeAttachment, NativeDriver
from .playwright import PlaywrightAttachment, PlaywrightDriver


def create_driver(name: DriverName) -> Driver:
    """Return the driver adapter for ``name``.

    Raises:
        ConfigurationError: If ``name`` is not a known driver.
    """
    if name == "native":
        return NativeDriver()
    if name in ("playwright", "patchright"):
        return PlaywrightDriver(name)
    raise ConfigurationError("driver must be 'native', 'patchright' or 'playwright'")


__all__ = [
    "Attachment",
    "Driver",
    "NativeAttachment",
    "NativeDriver",
    "PlaywrightAttachment",
    "PlaywrightDriver",
    "create_driver",
]
