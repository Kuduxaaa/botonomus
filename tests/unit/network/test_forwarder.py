import asyncio
import base64
import contextlib
import logging
import random
import socket
import struct
import sys

import pytest

from botonomus.config import parse_proxy
from botonomus.network import ProxyForwarder
from botonomus.network.forwarder import _REPLY_OK

# SO_LINGER {on, 0s}: close() sends RST. struct linger is two u_short on Windows, two int elsewhere.
LINGER_RESET = struct.pack("hh" if sys.platform == "win32" else "ii", 1, 0)


async def _relay(reader, writer, target_port):
    target_reader, target_writer = await asyncio.open_connection("127.0.0.1", target_port)

    async def pipe(src, dst):
        while data := await src.read(4096):
            dst.write(data)
            await dst.drain()
        dst.close()

    await asyncio.gather(pipe(reader, target_writer), pipe(target_reader, writer))


@pytest.fixture
async def echo_port():
    async def echo(reader, writer):
        while data := await reader.read(4096):
            writer.write(data.upper())
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1]
    server.close()


async def _http_proxy(requests, echo_port):
    async def handle(reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        requests.append(head)
        expected = base64.b64encode(b"user:secret").decode()
        if f"Proxy-Authorization: Basic {expected}" not in head:
            writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
            writer.close()
            return
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await _relay(reader, writer, echo_port)

    return await asyncio.start_server(handle, "127.0.0.1", 0)


async def _socks_proxy(seen, echo_port):
    async def handle(reader, writer):
        _, count = await reader.readexactly(2)
        assert 2 in await reader.readexactly(count)
        writer.write(b"\x05\x02")
        _, ulen = await reader.readexactly(2)
        user = await reader.readexactly(ulen)
        (plen,) = await reader.readexactly(1)
        password = await reader.readexactly(plen)
        ok = (user, password) == (b"user", b"secret")
        writer.write(b"\x01\x00" if ok else b"\x01\x01")
        if not ok:
            writer.close()
            return
        _, _, _, atyp = await reader.readexactly(4)
        (length,) = await reader.readexactly(1)
        seen.append((atyp, (await reader.readexactly(length)).decode()))
        await reader.readexactly(2)
        writer.write(b"\x05\x00\x00\x01" + bytes(6))
        await _relay(reader, writer, echo_port)

    return await asyncio.start_server(handle, "127.0.0.1", 0)


async def _socks_client(port, host, target_port):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"\x05\x01\x00")
    assert await reader.readexactly(2) == b"\x05\x00"
    name = host.encode()
    writer.write(b"\x05\x01\x00\x03" + bytes([len(name)]) + name + struct.pack("!H", target_port))
    reply = await reader.readexactly(10)
    return reader, writer, reply[1]


@pytest.mark.parametrize("kind", ["http", "socks5"])
async def test_forwarder_tunnels_with_credentials(kind, echo_port):
    seen = []
    factory = _http_proxy if kind == "http" else _socks_proxy
    upstream = await factory(seen, echo_port)
    port = upstream.sockets[0].getsockname()[1]
    forwarder = ProxyForwarder(parse_proxy(f"{kind}://user:secret@127.0.0.1:{port}"))
    await forwarder.start()
    try:
        assert forwarder.server == f"socks5://127.0.0.1:{forwarder.port}"
        reader, writer, status = await _socks_client(forwarder.port, "example.test", 443)
        assert status == 0
        writer.write(b"hello")
        assert await asyncio.wait_for(reader.readexactly(5), 2) == b"HELLO"
        writer.close()
        # Hostname reaches the upstream unresolved, so DNS happens remotely.
        assert "example.test" in str(seen)
    finally:
        await forwarder.close()
        upstream.close()


@pytest.mark.parametrize("kind", ["http", "socks5"])
async def test_forwarder_reports_upstream_auth_failure(kind, echo_port):
    factory = _http_proxy if kind == "http" else _socks_proxy
    upstream = await factory([], echo_port)
    port = upstream.sockets[0].getsockname()[1]
    forwarder = ProxyForwarder(parse_proxy(f"{kind}://user:wrong@127.0.0.1:{port}"))
    await forwarder.start()
    try:
        _, writer, status = await _socks_client(forwarder.port, "example.test", 443)
        assert status != 0
        writer.close()
    finally:
        await forwarder.close()
        upstream.close()


async def test_forwarder_rejects_clients_requiring_auth():
    forwarder = ProxyForwarder(parse_proxy("http://u:p@127.0.0.1:1"))
    await forwarder.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", forwarder.port)
        writer.write(b"\x05\x01\x02")
        assert await reader.readexactly(2) == b"\x05\xff"
        writer.close()
    finally:
        await forwarder.close()


async def test_forwarder_close_cancels_open_tunnels(echo_port):
    upstream = await _http_proxy([], echo_port)
    port = upstream.sockets[0].getsockname()[1]
    forwarder = ProxyForwarder(parse_proxy(f"http://user:secret@127.0.0.1:{port}"))
    await forwarder.start()
    reader, writer, status = await _socks_client(forwarder.port, "example.test", 80)
    assert status == 0
    await asyncio.wait_for(forwarder.close(), 2)
    assert await asyncio.wait_for(reader.read(), 2) == b""
    writer.close()
    upstream.close()


async def _stalling_upstream(stall_ratio, seed):
    """An HTTP CONNECT upstream that stalls some tunnels and floods the others."""
    rnd = random.Random(seed)

    async def handle(reader, writer):
        with contextlib.suppress(Exception):
            await reader.readuntil(b"\r\n\r\n")
            if rnd.random() >= stall_ratio:
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                # More than the socket buffers hold: the forwarder's writes to a
                # client that never reads stay pending.
                writer.write(b"x" * 1_000_000)
                await writer.drain()
            await asyncio.sleep(3600)

    return await asyncio.start_server(handle, "127.0.0.1", 0)


async def _abandoning_client(port, rnd, keep):
    loop = asyncio.get_running_loop()
    sock = socket.socket()
    sock.setblocking(False)
    try:
        await loop.sock_connect(sock, ("127.0.0.1", port))
        if rnd.random() < 0.8:
            await loop.sock_sendall(sock, b"\x05\x01\x00\x05\x01\x00\x03\x04abcd\x00\x50")
        await asyncio.sleep(rnd.random() * 0.03)
        if rnd.random() < 0.5:
            # Reset rather than FIN, like a killed browser.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, LINGER_RESET)
            sock.close()
        else:
            keep.append(sock)
    except OSError:
        sock.close()


@pytest.mark.parametrize("seed", range(24))
async def test_forwarder_close_never_hangs_with_racing_connections(seed):
    rnd = random.Random(seed)
    upstream = await _stalling_upstream(0.4, seed)
    port = upstream.sockets[0].getsockname()[1]
    forwarder = ProxyForwarder(parse_proxy(f"http://user:secret@127.0.0.1:{port}"))
    await forwarder.start()
    keep: list[socket.socket] = []
    clients = [
        asyncio.create_task(_abandoning_client(forwarder.port, rnd, keep)) for _ in range(150)
    ]
    try:
        # Close while connections are still being accepted, handshaking and tunnelling.
        await asyncio.sleep(rnd.random() * 0.03)
        await asyncio.wait_for(forwarder.close(), 3)
        assert forwarder.active_connections == 0
    finally:
        for task in clients:
            task.cancel()
        await asyncio.gather(*clients, return_exceptions=True)
        for sock in keep:
            sock.close()
        upstream.close()


async def test_forwarder_close_aborts_client_that_stopped_reading(echo_port):
    upstream = await _stalling_upstream(0.0, 0)
    port = upstream.sockets[0].getsockname()[1]
    forwarder = ProxyForwarder(parse_proxy(f"http://user:secret@127.0.0.1:{port}"))
    await forwarder.start()
    reader, writer, status = await _socks_client(forwarder.port, "example.test", 80)
    assert status == 0
    await asyncio.sleep(0.2)  # let the flood fill both socket buffers
    try:
        await asyncio.wait_for(forwarder.close(), 2)
        assert forwarder.active_connections == 0
    finally:
        writer.close()
        upstream.close()


async def test_forwarder_close_is_idempotent_and_refuses_new_clients():
    forwarder = ProxyForwarder(parse_proxy("http://u:p@127.0.0.1:1"))
    await forwarder.start()
    port = forwarder.port
    await forwarder.close()
    await forwarder.close()
    with pytest.raises(OSError):
        await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 5)


async def test_forwarder_close_cancels_handshake_of_half_closed_reset_client(caplog):
    # The sequence seen when a browser is killed mid-request: the client sends its
    # SOCKS request and half-closes (FIN), the forwarder answers data the client
    # never reads, the client's socket then dies with RST, and the forwarder only
    # closes its side when close() cancels the pending upstream connect. On the
    # Windows proactor loop the stdlib's shutdown() of that socket then raises
    # ConnectionResetError before Server._detach(), which used to leave
    # asyncio.Server.wait_closed() (and so close()) waiting forever.
    connecting = asyncio.Event()

    async def stalled_upstream(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        connecting.set()
        await asyncio.sleep(3600)

    upstream = await asyncio.start_server(stalled_upstream, "127.0.0.1", 0)
    port = upstream.sockets[0].getsockname()[1]
    forwarder = ProxyForwarder(parse_proxy(f"http://user:secret@127.0.0.1:{port}"))
    await forwarder.start()
    loop = asyncio.get_running_loop()
    client = socket.socket()
    client.setblocking(False)
    try:
        await loop.sock_connect(client, ("127.0.0.1", forwarder.port))
        await loop.sock_sendall(client, b"\x05\x01\x00\x05\x01\x00\x03\x04host\x00\x50")
        client.shutdown(socket.SHUT_WR)
        await asyncio.wait_for(connecting.wait(), 2)
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, LINGER_RESET)
        client.close()
        await asyncio.sleep(0.1)
        await asyncio.wait_for(forwarder.close(), 2)
        assert forwarder.active_connections == 0
    finally:
        client.close()
        upstream.close()
    assert not [r for r in caplog.records if r.name == "asyncio" and r.levelno >= logging.ERROR]


async def test_forwarder_close_after_half_closed_client_resets(caplog):
    # As above, but the tunnel was established and ends on its own before close().
    sent, release = asyncio.Event(), asyncio.Event()

    async def upstream_handler(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await reader.read()
        writer.write(b"x" * 1000)
        await writer.drain()
        sent.set()
        await release.wait()
        writer.close()

    upstream = await asyncio.start_server(upstream_handler, "127.0.0.1", 0)
    port = upstream.sockets[0].getsockname()[1]
    forwarder = ProxyForwarder(parse_proxy(f"http://user:secret@127.0.0.1:{port}"))
    await forwarder.start()
    loop = asyncio.get_running_loop()
    client = socket.socket()
    client.setblocking(False)
    try:
        await loop.sock_connect(client, ("127.0.0.1", forwarder.port))
        await loop.sock_sendall(client, b"\x05\x01\x00\x05\x01\x00\x03\x04host\x00\x50")
        reply = b""
        while len(reply) < 12:
            reply += await loop.sock_recv(client, 12 - len(reply))
        assert reply == b"\x05\x00" + _REPLY_OK
        client.shutdown(socket.SHUT_WR)
        await asyncio.wait_for(sent.wait(), 2)
        await asyncio.sleep(0.1)
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, LINGER_RESET)
        client.close()
        await asyncio.sleep(0.1)
        release.set()
        # The tunnel must end by itself, so the forwarder closes the socket
        # gracefully before close() is ever called.
        handlers = tuple(forwarder._connections.values())
        await asyncio.wait_for(asyncio.gather(*handlers), 2)
        assert forwarder.active_connections == 0
        await asyncio.wait_for(forwarder.close(), 2)
    finally:
        client.close()
        release.set()
        upstream.close()
    assert not [r for r in caplog.records if r.name == "asyncio" and r.levelno >= logging.ERROR]


async def test_forwarder_keeps_accepting_after_client_resets(echo_port):
    upstream = await _http_proxy([], echo_port)
    port = upstream.sockets[0].getsockname()[1]
    forwarder = ProxyForwarder(parse_proxy(f"http://user:secret@127.0.0.1:{port}"))
    await forwarder.start()
    try:
        for _ in range(50):
            sock = socket.create_connection(("127.0.0.1", forwarder.port))
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, LINGER_RESET)
            sock.close()
        reader, writer, status = await _socks_client(forwarder.port, "example.test", 80)
        assert status == 0
        writer.write(b"still up")
        assert await asyncio.wait_for(reader.readexactly(8), 2) == b"STILL UP"
        writer.close()
    finally:
        await forwarder.close()
        upstream.close()
