import sys

import pytest

from botonomus import BrowserConfig
from botonomus.diagnostics.consistency import run_consistency


@pytest.mark.browser
async def test_stock_chrome_is_consistent(tmp_path, executable):
    config = BrowserConfig(profile_root=tmp_path, executable_path=executable, persona="off")
    report = await run_consistency(config)
    failing = [c for c in report.checks if not c.passed]
    # Widevine needs a CDM component that fresh profiles download later. GPU-less Linux
    # CI runners have no WebGL and no speech voices; that is the machine, not Botonomus.
    tolerated = {"media"} | (
        {"voices", "webgl-stable"} if sys.platform.startswith("linux") else set()
    )
    assert {c.name for c in failing} <= tolerated, failing
    contexts = next(c for c in report.checks if c.name == "contexts-agree").observed["contexts"]
    assert {"page", "frame", "worker"} <= set(contexts)
