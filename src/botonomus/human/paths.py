"""Pointer geometry: curved paths, Fitts's-law timing, target points, wheel easing."""

import math
import random
from collections.abc import Mapping, Sequence

Point = tuple[float, float]
FloatRect = Mapping[str, float]


def bezier_path(
    start: Point, end: Point, rng: random.Random, steps: int | None = None
) -> list[Point]:
    """A cubic Bézier path from ``start`` to ``end`` with eased progress.

    Control points bow the curve perpendicular to the straight line by a random,
    slightly asymmetric amount, as a wrist-driven movement does.

    Args:
        start: Starting point in CSS pixels.
        end: Destination; always the exact last point.
        rng: Random source (seed it for reproducible paths).
        steps: Number of points; derived from distance when ``None``.

    Returns:
        Points after ``start``, ending at ``end``.
    """
    (x0, y0), (x3, y3) = start, end
    distance = math.hypot(x3 - x0, y3 - y0)
    if steps is None:
        steps = max(8, min(80, int(distance / 12) + rng.randint(4, 10)))
    nx, ny = (-(y3 - y0) / distance, (x3 - x0) / distance) if distance else (0.0, 0.0)
    bow = distance * rng.uniform(-0.25, 0.25)
    x1 = x0 + (x3 - x0) * rng.uniform(0.2, 0.4) + nx * bow
    y1 = y0 + (y3 - y0) * rng.uniform(0.2, 0.4) + ny * bow
    x2 = x0 + (x3 - x0) * rng.uniform(0.6, 0.8) + nx * bow * rng.uniform(0.3, 0.9)
    y2 = y0 + (y3 - y0) * rng.uniform(0.6, 0.8) + ny * bow * rng.uniform(0.3, 0.9)
    points = []
    for i in range(1, steps + 1):
        t = i / steps
        t = t * t * (3 - 2 * t)  # Smoothstep: slow start and slow approach.
        u = 1 - t
        x = u**3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3
        y = u**3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3
        points.append((x, y))
    points[-1] = (x3, y3)
    return points


def movement_duration(distance: float, width: float, speed: float, rng: random.Random) -> float:
    """Seconds to reach a target, following Fitts's law.

    Args:
        distance: Travel distance in CSS pixels.
        width: Target size along the approach; smaller targets take longer.
        speed: Multiplier; values above 1 are faster.
        rng: Random source for natural variation.
    """
    index = math.log2(distance / max(width, 1.0) + 1)
    return max(0.08, (0.12 + 0.11 * index) / speed * rng.uniform(0.85, 1.2))


def target_point(box: FloatRect, rng: random.Random) -> Point:
    """A point near the centre of ``box``, never on its outer 20% border."""

    def offset(size: float) -> float:
        return max(-0.3, min(0.3, rng.gauss(0, 0.13))) * size

    return (
        box["x"] + box["width"] / 2 + offset(box["width"]),
        box["y"] + box["height"] / 2 + offset(box["height"]),
    )


def eased_weights(count: int) -> Sequence[float]:
    """``count`` positive weights summing to 1, small at both ends (sine easing)."""
    raw = [math.sin(math.pi * (i + 0.5) / count) for i in range(count)]
    total = sum(raw)
    return [value / total for value in raw]
