"""Small numeric and NURBS serialization helpers."""
from __future__ import annotations

from .config import MIN_CURVE_LENGTH

from typing import Any


def clean(x: float) -> float:
    x = float(x)
    if abs(x) < 1.0e-14:
        return 0.0
    return round(x, 15)


def curve_knots(curve: Any) -> list[float]:
    knots = []
    for i in range(1, int(curve.NbKnots()) + 1):
        knots += [clean(curve.Knot(i))] * int(curve.Multiplicity(i))
    return knots


def surface_knots(surface: Any, uv: str) -> list[float]:
    knots = []
    if uv == "u":
        for i in range(1, int(surface.NbUKnots()) + 1):
            knots += [clean(surface.UKnot(i))] * int(surface.UMultiplicity(i))
    else:
        for i in range(1, int(surface.NbVKnots()) + 1):
            knots += [clean(surface.VKnot(i))] * int(surface.VMultiplicity(i))
    return knots


def add_point_ids(points: list[list[float]], start_id: int) -> tuple[list[list[Any]], int]:
    numbered = []
    point_id = start_id
    for point in points:
        numbered.append([point_id, point])
        point_id += 1
    return numbered, point_id


def sub(a: list[float], b: list[float]) -> list[float]:
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def dot(a: list[float], b: list[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: list[float], b: list[float]) -> list[float]:
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def norm(a: list[float]) -> float:
    return dot(a, a) ** 0.5


def distance(a: list[float], b: list[float]) -> float:
    return norm(sub(a, b))


def lerp(a: list[float], b: list[float], t: float) -> list[float]:
    return [clean(a[i] + (b[i] - a[i]) * t) for i in range(len(a))]


def polyline_length(points: list[list[float]]) -> float:
    total = 0.0
    for i in range(1, len(points)):
        total += distance(points[i - 1], points[i])
    return total


def points_are_linear(points: list[list[float]], tolerance: float = 1.0e-7) -> bool:
    if len(points) < 2:
        return False
    p0 = points[0][:3]
    p1 = points[-1][:3]
    if distance(p0, p1) <= tolerance:
        return False
    direction = sub(p1, p0)
    length = norm(direction)
    for point in points[1:-1]:
        offset = sub(point[:3], p0)
        if norm(cross(offset, direction)) / length > tolerance:
            return False
    return True


def point_at_polyline_fraction(points: list[list[float]], fraction: float) -> list[float]:
    total_length = polyline_length(points)
    if total_length <= MIN_CURVE_LENGTH:
        return points[0]

    target = total_length * fraction
    travelled = 0.0
    for i in range(len(points) - 1):
        segment_length = distance(points[i], points[i + 1])
        if segment_length <= 1.0e-14:
            continue
        if travelled + segment_length >= target:
            local = (target - travelled) / segment_length
            return lerp(points[i], points[i + 1], local)
        travelled += segment_length
    return points[-1]
