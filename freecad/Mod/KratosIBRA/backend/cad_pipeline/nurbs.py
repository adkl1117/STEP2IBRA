"""Pure NURBS curve evaluation, shared by visualization and regression tests."""
from __future__ import annotations

from typing import Any
import numpy as np


def sample_parameters(
    curve: dict[str, Any],
    refinement: int,
) -> list[float]:
    if type(refinement) is not int or refinement < 0:
        raise ValueError('refinement must be a non-negative integer.')
    knots = [float(value) for value in curve["knot_vector"]]
    active_range = curve.get("active_range")
    if not isinstance(active_range, list) or len(active_range) != 2:
        raise ValueError("Every coupling parameter curve needs an active_range.")

    first = float(active_range[0])
    last = float(active_range[1])
    if not np.all(np.isfinite([first, last, *knots])):
        raise ValueError('Curve parameters must be finite.')
    lower = min(first, last)
    upper = max(first, last)
    breakpoints = sorted(
        {lower, upper, *(value for value in knots if lower < value < upper)}
    )

    subdivisions = refinement + 1
    parameters: list[float] = []
    for span_index in range(len(breakpoints) - 1):
        start = breakpoints[span_index]
        end = breakpoints[span_index + 1]
        if end <= start:
            continue
        for local_index in range(subdivisions):
            if span_index > 0 and local_index == 0:
                continue
            fraction = local_index / subdivisions
            parameters.append((1.0 - fraction) * start + fraction * end)
        parameters.append(end)

    if first > last:
        parameters.reverse()
    if len(parameters) < 2:
        raise ValueError(f"Invalid curve active range: {active_range}")
    return parameters


def evaluate_curve(
    curve: dict[str, Any],
    parameter: float,
) -> tuple[float, float]:
    degree = int(curve["degree"])
    knots = np.asarray(curve["knot_vector"], dtype=np.float64)
    control_records = curve["control_points"]
    if degree < 1 or len(control_records) < degree + 1:
        raise ValueError('Invalid NURBS degree or control-point count.')
    if not np.all(np.isfinite(knots)) or np.any(np.diff(knots) < 0):
        raise ValueError('Knot vector must be finite and sorted.')
    if not np.isfinite(parameter):
        raise ValueError('Evaluation parameter must be finite.')

    control_points = []
    weights = []
    is_rational = bool(curve.get("is_rational", True))

    for record in control_records:
        values = record[-1] if isinstance(record[-1], list) else record
        if len(values) < 2:
            raise ValueError("A parameter-curve control point needs u and v.")
        control_points.append([float(values[0]), float(values[1])])
        if is_rational:
            weights.append(float(values[3]) if len(values) > 3 else 1.0)
        else:
            weights.append(1.0)

    control_points_array = np.asarray(control_points, dtype=np.float64)
    weights_array = np.asarray(weights, dtype=np.float64)
    number_of_control_points = len(control_points_array)

    expected_knot_count = number_of_control_points + degree + 1
    if len(knots) != expected_knot_count:
        raise ValueError(
            f"Invalid knot vector length {len(knots)}; expected "
            f"{expected_knot_count} for degree {degree} and "
            f"{number_of_control_points} control points."
        )

    span = find_span(
        number_of_control_points - 1,
        degree,
        parameter,
        knots,
    )
    basis = basis_functions(
        span,
        parameter,
        degree,
        knots,
    )
    first_control_point = span - degree
    selected_points = control_points_array[
        first_control_point : first_control_point + degree + 1
    ]
    selected_weights = weights_array[
        first_control_point : first_control_point + degree + 1
    ]

    rational_basis = basis * selected_weights
    denominator = rational_basis.sum()
    if abs(denominator) <= np.finfo(np.float64).eps:
        raise ValueError("NURBS curve evaluation produced a zero denominator.")
    coordinates = (
        rational_basis[:, np.newaxis] * selected_points
    ).sum(axis=0) / denominator
    return float(coordinates[0]), float(coordinates[1])


def find_span(
    last_control_point: int,
    degree: int,
    parameter: float,
    knots: np.ndarray,
) -> int:
    if parameter >= knots[last_control_point + 1]:
        return last_control_point
    if parameter <= knots[degree]:
        return degree

    low = degree
    high = last_control_point + 1
    middle = (low + high) // 2
    while parameter < knots[middle] or parameter >= knots[middle + 1]:
        if parameter < knots[middle]:
            high = middle
        else:
            low = middle
        middle = (low + high) // 2
    return middle


def basis_functions(
    span: int,
    parameter: float,
    degree: int,
    knots: np.ndarray,
) -> np.ndarray:
    basis = np.zeros(degree + 1, dtype=np.float64)
    left = np.zeros(degree + 1, dtype=np.float64)
    right = np.zeros(degree + 1, dtype=np.float64)
    basis[0] = 1.0

    for order in range(1, degree + 1):
        left[order] = parameter - knots[span + 1 - order]
        right[order] = knots[span + order] - parameter
        saved = 0.0
        for index in range(order):
            denominator = right[index + 1] + left[order - index]
            temporary = 0.0 if denominator == 0.0 else basis[index] / denominator
            basis[index] = saved + right[index + 1] * temporary
            saved = left[order - index] * temporary
        basis[order] = saved
    return basis
