import json

from botonomus.profiles.preferences import WEBRTC_POLICY, ensure_webrtc_policy


def read(profile):
    return json.loads((profile / "Default" / "Preferences").read_text(encoding="utf-8"))


def test_new_profile_gets_the_policy(tmp_path):
    ensure_webrtc_policy(tmp_path)
    assert read(tmp_path)["webrtc"]["ip_handling_policy"] == WEBRTC_POLICY


def test_existing_preferences_are_kept(tmp_path):
    prefs = tmp_path / "Default" / "Preferences"
    prefs.parent.mkdir()
    prefs.write_text(json.dumps({"webrtc": {"other": 1}, "intl": {"x": "y"}}), encoding="utf-8")
    ensure_webrtc_policy(tmp_path)
    data = read(tmp_path)
    assert data["webrtc"] == {"other": 1, "ip_handling_policy": WEBRTC_POLICY}
    assert data["intl"] == {"x": "y"}


def test_unreadable_preferences_are_replaced_by_a_minimal_file(tmp_path):
    prefs = tmp_path / "Default" / "Preferences"
    prefs.parent.mkdir()
    prefs.write_text("{broken", encoding="utf-8")
    ensure_webrtc_policy(tmp_path)
    assert read(tmp_path) == {"webrtc": {"ip_handling_policy": WEBRTC_POLICY}}
