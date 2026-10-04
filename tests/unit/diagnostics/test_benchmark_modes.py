import pytest

from botonomus.diagnostics import benchmark


def test_per_instance_memory_divides_growth_by_active():
    report = {"active": 4, "baseline_host_used_bytes": 1000, "peak_host_used_bytes": 1800}
    assert benchmark.per_instance_bytes(report) == 200
    assert benchmark.per_instance_bytes({**report, "active": 0}) is None


async def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        await benchmark.measure_level(1, None, "http://x", mode="tabs")
