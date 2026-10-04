import pytest

from botonomus.errors import ConfigurationError, ProfileInUseError
from botonomus.profiles import ProfileLease, list_profiles, remove_profile


def test_list_skips_non_profile_entries(tmp_path):
    (tmp_path / "one").mkdir()
    (tmp_path / ".locks").mkdir()
    (tmp_path / "not a profile").mkdir()
    (tmp_path / "file.txt").write_text("x")
    profiles = list_profiles(tmp_path)
    assert [p.name for p in profiles] == ["one"]
    assert profiles[0].path == tmp_path.resolve() / "one"
    assert profiles[0].in_use is False


def test_list_does_not_leave_profiles_locked(tmp_path):
    (tmp_path / "one").mkdir()
    list_profiles(tmp_path)
    lease = ProfileLease(tmp_path, "one")
    lease.acquire()
    lease.release()


def test_remove_refuses_locked_profile(tmp_path):
    (tmp_path / "busy" / "Default").mkdir(parents=True)
    holder = ProfileLease(tmp_path, "busy")
    holder.acquire()
    try:
        with pytest.raises(ProfileInUseError):
            remove_profile(tmp_path, "busy")
        assert (tmp_path / "busy" / "Default").is_dir()
    finally:
        holder.release()
    assert remove_profile(tmp_path, "busy") == tmp_path.resolve() / "busy"
    assert not (tmp_path / "busy").exists()


def test_remove_missing_or_invalid(tmp_path):
    with pytest.raises(ConfigurationError):
        remove_profile(tmp_path, "missing")
    with pytest.raises(ConfigurationError):
        remove_profile(tmp_path, "../x")
