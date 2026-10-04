"""Local consistency checks for a browser configuration, with no third-party traffic.

A loopback page renders the same canvas, WebGL and audio twice, reads identity
values in the main frame, an iframe and dedicated, shared and service workers, and
posts the raw observations back. `report_from_raw` turns them into named checks.
"""

import asyncio
import json
import queue
import secrets
import threading
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from types import TracebackType
from typing import Any, Final, Self

from ..config import BrowserConfig
from ..errors import BotonomusError

FIELDS: Final = ("ua", "brands", "hc", "dm", "tz", "langs")
"""Identity fields every context must agree on."""

CONTEXTS: Final = ("page", "frame", "worker", "shared", "service")
"""Contexts the page reads identity from; every one must report."""

CONTEXT_EXPRESSION: Final = (
    "(() => ({ua: navigator.userAgent, brands: navigator.userAgentData ? "
    "navigator.userAgentData.brands.map(b => b.brand + '/' + b.version).join(',') : null, "
    "hc: navigator.hardwareConcurrency, dm: navigator.deviceMemory ?? null, "
    "tz: Intl.DateTimeFormat().resolvedOptions().timeZone, "
    "langs: navigator.languages.join(',')}))()"
)
"""JavaScript that reads the identity fields in any global scope."""

_RESULT_LIMIT: Final = 256 * 1024


@dataclass(frozen=True, slots=True)
class Check:
    """One named check.

    Attributes:
        name: Check identifier.
        passed: Whether it passed.
        observed: The values it judged.
    """

    name: str
    passed: bool
    observed: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ConsistencyReport:
    """All checks for one browser.

    Attributes:
        product: Browser product string.
        checks: Checks in a fixed order.
    """

    product: str
    checks: list[Check]

    @property
    def ok(self) -> bool:
        """Whether every check passed."""
        return all(check.passed for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        """JSON-compatible data."""
        return {
            "product": self.product,
            "ok": self.ok,
            "checks": [asdict(check) for check in self.checks],
        }


def _pair_equal(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and value[0] is not None
        and value[0] == value[1]
    )


def report_from_raw(raw: dict[str, Any], product: str) -> ConsistencyReport:
    """Judge raw observations posted by the consistency page.

    Missing observations fail their check, which records what was observed.

    Args:
        raw: The page's posted observations.
        product: Browser product string, for the report.

    Returns:
        The report with every check, in a fixed order.
    """
    contexts: dict[str, dict[str, Any]] = raw.get("contexts") or {}
    page = contexts.get("page") or {}
    claims_chrome = "Google Chrome" in str(page.get("brands", ""))
    missing = sorted(set(CONTEXTS) - set(contexts))
    mismatches = [
        {"field": name, "context": context, "page": page.get(name), "value": values.get(name)}
        for context, values in contexts.items()
        if context != "page"
        for name in FIELDS
        if values.get(name) != page.get(name)
    ]
    posted_voices = raw.get("voices")
    voices: list[Any] = posted_voices if isinstance(posted_voices, list) else []
    google_voice = any(v.startswith("Google") and v.endswith("*") for v in voices)
    screen = raw.get("screen")
    if not isinstance(screen, dict):
        screen = {"error": screen} if screen else {}
    identity = str(page.get("ua", "")) + str(page.get("brands", ""))
    posted_accept = raw.get("accept")
    accept: dict[str, Any] = posted_accept if isinstance(posted_accept, dict) else {}
    document_accept = str(accept.get("document") or "")
    image_accept = str(accept.get("image") or "")
    jxl = "image/jxl" in document_accept + image_accept
    screen_ok = (
        bool(screen)
        and "error" not in screen
        and (
            screen.get("outerW", 0) >= screen.get("innerW", 1)
            and screen.get("outerH", 0) >= screen.get("innerH", 1)
            and screen.get("availH", 1) <= screen.get("h", 0)
            # CSS media queries must describe the same screen as JavaScript.
            and screen.get("cssDevice") is True
        )
    )
    persona_screen = raw.get("persona_screen")
    screen_observed: dict[str, Any] = dict(screen)
    if isinstance(persona_screen, dict):
        # A persona screen must be what every surface reports, and the real window
        # must fit inside its work area.
        screen_observed["persona"] = persona_screen
        screen_ok = screen_ok and (
            screen.get("w") == persona_screen.get("w")
            and screen.get("h") == persona_screen.get("h")
            and screen.get("availH")
            == persona_screen.get("h", 0) - persona_screen.get("taskbar", 0)
            and screen.get("outerW", 0) <= screen.get("availW", 0)
            and screen.get("outerH", 0) <= screen.get("availH", 0)
        )
    checks = [
        Check("canvas-stable", _pair_equal(raw.get("canvas")), {"reads": raw.get("canvas")}),
        Check("canvas-solid", raw.get("canvasSolid") is True, {"exact": raw.get("canvasSolid")}),
        Check("webgl-stable", _pair_equal(raw.get("webgl")), {"reads": raw.get("webgl")}),
        Check("audio-stable", _pair_equal(raw.get("audio")), {"reads": raw.get("audio")}),
        Check(
            "contexts-agree",
            not missing and not mismatches,
            {"contexts": sorted(contexts), "missing": missing, "mismatches": mismatches},
        ),
        Check(
            "media",
            raw.get("h264") == "probably" and (raw.get("widevine") is True or not claims_chrome),
            {
                "h264": raw.get("h264"),
                "widevine": raw.get("widevine"),
                "claims_chrome": claims_chrome,
            },
        ),
        # Chrome lists its "Google ..." network voices only after a public page has
        # loaded in the session, so a loopback-only check reports them but cannot
        # require them.
        Check(
            "voices",
            bool(voices),
            {
                "count": len(voices),
                "google_network_voice": google_voice,
                "claims_chrome": claims_chrome,
            },
        ),
        Check(
            "headless-ua",
            bool(page) and "headless" not in identity.lower(),
            {"ua": page.get("ua")},
        ),
        Check("screen", screen_ok, screen_observed),
        # Chrome stable does not advertise JPEG XL; a Chromium build with it on does,
        # in the image (and navigation) Accept headers the server sees.
        Check(
            "accept-header",
            bool(document_accept) and bool(image_accept) and not (jxl and claims_chrome),
            {"document": document_accept, "image": image_accept, "jxl": jxl},
        ),
    ]
    return ConsistencyReport(product, checks)


# A 1x1 transparent GIF: the page loads it so the server sees Chrome's image Accept header.
_PIXEL = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b"
)


class ConsistencyServer:
    """Serves the consistency page and its worker scripts; collects posted results.

    Use as a context manager; `url` is valid while entered.

    Attributes:
        url: The page URL.
    """

    def __init__(self) -> None:
        self._route = "/" + secrets.token_urlsafe(24)
        self._results: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=8)
        self.accept: dict[str, str] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def __enter__(self) -> Self:
        """Start serving on an ephemeral loopback port."""
        owner = self
        asset = files("botonomus.diagnostics").joinpath("assets/consistency.html")
        html = asset.read_text(encoding="utf-8").replace(
            "__CTX_JSON__", json.dumps(CONTEXT_EXPRESSION)
        )
        service_worker = (
            "self.addEventListener('install', () => self.skipWaiting());"
            "self.addEventListener('activate', (e) => e.waitUntil(clients.claim()));"
            f"self.addEventListener('message', (e) => e.source.postMessage({CONTEXT_EXPRESSION}));"
        )
        shared_worker = f"onconnect = (e) => e.ports[0].postMessage({CONTEXT_EXPRESSION});"
        bodies = {
            owner._route: ("text/html; charset=utf-8", html.encode()),
            owner._route + "/sw.js": ("text/javascript", service_worker.encode()),
            owner._route + "/shared.js": ("text/javascript", shared_worker.encode()),
            owner._route + "/pixel.gif": ("image/gif", _PIXEL),
        }
        recorded = {owner._route: "document", owner._route + "/pixel.gif": "image"}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                found = bodies.get(self.path)
                if found is None:
                    self.send_error(404)
                    return
                kind, body = found
                if self.path in recorded:
                    owner.accept[recorded[self.path]] = self.headers.get("Accept", "")
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = -1
                if not 0 < length <= _RESULT_LIMIT:
                    self.send_error(400)
                    return
                self.connection.settimeout(3)
                try:
                    body = self.rfile.read(length)
                except OSError:
                    return
                if self.path != owner._route + "/result":
                    self.send_error(404)
                    return
                try:
                    data = json.loads(body)
                    if not isinstance(data, dict):
                        raise ValueError("shape")
                    owner._results.put_nowait(data)
                except (ValueError, queue.Full):
                    self.send_error(400)
                    return
                self.send_response(204)
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}{self._route}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def receive(self, timeout: float = 30) -> dict[str, Any]:
        """Return the next posted result. Blocking.

        Raises:
            queue.Empty: If none arrives within ``timeout`` seconds.
        """
        return self._results.get(timeout=timeout)

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


async def run_consistency(config: BrowserConfig, *, timeout: float = 30) -> ConsistencyReport:
    """Open one session under ``config``, load the consistency page and judge it.

    Args:
        config: Session configuration.
        timeout: Seconds to wait for the page's observations.

    Returns:
        The report.

    Raises:
        BotonomusError: If the page posts nothing within ``timeout`` seconds.
    """
    from ..core import Botonomus
    from .detection import browser_product

    # A fresh profile per run: a reused one would keep the last run's service worker.
    profile = f"consistency-{secrets.token_hex(4)}"
    with ConsistencyServer() as server:
        async with Botonomus(1, config=config) as bot, bot.open(profile=profile) as session:
            await session.page.goto(server.url)
            product = await browser_product(session.page)
            try:
                raw = await asyncio.to_thread(server.receive, timeout)
            except queue.Empty:
                raise BotonomusError(
                    f"The consistency page posted no result within {timeout:g} s"
                ) from None
            raw["accept"] = dict(server.accept)
            persona = getattr(session, "persona", None)
            if persona is not None and persona.screen is not None:
                raw["persona_screen"] = {
                    "w": persona.screen[0],
                    "h": persona.screen[1],
                    "taskbar": persona.taskbar,
                }
    return report_from_raw(raw, product)
