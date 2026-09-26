"""Piecewise-linear scalar amplitude tables for loads (pure stdlib).

This file is copied into every exported case next to ``ibra_case_process.py`` so the
project validator and the runtime share exactly one implementation. Times are
analysis seconds and factors are dimensionless, so neither is changed by unit
conversion. A malformed table is reported, never sorted or otherwise repaired.
"""
from __future__ import annotations

import math


class AmplitudeTableError(ValueError):
    """A malformed amplitude table; the caller reports it and never repairs it."""


def is_finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(value)


def parse(points):
    """Return ``((t, f), ...)`` with finite strictly increasing times.

    At least two ``[time, factor]`` points; booleans are not numbers; the list is
    never auto-sorted.
    """
    if not isinstance(points, (list, tuple)) or len(points) < 2:
        raise AmplitudeTableError("an amplitude table needs at least two [time, factor] points")
    rows = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise AmplitudeTableError("every amplitude point must be [time, factor]")
        time, factor = point
        if not is_finite_number(time) or not is_finite_number(factor):
            raise AmplitudeTableError("amplitude times and factors must be finite numbers "
                                      "(booleans are not numbers)")
        rows.append((float(time), float(factor)))
    for i in range(1, len(rows)):
        if rows[i][0] <= rows[i - 1][0]:
            raise AmplitudeTableError("amplitude times must be strictly increasing; "
                                      "the table is never auto-sorted")
    return tuple(rows)


def evaluate(rows, time):
    """Value at ``time``: stored endpoints, linear inside, exactly 0 outside."""
    if not is_finite_number(time):
        raise AmplitudeTableError("analysis time must be a finite number")
    time = float(time)
    if time < rows[0][0] or time > rows[-1][0]:
        return 0.0
    for i in range(1, len(rows)):
        t0, f0 = rows[i - 1]
        t1, f1 = rows[i]
        if time <= t1:
            if time == t1 or t1 == t0:
                return f1
            return f0 + (f1 - f0) * (time - t0) / (t1 - t0)
    return rows[-1][1]
