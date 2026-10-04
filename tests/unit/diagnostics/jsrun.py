"""Run a catalogue extractor in Node.js against page text, without a browser."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

NODE = shutil.which("node")
FIXTURES = Path(__file__).parent / "fixtures"
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js not installed")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def run_extractor(
    source: str,
    text: str,
    *,
    title: str = "",
    elements: dict[str, list[dict[str, Any]]] | None = None,
) -> Any:
    """Evaluate ``source`` (a function expression) with a minimal ``document``.

    ``elements`` maps a CSS selector to the elements ``querySelectorAll`` returns;
    each element is a dict of properties (``textContent``, ``className``).
    """
    assert NODE is not None
    script = f"""
const elements = {json.dumps(elements or {})};
const wrap = (e) => Object.assign({{
  classList: {{ contains: (c) => (e.className || '').split(/\\s+/).includes(c) }},
}}, e);
globalThis.document = {{
  title: {json.dumps(title)},
  body: {{ innerText: {json.dumps(text)} }},
  querySelector: (s) => (elements[s] || []).map(wrap)[0] || null,
  querySelectorAll: (s) => (elements[s] || []).map(wrap),
}};
Promise.resolve(({source})()).then((r) => process.stdout.write(JSON.stringify(r)));
"""
    done = subprocess.run(
        [NODE, "-"], input=script, capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)
