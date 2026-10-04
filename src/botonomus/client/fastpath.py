"""The HTTP fast path: requests that look like the browser's own, without a tab.

After a real tab has loaded a host (and passed any check), later requests to that host
can go through ``curl_cffi``, which reproduces Chrome's TLS and HTTP/2 fingerprints, with
the browser's cookies, User-Agent and client hints. Requires ``botonomus[http]``.
"""

import asyncio
import http.cookiejar
import re
import time
import warnings
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..errors import BotonomusError
from .response import Headers, Response

_TARGET = re.compile(r"chrome(\d+)")
# Chrome's spelling of the replayed headers on HTTP/1.1 (HTTP/2 lowercases every name).
_CASING = {"user-agent": "User-Agent", "accept-language": "Accept-Language"}
# Chrome's TLS ClientHello and HTTP/2 settings have been stable across recent majors;
# impersonating a release more than this many majors older is refused.
MAX_MAJOR_GAP = 12


_COOKIE_PARAMS = ("name", "value", "domain", "path", "secure", "httpOnly", "sameSite",
                  "expires", "priority", "sourceScheme", "partitionKey")  # fmt: skip


class FastPathError(BotonomusError):
    """A fast-path request failed before a response arrived; the client retries in a tab."""


def cookie_param(cookie: Mapping[str, Any]) -> dict[str, Any]:
    """A ``Network.Cookie`` reduced to what ``Storage.setCookies`` accepts."""
    param = {key: cookie[key] for key in _COOKIE_PARAMS if key in cookie}
    expires = param.get("expires")
    if cookie.get("session") or not isinstance(expires, (int, float)) or expires <= 0:
        param.pop("expires", None)
    return param


def _expiry(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and value > 0 else None


def cookie_changes(
    sent: Sequence[Mapping[str, Any]], after: Iterable[http.cookiejar.Cookie]
) -> list[dict[str, Any]]:
    """``Storage.setCookies`` params that bring the browser in line with the jar.

    Only cookies the server added, changed or removed are returned. A changed cookie keeps
    the browser's attributes the jar cannot carry (``sameSite``, ``priority``...); a
    removed one is returned expired, which deletes it.
    """
    before = {(c["name"], c["domain"], c.get("path") or "/"): c for c in sent}
    changes = []
    seen = set()
    for cookie in after:
        key = (cookie.name, cookie.domain, cookie.path)
        seen.add(key)
        current = cookie_from_jar(cookie)
        original = before.get(key)
        if original is None:
            changes.append(current)
            continue
        if current["value"] == original.get("value") and cookie.expires == _expiry(
            original.get("expires")
        ):
            continue
        merged = cookie_param(original)
        merged.pop("expires", None)
        merged.update(
            value=current["value"], secure=current["secure"], httpOnly=current["httpOnly"]
        )
        if cookie.expires is not None:
            merged["expires"] = cookie.expires
        changes.append(merged)
    for key, original in before.items():
        if key not in seen:
            deleted = cookie_param(original)
            deleted["expires"] = 1
            changes.append(deleted)
    return changes


def available_targets() -> list[str] | None:
    """``curl_cffi`` impersonation targets, or ``None`` if it is not installed."""
    try:
        from curl_cffi.requests import BrowserType
    except ImportError:
        return None
    return [item.value for item in BrowserType]


def impersonation_target(major: int, targets: Iterable[str]) -> str | None:
    """The newest desktop Chrome target not newer than ``major``, within `MAX_MAJOR_GAP`."""
    best: tuple[int, str] | None = None
    for target in targets:
        match = _TARGET.fullmatch(target)
        if match is None:
            continue
        version = int(match.group(1))
        if version <= major and major - version <= MAX_MAJOR_GAP:
            if best is None or version > best[0]:
                best = (version, target)
    return best[1] if best else None


def jar_cookie(cookie: Mapping[str, Any]) -> http.cookiejar.Cookie:
    """A CDP ``Network.Cookie`` as a cookie-jar entry."""
    domain = str(cookie["domain"])
    expires = cookie.get("expires")
    rest = {"HttpOnly": ""} if cookie.get("httpOnly") else {}
    return http.cookiejar.Cookie(
        version=0,
        name=str(cookie["name"]),
        value=str(cookie["value"]),
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path=str(cookie.get("path") or "/"),
        path_specified=True,
        secure=bool(cookie.get("secure")),
        expires=int(expires) if isinstance(expires, (int, float)) and expires > 0 else None,
        discard=False,
        comment=None,
        comment_url=None,
        rest=rest,
    )


def cookie_from_jar(cookie: http.cookiejar.Cookie) -> dict[str, Any]:
    """A cookie-jar entry as a CDP cookie for ``Storage.setCookies``."""
    result: dict[str, Any] = {
        "name": cookie.name,
        "value": cookie.value or "",
        "domain": cookie.domain,
        "path": cookie.path,
        "secure": cookie.secure,
        "httpOnly": cookie.has_nonstandard_attr("HttpOnly")
        or cookie.has_nonstandard_attr("httponly"),
    }
    if cookie.expires is not None:
        result["expires"] = cookie.expires
    return result


class FastPath:
    """One ``curl_cffi`` session mirroring one browser context.

    Args:
        target: The ``curl_cffi`` impersonation target.
        headers: Navigation headers learned from the browser (User-Agent, client hints,
            Accept-Language); they override the target's defaults.
        proxy: Proxy URL (credentials allowed), or ``None``.
    """

    def __init__(self, target: str, headers: Mapping[str, str], proxy: str | None) -> None:
        from curl_cffi.requests import AsyncSession

        # On Windows' Proactor loop curl_cffi starts a selector thread itself and warns
        # about it; that is expected here, not something the user can act on.
        warnings.filterwarnings(
            "ignore", message=r"\s*Proactor event loop does not implement", module=r"curl_cffi\."
        )
        self.target = target
        self._headers = {_CASING.get(name.lower(), name): value for name, value in headers.items()}
        self._session: Any = AsyncSession(impersonate=target, proxy=proxy)
        self._lock = asyncio.Lock()  # one jar per session: requests take turns with it

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        cookies: list[dict[str, Any]],
        timeout: float,
    ) -> tuple[Response, list[dict[str, Any]]]:
        """Send one request with the browser's ``cookies``.

        Partitioned (CHIPS) cookies are left out: they belong to embedded contexts.

        Returns:
            The response and the cookie updates for the browser (see `cookie_changes`).

        Raises:
            FastPathError: If no response arrived (connection, proxy, TLS or timeout).
        """
        from curl_cffi import CurlHttpVersion

        sent = [cookie for cookie in cookies if not cookie.get("partitionKey")]
        async with self._lock:
            jar = self._session.cookies.jar
            jar.clear()
            for cookie in sent:
                jar.set_cookie(jar_cookie(cookie))
            started = time.monotonic()
            try:
                raw = await self._session.request(
                    method,
                    url,
                    headers={**self._headers, **headers},
                    data=body,
                    timeout=timeout,
                    allow_redirects=True,
                    # Chrome never upgrades cleartext HTTP to h2c; TLS uses ALPN for HTTP/2.
                    http_version=CurlHttpVersion.V1_1 if url.startswith("http:") else None,
                )
            except Exception as exc:
                # curl's messages name hosts and proxies; keep them out of ours.
                raise FastPathError(f"Fast path request failed ({type(exc).__name__})") from None
            changes = cookie_changes(sent, list(jar))
        response = Response(
            url=str(raw.url),
            status=int(raw.status_code),
            headers=Headers({key: value for key, value in raw.headers.items() if value}),
            content=bytes(raw.content),
            via="http",
            elapsed=time.monotonic() - started,
            method=method,
        )
        return response, changes

    async def close(self) -> None:
        """Close the session's connections."""
        await self._session.close()
