from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import botonomus.client.browser as browser_module
from botonomus import BrowserConfig, ProfileInUseError
from botonomus.client.browser import WEBRTC_POLICY, SharedBrowser


def test_client_profiles_persist_next_to_the_profile_root(tmp_path):
    shared = SharedBrowser(BrowserConfig(profile_root=tmp_path / "profiles"))
    assert shared.config.profile_root == (tmp_path / "client-browsers").resolve()
    assert WEBRTC_POLICY in shared.config.extra_args


class FakeConnection:
    def __init__(self):
        self.closed = SimpleNamespace(is_set=lambda: False)

    async def send(self, method, params=None):
        return {"product": "Chrome/155.0.8059.26"}


class FakeManager:
    busy = {"client-0"}
    opened: list[str] = []
    closed = 0

    def __init__(self, limit, *, config):
        self.config = config

    async def __aenter__(self):
        return self

    async def close(self):
        FakeManager.closed += 1

    @asynccontextmanager
    async def open(self, *, profile):
        if profile in self.busy:
            raise ProfileInUseError("in use")
        FakeManager.opened.append(profile)
        yield SimpleNamespace(context=SimpleNamespace(connection=FakeConnection()))


async def test_launch_takes_the_first_free_client_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_module, "Botonomus", FakeManager)
    shared = SharedBrowser(BrowserConfig(profile_root=tmp_path / "profiles"))
    await shared.connection()
    assert FakeManager.opened == ["client-1"]
    assert shared.major == 155
    await shared.close()


async def test_all_client_profiles_busy_is_a_clear_error(tmp_path, monkeypatch):
    class AllBusy(FakeManager):
        busy = {f"client-{n}" for n in range(64)}

    monkeypatch.setattr(browser_module, "Botonomus", AllBusy)
    shared = SharedBrowser(BrowserConfig(profile_root=tmp_path / "profiles"))
    with pytest.raises(ProfileInUseError):
        await shared.connection()
