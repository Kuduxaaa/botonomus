"""Widevine for Botonomus Chromium profiles, from the user's own Google Chrome.

Google Chrome ships the Widevine CDM; a Chromium build only gets it from the
component updater, about a minute after start, so a fresh profile fails
``requestMediaKeySystemAccess('com.widevine.alpha')`` where Chrome never does. When
Google Chrome is installed on the machine, its CDM is copied into the profile in the
layout the component updater uses, and the browser registers it at startup.

The CDM is Google's; it is copied locally between the user's own installations and
never redistributed.
"""

import json
import re
import shutil
from collections.abc import Iterable
from pathlib import Path

_VERSION = re.compile(r"\d+(\.\d+){1,3}")


def _bundled_cdm(chrome: Path) -> tuple[Path, str] | None:
    """Chrome's bundled ``WidevineCdm`` directory and its version, if present."""
    roots = [chrome.parent / "WidevineCdm"]  # Linux: /opt/google/chrome/WidevineCdm
    try:
        roots += sorted(
            (child / "WidevineCdm" for child in chrome.parent.iterdir() if child.is_dir()),
            reverse=True,
        )  # Windows: Application/<version>/WidevineCdm
    except OSError:
        return None
    for root in roots:
        try:
            version = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["version"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if isinstance(version, str) and _VERSION.fullmatch(version):
            return root, version
    return None


def seed_widevine(profile_path: Path, chrome_candidates: Iterable[Path]) -> bool:
    """Copy Google Chrome's Widevine CDM into ``profile_path`` unless one is there.

    Args:
        profile_path: The browser's ``--user-data-dir``.
        chrome_candidates: Google Chrome executables to take the CDM from.

    Returns:
        Whether a CDM was copied.
    """
    target_root = Path(profile_path) / "WidevineCdm"
    if target_root.is_dir() and any(target_root.iterdir()):
        return False
    for chrome in chrome_candidates:
        found = _bundled_cdm(Path(chrome))
        if found is None:
            continue
        source, version = found
        target = target_root / version
        temporary = target_root / f".{version}.botonomus-tmp"
        try:
            shutil.rmtree(temporary, ignore_errors=True)
            shutil.copytree(source, temporary)
            temporary.replace(target)
        except OSError:
            shutil.rmtree(temporary, ignore_errors=True)
            return False
        return True
    return False
