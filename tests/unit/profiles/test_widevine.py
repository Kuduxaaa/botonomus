import json

from botonomus.profiles.widevine import seed_widevine


def fake_chrome(tmp_path, version="4.10.3112.0"):
    application = tmp_path / "Chrome" / "Application"
    cdm = application / "154.0.8037.98" / "WidevineCdm"
    (cdm / "_platform_specific" / "win_x64").mkdir(parents=True)
    (cdm / "_platform_specific" / "win_x64" / "widevinecdm.dll").write_bytes(b"dll")
    (cdm / "manifest.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    exe = application / "chrome.exe"
    exe.write_bytes(b"")
    return exe


def test_copies_the_cdm_into_the_profile(tmp_path):
    chrome = fake_chrome(tmp_path)
    profile = tmp_path / "profile"
    assert seed_widevine(profile, [chrome])
    copied = profile / "WidevineCdm" / "4.10.3112.0"
    assert (copied / "manifest.json").is_file()
    assert (copied / "_platform_specific" / "win_x64" / "widevinecdm.dll").read_bytes() == b"dll"


def test_existing_cdm_is_left_alone(tmp_path):
    chrome = fake_chrome(tmp_path)
    profile = tmp_path / "profile"
    (profile / "WidevineCdm" / "1.0").mkdir(parents=True)
    assert not seed_widevine(profile, [chrome])
    assert not (profile / "WidevineCdm" / "4.10.3112.0").exists()


def test_no_chrome_means_nothing_to_copy(tmp_path):
    assert not seed_widevine(tmp_path / "profile", [tmp_path / "missing" / "chrome.exe"])
    assert not (tmp_path / "profile").exists()


def test_bad_manifest_is_skipped(tmp_path):
    chrome = fake_chrome(tmp_path, version="../escape")
    assert not seed_widevine(tmp_path / "profile", [chrome])
