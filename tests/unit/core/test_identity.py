import datetime

import pytest

from botonomus import BrowserConfig
from botonomus.core import identity as identity_module
from botonomus.core.identity import IdentityResolver
from botonomus.errors import (
    BinaryNotInstalledError,
    ConfigurationError,
    GeoLookupError,
    GeoMismatchError,
    PersonaUnsupportedError,
)
from botonomus.fingerprint import HostInfo, Persona
from botonomus.network import ExitInfo, UpstreamError

HOST = HostInfo(logical_cpus=16, memory_gb=32.0, platform="win32")


def exit_in(timezone, country="DE"):
    return ExitInfo(
        ip="203.0.113.7",
        country_code=country,
        region="",
        city="",
        timezone=timezone,
        isp="",
        hosting=False,
        flagged_proxy=False,
        latency=0.1,
    )


class FakeExits:
    def __init__(self, info=None, error=None):
        self.info, self.error, self.calls = info, error, 0

    async def get(self, spec):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.info


@pytest.fixture
def chrome(tmp_path, monkeypatch):
    executable = tmp_path / "chrome" / "chrome.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"")
    monkeypatch.setattr(
        identity_module, "find_chrome", lambda explicit=None: explicit or executable
    )
    monkeypatch.setattr(identity_module, "find_botonomus_chromium", lambda: None)
    monkeypatch.setattr(identity_module.HostInfo, "detect", staticmethod(lambda: HOST))
    return executable


@pytest.fixture
def botonomus_build(tmp_path, monkeypatch, chrome):
    executable = tmp_path / "bn" / "chrome.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"")
    (executable.parent / "botonomus-build.json").write_text("{}")
    monkeypatch.setattr(identity_module, "find_botonomus_chromium", lambda: executable)
    return executable


def host_zone_is(monkeypatch, zone):
    import zoneinfo

    offset = datetime.datetime.now(zoneinfo.ZoneInfo(zone)).utcoffset()
    monkeypatch.setattr(identity_module, "_host_utc_offset", lambda: offset)


async def test_chrome_auto_persona_adds_no_switches(chrome, tmp_path):
    config = BrowserConfig(profile_root=tmp_path / "p")
    result = await IdentityResolver().resolve(config, "acct")
    assert result.executable == chrome
    assert not result.botonomus_build
    assert result.persona is None
    assert result.config.persona_switches == ()


async def test_chrome_rejects_explicit_persona(chrome, tmp_path):
    config = BrowserConfig(profile_root=tmp_path / "p", persona=1234)
    with pytest.raises(PersonaUnsupportedError):
        await IdentityResolver().resolve(config, "acct")


async def test_botonomus_build_gets_stable_persona_switches(botonomus_build, tmp_path):
    config = BrowserConfig(profile_root=tmp_path / "p")
    first = await IdentityResolver().resolve(config, "acct")
    second = await IdentityResolver().resolve(config, "acct")
    other = await IdentityResolver().resolve(config, "other")
    assert first.botonomus_build and first.executable == botonomus_build
    assert first.persona == second.persona
    assert first.persona != other.persona
    assert first.config.persona_switches == first.persona.to_switches()


async def test_preferred_botonomus_build_missing_raises(chrome, tmp_path):
    config = BrowserConfig(profile_root=tmp_path / "p", browser="botonomus")
    with pytest.raises(BinaryNotInstalledError):
        await IdentityResolver().resolve(config, "acct")


async def test_browser_chrome_ignores_installed_build(botonomus_build, chrome, tmp_path):
    config = BrowserConfig(profile_root=tmp_path / "p", browser="chrome")
    result = await IdentityResolver().resolve(config, "acct")
    assert result.executable == chrome


async def test_geoip_sets_locale_and_persona_timezone(botonomus_build, tmp_path):
    exits = FakeExits(exit_in("Europe/Berlin"))
    config = BrowserConfig(profile_root=tmp_path / "p", proxy="http://h:1", geoip=True)
    result = await IdentityResolver(exits).resolve(config, "acct")
    assert result.config.locale == "de-DE"
    assert result.persona.timezone == "Europe/Berlin"
    assert "--bn-timezone=Europe/Berlin" in result.config.persona_switches
    assert result.exit.country_code == "DE"


async def test_explicit_locale_and_timezone_win_over_exit(botonomus_build, tmp_path):
    exits = FakeExits(exit_in("Europe/Berlin"))
    config = BrowserConfig(
        profile_root=tmp_path / "p",
        proxy="http://h:1",
        geoip=True,
        locale="en-GB",
        timezone="Europe/London",
    )
    result = await IdentityResolver(exits).resolve(config, "acct")
    assert result.config.locale == "en-GB"
    assert result.persona.timezone == "Europe/London"


async def test_persona_off_on_botonomus_build_still_sets_timezone(botonomus_build, tmp_path):
    exits = FakeExits(exit_in("Asia/Tokyo", "JP"))
    config = BrowserConfig(
        profile_root=tmp_path / "p", proxy="http://h:1", geoip=True, persona="off"
    )
    result = await IdentityResolver(exits).resolve(config, "acct")
    assert result.persona is None
    assert result.config.persona_switches == ("--bn-timezone=Asia/Tokyo",)


async def test_geo_lookup_failure_is_typed(chrome, tmp_path):
    exits = FakeExits(error=UpstreamError("down"))
    config = BrowserConfig(profile_root=tmp_path / "p", proxy="http://h:1", geoip=True)
    with pytest.raises(GeoLookupError):
        await IdentityResolver(exits).resolve(config, "acct")


async def test_chrome_timezone_mismatch_is_refused(chrome, tmp_path, monkeypatch):
    host_zone_is(monkeypatch, "Asia/Tokyo")
    exits = FakeExits(exit_in("America/New_York", "US"))
    config = BrowserConfig(profile_root=tmp_path / "p", proxy="http://h:1", geoip=True)
    with pytest.raises(GeoMismatchError):
        await IdentityResolver(exits).resolve(config, "acct")


async def test_chrome_timezone_mismatch_can_be_allowed(chrome, tmp_path, monkeypatch):
    host_zone_is(monkeypatch, "Asia/Tokyo")
    exits = FakeExits(exit_in("America/New_York", "US"))
    config = BrowserConfig(
        profile_root=tmp_path / "p",
        proxy="http://h:1",
        geoip=True,
        allow_timezone_mismatch=True,
    )
    result = await IdentityResolver(exits).resolve(config, "acct")
    assert result.config.locale == "en-US"


async def test_chrome_matching_offset_is_accepted(chrome, tmp_path, monkeypatch):
    host_zone_is(monkeypatch, "Europe/Paris")
    exits = FakeExits(exit_in("Europe/Berlin"))
    config = BrowserConfig(profile_root=tmp_path / "p", proxy="http://h:1", geoip=True)
    result = await IdentityResolver(exits).resolve(config, "acct")
    assert result.config.locale == "de-DE"


async def test_explicit_persona_object_is_used(botonomus_build, tmp_path):
    persona = Persona.from_seed(7, HOST)
    config = BrowserConfig(profile_root=tmp_path / "p", persona=persona)
    result = await IdentityResolver().resolve(config, "acct")
    assert result.persona == persona


@pytest.mark.parametrize(
    "kwargs",
    [
        {"browser": "firefox"},
        {"persona": "random"},
        {"persona": -1},
        {"persona": True},
        {"geoip": True},
        {"timezone": "not a zone"},
        {"humanize": "yes"},
        {"extra_args": ("--bn-seed=00",)},
    ],
)
def test_invalid_identity_configuration(kwargs):
    with pytest.raises(ConfigurationError):
        BrowserConfig(**kwargs)
