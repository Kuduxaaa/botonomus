"""Diagnostic tracer: which fingerprinting APIs a page reads, and what they return.

The hooks run in the page's main world and are visible to the page. Use the tracer
only to compare two browsers under identical tracing; never in production sessions.

Coverage: the main frame, same-process frames and out-of-process iframes. Workers
are not traced: auto-attaching a dedicated worker while the page's getters are
hooked makes Chrome 154/155 stop the page's renderer (reproduced 2026-10-04). Use
`botonomus consistency` for worker values.

Records travel by ``navigator.sendBeacon`` to a loopback `TraceServer`. Chrome's
Local Network Access checks would block or prompt for that, so traced sessions run
with ``LocalNetworkAccessChecks`` disabled (see `trace_flags`). CDP bindings are not
an option: Chromium installs ``Runtime.addBinding`` functions into new contexts only
while ``Runtime`` is enabled.
"""

import asyncio
import json
import secrets
import threading
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from types import TracebackType
from typing import Any, Final, Self

_PLACEHOLDER: Final = "__BOTONOMUS_TRACE_ENDPOINT__"
_BODY_LIMIT: Final = 512 * 1024
_FEATURE: Final = "LocalNetworkAccessChecks"


@dataclass(frozen=True, slots=True)
class TraceRecord:
    """One distinct API read.

    Attributes:
        api: ``Interface.member`` such as ``Navigator.hardwareConcurrency``.
        context: ``page``, ``frame``, ``worker``, ``shared-worker`` or
            ``service-worker``.
        origin: The calling context's origin.
        args: JSON of summarized arguments, or ``h:<hash>`` when long.
        value: Summarized return value; long strings and buffers become
            ``h:<hash>:<length>``.
    """

    api: str
    context: str
    origin: str
    args: str
    value: Any


@dataclass(frozen=True, slots=True)
class Trace:
    """Everything one browser read on one page.

    Attributes:
        url: The traced URL.
        product: The browser's ``Browser.getVersion`` product.
        records: Distinct reads in arrival order.
    """

    url: str
    product: str
    records: list[TraceRecord] = field(default_factory=list)

    def to_json(self) -> str:
        """Serialize as indented JSON."""
        return json.dumps(asdict(self), indent=1, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "Trace":
        """Parse `to_json` output."""
        data = json.loads(text)
        return cls(data["url"], data["product"], [TraceRecord(**r) for r in data["records"]])


@dataclass(frozen=True, slots=True)
class TraceDiff:
    """Reads that differ between two traces, keyed by (api, args, context).

    Attributes:
        only_a: Reads only the first browser made.
        only_b: Reads only the second browser made.
        different: Pairs of the same read with different values.
    """

    only_a: list[TraceRecord]
    only_b: list[TraceRecord]
    different: list[tuple[TraceRecord, TraceRecord]]


def trace_script(endpoint: str) -> str:
    """The tracer source with its report endpoint filled in.

    Args:
        endpoint: Where batches are posted, normally `TraceServer.endpoint`.
    """
    asset = files("botonomus.diagnostics").joinpath("assets/apitrace.js")
    return asset.read_text(encoding="utf-8").replace(_PLACEHOLDER, endpoint)


def trace_flags(extra_args: Sequence[str]) -> tuple[str, ...]:
    """Add ``LocalNetworkAccessChecks`` to ``--disable-features`` so beacons reach loopback.

    An existing ``--disable-features=`` value is extended, never duplicated, since
    Chrome honours only the last occurrence.

    Args:
        extra_args: A configuration's extra command-line arguments.

    Returns:
        The arguments with the feature disabled.
    """
    out = list(extra_args)
    for i, arg in enumerate(out):
        if arg.startswith("--disable-features="):
            if _FEATURE not in arg.split("=", 1)[1].split(","):
                out[i] = arg + "," + _FEATURE
            return tuple(out)
    return (*out, f"--disable-features={_FEATURE}")


class TraceServer:
    """Loopback endpoint that collects tracer beacons. Use as a context manager.

    The route is unguessable but visible to the traced page, which could post
    records of its own; at most ``limit`` records are kept.

    Args:
        limit: Maximum records kept; later ones are dropped.

    Attributes:
        endpoint: The URL the tracer posts to (valid while entered).
        limit: Maximum records kept.
    """

    def __init__(self, *, limit: int = 50_000) -> None:
        self.limit = limit
        self._route = "/" + secrets.token_urlsafe(24) + "/trace"
        self._lock = threading.Lock()
        self._records: list[TraceRecord] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.endpoint = ""

    def __enter__(self) -> Self:
        """Start serving on an ephemeral loopback port."""
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = -1
                if not 0 < length <= _BODY_LIMIT:
                    self.send_error(400)  # refused unread; the client may see a reset
                    return
                self.connection.settimeout(3)
                try:
                    body = self.rfile.read(length)  # read first so the client gets a reply
                except OSError:
                    return
                if self.path != owner._route:
                    self.send_error(404)
                    return
                try:
                    batch = json.loads(body)
                    if not isinstance(batch, list):
                        raise ValueError("shape")
                    records = [TraceRecord(**item) for item in batch]
                except (ValueError, TypeError, OSError):
                    self.send_error(400)
                    return
                with owner._lock:
                    room = max(0, owner.limit - len(owner._records))
                    owner._records.extend(records[:room])
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.endpoint = f"http://127.0.0.1:{self._server.server_port}{self._route}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def records(self) -> list[TraceRecord]:
        """A snapshot of every record received so far."""
        with self._lock:
            return list(self._records)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Stop serving."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


async def trace_page(page: Any, url: str, server: TraceServer, *, settle: float = 15.0) -> Trace:
    """Install the tracer in a native-driver page, its frames and OOPIFs, then visit.

    Args:
        page: A native driver page (``session.page``, humanized or not).
        url: Page to trace.
        server: An entered `TraceServer`.
        settle: Seconds to wait after load for the page's scripts to run.

    Returns:
        The trace. It may have no records; callers must report that as such.
    """
    page = getattr(page, "raw", page)
    session = page.session
    script = trace_script(server.endpoint)
    await session.send("Page.addScriptToEvaluateOnNewDocument", {"source": script})
    pending: set[asyncio.Task[None]] = set()

    async def install(params: dict[str, Any]) -> None:
        # A frame paused for the debugger may answer Runtime.evaluate only once it
        # runs, so the commands are sent together: CDP handles them in order, so the
        # script still runs before the frame's own code.
        child = session.connection.session(params["sessionId"])
        commands = [
            child.send("Page.addScriptToEvaluateOnNewDocument", {"source": script}, 10),
            child.send("Runtime.evaluate", {"expression": script}, 10),
            child.send("Runtime.runIfWaitingForDebugger", None, 10),
        ]
        # A frame may close before we reach it; tracing goes on regardless.
        await asyncio.gather(*commands, return_exceptions=True)

    def attached(params: dict[str, Any]) -> None:
        task = asyncio.ensure_future(install(params))
        pending.add(task)
        task.add_done_callback(pending.discard)

    session.on("Target.attachedToTarget", attached)
    await session.send(
        "Target.setAutoAttach",
        {
            "autoAttach": True,
            "waitForDebuggerOnStart": True,
            "flatten": True,
            "filter": [{"type": "iframe", "exclude": False}],
        },
    )
    try:
        await page.goto(url, timeout=90)
        await asyncio.sleep(settle)
        await asyncio.sleep(1.0)  # one more flush interval
    finally:
        session.off("Target.attachedToTarget", attached)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    version = await session.connection.send("Browser.getVersion")
    return Trace(url, str(version.get("product", "unknown")), server.records())


def diff_traces(a: Trace, b: Trace) -> TraceDiff:
    """Compare two traces by (api, args, context).

    Returns:
        Records unique to each side and pairs whose values differ, sorted by api.
    """

    def index(trace: Trace) -> dict[tuple[str, str, str], TraceRecord]:
        out: dict[tuple[str, str, str], TraceRecord] = {}
        for record in trace.records:
            out.setdefault((record.api, record.args, record.context), record)
        return out

    left, right = index(a), index(b)
    only_a = sorted((left[k] for k in left.keys() - right.keys()), key=lambda r: (r.api, r.args))
    only_b = sorted((right[k] for k in right.keys() - left.keys()), key=lambda r: (r.api, r.args))
    different = sorted(
        (
            (left[k], right[k])
            for k in left.keys() & right.keys()
            if left[k].value != right[k].value
        ),
        key=lambda pair: (pair[0].api, pair[0].args),
    )
    return TraceDiff(only_a, only_b, different)


def format_diff(diff: TraceDiff, a_name: str, b_name: str) -> str:
    """Render a diff as plain text, different values first.

    Args:
        diff: The diff.
        a_name: Label for the first trace.
        b_name: Label for the second trace.
    """
    if not (diff.only_a or diff.only_b or diff.different):
        return "No differences (check both traces have records; an empty trace has no records)."

    def short(value: Any) -> str:
        text = json.dumps(value, ensure_ascii=False)
        return text if len(text) <= 160 else text[:157] + "..."

    lines = [f"Different values ({len(diff.different)}):"]
    for x, y in diff.different:
        lines += [
            f"  {x.api} {x.args} [{x.context}]",
            f"    {a_name}: {short(x.value)}",
            f"    {b_name}: {short(y.value)}",
        ]
    lines.append(f"Only {a_name} ({len(diff.only_a)}):")
    lines += [f"  {r.api} {r.args} [{r.context}] = {short(r.value)}" for r in diff.only_a]
    lines.append(f"Only {b_name} ({len(diff.only_b)}):")
    lines += [f"  {r.api} {r.args} [{r.context}] = {short(r.value)}" for r in diff.only_b]
    return "\n".join(lines)
