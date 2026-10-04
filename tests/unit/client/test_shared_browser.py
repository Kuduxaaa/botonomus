from pathlib import Path

from botonomus import BrowserConfig
from botonomus.client.browser import WEBRTC_POLICY, SharedBrowser


def test_throwaway_profile_lives_outside_the_user_profile_root(tmp_path):
    shared = SharedBrowser(BrowserConfig(profile_root=tmp_path / "profiles"))
    root = Path(shared.config.profile_root)
    assert not root.is_relative_to(tmp_path / "profiles")
    assert root.name.startswith("botonomus-client-")
    assert WEBRTC_POLICY in shared.config.extra_args
