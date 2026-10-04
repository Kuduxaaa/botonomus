import hashlib
import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from botonomus.errors import ConfigurationError
from botonomus.fingerprint import persona_seed
from botonomus.fingerprint import seed as seed_module


def key_path(root):
    return root / ".botonomus" / "persona.key"


def write_key(root, key):
    key_path(root).parent.mkdir(parents=True)
    key_path(root).write_bytes(key)


def test_seed_is_stable_and_creates_key(tmp_path):
    first = persona_seed(tmp_path, "acct-01")
    assert key_path(tmp_path).stat().st_size == 32
    assert persona_seed(tmp_path, "acct-01") == first
    assert 0 <= first < 2**64


def test_profile_names_are_normalized_like_leases(tmp_path):
    assert persona_seed(tmp_path, "Acct-01") == persona_seed(tmp_path, "acct-01")


def test_profiles_get_different_seeds(tmp_path):
    seeds = {persona_seed(tmp_path, f"p{index}") for index in range(200)}
    assert len(seeds) == 200


def test_seed_differs_across_installations(tmp_path):
    assert persona_seed(tmp_path / "a", "acct") != persona_seed(tmp_path / "b", "acct")


def test_deleting_key_changes_identity(tmp_path):
    before = persona_seed(tmp_path, "acct")
    key_path(tmp_path).unlink()
    assert persona_seed(tmp_path, "acct") != before


def test_seed_is_keyed_blake2b_of_the_name(tmp_path):
    key = bytes(range(32))
    write_key(tmp_path, key)
    expected = hashlib.blake2b(b"acct", digest_size=8, key=key, person=b"bn-profile-seed")
    assert persona_seed(tmp_path, "ACCT") == int.from_bytes(expected.digest(), "big")


@pytest.mark.parametrize("profile", ["", "../x", "a/b", "é", "-lead", "x" * 65, None, 3])
def test_invalid_profile_rejected(tmp_path, profile):
    with pytest.raises(ConfigurationError):
        persona_seed(tmp_path, profile)
    assert not key_path(tmp_path).exists()


@pytest.mark.parametrize("size", [0, 31, 33, 64])
def test_corrupt_key_rejected(tmp_path, monkeypatch, size):
    monkeypatch.setattr(seed_module, "_PARTIAL_WAIT", 0.05)
    write_key(tmp_path, b"k" * size)
    with pytest.raises(ConfigurationError):
        persona_seed(tmp_path, "acct")


def test_partially_written_key_is_awaited(tmp_path):
    write_key(tmp_path, b"k" * 10)
    timer = threading.Timer(0.1, key_path(tmp_path).write_bytes, args=(b"k" * 32,))
    timer.start()
    try:
        expected = hashlib.blake2b(b"acct", digest_size=8, key=b"k" * 32, person=b"bn-profile-seed")
        assert persona_seed(tmp_path, "acct") == int.from_bytes(expected.digest(), "big")
    finally:
        timer.join()


def test_concurrent_threads_agree_on_one_key(tmp_path):
    barrier = threading.Barrier(16)

    def derive(_):
        barrier.wait()
        return persona_seed(tmp_path, "acct")

    with ThreadPoolExecutor(16) as pool:
        seeds = set(pool.map(derive, range(16)))
    assert len(seeds) == 1
    assert [p.name for p in key_path(tmp_path).parent.iterdir()] == ["persona.key"]


def test_fallback_without_hard_links(tmp_path, monkeypatch):
    def no_links(*args):
        raise OSError("hard links unsupported")

    monkeypatch.setattr(seed_module.os, "link", no_links)
    first = persona_seed(tmp_path, "acct")
    assert persona_seed(tmp_path, "acct") == first
    assert [p.name for p in key_path(tmp_path).parent.iterdir()] == ["persona.key"]


def test_fallback_creators_agree_on_one_key(tmp_path, monkeypatch):
    def no_links(*args):
        raise OSError("hard links unsupported")

    monkeypatch.setattr(seed_module.os, "link", no_links)
    barrier = threading.Barrier(8)

    def derive(_):
        barrier.wait()
        return persona_seed(tmp_path, "acct")

    with ThreadPoolExecutor(8) as pool:
        assert len(set(pool.map(derive, range(8)))) == 1


def derive_in_process(root, start, results):
    start.wait()
    results.put(persona_seed(root, "acct"))


def test_concurrent_processes_agree_on_one_key(tmp_path):
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(target=derive_in_process, args=(tmp_path, start, results)) for _ in range(4)
    ]
    for process in processes:
        process.start()
    start.set()
    seeds = {results.get(timeout=60) for _ in processes}
    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0
    assert len(seeds) == 1
    assert seeds == {persona_seed(tmp_path, "acct")}
