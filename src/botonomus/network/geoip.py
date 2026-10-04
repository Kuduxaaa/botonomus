"""Where traffic through a proxy appears to come from.

The lookup itself runs through the proxy, so the service sees the proxy's exit
address. Results feed geo consistency: browser locale and timezone should agree
with the exit country, or detectors see a contradiction.

Lookups try a list of providers in order. The default is ip-api.com over plain
HTTP (rich data, but rate-limited to about 45 requests a minute per exit address),
then ipinfo.io over TLS. TLS to the lookup service is negotiated end to end inside
the tunnel, so the proxy never sees the request.
"""

import asyncio
import json
import ssl
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from ..config import ProxySpec
from .tunnel import UpstreamError, open_tunnel

_IP_API_FIELDS: Final = (
    "status,message,countryCode,regionName,city,timezone,isp,proxy,hosting,query"
)

MAX_RESPONSE: Final = 65536
"""Largest lookup response accepted, in bytes; services answer in well under 1 KiB."""

COUNTRY_LOCALE: Final = {
    "US": "en-US", "GB": "en-GB", "CA": "en-CA", "AU": "en-AU", "IE": "en-IE",
    "DE": "de-DE", "AT": "de-AT", "CH": "de-CH", "FR": "fr-FR", "BE": "fr-BE",
    "NL": "nl-NL", "ES": "es-ES", "MX": "es-MX", "IT": "it-IT", "PT": "pt-PT",
    "BR": "pt-BR", "PL": "pl-PL", "SE": "sv-SE", "NO": "nb-NO", "DK": "da-DK",
    "FI": "fi-FI", "CZ": "cs-CZ", "RO": "ro-RO", "UA": "uk-UA", "RU": "ru-RU",
    "TR": "tr-TR", "GE": "ka-GE", "JP": "ja-JP", "KR": "ko-KR", "CN": "zh-CN",
    "TW": "zh-TW", "IN": "en-IN", "IL": "he-IL", "GR": "el-GR", "HU": "hu-HU",
}  # fmt: skip
"""Most common primary browser locale per ISO 3166-1 alpha-2 country."""


@dataclass(frozen=True, slots=True)
class ExitInfo:
    """A proxy exit as seen by the lookup service.

    Providers differ in what they report; fields a provider does not supply are
    empty strings, and flags it does not supply are ``False``.

    Attributes:
        ip: Public exit address.
        country_code: ISO 3166-1 alpha-2 code, or empty if unknown.
        region: Region or state name.
        city: City name.
        timezone: IANA zone such as ``Europe/Berlin``.
        isp: Network operator (ipinfo.io reports the AS organisation).
        hosting: The address belongs to a data-centre range (ip-api.com only).
        flagged_proxy: The address is a known proxy, VPN or Tor exit (ip-api.com only).
        latency: Seconds for the lookup round trip through the proxy.
        source: Name of the provider that answered, such as ``ip-api``.
    """

    ip: str
    country_code: str
    region: str
    city: str
    timezone: str
    isp: str
    hosting: bool
    flagged_proxy: bool
    latency: float
    source: str = ""

    def locale(self) -> str:
        """A plausible primary browser locale for the exit country."""
        return COUNTRY_LOCALE.get(self.country_code, f"en-{self.country_code or 'US'}")


class GeoProvider(Protocol):
    """An exit lookup service reachable with one HTTP request through the tunnel."""

    @property
    def name(self) -> str:
        """Short provider name, recorded in `ExitInfo.source` and errors."""
        ...

    @property
    def host(self) -> str:
        """Hostname of the service, sent unresolved to the proxy."""
        ...

    @property
    def port(self) -> int:
        """TCP port of the service."""
        ...

    def tls_context(self) -> ssl.SSLContext | None:
        """The TLS context for the service, or ``None`` for plain HTTP."""
        ...

    def request(self) -> bytes:
        """The complete HTTP request, which must ask the server to close afterwards."""
        ...

    def parse(self, response: bytes, *, latency: float) -> ExitInfo:
        """Turn the raw HTTP response into an `ExitInfo`.

        Raises:
            UpstreamError: If the response is not a successful lookup.
        """
        ...


def _get_request(host: str, path: str) -> bytes:
    return (
        f"GET {path} HTTP/1.1\r\nHost: {host}\r\nAccept: application/json\r\n"
        "User-Agent: botonomus\r\nConnection: close\r\n\r\n"
    ).encode()


@dataclass(frozen=True, slots=True)
class IpApiProvider:
    """ip-api.com over plain HTTP: location, ISP and data-centre / proxy flags.

    The free endpoint has no TLS and allows about 45 requests per minute per exit
    address; beyond that it answers ``429``.

    Attributes:
        host: Service hostname.
        port: Service port.
    """

    host: str = "ip-api.com"
    port: int = 80
    name: str = field(default="ip-api", init=False)

    def tls_context(self) -> ssl.SSLContext | None:
        """Plain HTTP, so no TLS context."""
        return None

    def request(self) -> bytes:
        """A ``GET /json/`` request for the fields `ExitInfo` needs."""
        return _get_request(self.host, f"/json/?fields={_IP_API_FIELDS}")

    def parse(self, response: bytes, *, latency: float) -> ExitInfo:
        """Parse an ip-api.com JSON response.

        Raises:
            UpstreamError: On a non-200 status (including rate limiting), malformed
                JSON, or a ``status`` other than ``success``.
        """
        data = _json_body(response, self.name)
        if data.get("status") != "success":
            raise UpstreamError(f"{self.name} lookup failed: {data.get('message', 'unknown')}")
        if not data.get("query"):
            raise UpstreamError(f"{self.name} returned no address")
        return ExitInfo(
            ip=str(data["query"]),
            country_code=str(data.get("countryCode") or ""),
            region=str(data.get("regionName") or ""),
            city=str(data.get("city") or ""),
            timezone=str(data.get("timezone") or ""),
            isp=str(data.get("isp") or ""),
            hosting=bool(data.get("hosting")),
            flagged_proxy=bool(data.get("proxy")),
            latency=latency,
            source=self.name,
        )


@dataclass(frozen=True, slots=True)
class IpInfoProvider:
    """ipinfo.io over TLS: location, timezone and AS organisation, no risk flags.

    Attributes:
        host: Service hostname, also used for SNI and certificate verification.
        port: Service port.
        context: TLS context; ``None`` uses the system trust store.
    """

    host: str = "ipinfo.io"
    port: int = 443
    context: ssl.SSLContext | None = field(default=None, compare=False, repr=False)
    name: str = field(default="ipinfo", init=False)

    def tls_context(self) -> ssl.SSLContext:
        """The configured context, or a verifying default one."""
        return self.context or ssl.create_default_context()

    def request(self) -> bytes:
        """A ``GET /json`` request."""
        return _get_request(self.host, "/json")

    def parse(self, response: bytes, *, latency: float) -> ExitInfo:
        """Parse an ipinfo.io JSON response.

        Raises:
            UpstreamError: On a non-200 status (including rate limiting), malformed
                JSON, an ``error`` object, or a missing address.
        """
        data = _json_body(response, self.name)
        if "error" in data or not data.get("ip"):
            raise UpstreamError(f"{self.name} lookup failed")
        return ExitInfo(
            ip=str(data["ip"]),
            country_code=str(data.get("country") or ""),
            region=str(data.get("region") or ""),
            city=str(data.get("city") or ""),
            timezone=str(data.get("timezone") or ""),
            isp=str(data.get("org") or ""),
            hosting=False,
            flagged_proxy=False,
            latency=latency,
            source=self.name,
        )


DEFAULT_PROVIDERS: Final[tuple[GeoProvider, ...]] = (IpApiProvider(), IpInfoProvider())
"""ip-api.com first for its risk flags, then ipinfo.io over TLS."""


class ExitLookupError(UpstreamError):
    """Every exit lookup provider failed.

    Attributes:
        causes: ``(provider name, exception)`` for each failed attempt, in order.
    """

    def __init__(self, causes: Sequence[tuple[str, Exception]]) -> None:
        self.causes = tuple(causes)
        summary = "; ".join(f"{name}: {type(exc).__name__}: {exc}" for name, exc in causes)
        super().__init__(f"Exit lookup failed ({summary or 'no providers'})")


async def exit_info(
    spec: ProxySpec,
    timeout: float = 15.0,
    providers: Sequence[GeoProvider] = DEFAULT_PROVIDERS,
) -> ExitInfo:
    """Look up the proxy's exit address and location through the proxy itself.

    Providers are tried in order until one succeeds.

    Args:
        spec: The proxy to test.
        timeout: Seconds allowed for each provider attempt, so the whole call is
            bounded by ``timeout * len(providers)``.
        providers: Lookup services in order of preference.

    Returns:
        The exit as seen by the first provider that answered.

    Raises:
        ExitLookupError: If every provider failed; ``causes`` holds each failure
            (``UpstreamError``, ``OSError``, ``TimeoutError`` and similar).
    """
    causes: list[tuple[str, Exception]] = []
    for provider in providers:
        try:
            return await lookup(spec, provider, timeout)
        except (OSError, UpstreamError, TimeoutError, asyncio.IncompleteReadError) as exc:
            causes.append((provider.name, exc))
    error = ExitLookupError(causes)
    if causes:
        raise error from causes[-1][1]
    raise error


async def lookup(spec: ProxySpec, provider: GeoProvider, timeout: float) -> ExitInfo:
    """Run one provider's lookup through the proxy.

    Args:
        spec: The proxy to tunnel through.
        provider: The lookup service.
        timeout: Seconds allowed for connect, TLS, request and response together.

    Returns:
        The provider's view of the exit.

    Raises:
        UpstreamError: If the proxy refuses the tunnel or the service fails.
        TimeoutError: If the attempt exceeds ``timeout``.
        OSError: If the proxy cannot be reached or TLS fails (``ssl.SSLError``).
    """
    started = time.monotonic()
    async with asyncio.timeout(timeout):
        reader, writer = await open_tunnel(spec, provider.host, provider.port)
        try:
            if (context := provider.tls_context()) is not None:
                await writer.start_tls(context, server_hostname=provider.host)
            writer.write(provider.request())
            await writer.drain()
            response = await _read_bounded(reader)
        finally:
            writer.transport.abort()
    return provider.parse(response, latency=round(time.monotonic() - started, 3))


async def _read_bounded(reader: asyncio.StreamReader) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := await reader.read(MAX_RESPONSE):
        size += len(chunk)
        if size > MAX_RESPONSE:
            raise UpstreamError("Exit lookup response too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _json_body(response: bytes, name: str) -> dict[str, Any]:
    """Return the JSON object in a ``200`` HTTP/1.x response, de-chunking if needed.

    Raises:
        UpstreamError: On another status, a malformed body or a non-object payload.
    """
    head, _, body = response.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    status = lines[0].split()
    if len(status) < 2 or not status[0].startswith(b"HTTP/1."):
        raise UpstreamError(f"{name} returned a malformed response")
    if status[1] == b"429":
        raise UpstreamError(f"{name} rate limit reached")
    if status[1] != b"200":
        raise UpstreamError(f"{name} returned HTTP {status[1].decode(errors='replace')}")
    headers = {
        key.strip().lower(): value.strip().lower()
        for key, _, value in (line.partition(b":") for line in lines[1:])
    }
    try:
        if b"chunked" in headers.get(b"transfer-encoding", b""):
            body = _dechunk(body)
        data = json.loads(body)
    except ValueError as exc:
        raise UpstreamError(f"{name} returned malformed data") from exc
    if not isinstance(data, dict):
        raise UpstreamError(f"{name} returned malformed data")
    return data


def _dechunk(body: bytes) -> bytes:
    """Decode an HTTP/1.1 chunked body.

    Raises:
        ValueError: If the framing is malformed or truncated.
    """
    out = bytearray()
    while True:
        size_line, sep, body = body.partition(b"\r\n")
        if not sep:
            raise ValueError("truncated chunk header")
        size = int(size_line.split(b";", 1)[0], 16)
        if size == 0:
            return bytes(out)
        if len(body) < size + 2:
            raise ValueError("truncated chunk")
        out += body[:size]
        body = body[size + 2 :]


class ExitCache:
    """Per-proxy exit lookups, cached in memory with a time to live.

    Concurrent `get` calls for the same proxy share one lookup. Failures
    are not cached. Proxies with different credentials are distinct keys, since
    providers often pin a sticky exit to the username.

    Args:
        ttl: Seconds a successful result stays valid.
        lookup: The lookup coroutine; defaults to `exit_info` with its
            default timeout and providers.
        clock: Monotonic time source, replaceable in tests.
    """

    def __init__(
        self,
        ttl: float = 3600.0,
        *,
        lookup: Callable[[ProxySpec], Awaitable[ExitInfo]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl
        self._lookup = lookup or exit_info
        self._clock = clock
        self._results: dict[ProxySpec, tuple[float, ExitInfo]] = {}
        self._pending: dict[ProxySpec, asyncio.Task[ExitInfo]] = {}

    async def get(self, spec: ProxySpec) -> ExitInfo:
        """Return the cached exit for ``spec``, looking it up if absent or expired.

        Cancelling one caller does not cancel a lookup other callers share.

        Raises:
            ExitLookupError: Or whatever the configured lookup raises, for every
                caller waiting on the failed lookup.
        """
        cached = self._results.get(spec)
        if cached is not None and cached[0] > self._clock():
            return cached[1]
        task = self._pending.get(spec)
        if task is None:
            task = asyncio.create_task(self._run(spec))
            self._pending[spec] = task
            task.add_done_callback(lambda done: self._settle(spec, done))
        return await asyncio.shield(task)

    def invalidate(self, spec: ProxySpec | None = None) -> None:
        """Forget the result for ``spec``, or every result when ``spec`` is ``None``.

        Lookups already running are not affected.
        """
        if spec is None:
            self._results.clear()
        else:
            self._results.pop(spec, None)

    async def _run(self, spec: ProxySpec) -> ExitInfo:
        info = await self._lookup(spec)
        self._results[spec] = (self._clock() + self._ttl, info)
        return info

    def _settle(self, spec: ProxySpec, task: asyncio.Task[ExitInfo]) -> None:
        if self._pending.get(spec) is task:
            del self._pending[spec]
        if not task.cancelled():
            # Marks the exception retrieved when every waiter was cancelled.
            task.exception()
