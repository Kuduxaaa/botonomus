import hashlib
import sys
from pathlib import Path

import pytest

from botonomus.browser import executable_sha256, executable_version
from botonomus.browser import version as version_module


def test_version_from_highest_sibling_directory(tmp_path):
    for name in ("9.0.0.1", "131.0.6778.86", "131.0.6778.9", "Locales", "1.2.3"):
        (tmp_path / name).mkdir()
    executable = tmp_path / "chrome.exe"
    executable.write_bytes(b"")
    assert executable_version(executable) == "131.0.6778.86"


def test_version_unknown_without_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(version_module, "_file_version", lambda path: None)
    executable = tmp_path / "chrome"
    executable.write_bytes(b"")
    assert executable_version(executable) is None
    assert executable_version(tmp_path / "missing" / "chrome") is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows version resource")
def test_windows_version_resource():
    # Every Windows Python executable carries a version resource.
    version = version_module._file_version(Path(sys.executable))
    assert version is not None and version.count(".") == 3


def test_sha256(tmp_path):
    executable = tmp_path / "chrome"
    executable.write_bytes(b"binary" * 1000)
    assert executable_sha256(executable) == hashlib.sha256(b"binary" * 1000).hexdigest()
    assert executable_sha256(tmp_path / "missing") is None
