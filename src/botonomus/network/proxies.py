"""Proxy list files and bulk proxy health checks."""

import asyncio
import ssl
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..config import parse_proxy
from ..errors import ConfigurationError
from .geoip import DEFAULT_PROVIDERS, ExitInfo, ExitLookupError, GeoProvider, exit_info
from .tunnel import UpstreamError


def load_proxies(path: str | Path) -> list[str]:
    """Read one proxy URL per line.

    Blank lines and lines starting with ``#`` are ignored. Every entry is validated
    up front, without echoing credentials in errors.

    Args:
        path: A UTF-8 text file.

    Returns:
        The proxy URLs in file order.

    Raises:
        ConfigurationError: If any entry is not a valid proxy URL.
        OSError: If the file cannot be read.
    """
    proxies = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            parse_proxy(line)
            proxies.append(line)
    return proxies


@dataclass(frozen=True, slots=True)
class ProxyCheck:
    """The outcome of checking one proxy. Never contains credentials.

    Attributes:
        address: ``host:port`` of the proxy, or ``entry N`` (1-based) when the
            URL itself is invalid.
        ok: Whether an exit lookup through the proxy succeeded.
        error: Failure category, ``None`` on success: ``invalid`` (malformed
            URL), ``unreachable`` (cannot connect to the proxy), ``timeout``,
            ``tls`` (TLS to the proxy or lookup service failed) or ``upstream``
            (the proxy refused the tunnel or the lookup service failed).
        exit: The exit as seen by the lookup service, ``None`` on failure.
    """

    address: str
    ok: bool
    error: str | None
    exit: ExitInfo | None


def error_category(exc: BaseException) -> str:
    """Map a check failure to a `ProxyCheck` error category.

    For [`ExitLookupError`][botonomus.network.ExitLookupError] the category of the last
    provider attempt is used.

    Args:
        exc: The exception raised while checking a proxy.

    Returns:
        One of ``invalid``, ``timeout``, ``tls``, ``unreachable`` or ``upstream``.
    """
    if isinstance(exc, ExitLookupError) and exc.causes:
        exc = exc.causes[-1][1]
    if isinstance(exc, ConfigurationError):
        return "invalid"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ssl.SSLError):
        return "tls"
    if isinstance(exc, UpstreamError | asyncio.IncompleteReadError):
        return "upstream"
    return "unreachable"


async def check_proxies(
    urls: Iterable[str],
    *,
    parallel: int = 16,
    timeout: float = 20.0,
    providers: Sequence[GeoProvider] = DEFAULT_PROVIDERS,
) -> list[ProxyCheck]:
    """Check proxies concurrently by looking up each one's exit through it.

    A failing proxy never fails the batch; it is reported in its result.

    Args:
        urls: Proxy URLs, for example from `load_proxies`.
        parallel: Maximum number of checks in flight.
        timeout: Seconds allowed per lookup provider attempt.
        providers: Exit lookup services, tried in order for each proxy.

    Returns:
        One result per URL, in input order.

    Raises:
        ValueError: If ``parallel`` is less than 1.
    """
    if parallel < 1:
        raise ValueError("parallel must be at least 1")
    limiter = asyncio.Semaphore(parallel)

    async def check(index: int, url: str) -> ProxyCheck:
        try:
            spec = parse_proxy(url)
        except ConfigurationError as exc:
            return ProxyCheck(f"entry {index}", False, error_category(exc), None)
        async with limiter:
            try:
                info = await exit_info(spec, timeout, providers)
            except (ExitLookupError, OSError, UpstreamError, TimeoutError) as exc:
                return ProxyCheck(spec.address, False, error_category(exc), None)
        return ProxyCheck(spec.address, True, None, info)

    return list(await asyncio.gather(*(check(i, url) for i, url in enumerate(urls, start=1))))
