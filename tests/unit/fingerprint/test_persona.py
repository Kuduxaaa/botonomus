import dataclasses
from collections import Counter

import pytest

from botonomus.errors import ConfigurationError
from botonomus.fingerprint import (
    DEVICE_MEMORY_WEIGHTS,
    GPU_TABLE,
    HARDWARE_CONCURRENCY_WEIGHTS,
    HostInfo,
    Persona,
    persona_seed,
    resolve_persona,
    switches,
)
from botonomus.fingerprint import persona as persona_module

BIG = HostInfo(logical_cpus=64, memory_gb=128.0, platform="win32")
GPU = GPU_TABLE["nvidia"][0]


@pytest.fixture
def no_tz_database(monkeypatch):
    monkeypatch.setattr(persona_module, "_tz_database_available", lambda: False)


@pytest.fixture
def tz_database(monkeypatch):
    known = {"UTC", "Europe/Berlin", "America/Argentina/Buenos_Aires", "Etc/GMT+5"}

    def zone_info(name):
        if name not in known:
            raise persona_module.zoneinfo.ZoneInfoNotFoundError(name)

    monkeypatch.setattr(persona_module.zoneinfo, "ZoneInfo", zone_info)
    monkeypatch.setattr(persona_module, "_tz_database_available", lambda: True)


def test_from_seed_is_deterministic():
    assert Persona.from_seed(42, BIG) == Persona.from_seed(42, BIG)
    personas = {Persona.from_seed(seed, BIG) for seed in range(200)}
    assert len({(p.hardware_concurrency, p.device_memory) for p in personas}) > 10


def test_known_vectors_pin_the_derivation():
    # Changing these values changes every existing profile's identity.
    observed = [
        (p.hardware_concurrency, p.device_memory)
        for p in (Persona.from_seed(seed, BIG) for seed in (0, 1, 0xDEADBEEF, 2**64 - 1))
    ]
    assert observed == KNOWN_VECTORS


def shares(values):
    counts = Counter(values)
    return {value: count / len(values) for value, count in counts.items()}


def expected_shares(weights, cap):
    allowed = [(value, weight) for value, weight in weights if value <= cap]
    total = sum(weight for _, weight in allowed)
    return {value: weight / total for value, weight in allowed}


@pytest.mark.parametrize(
    "host",
    [BIG, HostInfo(12, 15.8, "win32"), HostInfo(8, 7.9, "win32")],
)
def test_distribution_follows_weights_within_host_caps(host):
    personas = [Persona.from_seed(seed, host) for seed in range(20_000)]
    for observed, expected in (
        (
            shares([p.hardware_concurrency for p in personas]),
            expected_shares(HARDWARE_CONCURRENCY_WEIGHTS, host.logical_cpus),
        ),
        (
            shares([p.device_memory for p in personas]),
            expected_shares(DEVICE_MEMORY_WEIGHTS, host.device_memory),
        ),
    ):
        assert set(observed) == set(expected)
        for value, share in expected.items():
            assert observed[value] == pytest.approx(share, abs=0.015)


def test_hardware_and_memory_draws_are_independent():
    pairs = Counter(
        (p.hardware_concurrency, p.device_memory)
        for p in (Persona.from_seed(seed, BIG) for seed in range(20_000))
    )
    concurrency = expected_shares(HARDWARE_CONCURRENCY_WEIGHTS, 64)
    memory = expected_shares(DEVICE_MEMORY_WEIGHTS, 32)
    for (cores, gigabytes), count in pairs.items():
        assert count / 20_000 == pytest.approx(concurrency[cores] * memory[gigabytes], abs=0.01)


@pytest.mark.parametrize(
    ("cpus", "memory_gb", "max_cores", "max_memory"),
    [(64, 128.0, 24, 32), (16, 31.7, 16, 32), (6, 15.8, 6, 16), (4, 7.9, 4, 8), (5, 3.9, 4, 4)],
)
def test_host_caps_respected(cpus, memory_gb, max_cores, max_memory):
    host = HostInfo(cpus, memory_gb, "win32")
    personas = [Persona.from_seed(seed, host) for seed in range(2_000)]
    assert max(p.hardware_concurrency for p in personas) == max_cores
    assert max(p.device_memory for p in personas) == max_memory
    assert all(p.hardware_concurrency <= cpus for p in personas)


def test_host_below_every_candidate_reports_itself():
    host = HostInfo(logical_cpus=2, memory_gb=1.5, platform="win32")
    persona = Persona.from_seed(7, host)
    assert (persona.hardware_concurrency, persona.device_memory) == (2, 2)


def test_capping_one_value_does_not_shift_the_other():
    small_cpu = HostInfo(4, 128.0, "win32")
    for seed in range(500):
        assert Persona.from_seed(seed, small_cpu).device_memory == (
            Persona.from_seed(seed, BIG).device_memory
        )


def test_switches_are_sorted_and_minimal():
    persona = Persona(seed=0xAB, hardware_concurrency=8, device_memory=16)
    assert persona.to_switches() == (
        "--bn-device-memory=16",
        "--bn-hardware-concurrency=8",
        "--bn-seed=00000000000000ab",
    )


def test_switches_with_every_option(no_tz_database):
    persona = Persona(
        seed=2**64 - 1,
        hardware_concurrency=12,
        device_memory=32,
        timezone="Europe/Berlin",
        gpu=GPU,
        noise=False,
        screen=(1536, 864),
        taskbar=40,
    )
    rendered = persona.to_switches()
    assert rendered == tuple(sorted(rendered))
    assert set(rendered) == {
        "--bn-device-memory=32",
        "--bn-hardware-concurrency=12",
        "--bn-seed=ffffffffffffffff",
        "--bn-timezone=Europe/Berlin",
        f"--bn-gpu-vendor={GPU.vendor}",
        f"--bn-gpu-renderer={GPU.renderer}",
        "--bn-noise=0",
        "--bn-screen=1536x864",
        "--bn-taskbar=40",
    }
    assert {item.split("=", 1)[0] for item in rendered} == switches.PERSONA_SWITCHES


def test_seed_switch_is_sixteen_lowercase_hex_digits():
    for seed in (0, 1, 0xDEADBEEF, 2**63, 2**64 - 1):
        (value,) = [
            item.split("=", 1)[1]
            for item in Persona.from_seed(seed, BIG).to_switches()
            if item.startswith(switches.SEED + "=")
        ]
        assert len(value) == 16
        assert value == value.lower()
        assert int(value, 16) == seed


@pytest.mark.parametrize("seed", [-1, 2**64, True, 1.0, "1", None])
def test_invalid_seed_rejected(seed):
    with pytest.raises(ConfigurationError):
        Persona.from_seed(seed, BIG)
    with pytest.raises(ConfigurationError):
        Persona(seed=seed, hardware_concurrency=4, device_memory=4)


@pytest.mark.parametrize("value", [0, -4, 1025, True, 4.0, "4"])
def test_invalid_hardware_concurrency_rejected(value):
    with pytest.raises(ConfigurationError):
        Persona(seed=1, hardware_concurrency=value, device_memory=8)


@pytest.mark.parametrize("value", [0, 1, 3, 6, 12, 64, True, 8.0, "8"])
def test_invalid_device_memory_rejected(value):
    with pytest.raises(ConfigurationError):
        Persona(seed=1, hardware_concurrency=4, device_memory=value)


@pytest.mark.parametrize("value", [2, 4, 8, 16, 32])
def test_every_chromium_device_memory_value_accepted(value):
    Persona(seed=1, hardware_concurrency=4, device_memory=value)


def test_gpu_override_is_type_checked():
    with pytest.raises(ConfigurationError):
        Persona(seed=1, hardware_concurrency=4, device_memory=8, gpu=("v", "r"))
    with pytest.raises(ConfigurationError):
        Persona(seed=1, hardware_concurrency=4, device_memory=8, noise=0)


def test_invalid_host_rejected():
    with pytest.raises(ConfigurationError):
        Persona.from_seed(1, object())


@pytest.mark.parametrize(
    "zone",
    [
        "",
        "/Europe/Berlin",
        "Europe/",
        "Europe//Berlin",
        "../etc/passwd",
        "Europe/Berlin\n",
        "Europe Berlin",
        "Europe/Berlin=x",
        "x" * 65,
        3,
    ],
)
def test_malformed_timezone_rejected(no_tz_database, zone):
    with pytest.raises(ConfigurationError):
        Persona.from_seed(1, BIG, timezone=zone)


@pytest.mark.parametrize(
    "zone", ["UTC", "Europe/Berlin", "America/Argentina/Buenos_Aires", "Etc/GMT+5"]
)
def test_known_timezone_accepted(tz_database, zone):
    assert Persona.from_seed(1, BIG, timezone=zone).timezone == zone


def test_unknown_timezone_rejected_when_database_present(tz_database):
    with pytest.raises(ConfigurationError):
        Persona.from_seed(1, BIG, timezone="Mars/Olympus_Mons")


def test_unknown_timezone_syntax_checked_only_without_database(no_tz_database):
    assert Persona.from_seed(1, BIG, timezone="Mars/Olympus_Mons").timezone == "Mars/Olympus_Mons"


def test_persona_is_frozen():
    persona = Persona.from_seed(1, BIG)
    with pytest.raises(dataclasses.FrozenInstanceError):
        persona.seed = 2


def test_gpu_override_is_carried():
    assert Persona.from_seed(1, BIG, gpu=GPU).gpu == GPU


def test_resolve_off_returns_none(tmp_path):
    assert resolve_persona("off", tmp_path, "acct", BIG) is None
    assert not (tmp_path / ".botonomus").exists()


def test_resolve_auto_seeds_from_profile(tmp_path, no_tz_database):
    persona = resolve_persona("auto", tmp_path, "acct", BIG, timezone="Europe/Berlin")
    assert persona == Persona.from_seed(
        persona_seed(tmp_path, "acct"), BIG, timezone="Europe/Berlin"
    )
    assert resolve_persona("auto", tmp_path, "other", BIG) != persona


def test_resolve_int_uses_seed(tmp_path):
    assert resolve_persona(99, tmp_path, "acct", BIG) == Persona.from_seed(99, BIG)


def test_resolve_explicit_persona(tmp_path, no_tz_database):
    explicit = Persona(seed=5, hardware_concurrency=4, device_memory=8)
    assert resolve_persona(explicit, tmp_path, "acct", BIG) is explicit
    filled = resolve_persona(explicit, tmp_path, "acct", BIG, timezone="UTC")
    assert filled == dataclasses.replace(explicit, timezone="UTC")
    zoned = dataclasses.replace(explicit, timezone="Europe/Berlin")
    assert resolve_persona(zoned, tmp_path, "acct", BIG, timezone="UTC") is zoned


@pytest.mark.parametrize(
    "persona",
    [
        Persona(seed=5, hardware_concurrency=8, device_memory=4),
        Persona(seed=5, hardware_concurrency=4, device_memory=16),
    ],
)
def test_resolve_explicit_persona_beyond_host_rejected(tmp_path, persona):
    host = HostInfo(logical_cpus=4, memory_gb=7.9, platform="win32")
    with pytest.raises(ConfigurationError):
        resolve_persona(persona, tmp_path, "acct", host)


@pytest.mark.parametrize("spec", ["on", "AUTO", "", True, False, -1, 2**64, 1.5, None])
def test_resolve_invalid_spec_rejected(tmp_path, spec):
    with pytest.raises(ConfigurationError):
        resolve_persona(spec, tmp_path, "acct", BIG)


def test_resolve_auto_invalid_profile_rejected(tmp_path):
    with pytest.raises(ConfigurationError):
        resolve_persona("auto", tmp_path, "../escape", BIG)


KNOWN_VECTORS = [(12, 8), (12, 16), (24, 16), (8, 32)]
