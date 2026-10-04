"""Real-browser checks for ``botonomus probe`` and detection runs on a local page."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from botonomus import Botonomus, BrowserConfig
from botonomus.cli import main
from botonomus.diagnostics import DetectionSite, run_detection

pytestmark = pytest.mark.browser

# The page computes its verdict after load and keeps a secret in a page global, which
# the extractor (isolated world) must not see.
FAKE_DETECTOR = b"""<html><title>Fake detector</title><body><div id=result>pending</div>
<script>
window.pageSecret = 'main-world-only';
setTimeout(() => {
  document.getElementById('result').textContent =
    navigator.webdriver ? 'You are a bot' : 'You are human';
}, 300);
</script></body></html>"""

EXTRACTOR = """() => {
  const text = document.getElementById('result').textContent;
  const verdict = text === 'You are human' ? 'pass' : text === 'You are a bot' ? 'fail' : 'unknown';
  return {verdict, details: {text, secret: typeof window.pageSecret}};
}"""


@pytest.fixture
def detector_url():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(FAKE_DETECTOR)

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


def test_probe_command_json(executable, tmp_path, capsys):
    argv = ["probe", "--json", "--output", str(tmp_path)]
    if executable is not None:
        argv += ["--executable", str(executable)]
    assert main(argv) == 0
    data = json.loads(capsys.readouterr().out)
    snapshot = data["automated"]
    assert snapshot["schema_version"] == 1
    assert snapshot["observations"]
    assert data["comparison"] is None
    assert json.loads((tmp_path / "automated.json").read_text(encoding="utf-8")) == snapshot


@pytest.mark.parametrize("driver", ["native", "patchright"])
async def test_detection_run_on_local_site(driver, executable, tmp_path, detector_url):
    site = DetectionSite(
        "local",
        detector_url,
        ready="document.getElementById('result').textContent !== 'pending'",
        ready_timeout=10,
        settle=0.2,
        extractor=EXTRACTOR,
    )
    config = BrowserConfig(
        profile_root=tmp_path / "profiles", executable_path=executable, driver=driver
    )
    async with Botonomus(config=config) as bot:
        report = await run_detection(bot, [site], runs=2, output=tmp_path / "out", seed=1)
    (summary,) = report.sites
    assert [run.error_type for run in report.runs] == [None, None]
    assert (summary.runs, summary.passed, summary.pass_rate) == (2, 2, 1.0)
    first = report.runs[0]
    assert first.details == {"text": "You are human", "secret": "undefined"}
    assert "You are human" in first.text_excerpt
    assert (tmp_path / "out" / first.screenshot).read_bytes()[:4] == b"\x89PNG"
    assert report.environment.browser_product.split("/")[0] in ("Chrome", "Chromium")
    assert report.environment.executable_sha256
    assert json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
