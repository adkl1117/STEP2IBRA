"""Geometric candidate generation; no JSON mutation."""
from __future__ import annotations

from .config import MIN_CURVE_LENGTH, DEFAULT_SAMPLE_COUNT

from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.GeomAPI import GeomAPI_IntCS, GeomAPI_IntSS, GeomAPI_ProjectPointOnSurf
from OCP.TopAbs import TopAbs_IN, TopAbs_ON
from OCP.gp import gp_Pnt, gp_Pnt2d
from typing import Any
import math

from .numeric import clean, distance, point_at_polyline_fraction, polyline_length


def sample_curve_points(curve: Any, sample_count: int = DEFAULT_SAMPLE_COUNT) -> list[list[float]]:
    first = float(curve.FirstParameter())
    last = float(curve.LastParameter())
    if not math.isfinite(first) or not math.isfinite(last):
        return []
    if abs(last - first) <= MIN_CURVE_LENGTH:
        return []

    points = []
    for i in range(sample_count):
        t = i / (sample_count - 1)
        parameter = first + (last - first) * t
        point = curve.Value(parameter)
        points.append([float(point.X()), float(point.Y()), float(point.Z())])
    return points


def projected_point_on_face(
        point: list[float],
        face_record: dict[str, Any],
        tolerance: float,
) -> dict[str, Any] | None:
    u_min, u_max, v_min, v_max = face_record["uv_bounds"]
    projector = GeomAPI_ProjectPointOnSurf(
        gp_Pnt(point[0], point[1], point[2]),
        face_record["surface"],
        u_min,
        u_max,
        v_min,
        v_max,
        tolerance,
    )
    if not projector.IsDone() or projector.NbPoints() == 0:
        return None

    u, v = projector.LowerDistanceParameters()
    classifier = BRepClass_FaceClassifier(face_record["face"], gp_Pnt2d(float(u), float(v)), tolerance)
    if classifier.State() not in (TopAbs_IN, TopAbs_ON):
        return None

    projected = face_record["surface"].Value(float(u), float(v))
    projected_xyz = [float(projected.X()), float(projected.Y()), float(projected.Z())]
    projection_error = float(projector.LowerDistance())
    if projection_error > tolerance:
        return None

    return {
        "uv": [clean(u), clean(v)],
        "xyz": [clean(projected_xyz[0]), clean(projected_xyz[1]), clean(projected_xyz[2])],
        "error": projection_error,
    }


def build_curve_coupling_candidate(
        left: dict[str, Any],
        right: dict[str, Any],
        curve: Any,
        detection_type: str,
        tolerance: float,
        sample_count: int = DEFAULT_SAMPLE_COUNT,
) -> dict[str, Any] | None:
    sampled_points = sample_curve_points(curve, sample_count)
    if len(sampled_points) < 2:
        return None
    if polyline_length(sampled_points) <= MIN_CURVE_LENGTH:
        return None

    physical_points = []
    parametric_points_a = []
    parametric_points_b = []
    max_projection_error = 0.0
    max_distance_error = 0.0

    for point in sampled_points:
        projected_a = projected_point_on_face(point, left, tolerance)
        projected_b = projected_point_on_face(point, right, tolerance)
        if projected_a is None or projected_b is None:
            return None

        distance_error = distance(projected_a["xyz"], projected_b["xyz"])
        if distance_error > tolerance:
            return None

        physical_points.append([clean(point[0]), clean(point[1]), clean(point[2])])
        parametric_points_a.append(projected_a["uv"])
        parametric_points_b.append(projected_b["uv"])
        max_projection_error = max(max_projection_error, projected_a["error"], projected_b["error"])
        max_distance_error = max(max_distance_error, distance_error)

    return {
        "face_id_a": left["face_brep_id"],
        "face_id_b": right["face_brep_id"],
        "physical_points": physical_points,
        "parametric_points_a": parametric_points_a,
        "parametric_points_b": parametric_points_b,
        "detection_type": detection_type,
        "projection_error": clean(max_projection_error),
        "max_distance_error": clean(max_distance_error),
        "tolerance": clean(tolerance),
    }


def curve_coupling_signature(candidate: dict[str, Any], tolerance: float) -> tuple[Any, ...]:
    points = candidate["physical_points"]
    midpoint = point_at_polyline_fraction(points, 0.5)
    sample = [points[0], midpoint, points[-1]]
    reverse_sample = [points[-1], midpoint, points[0]]

    def quantized(values: list[list[float]]) -> tuple[tuple[int, int, int], ...]:
        scale = 1.0 / tolerance
        return tuple(
            (
                int(round(point[0] * scale)),
                int(round(point[1] * scale)),
                int(round(point[2] * scale)),
            )
            for point in values
        )

    forward = quantized(sample)
    backward = quantized(reverse_sample)
    return (
        min(candidate["face_id_a"], candidate["face_id_b"]),
        max(candidate["face_id_a"], candidate["face_id_b"]),
        min(forward, backward),
    )


def boundary_curve_surface_candidates(
        left: dict[str, Any],
        right: dict[str, Any],
        tolerance: float,
        sample_count: int = DEFAULT_SAMPLE_COUNT,
) -> list[dict[str, Any]]:
    candidates = []
    for source, target in ((left, right), (right, left)):
        for boundary_curve in source["boundary_curves"]:
            intersector = GeomAPI_IntCS(boundary_curve, target["surface"])
            found_segment = False
            if intersector.IsDone():
                for segment_index in range(1, int(intersector.NbSegments()) + 1):
                    found_segment = True
                    segment_curve = intersector.Segment(segment_index)
                    candidate = build_curve_coupling_candidate(
                        left,
                        right,
                        segment_curve,
                        "boundary_curve_surface",
                        tolerance,
                        sample_count,
                    )
                    if candidate is not None:
                        candidates.append(candidate)

            if found_segment:
                continue

            # Coincident boundary curves are common in CAD coupling models.
            # Some OCCT wrappers report them as points only, so validate the whole edge.
            candidate = build_curve_coupling_candidate(
                left,
                right,
                boundary_curve,
                "boundary_curve_surface",
                tolerance,
                sample_count,
            )
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def surface_surface_candidates(
        left: dict[str, Any],
        right: dict[str, Any],
        tolerance: float,
        sample_count: int = DEFAULT_SAMPLE_COUNT,
) -> list[dict[str, Any]]:
    intersector = GeomAPI_IntSS(left["surface"], right["surface"], tolerance)
    if not intersector.IsDone():
        return []

    candidates = []
    for line_index in range(1, int(intersector.NbLines()) + 1):
        curve = intersector.Line(line_index)
        candidate = build_curve_coupling_candidate(
            left,
            right,
            curve,
            "surface_surface",
            tolerance,
            sample_count,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates
