"""Locating a browser executable.

Unbranded Chromium is never a silent substitute for Chrome: `find_chrome` only
returns Google Chrome unless the caller names an executable. Botonomus Chromium
builds are found separately and identified by a marker file shipped beside them.
"""

import os
import shutil
import sys
from pathlib import Path
from typing import Final

from ..errors import BrowserUnavailableError
from . import installer

BUILD_MARKER: Final = "botonomus-build.json"
"""File placed next to the executable of every Botonomus Chromium build."""


def is_botonomus_build(executable: Path) -> bool:
    """Whether ``executable`` is a Botonomus Chromium build.

    True when the build marker sits next to the executable, or the executable lives
    inside a verified install made by
    [`botonomus.browser.install`][botonomus.browser.install]. Blocking.
    """
    if (executable.parent / BUILD_MARKER).is_file():
        return True
    return any((parent / installer.INSTALL_RECORD).is_file() for parent in executable.parents[:3])


def find_chrome(explicit: Path | None = None) -> Path:
    """Return the browser executable to launch.

    Args:
        explicit: A caller-supplied executable. When given, it is the only candidate.

    Returns:
        The resolved path of an existing executable.

    Raises:
        BrowserUnavailableError: If ``explicit`` does not exist, or no installed Google
            Chrome is found in the standard locations or on ``PATH``.
    """
    if explicit is not None:
        if explicit.is_file():
            return explicit
        raise BrowserUnavailableError("Configured browser executable does not exist")
    for candidate in _chrome_candidates():
        if candidate.is_file():
            return candidate.resolve()
    raise BrowserUnavailableError(
        "Chrome not found; set BrowserConfig(executable_path=...) explicitly"
    )


def find_botonomus_chromium(
    version: str | None = None, *, cache_dir: Path | None = None
) -> Path | None:
    """Return the executable of an installed Botonomus Chromium build, if any.

    Not consulted by `find_chrome`; callers decide the precedence between an
    installed Botonomus build and Google Chrome.

    Args:
        version: A specific build version, or ``None`` for the newest installed.
        cache_dir: Botonomus data directory; ``None`` uses the default location.

    Returns:
        The executable path, or ``None`` if no matching build is installed.

    Raises:
        ConfigurationError: If ``version`` is malformed.
    """
    return installer.find_installed(version, cache_dir=cache_dir)


def is_testing_build(executable: Path) -> bool:
    """Whether ``executable`` looks like Chrome for Testing or a Playwright download.

    Those builds show a permanent "only for automated testing" infobar, which also
    shrinks the viewport, and are not what ordinary users run.
    """
    text = str(executable).replace("\\", "/").lower()
    return "ms-playwright" in text or "chrome for testing" in text or "chrome-win64/" in text


def _chrome_candidates() -> list[Path]:
    candidates: list[Path] = []
    if sys.platform == "win32":
        for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if root := os.environ.get(key):
                candidates.append(Path(root) / "Google/Chrome/Application/chrome.exe")
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    for name in ("google-chrome", "google-chrome-stable", "chrome"):
        if found := shutil.which(name):
            candidates.append(Path(found))
    return candidates
