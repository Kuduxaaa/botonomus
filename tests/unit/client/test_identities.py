import json
import os
import stat

import pytest

from botonomus import ConfigurationError, ProfileInUseError
from botonomus.client.identities import IdentityStore, StorageState


def test_new_identity_starts_empty(tmp_path):
    store = IdentityStore(tmp_path, "acct-01")
    with store:
        assert store.load() == StorageState()


def test_state_round_trips(tmp_path):
    state = StorageState(
        cookies=[{"name": "sid", "value": "1", "domain": "example.com", "path": "/"}],
        local_storage={"https://example.com": {"theme": "dark"}},
        proxy="http://u:p@proxy.example:8080",
    )
    with IdentityStore(tmp_path, "Acct-01") as store:
        store.save(state)
    with IdentityStore(tmp_path, "acct-01") as store:
        assert store.load() == state
    saved = json.loads((tmp_path / "acct-01" / "state.json").read_text(encoding="utf-8"))
    assert saved["version"] == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_state_file_is_private(tmp_path):
    with IdentityStore(tmp_path, "acct") as store:
        store.save(StorageState())
    mode = stat.S_IMODE((tmp_path / "acct" / "state.json").stat().st_mode)
    assert mode == 0o600


def test_identity_is_exclusive(tmp_path):
    with IdentityStore(tmp_path, "acct"):
        with pytest.raises(ProfileInUseError):
            IdentityStore(tmp_path, "acct").__enter__()


def test_invalid_names_rejected(tmp_path):
    with pytest.raises(ConfigurationError):
        IdentityStore(tmp_path, "../escape")


def test_corrupt_state_is_a_clear_error(tmp_path):
    with IdentityStore(tmp_path, "acct") as store:
        (tmp_path / "acct" / "state.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="acct"):
            store.load()


@pytest.mark.skipif(os.name != "nt", reason="Windows ACLs")
def test_state_directory_is_private_on_windows(tmp_path):
    import getpass
    import subprocess

    with IdentityStore(tmp_path, "acct") as store:
        store.save(StorageState())
    listing = subprocess.run(
        ["icacls", str(tmp_path / "acct" / "state.json")], capture_output=True, text=True
    ).stdout
    entries = [line for line in listing.splitlines() if ":(" in line]
    assert len(entries) == 1, listing
    assert getpass.getuser().lower() in entries[0].lower(), listing
    for broad in ("everyone", "users", "authenticated", "system", "administrators"):
        assert f"\{broad}:" not in listing.lower() and f" {broad}:" not in listing.lower()
