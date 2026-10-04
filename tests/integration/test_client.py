"""Real-browser checks of the httpx-like Client against a local server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import botonomus
from botonomus import BrowserConfig, Client, HTTPStatusError
from botonomus.client.fastpath import available_targets, impersonation_target

pytestmark = pytest.mark.browser

PAGE = b"""<!doctype html><meta charset=utf-8><title>Page</title>
<img src="/img.png"><div id=out>static</div>
<script>
document.getElementById('out').textContent = 'rendered';
localStorage.setItem('k', 'v');
</script>"""
STORAGE = b"""<!doctype html><title>pending</title>
<script>document.title = 'k=' + localStorage.getItem('k');</script>"""


@pytest.fixture
def server():
    hits = {"img": 0}

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status, body, content_type="text/html; charset=utf-8", extra=()):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            for name, value in extra:
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _echo(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode() if length else ""
            data = {"method": self.command, "headers": dict(self.headers.items()), "body": body}
            self._reply(200, json.dumps(data).encode(), "application/json")

        def do_GET(self):
            if self.path == "/page":
                self._reply(200, PAGE, extra=[("Set-Cookie", "sid=abc; Path=/")])
            elif self.path == "/storage":
                self._reply(200, STORAGE)
            elif self.path == "/img.png":
                hits["img"] += 1
                self._reply(200, b"", "image/png")
            elif self.path == "/strict":
                self._reply(
                    200, b"<title>s</title>", extra=[("Set-Cookie", "s=1; Path=/; SameSite=Strict")]
                )
            elif self.path == "/logout":
                self._reply(
                    200,
                    b"<title>bye</title>",
                    extra=[
                        ("Set-Cookie", "s=; Path=/; Max-Age=0"),
                        ("Set-Cookie", "fresh=2; Path=/"),
                    ],
                )
            elif self.path == "/missing":
                self._reply(404, b"<title>missing</title>")
            else:
                self._echo()

        def do_POST(self):
            self._echo()

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", hits
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


@pytest.fixture
def config(tmp_path, executable):
    return BrowserConfig(profile_root=tmp_path / "profiles", executable_path=executable)


async def test_get_returns_raw_body_rendered_html_and_cookies(server, config):
    base, _ = server
    async with Client(config=config, mode="browser") as client:
        response = await client.get(f"{base}/page")
    assert (response.status, response.via) == (200, "browser")
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert ">static<" in response.text
    assert ">rendered<" in (response.html or "")
    assert response.cookies == {"sid": "abc"}
    assert response.elapsed > 0


async def test_post_form_and_json_bodies(server, config):
    base, _ = server
    async with Client(config=config, mode="browser") as client:
        form = (await client.post(f"{base}/echo", data={"user": "a b"})).json()
        body = (await client.post(f"{base}/echo", json={"x": 1}, headers={"X-Test": "1"})).json()
    assert form["method"] == "POST" and form["body"] == "user=a+b"
    assert form["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert body["body"] == '{"x": 1}' and body["headers"]["X-Test"] == "1"


async def test_cookies_persist_in_the_shared_context_only(server, config):
    base, _ = server
    async with Client(config=config, mode="browser") as client:
        await client.get(f"{base}/page")
        echoed = (await client.get(f"{base}/echo")).json()
    assert echoed["headers"].get("Cookie") == "sid=abc"
    async with Client(config=config, mode="browser", fresh_context=True) as client:
        await client.get(f"{base}/page")
        assert "Cookie" not in (await client.get(f"{base}/echo")).json()["headers"]


async def test_blocked_images_are_not_requested(server, config):
    base, hits = server
    async with Client(config=config, mode="browser", block=["image"]) as client:
        await client.get(f"{base}/page")
    assert hits["img"] == 0


async def test_error_status_is_returned_and_raises_on_request(server, config):
    base, _ = server
    async with Client(config=config, mode="browser") as client:
        response = await client.get(f"{base}/missing")
    assert response.status == 404
    with pytest.raises(HTTPStatusError):
        response.raise_for_status()


async def test_fast_path_reuses_browser_cookies_and_headers(server, config):
    base, _ = server
    async with Client(config=config) as client:
        first = await client.get(f"{base}/page")
        if impersonation_target(client._browser.major, available_targets() or []) is None:
            pytest.skip("curl_cffi has no impersonation target for this Chrome")
        second = await client.get(f"{base}/echo")
    assert (first.via, second.via) == ("browser", "http")
    sent = second.json()["headers"]
    assert sent["Cookie"] == "sid=abc"
    assert "Chrome/" in sent["User-Agent"]
    assert "Upgrade" not in sent


async def test_identity_restores_cookies_and_local_storage(server, config, tmp_path):
    base, _ = server
    ids = tmp_path / "ids"
    async with Client(config=config, mode="browser", identity_root=ids) as client:
        async with client.identity("acct-01") as me:
            await me.get(f"{base}/page")
    assert (ids / "acct-01" / "state.json").is_file()
    async with Client(config=config, mode="browser", identity_root=ids) as client:
        async with client.identity("acct-01") as me:
            assert (await me.get(f"{base}/echo")).json()["headers"]["Cookie"] == "sid=abc"
            async with me.page(f"{base}/storage") as page:
                assert await page.title() == "k=v"


async def test_module_level_get(server, config):
    base, _ = server
    response = await botonomus.get(f"{base}/page", config=config)
    assert response.status == 200 and response.cookies == {"sid": "abc"}


async def test_fast_path_keeps_cookie_attributes_and_deletions(server, config, tmp_path):
    base, _ = server
    async with Client(config=config, identity_root=tmp_path / "ids") as client:
        async with client.identity("acct") as me:
            await me.get(f"{base}/strict")
            if (await me.get(f"{base}/echo")).via != "http":
                pytest.skip("fast path unavailable for this Chrome")
            [kept] = [c for c in await me.cookies() if c["name"] == "s"]
            assert kept["sameSite"] == "Strict"
            assert (await me.get(f"{base}/logout")).via == "http"
            names = {c["name"] for c in await me.cookies()}
    assert names == {"fresh"}


async def test_handlers_do_not_grow_with_requests(server, config):
    base, _ = server
    async with Client(config=config, mode="browser") as client:
        await client.get(f"{base}/page")
        connection = client._browser.connection_of(client._browser.session)
        before = sum(len(handlers) for handlers in connection._handlers.values())
        keys = len(connection._handlers)
        for _ in range(5):
            await client.get(f"{base}/page")
        after = sum(len(handlers) for handlers in connection._handlers.values())
        assert len(connection._handlers) == keys
    assert after == before


async def test_close_during_requests_raises_closed_errors(server, config):
    import asyncio

    from botonomus import ManagerClosedError

    base, _ = server
    client = Client(config=config, mode="browser")
    await client.get(f"{base}/page")
    tasks = [asyncio.create_task(client.get(f"{base}/page", settle=1)) for _ in range(3)]
    await asyncio.sleep(0.3)
    await asyncio.wait_for(client.close(), 30)
    results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 30)
    for result in results:
        assert not isinstance(result, BaseException) or isinstance(result, ManagerClosedError)
    assert not client._browser.alive
