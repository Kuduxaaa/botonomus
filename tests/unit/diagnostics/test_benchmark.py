import pytest

from botonomus.diagnostics import parse_levels, should_stop


@pytest.mark.parametrize("value", ["", "0", "-1", "1,2,0", "1.5", "a"])
def test_invalid_levels_rejected(value):
    with pytest.raises(ValueError):
        parse_levels(value)


def test_levels_support_requested_scale():
    assert parse_levels("1,10,20,40,80") == [1, 10, 20, 40, 80]


def test_pressure_or_repeated_failures_stop_launching():
    assert should_stop(14, 100, 0)
    assert should_stop(80, 100, 2)
    assert not should_stop(80, 100, 1)
