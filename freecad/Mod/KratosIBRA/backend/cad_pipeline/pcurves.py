"""Raw STEP parameter-curve evaluation and per-conversion matching."""
from __future__ import annotations

from OCP.BRep import BRep_Tool
from typing import Any
import math

from .numeric import add_point_ids, clean

RAW_STEP_PCURVE_LOOKAHEAD = 50
RAW_STEP_PCURVE_MAX_ACCEPT_ERROR = 1.0
RAW_STEP_PCURVE_MIN_ERROR_GAP = 10.0
RAW_STEP_PCURVE_VALIDATION_TOLERANCE = 1.0e-2

def raw_line_pcurve_to_json(
        raw_pcurve: dict[str, Any],
        start_point_id: int,
        active_range: list[float],
) -> tuple[dict[str, Any], int]:
    first, last = [float(value) for value in active_range]
    point = raw_pcurve["line_point"]
    direction = raw_pcurve["line_direction"]
    points = [
        [clean(point[0] + first * direction[0]), clean(point[1] + first * direction[1]), 0.0, 1.0],
        [clean(point[0] + last * direction[0]), clean(point[1] + last * direction[1]), 0.0, 1.0],
    ]
    control_points, next_point_id = add_point_ids(points, start_point_id)
    return {
        "is_rational": False,
        "degree": 1,
        "knot_vector": [clean(first), clean(first), clean(last), clean(last)],
        "active_range": [clean(first), clean(last)],
        "control_points": control_points,
    }, next_point_id


def raw_step_pcurve_to_json(
        raw_pcurve: dict[str, Any],
        start_point_id: int,
        active_range: list[float] | None = None,
) -> tuple[dict[str, Any], int]:
    if raw_pcurve.get("kind") == "line":
        if active_range is None:
            raise RuntimeError("A raw STEP LINE pcurve needs an active range from the current edge-face pair.")
        return raw_line_pcurve_to_json(raw_pcurve, start_point_id, active_range)

    control_points, next_point_id = add_point_ids(raw_pcurve["control_points"], start_point_id)
    knot_vector = raw_pcurve["knot_vector"]
    active_range = raw_pcurve["active_range"]
    if (
            int(raw_pcurve["degree"]) == 1
            and knot_vector
            and clean(knot_vector[-1]) == 0.0
            and knot_vector[0] < 0.0
    ):
        offset = -knot_vector[0]
        knot_vector = [clean(knot + offset) for knot in knot_vector]
        active_range = [clean(active_range[0] + offset), clean(active_range[1] + offset)]
    return {
        "is_rational": bool(raw_pcurve.get("is_rational", False)),
        "degree": int(raw_pcurve["degree"]),
        "knot_vector": knot_vector,
        "active_range": active_range,
        "control_points": control_points,
    }, next_point_id


def raw_bspline_find_span(degree: int, knots: list[float], pole_count: int, parameter: float) -> int | None:
    n = pole_count - 1
    if n < degree or len(knots) < n + degree + 2:
        return None
    if parameter >= knots[n + 1]:
        return n
    if parameter <= knots[degree]:
        return degree
    low = degree
    high = n + 1
    mid = (low + high) // 2
    while parameter < knots[mid] or parameter >= knots[mid + 1]:
        if parameter < knots[mid]:
            high = mid
        else:
            low = mid
        mid = (low + high) // 2
    return mid


def evaluate_raw_bspline_pcurve(raw_pcurve: dict[str, Any], parameter: float) -> list[float] | None:
    control_points = raw_pcurve.get("control_points", [])
    knots = [float(value) for value in raw_pcurve.get("knot_vector", [])]
    degree = int(raw_pcurve.get("degree", 0))
    if not control_points or not knots:
        return None

    n = len(control_points) - 1
    if len(knots) < n + degree + 2:
        return None
    domain_min = knots[degree]
    domain_max = knots[n + 1]
    parameter = max(domain_min, min(domain_max, float(parameter)))
    span = raw_bspline_find_span(degree, knots, len(control_points), parameter)
    if span is None:
        return None

    work = []
    for j in range(degree + 1):
        cp = control_points[span - degree + j]
        weight = float(cp[3]) if len(cp) > 3 else 1.0
        work.append([float(cp[0]) * weight, float(cp[1]) * weight, weight])

    for r in range(1, degree + 1):
        for j in range(degree, r - 1, -1):
            left = knots[span - degree + j]
            right = knots[span + 1 + j - r]
            denominator = right - left
            alpha = 0.0 if abs(denominator) < 1.0e-14 else (parameter - left) / denominator
            work[j] = [
                (1.0 - alpha) * work[j - 1][0] + alpha * work[j][0],
                (1.0 - alpha) * work[j - 1][1] + alpha * work[j][1],
                (1.0 - alpha) * work[j - 1][2] + alpha * work[j][2],
            ]

    if abs(work[degree][2]) < 1.0e-14:
        return None
    return [work[degree][0] / work[degree][2], work[degree][1] / work[degree][2]]


def evaluate_raw_pcurve(
        raw_pcurve: dict[str, Any],
        parameter: float,
        active_range: list[float],
) -> list[float] | None:
    if raw_pcurve.get("kind") == "line":
        point = raw_pcurve["line_point"]
        direction = raw_pcurve["line_direction"]
        return [
            float(point[0]) + float(parameter) * float(direction[0]),
            float(point[1]) + float(parameter) * float(direction[1]),
        ]
    return evaluate_raw_bspline_pcurve(raw_pcurve, parameter)


def raw_pcurve_active_range(raw_pcurve: dict[str, Any], fallback: list[float]) -> list[float]:
    if raw_pcurve.get("kind") == "line":
        return fallback
    return [float(value) for value in raw_pcurve.get("active_range", fallback)]


def validate_raw_pcurve_against_edge(
        edge: Any,
        face: Any,
        raw_pcurve: dict[str, Any],
        first: float,
        last: float,
        sample_count: int = 7,
) -> float:
    try:
        surface = BRep_Tool.Surface_s(face)
        edge_result = BRep_Tool.Curve_s(edge, first, last)
        edge_curve = edge_result[0] if isinstance(edge_result, tuple) else edge_result
        if surface is None or edge_curve is None:
            return math.inf

        raw_range = raw_pcurve_active_range(raw_pcurve, [first, last])
        max_error = 0.0
        for i in range(sample_count):
            fraction = i / (sample_count - 1) if sample_count > 1 else 0.0
            edge_parameter = first + (last - first) * fraction
            raw_parameter = raw_range[0] + (raw_range[1] - raw_range[0]) * fraction
            uv = evaluate_raw_pcurve(raw_pcurve, raw_parameter, [first, last])
            if uv is None:
                return math.inf
            surface_point = surface.Value(float(uv[0]), float(uv[1]))
            edge_point = edge_curve.Value(edge_parameter)
            max_error = max(max_error, float(surface_point.Distance(edge_point)))
        return max_error
    except Exception:
        return math.inf


class PcurveMatcher:
    """Per-conversion STEP matching state; safe to use for consecutive conversions."""

    def __init__(self, records: list[dict[str, Any]] | None = None):
        self.records = records or []
        self.cursor = 0
        self.used: set[int] = set()
        self.stats = {"used": 0, "fallback": 0, "failed_validation": 0}

    def _advance(self) -> None:
        while (
                self.cursor < len(self.records)
                and self.cursor in self.used
        ):
            self.cursor += 1

    def find_match(
            self,
            edge: Any,
            face: Any,
            first: float,
            last: float,
    ) -> tuple[dict[str, Any], float, int] | None:
        if not self.records:
            return None

        self._advance()
        start = self.cursor
        stop = min(len(self.records), start + RAW_STEP_PCURVE_LOOKAHEAD)
        tested: list[tuple[dict[str, Any], float, int]] = []

        for index in range(start, stop):
            if index in self.used:
                continue
            candidate = self.records[index]
            error = validate_raw_pcurve_against_edge(edge, face, candidate, first, last)
            tested.append((candidate, error, index))
            if error <= RAW_STEP_PCURVE_VALIDATION_TOLERANCE:
                self.used.add(index)
                self._advance()
                self.stats["used"] += 1
                return candidate, error, index

        tested = sorted(tested, key=lambda item: item[1])
        if tested:
            best = tested[0]
            second_error = tested[1][1] if len(tested) > 1 else math.inf
            if (
                    best[1] <= RAW_STEP_PCURVE_MAX_ACCEPT_ERROR
                    and second_error >= best[1] * RAW_STEP_PCURVE_MIN_ERROR_GAP
            ):
                self.used.add(best[2])
                self._advance()
                self.stats["used"] += 1
                return best
            self.stats["failed_validation"] += 1
        return None
