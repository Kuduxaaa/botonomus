"""A temporary loopback server for the same probe in normal and automated browsers."""

import json
import queue
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from types import TracebackType
from typing import Any, Self

from .compare import compare_snapshots


class ProbeServer:
    """Serves the probe page on an unguessable loopback route and collects snapshots.

    Use as a context manager; `url` is valid while entered.

    Attributes:
        url: The probe page URL.
    """

    def __init__(self) -> None:
        self._snapshots: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=128)
        self._route = "/" + secrets.token_urlsafe(24)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def __enter__(self) -> Self:
        """Start serving on an ephemeral loopback port."""
        owner = self
        asset = files("botonomus.diagnostics").joinpath("assets/probe.html")
        source = asset.read_text(encoding="utf-8")
        reporting = (
            "<script>collectSnapshot().then(s => fetch(location.pathname + '/snapshot',"
            "{method:'POST',headers:{'Content-Type':'application/json'},"
            "body:JSON.stringify(s)}));</script>"
        )
        body = (source + reporting).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path != owner._route:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                if self.path != owner._route + "/snapshot":
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 65536:
                        raise ValueError("Invalid size")
                    self.connection.settimeout(3)
                    snapshot = json.loads(self.rfile.read(length))
                    if not isinstance(snapshot, dict):
                        raise ValueError("Expected object")
                    compare_snapshots(None, snapshot)
                    owner._snapshots.put_nowait(snapshot)
                except (ValueError, OSError, queue.Full):
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
        """Return the next posted snapshot. Blocking.

        Raises:
            queue.Empty: If none arrives within ``timeout`` seconds.
        """
        return self._snapshots.get(timeout=timeout)

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
