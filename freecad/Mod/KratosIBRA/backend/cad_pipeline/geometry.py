"""OCCT geometry access and conversion to the CAD JSON schema."""
from __future__ import annotations

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.Bnd import Bnd_Box
from OCP.Geom import Geom_RectangularTrimmedSurface, Geom_TrimmedCurve
from OCP.Geom2d import Geom2d_TrimmedCurve
from OCP.Geom2dConvert import Geom2dConvert
from OCP.GeomConvert import GeomConvert
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FORWARD
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from typing import Any
import math

from .numeric import add_point_ids, clean, curve_knots, surface_knots
from .pcurves import PcurveMatcher, raw_step_pcurve_to_json


def surface_to_json(face: Any, start_point_id: int) -> tuple[dict[str, Any], int]:
    trimmed_surface, _ = trimmed_face_surface(face)
    bspline = GeomConvert.SurfaceToBSplineSurface_s(trimmed_surface)

    points = []
    for v in range(1, int(bspline.NbVPoles()) + 1):
        for u in range(1, int(bspline.NbUPoles()) + 1):
            p = bspline.Pole(u, v)
            w = bspline.Weight(u, v)
            points.append([clean(p.X()), clean(p.Y()), clean(p.Z()), clean(w)])

    control_points, next_point_id = add_point_ids(points, start_point_id)

    # dict construction
    return {
        "is_trimmed": True,
        "is_rational": bool(bspline.IsURational() or bspline.IsVRational()),
        "degrees": [int(bspline.UDegree()), int(bspline.VDegree())],
        "knot_vectors": [surface_knots(bspline, "u"), surface_knots(bspline, "v")],
        "control_points": control_points,
    }, next_point_id


def bspline_curve_to_json(
        bspline: Any,
        start_point_id: int,
        active_range: list[float],
        is_2d: bool,
) -> tuple[dict[str, Any], int]:
    points = []
    for i in range(1, int(bspline.NbPoles()) + 1):
        p = bspline.Pole(i)
        w = bspline.Weight(i) if bspline.IsRational() else 1.0
        if is_2d:
            points.append([clean(p.X()), clean(p.Y()), 0.0, clean(w)])
        else:
            points.append([clean(p.X()), clean(p.Y()), clean(p.Z()), clean(w)])

    control_points, next_point_id = add_point_ids(points, start_point_id)

    data = {
        "degree": int(bspline.Degree()),
        "knot_vector": curve_knots(bspline),
        "active_range": active_range,
        "control_points": control_points,
    }
    if is_2d:
        data["is_rational"] = bool(bspline.IsRational())
    return data, next_point_id


def edge_3d_curve_to_json(edge: Any, start_point_id: int) -> tuple[dict[str, Any], int]:
    adaptor = BRepAdaptor_Curve(edge)
    first = float(adaptor.FirstParameter())
    last = float(adaptor.LastParameter())

    result = BRep_Tool.Curve_s(edge, first, last)
    curve = result[0] if isinstance(result, tuple) else result

    trimmed_curve = Geom_TrimmedCurve(curve, first, last)
    bspline = GeomConvert.CurveToBSplineCurve_s(trimmed_curve)
    return bspline_curve_to_json(bspline, start_point_id, [clean(first), clean(last)], is_2d=False)


def validate_pcurve_against_edge(
        edge: Any,
        face: Any,
        pcurve: Any,
        first: float,
        last: float,
        sample_count: int = 5,
        tolerance: float = 1.0e-4,
) -> float:
    try:
        surface = BRep_Tool.Surface_s(face)
        edge_result = BRep_Tool.Curve_s(edge, first, last)
        edge_curve = edge_result[0] if isinstance(edge_result, tuple) else edge_result
        if surface is None or edge_curve is None:
            return math.inf

        max_error = 0.0
        for i in range(sample_count):
            fraction = i / (sample_count - 1) if sample_count > 1 else 0.0
            parameter = first + (last - first) * fraction
            uv_point = pcurve.Value(parameter)
            surface_point = surface.Value(float(uv_point.X()), float(uv_point.Y()))
            edge_point = edge_curve.Value(parameter)
            max_error = max(max_error, float(surface_point.Distance(edge_point)))
        return max_error
    except Exception:
        return math.inf


def edge_pcurve_to_json(edge: Any, face: Any, start_point_id: int, matcher: PcurveMatcher) -> tuple[dict[str, Any], int]:
    first, last = BRep_Tool.Range_s(edge, face)
    first = float(first)
    last = float(last)

    raw_match = matcher.find_match(edge, face, first, last)
    if raw_match is not None:
        raw_pcurve, error, raw_index = raw_match
        return raw_step_pcurve_to_json(raw_pcurve, start_point_id, [clean(first), clean(last)])

    matcher.stats["fallback"] += 1
    result = BRep_Tool.CurveOnSurface_s(edge, face, first, last)
    pcurve = result[0] if isinstance(result, tuple) else result
    if pcurve is None:
        raise RuntimeError("Pcurve could not be found for the current edge-face pair.")

    tolerance = 1.0e-4
    max_error = validate_pcurve_against_edge(edge, face, pcurve, first, last, tolerance=tolerance)
    if max_error > tolerance:
        print(f"WARNING: pcurve validation error max_error={max_error}")

    trimmed_pcurve = Geom2d_TrimmedCurve(pcurve, first, last)
    bspline = Geom2dConvert.CurveToBSplineCurve_s(trimmed_pcurve)
    return bspline_curve_to_json(bspline, start_point_id, [clean(first), clean(last)], is_2d=True)


def orient_parameter_curve_to_wire(
        parameter_curve: dict[str, Any],
        edge: Any,
) -> dict[str, Any]:
    if edge.Orientation() == TopAbs_FORWARD:
        return parameter_curve

    first, last = [float(value) for value in parameter_curve["active_range"]]
    parameter_curve["control_points"].reverse()
    parameter_curve["knot_vector"] = [
        clean(first + last - float(knot))
        for knot in reversed(parameter_curve["knot_vector"])
    ]
    return parameter_curve


def ordered_edges(wire: Any, face: Any) -> list[Any]:
    try:
        explorer = BRepTools_WireExplorer(wire, face)
    except TypeError:
        explorer = BRepTools_WireExplorer(wire)

    edges = []
    while explorer.More():
        edges.append(TopoDS.Edge_s(explorer.Current()))
        explorer.Next()
    return edges


def face_aabb(face: Any, tolerance: float) -> list[float]:
    box = Bnd_Box()
    BRepBndLib.Add_s(face, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return [
        float(xmin) - tolerance,
        float(ymin) - tolerance,
        float(zmin) - tolerance,
        float(xmax) + tolerance,
        float(ymax) + tolerance,
        float(zmax) + tolerance,
    ]


def face_surface_record(face: Any, face_brep_id: int, tolerance: float = 1.0e-6) -> dict[str, Any]:
    trimmed_surface, bounds = trimmed_face_surface(face)
    return {
        "face": face,
        "face_brep_id": face_brep_id,
        "surface": trimmed_surface,
        "uv_bounds": bounds,
        "aabb": face_aabb(face, 10.0 * tolerance),
        "tight_aabb": tight_face_bounds(face),
        "boundary_curves": face_boundary_curves(face),
    }


def edge_trimmed_curve(edge: Any) -> Any:
    adaptor = BRepAdaptor_Curve(edge)
    first = float(adaptor.FirstParameter())
    last = float(adaptor.LastParameter())
    result = BRep_Tool.Curve_s(edge, first, last)
    curve = result[0] if isinstance(result, tuple) else result
    return Geom_TrimmedCurve(curve, first, last)


def face_boundary_curves(face: Any) -> list[Any]:
    curves = []
    edge_explorer = TopExp_Explorer(face, TopAbs_EDGE)
    while edge_explorer.More():
        edge = TopoDS.Edge_s(edge_explorer.Current())
        curves.append(edge_trimmed_curve(edge))
        edge_explorer.Next()
    return curves


def aabb_overlap(box_a: list[float], box_b: list[float]) -> bool:
    return (
            box_a[0] <= box_b[3]
            and box_a[3] >= box_b[0]
            and box_a[1] <= box_b[4]
            and box_a[4] >= box_b[1]
            and box_a[2] <= box_b[5]
            and box_a[5] >= box_b[2]
    )


def tight_face_bounds(face: Any) -> list[float]:
    """Geometric face bounds, without the broad-phase tolerance padding."""
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(face, box, False, False)
    return list(box.Get())


def trimmed_face_surface(face: Any) -> tuple[Any, list[float]]:
    """Share one clamped UV domain between detection and surface export."""
    surface = BRep_Tool.Surface_s(face)
    u_min, u_max, v_min, v_max = BRepTools.UVBounds_s(face)

    # Clamp the face domain against finite support-surface limits.
    s_u_min, s_u_max, s_v_min, s_v_max = surface.Bounds()
    u_min, u_max = max(u_min, s_u_min), min(u_max, s_u_max)
    v_min, v_max = max(v_min, s_v_min), min(v_max, s_v_max)

    trimmed_surface = Geom_RectangularTrimmedSurface(
        surface,
        float(u_min),
        float(u_max),
        float(v_min),
        float(v_max),
    )
    return trimmed_surface, [float(u_min), float(u_max), float(v_min), float(v_max)]
