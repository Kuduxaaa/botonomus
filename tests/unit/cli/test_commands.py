"""``install``, ``uninstall``, ``binaries``, ``profiles warmup`` and the browser options."""

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from botonomus import Botonomus
from botonomus.browser import BUILD_MARKER, InstalledBinary
from botonomus.cli import binaries as binaries_command
from botonomus.cli import browse as browse_command
from botonomus.cli import build_parser, main
from botonomus.cli import info as info_command
from botonomus.cli import profiles as profiles_command
from botonomus.cli.common import browser_config, humanize_setting, persona_spec
from botonomus.core import identity
from botonomus.errors import (
    BinaryDownloadError,
    BinaryNotInstalledError,
    BinaryVerificationError,
    ConfigurationError,
)
from botonomus.human import Human, HumanConfig
from botonomus.profiles import DEFAULT_SITES, WarmupReport

from ..core.fakes import FakeBackend

SECRET_URL = "https://releases.example/chromium/manifest.json?token=s3cr3t"


def binary(tmp_path: Path, version: str = "155.0.8059.26-bn1") -> InstalledBinary:
    directory = tmp_path / "chromium" / version
    return InstalledBinary(
        version=version,
        chromium_version=version.split("-")[0],
        channel="stable",
        platform="win-x64",
        sha256="ab" * 32,
        installed_at=datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
        directory=directory,
        executable=directory / "chrome.exe",
    )


# --- browser options ---------------------------------------------------------------


def test_browser_options_build_the_config(tmp_path):
    args = build_parser().parse_args(
        [
            "open",
            "--browser",
            "chrome",
            "--persona",
            "42",
            "--proxy",
            "http://h.example:8080",
            "--geoip",
            "--timezone",
            "Europe/Berlin",
            "--allow-timezone-mismatch",
            "--humanize",
            "careful",
        ]  # fmt: skip
    )
    config = browser_config(args, tmp_path)
    assert config.browser == "chrome"
    assert config.persona == 42
    assert config.geoip is True
    assert config.timezone == "Europe/Berlin"
    assert config.allow_timezone_mismatch is True
    assert config.humanize == HumanConfig.preset("careful")


def test_browser_option_defaults_match_browser_config(tmp_path):
    for command in (["open"], ["detect"], ["probe"], ["benchmark"], ["profiles", "warmup", "p"]):
        config = browser_config(build_parser().parse_args(command), tmp_path)
        assert (config.browser, config.persona, config.geoip, config.timezone) == (
            "auto",
            "auto",
            False,
            None,
        )
        assert (config.allow_timezone_mismatch, config.humanize) == (False, False)


@pytest.mark.parametrize(
    ("value", "expected"), [("auto", "auto"), ("off", "off"), ("0", 0), (str(2**64 - 1), 2**64 - 1)]
)
def test_persona_values(value, expected):
    assert persona_spec(value) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("off", False),
        ("default", HumanConfig.preset("default")),
        ("careful", HumanConfig.preset("careful")),
        ("fast", HumanConfig.preset("fast")),
    ],
)
def test_humanize_presets(name, expected):
    assert humanize_setting(name) == expected


def test_unknown_humanize_name_is_configuration_error():
    with pytest.raises(ConfigurationError):
        humanize_setting("slow")


@pytest.mark.parametrize(
    "argv",
    [
        ["open", "--browser", "firefox"],
        ["open", "--persona", "seed"],
        ["open", "--persona", "-1"],
        ["open", "--persona", str(2**64)],
        ["open", "--humanize", "slow"],
        ["detect", "--humanize", "turbo"],
        ["probe", "--humanize", "default"],  # probe never acts on session.page
        ["benchmark", "--humanize", "default"],
        ["profiles", "warmup", "p", "--duration", "0"],
        ["profiles", "warmup", "p", "--duration", "x"],
        ["profiles", "warmup", "p", "--sites", "ftp://example.com/"],
        ["profiles", "warmup", "p", "--sites", "example.com"],
        ["profiles", "warmup"],
        ["uninstall"],
    ],
)
def test_new_usage_errors_exit_2(argv, capsys):
    assert main(argv) == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    ("argv", "word"),
    [
        (["open", "--geoip"], "geoip"),
        (["open", "--timezone", "not a zone!"], "timezone"),
        (["detect", "--geoip", "--output", "unused"], "geoip"),
        (["probe", "--timezone", "Bad Zone"], "timezone"),
        (["profiles", "warmup", "p", "--geoip"], "geoip"),
    ],
)
def test_invalid_browser_configuration_exits_2(argv, word, capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert main(argv) == 2
    assert word in capsys.readouterr().err


def test_open_passes_the_built_config(monkeypatch, tmp_path):
    seen = {}

    async def browse(config, profile, url):
        seen.update(config=config, profile=profile)

    monkeypatch.setattr(browse_command, "browse", browse)
    argv = ["open", "--root", str(tmp_path), "--profile", "p", "--persona", "off",
            "--humanize", "fast", "--browser", "botonomus"]  # fmt: skip
    assert main(argv) == 0
    assert seen["profile"] == "p"
    config = seen["config"]
    assert (config.persona, config.browser, config.humanize) == (
        "off",
        "botonomus",
        HumanConfig.preset("fast"),
    )


# --- install / uninstall / binaries --------------------------------------------------


@pytest.fixture
def fake_install(monkeypatch, tmp_path):
    calls = []

    async def install(*, version=None, manifest_url=None, progress=None):
        calls.append({"version": version, "manifest_url": manifest_url})
        if progress is not None:
            for received in (0, 25_000_000, 50_000_000, 100_000_000):
                progress(received, 100_000_000)
        return binary(tmp_path, version or "155.0.8059.26-bn1")

    monkeypatch.setattr(binaries_command.installer, "install", install)
    return calls


def test_install_prints_version_executable_and_progress(fake_install, capsys, tmp_path):
    assert main(["install"]) == 0
    captured = capsys.readouterr()
    assert "155.0.8059.26-bn1" in captured.out
    assert str(tmp_path / "chromium" / "155.0.8059.26-bn1" / "chrome.exe") in captured.out
    assert "100%" in captured.err and "100.0/100.0 MB" in captured.err
    assert " 25%" in captured.err and captured.err.endswith("\n")
    assert fake_install == [{"version": None, "manifest_url": None}]


def test_install_json_shape_and_arguments(fake_install, capsys, tmp_path):
    argv = ["install", "--version", "155.0.8059.27-bn2", "--manifest-url", SECRET_URL, "--json"]
    assert main(argv) == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "version": "155.0.8059.27-bn2",
        "chromium_version": "155.0.8059.27",
        "channel": "stable",
        "platform": "win-x64",
        "sha256": "ab" * 32,
        "installed_at": "2026-10-01T12:00:00+00:00",
        "directory": str(tmp_path / "chromium" / "155.0.8059.27-bn2"),
        "executable": str(tmp_path / "chromium" / "155.0.8059.27-bn2" / "chrome.exe"),
    }
    assert fake_install == [{"version": "155.0.8059.27-bn2", "manifest_url": SECRET_URL}]


@pytest.mark.parametrize(
    ("exc", "word"),
    [
        (BinaryDownloadError("Could not download release manifest (HTTP 404)"), "retry"),
        (BinaryVerificationError("Release manifest signature verification failed"), "nothing"),
        (BinaryNotInstalledError("Release 1.0.0.0-bn1 has no mac-arm64 build"), "mac-arm64"),
    ],
)
def test_install_errors_exit_1_without_urls(exc, word, monkeypatch, capsys):
    async def install(**kwargs):
        progress = kwargs["progress"]
        progress(0, 1000)
        progress(500, 1000)
        try:
            raise OSError(f"connection to {SECRET_URL} failed")
        except OSError as cause:
            raise exc from cause

    monkeypatch.setattr(binaries_command.installer, "install", install)
    assert main(["install", "--manifest-url", SECRET_URL]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "botonomus: error:" in captured.err and word in captured.err
    assert "s3cr3t" not in captured.err and "releases.example" not in captured.err
    # The bar's line is ended before the error message starts.
    assert "\nbotonomus: error:" in captured.err


def test_install_invalid_version_is_usage_error(capsys):
    assert main(["install", "--version", "not-a-version"]) == 2
    assert "version" in capsys.readouterr().err.lower()


def test_uninstall(monkeypatch, capsys):
    removed = []

    def uninstall(version):
        removed.append(version)
        return version == "155.0.8059.26-bn1"

    monkeypatch.setattr(binaries_command.installer, "uninstall", uninstall)
    assert main(["uninstall", "155.0.8059.26-bn1"]) == 0
    assert "removed 155.0.8059.26-bn1" in capsys.readouterr().out
    assert main(["uninstall", "155.0.8059.27-bn1"]) == 1
    assert "not installed" in capsys.readouterr().err
    assert removed == ["155.0.8059.26-bn1", "155.0.8059.27-bn1"]


def test_uninstall_rejects_malformed_version_and_reports_busy_files(monkeypatch, tmp_path):
    monkeypatch.setenv("BOTONOMUS_HOME", str(tmp_path))
    assert main(["uninstall", "../escape"]) == 2

    def busy(version):
        raise PermissionError("in use")

    monkeypatch.setattr(binaries_command.installer, "uninstall", busy)
    assert main(["uninstall", "155.0.8059.26-bn1"]) == 1


def test_binaries_json_and_text(monkeypatch, capsys, tmp_path):
    builds = [binary(tmp_path, "155.0.8059.27-bn1"), binary(tmp_path, "155.0.8059.26-bn1")]
    monkeypatch.setattr(binaries_command.installer, "installed_binaries", lambda: builds)
    assert main(["binaries", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [item["version"] for item in data] == ["155.0.8059.27-bn1", "155.0.8059.26-bn1"]
    assert set(data[0]) == {
        "version",
        "chromium_version",
        "channel",
        "platform",
        "sha256",
        "installed_at",
        "directory",
        "executable",
    }
    assert main(["binaries"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2 and out[0].startswith("155.0.8059.27-bn1")


def test_binaries_empty(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("BOTONOMUS_HOME", str(tmp_path / "none"))
    assert main(["binaries", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert main(["binaries"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and "botonomus install" in captured.err


def test_progress_bar_redraws_only_on_percent_change():
    stream = io.StringIO()
    bar = binaries_command.ProgressBar(stream)
    bar(0, 3_000_000)
    bar(1, 3_000_000)  # still 0%
    bar(1_500_000, 3_000_000)
    assert stream.getvalue().count("\r") == 2
    assert stream.getvalue().endswith(" 50%  1.5/3.0 MB")
    bar(3_000_000, 3_000_000)
    assert stream.getvalue().endswith("100%  3.0/3.0 MB\n")
    bar.finish()  # idempotent
    assert stream.getvalue().count("\n") == 1
    empty = io.StringIO()
    binaries_command.ProgressBar(empty)(0, 0)
    assert "100%" in empty.getvalue()


# --- profiles warmup -----------------------------------------------------------------


@pytest.fixture
def fake_warmup(monkeypatch):
    backend = FakeBackend()
    calls = []

    async def warm_up(page, *, sites, duration, human):
        calls.append({"page": page, "sites": list(sites), "duration": duration, "human": human})
        return WarmupReport(sites_visited=2, sites_failed=1, links_followed=3, elapsed=12.5)

    monkeypatch.setattr(
        profiles_command, "Botonomus", lambda config: Botonomus(config=config, backend=backend)
    )
    monkeypatch.setattr(profiles_command, "warm_up", warm_up)
    return backend, calls


def test_profiles_warmup_text(fake_warmup, capsys, tmp_path):
    backend, calls = fake_warmup
    assert main(["profiles", "warmup", "alice", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "sites visited   2" in out and "links followed  3" in out and "12.5s" in out
    (call,) = calls
    assert call["page"] is backend.handles[0].page
    assert call["sites"] == list(DEFAULT_SITES)
    assert call["duration"] == profiles_command.DEFAULT_WARMUP_SECONDS
    assert isinstance(call["human"], Human)
    assert call["human"].config == HumanConfig.preset("default")
    assert (tmp_path / "alice").is_dir()


def test_profiles_warmup_json_sites_and_humanize(fake_warmup, capsys, tmp_path):
    backend, calls = fake_warmup
    argv = ["profiles", "warmup", "bob", "--root", str(tmp_path), "--duration", "30",
            "--sites", "https://a.example/", "http://b.example/x", "https://a.example/",
            "--humanize", "careful", "--json"]  # fmt: skip
    assert main(argv) == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {"sites_visited": 2, "sites_failed": 1, "links_followed": 3, "elapsed": 12.5}
    (call,) = calls
    assert call["sites"] == ["https://a.example/", "http://b.example/x"]
    assert call["duration"] == 30.0
    assert call["page"] is backend.handles[0].page  # the raw page, not the HumanPage
    assert call["human"].config == HumanConfig.preset("careful")
    assert backend.configs[0].humanize == HumanConfig.preset("careful")


def test_profiles_warmup_invalid_profile_name_is_usage_error(fake_warmup, tmp_path):
    assert main(["profiles", "warmup", "../escape", "--root", str(tmp_path)]) == 2


# --- info ----------------------------------------------------------------------------


def test_info_reports_build_and_what_auto_launches(monkeypatch, capsys, tmp_path):
    build = tmp_path / "build" / "chrome.exe"
    build.parent.mkdir()
    build.write_bytes(b"MZ")
    (build.parent / BUILD_MARKER).write_text("{}", encoding="utf-8")
    chrome = tmp_path / "chrome" / "chrome.exe"
    chrome.parent.mkdir()
    chrome.write_bytes(b"MZ")
    monkeypatch.setattr(info_command, "find_botonomus_chromium", lambda: build)
    monkeypatch.setattr(info_command, "find_chrome", lambda explicit=None: explicit or chrome)
    monkeypatch.setattr(identity, "find_botonomus_chromium", lambda: build)
    monkeypatch.setattr(identity, "find_chrome", lambda explicit=None: explicit or chrome)
    monkeypatch.setattr(info_command.installer, "installed_binaries", lambda: [])

    assert main(["info", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["botonomus_chromium"]["path"] == str(build)
    assert data["default"]["path"] == str(build)
    assert data["default"]["botonomus_build"] is True
    assert data["default"]["browser"] == "auto"

    assert main(["info", "--json", "--browser", "chrome"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["default"]["path"], data["default"]["botonomus_build"]) == (str(chrome), False)

    assert main(["info"]) == 0
    out = capsys.readouterr().out
    assert f"build      {build}" in out
    assert f"default    {build} [Botonomus Chromium, browser=auto]" in out

    monkeypatch.setattr(identity, "find_botonomus_chromium", lambda: None)
    monkeypatch.setattr(info_command, "find_botonomus_chromium", lambda: None)
    assert main(["info", "--browser", "botonomus"]) == 0
    out = capsys.readouterr().out
    assert "no Botonomus Chromium build installed" in out
    assert "default    none: browser=botonomus" in out
