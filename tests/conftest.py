import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("BOTONOMUS_BROWSER_TESTS") != "1":
        for item in items:
            if item.get_closest_marker("browser") is not None:
                item.add_marker(pytest.mark.skip(reason="Set BOTONOMUS_BROWSER_TESTS=1"))


@pytest.fixture
def executable():
    value = os.environ.get("BOTONOMUS_EXECUTABLE")
    return Path(value) if value else None


@pytest.fixture
def local_url():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = (
                b"<html><title>Local test</title>"
                b"<button onclick=\"this.textContent='Clicked'\">Click</button></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
