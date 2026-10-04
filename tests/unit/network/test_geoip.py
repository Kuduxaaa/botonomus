import asyncio
import json
import ssl
from pathlib import Path

import pytest

from botonomus.config import parse_proxy
from botonomus.network import (
    DEFAULT_PROVIDERS,
    ExitCache,
    ExitInfo,
    ExitLookupError,
    IpApiProvider,
    IpInfoProvider,
    UpstreamError,
    exit_info,
)
from botonomus.network.geoip import MAX_RESPONSE

DATA = Path(__file__).parent / "data"
IP_API = IpApiProvider()

IP_API_BODY = {
    "status": "success",
    "countryCode": "DE",
    "regionName": "Hesse",
    "city": "Frankfurt am Main",
    "timezone": "Europe/Berlin",
    "isp": "Example Carrier",
    "proxy": False,
    "hosting": True,
    "query": "203.0.113.7",
}
IPINFO_BODY = {
    "ip": "198.51.100.9",
    "city": "Lisbon",
    "region": "Lisbon",
    "country": "PT",
    "loc": "38.7223,-9.1393",
    "org": "AS12345 Example Net",
    "timezone": "Europe/Lisbon",
}


def _http(status: str, body: bytes, *headers: str) -> bytes:
    head = "\r\n".join([f"HTTP/1.1 {status}", *headers, "Connection: close"])
    return head.encode() + b"\r\n\r\n" + body


def _ok(data: object) -> bytes:
    body = json.dumps(data).encode()
    return _http("200 OK", body, "Content-Type: application/json", f"Content-Length: {len(body)}")


def _chunked(data: object) -> bytes:
    body = json.dumps(data).encode()
    half = len(body) // 2
    chunks = b"".join(
        f"{len(part):x}\r\n".encode() + part + b"\r\n" for part in (body[:half], body[half:])
    )
    return _http("200 OK", chunks + b"0\r\n\r\n", "Transfer-Encoding: chunked")


def _client_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=str(DATA / "ca.pem"))
    context.verify_flags |= ssl.VERIFY_X509_STRICT
    return context


class FakeService:
    """A lookup service that records requests and answers with canned bytes."""

    def __init__(self, response: bytes | None, *, tls: bool = False) -> None:
        self.response = response
        self.tls = tls
        self.requests: list[bytes] = []
        self.server: asyncio.Server | None = None

    async def __aenter__(self) -> "FakeService":
        context = None
        if self.tls:
            context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            context.load_cert_chain(DATA / "server.pem", DATA / "server.key")
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0, ssl=context)
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self.server is not None
        self.server.close()

    @property
    def port(self) -> int:
        assert self.server is not None
        return int(self.server.sockets[0].getsockname()[1])

    async def _handle(self, reader, writer):
        try:
            self.requests.append(await reader.readuntil(b"\r\n\r\n"))
            if self.response is None:
                await asyncio.sleep(3600)
            writer.write(self.response)
            await writer.drain()
        except (OSError, asyncio.IncompleteReadError, ssl.SSLError):
            pass
        finally:
            writer.close()


class FakeConnectProxy:
    """An HTTP CONNECT proxy routing ``host:port`` authorities to local ports."""

    def __init__(self, routes: dict[str, int]) -> None:
        self.routes = routes
        self.connects: list[str] = []
        self.server: asyncio.Server | None = None

    async def __aenter__(self) -> "FakeConnectProxy":
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self.server is not None
        self.server.close()

    @property
    def url(self) -> str:
        assert self.server is not None
        return f"http://user:secret@127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def _handle(self, reader, writer):
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode()
            authority = head.split()[1]
            self.connects.append(authority)
            if authority not in self.routes:
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                return
            target_reader, target_writer = await asyncio.open_connection(
                "127.0.0.1", self.routes[authority]
            )
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await asyncio.gather(_pipe(reader, target_writer), _pipe(target_reader, writer))
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()


async def _pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()


def _ipinfo() -> IpInfoProvider:
    return IpInfoProvider(host="ipinfo.test", context=_client_context())


async def test_ip_api_lookup_runs_through_the_proxy():
    async with FakeService(_ok(IP_API_BODY)) as service:
        async with FakeConnectProxy({"ip-api.com:80": service.port}) as proxy:
            info = await exit_info(parse_proxy(proxy.url), 5, providers=[IP_API])
    assert proxy.connects == ["ip-api.com:80"]
    request = service.requests[0].decode()
    assert request.startswith("GET /json/?fields=")
    assert "Host: ip-api.com\r\n" in request
    assert info.ip == "203.0.113.7"
    assert (info.country_code, info.city, info.timezone) == (
        "DE",
        "Frankfurt am Main",
        "Europe/Berlin",
    )
    assert info.hosting is True and info.flagged_proxy is False
    assert info.source == "ip-api"
    assert info.locale() == "de-DE"


async def test_falls_back_to_ipinfo_over_tls_when_ip_api_is_rate_limited():
    limited = _http("429 Too Many Requests", b"", "X-Rl: 0", "X-Ttl: 30")
    async with FakeService(limited) as ip_api, FakeService(_ok(IPINFO_BODY), tls=True) as ipinfo:
        routes = {"ip-api.com:80": ip_api.port, "ipinfo.test:443": ipinfo.port}
        async with FakeConnectProxy(routes) as proxy:
            info = await exit_info(parse_proxy(proxy.url), 5, providers=[IP_API, _ipinfo()])
    assert proxy.connects == ["ip-api.com:80", "ipinfo.test:443"]
    assert ipinfo.requests[0].startswith(b"GET /json HTTP/1.1\r\nHost: ipinfo.test\r\n")
    assert info == ExitInfo(
        ip="198.51.100.9",
        country_code="PT",
        region="Lisbon",
        city="Lisbon",
        timezone="Europe/Lisbon",
        isp="AS12345 Example Net",
        hosting=False,
        flagged_proxy=False,
        latency=info.latency,
        source="ipinfo",
    )
    assert info.locale() == "pt-PT"


async def test_falls_back_when_primary_times_out():
    async with FakeService(None) as ip_api, FakeService(_chunked(IPINFO_BODY), tls=True) as ipinfo:
        routes = {"ip-api.com:80": ip_api.port, "ipinfo.test:443": ipinfo.port}
        async with FakeConnectProxy(routes) as proxy:
            info = await exit_info(parse_proxy(proxy.url), 0.5, providers=[IP_API, _ipinfo()])
    assert info.source == "ipinfo"
    assert info.ip == "198.51.100.9"


async def test_tls_certificate_is_verified_against_the_provider_host():
    async with FakeService(_ok(IPINFO_BODY), tls=True) as ipinfo:
        async with FakeConnectProxy({"other.test:443": ipinfo.port}) as proxy:
            wrong_name = IpInfoProvider(host="other.test", context=_client_context())
            with pytest.raises(ExitLookupError) as caught:
                await exit_info(parse_proxy(proxy.url), 5, providers=[wrong_name])
    assert isinstance(caught.value.causes[0][1], ssl.SSLCertVerificationError)


async def test_all_providers_failing_reports_every_cause():
    async with FakeService(_ok({"status": "fail", "message": "private range"})) as ip_api:
        async with FakeConnectProxy({"ip-api.com:80": ip_api.port}) as proxy:
            with pytest.raises(ExitLookupError) as caught:
                await exit_info(parse_proxy(proxy.url), 5, providers=[IP_API, _ipinfo()])
    error = caught.value
    assert isinstance(error, UpstreamError)
    assert [name for name, _ in error.causes] == ["ip-api", "ipinfo"]
    assert "private range" in str(error)
    assert "ipinfo: UpstreamError" in str(error)
    assert "secret" not in str(error)
    assert error.__cause__ is error.causes[-1][1]


async def test_unreachable_proxy_is_a_lookup_error():
    with pytest.raises(ExitLookupError) as caught:
        await exit_info(parse_proxy("http://u:p@127.0.0.1:1"), 5, providers=[IP_API])
    assert isinstance(caught.value.causes[0][1], OSError)


def test_default_providers_are_ip_api_then_ipinfo():
    assert [p.name for p in DEFAULT_PROVIDERS] == ["ip-api", "ipinfo"]
    assert DEFAULT_PROVIDERS[0].tls_context() is None
    assert DEFAULT_PROVIDERS[1].port == 443
    assert DEFAULT_PROVIDERS[1].tls_context() is not None


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_http("500 Internal Server Error", b""), "HTTP 500"),
        (_http("429 Too Many Requests", b""), "rate limit"),
        (_http("200 OK", b"not json"), "malformed"),
        (_http("200 OK", b"[1, 2]"), "malformed"),
        (b"garbage", "malformed"),
        (_ok({"status": "fail", "message": "invalid query"}), "invalid query"),
        (_ok({"status": "success"}), "no address"),
    ],
)
def test_ip_api_parser_rejects_bad_responses(response, message):
    with pytest.raises(UpstreamError, match=message):
        IP_API.parse(response, latency=0.1)


@pytest.mark.parametrize(
    "response",
    [
        _ok({"error": {"title": "Wrong ip"}}),
        _ok({"city": "Nowhere"}),
        _http("200 OK", b'5\r\n{"ip"', "Transfer-Encoding: chunked"),
    ],
)
def test_ipinfo_parser_rejects_bad_responses(response):
    with pytest.raises(UpstreamError):
        IpInfoProvider().parse(response, latency=0.1)


def test_ipinfo_parser_accepts_partial_data():
    info = IpInfoProvider().parse(_ok({"ip": "192.0.2.1"}), latency=0.2)
    assert info.ip == "192.0.2.1"
    assert (info.country_code, info.timezone, info.isp) == ("", "", "")
    assert info.locale() == "en-US"


async def test_oversized_response_is_rejected():
    huge = _http("200 OK", b" " * (MAX_RESPONSE + 1))
    async with FakeService(huge) as service:
        async with FakeConnectProxy({"ip-api.com:80": service.port}) as proxy:
            with pytest.raises(ExitLookupError, match="too large"):
                await exit_info(parse_proxy(proxy.url), 5, providers=[IP_API])


def _info(ip: str) -> ExitInfo:
    return ExitInfo(ip, "US", "", "", "America/New_York", "", False, False, 0.1, "test")


class CountingLookup:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.gate = asyncio.Event()
        self.fail = False

    async def __call__(self, spec):
        self.calls.append(spec.address)
        await self.gate.wait()
        if self.fail:
            raise UpstreamError("lookup failed")
        return _info(f"ip-{len(self.calls)}")


async def test_cache_dedupes_concurrent_lookups_for_one_proxy():
    lookup = CountingLookup()
    cache = ExitCache(lookup=lookup)
    spec = parse_proxy("http://u:p@10.0.0.1:8000")
    waiters = [asyncio.create_task(cache.get(spec)) for _ in range(5)]
    await asyncio.sleep(0)
    lookup.gate.set()
    results = await asyncio.gather(*waiters)
    assert lookup.calls == ["10.0.0.1:8000"]
    assert {r.ip for r in results} == {"ip-1"}
    assert (await cache.get(spec)).ip == "ip-1"
    assert len(lookup.calls) == 1


async def test_cache_keys_by_proxy_including_credentials():
    lookup = CountingLookup()
    lookup.gate.set()
    cache = ExitCache(lookup=lookup)
    first = await cache.get(parse_proxy("http://a:p@10.0.0.1:8000"))
    second = await cache.get(parse_proxy("http://b:p@10.0.0.1:8000"))
    assert first.ip != second.ip
    assert len(lookup.calls) == 2


async def test_cache_expires_after_ttl_and_can_be_invalidated():
    now = [100.0]
    lookup = CountingLookup()
    lookup.gate.set()
    cache = ExitCache(ttl=60, lookup=lookup, clock=lambda: now[0])
    spec = parse_proxy("socks5://10.0.0.2:1080")
    assert (await cache.get(spec)).ip == "ip-1"
    now[0] += 59
    assert (await cache.get(spec)).ip == "ip-1"
    now[0] += 2
    assert (await cache.get(spec)).ip == "ip-2"
    cache.invalidate(spec)
    assert (await cache.get(spec)).ip == "ip-3"
    cache.invalidate()
    assert (await cache.get(spec)).ip == "ip-4"


async def test_cache_does_not_keep_failures():
    lookup = CountingLookup()
    lookup.fail = True
    lookup.gate.set()
    cache = ExitCache(lookup=lookup)
    spec = parse_proxy("http://10.0.0.3:3128")
    results = await asyncio.gather(cache.get(spec), cache.get(spec), return_exceptions=True)
    assert all(isinstance(r, UpstreamError) for r in results)
    assert len(lookup.calls) == 1
    lookup.fail = False
    assert (await cache.get(spec)).ip == "ip-2"


async def test_cancelling_one_waiter_keeps_the_shared_lookup():
    lookup = CountingLookup()
    cache = ExitCache(lookup=lookup)
    spec = parse_proxy("http://10.0.0.4:3128")
    first = asyncio.create_task(cache.get(spec))
    second = asyncio.create_task(cache.get(spec))
    await asyncio.sleep(0)
    first.cancel()
    lookup.gate.set()
    assert (await second).ip == "ip-1"
    assert first.cancelled()
    assert len(lookup.calls) == 1
