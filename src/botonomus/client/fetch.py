"""Loading one request in a real tab and reading back what the browser received."""

import asyncio
import base64
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from ..cdp import Page, ProtocolError, TargetClosedError
from .response import CHARSET, Headers, Response

Wait = Literal["load", "domcontentloaded"]
RESOURCE_TYPES = {"image": "Image", "font": "Font", "media": "Media", "stylesheet": "Stylesheet"}
# Request headers worth replaying on the fast path, as the browser actually sent them.
NAVIGATOR_HEADERS = (
    "user-agent",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "accept-language",
)


class PageContext(Protocol):
    """Anything that opens tabs and holds cookies (`IsolatedContext` or `Context`)."""

    async def new_page(self, url: str = "about:blank") -> Page: ...

    async def cookies(self) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class Request:
    """A request to send: method, URL, extra headers and an optional body."""

    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes | None = None

    @property
    def rewrites(self) -> bool:
        """Whether the navigation must be altered (method, headers or body)."""
        return self.method != "GET" or bool(self.headers) or self.body is not None


@dataclass
class Fetched:
    """A browser response plus what was learned while loading it."""

    response: Response
    navigator_headers: dict[str, str]
    local_storage: tuple[str, dict[str, str]] | None


def cookies_for(url: str, cookies: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Names and values of ``cookies`` that apply to ``url``'s host and path."""
    parts = urlsplit(url)
    host, path = (parts.hostname or "").lower(), parts.path or "/"
    selected = {}
    for cookie in cookies:
        domain = str(cookie.get("domain", "")).lower()
        bare = domain.lstrip(".")
        if host != bare and not (domain.startswith(".") and host.endswith("." + bare)):
            continue
        cookie_path = str(cookie.get("path") or "/")
        if not (
            path == cookie_path
            or path.startswith(cookie_path)
            and (cookie_path.endswith("/") or path[len(cookie_path)] == "/")
        ):
            continue
        if cookie.get("secure") and parts.scheme != "https":
            continue
        selected[str(cookie["name"])] = str(cookie.get("value", ""))
    return selected


async def read_local_storage(page: Page) -> tuple[str, dict[str, str]] | None:
    """The page's origin and its ``localStorage`` items (read from the isolated world)."""
    try:
        result = await page.evaluate(
            "() => { try { return [location.origin, Object.fromEntries("
            "Object.entries(localStorage))]; } catch (e) { return null; } }"
        )
    except Exception:
        return None
    if not result or not isinstance(result[0], str) or not result[0].startswith("http"):
        return None
    return result[0], {str(k): str(v) for k, v in dict(result[1]).items()}


async def fetch_in_page(
    context: PageContext,
    request: Request,
    *,
    wait: Wait = "load",
    timeout: float = 30.0,
    block: Sequence[str] = (),
    settle: float = 0.0,
) -> Fetched:
    """Navigate a fresh tab in ``context`` and return the main document's response.

    Non-GET methods, extra headers and bodies are applied to the navigation request
    itself through ``Fetch`` interception, so the page loads exactly as a browser
    would after a form submission.

    Raises:
        NavigationError: If the browser cannot load the URL.
        TimeoutError_: If ``wait`` is not reached within ``timeout``.
    """
    page = await context.new_page()
    session = page.session
    frame = page.main_frame_id
    documents: list[dict[str, Any]] = []
    sent: dict[str, dict[str, str]] = {}
    received: dict[str, dict[str, str]] = {}
    tasks: set[asyncio.Task[None]] = set()
    blocked = {RESOURCE_TYPES[name] for name in block}
    pending_rewrite = request.rewrites

    def on_response(params: dict[str, Any]) -> None:
        if params.get("type") == "Document" and params.get("frameId") == frame:
            documents.append(params)

    def on_sent_extra(params: dict[str, Any]) -> None:
        sent[params["requestId"]] = {k.lower(): v for k, v in params["headers"].items()}

    def on_received_extra(params: dict[str, Any]) -> None:
        received[params["requestId"]] = params["headers"]

    async def resolve(params: dict[str, Any]) -> None:
        nonlocal pending_rewrite
        request_id = params["requestId"]
        try:
            if params["resourceType"] in blocked:
                await session.send(
                    "Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"}
                )
                return
            if (
                pending_rewrite
                and params["resourceType"] == "Document"
                and (params.get("frameId") == frame)
            ):
                pending_rewrite = False
                headers = {**params["request"]["headers"], **request.headers}
                change: dict[str, Any] = {
                    "requestId": request_id,
                    "method": request.method,
                    "headers": [{"name": k, "value": v} for k, v in headers.items()],
                }
                if request.body is not None:
                    change["postData"] = base64.b64encode(request.body).decode("ascii")
                await session.send("Fetch.continueRequest", change)
                return
            await session.send("Fetch.continueRequest", {"requestId": request_id})
        except (ProtocolError, TargetClosedError):
            pass  # The request was cancelled, or the tab navigated on or closed.

    def on_paused(params: dict[str, Any]) -> None:
        task = asyncio.create_task(resolve(params))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    session.on("Network.responseReceived", on_response)
    session.on("Network.requestWillBeSentExtraInfo", on_sent_extra)
    session.on("Network.responseReceivedExtraInfo", on_received_extra)
    session.on("Fetch.requestPaused", on_paused)
    try:
        await session.send("Network.enable")
        patterns = [
            {"urlPattern": "*", "resourceType": kind, "requestStage": "Request"}
            for kind in sorted(blocked)
        ]
        if request.rewrites:
            patterns.append(
                {"urlPattern": "*", "resourceType": "Document", "requestStage": "Request"}
            )
        if patterns:
            await session.send("Fetch.enable", {"patterns": patterns})
        started = time.monotonic()
        await page.goto(request.url, wait_until=wait, timeout=timeout)
        if settle:
            await asyncio.sleep(settle)
        response = await _shape(page, context, request, documents, received, started)
        main = documents[-1]["requestId"] if documents else None
        navigator = {
            name: value
            for name, value in sent.get(main or "", {}).items()
            if name in NAVIGATOR_HEADERS
        }
        storage = await read_local_storage(page)
        return Fetched(response, navigator, storage)
    finally:
        for task in tuple(tasks):
            task.cancel()
        session.off("Network.responseReceived", on_response)
        session.off("Network.requestWillBeSentExtraInfo", on_sent_extra)
        session.off("Network.responseReceivedExtraInfo", on_received_extra)
        session.off("Fetch.requestPaused", on_paused)
        await page.close()


async def _shape(
    page: Page,
    context: PageContext,
    request: Request,
    documents: list[dict[str, Any]],
    received: dict[str, dict[str, str]],
    started: float,
) -> Response:
    html = await page.content()
    if not documents:
        # No network response (about:, data: or a same-document navigation).
        content, status, headers, url = html.encode(), 200, Headers({}), page.url
    else:
        last = documents[-1]
        status = int(last["response"]["status"])
        url = str(last["response"]["url"])
        headers = Headers({**last["response"]["headers"], **received.get(last["requestId"], {})})
        try:
            body = await page.session.send(
                "Network.getResponseBody", {"requestId": last["requestId"]}
            )
            if body.get("base64Encoded"):
                content = base64.b64decode(body["body"])
            else:
                content = _encode(str(body["body"]), headers.get("content-type", ""))
        except ProtocolError:
            content = html.encode()  # The body was evicted or never kept.
    elapsed = time.monotonic() - started
    context_cookies = await context.cookies()
    return Response(
        url=url,
        status=status,
        headers=headers,
        content=content,
        via="browser",
        elapsed=elapsed,
        method=request.method,
        html=html,
        cookies=cookies_for(url, context_cookies),
    )


def _encode(text: str, content_type: str) -> bytes:
    # CDP decoded the body with the document's charset; encode it back the same way so
    # `Response.text` (which decodes with the declared charset) round-trips.
    match = CHARSET.search(content_type)
    try:
        return text.encode(match.group(1) if match else "utf-8", errors="replace")
    except LookupError:
        return text.encode("utf-8", errors="replace")
