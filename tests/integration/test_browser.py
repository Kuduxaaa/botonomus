import asyncio
import time

import psutil
import pytest

from botonomus import Botonomus, BrowserConfig, Human
from botonomus.browser import ChromeBackend
from botonomus.diagnostics import ProbeServer
from botonomus.errors import BrowserStartupError

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("driver", ["patchright", "playwright"])
async def test_visible_browser_native_properties_and_actions(
    tmp_path, executable, local_url, driver
):
    config = BrowserConfig(profile_root=tmp_path, executable_path=executable, driver=driver)
    async with Botonomus(config=config) as bot:
        async with bot.open(profile="native") as session:
            await session.page.goto(local_url)
            assert await session.page.evaluate("navigator.webdriver") is False
            await session.page.get_by_role("button", name="Click", exact=True).click()
            assert await session.page.get_by_role("button").inner_text() == "Clicked"
            assert await session.page.title() == "Local test"


async def test_parallel_profiles_isolated_and_persistent(tmp_path, executable, local_url):
    config = BrowserConfig(profile_root=tmp_path, executable_path=executable)
    async with Botonomus(2, config=config) as bot:
        async with bot.open(profile="a") as first, bot.open(profile="b") as second:
            assert bot.active_count == 2
            await asyncio.gather(first.page.goto(local_url), second.page.goto(local_url))
            await first.page.evaluate("localStorage.setItem('marker','a')")
            await first.context.add_cookies(
                [
                    {
                        "name": "persist",
                        "value": "a",
                        "url": local_url,
                        "expires": time.time() + 3600,
                    }
                ]
            )
            assert await second.page.evaluate("localStorage.getItem('marker')") is None
            assert await second.context.cookies() == []
        async with bot.open(profile="a") as reopened:
            await reopened.page.goto(local_url)
            assert await reopened.page.evaluate("localStorage.getItem('marker')") == "a"
            assert any(
                c["name"] == "persist" and c["value"] == "a"
                for c in await reopened.context.cookies()
            )


async def test_close_stops_owned_process(tmp_path, executable):
    backend = ChromeBackend()
    config = BrowserConfig(profile_root=tmp_path, executable_path=executable)
    await backend.start()
    try:
        handle = await backend.launch(tmp_path / "owned", config)
        identity = psutil.Process(handle.process.pid)
        identity.create_time()
        children = identity.children(recursive=True)
        await handle.close()
        assert not identity.is_running()
        assert all(not child.is_running() for child in children)
        await handle.close()
    finally:
        await backend.close()


async def test_tiny_launch_timeout_recovers_capacity(tmp_path, executable):
    config = BrowserConfig(profile_root=tmp_path, executable_path=executable, launch_timeout=0.001)
    async with Botonomus(config=config) as bot:
        with pytest.raises(BrowserStartupError):
            async with bot.open(profile="timeout"):
                pass
        assert bot.active_count == 0


async def test_locale_sets_languages_and_header(tmp_path, executable):
    headers: list[str] = []

    async def record(reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        headers.extend(line for line in head.split("\r\n") if line.startswith("Accept-Language"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        writer.close()

    server = await asyncio.start_server(record, "127.0.0.1", 0)
    config = BrowserConfig(profile_root=tmp_path, executable_path=executable, locale="de-DE")
    try:
        async with Botonomus(config=config) as bot, bot.open(profile="locale") as session:
            await session.page.goto(f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/")
            assert await session.page.evaluate("navigator.languages") == ["de-DE"]
        assert headers and headers[0].startswith("Accept-Language: de-DE")
    finally:
        server.close()


async def test_authenticated_proxy_carries_browser_traffic(tmp_path, executable, local_url):
    target_port = int(local_url.rsplit(":", 1)[1])
    seen: list[str] = []

    async def relay(reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        seen.append(head)
        if "Proxy-Authorization: Basic dXNlcjpzM2NyZXQ=" not in head:
            writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
            writer.close()
            return
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        target_reader, target_writer = await asyncio.open_connection("127.0.0.1", target_port)

        async def pipe(src, dst):
            while data := await src.read(65536):
                dst.write(data)
                await dst.drain()
            dst.close()

        await asyncio.gather(pipe(reader, target_writer), pipe(target_reader, writer))

    upstream = await asyncio.start_server(relay, "127.0.0.1", 0)
    proxy = f"http://user:s3cret@127.0.0.1:{upstream.sockets[0].getsockname()[1]}"
    config = BrowserConfig(profile_root=tmp_path, executable_path=executable, proxy=proxy)
    try:
        async with Botonomus(config=config) as bot, bot.open(profile="proxied") as session:
            # A non-loopback name, so Chrome's implicit localhost bypass does not apply.
            await session.page.goto("http://botonomus-proxy.test/")
            assert await session.page.title() == "Local test"
        assert any(head.startswith("CONNECT botonomus-proxy.test:80 ") for head in seen)
    finally:
        upstream.close()


async def test_human_input_produces_trusted_pointer_path(tmp_path, executable, local_url):
    config = BrowserConfig(profile_root=tmp_path, executable_path=executable)
    async with Botonomus(config=config) as bot, bot.open(profile="human") as session:
        page = session.page
        # Recorded in the DOM so the check works from Patchright's isolated world too.
        await page.set_content(
            "<button onclick=\"this.textContent='Clicked'\">Click</button><script>"
            "let n = 0, untrusted = 0; addEventListener('mousemove', e => {"
            "n++; untrusted += !e.isTrusted; document.body.dataset.moves = n;"
            "document.body.dataset.untrusted = untrusted; });</script>"
        )
        human = Human(page, seed=1)
        await human.click(page.get_by_role("button", name="Click"))
        assert await page.get_by_role("button").inner_text() == "Clicked"
        assert int(await page.locator("body").get_attribute("data-moves") or 0) >= 8
        assert await page.locator("body").get_attribute("data-untrusted") == "0"
        await page.set_content("<input id=box>")
        await human.type("Hello there", page.locator("#box"))
        assert await page.locator("#box").input_value() == "Hello there"


async def test_probe_reports_no_automation_signals(tmp_path, executable):
    config = BrowserConfig(profile_root=tmp_path, executable_path=executable)
    with ProbeServer() as server:
        async with Botonomus(config=config) as bot, bot.open(profile="probe") as session:
            await session.page.goto(server.url)
            # The page reports itself; no evaluate() in the page's main world.
            snapshot = await asyncio.to_thread(server.receive, 30)
    observations = snapshot["observations"]
    assert observations["webdriver"] is False
    assert observations["automation"]["consoleSerialized"] is False
    assert observations["automation"]["injectedGlobals"] == []
    assert observations["automation"]["windowChrome"] is True
    assert "HeadlessChrome" not in observations["userAgent"]


async def test_covered_window_keeps_rendering(tmp_path, executable):
    config = BrowserConfig(profile_root=tmp_path, executable_path=executable)
    async with Botonomus(config=config) as bot, bot.open(profile="occluded") as session:
        await session.page.set_content("<p>frame</p>")
        frame = await session.page.evaluate(
            "new Promise(r => { requestAnimationFrame(() => r(true));"
            " setTimeout(() => r(false), 3000); })"
        )
        assert frame is True
