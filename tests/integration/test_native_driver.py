import asyncio

import pytest

from botonomus import Botonomus, BrowserConfig
from botonomus.cdp import EvaluationError, NavigationError

pytestmark = pytest.mark.browser

PAGE = (
    "<title>Native</title><input id=name><button id=go>Go</button>"
    "<div id=keys></div><script>window.pageSecret = 41;"
    "addEventListener('keydown', e => document.getElementById('keys').textContent +="
    " `${e.key}:${e.code}:${e.keyCode}:${e.isTrusted};`);</script>"
)


@pytest.fixture
async def session(tmp_path, executable):
    config = BrowserConfig(profile_root=tmp_path, executable_path=executable, driver="native")
    async with Botonomus(config=config) as bot, bot.open(profile="native") as session:
        yield session


async def test_isolated_world_is_invisible_to_page(session, local_url):
    page = session.page
    await page.goto(local_url)
    await page.set_content(PAGE)
    assert await page.evaluate("typeof window.pageSecret") == "undefined"
    assert await page.evaluate("window.pageSecret", isolated_context=False) == 41
    await page.locator("#go").count()  # installs the helper in the isolated world
    assert await page.evaluate("typeof __b", isolated_context=False) == "undefined"
    assert await page.evaluate("document.title") == "Native"


async def test_keyboard_sends_real_key_codes(session, local_url):
    page = session.page
    await page.goto(local_url)
    await page.set_content(PAGE)
    await page.locator("#name").type("aA?")
    assert await page.locator("#name").input_value() == "aA?"
    keys = await page.locator("#keys").inner_text()
    assert "a:KeyA:65:true;" in keys
    assert "A:KeyA:65:true;" in keys
    assert "?:Slash:191:true;" in keys
    await page.locator("#name").fill("replaced ქართული")
    assert await page.locator("#name").input_value() == "replaced ქართული"


async def test_navigation_screenshot_pages_and_errors(session, local_url):
    page = session.page
    await page.goto(local_url)
    assert page.url.startswith(local_url)
    assert await page.title() == "Local test"
    assert (await page.screenshot())[:8] == b"\x89PNG\r\n\x1a\n"
    assert (await page.screenshot(full_page=True))[:4] == b"\x89PNG"
    with pytest.raises(NavigationError):
        await page.goto("http://127.0.0.1:1/")
    with pytest.raises(EvaluationError):
        await page.evaluate("throw new Error('boom')")
    second = await session.context.new_page(local_url)
    await second.wait_for_load()
    assert await second.title() == "Local test"
    await second.close()


async def test_role_locators_and_waiting(session, local_url):
    page = session.page
    await page.goto(local_url)
    await page.set_content(
        "<label for=e>Email</label><input id=e><button aria-label='Send now'>→</button>"
        "<script>setTimeout(() => document.body.insertAdjacentHTML('beforeend',"
        " '<p id=late>late</p>'), 300)</script>"
    )
    await page.get_by_role("textbox", name="Email").type("x@y.z")
    assert await page.locator("#e").input_value() == "x@y.z"
    assert await page.get_by_role("button", name="Send now", exact=True).count() == 1
    assert await page.get_by_role("button", name="Nope").count() == 0
    await page.wait_for_selector("#late", timeout=3)
    assert await page.get_by_text("late").count() == 1


async def test_cookies_roundtrip(session, local_url):
    await session.page.goto(local_url)
    await session.context.add_cookies([{"name": "k", "value": "v", "url": local_url}])
    names = {cookie["name"] for cookie in await session.context.cookies()}
    assert "k" in names
    assert await session.page.evaluate("document.cookie") == "k=v"
    await asyncio.sleep(0)


async def test_proxy_sessions_never_gather_non_proxied_webrtc_candidates(
    tmp_path, executable, local_url
):
    """Chrome ignores --force-webrtc-ip-handling-policy; the profile pref must be set."""
    config = BrowserConfig(
        profile_root=tmp_path, executable_path=executable, proxy="http://127.0.0.1:9"
    )
    async with Botonomus(config=config) as bot, bot.open(profile="rtc") as session:
        await session.page.goto(local_url)
        candidates = await session.page.evaluate(
            """async () => {
              const pc = new RTCPeerConnection();
              pc.createDataChannel('x');
              const found = [];
              pc.onicecandidate = (e) => { if (e.candidate) found.push(e.candidate.candidate); };
              await pc.setLocalDescription(await pc.createOffer());
              await new Promise((r) => setTimeout(r, 1500));
              pc.close();
              return found;
            }""",
            isolated_context=False,
        )
    assert candidates == []
