import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from botonomus import Botonomus, BrowserConfig
from botonomus.human import HumanConfig, HumanPage
from botonomus.profiles import warm_up

pytestmark = pytest.mark.browser

FORM = """<title>Human</title>
<input id=name value="old value"><input id=pw type=password>
<button id=go disabled>Go</button><div id=log></div>
<div style="height:3000px"></div><button id=far>Far</button>
<script>
  const log = (s) => { document.getElementById('log').textContent += s + ';'; };
  setTimeout(() => { document.getElementById('go').disabled = false; }, 400);
  document.getElementById('go').onclick = () => log('go');
  document.getElementById('far').onclick = () => log('far');
  addEventListener('keydown', (e) => { if (!e.isTrusted) log('untrusted'); });
  addEventListener('mousedown', (e) => { if (!e.isTrusted) log('untrusted'); });
</script>"""


@pytest.fixture(params=["native", "playwright"])
async def page(request, tmp_path, executable):
    config = BrowserConfig(profile_root=tmp_path, executable_path=executable, driver=request.param)
    async with Botonomus(config=config) as bot, bot.open(profile="human") as session:
        yield session.page


async def test_human_page_types_clicks_and_scrolls_with_trusted_events(page, local_url):
    await page.goto(local_url)
    await page.set_content(FORM)
    human_page = HumanPage(page, config=HumanConfig.preset("fast", mistype_rate=0.2), seed=3)
    await human_page.fill("#name", "Hello World")
    assert await human_page.locator("#name").input_value() == "Hello World"
    await human_page.type("#pw", "s3cret-Pass", sensitive=True)
    assert await human_page.locator("#pw").input_value() == "s3cret-Pass"
    await human_page.click("#go")  # Disabled for 400 ms: actionability waits.
    await human_page.locator("#far").click()  # Below the fold: reached by wheel.
    await human_page.press("Tab")
    log = await page.locator("#log").inner_text()
    assert "go;" in log and "far;" in log
    assert "untrusted" not in log
    assert await human_page.title() == "Human"


class _Site(BaseHTTPRequestHandler):
    paths: list[str] = []

    def do_GET(self):
        self.paths.append(self.path)
        port = self.server.server_port
        links = (
            '<a href="/a">Article A</a> <a href="/b">Article B</a> '
            '<a href="/login">Sign in</a> <a href="/cart">Cart</a> '
            f'<a href="http://localhost:{port}/elsewhere">Other host</a>'
        )
        body = (
            f"<html><title>Site {self.path}</title><body>{links}"
            '<form action="/search"><input name=q><button>Search</button></form>'
            '<div style="height:2500px">long article text</div>'
            f"{links}</body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.paths.append("POST " + self.path)
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def site():
    _Site.paths = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", _Site.paths
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


async def test_warm_up_browses_local_site_without_forms_or_logins(page, site):
    base, paths = site
    report = await warm_up(
        page,
        sites=[f"{base}/", f"{base}/b", "http://127.0.0.1:1/"],
        duration=24,
        rng_seed=5,
        max_links_per_site=2,
    )
    assert report.sites_visited == 2 and report.sites_failed == 1
    assert report.elapsed <= 24 + 3
    assert "/" in paths and "/b" in paths
    # Which safe article it follows (/a or /b) depends on randomness and timing; every
    # followed link must be one of them. "/b" is also visited once as a site.
    articles = [p for p in paths if p in ("/a", "/b")]
    assert report.links_followed >= 1
    assert len(articles) == 1 + report.links_followed
    assert not any(
        p.startswith(("/login", "/cart", "/search", "/elsewhere", "POST")) for p in paths
    )
