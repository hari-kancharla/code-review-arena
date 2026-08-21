"""Deterministic benchmark metric formulas."""

from __future__ import annotations

import math


def precision(true_positives: int, false_positives: int) -> float:
    denominator = true_positives + false_positives
    return true_positives / denominator if denominator else 0.0


def recall(true_positives: int, false_negatives: int) -> float:
    denominator = true_positives + false_negatives
    return true_positives / denominator if denominator else 0.0


def f_beta_score(value_precision: float, value_recall: float, beta: float = 1.0) -> float:
    if value_precision == 0 and value_recall == 0:
        return 0.0
    beta_squared = beta**2
    return (
        (1 + beta_squared)
        * value_precision
        * value_recall
        / (beta_squared * value_precision + value_recall)
    )


def rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def wilson_interval(
    successes: int, total: int, z: float = 1.96, round_to: int | None = 6
) -> tuple[float, float] | None:
    """Wilson score confidence interval for a binomial proportion (default 95%).

    Returns None when there is nothing to estimate (total == 0). The interval is
    deliberately wide at small n: 7/10 is not [0.70, 0.70] but roughly [0.40, 0.89],
    so two reviewers whose intervals overlap are not reliably ranked.

    ``round_to=None`` returns the unrounded limits. The difference interval below
    is built from two of these, and rounding them first would compound into the
    result.
    """
    if total <= 0:
        return None
    p_hat = successes / total
    z2 = z * z
    denominator = 1 + z2 / total
    center = (p_hat + z2 / (2 * total)) / denominator
    margin = z * ((p_hat * (1 - p_hat) / total + z2 / (4 * total * total)) ** 0.5) / denominator
    low = max(0.0, center - margin)
    high = min(1.0, center + margin)
    if round_to is None:
        return low, high
    return round(low, round_to), round(high, round_to)


def newcombe_difference_interval(
    s1: int, n1: int, s2: int, n2: int, z: float = 1.96
) -> tuple[float, float] | None:
    """Newcombe (1998) method 10 "square-and-add" interval for p1 - p2.

    Built from the two Wilson intervals rather than a Wald interval, which is
    unusable at these pack sizes: Wald returns a ZERO-WIDTH interval at 0/n and
    n/n, and a five-case cohort hits those constantly. A zero-width interval on a
    difference would read as certainty produced by having almost no data.

    Returns None when either denominator is 0.
    """
    if n1 <= 0 or n2 <= 0:
        return None
    first = wilson_interval(s1, n1, z, round_to=None)
    second = wilson_interval(s2, n2, z, round_to=None)
    assert first is not None and second is not None  # denominators checked above
    l1, u1 = first
    l2, u2 = second
    p1 = s1 / n1
    p2 = s2 / n2
    delta = p1 - p2
    low = delta - (((p1 - l1) ** 2 + (u2 - p2) ** 2) ** 0.5)
    high = delta + (((u1 - p1) ** 2 + (p2 - l2) ** 2) ** 0.5)
    return round(max(-1.0, low), 6), round(min(1.0, high), 6)


# Normal quantiles for a 95% two-sided test at 80% power. Hard-coded rather than
# pulled from scipy: the project pins a minimum-dependency floor, and one normal
# quantile for one disclosure metric does not justify the dependency.
_Z_ALPHA_2 = 1.959964
_Z_POWER = 0.841621


def min_detectable_difference(
    n1: int, n2: int, base_rate: float, z_alpha_2: float = _Z_ALPHA_2, z_power: float = _Z_POWER
) -> float | None:
    """The smallest p1 - p2 these cohort sizes could detect, as a rate difference.

    Computed through the arcsine (Cohen's h) transform, which is the right scale
    here precisely because these rates sit near 0 and 1 where the variance of a
    proportion collapses and a raw-difference power calculation misleads.

    This is published even when the difference itself is suppressed. It is the
    number that says "your n is 5" before a reader has to work it out.
    """
    if n1 <= 0 or n2 <= 0:
        return None
    # A base rate of exactly 0 or 1 is degenerate: the arcsine transform
    # saturates there and the answer collapses toward "zero difference is
    # detectable", which at these cohort sizes is the exact opposite of the
    # truth. Callers pass a pooled rate and fall back to 0.5, the
    # maximum-variance case, when it lands on a boundary.
    base = min(max(base_rate, 0.0), 1.0)
    if base <= 0.0 or base >= 1.0:
        base = 0.5
    h_min = (z_alpha_2 + z_power) * ((1 / n1 + 1 / n2) ** 0.5)
    phi_1 = 2 * math.asin(base**0.5)
    phi_2 = phi_1 + h_min
    if phi_2 >= math.pi:
        # The smallest detectable increase runs past a rate of 1.0: at these
        # sizes nothing short of the entire range would be detectable.
        return 1.0
    return round(math.sin(phi_2 / 2) ** 2 - base, 6)
