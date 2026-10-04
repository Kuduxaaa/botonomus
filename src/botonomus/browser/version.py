"""Reading an installed browser's version without starting it.

Running ``chrome --version`` on Windows opens a browser window instead of printing,
so the version is read from the install layout or the file's version resource.
"""

import ctypes
import hashlib
import re
import sys
from pathlib import Path
from typing import Final

_VERSION_DIR: Final = re.compile(r"\d+\.\d+\.\d+\.\d+")


def executable_version(executable: Path) -> str | None:
    """Return the browser version for ``executable``, or ``None`` if unknown.

    Chrome installs on Windows keep a sibling directory named after the version
    (``Application/141.0.7390.55``); the highest such name is used. Otherwise, on
    Windows, the executable's ``VS_FIXEDFILEINFO`` version resource is read.

    Args:
        executable: The browser executable.

    Returns:
        A dotted version such as ``141.0.7390.55``, or ``None``.
    """
    versions = []
    try:
        for entry in executable.parent.iterdir():
            if entry.is_dir() and _VERSION_DIR.fullmatch(entry.name):
                versions.append(tuple(int(part) for part in entry.name.split(".")))
    except OSError:
        pass
    if versions:
        return ".".join(str(part) for part in max(versions))
    if sys.platform == "win32":
        return _file_version(executable)
    return None


def executable_sha256(executable: Path) -> str | None:
    """SHA-256 of the executable's bytes, or ``None`` if it cannot be read. Blocking.

    Args:
        executable: The browser executable.
    """
    digest = hashlib.sha256()
    try:
        with executable.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _file_version(executable: Path) -> str | None:
    if sys.platform != "win32":  # pragma: no cover - narrows ctypes.windll for mypy
        return None
    version = ctypes.windll.version
    path = str(executable)
    size = version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    buffer = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, buffer):
        return None
    info = ctypes.c_void_p()
    length = ctypes.c_uint()
    if not version.VerQueryValueW(buffer, "\\", ctypes.byref(info), ctypes.byref(length)):
        return None
    # VS_FIXEDFILEINFO: signature, struct version, then file version MS and LS dwords.
    words = ctypes.cast(info, ctypes.POINTER(ctypes.c_uint32 * 4)).contents
    if words[0] != 0xFEEF04BD:
        return None
    ms, ls = words[2], words[3]
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
