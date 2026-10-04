"""Upstream proxy specification and parsing.

Proxy URLs carry credentials, so every type here keeps them out of ``repr`` and
error messages. Chrome itself only ever sees the credential-free ``server`` form.
"""

from dataclasses import dataclass, field
from typing import Final
from urllib.parse import unquote, urlsplit

from ..errors import ConfigurationError

PROXY_SCHEMES: Final = frozenset({"http", "https", "socks5"})
_FORMAT_HINT: Final = "Proxy must look like scheme://[user:pass@]host:port"


@dataclass(frozen=True, slots=True)
class ProxySpec:
    """A parsed upstream proxy.

    Attributes:
        scheme: ``http``, ``https`` (TLS to the proxy) or ``socks5``.
        host: Proxy hostname or IP literal, without brackets.
        port: Proxy TCP port.
        username: Optional username; excluded from ``repr``.
        password: Optional password; excluded from ``repr``.
    """

    scheme: str
    host: str
    port: int
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)

    @property
    def has_credentials(self) -> bool:
        """Whether the proxy requires authentication."""
        return self.username is not None

    @property
    def server(self) -> str:
        """The credential-free form accepted by Chrome's ``--proxy-server``."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}:{self.port}"

    @property
    def address(self) -> str:
        """``host:port`` for logs and reports; never contains credentials."""
        return f"{self.host}:{self.port}"


def parse_proxy(value: str) -> ProxySpec:
    """Parse and validate a proxy URL.

    Args:
        value: A URL such as ``http://user:pass@host:8080`` or ``socks5://host:1080``.
            Percent-encoded credentials are decoded.

    Returns:
        The parsed proxy.

    Raises:
        ConfigurationError: If the URL is malformed, uses an unsupported scheme, has a
            path, query or fragment, or has a password without a username. The message
            never echoes the input, which may contain credentials.
    """
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise ConfigurationError(_FORMAT_HINT) from exc
    if (
        parts.scheme not in PROXY_SCHEMES
        or not parts.hostname
        or port is None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ConfigurationError(_FORMAT_HINT)
    username = unquote(parts.username) if parts.username is not None else None
    password = unquote(parts.password) if parts.password is not None else None
    if username == "" or (password is not None and username is None):
        raise ConfigurationError("Proxy password requires a username")
    return ProxySpec(parts.scheme, parts.hostname, port, username, password)
