from botonomus.cdp import IsolatedContext

from .fakes import FakeConnection


async def test_create_with_proxy_and_open_page():
    connection = FakeConnection()
    context = await IsolatedContext.create(connection, proxy_server="http://127.0.0.1:9000")
    assert context.id == "ctx-1"
    assert connection.sent[0] == (
        "Target.createBrowserContext",
        {"disposeOnDetach": True, "proxyServer": "http://127.0.0.1:9000"},
        None,
    )
    page = await context.new_page()
    assert ("Target.createTarget", {"url": "about:blank", "browserContextId": "ctx-1"}, None) in (
        connection.sent
    )
    assert page.target_id == "tab-1"
    assert context.pages == [page]


async def test_cookies_are_scoped_to_the_context():
    connection = FakeConnection()
    context = await IsolatedContext.create(connection)
    assert connection.sent[0][1] == {"disposeOnDetach": True}
    assert await context.cookies() == [{"name": "a", "value": "1"}]
    await context.add_cookies([{"name": "b", "value": "2", "url": "https://x.example/p"}])
    await context.clear_cookies()
    scoped = [(m, p) for m, p, _ in connection.sent if m.startswith("Storage.")]
    assert scoped == [
        ("Storage.getCookies", {"browserContextId": "ctx-1"}),
        (
            "Storage.setCookies",
            {
                "browserContextId": "ctx-1",
                "cookies": [
                    {"name": "b", "value": "2", "domain": "x.example", "path": "/", "secure": True}
                ],  # fmt: skip
            },
        ),
        ("Storage.clearCookies", {"browserContextId": "ctx-1"}),
    ]


async def test_close_disposes_once():
    connection = FakeConnection()
    context = await IsolatedContext.create(connection)
    await context.close()
    await context.close()
    assert connection.methods().count("Target.disposeBrowserContext") == 1
    assert context.closed


async def test_closing_a_page_releases_its_handlers_and_entry():
    connection = FakeConnection()
    context = await IsolatedContext.create(connection)
    before = len(connection.handlers)
    page = await context.new_page()
    assert len(connection.handlers) > before
    await page.close()
    assert connection.handlers_live() == before
    await context.new_page()
    assert page not in context.pages
