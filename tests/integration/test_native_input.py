"""Real-browser checks of input realism and CDP-detection resistance (native driver)."""

import pytest

from botonomus import Botonomus, BrowserConfig
from botonomus.cdp import Connection

pytestmark = pytest.mark.browser

EVENT_PAGE = """<title>Input</title>
<style>body{margin:0}#pad{position:fixed;left:0;top:0;width:300px;height:300px;background:#cde}</style>
<div id=pad>pad</div>
<script>
window.__events = [];
for (const type of ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove',
                    'mouseup', 'click', 'dblclick']) {
  addEventListener(type, e => __events.push({
    type, pressure: e.pressure ?? null, detail: e.detail, buttons: e.buttons,
    button: e.button, trusted: e.isTrusted, x: e.clientX, y: e.clientY,
    tilt: e.pointerType ? [e.tiltX, e.tiltY, e.twist, e.tangentialPressure] : null,
  }), true);
}
</script>"""

# Runtime-domain probes. Each relies on the V8 inspector building previews of console
# arguments, which it only does while a CDP client has the Runtime domain enabled.
# Measured on Chrome 154: the Proxy and Error.stack probes no longer fire even with
# Runtime enabled (V8 fixed both), but the Error-name lookup counter still does, so it
# doubles as the positive control proving the page really can see Runtime.enable.
DETECTION_PAGE = """<title>Detect</title>
<script>
window.__hits = {calls: 0, proxy: 0, stack: 0, nameDelta: 0};
window.__probe = () => {
  const run = {proxy: 0, stack: 0, name: 0};
  // March 2026 (svebaa): the preview walks the prototype chain into the Proxy's
  // ownKeys trap.
  const trap = new Proxy({}, {ownKeys(target) { run.proxy++; return Reflect.ownKeys(target); }});
  console.groupEnd(Object.create(trap));
  console.debug(Object.create(trap));
  // Classic Error.stack getter (patched in V8 in May 2025, kept as a regression check).
  const err = new Error('probe');
  Object.defineProperty(err, 'stack', {get() { run.stack++; return ''; }});
  console.debug(err);
  // Brotector variant: two or more Error name lookups while logging means a preview.
  const proto = Object.create(Error.prototype);
  Object.defineProperty(proto, 'name', {get() { run.name++; return 'Error'; }});
  const named = new Error('probe');
  Object.setPrototypeOf(named, proto);
  run.name = 0;
  console.debug(named);
  __hits.calls++;
  __hits.proxy += run.proxy;
  __hits.stack += run.stack;
  __hits.nameDelta = Math.max(__hits.nameDelta, run.name);
  return run;
};
__probe();
</script>
<button id=go onclick="__probe()">probe</button>"""


@pytest.fixture
def sent_methods(monkeypatch):
    """Record every CDP method the driver sends over any connection."""
    methods: list[str] = []
    original = Connection.send

    async def recording(self, method, params=None, session_id=None, timeout=60.0):
        methods.append(method)
        return await original(self, method, params, session_id, timeout)

    monkeypatch.setattr(Connection, "send", recording)
    return methods


@pytest.fixture
async def session(tmp_path, executable, sent_methods):
    config = BrowserConfig(profile_root=tmp_path, executable_path=executable, driver="native")
    async with Botonomus(config=config) as bot, bot.open(profile="input") as session:
        yield session


async def _load(page, url, html):
    await page.goto(url)
    await page.set_content(html)
    # Chromium drops presses (not moves) until the new document has painted, so wait
    # two frames, as a person would only click on something already visible.
    await page.evaluate(
        "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))",
        isolated_context=False,
    )


async def _events(page, *types):
    events = await page.evaluate("window.__events", isolated_context=False)
    return [e for e in events if not types or e["type"] in types]


async def test_click_reports_pressure_detail_and_trust(session, local_url):
    page = session.page
    await _load(page, local_url, EVENT_PAGE)
    await page.mouse.click(100, 100, delay=0.05)
    down = (await _events(page, "pointerdown"))[0]
    assert down["pressure"] == 0.5
    assert down["buttons"] == 1 and down["button"] == 0
    assert down["tilt"] == [0, 0, 0, 0]
    up = (await _events(page, "pointerup"))[0]
    assert up["pressure"] == 0 and up["buttons"] == 0
    for kind in ("mousedown", "mouseup", "click"):
        (event,) = await _events(page, kind)
        assert event["detail"] == 1, kind
        assert event["trusted"] is True
        assert (event["x"], event["y"]) == (100, 100)
    assert (await _events(page, "mousedown"))[0]["buttons"] == 1
    # The pointer arrived with a move before the press, as a hand would.
    assert (await _events(page))[0]["type"] in ("pointermove", "mousemove")


async def test_dblclick_counts_detail_two(session, local_url):
    page = session.page
    await _load(page, local_url, EVENT_PAGE)
    await page.mouse.dblclick(120, 80)
    assert [e["detail"] for e in await _events(page, "mousedown")] == [1, 2]
    assert [e["detail"] for e in await _events(page, "click")] == [1, 2]
    (dbl,) = await _events(page, "dblclick")
    assert dbl["detail"] == 2 and dbl["trusted"] is True


async def test_automatic_double_click_from_two_clicks(session, local_url):
    page = session.page
    await _load(page, local_url, EVENT_PAGE)
    await page.mouse.click(60, 60)
    await page.mouse.click(61, 60)
    assert [e["detail"] for e in await _events(page, "click")] == [1, 2]
    assert len(await _events(page, "dblclick")) == 1


async def test_drag_moves_report_buttons_and_pressure(session, local_url):
    page = session.page
    await _load(page, local_url, EVENT_PAGE)
    await page.mouse.move(50, 50)
    await page.mouse.down()
    await page.mouse.move(150, 150, steps=5)
    await page.mouse.up()
    pointer_moves = [e for e in await _events(page, "pointermove") if (e["x"], e["y"]) != (50, 50)]
    mouse_moves = [e for e in await _events(page, "mousemove") if (e["x"], e["y"]) != (50, 50)]
    assert pointer_moves and mouse_moves
    assert all(e["buttons"] == 1 and e["pressure"] == 0.5 for e in pointer_moves)
    assert all(e["buttons"] == 1 and e["trusted"] for e in mouse_moves)
    assert (await _events(page, "pointerup"))[0]["pressure"] == 0


async def test_runtime_domain_probes_do_not_fire(session, local_url, sent_methods):
    page = session.page
    await _load(page, local_url, DETECTION_PAGE)
    # Exercise the usual driver paths, then let the page probe again.
    await page.locator("#go").click()
    assert await page.title() == "Detect"
    await page.evaluate("__probe()", isolated_context=False)
    hits = await page.evaluate("window.__hits", isolated_context=False)
    assert hits["calls"] == 3  # on load, from the trusted click, and from evaluate
    assert hits["proxy"] == 0, "console Proxy ownKeys probe detected CDP"
    assert hits["stack"] == 0, "Error.stack getter probe detected CDP"
    assert hits["nameDelta"] < 2, "Error name-lookup counter detected CDP"
    assert "Runtime.enable" not in sent_methods
    assert "Console.enable" not in sent_methods

    # Positive control: with Runtime enabled the same page must notice, otherwise the
    # assertions above would pass vacuously.
    await page.session.send("Runtime.enable")
    try:
        run = await page.evaluate("__probe()", isolated_context=False)
    finally:
        await page.session.send("Runtime.disable")
    assert run["name"] >= 2 or run["proxy"] > 0 or run["stack"] > 0
