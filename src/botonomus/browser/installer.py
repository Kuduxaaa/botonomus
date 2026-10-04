"""Downloading, verifying and managing local Botonomus Chromium installs.

`install` fetches the channel's signed release manifest, authenticates it with
`RELEASE_PUBLIC_KEY`, downloads the archive for the running platform, checks its
size and SHA-256 against the manifest and unpacks it into
``<home>/chromium/<version>/``. An install directory either exists complete, with its
``installed.json`` record, or not at all: archives are extracted into a temporary
sibling directory that is renamed into place only after every check passed.

``<home>`` is, in order of precedence: the ``cache_dir`` argument, the
``BOTONOMUS_HOME`` environment variable, ``%LOCALAPPDATA%\\botonomus`` on Windows and
``~/.cache/botonomus`` elsewhere.

Release layout on the server, relative to the channel manifest URL:

```text
manifest.json              newest release of the channel
manifest.json.sig          base64 Ed25519 signature of manifest.json
<version>/manifest.json    one specific release (used when version= is given)
<version>/manifest.json.sig
```
"""

import asyncio
import contextlib
import hashlib
import http.client
import json
import os
import secrets
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import IO, Any

from filelock import FileLock, Timeout

from .._version import __version__
from ..errors import (
    BinaryDownloadError,
    BinaryNotInstalledError,
    BinaryVerificationError,
    ConfigurationError,
)
from . import release

__all__ = [
    "DEFAULT_MANIFEST_URL",
    "HOME_ENV",
    "MANIFEST_URL_ENV",
    "RELEASE_PUBLIC_KEY",
    "InstalledBinary",
    "botonomus_home",
    "find_installed",
    "install",
    "installed_binaries",
    "uninstall",
]

DEFAULT_MANIFEST_URL = "https://releases.botonomus.dev/chromium/stable/manifest.json"
"""Stable-channel manifest location.

Placeholder until the release host is provisioned; override with
``BOTONOMUS_MANIFEST_URL`` or the ``manifest_url`` argument.
"""

RELEASE_PUBLIC_KEY = bytes.fromhex(
    "ec4619fa58b30810b7287cccb56bd49020ed8b31f7f8efaa774ee39656e75cbc"
)
"""Ed25519 public key that release manifests must be signed with.

Placeholder: generated from a random seed that was discarded immediately, so no
manifest verifies against it until the real release key replaces this constant at
the first release. The release private key is kept offline and never enters the
repository.
"""

MANIFEST_URL_ENV = "BOTONOMUS_MANIFEST_URL"
"""Environment variable overriding `DEFAULT_MANIFEST_URL`."""

HOME_ENV = "BOTONOMUS_HOME"
"""Environment variable overriding the Botonomus data directory."""

INSTALL_RECORD = "installed.json"
"""Name of the record file written inside each complete install directory."""

_RECORD_VERSION = 1
_CHUNK_SIZE = 1 << 20
_MAX_EXTRACTED_BYTES = 8 << 30
_TEMP_PREFIX = ".tmp-"
_LOCK_NAME = ".install.lock"
_LOCK_POLL_SECONDS = 0.2
_EXECUTABLES = {
    "win": "chrome.exe",
    "linux": "chrome",
    "mac": "Chromium.app/Contents/MacOS/Chromium",
}

ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class InstalledBinary:
    """A complete, verified Botonomus Chromium install on disk.

    Attributes:
        version: Botonomus build version, e.g. ``"155.0.8059.26-bn1"``.
        chromium_version: Upstream Chromium version.
        channel: Release channel the build came from.
        platform: Platform tag of the build, e.g. ``"win-x64"``.
        sha256: SHA-256 of the archive the install was unpacked from.
        installed_at: When the install completed (UTC).
        directory: The install directory ``<home>/chromium/<version>``.
        executable: The browser executable inside ``directory``.
    """

    version: str
    chromium_version: str
    channel: str
    platform: str
    sha256: str
    installed_at: datetime
    directory: Path
    executable: Path


def botonomus_home() -> Path:
    """Return the Botonomus data directory, honouring ``BOTONOMUS_HOME``.

    Returns:
        ``$BOTONOMUS_HOME`` if set, else ``%LOCALAPPDATA%\\botonomus`` on Windows and
        ``~/.cache/botonomus`` elsewhere. The directory is not created.
    """
    if override := os.environ.get(HOME_ENV):
        return Path(override)
    if sys.platform == "win32" and (local := os.environ.get("LOCALAPPDATA")):
        return Path(local) / "botonomus"
    return Path.home() / ".cache" / "botonomus"


def _chromium_root(cache_dir: Path | None) -> Path:
    return (cache_dir if cache_dir is not None else botonomus_home()) / "chromium"


async def install(
    *,
    version: str | None = None,
    manifest_url: str | None = None,
    cache_dir: Path | None = None,
    progress: ProgressCallback | None = None,
    public_key: bytes = RELEASE_PUBLIC_KEY,
    allow_insecure_loopback: bool = False,
    timeout: float = 60.0,
) -> InstalledBinary:
    """Download, verify and install Botonomus Chromium for this platform.

    Returns immediately, without network access, when ``version`` is given and
    already installed. Otherwise the manifest is fetched and verified first; if its
    release is already installed that install is returned. Concurrent installs into
    the same ``cache_dir``, from any process, are serialised by a file lock.

    Args:
        version: A specific build version; ``None`` installs the channel's newest.
        manifest_url: Channel manifest URL. ``None`` uses ``BOTONOMUS_MANIFEST_URL``
            if set, else `DEFAULT_MANIFEST_URL`.
        cache_dir: Botonomus data directory; ``None`` uses `botonomus_home`.
        progress: Called on the event loop thread as ``progress(received, total)``
            while the archive downloads, starting with ``(0, total)``.
        public_key: Release verification key. Only tests should change this.
        allow_insecure_loopback: Accept ``http://`` URLs on loopback hosts. For tests
            against a local server only; never enable it for real downloads.
        timeout: Socket timeout in seconds for each network operation.

    Returns:
        The installed binary.

    Raises:
        ConfigurationError: If ``version`` is malformed.
        BinaryDownloadError: If the manifest, signature or archive cannot be fetched.
        BinaryVerificationError: If the signature, manifest, archive size or hash, or
            an archive entry fails verification.
        BinaryNotInstalledError: If the release has no build for this platform.
        OSError: On local filesystem failures such as a full disk.
    """
    root = _chromium_root(cache_dir)
    platform = release.current_platform()
    url = manifest_url or os.environ.get(MANIFEST_URL_ENV) or DEFAULT_MANIFEST_URL
    release.validate_url(url, allow_insecure_loopback=allow_insecure_loopback)
    if version is not None:
        _check_version(version)
        if (existing := _read_record(root / version)) is not None:
            return existing
        url = _sibling_url(url, f"{version}/manifest.json")

    manifest_bytes = await asyncio.to_thread(
        _fetch_bytes, url, release.MAX_MANIFEST_BYTES, timeout, "release manifest"
    )
    signature = await asyncio.to_thread(
        _fetch_bytes,
        _sibling_url(url, "manifest.json.sig"),
        release.MAX_SIGNATURE_BYTES,
        timeout,
        "release manifest signature",
    )
    manifest = release.verify_manifest(
        manifest_bytes,
        signature,
        public_key,
        allow_insecure_loopback=allow_insecure_loopback,
    )
    if version is not None and manifest.version != version:
        raise BinaryVerificationError("Release manifest is for a different version")
    if (existing := _read_record(root / manifest.version)) is not None:
        return existing
    artifact = manifest.artifact_for(platform)
    if artifact is None:
        raise BinaryNotInstalledError(f"Release {manifest.version} has no {platform} build")

    root.mkdir(parents=True, exist_ok=True)
    async with _install_lock(root):
        if (existing := _read_record(root / manifest.version)) is not None:
            return existing
        await asyncio.to_thread(_remove_stale_temp, root)
        token = secrets.token_hex(8)
        archive = root / f"{_TEMP_PREFIX}{token}.zip"
        staging = root / f"{_TEMP_PREFIX}{token}"
        try:
            await _download(artifact, archive, timeout, progress)
            return await asyncio.to_thread(
                _install_archive, archive, staging, root, manifest, artifact
            )
        finally:
            await asyncio.to_thread(_discard, archive, staging)


def installed_binaries(cache_dir: Path | None = None) -> list[InstalledBinary]:
    """List complete installs, newest version first.

    Directories without a valid ``installed.json`` record or whose executable is
    missing are ignored.

    Args:
        cache_dir: Botonomus data directory; ``None`` uses `botonomus_home`.

    Returns:
        Installed binaries for every platform found, sorted by version descending.
    """
    root = _chromium_root(cache_dir)
    if not root.is_dir():
        return []
    found = []
    for entry in root.iterdir():
        if entry.name.startswith(".") or not release.is_valid_version(entry.name):
            continue
        if (record := _read_record(entry)) is not None:
            found.append(record)
    found.sort(key=lambda item: _version_key(item.version), reverse=True)
    return found


def find_installed(version: str | None = None, *, cache_dir: Path | None = None) -> Path | None:
    """Return the executable of the newest (or the given) install for this platform.

    Args:
        version: A specific build version, or ``None`` for the newest.
        cache_dir: Botonomus data directory; ``None`` uses `botonomus_home`.

    Returns:
        The executable path, or ``None`` if no matching install exists.

    Raises:
        ConfigurationError: If ``version`` is malformed.
    """
    if version is not None:
        _check_version(version)
    platform = release.current_platform()
    for item in installed_binaries(cache_dir):
        if item.platform == platform and (version is None or item.version == version):
            return item.executable
    return None


def uninstall(version: str, *, cache_dir: Path | None = None) -> bool:
    """Remove an install.

    The directory is first renamed out of place, so a partially deleted install is
    never mistaken for a valid one; leftovers are cleaned by the next `install`.

    Args:
        version: The build version to remove.
        cache_dir: Botonomus data directory; ``None`` uses `botonomus_home`.

    Returns:
        ``True`` if an install directory was removed, ``False`` if none existed.

    Raises:
        ConfigurationError: If ``version`` is malformed.
        OSError: If the files are in use (for example by a running browser).
    """
    _check_version(version)
    root = _chromium_root(cache_dir)
    target = root / version
    if not target.exists():
        return False
    with FileLock(str(root / _LOCK_NAME)):
        if not target.exists():
            return False
        doomed = root / f"{_TEMP_PREFIX}{secrets.token_hex(8)}"
        os.replace(target, doomed)
        shutil.rmtree(doomed, ignore_errors=True)
    return True


def _check_version(version: str) -> None:
    if not release.is_valid_version(version):
        raise ConfigurationError("Invalid Botonomus Chromium version")


def _version_key(version: str) -> tuple[int, ...]:
    numbers, _, build = version.partition("-bn")
    return (*(int(part) for part in numbers.split(".")), int(build))


def _sibling_url(url: str, relative: str) -> str:
    # Keeps the query string so signed or tokenised download URLs still work.
    parts = urllib.parse.urlsplit(url)
    directory = parts.path.rsplit("/", 1)[0]
    return urllib.parse.urlunsplit(parts._replace(path=f"{directory}/{relative}", fragment=""))


def _open(url: str, timeout: float, what: str) -> http.client.HTTPResponse:
    request = urllib.request.Request(url, headers={"User-Agent": f"botonomus/{__version__}"})
    try:
        response: http.client.HTTPResponse = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise BinaryDownloadError(f"Could not download {what} (HTTP {exc.code})") from exc
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        raise BinaryDownloadError(f"Could not download {what}") from exc
    return response


def _fetch_bytes(url: str, limit: int, timeout: float, what: str) -> bytes:
    response = _open(url, timeout, what)
    with response:
        try:
            data = response.read(limit + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise BinaryDownloadError(f"Could not download {what}") from exc
    if len(data) > limit:
        raise BinaryVerificationError(f"The {what} is too large")
    return data


async def _download(
    artifact: release.Artifact,
    destination: Path,
    timeout: float,
    progress: ProgressCallback | None,
) -> None:
    response = await asyncio.to_thread(_open, artifact.url, timeout, "browser archive")
    try:
        declared = response.getheader("Content-Length")
        if declared is not None and declared.isdigit() and int(declared) != artifact.size:
            raise BinaryVerificationError("Browser archive size does not match the manifest")
        digest = hashlib.sha256()
        received = 0
        if progress is not None:
            progress(0, artifact.size)
        sink = await asyncio.to_thread(destination.open, "xb")
        try:
            while True:
                count = await asyncio.to_thread(_pump, response, sink, digest)
                if count == 0:
                    break
                received += count
                if received > artifact.size:
                    raise BinaryVerificationError(
                        "Browser archive is larger than the manifest states"
                    )
                if progress is not None:
                    progress(received, artifact.size)
        finally:
            await asyncio.to_thread(sink.close)
    finally:
        await asyncio.to_thread(response.close)
    if received != artifact.size:
        raise BinaryVerificationError("Browser archive size does not match the manifest")
    if digest.hexdigest() != artifact.sha256:
        raise BinaryVerificationError("Browser archive SHA-256 does not match the manifest")


def _pump(response: http.client.HTTPResponse, sink: IO[bytes], digest: Any) -> int:
    try:
        chunk = response.read(_CHUNK_SIZE)
    except (OSError, http.client.HTTPException) as exc:
        raise BinaryDownloadError("Could not download browser archive") from exc
    if chunk:
        digest.update(chunk)
        sink.write(chunk)
    return len(chunk)


def _install_archive(
    archive: Path,
    staging: Path,
    root: Path,
    manifest: release.ReleaseManifest,
    artifact: release.Artifact,
) -> InstalledBinary:
    staging.mkdir()
    extract_archive(archive, staging)
    os_tag = artifact.platform.split("-", 1)[0]
    executable = _locate_executable(staging, _EXECUTABLES.get(os_tag, "chrome"))
    record = {
        "record_version": _RECORD_VERSION,
        "version": manifest.version,
        "chromium_version": manifest.chromium_version,
        "channel": manifest.channel,
        "platform": artifact.platform,
        "sha256": artifact.sha256,
        "installed_at": datetime.now(UTC).isoformat(),
        "executable": executable.as_posix(),
    }
    (staging / INSTALL_RECORD).write_text(json.dumps(record, indent=2), encoding="utf-8")
    target = root / manifest.version
    if target.exists():
        # A directory without a valid record (manual copy, foreign tool): replace it.
        doomed = root / f"{_TEMP_PREFIX}{secrets.token_hex(8)}"
        os.replace(target, doomed)
        shutil.rmtree(doomed, ignore_errors=True)
    os.replace(staging, target)
    installed = _read_record(target)
    if installed is None:
        raise BinaryVerificationError("Installed browser could not be validated")
    return installed


def _locate_executable(directory: Path, relative: str) -> PurePosixPath:
    candidate = PurePosixPath(relative)
    if (directory / candidate).is_file():
        return candidate
    children = [child for child in directory.iterdir() if child.is_dir()]
    if len(children) == 1 and (children[0] / candidate).is_file():
        return PurePosixPath(children[0].name) / candidate
    raise BinaryVerificationError("Browser archive does not contain the browser executable")


def extract_archive(archive: Path, destination: Path) -> None:
    """Safely extract a zip archive into an existing empty directory.

    Every entry is validated before anything is written. Absolute paths, drive or
    stream specifiers, backslashes, ``.``/``..`` components, names Windows would
    silently alter, case-insensitive duplicates, symlinks and archives larger than
    8 GiB uncompressed are rejected. Files are created exclusively, so an entry can
    never overwrite another.

    Args:
        archive: The zip file.
        destination: Directory to extract into.

    Raises:
        BinaryVerificationError: If the archive is corrupt or contains unsafe entries.
        OSError: On local filesystem failures.
    """
    base = destination.resolve()
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = _validated_members(bundle, base)
            for info, target in members:
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                _copy_member(bundle, info, target)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError) as exc:
        raise BinaryVerificationError("Browser archive is corrupt") from exc


def _validated_members(bundle: zipfile.ZipFile, base: Path) -> list[tuple[zipfile.ZipInfo, Path]]:
    seen: set[str] = set()
    total = 0
    checked: list[tuple[zipfile.ZipInfo, Path]] = []
    for info in bundle.infolist():
        parts = _member_parts(info)
        key = "/".join(parts).casefold()
        if key in seen:
            raise BinaryVerificationError("Browser archive contains duplicate entries")
        seen.add(key)
        total += info.file_size
        if total > _MAX_EXTRACTED_BYTES:
            raise BinaryVerificationError("Browser archive is too large")
        target = base.joinpath(*parts)
        if not target.resolve().is_relative_to(base):
            raise BinaryVerificationError("Browser archive contains an unsafe path")
        checked.append((info, target))
    return checked


def _member_parts(info: zipfile.ZipInfo) -> list[str]:
    name = info.filename
    mode = info.external_attr >> 16
    if (mode & 0o170000) == 0o120000:
        raise BinaryVerificationError("Browser archive contains a symbolic link")
    if not name or name.startswith("/") or any(char in name for char in "\\:\x00"):
        raise BinaryVerificationError("Browser archive contains an unsafe path")
    parts = name.rstrip("/").split("/") if info.is_dir() else name.split("/")
    for part in parts:
        if part in {"", ".", ".."} or part != part.rstrip(". "):
            raise BinaryVerificationError("Browser archive contains an unsafe path")
    return parts


def _copy_member(bundle: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path) -> None:
    with bundle.open(info) as source, open(target, "xb") as sink:
        shutil.copyfileobj(source, sink, _CHUNK_SIZE)
    mode = (info.external_attr >> 16) & 0o777
    if os.name != "nt" and info.create_system == 3 and mode:
        # Unix-made archives carry permission bits; Chromium needs its executables +x.
        os.chmod(target, mode & 0o755)


def _read_record(directory: Path) -> InstalledBinary | None:
    try:
        data = json.loads((directory / INSTALL_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        if data["record_version"] != _RECORD_VERSION or data["version"] != directory.name:
            return None
        relative = PurePosixPath(data["executable"])
        if relative.is_absolute() or ".." in relative.parts:
            return None
        executable = directory.joinpath(*relative.parts)
        if not executable.is_file():
            return None
        installed_at = datetime.fromisoformat(data["installed_at"])
        return InstalledBinary(
            version=str(data["version"]),
            chromium_version=str(data["chromium_version"]),
            channel=str(data["channel"]),
            platform=str(data["platform"]),
            sha256=str(data["sha256"]),
            installed_at=installed_at,
            directory=directory,
            executable=executable,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _remove_stale_temp(root: Path) -> None:
    # Only called while holding the install lock, so no live install owns these.
    for entry in root.iterdir():
        if entry.name.startswith(_TEMP_PREFIX):
            _discard(entry)


def _discard(*paths: Path) -> None:
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()


@contextlib.asynccontextmanager
async def _install_lock(root: Path) -> AsyncIterator[None]:
    # Polls a non-blocking acquire on the loop thread: a blocking acquire in a worker
    # thread could take the lock after cancellation and never release it.
    lock = FileLock(str(root / _LOCK_NAME), thread_local=False)
    while True:
        try:
            lock.acquire(timeout=0)
            break
        except Timeout:
            await asyncio.sleep(_LOCK_POLL_SECONDS)
    try:
        yield
    finally:
        lock.release()
