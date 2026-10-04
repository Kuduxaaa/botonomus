from botonomus.client.pool import auto_tabs

MB = 1024 * 1024


def test_memory_bounds_tabs():
    assert auto_tabs(available=1800 * MB, cpus=8) == 10


def test_cpu_bounds_tabs():
    assert auto_tabs(available=64_000 * MB, cpus=2) == 8


def test_at_least_one_tab():
    assert auto_tabs(available=100 * MB, cpus=1) == 1
