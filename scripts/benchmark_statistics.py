"""Paired family bootstrap for public development benchmarks, not a power analysis."""

import math
import random


def reduction_interval(clusters, field, *, seed=20261021, samples=10000):
    if type(samples) is not int or samples < 40:
        raise ValueError("Use at least 40 bootstrap samples")
    groups = list(clusters)
    if len(groups) < 2:
        return None
    for group in groups:
        for arm in ["main", "feature"]:
            value = group[arm][field]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid {arm}/{field} measurement")
    rng = random.Random(seed)
    reductions = []
    for _ in range(samples):
        chosen = rng.choices(groups, k=len(groups))
        baseline = sum(p["main"][field] for p in chosen)
        if baseline <= 0:
            return None
        reductions.append(1 - sum(p["feature"][field] for p in chosen) / baseline)
    reductions.sort()
    return [reductions[int(samples * 0.025)], reductions[int(samples * 0.975) - 1]]


def supported_improvement(baseline, candidate, interval):
    return (
        all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in [baseline, candidate])
        and baseline > 0
        and candidate < baseline
        and isinstance(interval, list)
        and len(interval) == 2
        and all(type(v) in (int, float) and math.isfinite(v) for v in interval)
        and 0 < interval[0] <= interval[1] <= 1
    )
