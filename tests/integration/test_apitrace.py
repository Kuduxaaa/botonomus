import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from botonomus import Botonomus, BrowserConfig
from botonomus.diagnostics.apitrace import TraceServer, trace_flags, trace_page

PAGE = b"""<html><body><script>
const c = document.createElement('canvas'); c.getContext('2d').fillRect(0, 0, 5, 5);
c.toDataURL();
navigator.hardwareConcurrency;
window.w = new Worker(URL.createObjectURL(new Blob(['navigator.hardwareConcurrency'])));
window.__native = HTMLCanvasElement.prototype.toDataURL.toString();
</script>
<iframe srcdoc="<script>navigator.platform</script>"></iframe>
</body></html>"""


@pytest.fixture
def page_url():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.browser
async def test_tracer_records_page_and_worker(tmp_path, executable, page_url):
    config = BrowserConfig(
        profile_root=tmp_path,
        executable_path=executable,
        persona="off",
        extra_args=trace_flags(()),
    )
    with TraceServer() as server:
        async with Botonomus(1, config=config) as bot, bot.open(profile="t") as session:
            trace = await trace_page(session.page, page_url, server, settle=3)
            native = await session.page.evaluate("window.__native", isolated_context=False)
    apis = {(r.api, r.context) for r in trace.records}
    assert ("HTMLCanvasElement.toDataURL", "page") in apis
    assert ("Navigator.hardwareConcurrency", "page") in apis
    assert ("Navigator.platform", "frame") in apis
    # Workers are not traced, and their presence must not break the page.
    assert not any(context == "worker" for _, context in apis)
    assert "[native code]" in native
