# Contributing to Botonomus

Thanks for helping. This guide covers the development setup, the checks every change must pass, and the conventions the code base follows. By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Report security issues privately as described in [SECURITY.md](SECURITY.md), never in a public issue.

## Development setup

Python 3.12 or newer and an installed Google Chrome (stable) are required. Windows is the primary platform; unit tests also run elsewhere.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

On Linux or macOS:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

The `dev` extra installs Playwright, Patchright, pytest, pytest-asyncio, ruff, mypy and the build tool. For the documentation site, also install the `docs` extra:

```bash
python -m pip install -e ".[docs]"
mkdocs serve          # live preview at http://127.0.0.1:8000
mkdocs build --strict # what CI runs
```

## Checks

Every pull request must pass:

```powershell
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m ruff format --check .
.\.venv\Scripts\python -m mypy src/botonomus
.\.venv\Scripts\python -m pytest -q
```

`mypy` runs in strict mode (`[tool.mypy] strict = true`). Ruff's line length is 100.

### Browser tests (opt-in)

Tests marked `browser` launch a real browser and are skipped unless you opt in:

```powershell
$env:BOTONOMUS_BROWSER_TESTS = "1"
$env:BOTONOMUS_EXECUTABLE = "C:\Program Files\Google\Chrome\Application\chrome.exe"  # optional
.\.venv\Scripts\python -m pytest tests/integration -q
```

| Variable | Meaning |
|---|---|
| `BOTONOMUS_BROWSER_TESTS=1` | Run tests marked `browser` (real browser launches) |
| `BOTONOMUS_EXECUTABLE` | Browser executable for integration tests; Chrome is discovered when unset |

Run browser tests on a machine you trust: a running session's debugging port is reachable by local processes. `botonomus detect` contacts third-party sites and is never part of the automated test suite.

## Code conventions

- **Package boundaries.** `core` depends on `browser`, `drivers`, `profiles`, `network` and `fingerprint`; `drivers` depend on `cdp`; `human` depends only on the page protocol; `diagnostics` and `cli` sit on top. Nothing imports `cli`, and no package imports another's private (`_`-prefixed) modules.
- **Async throughout.** Blocking I/O runs in `asyncio.to_thread`. Cleanup paths are idempotent and shielded from cancellation.
- **Errors.** Raise a subclass of `botonomus.errors.BotonomusError`, chain the cause (`raise ... from exc`), and never put credentials, cookies, page contents or navigation URLs in messages or logs.
- **No JavaScript fingerprint spoofing.** Fingerprint changes belong in Botonomus Chromium, applied by switches. Do not add code that patches page globals.
- **Claims.** Do not add detection claims to docs or code comments that were not measured; record measurements with date, machine, browser build and network.

### Docstrings

Every public module, class, function and method has a [Google-style docstring](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings), which the API reference renders with mkdocstrings:

```python
def parse_proxy(value: str) -> ProxySpec:
    """Parse and validate a proxy URL.

    Args:
        value: A URL such as ``http://user:pass@host:8080``.

    Returns:
        The parsed proxy.

    Raises:
        ConfigurationError: If the URL is malformed.
    """
```

- First line: one sentence in the imperative or descriptive mood, ending with a period.
- Sections: `Args`, `Returns` (or `Yields`), `Raises`, `Attributes` (for classes), `Example`.
- Use double backticks for code and `:class:`/`:func:` roles for cross-references.
- State units (seconds versus milliseconds) for every timeout. Native-driver timeouts are in seconds.
- Examples that need a browser or network use `# doctest: +SKIP`.

## Commits and pull requests

- One logical change per commit. Keep refactors separate from behaviour changes.
- Subject line in the imperative mood, at most about 72 characters, optionally prefixed with the package (`network: fix forwarder close hang`). Explain the why in the body, including root causes for bug fixes.
- Add or update tests for every behaviour change, and an entry under `Unreleased` in [CHANGELOG.md](CHANGELOG.md) for user-visible changes.
- Update the docs (`README.md`, `docs/`) when public behaviour changes.
