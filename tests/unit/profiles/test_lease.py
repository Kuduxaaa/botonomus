import multiprocessing
from pathlib import Path

import pytest

from botonomus.config import BrowserConfig
from botonomus.errors import ConfigurationError, ProfileInUseError
from botonomus.profiles import ProfileLease


def hold_profile(root, connection):
    lease = ProfileLease(Path(root), "shared")
    lease.acquire()
    connection.send("locked")
    connection.recv()
    lease.release()


@pytest.mark.parametrize("name", ["../escape", "a/b", "a\\b", "CON", "LPT1", "x.", "", "é"])
def test_unsafe_profile_rejected(tmp_path, name):
    with pytest.raises(ConfigurationError):
        ProfileLease(tmp_path, name).acquire()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_invalid_timeout_rejected(tmp_path, timeout):
    with pytest.raises(ConfigurationError):
        BrowserConfig(profile_root=tmp_path, launch_timeout=timeout)


def test_profile_aliases_conflict_and_release_preserves_data(tmp_path):
    first = ProfileLease(tmp_path, "Shared")
    path = first.acquire()
    (path / "state.txt").write_text("saved")
    second = ProfileLease(tmp_path, "shared")
    with pytest.raises(ProfileInUseError):
        second.acquire()
    first.release()
    first.release()
    assert (second.acquire() / "state.txt").read_text() == "saved"
    second.release()


def test_profile_lock_is_cross_process(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=hold_profile, args=(str(tmp_path), child))
    process.start()
    try:
        assert parent.poll(15), "child failed to acquire profile"
        assert parent.recv() == "locked"
        with pytest.raises(ProfileInUseError):
            ProfileLease(tmp_path, "SHARED").acquire()
        parent.send("release")
        process.join(10)
        assert process.exitcode == 0
        lease = ProfileLease(tmp_path, "shared")
        lease.acquire()
        lease.release()
    finally:
        if process.is_alive():
            process.terminate()
            process.join()
        parent.close()
        child.close()


def test_profile_root_cannot_be_file(tmp_path):
    path = tmp_path / "file"
    path.write_text("existing")
    with pytest.raises(ConfigurationError):
        ProfileLease(path, "sample").acquire()
