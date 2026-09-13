"""Write accepted couplings into the CAD JSON topology."""
from __future__ import annotations

from .config import MIN_CURVE_LENGTH, DEFAULT_TOLERANCE

from typing import Any
import logging

logger = logging.getLogger(__name__)

from .numeric import add_point_ids, clean, distance, points_are_linear, polyline_length


def find_face(cad_json: dict[str, Any], face_brep_id: int) -> dict[str, Any]:
    for brep in cad_json["breps"]:
        for face in brep["faces"]:
            if face["brep_id"] == face_brep_id:
                return face
    raise KeyError(face_brep_id)


def next_trim_index(face_json: dict[str, Any]) -> int:
    biggest = -1
    for loop in face_json["boundary_loops"]:
        for trim in loop["trimming_curves"]:
            biggest = max(biggest, int(trim["trim_index"]))
    for trim in face_json["embedded_edges"]:
        biggest = max(biggest, int(trim["trim_index"]))
    return biggest + 1


def sampled_polyline_curve_to_json(
        points: list[list[float]],
        next_point_id: int,
        is_2d: bool = False,
) -> tuple[dict[str, Any] | None, int]:
    if len(points) < 2:
        return None, next_point_id

    parameters = [0.0]
    for i in range(len(points) - 1):
        parameters.append(parameters[-1] + distance(points[i][:3], points[i + 1][:3]))

    length = parameters[-1]
    if length <= MIN_CURVE_LENGTH:
        return None, next_point_id

    control_points, next_point_id = add_point_ids(
        [[clean(value) for value in point] for point in points],
        next_point_id,
    )
    knot_vector = [0.0, 0.0]
    knot_vector += [clean(value) for value in parameters[1:-1]]
    knot_vector += [clean(length), clean(length)]

    data = {
        "degree": 1,
        "knot_vector": knot_vector,
        "active_range": [0.0, clean(length)],
        "control_points": control_points,
    }
    if is_2d:
        data["is_rational"] = False

    return data, next_point_id


def find_or_create_shared_brep(
        cad_json: dict[str, Any],
        next_brep_id: int,
) -> tuple[dict[str, Any], int]:
    for brep in cad_json["breps"]:
        if brep.get("faces") == [] and "edges" in brep and brep.get("vertices") == []:
            return brep, next_brep_id

    shared_brep = {"brep_id": next_brep_id, "faces": [], "edges": [], "vertices": []}
    cad_json["breps"].append(shared_brep)
    return shared_brep, next_brep_id + 1


def write_curve_coupling_as_shared_edge(
        cad_json: dict[str, Any],
        candidate: dict[str, Any],
        shared_brep: dict[str, Any],
        next_brep_id: int,
        next_point_id: int,
) -> tuple[int, int, bool]:
    physical_points = candidate.get("physical_points", [])
    parametric_points_a = candidate.get("parametric_points_a", [])
    parametric_points_b = candidate.get("parametric_points_b", [])

    if len(physical_points) < 2:
        return next_brep_id, next_point_id, False
    if len(physical_points) != len(parametric_points_a) or len(physical_points) != len(parametric_points_b):
        return next_brep_id, next_point_id, False
    if polyline_length(physical_points) <= MIN_CURVE_LENGTH:
        return next_brep_id, next_point_id, False
    if points_are_linear(physical_points, float(candidate.get("tolerance", DEFAULT_TOLERANCE))):
        physical_points = [physical_points[0], physical_points[-1]]
        parametric_points_a = [parametric_points_a[0], parametric_points_a[-1]]
        parametric_points_b = [parametric_points_b[0], parametric_points_b[-1]]

    face_a = find_face(cad_json, candidate["face_id_a"])
    face_b = find_face(cad_json, candidate["face_id_b"])
    trim_index_a = next_trim_index(face_a)
    trim_index_b = next_trim_index(face_b)

    curve_a_points = [[uv[0], uv[1], 0.0, 1.0] for uv in parametric_points_a]
    curve_b_points = [[uv[0], uv[1], 0.0, 1.0] for uv in parametric_points_b]
    physical_curve_points = [[point[0], point[1], point[2], 1.0] for point in physical_points]

    parameter_curve_a, next_point_id = sampled_polyline_curve_to_json(curve_a_points, next_point_id, is_2d=True)
    parameter_curve_b, next_point_id = sampled_polyline_curve_to_json(curve_b_points, next_point_id, is_2d=True)
    physical_curve, next_point_id = sampled_polyline_curve_to_json(physical_curve_points, next_point_id, is_2d=False)
    if parameter_curve_a is None or parameter_curve_b is None or physical_curve is None:
        return next_brep_id, next_point_id, False

    face_a["embedded_edges"].append(
        {
            "trim_index": trim_index_a,
            "curve_direction": True,
            "parameter_curve": parameter_curve_a,
        }
    )
    face_b["embedded_edges"].append(
        {
            "trim_index": trim_index_b,
            "curve_direction": True,
            "parameter_curve": parameter_curve_b,
        }
    )

    edge_brep_id = next_brep_id
    shared_brep["edges"].append(
        {
            "brep_id": edge_brep_id,
            "3d_curve": physical_curve,
            "topology": [
                {
                    "brep_id": candidate["face_id_a"],
                    "trim_index": trim_index_a,
                    "relative_direction": True,
                },
                {
                    "brep_id": candidate["face_id_b"],
                    "trim_index": trim_index_b,
                    "relative_direction": True,
                },
            ],
        }
    )
    logger.debug(
        "written curve coupling as shared edge: "
        f"face {candidate['face_id_a']} - face {candidate['face_id_b']}, "
        f"edge id {edge_brep_id}, trim A {trim_index_a}, trim B {trim_index_b}"
    )
    return next_brep_id + 1, next_point_id, True
