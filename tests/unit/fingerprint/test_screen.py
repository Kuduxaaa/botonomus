import pytest

from botonomus import BrowserConfig, ConfigurationError
from botonomus.browser import launch_arguments
from botonomus.fingerprint import HostInfo, Persona, resolve_persona
from botonomus.fingerprint.persona import SCREEN_WEIGHTS

FULL_HD = HostInfo(8, 16.0, "win32", screen=(1920, 1080), scale=1.0)
SCALED = HostInfo(8, 16.0, "win32", screen=(1920, 1080), scale=1.25)
PHYSICAL = {size for size, _ in SCREEN_WEIGHTS}


def test_screen_never_exceeds_host():
    for seed in range(300):
        width, height = Persona.from_seed(seed, FULL_HD).screen
        assert width <= 1920 and height <= 1080
        assert (width, height) in PHYSICAL


def test_screen_dips_follow_host_scale():
    seen = set()
    for seed in range(300):
        width, height = Persona.from_seed(seed, SCALED).screen
        physical = (round(width * 1.25), round(height * 1.25))
        assert physical in PHYSICAL, (width, height)
        seen.add((width, height))
    assert (1536, 864) in seen


def test_screen_is_deterministic_and_varies_by_seed():
    assert Persona.from_seed(7, FULL_HD) == Persona.from_seed(7, FULL_HD)
    assert len({Persona.from_seed(seed, FULL_HD).screen for seed in range(200)}) > 2


def test_taskbar_is_a_real_windows_height():
    assert {Persona.from_seed(seed, FULL_HD).taskbar for seed in range(200)} == {40, 48}


def test_switches_include_screen_and_taskbar():
    persona = Persona(seed=1, hardware_concurrency=8, device_memory=8, screen=(1366, 768),
                      taskbar=40)  # fmt: skip
    assert "--bn-screen=1366x768" in persona.to_switches()
    assert "--bn-taskbar=40" in persona.to_switches()


def test_unknown_host_screen_emits_no_switch():
    persona = Persona.from_seed(1, HostInfo(8, 16.0, "linux"))
    assert persona.screen is None
    assert not any(s.startswith(("--bn-screen", "--bn-taskbar")) for s in persona.to_switches())


def test_tiny_host_screen_keeps_the_host_size():
    tiny = HostInfo(8, 16.0, "win32", screen=(1024, 600), scale=1.0)
    assert Persona.from_seed(3, tiny).screen == (1024, 600)


@pytest.mark.parametrize(
    "fields",
    [
        {"screen": (100, 100)},
        {"screen": (9000, 1000)},
        {"screen": (1920,)},
        {"screen": (1920, 1080), "taskbar": 500},
        {"screen": (1920, 1080), "taskbar": -1},
    ],  # fmt: skip
)
def test_invalid_screen_rejected(fields):
    with pytest.raises(ConfigurationError):
        Persona(seed=1, hardware_concurrency=8, device_memory=8, **fields)


def test_explicit_persona_larger_than_host_screen_rejected(tmp_path):
    big = Persona(seed=1, hardware_concurrency=8, device_memory=8, screen=(2560, 1440))
    with pytest.raises(ConfigurationError):
        resolve_persona(big, tmp_path, "p", FULL_HD)


@pytest.mark.parametrize(
    "fields", [{"screen": (0, 1080)}, {"screen": (1920, 1080), "scale": 0}, {"scale": float("nan")}]
)
def test_invalid_host_screen_rejected(fields):
    with pytest.raises(ConfigurationError):
        HostInfo(8, 16.0, "win32", **fields)


def test_window_fits_persona_screen(tmp_path):
    config = BrowserConfig(persona_switches=("--bn-screen=1366x768", "--bn-taskbar=40"))
    args = launch_arguments(tmp_path / "chrome", tmp_path / "p", 9222, config)
    assert args[-3:] == ["--window-position=0,0", "--window-size=1366,728", "about:blank"]


def test_persona_off_has_no_window_flags(tmp_path):
    args = launch_arguments(tmp_path / "chrome", tmp_path / "p", 9222, BrowserConfig())
    assert not any(arg.startswith("--window-") for arg in args)


def test_windows_server_host_presents_a_consumer_platform_version():
    server = HostInfo(8, 16.0, "win32", windows_server=True)
    assert Persona.from_seed(1, server).platform_version == "19.0.0"
    assert "--bn-platform-version=19.0.0" in Persona.from_seed(1, server).to_switches()
    assert Persona.from_seed(1, FULL_HD).platform_version is None


@pytest.mark.parametrize("value", ["19", "19.0", "1.2.3.4", "100.0.0", "a.b.c"])
def test_invalid_platform_version_rejected(value):
    with pytest.raises(ConfigurationError):
        Persona(seed=1, hardware_concurrency=8, device_memory=8, platform_version=value)


def test_seeded_personas_have_noise_off_by_default():
    persona = Persona.from_seed(1, FULL_HD)
    assert persona.noise is False
    assert "--bn-noise=0" in persona.to_switches()


def test_noise_can_be_requested():
    persona = Persona.from_seed(1, FULL_HD, noise=True)
    assert persona.noise is True
    assert not any(s.startswith("--bn-noise") for s in persona.to_switches())
