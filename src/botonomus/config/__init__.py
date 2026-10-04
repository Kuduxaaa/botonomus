"""Validated, immutable configuration objects."""

from .browser import DRIVERS, OWNED_FLAGS, BrowserConfig, DriverName, validate_args
from .proxy import PROXY_SCHEMES, ProxySpec, parse_proxy

__all__ = [
    "DRIVERS",
    "OWNED_FLAGS",
    "PROXY_SCHEMES",
    "BrowserConfig",
    "DriverName",
    "ProxySpec",
    "parse_proxy",
    "validate_args",
]
