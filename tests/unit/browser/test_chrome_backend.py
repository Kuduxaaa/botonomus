from pathlib import Path

import pytest

from botonomus.browser import ChromeBackend, is_testing_build
from botonomus.config import BrowserConfig
from botonomus.errors import BrowserUnavailableError


async def test_missing_executable_has_typed_error(tmp_path):
    backend = ChromeBackend()
    await backend.start()
    try:
        with pytest.raises(BrowserUnavailableError):
            await backend.launch(
                tmp_path / "profile", BrowserConfig(executable_path=tmp_path / "absent.exe")
            )
    finally:
        await backend.close()


async def test_backend_close_is_idempotent():
    backend = ChromeBackend()
    await backend.start()
    await backend.close()
    await backend.close()


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (r"C:\Users\x\AppData\Local\ms-playwright\chromium-1243\chrome-win64\chrome.exe", True),
        ("/opt/Google Chrome for Testing.app/Contents/MacOS/x", True),
        (r"C:\Program Files\Google\Chrome\Application\chrome.exe", False),
    ],
)
def test_testing_build_detection(path, expected):
    assert is_testing_build(Path(path)) is expected


def test_launch_arguments_place_platform_args_before_user_args(tmp_path):
    from botonomus.browser import launch_arguments

    config = BrowserConfig(profile_root=tmp_path, extra_args=("--mute-audio",))
    args = launch_arguments(
        Path("chrome"), tmp_path / "p", 9222, config,
        platform_args=("--disable-dev-shm-usage", "--window-size=1920,1080"),
    )  # fmt: skip
    assert args.index("--disable-dev-shm-usage") < args.index("--mute-audio")
    assert "--window-size=1920,1080" in args and args[-1] == "about:blank"


async def test_backend_shares_one_virtual_display_and_stops_it(tmp_path, monkeypatch):
    from botonomus.browser import chrome

    created = []

    class FakeDisplay:
        def __init__(self, **kw):
            self.width, self.height, self.display = 1920, 1080, None
            self.stopped = False
            created.append(self)

        async def start(self):
            self.display = ":99"

        async def stop(self):
            self.stopped = True

    monkeypatch.setattr(chrome, "VirtualDisplay", FakeDisplay)
    monkeypatch.setattr(chrome, "needs_virtual_display", lambda config: True)
    monkeypatch.setattr(chrome, "host_container_flags", lambda: ("--disable-dev-shm-usage",))
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    backend = ChromeBackend()
    await backend.start()
    config = BrowserConfig(profile_root=tmp_path)
    args1, env1 = await backend._platform(config)
    args2, env2 = await backend._platform(config)
    assert len(created) == 1 and env1["DISPLAY"] == env2["DISPLAY"] == ":99"
    assert args1 == (
        "--disable-dev-shm-usage",
        "--ozone-platform=x11",
        "--window-position=0,0",
        "--window-size=1920,1080",
    )
    assert "WAYLAND_DISPLAY" not in env1
    await backend.close()
    assert created[0].stopped


async def test_backend_without_virtual_display_keeps_environment(tmp_path, monkeypatch):
    from botonomus.browser import chrome

    monkeypatch.setattr(chrome, "needs_virtual_display", lambda config: False)
    monkeypatch.setattr(chrome, "host_container_flags", lambda: ())
    backend = ChromeBackend()
    await backend.start()
    assert await backend._platform(BrowserConfig(profile_root=tmp_path)) == ((), None)
    await backend.close()


async def test_display_errors_reach_the_caller_unchanged(tmp_path, monkeypatch):
    from botonomus.browser import chrome
    from botonomus.errors import BrowserStartupError, ConfigurationError

    def refuse(config):
        raise ConfigurationError("virtual_display=True needs Linux with Xvfb")

    monkeypatch.setattr(chrome, "needs_virtual_display", refuse)
    monkeypatch.setattr(chrome, "find_chrome", lambda path: Path("chrome"))
    backend = ChromeBackend()
    await backend.start()
    try:
        with pytest.raises(ConfigurationError, match="Linux"):
            await backend.launch(tmp_path / "p", BrowserConfig(profile_root=tmp_path))

        class Missing:
            def __init__(self, **kw):
                pass

            async def start(self):
                raise BrowserStartupError("Xvfb is not installed (apt-get install xvfb)")

        monkeypatch.setattr(chrome, "needs_virtual_display", lambda config: True)
        monkeypatch.setattr(chrome, "VirtualDisplay", Missing)
        with pytest.raises(BrowserStartupError, match="apt-get install xvfb"):
            await backend.launch(tmp_path / "p", BrowserConfig(profile_root=tmp_path))
    finally:
        await backend.close()


async def test_display_stops_even_when_a_browser_fails_to_close(tmp_path, monkeypatch):
    from botonomus.browser import chrome
    from botonomus.errors import BrowserCleanupError

    stopped = []

    class FakeDisplay:
        def __init__(self, **kw):
            self.width, self.height, self.display = 1920, 1080, ":99"

        async def start(self):
            pass

        async def stop(self):
            stopped.append(True)

    class StuckHandle:
        async def close(self):
            raise BrowserCleanupError("stuck")

    monkeypatch.setattr(chrome, "VirtualDisplay", FakeDisplay)
    monkeypatch.setattr(chrome, "needs_virtual_display", lambda config: True)
    monkeypatch.setattr(chrome, "host_container_flags", lambda: ())
    backend = ChromeBackend()
    await backend.start()
    await backend._platform(BrowserConfig(profile_root=tmp_path))
    backend._handles.add(StuckHandle())
    with pytest.raises(BrowserCleanupError):
        await backend.close()
    assert stopped == [True]
