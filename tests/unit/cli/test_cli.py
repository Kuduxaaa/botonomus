import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from botonomus import Botonomus, __version__
from botonomus.cli import build_parser, main
from botonomus.cli import detect as detect_command
from botonomus.cli import info as info_command
from botonomus.cli import proxies as proxy_command
from botonomus.diagnostics import SITES, detection
from botonomus.errors import BrowserUnavailableError
from botonomus.network import ExitInfo
from botonomus.profiles import ProfileLease

from ..diagnostics.fakes import FakePage


def run_json(capsys, argv):
    code = main(argv)
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["nope"],
        ["detect", "--runs", "0"],
        ["detect", "--runs", "x"],
        ["detect", "--sites", "not-a-site"],
        ["detect", "--settle", "-1"],
        ["detect", "--driver", "selenium"],
        ["proxy-check"],
        ["proxy-check", "f.txt", "--parallel", "0"],
        ["proxy-check", "f.txt", "--timeout", "nan"],
        ["profiles"],
        ["profiles", "remove"],
        ["benchmark", "--levels", "1,0"],
        ["probe", "--normal", "--baseline", "x.json"],
    ],
)
def test_usage_errors_exit_2(argv, capsys):
    assert main(argv) == 2
    assert capsys.readouterr().out == ""


def test_proxy_argument_error_never_echoes_credentials(capsys):
    assert main(["open", "--proxy", "ftp://user:topsecret@host:1"]) == 2
    captured = capsys.readouterr()
    assert "topsecret" not in captured.err + captured.out


def test_help_and_version_exit_0(capsys):
    assert main(["--help"]) == 0
    assert main(["--version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_parser_defaults():
    args = build_parser().parse_args(["detect"])
    assert args.sites == list(SITES)
    assert (args.runs, args.parallel, args.driver, args.json) == (1, 2, "native", False)
    args = build_parser().parse_args(["benchmark", "--levels", "1,3"])
    assert (args.levels, args.mode) == ([1, 3], "sessions")
    args = build_parser().parse_args(["benchmark", "--mode", "contexts"])
    assert args.mode == "contexts"
    args = build_parser().parse_args(["open", "--profile", "p", "--locale", "de-DE"])
    assert (args.profile, args.locale, args.url) == ("p", "de-DE", "about:blank")


def test_invalid_configuration_is_usage_error(capsys):
    assert main(["open", "--locale", "not a locale!"]) == 2
    assert "locale" in capsys.readouterr().err


def fake_install(tmp_path, name="Application"):
    folder = tmp_path / name
    (folder / "131.0.6778.86").mkdir(parents=True)
    (folder / "99.0.1.2").mkdir()
    executable = folder / "chrome.exe"
    executable.write_bytes(b"MZ")
    return executable


def test_info_json_shape(tmp_path, capsys, monkeypatch):
    monkeypatch.delitem(sys.modules, "botonomus.browser.install", raising=False)
    executable = fake_install(tmp_path)
    code, data = run_json(capsys, ["info", "--json", "--executable", str(executable)])
    assert code == 0
    assert set(data) == {
        "botonomus",
        "python",
        "platform",
        "chrome",
        "botonomus_chromium",
        "default",
        "drivers",
        "installed_binaries",
    }
    assert data["botonomus"] == __version__
    assert data["chrome"] == {
        "path": str(executable),
        "version": "131.0.6778.86",
        "testing_build": False,
    }
    assert data["drivers"]["native"] is True
    assert isinstance(data["installed_binaries"], list)
    assert data["default"] == {
        "browser": "auto",
        "path": str(executable),
        "version": "131.0.6778.86",
        "botonomus_build": False,
    }


def test_info_without_chrome_lists_installed_binaries(capsys, monkeypatch, tmp_path):
    def missing(explicit=None):
        raise BrowserUnavailableError("none")

    @dataclass
    class Binary:
        version: str
        chromium_version: str
        executable: Path

    build = Binary("155.0.8059.26-bn1", "155.0.8059.26", tmp_path / "chrome.exe")
    monkeypatch.setattr(info_command.installer, "installed_binaries", lambda: [build])
    monkeypatch.setattr(info_command, "find_chrome", missing)
    monkeypatch.setattr(info_command, "find_botonomus_chromium", lambda: None)
    monkeypatch.setattr(info_command, "resolve_executable", lambda config: missing())
    code, data = run_json(capsys, ["info", "--json"])
    assert code == 0
    assert data["chrome"] is None
    assert data["botonomus_chromium"] is None
    assert data["default"] is None
    assert data["installed_binaries"] == [
        {
            "version": "155.0.8059.26-bn1",
            "chromium_version": "155.0.8059.26",
            "executable": str(tmp_path / "chrome.exe"),
        }
    ]
    assert main(["info"]) == 0
    out = capsys.readouterr().out
    assert "not found" in out
    assert "155.0.8059.26-bn1" in out


def test_info_survives_unreadable_install_directory(monkeypatch):
    def broken():
        raise OSError("x")

    monkeypatch.setattr(info_command.installer, "installed_binaries", broken)
    assert info_command.installed_binaries() is None


def test_profiles_list_and_remove(tmp_path, capsys):
    root = tmp_path / "profiles"
    for name in ("alpha", "beta"):
        (root / name).mkdir(parents=True)
    (root / ".locks").mkdir()
    held = ProfileLease(root, "beta")
    held.acquire()
    try:
        code, data = run_json(capsys, ["profiles", "list", "--root", str(root), "--json"])
        assert code == 0
        assert [(p["name"], p["in_use"]) for p in data] == [("alpha", False), ("beta", True)]
        assert set(data[0]) == {"name", "path", "in_use", "modified"}

        assert main(["profiles", "remove", "beta", "--root", str(root)]) == 1
        assert "in use" in capsys.readouterr().err
        assert (root / "beta").is_dir()
    finally:
        held.release()

    assert main(["profiles", "remove", "beta", "--root", str(root)]) == 0
    assert not (root / "beta").exists()
    assert main(["profiles", "remove", "missing", "--root", str(root)]) == 2
    assert main(["profiles", "remove", "../escape", "--root", str(root)]) == 2
    assert main(["profiles", "list", "--root", str(root)]) == 0
    assert "alpha" in capsys.readouterr().out


def test_profiles_list_empty_root(tmp_path, capsys):
    code, data = run_json(capsys, ["profiles", "list", "--root", str(tmp_path / "x"), "--json"])
    assert (code, data) == (0, [])


@pytest.fixture
def proxy_file(tmp_path):
    path = tmp_path / "proxies.txt"
    path.write_text(
        "# comment\nhttp://alice:hunter2@good.example:8080\n\nsocks5://bad.example:1080\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def fake_exit(monkeypatch):
    async def exit_info(spec, timeout):
        if spec.host == "bad.example":
            raise ConnectionRefusedError("refused")
        return ExitInfo("203.0.113.5", "DE", "Berlin", "Berlin", "Europe/Berlin", "ISP", False,
                        False, 0.25)  # fmt: skip

    monkeypatch.delattr("botonomus.network.check_proxies", raising=False)
    monkeypatch.setattr(proxy_command, "exit_info", exit_info)


def test_proxy_check_json_shape_without_credentials(proxy_file, fake_exit, capsys, tmp_path):
    output = tmp_path / "out" / "proxies.json"
    code = main(["proxy-check", str(proxy_file), "--json", "--output", str(output)])
    text = capsys.readouterr().out
    assert code == 0
    good, bad = json.loads(text)
    assert good["proxy"] == "good.example:8080" and good["scheme"] == "http"
    assert good["ok"] is True and good["country_code"] == "DE" and good["ip"] == "203.0.113.5"
    assert bad == {
        "proxy": "bad.example:1080",
        "scheme": "socks5",
        "ok": False,
        "error": "ConnectionRefusedError",
    }
    for blob in (text, output.read_text(encoding="utf-8")):
        assert "hunter2" not in blob and "alice" not in blob


def test_proxy_check_text_output(proxy_file, fake_exit, capsys):
    assert main(["proxy-check", str(proxy_file)]) == 0
    out = capsys.readouterr().out
    assert "1/2 working" in out and "FAILED ConnectionRefusedError" in out
    assert "hunter2" not in out


def test_proxy_check_invalid_file_entry_is_usage_error(tmp_path, capsys):
    path = tmp_path / "p.txt"
    path.write_text("http://user:pw@host\n", encoding="utf-8")
    assert main(["proxy-check", str(path)]) == 2
    assert "pw" not in capsys.readouterr().err.replace("pass", "")


def test_proxy_check_missing_file_is_runtime_failure(tmp_path):
    assert main(["proxy-check", str(tmp_path / "none.txt")]) == 1


def test_proxy_check_prefers_shared_checker_and_redacts(proxy_file, monkeypatch, capsys):
    async def check_proxies(proxies, *, parallel, timeout):
        return [{"proxy": proxies[0], "ok": True, "password": "hunter2"}]

    monkeypatch.setattr("botonomus.network.check_proxies", check_proxies, raising=False)
    code, data = run_json(capsys, ["proxy-check", str(proxy_file), "--json"])
    assert (code, data) == (0, [{"proxy": "good.example:8080", "ok": True}])


def test_proxy_check_flattens_shared_proxy_check_results(proxy_file, monkeypatch, capsys):
    from botonomus.network import ProxyCheck

    info = ExitInfo("203.0.113.5", "DE", "Berlin", "Berlin", "Europe/Berlin", "ISP", False,
                    False, 0.25)  # fmt: skip

    async def check_proxies(proxies, *, parallel, timeout):
        return [
            ProxyCheck("good.example:8080", True, None, info),
            ProxyCheck("bad.example:1080", False, "unreachable", None),
        ]

    monkeypatch.setattr("botonomus.network.check_proxies", check_proxies)
    code, (good, bad) = run_json(capsys, ["proxy-check", str(proxy_file), "--json"])
    assert code == 0
    assert good["proxy"] == "good.example:8080" and good["scheme"] == "http"
    assert (
        good["ok"] is True and good["ip"] == "203.0.113.5" and good["timezone"] == "Europe/Berlin"
    )
    assert bad == {
        "proxy": "bad.example:1080",
        "scheme": "socks5",
        "ok": False,
        "error": "unreachable",
    }

    assert main(["proxy-check", str(proxy_file)]) == 0
    out = capsys.readouterr().out
    assert "http://good.example:8080" in out and "203.0.113.5" in out and "?://?" not in out


@pytest.fixture
def fake_detect(monkeypatch):
    from ..core.fakes import FakeBackend

    pages = []

    class Backend(FakeBackend):
        async def launch(self, profile_path, config):
            handle = await super().launch(profile_path, config)
            handle.page = FakePage({"verdict": "pass", "details": {"ok": 1}})
            pages.append(handle.page)
            return handle

    def missing(config):
        raise BrowserUnavailableError("none")

    monkeypatch.setattr(
        detect_command,
        "Botonomus",
        lambda limit, config: Botonomus(limit, config=config, backend=Backend()),
    )
    monkeypatch.setattr(detection, "resolve_executable", missing)
    return pages


def test_detect_json_shape(tmp_path, fake_detect, capsys):
    output = tmp_path / "report"
    argv = ["detect", "--sites", "sannysoft", "browserscan", "--runs", "2", "--settle", "0",
            "--no-interact", "--output", str(output), "--json"]  # fmt: skip
    code, data = run_json(capsys, argv)
    assert code == 0
    assert set(data) == {"environment", "sites", "runs", "limitation"}
    assert set(data["environment"]) == {
        "captured_at",
        "os",
        "python",
        "botonomus_version",
        "driver",
        "headless",
        "browser_product",
        "executable_name",
        "executable_sha256",
        "proxy_type",
        "botonomus_build",
    }
    sanny, scan = data["sites"]
    assert (sanny["site"], sanny["runs"], sanny["passed"], sanny["pass_rate"]) == (
        "sannysoft",
        2,
        2,
        1.0,
    )
    assert (scan["site"], scan["passed"], scan["blocked"]) == ("browserscan", 2, 0)
    assert len(data["runs"]) == 4
    assert set(data["runs"][0]) == {
        "site",
        "run",
        "profile",
        "verdict",
        "details",
        "duration",
        "error",
        "error_type",
        "screenshot",
        "text_excerpt",
    }
    assert (output / "report.json").is_file()
    assert len(fake_detect) == 4


def test_detect_text_output(tmp_path, fake_detect, capsys):
    argv = ["detect", "--sites", "nowsecure", "--settle", "0", "--no-interact",
            "--output", str(tmp_path)]  # fmt: skip
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "nowsecure" in out and "100%" in out and "not proof" in out
