import math
import random

from botonomus.human import (
    HumanProfile,
    bezier_path,
    key_delays,
    movement_duration,
    target_point,
)


def test_bezier_path_ends_exactly_on_target_and_is_deterministic():
    first = bezier_path((10, 10), (400, 300), random.Random(1))
    second = bezier_path((10, 10), (400, 300), random.Random(1))
    assert first == second
    assert first[-1] == (400, 300)
    assert 8 <= len(first) <= 80


def test_bezier_path_has_no_teleporting_steps():
    path = [(0.0, 0.0), *bezier_path((0, 0), (800, 600), random.Random(3))]
    longest = max(math.dist(a, b) for a, b in zip(path, path[1:], strict=False))
    assert longest < 1000 / 4


def test_bezier_path_zero_distance():
    assert bezier_path((5, 5), (5, 5), random.Random(0))[-1] == (5, 5)


def test_target_point_stays_inside_inner_box():
    rng = random.Random(0)
    box = {"x": 100.0, "y": 50.0, "width": 80.0, "height": 20.0}
    for _ in range(500):
        x, y = target_point(box, rng)
        assert 100 + 80 * 0.2 <= x <= 100 + 80 * 0.8
        assert 50 + 20 * 0.2 <= y <= 50 + 20 * 0.8


def test_duration_grows_with_difficulty():
    small = movement_duration(800, 10, 1.0, random.Random(0))
    large = movement_duration(80, 200, 1.0, random.Random(0))
    assert small > large >= 0.08


def test_key_delays_are_bounded_and_varied():
    delays = key_delays("hello world, again", HumanProfile(), random.Random(2))
    assert len(delays) == len("hello world, again")
    assert all(0 < d <= 1.5 for d in delays)
    assert len(set(delays)) > 5
