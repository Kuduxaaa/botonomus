"""Client side of upstream proxy tunnels: HTTP ``CONNECT`` and SOCKS5.

Hostnames are sent to the proxy unresolved, so DNS happens at the proxy's exit and
never leaks the local resolver. TLS between Chrome and the destination passes
through untouched, which keeps Chrome's own TLS and HTTP/2 fingerprints.
"""

import asyncio
import base64
import ipaddress
import ssl
import struct
from contextlib import suppress
from typing import Final

from ..config import ProxySpec

MAX_RESPONSE_HEADER: Final = 16384


class UpstreamError(Exception):
    """The upstream proxy refused or failed the tunnel."""


async def open_tunnel(
    spec: ProxySpec, host: str, port: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a TCP stream to ``host:port`` through the upstream proxy.

    Args:
        spec: The upstream proxy. ``https`` proxies are reached over TLS.
        host: Destination hostname or IP literal.
        port: Destination port.

    Returns:
        A connected ``(reader, writer)`` pair carrying the tunnelled stream.

    Raises:
        UpstreamError: If the proxy refuses, fails authentication or misbehaves.
        OSError: If the proxy cannot be reached.
    """
    tls = ssl.create_default_context() if spec.scheme == "https" else None
    reader, writer = await asyncio.open_connection(
        spec.host, spec.port, ssl=tls, server_hostname=spec.host if tls else None
    )
    try:
        if spec.scheme == "socks5":
            await socks5_connect(reader, writer, spec, host, port)
        else:
            await http_connect(reader, writer, spec, host, port)
    except BaseException:
        writer.close()
        raise
    return reader, writer


async def http_connect(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    spec: ProxySpec,
    host: str,
    port: int,
) -> None:
    """Perform an HTTP ``CONNECT`` handshake, with Basic auth when configured.

    Args:
        reader: Stream from the proxy.
        writer: Stream to the proxy.
        spec: The proxy, for its credentials.
        host: Destination hostname or IP literal.
        port: Destination port.

    Raises:
        UpstreamError: On a non-200 status or an oversized response header.
    """
    authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    lines = [f"CONNECT {authority} HTTP/1.1", f"Host: {authority}"]
    if spec.username is not None:
        token = base64.b64encode(f"{spec.username}:{spec.password or ''}".encode()).decode()
        lines.append(f"Proxy-Authorization: Basic {token}")
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
    await writer.drain()
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except asyncio.LimitOverrunError as exc:
        raise UpstreamError("Upstream proxy response too large") from exc
    if len(head) > MAX_RESPONSE_HEADER:
        raise UpstreamError("Upstream proxy response too large")
    status = head.split(b"\r\n", 1)[0].split()
    if len(status) < 2 or status[1] != b"200":
        raise UpstreamError(f"Upstream proxy refused tunnel ({status[1:2]!r})")


async def socks5_connect(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    spec: ProxySpec,
    host: str,
    port: int,
) -> None:
    """Perform a SOCKS5 handshake (RFC 1928) with username/password auth (RFC 1929).

    Args:
        reader: Stream from the proxy.
        writer: Stream to the proxy.
        spec: The proxy, for its credentials.
        host: Destination hostname or IP literal, sent unresolved.
        port: Destination port.

    Raises:
        UpstreamError: If no acceptable method is offered, authentication fails, the
            hostname or credentials are too long, or the connect request is refused.
    """
    writer.write(b"\x05\x02\x00\x02" if spec.username is not None else b"\x05\x01\x00")
    await writer.drain()
    version, method = await reader.readexactly(2)
    if version != 5 or method not in (0, 2) or (method == 2 and spec.username is None):
        raise UpstreamError("Upstream SOCKS5 proxy offered no usable method")
    if method == 2:
        user = (spec.username or "").encode()
        password = (spec.password or "").encode()
        if len(user) > 255 or len(password) > 255:
            raise UpstreamError("SOCKS5 credentials are too long")
        writer.write(b"\x01" + bytes([len(user)]) + user + bytes([len(password)]) + password)
        await writer.drain()
        _, status = await reader.readexactly(2)
        if status != 0:
            raise UpstreamError("Upstream SOCKS5 authentication failed")
    writer.write(b"\x05\x01\x00" + encode_address(host) + struct.pack("!H", port))
    await writer.drain()
    version, reply, _, address_type = await reader.readexactly(4)
    if version != 5 or reply != 0:
        raise UpstreamError(f"Upstream SOCKS5 connect failed (code {reply})")
    await read_address(reader, address_type)
    await reader.readexactly(2)


def encode_address(host: str) -> bytes:
    """Encode ``host`` as a SOCKS5 address (type byte followed by the address).

    Args:
        host: Hostname or IPv4 / IPv6 literal.

    Returns:
        The address type byte followed by the encoded address.

    Raises:
        UpstreamError: If a hostname exceeds 255 bytes after IDNA encoding.
    """
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        encoded = host.encode("idna")
        if len(encoded) > 255:
            raise UpstreamError("Hostname too long for SOCKS5") from None
        return b"\x03" + bytes([len(encoded)]) + encoded
    return (b"\x01" if literal.version == 4 else b"\x04") + literal.packed


async def read_address(reader: asyncio.StreamReader, address_type: int) -> str:
    """Read a SOCKS5 address of ``address_type`` (1 IPv4, 3 domain, 4 IPv6).

    Args:
        reader: Stream positioned at the address.
        address_type: The SOCKS5 ``ATYP`` byte that preceded it.

    Returns:
        The address as text.

    Raises:
        UpstreamError: For any other address type.
    """
    if address_type == 1:
        return str(ipaddress.IPv4Address(await reader.readexactly(4)))
    if address_type == 4:
        return str(ipaddress.IPv6Address(await reader.readexactly(16)))
    if address_type == 3:
        (length,) = await reader.readexactly(1)
        return (await reader.readexactly(length)).decode("idna")
    raise UpstreamError("Unsupported SOCKS address type")


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy bytes from ``reader`` to ``writer`` until EOF, then half-close ``writer``.

    Args:
        reader: Source stream.
        writer: Destination stream; its write side is shut down at the end.
    """
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    finally:
        if writer.can_write_eof():
            with suppress(OSError):
                writer.write_eof()
