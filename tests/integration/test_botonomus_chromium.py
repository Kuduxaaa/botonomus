"""Real-browser checks that Botonomus Chromium applies personas at the C++ level.

Opt in with ``BOTONOMUS_BROWSER_TESTS=1`` and point ``BOTONOMUS_CHROMIUM`` at a
Botonomus Chromium ``chrome.exe``. Every value is read the way a site reads it, in the
window and in a dedicated worker, and compared with the persona the SDK derived.
"""

import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from botonomus import Botonomus, BrowserConfig
from botonomus.browser import BUILD_MARKER
from botonomus.fingerprint import HostInfo, Persona

pytestmark = pytest.mark.browser

TIMEZONE = "Asia/Tokyo"

PAGE = b"<!doctype html><title>bn</title><body>persona</body>"

PROBE = """async () => {
  const workerSource = `postMessage({
    cores: navigator.hardwareConcurrency, memory: navigator.deviceMemory,
    zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    offset: new Date(2026, 0, 15).getTimezoneOffset()})`;
  const worker = new Worker(URL.createObjectURL(new Blob([workerSource])));
  const fromWorker = await new Promise(r => { worker.onmessage = e => r(e.data); });
  const ua = await navigator.userAgentData.getHighEntropyValues(['fullVersionList']);
  return {
    cores: navigator.hardwareConcurrency, memory: navigator.deviceMemory,
    zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    offset: new Date(2026, 0, 15).getTimezoneOffset(),
    worker: fromWorker, webdriver: navigator.webdriver,
    brands: navigator.userAgentData.brands.map(b => b.brand),
    fullVersionList: ua.fullVersionList.map(b => b.brand),
  };
}"""

CANVAS = """() => {
  const c = document.createElement('canvas'); c.width = 240; c.height = 80;
  const g = c.getContext('2d');
  const grad = g.createLinearGradient(0, 0, 240, 80);
  grad.addColorStop(0, '#f60'); grad.addColorStop(0.5, '#09c'); grad.addColorStop(1, '#3c3');
  g.fillStyle = grad; g.fillRect(0, 0, 240, 80);
  g.font = '18px Arial'; g.fillStyle = 'rgba(20,20,120,0.8)';
  g.fillText('Botonomus persona \\u{1F600} fingerprint', 6, 44);
  g.beginPath(); g.arc(200, 40, 30, 0, Math.PI * 1.7); g.strokeStyle = '#a0f'; g.stroke();
  const full = g.getImageData(0, 0, 240, 80).data;
  const again = g.getImageData(0, 0, 240, 80).data;
  const sub = g.getImageData(40, 20, 50, 30).data;
  let subMatches = true;
  for (let y = 0; y < 30 && subMatches; y++)
    for (let x = 0; x < 50 * 4; x++)
      if (sub[y * 200 + x] !== full[(y + 20) * 960 + 160 + x]) { subMatches = false; break; }
  return {pixels: Array.from(full).join(','), repeatable: full.every((v, i) => v === again[i]),
          subMatches, dataUrl: c.toDataURL()};
}"""

AUDIO = """async () => {
  const ctx = new OfflineAudioContext(1, 5000, 44100);
  const osc = ctx.createOscillator(); osc.type = 'triangle'; osc.frequency.value = 10000;
  const comp = ctx.createDynamicsCompressor();
  osc.connect(comp); comp.connect(ctx.destination); osc.start(0);
  const buffer = await ctx.startRendering();
  const data = buffer.getChannelData(0);
  let sum = 0; for (let i = 4500; i < 5000; i++) sum += Math.abs(data[i]);
  return sum.toString();
}"""


@pytest.fixture
def chromium():
    value = os.environ.get("BOTONOMUS_CHROMIUM")
    if not value:
        pytest.skip("Set BOTONOMUS_CHROMIUM to a Botonomus Chromium chrome.exe")
    executable = Path(value)
    marker = executable.parent / BUILD_MARKER
    if not marker.exists():
        marker.write_text(json.dumps({"product": "Botonomus Chromium", "source": "dev build"}))
    return executable


@pytest.fixture
def site():
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


def distinct_seed(host: HostInfo) -> int:
    """A seed whose persona differs from the host, so an unapplied switch is visible."""
    for seed in range(1, 10_000):
        persona = Persona.from_seed(seed, host)
        if persona.hardware_concurrency != host.logical_cpus:
            return seed
    pytest.skip("Host has too few cores for a distinguishable persona")


async def collect(chromium, root, site, profile, persona):
    config = BrowserConfig(
        profile_root=root, executable_path=chromium, persona=persona, timezone=TIMEZONE
    )
    async with Botonomus(config=config) as bot, bot.open(profile=profile) as session:
        await session.page.goto(site)
        return {
            "persona": session.persona,
            "probe": await session.page.evaluate(PROBE),
            "canvas": await session.page.evaluate(CANVAS),
            "audio": await session.page.evaluate(AUDIO),
        }


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def test_persona_is_applied_in_window_and_worker(chromium, tmp_path, site):
    seed = distinct_seed(HostInfo.detect())
    result = await collect(chromium, tmp_path, site, "bn-a", seed)
    persona, probe = result["persona"], result["probe"]
    assert persona is not None and persona.timezone == TIMEZONE
    for scope in (probe, probe["worker"]):
        assert scope["cores"] == persona.hardware_concurrency
        assert scope["memory"] == persona.device_memory
        assert scope["zone"] == TIMEZONE
        assert scope["offset"] == -540
    assert probe["webdriver"] is False
    assert "Google Chrome" in probe["brands"]
    assert "Google Chrome" in probe["fullVersionList"]


async def test_noise_is_stable_per_profile_and_differs_between_profiles(chromium, tmp_path, site):
    first = await collect(chromium, tmp_path, site, "bn-a", 11)
    again = await collect(chromium, tmp_path, site, "bn-a", 11)
    other = await collect(chromium, tmp_path, site, "bn-b", 22)
    plain = await collect(chromium, tmp_path, site, "bn-c", "off")
    for result in (first, other):
        assert result["canvas"]["repeatable"] and result["canvas"]["subMatches"]
    assert digest(first["canvas"]["pixels"]) == digest(again["canvas"]["pixels"])
    assert first["canvas"]["dataUrl"] == again["canvas"]["dataUrl"]
    assert first["audio"] == again["audio"]
    assert digest(first["canvas"]["pixels"]) != digest(other["canvas"]["pixels"])
    assert digest(first["canvas"]["pixels"]) != digest(plain["canvas"]["pixels"])
    assert first["audio"] != other["audio"]
