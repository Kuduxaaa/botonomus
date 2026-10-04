"""A lightweight, ``httpx``-like layer over a shared real browser.

    import botonomus

    response = await botonomus.get("https://example.com")

    async with botonomus.Client(max_tabs="auto") as client:
        response = await client.get("https://example.com")
        async with client.identity("acct-01") as me:
            await me.post("https://example.com/login", data={"user": "..."})

The module-level functions open a `Client` for one request, like ``httpx.get``; keep a
`Client` open to reuse the browser, cookies and the HTTP fast path.
"""

from typing import Any

from ..config import BrowserConfig
from .client import Client, Identity
from .response import Headers, Response


async def request(
    method: str,
    url: str,
    *,
    config: BrowserConfig | None = None,
    proxy: str | None = None,
    **kwargs: Any,
) -> Response:
    """Send one request with a temporary `Client`. See `Client.request`."""
    async with Client(config=config, proxy=proxy, mode="browser", max_tabs=1) as client:
        return await client.request(method, url, **kwargs)


async def get(url: str, **kwargs: Any) -> Response:
    """``request("GET", url, ...)`` with a temporary `Client`."""
    return await request("GET", url, **kwargs)


async def post(url: str, **kwargs: Any) -> Response:
    """``request("POST", url, ...)`` with a temporary `Client`."""
    return await request("POST", url, **kwargs)


async def put(url: str, **kwargs: Any) -> Response:
    """``request("PUT", url, ...)`` with a temporary `Client`."""
    return await request("PUT", url, **kwargs)


async def patch(url: str, **kwargs: Any) -> Response:
    """``request("PATCH", url, ...)`` with a temporary `Client`."""
    return await request("PATCH", url, **kwargs)


async def delete(url: str, **kwargs: Any) -> Response:
    """``request("DELETE", url, ...)`` with a temporary `Client`."""
    return await request("DELETE", url, **kwargs)


async def head(url: str, **kwargs: Any) -> Response:
    """``request("HEAD", url, ...)`` with a temporary `Client`."""
    return await request("HEAD", url, **kwargs)


__all__ = [
    "Client",
    "Headers",
    "Identity",
    "Response",
    "delete",
    "get",
    "head",
    "patch",
    "post",
    "put",
    "request",
]
