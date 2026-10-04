import json
import urllib.error
import urllib.request

import pytest

from botonomus.diagnostics.apitrace import (
    Trace,
    TraceRecord,
    TraceServer,
    diff_traces,
    format_diff,
    trace_flags,
    trace_script,
)


def rec(api, value, args="[]", context="page"):
    return TraceRecord(api=api, context=context, origin="https://x", args=args, value=value)


def test_diff_classifies_records():
    a = Trace(
        "u",
        "Chrome/154",
        [rec("Navigator.userAgent", "A"), rec("Screen.width", 1920), rec("only.a", 1)],
    )
    b = Trace(
        "u",
        "Chrome/155",
        [rec("Navigator.userAgent", "B"), rec("Screen.width", 1920), rec("only.b", 2)],
    )
    diff = diff_traces(a, b)
    assert [r.api for r in diff.only_a] == ["only.a"]
    assert [r.api for r in diff.only_b] == ["only.b"]
    assert [(x.value, y.value) for x, y in diff.different] == [("A", "B")]
    text = format_diff(diff, "chrome", "bn")
    assert "Navigator.userAgent" in text and "chrome" in text and "only.a" in text


def test_diff_of_empty_traces_reports_empty():
    diff = diff_traces(Trace("u", "p", []), Trace("u", "p", []))
    assert "no records" in format_diff(diff, "a", "b").lower()


def test_trace_roundtrip():
    t = Trace("u", "p", [rec("x", {"k": 1})])
    assert Trace.from_json(t.to_json()) == t


def test_trace_flags_merge_disable_features():
    assert trace_flags(()) == ("--disable-features=LocalNetworkAccessChecks",)
    assert trace_flags(("--x", "--disable-features=JXLImageFormat")) == (
        "--x",
        "--disable-features=JXLImageFormat,LocalNetworkAccessChecks",
    )
    already = ("--disable-features=LocalNetworkAccessChecks",)
    assert trace_flags(already) == already


def test_trace_script_embeds_endpoint():
    script = trace_script("http://127.0.0.1:1/abc/trace")
    assert "http://127.0.0.1:1/abc/trace" in script
    assert "__BOTONOMUS_TRACE_ENDPOINT__" not in script


def post(url, body: bytes):
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "text/plain"}
    )
    return urllib.request.urlopen(request, timeout=5).status


def test_trace_server_accepts_batches_and_rejects_bad_bodies():
    with TraceServer() as server:
        batch = [
            {"api": "Screen.width", "context": "page", "origin": "o", "args": "[]", "value": 1}
        ]
        assert post(server.endpoint, json.dumps(batch).encode()) == 204
        assert server.records() == [TraceRecord("Screen.width", "page", "o", "[]", 1)]
        with pytest.raises(urllib.error.HTTPError) as bad:
            post(server.endpoint, b"not json")
        assert bad.value.code == 400
        # The server refuses without reading the body; the client sees 400 or a reset.
        with pytest.raises((urllib.error.HTTPError, ConnectionError)) as big:
            post(server.endpoint, b"[" + b"1," * 300_000 + b"1]")
        if isinstance(big.value, urllib.error.HTTPError):
            assert big.value.code == 400
        assert len(server.records()) == 1
        with pytest.raises(urllib.error.HTTPError) as wrong:
            post(server.endpoint.rsplit("/", 2)[0] + "/nope/trace", b"[]")
        assert wrong.value.code == 404


def test_large_flush_is_chunked_and_never_dropped(tmp_path):
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    harness = """
globalThis.self = globalThis; globalThis.top = globalThis;
globalThis.location = { origin: 'https://x' };
const beacons = [], fetches = [];
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: {
  sendBeacon: (url, body) => {
    if (body.length > 65536) return false;  // Chrome's keepalive budget
    beacons.push(body.length); received.push(...JSON.parse(body)); return true;
  } } });
globalThis.fetch = (url, opts) => {
  if (opts.keepalive && opts.body.length > 65536) return Promise.reject(new TypeError('budget'));
  fetches.push(opts.body.length); received.push(...JSON.parse(opts.body)); return Promise.resolve();
};
const received = [];
class HTMLMediaElement { canPlayType(t) { return 'probably-'.repeat(20); } }
globalThis.HTMLMediaElement = HTMLMediaElement;
SCRIPT
const el = new HTMLMediaElement();
for (let i = 0; i < 500; i++) {
  el.canPlayType('video/mp4; codecs="avc1.' + i + '"' + 'x'.repeat(120));
}
setTimeout(() => {
  const big = Math.max(0, ...beacons, ...fetches);
  process.stdout.write(JSON.stringify({ received: received.length, biggest: big }));
  process.exit(0);
}, 1200);
"""
    script = harness.replace("SCRIPT", trace_script("http://127.0.0.1:1/t/trace"))
    done = subprocess.run([node, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["received"] == 500
    assert result["biggest"] <= 65536


def node_trace(body: str) -> dict:
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    harness = """
globalThis.self = globalThis; globalThis.top = globalThis;
globalThis.location = { origin: 'https://x' };
const received = [];
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: {
  sendBeacon: (url, b) => { received.push(...JSON.parse(b)); return true; } } });
SETUP
SCRIPT
BODY
setTimeout(() => { process.stdout.write(JSON.stringify(OUT)); process.exit(0); }, 700);
"""
    setup, run, out = body.split("---")
    script = (
        harness.replace("SETUP", setup)
        .replace("SCRIPT", trace_script("http://127.0.0.1:1/t/trace"))
        .replace("BODY", run)
        .replace("OUT", out.strip())
    )
    done = subprocess.run([node, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_tracer_does_not_record_its_own_nested_reads():
    result = node_trace("""
class NavigatorUAData {}
// IDL attributes are enumerable accessors on the prototype, unlike class getters.
Object.defineProperty(NavigatorUAData.prototype, 'brands', { enumerable: true, configurable: true,
  get() { return [{ brand: 'X', version: '1' }]; } });
class Navigator { get userAgentData() { return new NavigatorUAData(); } }
globalThis.NavigatorUAData = NavigatorUAData; globalThis.Navigator = Navigator;
const nav = new Navigator();
---
nav.userAgentData;
---
received.map((r) => r.api)
""")
    assert result == ["Navigator.userAgentData"]


def test_wrappers_keep_native_length_and_name():
    result = node_trace("""
class HTMLMediaElement { canPlayType(type) { return ''; } }
globalThis.HTMLMediaElement = HTMLMediaElement;
---
const f = HTMLMediaElement.prototype.canPlayType;
---
[f.length, f.name]
""")
    assert result == [1, "canPlayType"]


def test_trace_server_caps_records():
    from botonomus.diagnostics import apitrace

    with TraceServer(limit=3) as server:
        batch = [
            {"api": f"a{i}", "context": "page", "origin": "o", "args": "[]", "value": i}
            for i in range(5)
        ]
        post(server.endpoint, json.dumps(batch).encode())
        assert len(server.records()) == 3
    assert apitrace.TraceServer().limit == 50_000


def test_non_configurable_property_does_not_stop_the_tracer():
    result = node_trace("""
class Screen {}
Object.defineProperty(Screen.prototype, 'width', { get() { return 1; } });  // non-configurable
class HTMLMediaElement { canPlayType(type) { return ''; } }
globalThis.Screen = Screen; globalThis.HTMLMediaElement = HTMLMediaElement;
---
new HTMLMediaElement().canPlayType('video/mp4');
---
received.map((r) => r.api)
""")
    assert result == ["HTMLMediaElement.canPlayType"]
