"""`Client`: call a real browser like ``httpx``, on one shared Chrome.

Every request loads in a tab of an in-memory browser context. Contexts share one
browser process, so memory per concurrent request is a renderer, not a browser. Once a
tab has loaded a host without a challenge, later requests to that host may take the
HTTP fast path, which replays the browser's cookies and headers with Chrome's TLS and
HTTP/2 fingerprints; a challenge sends the request back to a tab.
"""

import asyncio
import base64
import json as jsonlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Self
from urllib.parse import urlencode, urlsplit

from ..cdp import IsolatedContext, Page, ProtocolError, TargetClosedError, TimeoutError_
from ..config import BrowserConfig, parse_proxy
from ..core import Botonomus
from ..errors import ConfigurationError, ManagerClosedError
from ..human import HumanPage
from ..network import ProxyForwarder
from .browser import SharedBrowser
from .challenge import is_challenge
from .fastpath import (
    FastPath,
    FastPathError,
    available_targets,
    cookie_param,
    impersonation_target,
)
from .fetch import (
    RESOURCE_TYPES,
    Fetched,
    Request,
    Wait,
    cookies_for,
    fetch_in_page,
    read_local_storage,
)
from .identities import IdentityStore, StorageState
from .pool import default_tabs
from .response import Response

_log = logging.getLogger("botonomus")
Mode = Literal["auto", "browser"]
Backend = Literal["context", "profile"]
# A host whose fast-path requests were challenged this many times stays on the browser.
FALLBACKS_BEFORE_PIN = 2


def build_request(
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    data: Mapping[str, Any] | str | bytes | None = None,
    json: Any = None,
) -> Request:
    """A `Request` from ``httpx``-style arguments.

    Raises:
        ConfigurationError: For a non-HTTP(S) URL or both ``data`` and ``json``.
    """
    if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https"):
        raise ConfigurationError("url must be an http:// or https:// URL")
    if data is not None and json is not None:
        raise ConfigurationError("Pass data or json, not both")
    if params:
        url += ("&" if urlsplit(url).query else "?") + urlencode(params, doseq=True)
    merged = dict(headers or {})
    lower = {key.lower() for key in merged}
    body: bytes | None = None
    if json is not None:
        body = jsonlib.dumps(json).encode()
        if "content-type" not in lower:
            merged["Content-Type"] = "application/json"
    elif isinstance(data, Mapping):
        body = urlencode(data, doseq=True).encode()
        if "content-type" not in lower:
            merged["Content-Type"] = "application/x-www-form-urlencoded"
    elif isinstance(data, str):
        body = data.encode()
    elif data is not None:
        body = bytes(data)
    return Request(method.upper(), url, merged, body)


@dataclass(eq=False)
class _Holder:
    """One logical context; recreated (and its state restored) after a browser relaunch."""

    proxy: str | None
    state: StorageState = field(default_factory=StorageState)
    context: Any = None
    external: bool = False
    generation: int = -1
    forwarder: ProxyForwarder | None = None
    fast: FastPath | None = None
    fast_allowed: bool = True
    hosts: dict[str, int] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # localStorage still to write into the current context; done inside a tab slot.
    pending_storage: bool = False


class _Verbs:
    """``get``/``post``/... shortcuts over ``request``."""

    request: Callable[..., Awaitable[Response]]

    async def get(self, url: str, **kwargs: Any) -> Response:
        """``request("GET", url, ...)``."""
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> Response:
        """``request("POST", url, ...)``."""
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> Response:
        """``request("PUT", url, ...)``."""
        return await self.request("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> Response:
        """``request("PATCH", url, ...)``."""
        return await self.request("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> Response:
        """``request("DELETE", url, ...)``."""
        return await self.request("DELETE", url, **kwargs)

    async def head(self, url: str, **kwargs: Any) -> Response:
        """``request("HEAD", url, ...)``."""
        return await self.request("HEAD", url, **kwargs)


class Client(_Verbs):
    """A lightweight, ``httpx``-like client backed by one shared, real Chrome.

    An additional layer: `Botonomus` and its profile-backed sessions are unchanged.
    The browser starts on the first request and closes with the client.

    Args:
        config: Browser settings (executable, persona, proxy, headless, humanize...).
            ``driver`` must be ``native``. Defaults to ``BrowserConfig()``.
        proxy: Proxy for this client's contexts (credentials allowed); ``None`` uses
            ``config.proxy``.
        mode: ``"auto"`` (a tab first, then the HTTP fast path per host when
            ``botonomus[http]`` is installed) or ``"browser"`` (always a tab).
        max_tabs: Concurrent tabs, or ``"auto"`` to size from free memory and CPUs.
        block: Resource types to skip loading: ``image``, ``font``, ``media``,
            ``stylesheet``. Saves bandwidth and CPU; some sites notice.
        fresh_context: A new, empty context for every request instead of one shared
            context (cookies persist across requests by default).
        timeout: Default seconds per request.
        wait: Default page state to wait for: ``load`` or ``domcontentloaded``.
        identity_root: Where `identity` keeps state files. Defaults to an
            ``identities`` directory next to ``config.profile_root``.

    Raises:
        ConfigurationError: For an invalid option.

    Example:
        >>> async with Client() as client:  # doctest: +SKIP
        ...     response = await client.get("https://example.com")
        ...     print(response.status, response.via)
    """

    def __init__(
        self,
        *,
        config: BrowserConfig | None = None,
        proxy: str | None = None,
        mode: Mode = "auto",
        max_tabs: int | Literal["auto"] = "auto",
        block: Sequence[str] = (),
        fresh_context: bool = False,
        timeout: float = 30.0,
        wait: Wait = "load",
        identity_root: str | Path | None = None,
    ) -> None:
        config = config or BrowserConfig()
        if mode not in ("auto", "browser"):
            raise ConfigurationError("mode must be 'auto' or 'browser'")
        if max_tabs != "auto" and (type(max_tabs) is not int or max_tabs < 1):
            raise ConfigurationError("max_tabs must be a positive integer or 'auto'")
        unknown = [name for name in block if name not in RESOURCE_TYPES]
        if isinstance(block, str) or unknown:
            raise ConfigurationError(f"block accepts {', '.join(sorted(RESOURCE_TYPES))}")
        if wait not in ("load", "domcontentloaded"):
            raise ConfigurationError("wait must be 'load' or 'domcontentloaded'")
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ConfigurationError("timeout must be a positive number of seconds")
        if proxy is not None:
            parse_proxy(proxy)
        self._config = config
        self._browser = SharedBrowser(config)
        self._proxy = proxy
        self._mode: Mode = mode
        self._tabs = default_tabs() if max_tabs == "auto" else max_tabs
        self._slots = asyncio.Semaphore(self._tabs)
        self._block = tuple(block)
        self._fresh = fresh_context
        self._timeout = float(timeout)
        self._wait: Wait = wait
        self._identity_root = (
            Path(identity_root) if identity_root is not None
            else config.profile_root.parent / "identities"
        )  # fmt: skip
        self._default = _Holder(proxy)
        self._holders: set[_Holder] = {self._default}
        self._navigator: dict[str, str] = {}
        self._fast_note = False
        self._closed = False

    @property
    def max_tabs(self) -> int:
        """Concurrent tabs this client allows."""
        return self._tabs

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Close every context and the browser. Idempotent."""
        if self._closed:
            return
        self._closed = True
        try:
            for holder in tuple(self._holders):
                await self._dispose(holder)
        finally:
            await self._browser.close()

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        data: Mapping[str, Any] | str | bytes | None = None,
        json: Any = None,
        timeout: float | None = None,
        wait: Wait | None = None,
        settle: float = 0.0,
    ) -> Response:
        """Send a request and return its `Response`.

        Args:
            method: HTTP method.
            url: ``http://`` or ``https://`` URL.
            params: Query parameters appended to ``url``.
            headers: Extra request headers.
            data: Form fields (mapping), or a raw ``str``/``bytes`` body.
            json: A JSON body (sets ``Content-Type: application/json``).
            timeout: Seconds; defaults to the client's.
            wait: Page state to wait for in a tab; defaults to the client's.
            settle: Extra seconds to let scripts run in a tab before reading it.

        Raises:
            ConfigurationError: For invalid arguments.
            ManagerClosedError: If the client is closed.
            NavigationError: If the browser cannot load the URL.
            TimeoutError_: If the page does not reach ``wait`` in time.
        """
        return await self._request(
            self._default,
            build_request(method, url, params=params, headers=headers, data=data, json=json),
            timeout=timeout,
            wait=wait,
            settle=settle,
        )

    @asynccontextmanager
    async def page(
        self, url: str | None = None, *, wait: Wait | None = None, timeout: float | None = None
    ) -> AsyncIterator[Any]:
        """A tab in the client's context for full interaction (click, type, scroll).

        Yields a native `Page`, or a `HumanPage` when ``config.humanize`` is on. The
        tab closes on exit; cookies and ``localStorage`` stay in the context.
        """
        async with self._page(self._default, url, wait=wait, timeout=timeout) as page:
            yield page

    @asynccontextmanager
    async def identity(
        self, name: str, *, proxy: str | None = None, backend: Backend = "context"
    ) -> AsyncIterator["Identity"]:
        """A persistent identity: cookies, ``localStorage`` and proxy kept between uses.

        Args:
            name: Identity name (profile-name rules). One live use at a time, across
                processes.
            proxy: Proxy for this identity, saved with it; ``None`` reuses the saved
                proxy, else the client's.
            backend: ``"context"`` keeps state in a small file and restores it into an
                in-memory context. ``"profile"`` opens the full Chrome profile
                ``name`` under ``config.profile_root`` (the one `Botonomus.open`
                uses) in its own browser, for sites that keep login state in
                IndexedDB, Cache Storage or service workers.

        Raises:
            ConfigurationError: For an invalid name, proxy or backend.
            ProfileInUseError: If the identity is in use elsewhere.
        """
        self._check_open()
        if proxy is not None:
            parse_proxy(proxy)
        if backend == "context":
            with IdentityStore(self._identity_root, name) as store:
                state = store.load()
                if proxy is not None:
                    state.proxy = proxy
                holder = _Holder(state.proxy or self._proxy, state)
                self._holders.add(holder)
                try:
                    yield Identity(self, holder, store.name)
                finally:
                    try:
                        await self._refresh_cookies(holder)
                    finally:
                        store.save(holder.state)
                        await self._dispose(holder)
        elif backend == "profile":
            config = self._config
            if proxy or self._proxy:
                config = replace(config, proxy=proxy or self._proxy)
            async with Botonomus(1, config=config) as bot, bot.open(profile=name) as session:
                holder = _Holder(
                    None,
                    StorageState(proxy=proxy or self._proxy),
                    context=session.context,
                    external=True,
                    fast_allowed=False,
                )
                yield Identity(self, holder, session.profile)
        else:
            raise ConfigurationError("backend must be 'context' or 'profile'")

    # Internals shared with Identity.

    def _check_open(self) -> None:
        if self._closed:
            raise ManagerClosedError("Client is closed")

    async def _request(
        self,
        holder: _Holder,
        request: Request,
        *,
        timeout: float | None,
        wait: Wait | None,
        settle: float,
    ) -> Response:
        self._check_open()
        timeout = timeout or self._timeout
        wait = wait or self._wait
        try:
            if self._fresh and holder is self._default:
                holder = _Holder(self._proxy)
                self._holders.add(holder)
                try:
                    return await self._send(holder, request, timeout, wait, settle)
                finally:
                    await self._dispose(holder)
            return await self._send(holder, request, timeout, wait, settle)
        except (ProtocolError, TargetClosedError, TimeoutError_) as exc:
            if self._closed:
                raise ManagerClosedError("Client closed during the request") from exc
            raise

    async def _send(
        self, holder: _Holder, request: Request, timeout: float, wait: Wait, settle: float
    ) -> Response:
        host = (urlsplit(request.url).hostname or "").lower()
        if self._mode == "auto" and holder.hosts.get(host, FALLBACKS_BEFORE_PIN) < (
            FALLBACKS_BEFORE_PIN
        ):
            fast = await self._fast(holder)
            if fast is not None:
                try:
                    response = await self._send_fast(holder, fast, request, timeout)
                except FastPathError:
                    _log.info("fast_path_failed")  # transient: this request uses a tab
                else:
                    if not is_challenge(response.status, response.headers, response.text):
                        return response
                    holder.hosts[host] += 1
                    _log.info("fast_path_challenged", extra={"fallbacks": holder.hosts[host]})
        fetched = await self._fetch(holder, request, timeout, wait, settle)
        response = fetched.response
        if fetched.navigator_headers and not self._navigator:
            self._navigator = fetched.navigator_headers
        if (
            self._mode == "auto"
            and holder.fast_allowed
            and host not in holder.hosts
            and response.status < 400
            and not is_challenge(response.status, response.headers, response.text)
        ):
            holder.hosts[host] = 0
        return response

    async def _send_fast(
        self, holder: _Holder, fast: FastPath, request: Request, timeout: float
    ) -> Response:
        context = await self._ensure(holder)
        response, changes = await fast.request(
            request.method,
            request.url,
            headers=request.headers,
            body=request.body,
            cookies=await context.cookies(),
            timeout=timeout,
        )
        if changes:
            await context.add_cookies(changes)
        holder.state.cookies = await context.cookies()
        return replace(response, cookies=cookies_for(response.url, holder.state.cookies))

    async def _fetch(
        self, holder: _Holder, request: Request, timeout: float, wait: Wait, settle: float
    ) -> Fetched:
        async with self._slots:
            for attempt in range(2):
                context = await self._ensure(holder)
                try:
                    await self._restore_storage(holder, context)
                    fetched = await fetch_in_page(
                        context, request, wait=wait, timeout=timeout,
                        block=self._block, settle=settle,
                    )  # fmt: skip
                except TargetClosedError as exc:
                    if self._closed:
                        raise ManagerClosedError("Client closed during the request") from exc
                    if attempt or holder.external or self._browser.alive:
                        raise
                    _log.warning("client_browser_relaunch")
                    continue
                await self._remember(holder, context, fetched.local_storage)
                return fetched
        raise AssertionError("unreachable")

    @asynccontextmanager
    async def _page(
        self, holder: _Holder, url: str | None, *, wait: Wait | None, timeout: float | None
    ) -> AsyncIterator[Any]:
        self._check_open()
        if url is not None:
            build_request("GET", url)
        async with self._slots:
            context = await self._ensure(holder)
            await self._restore_storage(holder, context)
            page: Page = await context.new_page()
            try:
                if url is not None:
                    await page.goto(
                        url, wait_until=wait or self._wait, timeout=timeout or self._timeout
                    )
                human = self._config.human_config
                yield HumanPage(page, config=human) if human else page
            finally:
                storage = None
                if not page.closed:
                    storage = await read_local_storage(page)
                    await page.close()
                try:
                    await self._remember(holder, context, storage)
                except (ProtocolError, TargetClosedError):
                    pass

    async def _remember(
        self, holder: _Holder, context: Any, storage: tuple[str, dict[str, str]] | None
    ) -> None:
        holder.state.cookies = await context.cookies()
        if storage is not None:
            origin, items = storage
            holder.state.local_storage[origin] = items

    async def _refresh_cookies(self, holder: _Holder) -> None:
        context = holder.context
        if (
            context is not None
            and not getattr(context, "closed", False)
            and (holder.external or holder.generation == self._browser.generation)
            and self._browser.alive
        ):
            try:
                holder.state.cookies = await context.cookies()
            except (ProtocolError, TargetClosedError):
                pass

    async def _ensure(self, holder: _Holder) -> Any:
        """The holder's live context, creating it (and restoring state) when needed."""
        self._check_open()
        if holder.external:
            return holder.context
        async with holder.lock:
            connection = await self._browser.connection()
            if (
                holder.context is None
                or holder.context.closed
                or (holder.generation != self._browser.generation)
            ):
                server = None
                if holder.proxy is not None:
                    spec = parse_proxy(holder.proxy)
                    if spec.has_credentials and holder.forwarder is None:
                        holder.forwarder = ProxyForwarder(spec)
                        await holder.forwarder.start()
                    server = holder.forwarder.server if holder.forwarder else spec.server
                context = await IsolatedContext.create(connection, proxy_server=server)
                try:
                    await self._restore(context, holder.state)
                except BaseException:
                    # Never publish a context missing the saved state: requests through it
                    # would overwrite that state with an empty one.
                    await context.close()
                    raise
                holder.context = context
                holder.generation = self._browser.generation
                holder.pending_storage = bool(holder.state.local_storage)
            return holder.context

    async def _restore(self, context: IsolatedContext, state: StorageState) -> None:
        if state.cookies:
            await context.add_cookies([cookie_param(cookie) for cookie in state.cookies])

    async def _restore_storage(self, holder: _Holder, context: Any) -> None:
        """Write pending ``localStorage``; callers hold a tab slot (it opens a tab)."""
        if not holder.pending_storage:
            return
        for origin, items in holder.state.local_storage.items():
            if items:
                await _restore_local_storage(context, origin, items)
        holder.pending_storage = False

    async def _fast(self, holder: _Holder) -> FastPath | None:
        if holder.fast is not None or not holder.fast_allowed or not self._navigator:
            return holder.fast
        targets = available_targets()
        target = impersonation_target(self._browser.major, targets or [])
        if target is None:
            if not self._fast_note:
                self._fast_note = True
                _log.info(
                    "fast_path_unavailable",
                    extra={"reason": "not installed" if targets is None else "no target"},
                )
            holder.fast_allowed = False
            return None
        holder.fast = FastPath(target, self._navigator, holder.proxy or self._config.proxy)
        return holder.fast

    async def _dispose(self, holder: _Holder) -> None:
        self._holders.discard(holder)
        fast, holder.fast = holder.fast, None
        forwarder, holder.forwarder = holder.forwarder, None
        try:
            if fast is not None:
                await fast.close()
            if not holder.external and holder.context is not None and self._browser.alive:
                await holder.context.close()
        finally:
            if forwarder is not None:
                await forwarder.close()


class Identity(_Verbs):
    """A named, persistent identity of a `Client`. Obtained from `Client.identity`.

    Attributes:
        name: The normalized identity name.
    """

    def __init__(self, client: Client, holder: _Holder, name: str) -> None:
        self._client = client
        self._holder = holder
        self.name = name

    @property
    def proxy(self) -> str | None:
        """The identity's proxy URL, or ``None`` for the client's."""
        return self._holder.state.proxy

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        data: Mapping[str, Any] | str | bytes | None = None,
        json: Any = None,
        timeout: float | None = None,
        wait: Wait | None = None,
        settle: float = 0.0,
    ) -> Response:
        """Send a request as this identity. See `Client.request`."""
        return await self._client._request(
            self._holder,
            build_request(method, url, params=params, headers=headers, data=data, json=json),
            timeout=timeout,
            wait=wait,
            settle=settle,
        )

    @asynccontextmanager
    async def page(
        self, url: str | None = None, *, wait: Wait | None = None, timeout: float | None = None
    ) -> AsyncIterator[Any]:
        """A tab as this identity. See `Client.page`."""
        async with self._client._page(self._holder, url, wait=wait, timeout=timeout) as page:
            yield page

    async def cookies(self) -> list[dict[str, Any]]:
        """This identity's cookies."""
        context = await self._client._ensure(self._holder)
        return list(await context.cookies())


async def _restore_local_storage(
    context: IsolatedContext, origin: str, items: Mapping[str, str]
) -> None:
    """Write ``items`` into ``origin``'s ``localStorage`` without contacting the site.

    The tab navigates to the origin, but the request is answered locally with an empty
    document, so the storage belongs to the right origin and no traffic is sent.
    """
    page = await context.new_page()
    session = page.session
    tasks: set[asyncio.Task[Any]] = set()
    empty = base64.b64encode(b"<!doctype html><title></title>").decode("ascii")

    def on_paused(params: dict[str, Any]) -> None:
        task = asyncio.create_task(
            session.send(
                "Fetch.fulfillRequest",
                {
                    "requestId": params["requestId"],
                    "responseCode": 200,
                    "responseHeaders": [{"name": "Content-Type", "value": "text/html"}],
                    "body": empty,
                },
            )
        )
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    session.on("Fetch.requestPaused", on_paused)
    try:
        await session.send("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})
        await page.goto(origin.rstrip("/") + "/", timeout=15)
        await page.evaluate(
            "(items) => { for (const [k, v] of Object.entries(items))"
            " localStorage.setItem(k, v); }",
            dict(items),
        )
    except Exception:
        _log.warning("local_storage_restore_failed")
    finally:
        await page.close()
