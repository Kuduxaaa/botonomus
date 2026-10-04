import os

import pytest

from botonomus.browser import display
from botonomus.browser.display import VirtualDisplay, container_flags, needs_virtual_display
from botonomus.config import BrowserConfig
from botonomus.errors import BrowserStartupError, ConfigurationError


def config(tmp_path, **kw):
    return BrowserConfig(profile_root=tmp_path, **kw)


@pytest.mark.parametrize(
    ("platform", "environ", "kw", "expected"),
    [
        ("linux", {}, {}, True),
        ("linux", {"DISPLAY": ":0"}, {}, False),
        ("linux", {"WAYLAND_DISPLAY": "wayland-0"}, {}, False),
        ("linux", {}, {"headless": True}, False),
        ("linux", {"DISPLAY": ":0"}, {"virtual_display": True}, True),
        ("linux", {}, {"virtual_display": False}, False),
        ("win32", {}, {}, False),
        ("darwin", {}, {}, False),
    ],
)
def test_needs_virtual_display(tmp_path, platform, environ, kw, expected):
    assert needs_virtual_display(config(tmp_path, **kw), platform, environ) is expected


def test_virtual_display_forced_off_linux_is_an_error(tmp_path):
    with pytest.raises(ConfigurationError, match="Linux"):
        needs_virtual_display(config(tmp_path, virtual_display=True), "win32", {})


def test_virtual_display_must_be_bool_or_none(tmp_path):
    with pytest.raises(ConfigurationError, match="virtual_display"):
        config(tmp_path, virtual_display="yes")


@pytest.mark.parametrize(
    ("platform", "shm", "euid", "expected"),
    [
        ("linux", 64 * 2**20, 1000, ("--disable-dev-shm-usage",)),
        ("linux", 8 * 2**30, 1000, ()),
        ("linux", 8 * 2**30, 0, ("--no-sandbox",)),
        ("linux", None, 0, ("--no-sandbox",)),
        ("win32", 64 * 2**20, 0, ()),
    ],
)
def test_container_flags(platform, shm, euid, expected):
    assert container_flags(platform, shm, euid) == expected


class FakeProcess:
    def __init__(self, write_fd: int | None, number: bytes | None):
        self.returncode = None
        self.terminated = False
        if write_fd is not None and number is not None:
            os.write(write_fd, number)

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.terminate()

    async def wait(self):
        return self.returncode


def fake_xvfb(launched, number=b"101\n"):
    async def spawn(*argv, pass_fds=(), **kw):
        launched.append(argv)
        fd = int(argv[argv.index("-displayfd") + 1])
        assert fd in pass_fds
        return FakeProcess(fd, number)

    return spawn


async def test_virtual_display_lets_xvfb_choose_the_number(monkeypatch):
    launched = []
    monkeypatch.setattr(display.asyncio, "create_subprocess_exec", fake_xvfb(launched))
    virtual = VirtualDisplay(width=1366, height=768)
    await virtual.start()
    assert virtual.display == ":101"
    argv = launched[0]
    assert argv[0] == "Xvfb" and "-displayfd" in argv
    assert argv[argv.index("-screen") + 2] == "1366x768x24"
    assert argv[argv.index("-nolisten") + 1] == "tcp"
    process = virtual._process
    await virtual.stop()
    assert process.terminated and virtual.display is None


async def test_virtual_display_failure_is_a_startup_error(monkeypatch):
    launched = []
    monkeypatch.setattr(display.asyncio, "create_subprocess_exec", fake_xvfb(launched, number=b""))
    with pytest.raises(BrowserStartupError, match="Xvfb"):
        await VirtualDisplay(start_timeout=2).start()


async def test_virtual_display_start_timeout_kills_xvfb(monkeypatch):
    processes = []

    async def spawn(*argv, pass_fds=(), **kw):
        processes.append(FakeProcess(None, None))  # never reports a display number
        return processes[-1]

    monkeypatch.setattr(display.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(BrowserStartupError, match="Xvfb"):
        await VirtualDisplay(start_timeout=0.2).start()
    assert processes[0].terminated


async def test_missing_xvfb_is_a_clear_error(monkeypatch):
    async def spawn(*argv, **kw):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(display.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(BrowserStartupError, match="apt-get install xvfb"):
        await VirtualDisplay().start()


def test_headless_never_uses_a_virtual_display(tmp_path):
    cfg = config(tmp_path, headless=True, virtual_display=True)
    assert needs_virtual_display(cfg, "linux", {}) is False
