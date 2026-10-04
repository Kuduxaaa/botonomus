"""Small statistics for repeated detection runs."""

import math


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for a binomial proportion.

    Args:
        successes: Successful trials.
        n: Trials.
        z: Normal quantile; 1.96 gives a 95 % interval.

    Returns:
        ``(low, high)`` rounded to four places, or ``None`` when ``n`` is 0.
    """
    if n == 0:
        return None
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)
