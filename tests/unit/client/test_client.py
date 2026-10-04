import pytest

import botonomus.client.client as client_module
from botonomus import Client, ConfigurationError, ManagerClosedError
from botonomus.cdp import TargetClosedError
from botonomus.client import Headers, Response
from botonomus.client.client import build_request
from botonomus.client.fastpath import FastPathError, cookie_param
from botonomus.client.fetch import Fetched, cookies_for

CHALLENGE = "<title>Just a moment...</title>"


def response(url, via, status=200, body="<p>ok</p>"):
    return Response(url=url, status=status, headers=Headers({}), content=body.encode(), via=via,
                    elapsed=0.0)  # fmt: skip


class FakeContext:
    def __init__(self, generation):
        self.generation = generation
        self.closed = False
        self.jar = []

    async def cookies(self):
        return list(self.jar)

    async def add_cookies(self, cookies):
        self.jar.extend(cookies)

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self):
        self.generation = 1
        self.major = 154
        self.alive = True
        self.closed = False

    async def connection(self):
        self.alive = True
        return object()

    async def close(self):
        self.closed = True


class FakeFast:
    def __init__(self, script):
        self.script = script
        self.calls = []
        self.closed = False

    async def request(self, method, url, *, headers, body, cookies, timeout):
        self.calls.append(url)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        status, text = item
        return response(url, "http", status, text), [{"name": "f", "value": "1", "domain": "x"}]

    async def close(self):
        self.closed = True


@pytest.fixture
def rig(monkeypatch, tmp_path):
    state = {"browser_script": [], "fetched": [], "contexts": [], "fast": None, "fast_script": []}
    browser = FakeBrowser()

    async def create(connection, *, proxy_server=None):
        context = FakeContext(browser.generation)
        context.proxy_server = proxy_server
        state["contexts"].append(context)
        return context

    async def fetch(context, request, **kwargs):
        state["fetched"].append(request)
        item = state["browser_script"].pop(0) if state["browser_script"] else (200, "<p>ok</p>")
        if callable(item):
            item = item()
        if isinstance(item, BaseException):
            raise item
        status, text = item
        return Fetched(response(request.url, "browser", status, text), {"user-agent": "UA"}, None)

    def fast_factory(target, headers, proxy):
        state["fast"] = FakeFast(state["fast_script"])
        state["fast"].target = target
        return state["fast"]

    monkeypatch.setattr(client_module.IsolatedContext, "create", create)
    monkeypatch.setattr(client_module, "fetch_in_page", fetch)
    monkeypatch.setattr(client_module, "FastPath", fast_factory)
    monkeypatch.setattr(client_module, "available_targets", lambda: ["chrome142", "chrome150"])

    def make(**options):
        client = Client(identity_root=tmp_path / "ids", max_tabs=2, **options)
        client._browser = browser
        return client

    state["make"] = make
    state["browser"] = browser
    return state


async def test_first_request_uses_a_tab_then_the_fast_path(rig):
    rig["fast_script"] += [(200, "<p>fast</p>")]
    async with rig["make"]() as client:
        first = await client.get("https://shop.example/a")
        second = await client.get("https://shop.example/b")
        other = await client.get("https://other.example/")
    assert (first.via, second.via, other.via) == ("browser", "http", "browser")
    assert rig["fast"].target == "chrome150"
    assert rig["fast"].closed and rig["browser"].closed


async def test_fast_path_cookies_are_written_back_to_the_browser(rig):
    rig["fast_script"] += [(200, "ok")]
    async with rig["make"]() as client:
        await client.get("https://shop.example/a")
        await client.get("https://shop.example/b")
        assert {"name": "f", "value": "1", "domain": "x"} in rig["contexts"][0].jar


async def test_challenge_on_fast_path_falls_back_then_pins_the_host(rig):
    rig["fast_script"] += [(403, CHALLENGE), (503, CHALLENGE)]
    async with rig["make"]() as client:
        await client.get("https://shop.example/")
        assert (await client.get("https://shop.example/1")).via == "browser"
        assert (await client.get("https://shop.example/2")).via == "browser"
        assert (await client.get("https://shop.example/3")).via == "browser"
    assert len(rig["fast"].calls) == 2


async def test_challenged_tab_does_not_enable_the_fast_path(rig):
    rig["browser_script"] += [(403, CHALLENGE)]
    async with rig["make"]() as client:
        await client.get("https://shop.example/")
        assert (await client.get("https://shop.example/")).via == "browser"
    assert rig["fast"] is None


async def test_browser_mode_never_uses_the_fast_path(rig):
    async with rig["make"](mode="browser") as client:
        for _ in range(3):
            assert (await client.get("https://shop.example/")).via == "browser"
    assert rig["fast"] is None


async def test_without_curl_cffi_everything_uses_tabs(rig, monkeypatch):
    monkeypatch.setattr(client_module, "available_targets", lambda: None)
    async with rig["make"]() as client:
        for _ in range(3):
            assert (await client.get("https://shop.example/")).via == "browser"


async def test_fresh_context_per_request(rig):
    async with rig["make"](fresh_context=True, mode="browser") as client:
        await client.get("https://a.example/")
        await client.get("https://a.example/")
    assert len(rig["contexts"]) == 2
    assert all(context.closed for context in rig["contexts"])


async def test_shared_context_is_reused(rig):
    async with rig["make"](mode="browser") as client:
        await client.get("https://a.example/")
        await client.get("https://b.example/")
    assert len(rig["contexts"]) == 1


async def test_browser_crash_relaunches_and_retries_once(rig):
    browser = rig["browser"]

    def crash():
        browser.alive = False
        browser.generation += 1
        return TargetClosedError("connection closed")

    rig["browser_script"] += [crash, (200, "<p>again</p>")]
    async with rig["make"](mode="browser") as client:
        result = await client.get("https://a.example/")
    assert result.text == "<p>again</p>"
    assert len(rig["contexts"]) == 2


async def test_closed_tab_error_is_not_retried(rig):
    rig["browser_script"] += [TargetClosedError("tab closed")]
    async with rig["make"](mode="browser") as client:
        with pytest.raises(TargetClosedError):
            await client.get("https://a.example/")
    assert len(rig["fetched"]) == 1


async def test_identity_state_survives_between_uses(rig, tmp_path):
    async with rig["make"](mode="browser") as client:
        async with client.identity("acct-01", proxy="http://proxy.example:8080") as me:
            await me.get("https://a.example/")
            rig["contexts"][-1].jar.append({"name": "sid", "value": "s", "domain": "a.example",
                                            "path": "/", "size": 4, "session": True})  # fmt: skip
        assert rig["contexts"][-1].closed
        async with client.identity("acct-01") as me:
            assert me.proxy == "http://proxy.example:8080"
            assert await me.cookies() == [
                {"name": "sid", "value": "s", "domain": "a.example", "path": "/"}
            ]
    assert rig["contexts"][-1].proxy_server == "http://proxy.example:8080"


async def test_closed_client_rejects_requests(rig):
    client = rig["make"]()
    await client.close()
    with pytest.raises(ManagerClosedError):
        await client.get("https://a.example/")


@pytest.mark.parametrize(
    "options",
    [
        {"mode": "fast"},
        {"max_tabs": 0},
        {"block": ["video"]},
        {"block": "image"},
        {"wait": "networkidle"},
        {"timeout": 0},
        {"proxy": "ftp://x"},
    ],  # fmt: skip
)
def test_invalid_options(options):
    with pytest.raises(ConfigurationError):
        Client(**options)


def test_client_requires_native_driver():
    from botonomus import BrowserConfig

    with pytest.raises(ConfigurationError):
        Client(config=BrowserConfig(driver="playwright"))


def test_build_request_encodes_params_forms_and_json():
    get = build_request("get", "https://a.example/s?q=1", params={"page": 2})
    assert (get.method, get.url, get.body, get.rewrites) == (
        "GET", "https://a.example/s?q=1&page=2", None, False,
    )  # fmt: skip
    form = build_request("POST", "https://a.example/", data={"u": "x y"})
    assert form.body == b"u=x+y"
    assert form.headers == {"Content-Type": "application/x-www-form-urlencoded"}
    body = build_request("POST", "https://a.example/", json={"a": 1}, headers={"X": "1"})
    assert body.body == b'{"a": 1}' and body.headers == {
        "X": "1",
        "Content-Type": "application/json",
    }
    assert build_request("GET", "https://a.example/", headers={"X": "1"}).rewrites


@pytest.mark.parametrize(
    ("url", "kwargs"),
    [
        ("file:///etc/passwd", {}),
        ("javascript:alert(1)", {}),
        ("https://a.example/", {"data": "x", "json": {}}),
    ],  # fmt: skip
)
def test_build_request_rejects(url, kwargs):
    with pytest.raises(ConfigurationError):
        build_request("POST", url, **kwargs)


def test_cookie_param_drops_read_only_fields():
    cookie = {"name": "a", "value": "1", "domain": "x", "path": "/", "size": 2, "session": True,
              "expires": -1, "secure": False}  # fmt: skip
    assert cookie_param(cookie) == {"name": "a", "value": "1", "domain": "x", "path": "/",
                                    "secure": False}  # fmt: skip
    assert cookie_param({"name": "b", "value": "", "expires": 2e9})["expires"] == 2e9


async def test_fast_path_failure_falls_back_to_a_tab(rig):
    rig["fast_script"] += [FastPathError("fast path request failed"), (200, "ok")]
    async with rig["make"]() as client:
        await client.get("https://shop.example/")
        assert (await client.get("https://shop.example/1")).via == "browser"
        assert (await client.get("https://shop.example/2")).via == "http"


async def test_failed_restore_does_not_publish_an_empty_context(rig, tmp_path):
    from botonomus.cdp import ProtocolError

    async with rig["make"](mode="browser") as client:
        async with client.identity("acct") as me:
            await me.get("https://a.example/")
            rig["contexts"][-1].jar.append({"name": "sid", "value": "s", "domain": "a.example"})
        failing = True

        async def add_cookies(self, cookies):
            if failing:
                raise ProtocolError("Storage.setCookies", {"code": -32602, "message": "bad"})
            self.jar.extend(cookies)

        FakeContext.add_cookies, original = add_cookies, FakeContext.add_cookies
        try:
            with pytest.raises(ProtocolError):
                async with client.identity("acct") as me:
                    await me.get("https://a.example/")
            assert rig["contexts"][-1].closed
            failing = False
            async with client.identity("acct") as me:
                assert [c["value"] for c in await me.cookies()] == ["s"]
        finally:
            FakeContext.add_cookies = original


async def test_closed_client_never_relaunches_during_a_retry(rig):
    browser = rig["browser"]
    client = rig["make"](mode="browser")

    def crash_and_close():
        browser.alive = False
        client._closed = True
        return TargetClosedError("connection closed")

    rig["browser_script"] += [crash_and_close]
    with pytest.raises(ManagerClosedError):
        await client.get("https://a.example/")
    assert len(rig["fetched"]) == 1


def test_cookie_path_matching_respects_segments():
    cookies = [{"name": "a", "value": "1", "domain": "x.example", "path": "/foo"}]
    assert cookies_for("https://x.example/foo/bar", cookies) == {"a": "1"}
    assert cookies_for("https://x.example/foo", cookies) == {"a": "1"}
    assert cookies_for("https://x.example/foobar", cookies) == {}
