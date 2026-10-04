import functools
import hashlib
import http.server
import io
import json
import stat
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from botonomus.browser import find_botonomus_chromium, installer, release, signing
from botonomus.errors import (
    BinaryDownloadError,
    BinaryNotInstalledError,
    BinaryVerificationError,
    BrowserUnavailableError,
    ConfigurationError,
)

SEED = bytes(range(32))
PUBLIC = signing.public_key_from_seed(SEED)
VERSION = "155.0.8059.26-bn1"
PLATFORM = release.current_platform()
EXECUTABLE = installer._EXECUTABLES[PLATFORM.split("-")[0]]


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    handler = functools.partial(_QuietHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", root
    finally:
        httpd.shutdown()
        httpd.server_close()


def make_zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, content in entries.items():
            bundle.writestr(name, content)
    return buffer.getvalue()


def browser_zip(prefix=""):
    return make_zip(
        {
            f"{prefix}{EXECUTABLE}": b"binary" * 1000,
            f"{prefix}resources.pak": b"pak" * 5000,
            f"{prefix}locales/en-US.pak": b"locale",
        }
    )


def publish(
    root,
    base,
    archive,
    *,
    version=VERSION,
    subdir="",
    sha256=None,
    size=None,
    platform=PLATFORM,
    seed=SEED,
):
    target = root / subdir if subdir else root
    target.mkdir(parents=True, exist_ok=True)
    (target / "browser.zip").write_bytes(archive)
    prefix = f"{base}/{subdir}" if subdir else base
    document = {
        "schema_version": 1,
        "channel": "stable",
        "version": version,
        "chromium_version": version.split("-")[0],
        "published_at": "2026-10-01T12:00:00+00:00",
        "artifacts": [
            {
                "platform": platform,
                "url": f"{prefix}/browser.zip",
                "sha256": sha256 or hashlib.sha256(archive).hexdigest(),
                "size": size or len(archive),
            }
        ],
    }
    data = json.dumps(document).encode()
    (target / "manifest.json").write_bytes(data)
    (target / "manifest.json.sig").write_bytes(release.sign_manifest(data, seed))
    return f"{prefix}/manifest.json"


async def run_install(url, cache, **kwargs):
    kwargs.setdefault("public_key", PUBLIC)
    kwargs.setdefault("allow_insecure_loopback", True)
    return await installer.install(manifest_url=url, cache_dir=cache, **kwargs)


def assert_nothing_installed(cache):
    root = cache / "chromium"
    leftovers = [p.name for p in root.iterdir() if p.name != ".install.lock"]
    assert leftovers == []
    assert installer.find_installed(cache_dir=cache) is None


async def test_full_install_against_local_server(server, tmp_path):
    base, root = server
    url = publish(root, base, browser_zip())
    cache = tmp_path / "cache"
    seen = []

    installed = await run_install(url, cache, progress=lambda done, total: seen.append(done))

    assert installed.version == VERSION
    assert installed.platform == PLATFORM
    assert installed.directory == cache / "chromium" / VERSION
    assert installed.executable == installed.directory / EXECUTABLE
    assert installed.executable.read_bytes() == b"binary" * 1000
    assert (installed.directory / "locales" / "en-US.pak").read_bytes() == b"locale"
    record = json.loads((installed.directory / "installed.json").read_text())
    assert record["version"] == VERSION and record["executable"] == EXECUTABLE
    total = len(browser_zip())
    assert seen[0] == 0 and seen[-1] == total and seen == sorted(seen)

    assert installer.find_installed(cache_dir=cache) == installed.executable
    assert installer.find_installed(VERSION, cache_dir=cache) == installed.executable
    assert find_botonomus_chromium(cache_dir=cache) == installed.executable
    assert installer.installed_binaries(cache) == [installed]
    assert [p.name for p in (cache / "chromium").iterdir() if p.name.startswith(".tmp")] == []

    # Already installed: the archive is not downloaded again.
    (root / "browser.zip").unlink()
    again = await run_install(url, cache)
    assert again == installed

    assert installer.uninstall(VERSION, cache_dir=cache) is True
    assert installer.find_installed(cache_dir=cache) is None
    assert installer.uninstall(VERSION, cache_dir=cache) is False


async def test_archive_with_top_level_directory(server, tmp_path):
    base, root = server
    url = publish(root, base, browser_zip(prefix="chrome-dist/"))
    installed = await run_install(url, tmp_path / "cache")
    assert installed.executable == installed.directory / "chrome-dist" / EXECUTABLE
    assert installed.executable.is_file()


async def test_specific_version_uses_versioned_manifest(server, tmp_path):
    base, root = server
    channel_url = publish(root, base, browser_zip(), version="156.0.1.1-bn1")
    publish(root, base, browser_zip(), subdir=VERSION)
    cache = tmp_path / "cache"

    installed = await run_install(channel_url, cache, version=VERSION)
    assert installed.version == VERSION

    # Installed versions are served from disk without touching the network.
    offline = await run_install("http://127.0.0.1:9/manifest.json", cache, version=VERSION)
    assert offline == installed


async def test_versioned_manifest_for_other_version_is_rejected(server, tmp_path):
    base, root = server
    channel_url = f"{base}/manifest.json"
    publish(root, base, browser_zip(), subdir=VERSION, version="156.0.1.1-bn1")
    cache = tmp_path / "cache"
    with pytest.raises(BinaryVerificationError, match="different version"):
        await run_install(channel_url, cache, version=VERSION)
    assert installer.find_installed(cache_dir=cache) is None


async def test_newest_install_is_found(server, tmp_path):
    base, root = server
    cache = tmp_path / "cache"
    for version in ("155.0.8059.26-bn1", "155.0.8059.26-bn10", "155.0.8059.3-bn2"):
        await run_install(publish(root, base, browser_zip(), version=version), cache)
    versions = [item.version for item in installer.installed_binaries(cache)]
    assert versions == ["155.0.8059.26-bn10", "155.0.8059.26-bn1", "155.0.8059.3-bn2"]
    newest = installer.find_installed(cache_dir=cache)
    assert newest == cache / "chromium" / "155.0.8059.26-bn10" / EXECUTABLE


async def test_tampered_manifest_is_rejected(server, tmp_path):
    base, root = server
    url = publish(root, base, browser_zip())
    manifest = root / "manifest.json"
    manifest.write_bytes(manifest.read_bytes().replace(b'"stable"', b'"stablf"'))
    cache = tmp_path / "cache"
    with pytest.raises(BinaryVerificationError, match="signature"):
        await run_install(url, cache)
    assert not (cache / "chromium").exists()


async def test_tampered_signature_is_rejected(server, tmp_path):
    base, root = server
    url = publish(root, base, browser_zip())
    data = (root / "manifest.json").read_bytes()
    (root / "manifest.json.sig").write_bytes(release.sign_manifest(data + b" ", SEED))
    with pytest.raises(BinaryVerificationError, match="signature"):
        await run_install(url, tmp_path / "cache")


async def test_embedded_placeholder_key_rejects_test_signatures(server, tmp_path):
    base, root = server
    url = publish(root, base, browser_zip())
    with pytest.raises(BinaryVerificationError, match="signature"):
        await run_install(url, tmp_path / "cache", public_key=installer.RELEASE_PUBLIC_KEY)


async def test_artifact_hash_mismatch_is_rejected(server, tmp_path):
    base, root = server
    url = publish(root, base, browser_zip(), sha256="00" * 32)
    cache = tmp_path / "cache"
    with pytest.raises(BinaryVerificationError, match="SHA-256"):
        await run_install(url, cache)
    assert_nothing_installed(cache)


async def test_artifact_size_mismatch_is_rejected(server, tmp_path):
    base, root = server
    archive = browser_zip()
    url = publish(root, base, archive, size=len(archive) + 1)
    cache = tmp_path / "cache"
    with pytest.raises(BinaryVerificationError, match="size"):
        await run_install(url, cache)
    assert_nothing_installed(cache)


async def test_http_requires_explicit_loopback_opt_in(server, tmp_path):
    base, root = server
    url = publish(root, base, browser_zip())
    with pytest.raises(BinaryVerificationError, match="https"):
        await run_install(url, tmp_path / "cache", allow_insecure_loopback=False)


async def test_missing_platform_build(server, tmp_path):
    base, root = server
    url = publish(root, base, browser_zip(), platform="solaris-sparc")
    with pytest.raises(BinaryNotInstalledError) as info:
        await run_install(url, tmp_path / "cache")
    assert isinstance(info.value, BrowserUnavailableError)


async def test_download_errors_hide_url_tokens(server, tmp_path):
    base, _ = server
    with pytest.raises(BinaryDownloadError, match="HTTP 404") as info:
        await run_install(f"{base}/missing/manifest.json?token=SECRET", tmp_path / "cache")
    assert "SECRET" not in str(info.value)
    assert "127.0.0.1" not in str(info.value)


async def test_unreachable_server_is_a_download_error(tmp_path, monkeypatch):
    def refuse(request, timeout):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(BinaryDownloadError) as info:
        await run_install("http://127.0.0.1:9/manifest.json?token=SECRET", tmp_path / "cache")
    assert "SECRET" not in str(info.value)
    assert isinstance(info.value.__cause__, urllib.error.URLError)


async def test_archive_without_executable_is_rejected(server, tmp_path):
    base, root = server
    url = publish(root, base, make_zip({"README.txt": b"no browser here"}))
    cache = tmp_path / "cache"
    with pytest.raises(BinaryVerificationError, match="executable"):
        await run_install(url, cache)
    assert_nothing_installed(cache)


async def test_zip_slip_archive_is_rejected_during_install(server, tmp_path):
    base, root = server
    url = publish(root, base, make_zip({EXECUTABLE: b"x", "../escaped.txt": b"evil"}))
    cache = tmp_path / "cache"
    with pytest.raises(BinaryVerificationError, match="unsafe"):
        await run_install(url, cache)
    assert not (cache / "escaped.txt").exists()
    assert not (cache / "chromium" / "escaped.txt").exists()
    assert_nothing_installed(cache)


async def test_interrupted_extraction_leaves_no_partial_install(server, tmp_path, monkeypatch):
    base, root = server
    url = publish(root, base, browser_zip())
    cache = tmp_path / "cache"
    real_copy = installer._copy_member
    calls = []

    def failing_copy(bundle, info, target):
        calls.append(info.filename)
        if len(calls) == 2:
            raise OSError("disk full")
        real_copy(bundle, info, target)

    monkeypatch.setattr(installer, "_copy_member", failing_copy)
    with pytest.raises(OSError, match="disk full"):
        await run_install(url, cache)
    assert len(calls) == 2
    assert_nothing_installed(cache)

    monkeypatch.setattr(installer, "_copy_member", real_copy)
    installed = await run_install(url, cache)
    assert installed.executable.is_file()


async def test_crash_leftovers_are_cleaned_and_recordless_dir_replaced(server, tmp_path):
    base, root = server
    url = publish(root, base, browser_zip())
    chromium = tmp_path / "cache" / "chromium"
    (chromium / ".tmp-deadbeef").mkdir(parents=True)
    (chromium / ".tmp-deadbeef" / "half.bin").write_bytes(b"x")
    (chromium / ".tmp-cafe.zip").write_bytes(b"partial")
    (chromium / VERSION).mkdir()
    (chromium / VERSION / "junk").write_bytes(b"x")
    assert installer.find_installed(cache_dir=tmp_path / "cache") is None

    installed = await run_install(url, tmp_path / "cache")

    assert [p.name for p in chromium.iterdir() if p.name != ".install.lock"] == [VERSION]
    assert not (installed.directory / "junk").exists()


async def test_environment_overrides(server, tmp_path, monkeypatch):
    base, root = server
    url = publish(root, base, browser_zip())
    home = tmp_path / "home"
    monkeypatch.setenv("BOTONOMUS_HOME", str(home))
    monkeypatch.setenv("BOTONOMUS_MANIFEST_URL", url)
    installed = await installer.install(public_key=PUBLIC, allow_insecure_loopback=True)
    assert installed.directory == home / "chromium" / VERSION
    assert installer.find_installed() == installed.executable


def test_home_directory_defaults(monkeypatch, tmp_path):
    monkeypatch.delenv("BOTONOMUS_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert installer.botonomus_home() == tmp_path / "local" / "botonomus"
    monkeypatch.setattr(sys, "platform", "linux")
    assert installer.botonomus_home() == Path.home() / ".cache" / "botonomus"
    monkeypatch.setenv("BOTONOMUS_HOME", str(tmp_path / "override"))
    assert installer.botonomus_home() == tmp_path / "override"


async def test_invalid_versions_are_configuration_errors(tmp_path):
    for bad in ("latest", "../x-bn1", "155.0.8059.26"):
        with pytest.raises(ConfigurationError):
            installer.find_installed(bad, cache_dir=tmp_path)
        with pytest.raises(ConfigurationError):
            installer.uninstall(bad, cache_dir=tmp_path)
        with pytest.raises(ConfigurationError):
            await installer.install(version=bad, cache_dir=tmp_path)


def test_corrupt_records_are_ignored(tmp_path):
    directory = tmp_path / "chromium" / VERSION
    directory.mkdir(parents=True)
    (directory / EXECUTABLE).parent.mkdir(parents=True, exist_ok=True)
    (directory / EXECUTABLE).write_bytes(b"x")
    record = {
        "record_version": 1,
        "version": VERSION,
        "chromium_version": "155.0.8059.26",
        "channel": "stable",
        "platform": PLATFORM,
        "sha256": "00" * 32,
        "installed_at": "2026-10-01T00:00:00+00:00",
        "executable": EXECUTABLE,
    }
    path = directory / "installed.json"
    path.write_text(json.dumps(record))
    assert installer.find_installed(cache_dir=tmp_path) == directory / EXECUTABLE
    for broken in (
        "{",
        json.dumps({**record, "executable": "../../outside"}),
        json.dumps({**record, "version": "155.0.8059.26-bn2"}),
        json.dumps({**record, "record_version": 2}),
        json.dumps({k: v for k, v in record.items() if k != "platform"}),
        json.dumps([]),
    ):
        path.write_text(broken)
        assert installer.find_installed(cache_dir=tmp_path) is None


def test_sibling_url_keeps_query():
    url = "https://h.example/chromium/stable/manifest.json?token=abc#frag"
    assert (
        installer._sibling_url(url, "manifest.json.sig")
        == "https://h.example/chromium/stable/manifest.json.sig?token=abc"
    )
    assert (
        installer._sibling_url(url, f"{VERSION}/manifest.json")
        == f"https://h.example/chromium/stable/{VERSION}/manifest.json?token=abc"
    )


def test_placeholder_public_key_is_a_valid_point():
    assert len(installer.RELEASE_PUBLIC_KEY) == 32
    assert signing._decode(installer.RELEASE_PUBLIC_KEY) is not None


# Safe extraction -------------------------------------------------------------------


def _symlink_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(info, "/etc/passwd")
    return buffer.getvalue()


@pytest.mark.parametrize(
    "archive",
    [
        make_zip({"../evil.txt": b"x"}),
        make_zip({"a/../../evil.txt": b"x"}),
        make_zip({"/abs/evil.txt": b"x"}),
        make_zip({"C:/evil.txt": b"x"}),
        make_zip({"C:evil.txt": b"x"}),
        make_zip({"a\\..\\..\\evil.txt": b"x"}),
        make_zip({"a/./b.txt": b"x"}),
        make_zip({"a//b.txt": b"x"}),
        make_zip({"trailing.": b"x"}),
        make_zip({"file.txt:stream": b"x"}),
        make_zip({"Chrome.exe": b"x", "chrome.exe": b"y"}),
        _symlink_zip(),
    ],
    ids=[
        "parent",
        "nested-parent",
        "absolute",
        "drive",
        "drive-relative",
        "backslash",
        "dot",
        "empty-component",
        "trailing-dot",
        "ads",
        "case-duplicate",
        "symlink",
    ],
)
def test_unsafe_entries_are_rejected_before_writing(tmp_path, archive):
    source = tmp_path / "a.zip"
    source.write_bytes(archive)
    destination = tmp_path / "out" / "inner"
    destination.mkdir(parents=True)
    with pytest.raises(BinaryVerificationError):
        installer.extract_archive(source, destination)
    assert list((tmp_path / "out").rglob("*")) == [destination]
    assert not (tmp_path / "evil.txt").exists()


def test_safe_archive_with_directories_extracts(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("dir/", b"")
        bundle.writestr("dir/sub/file.txt", b"hello")
    source = tmp_path / "a.zip"
    source.write_bytes(buffer.getvalue())
    destination = tmp_path / "out"
    destination.mkdir()
    installer.extract_archive(source, destination)
    assert (destination / "dir" / "sub" / "file.txt").read_bytes() == b"hello"


def test_corrupt_archive_is_rejected(tmp_path):
    source = tmp_path / "a.zip"
    source.write_bytes(b"PK\x03\x04 not really a zip")
    destination = tmp_path / "out"
    destination.mkdir()
    with pytest.raises(BinaryVerificationError, match="corrupt"):
        installer.extract_archive(source, destination)
