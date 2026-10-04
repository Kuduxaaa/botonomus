import asyncio
import ssl
from dataclasses import asdict

import pytest

from botonomus.config import parse_proxy
from botonomus.errors import ConfigurationError
from botonomus.network import (
    ExitLookupError,
    IpApiProvider,
    ProxyCheck,
    UpstreamError,
    check_proxies,
    load_proxies,
)
from botonomus.network.proxies import error_category

from .test_geoip import IP_API_BODY, FakeConnectProxy, FakeService, _ok

IP_API = [IpApiProvider()]


def test_load_proxies_skips_comments_and_validates(tmp_path):
    path = tmp_path / "proxies.txt"
    path.write_text("# list\n\nhttp://u:p@h:1\n  socks5://h2:2  \n", encoding="utf-8")
    assert load_proxies(path) == ["http://u:p@h:1", "socks5://h2:2"]
    path.write_text("http://u:topsecret@h:1\nnot a proxy\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as caught:
        load_proxies(path)
    assert "topsecret" not in str(caught.value)


async def test_check_proxies_reports_each_proxy_in_order_without_credentials():
    hang_up = await asyncio.start_server(lambda _, writer: writer.close(), "127.0.0.1", 0)
    hang_up_port = hang_up.sockets[0].getsockname()[1]
    try:
        async with FakeService(_ok(IP_API_BODY)) as service:
            async with FakeConnectProxy({"ip-api.com:80": service.port}) as good:
                urls = [
                    good.url,
                    "http://user:secret@127.0.0.1:1",
                    "garbage://user:secret@",
                    f"socks5://user:secret@127.0.0.1:{hang_up_port}",
                ]
                good_address = parse_proxy(good.url).address
                # Windows takes about 2 s to report a refused loopback connect.
                results = await check_proxies(urls, parallel=2, timeout=8, providers=IP_API)
    finally:
        hang_up.close()
    assert [r.address for r in results] == [
        good_address,
        "127.0.0.1:1",
        "entry 3",
        f"127.0.0.1:{hang_up_port}",
    ]
    assert [r.ok for r in results] == [True, False, False, False]
    assert [r.error for r in results] == [None, "unreachable", "invalid", "upstream"]
    assert results[0].exit is not None and results[0].exit.ip == "203.0.113.7"
    assert all(r.exit is None for r in results[1:])
    assert "secret" not in repr(results) and "secret" not in str([asdict(r) for r in results])


async def test_check_proxies_bounds_concurrency():
    in_flight = 0
    peak = 0

    async def handle(reader, writer):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await reader.readuntil(b"\r\n\r\n")
            await asyncio.sleep(0.05)
            writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            await writer.drain()
        finally:
            in_flight -= 1
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        results = await check_proxies([url] * 9, parallel=3, timeout=2, providers=IP_API)
    finally:
        server.close()
    assert peak == 3
    assert {r.error for r in results} == {"upstream"}


async def test_check_proxies_reports_timeouts():
    async with FakeService(None) as stalled:
        async with FakeConnectProxy({"ip-api.com:80": stalled.port}) as proxy:
            address = parse_proxy(proxy.url).address
            [result] = await check_proxies([proxy.url], timeout=0.2, providers=IP_API)
    assert result == ProxyCheck(address, False, "timeout", None)


async def test_check_proxies_rejects_zero_parallelism():
    with pytest.raises(ValueError):
        await check_proxies([], parallel=0)


@pytest.mark.parametrize(
    ("exc", "category"),
    [
        (ConfigurationError("bad"), "invalid"),
        (TimeoutError(), "timeout"),
        (ssl.SSLCertVerificationError("bad cert"), "tls"),
        (UpstreamError("refused"), "upstream"),
        (ConnectionRefusedError(), "unreachable"),
        (ExitLookupError([("a", UpstreamError("x")), ("b", TimeoutError())]), "timeout"),
    ],
)
def test_error_category(exc, category):
    assert error_category(exc) == category


@pytest.mark.parametrize(
    ("exc", "category"),
    [
        (ConnectionRefusedError(), "unreachable"),
        (OSError("no route"), "unreachable"),
        (ConnectionResetError(), "upstream"),  # connected, then the proxy hung up (macOS)
        (ConnectionAbortedError(), "upstream"),
        (BrokenPipeError(), "upstream"),
        (asyncio.IncompleteReadError(b"", 10), "upstream"),
    ],
)
def test_error_category_separates_refused_from_hung_up(exc, category):
    from botonomus.network.proxies import error_category

    assert error_category(exc) == category
