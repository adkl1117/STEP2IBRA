"""Run with the OCP virtualenv; never import OCP in FreeCAD itself."""
import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kratos_iga.project import read_json, write_json


def surface_point(surface, uv):
    import numpy as np
    from cad_pipeline.nurbs import find_span, basis_functions
    p, q = surface["degrees"]
    ku, kv = surface["knot_vectors"]
    nu, nv = len(ku)-p-1, len(kv)-q-1
    su, sv = find_span(nu-1, p, uv[0], ku), find_span(nv-1, q, uv[1], kv)
    bu, bv = basis_functions(su, uv[0], p, ku), basis_functions(sv, uv[1], q, kv)
    total, weight = np.zeros(3), 0.
    for j in range(q+1):
        for i in range(p+1):
            cp = surface["control_points"][(sv-q+j)*nu + su-p+i][1]
            w = float(bu[i]*bv[j]*cp[3])
            total += np.asarray(cp[:3])*w
            weight += w
    return (total/weight).tolist()


def source_face_area_mm2(face, face_id=None):
    """Trimmed area (mm^2) of the converter's own OCCT face object.

    Uses the same ``TopExp_Explorer`` traversal that assigns the converter ids, so
    the number belongs to exactly the face whose ``source_area_mm2`` is recorded.
    A non-finite or non-positive area is malformed source geometry and raises.
    """
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, properties)
    raw = float(properties.Mass())
    if not math.isfinite(raw) or raw <= 0.0:
        label = f"face {face_id}" if face_id is not None else "the source face"
        raise ValueError(f"OCCT reports a non-finite or non-positive area for {label}; "
                         "the source geometry is malformed and cannot be exported.")
    return abs(raw)


def _trim_record(face, trim_index):
    """Return (trimming_curve_record, origin) for a face-local trim index."""
    collections = (
        ("boundary_loop", [c for loop in face.get("boundary_loops", []) for c in loop.get("trimming_curves", [])]),
        ("embedded_edge", list(face.get("embedded_edges", []))),
        ("embedded_loop", [c for loop in face.get("embedded_loops", []) for c in loop.get("trimming_curves", [])]),
    )
    for origin, collection in collections:
        for record in collection:
            if record["trim_index"] == trim_index:
                return record, origin
    return None, None


def _distance(a, b):
    return sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)) ** 0.5


def _positive_spans(knots):
    """Number of positive-length knot spans (endpoint repetitions excluded).

    ``RefinementModeler`` inserts a fixed number of knots per *span*, so this count is
    what turns ``n_final = n + degree_delta*spans + inserts*spans`` into the final
    control-net size used by P0-3 strong-enforcement overlap checks.
    """
    interior = [knot for knot in knots if knots[0] < knot < knots[-1]]
    return len(set(interior)) + 1


def _direction(points, reference):
    """Sampled pcurve direction relative to the shared 3D curve."""
    if reference is None or len(points) < 3:
        return None
    mid = len(points) // 2
    tangent = [float(points[mid + 1][i]) - float(points[mid - 1][i]) for i in range(3)]
    along = [float(reference[mid + 1][i]) - float(reference[mid - 1][i]) for i in range(3)]
    dot = sum(tangent[i] * along[i] for i in range(3))
    if abs(dot) < 1.0e-30:
        return None
    return "forward" if dot > 0 else "reversed"


_COINCIDENT_EPS = 1.0e-12


def _segment_distance(point, start, end):
    ax, ay, az = (float(v) for v in start)
    bx, by, bz = (float(v) for v in end)
    px, py, pz = (float(v) for v in point)
    dx, dy, dz = bx - ax, by - ay, bz - az
    length2 = dx * dx + dy * dy + dz * dz
    if length2 <= 0.0:
        return _distance(point, start)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy + (pz - az) * dz) / length2))
    return _distance(point, [ax + t * dx, ay + t * dy, az + t * dz])


def _nearest_segment_gap(source, target):
    """Bounded arc-length spatial approximation, independent of parameterization."""
    return max(min(_segment_distance(p, target[i], target[i + 1]) for i in range(len(target) - 1))
               for p in source)


def _evaluate_curve_3d(curve, parameter, nurbs, np):
    """Evaluate a 3D NURBS curve. cad_pipeline.nurbs.evaluate_curve is 2D-only."""
    degree = int(curve["degree"])
    knots = np.asarray(curve["knot_vector"], dtype=np.float64)
    records = curve["control_points"]
    values = [record[-1] if isinstance(record[-1], list) else record for record in records]
    points = np.asarray([[float(v) for v in value[:3]] for value in values], dtype=np.float64)
    weights = np.asarray([float(value[3]) if len(value) > 3 else 1.0 for value in values], dtype=np.float64)
    span = nurbs.find_span(len(points) - 1, degree, float(parameter), knots)
    basis = nurbs.basis_functions(span, float(parameter), degree, knots)
    selected = slice(span - degree, span + 1)
    rational = basis * weights[selected]
    return (rational[:, np.newaxis] * points[selected]).sum(axis=0) / rational.sum()


def coupling_diagnostics(face_map, edge, nurbs, np, sample_count=41,
                         tolerance=1.0e-3, geometry_sha256=""):
    """Sample BOTH pcurves on their own source surfaces and pair them.

    The result is a sample statement: a paired spatial gap plus the pcurve
    directions. It does not prove whole-edge coincidence, tangency or mechanical
    intent. The index owns this record; the project owns the decision.
    """
    evaluate_curve = nurbs.evaluate_curve
    reference, reference_error = None, None
    if "3d_curve" not in edge:
        reference_error = "the interface has no shared 3D curve"
    else:
        values = edge["3d_curve"].get("active_range") or [None, None]
        first, last = values[0], values[1]
        if not (isinstance(first, (int, float)) and isinstance(last, (int, float))
                and math.isfinite(first) and math.isfinite(last) and last > first):
            reference_error = "the shared 3D curve has a zero or non-finite active range"
        else:
            reference = [_evaluate_curve_3d(edge["3d_curve"], t, nurbs, np)
                         for t in np.linspace(first, last, sample_count)]
    topology = edge["topology"]
    status, detail = "ok", ""
    if len({(s["brep_id"], s["trim_index"]) for s in topology}) != len(topology):
        status, detail = "invalid", "the interface references the same face/trim twice"
    sides, sampled, directions = [], [], []
    for side in topology:
        entry = {"face_id": side["brep_id"], "trim_index": side["trim_index"],
                 "curve_origin": None, "direction": None, "curve_error": None, "degenerate": True}
        face = face_map.get(side["brep_id"])
        if face is None:
            status, detail = "missing", detail or f"missing face {side['brep_id']}"
            sides.append(entry)
            continue
        record, origin = _trim_record(face, side["trim_index"])
        entry["curve_origin"] = origin
        if record is None:
            status, detail = "missing", detail or f"missing trim {side['trim_index']} on face {side['brep_id']}"
            sides.append(entry)
            continue
        curve = record.get("parameter_curve") or {}
        values = curve.get("active_range") or [None, None]
        first, last = values[0], values[1]
        if not (isinstance(first, (int, float)) and isinstance(last, (int, float))
                and math.isfinite(first) and math.isfinite(last) and last > first):
            status, detail = "degenerate", detail or f"trim {side['trim_index']} has a zero or non-finite parameter span"
            sides.append(entry)
            continue
        try:
            points = [surface_point(face["surface"], evaluate_curve(curve, float(t)))
                      for t in np.linspace(first, last, sample_count)]
        except Exception as exc:  # defensive: reported as evidence, never hidden
            status, detail = "degenerate", detail or f"trim {side['trim_index']} could not be evaluated: {type(exc).__name__}"
            sides.append(entry)
            continue
        if len(points) < 2 or any(not all(math.isfinite(float(v)) for v in point) for point in points):
            status, detail = "degenerate", detail or f"trim {side['trim_index']} yielded non-finite samples"
            sides.append(entry)
            continue
        if max(_distance(points[0], point) for point in points) <= _COINCIDENT_EPS:
            status, detail = "degenerate", detail or f"all samples of trim {side['trim_index']} coincide"
            sides.append(entry)
            continue
        entry["degenerate"] = False
        entry["direction"] = _direction(points, reference)
        if reference is not None:
            # Spatial, parameterization-independent: the pcurve's sample points
            # against the shared 3D polyline. Index-wise comparison would smuggle
            # a parameter assumption into a "curve error".
            entry["curve_error"] = float(_nearest_segment_gap(points, reference))
        sampled.append(points)
        directions.append(entry["direction"])
        sides.append(entry)
    max_curve = max((s["curve_error"] for s in sides if s["curve_error"] is not None), default=None)
    max_gap = parameter_mismatch = nearest = None
    param_correspondence, orientation_source = "unknown", None
    if status == "ok" and reference is None:
        status, detail = "correspondence_unknown", reference_error
    if status == "ok" and len(sampled) == 2 and all(d is not None for d in directions):
        aligned = directions[0] == directions[1]
        forward = max(_distance(sampled[0][i], sampled[1][i]) for i in range(sample_count))
        reverse = max(_distance(sampled[0][i], sampled[1][sample_count - 1 - i]) for i in range(sample_count))
        # The pairing follows the exported direction convention; the opposite pairing
        # is reported separately as a parameter-correspondence number, never as a gap.
        max_gap = float(forward if aligned else reverse)
        parameter_mismatch = float(reverse if aligned else forward)
        param_correspondence = ("index_aligned_same_direction" if aligned
                                else "index_aligned_opposite_direction")
        orientation_source = "pcurve_direction_vs_shared_3d_curve"
        nearest = float(max(_nearest_segment_gap(sampled[0], sampled[1]),
                            _nearest_segment_gap(sampled[1], sampled[0])))
    elif status == "ok":
        status, detail = "correspondence_unknown", "pcurve direction against the shared 3D curve is not resolvable"
    if status == "ok" and nearest is not None and nearest > tolerance:
        status, detail = "mismatch", (f"nearest-segment spatial approximation {nearest:.3g} mm "
                                      f"exceeds tolerance {tolerance:.3g} mm")
    if status == "ok" and max_curve is not None and max_curve > tolerance:
        status, detail = "mismatch", f"a pcurve differs from the shared 3D curve by {max_curve:.3g} mm"
    if (status == "ok" and nearest is not None and max_gap is not None
            and max_gap > tolerance and nearest <= tolerance):
        status, detail = "parameter_correspondence_mismatch", (
            f"the exported direction convention pairs points {max_gap:.3g} mm apart although the "
            f"nearest-segment distance is {nearest:.3g} mm; the parameter correspondence is unresolved")
    if status == "ok" and len(sides) != 2:
        status, detail = "missing", "the interface needs two evaluable trimming curves"
    return {"geometry_sha256": geometry_sha256, "sample_count": sample_count, "tolerance": float(tolerance),
            "max_paired_gap": max_gap, "parameter_mismatch": parameter_mismatch,
            "max_nearest_segment_gap": nearest, "param_correspondence": param_correspondence,
            "orientation_source": orientation_source, "max_curve_error": max_curve, "sides": sides,
            "status": status, "detail": detail or "sampled on both source surfaces",
            "note": ("sampled; the nearest-segment gap is an approximation and this is not "
                     "whole-edge or mechanical proof")}


def build_index(cad, report, folder):
    import numpy as np
    from cad_pipeline import nurbs
    from cad_pipeline.nurbs import evaluate_curve
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from cad_pipeline.step_io import read_step_file
    # Re-read using the SAME reader and traversal as the converter, tying IDs to
    # original OCCT faces. FreeCAD import order / FaceN is never used as an ID.
    shape = read_step_file(Path(report["input"]))
    traversal = TopExp_Explorer(shape, TopAbs_FACE)
    face_objects = {}
    for record in report["faces"]:
        if not traversal.More():
            raise ValueError("STEP traversal no longer agrees with conversion report")
        face_objects[record["face_id"]] = traversal.Current()
        traversal.Next()
    if traversal.More():
        raise ValueError("STEP face count changed")
    faces = {f["brep_id"]: f for b in cad["breps"] for f in b["faces"]}
    index = {"source_sha256": report["input_sha256"],
             "geometry_sha256": hashlib.sha256(json.dumps(cad, sort_keys=True).encode()).hexdigest(), "faces": [], "edges": [],
             # P0-4 additive metadata: the cache built by this version always carries
             # a measured source area; older caches are detected by the missing field.
             "preprocessing_metadata": {"version": 1, "source_face_area_field": "faces[].source_area_mm2",
                 "source_face_area_units": "mm^2",
                 "source_face_area_source": "OCCT BRepGProp.SurfaceProperties_s on the converter face object"}}
    for fid, face in faces.items():
        filename = f"face_{fid}.brep"
        if not BRepTools.Write_s(face_objects[fid], str(folder/filename)):
            raise RuntimeError("Cannot write OCCT BREP")
        surface = face["surface"]
        ku, kv = surface["knot_vectors"]
        degrees = surface["degrees"]
        # Independent OCP source expectation for P0-4: the trimmed face area of the
        # same face object the converter's traversal produced. Additive field; a cache
        # built before this field is reported as unknown and must be rebuilt.
        source_area_mm2 = source_face_area_mm2(face_objects[fid], fid)
        index["faces"].append({"id": fid, "brep_file": filename, "degrees": degrees,
            "source_area_mm2": source_area_mm2,
            "min_continuity": [min((degree-count for knot, count in Counter(knots).items() if knots[0] < knot < knots[-1]), default=99)
                               for degree, knots in zip(degrees, surface["knot_vectors"])],
            # The integer total is kept for backward compatibility; the additive
            # per-axis counts and span counts let P0-3 resolve control-net rows/columns
            # exactly instead of guessing a factorization.
            "control_points": len(surface["control_points"]),
            "control_point_counts": {"u": len(ku)-degrees[0]-1, "v": len(kv)-degrees[1]-1,
                                     "total": len(surface["control_points"])},
            "knot_spans": {"u": _positive_spans(ku), "v": _positive_spans(kv)},
            "corners": {key: surface_point(surface, uv) for key, uv in {
                "00": [ku[0], kv[0]], "10": [ku[-1], kv[0]], "01": [ku[0], kv[-1]], "11": [ku[-1], kv[-1]]}.items()}})
    for brep in cad["breps"]:
        for edge in brep["edges"]:
            topology = edge["topology"]
            side = topology[0]
            face = faces[side["brep_id"]]
            curves = [c for loop in face["boundary_loops"] for c in loop["trimming_curves"]] + face.get("embedded_edges", [])
            curve_record = next((c for c in curves if c["trim_index"] == side["trim_index"]), None)
            if curve_record is None:
                # Converter stores generated coupling trims in embedded loops.
                curves += [c for loop in face.get("embedded_loops", []) for c in loop.get("trimming_curves", [])]
                curve_record = next(c for c in curves if c["trim_index"] == side["trim_index"])
            curve = curve_record["parameter_curve"]
            a, b = curve["active_range"]
            uv = [evaluate_curve(curve, float(t)) for t in np.linspace(a, b, 41)]
            points = [surface_point(face["surface"], pair) for pair in uv]
            ku, kv = face["surface"]["knot_vectors"]
            strong = None
            # Convex-hull test on ALL rational curve poles: not a sampled guess.
            poles = [c[1] for c in curve["control_points"]]
            for axis, knot, other in ((0, ku, kv), (1, kv, ku)):
                eps = max(1., abs(knot[-1]-knot[0]))*1e-9
                for end in (0, 1):
                    constant = knot[0] if end == 0 else knot[-1]
                    if all(abs(cp[axis]-constant) < eps for cp in poles):
                        extent = sorted([uv[0][1-axis], uv[-1][1-axis]])
                        if abs(extent[0]-other[0]) < eps and abs(extent[1]-other[-1]) < eps:
                            strong = [end, -1] if axis == 0 else [-1, end]
            index["edges"].append({"id": edge["brep_id"], "kind": "coupling" if len(topology) == 2 else "edge",
                "face_ids": [s["brep_id"] for s in topology], "points": points,
                "strong_local": strong if len(topology) == 1 else None,
                "diagnostics": coupling_diagnostics(faces, edge, nurbs, np,
                    tolerance=float(cad.get("tolerances", {}).get("model_tolerance", 1.0e-3)),
                    geometry_sha256=index["geometry_sha256"]) if len(topology) == 2 else None})
    return index


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("request")
    args = parser.parse_args()
    request = read_json(args.request)
    pipeline = Path(request["pipeline_root"]).resolve()
    if not (pipeline/"cad_pipeline"/"api.py").is_file():
        raise ValueError("Select the conversion pipeline directory containing cad_pipeline.")
    sys.path.insert(0, str(pipeline))
    action = request["action"]
    if action == "convert":
        from cad_pipeline.api import convert_step
        from cad_pipeline.config import ConversionOptions
        source = Path(request["source_step"]).resolve(strict=True)
        if source.suffix.lower() not in (".step", ".stp"):
            raise ValueError("Only STEP/STP input is supported")
        options = ConversionOptions.from_file(Path(request["profile"])) if request.get("profile") else ConversionOptions()
        folder = Path(request["cache_dir"])
        folder.mkdir(parents=True, exist_ok=True)
        cad, report = convert_step(source, options)
        index = build_index(cad, report, folder)
        if hashlib.sha256(source.read_bytes()).hexdigest() != report["input_sha256"]:
            raise ValueError("The STEP file changed during conversion. Reimport the source geometry.")
        write_json(folder/"geometry.cad.json", cad)
        write_json(folder/"geometry.cad.report.json", report)
        write_json(folder/"index.json", index)
        print(f"Converted: {len(index['faces'])} faces, {len(index['edges'])} boundaries/interfaces", flush=True)
    elif action == "export":
        from kratos_iga.bundle import export_bundle
        project = read_json(request["project_file"])
        source = Path(project["source_step"])
        if hashlib.sha256(source.read_bytes()).hexdigest() != project["source_sha256"]:
            raise ValueError("The STEP file has changed. Reimport it before exporting the model.")
        folder = Path(request["cache_dir"])
        report = export_bundle(project, read_json(folder/"geometry.cad.json"), read_json(folder/"index.json"), request["destination"])
        print(json.dumps(report, ensure_ascii=False), flush=True)
    else:
        raise ValueError("Unknown worker action")


if __name__ == "__main__":
    main()
