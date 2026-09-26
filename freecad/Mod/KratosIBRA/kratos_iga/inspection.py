"""P0-4 pre-export inspection: CAD diagnostics and preprocessing expectations.

Pure stdlib, no OCP and no Kratos, so the same code runs in FreeCAD, in the OCP
backend worker and in the test suite. It owns exactly two things:

* ``cad_diagnostics`` -- read-only checks of the converted NURBS data (finite
  weights/knots/degrees, at least one positive-length span per axis). Repeated
  knots are a valid multiplicity and are only reported, never repaired. Malformed
  data is returned as errors; the caller rejects the export. Nothing here modifies
  the CAD document, so ``geometry_sha256`` and the invalidation protection are
  untouched.
* ``preprocessing_report`` -- the additive ``export_report.json`` block that keeps
  the analysis refinement, the requested quadrature and the display sampling as
  separate fields, and reports the independent OCCT source area per active patch
  (scaled with the length unit squared).

A cache whose ``index.json`` predates the source-area field is reported as
``unknown``/``reimport_required``. It is never silently treated as verified.
"""
from __future__ import annotations

import math
from collections import Counter

from .groups import final_grid

# unit system -> (length unit, area unit, area = mm^2 * scale)
UNITS_AREA = {"mm-N-s": ("mm", "mm^2", 1.0), "m-N-s": ("m", "m^2", 1.0e-6)}
SOURCE_AREA_SOURCE = ("OCCT BRepGProp.SurfaceProperties_s on the converter's face object, "
                      "identified by the same STEP traversal id")
SOURCE_AREA_UNKNOWN_NOTE = ("This geometry cache predates the source-area field. Reimport the "
                            "source STEP and rebuild the cache; the analysis area is NOT verified.")
DISPLAY_SAMPLING_NOTE = ("Display sampling only. It does not change analysis degree elevation, "
                         "knot insertion or the requested quadrature.")


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite(value):
    return _is_number(value) and math.isfinite(value)


def positive_span_count(knots):
    """Positive-length spans between distinct knot values, or ``None`` if malformed."""
    if not isinstance(knots, (list, tuple)) or len(knots) < 2:
        return None
    if any(not _finite(knot) for knot in knots):
        return None
    if any(b < a for a, b in zip(knots, knots[1:])):
        return None
    return sum(1 for a, b in zip(knots, knots[1:]) if b > a)


def _repeated_knots(knots):
    """Multiplicity map for a well-formed finite non-decreasing knot vector.

    Malformed input (non-sequence, non-finite, mixed types, decreasing) returns an
    empty map instead of sorting values that cannot be compared.
    """
    if not isinstance(knots, (list, tuple)) or not knots:
        return {}
    if any(not _finite(knot) for knot in knots):
        return {}
    if any(b < a for a, b in zip(knots, knots[1:])):
        return {}
    counts = Counter(knots)
    return {str(knot): count
            for knot, count in sorted(counts.items(), key=lambda item: item[0]) if count > 1}


def _weights(control_points):
    """Yield the fourth (weight) component of each control point.

    Handles both ``[id, [x, y, z, w]]`` and a flat ``[x, y, z, w]`` record. A
    record with fewer than four components is non-rational and carries weight 1.
    """
    for record in control_points or []:
        if not isinstance(record, (list, tuple)) or not record:
            yield None
            continue
        value = record[-1] if isinstance(record[-1], (list, tuple)) else record
        if isinstance(value, (list, tuple)) and len(value) >= 4:
            yield value[3]
        else:
            yield 1.0


def _knot_errors(label, knots, direction):
    errors = []
    if not isinstance(knots, (list, tuple)) or len(knots) < 2:
        errors.append(f"{label}: the {direction} knot vector must list at least two knots.")
        return errors, None
    if any(not _finite(knot) for knot in knots):
        errors.append(f"{label}: the {direction} knot vector has a non-finite value.")
        return errors, None
    if any(b < a for a, b in zip(knots, knots[1:])):
        errors.append(f"{label}: the {direction} knot vector is not non-decreasing.")
        return errors, None
    spans = positive_span_count(knots)
    if not spans:
        errors.append(f"{label}: the {direction} knot vector has no positive-length span.")
    return errors, spans


def _weight_errors(label, control_points):
    if not isinstance(control_points, (list, tuple)) or not control_points:
        return [f"{label}: the control point list must be a non-empty list."]
    errors = []
    for i, weight in enumerate(_weights(control_points)):
        if weight is None or not _finite(weight):
            errors.append(f"{label}: control point {i} has a non-finite weight.")
        elif weight <= 0:
            errors.append(f"{label}: control point {i} has a non-positive weight ({weight!r}).")
    return errors


def _curve_diagnostics(label, curve, direction="parameter"):
    if not isinstance(curve, dict):
        return [f"{label}: the {direction} curve must be an object."]
    errors = []
    degree = curve.get("degree")
    if not isinstance(degree, int) or isinstance(degree, bool) or degree < 1:
        errors.append(f"{label}: the {direction} curve degree must be a positive integer.")
    errors.extend(_knot_errors(label, curve.get("knot_vector"), direction)[0])
    errors.extend(_weight_errors(label, curve.get("control_points")))
    return errors


def _surface_diagnostics(face_id, surface):
    label = f"face {face_id}"
    errors = []
    degrees = surface.get("degrees")
    if (not isinstance(degrees, (list, tuple)) or len(degrees) != 2
            or any(not isinstance(d, int) or isinstance(d, bool) or d < 1 for d in degrees)):
        errors.append(f"{label}: the surface degrees must be two positive integers, got {degrees!r}.")
    knots = surface.get("knot_vectors")
    spans = [None, None]
    if not isinstance(knots, (list, tuple)) or len(knots) != 2:
        errors.append(f"{label}: the surface must carry exactly two knot vectors.")
    else:
        for axis, direction in enumerate(("u", "v")):
            axis_errors, axis_spans = _knot_errors(label, knots[axis], direction)
            errors.extend(axis_errors)
            spans[axis] = axis_spans
    errors.extend(_weight_errors(label, surface.get("control_points")))
    info = {
        "degrees": list(degrees) if isinstance(degrees, (list, tuple)) else degrees,
        "positive_knot_spans": spans,
        "control_points": len(surface.get("control_points") or []),
        "repeated_knots": {
            "u": _repeated_knots(knots[0]) if isinstance(knots, (list, tuple)) and len(knots) == 2 else {},
            "v": _repeated_knots(knots[1]) if isinstance(knots, (list, tuple)) and len(knots) == 2 else {},
        },
    }
    return errors, info


def cad_diagnostics(cad):
    """Read-only diagnostics of every converted face and trimming curve.

    Returns ``{"errors": [...], "faces": {...}, "repeated_knots_present": bool}``.
    Errors mean malformed data: the caller must reject the export, never repair the
    geometry or the weights. A non-object CAD document is rejected explicitly.
    """
    if not isinstance(cad, dict):
        return {"errors": [f"The converted CAD document must be an object, got "
                           f"{type(cad).__name__}; reimport the source geometry."],
                "faces": {}, "repeated_knots_present": False}
    errors = []
    faces = {}
    repeated = False
    for brep in cad.get("breps", []):
        for face in brep.get("faces", []):
            face_id = face.get("brep_id")
            surface = face.get("surface")
            if not isinstance(surface, dict):
                errors.append(f"face {face_id}: the surface must be an object.")
                surface = {}
            face_errors, info = _surface_diagnostics(face_id, surface)
            errors.extend(face_errors)
            for loop in face.get("boundary_loops", []) or []:
                if not isinstance(loop, dict):
                    errors.append(f"face {face_id}: a boundary loop is not an object.")
                    continue
                for curve in loop.get("trimming_curves", []) or []:
                    if not isinstance(curve, dict):
                        errors.append(f"face {face_id}: a trimming curve is not an object.")
                        continue
                    label = f"face {face_id} trim {curve.get('trim_index')}"
                    errors.extend(_curve_diagnostics(label, curve.get("parameter_curve") or {}))
            for curve in face.get("embedded_edges", []) or []:
                if not isinstance(curve, dict):
                    errors.append(f"face {face_id}: an embedded edge is not an object.")
                    continue
                label = f"face {face_id} embedded edge {curve.get('trim_index')}"
                errors.extend(_curve_diagnostics(label, curve.get("parameter_curve") or {}))
            for loop in face.get("embedded_loops", []) or []:
                if not isinstance(loop, dict):
                    errors.append(f"face {face_id}: an embedded loop is not an object.")
                    continue
                for curve in loop.get("trimming_curves", []) or []:
                    if not isinstance(curve, dict):
                        errors.append(f"face {face_id}: an embedded loop trim is not an object.")
                        continue
                    label = f"face {face_id} embedded loop trim {curve.get('trim_index')}"
                    errors.extend(_curve_diagnostics(label, curve.get("parameter_curve") or {}))
            repeated = repeated or any(info["repeated_knots"]["u"].values()) \
                or any(info["repeated_knots"]["v"].values())
            faces[str(face_id)] = info
    return {"errors": errors, "faces": faces, "repeated_knots_present": repeated}


def source_area_expectations(project, index):
    """Per active patch independent source area, in the project's export unit.

    ``state`` is ``"measured"`` when the cache carries ``source_area_mm2`` for the
    face, otherwise ``"unknown"`` with ``reimport_required``. A legacy cache never
    produces a fabricated number.
    """
    units = project.get("units", "mm-N-s")
    _length_unit, area_unit, scale = UNITS_AREA.get(units, ("mm", "mm^2", 1.0))
    faces = {f.get("id"): f for f in index.get("faces", [])}
    expectations = []
    for patch in project.get("patches", []):
        if not patch.get("active", True):
            continue
        face = faces.get(patch.get("face_id"), {})
        raw = face.get("source_area_mm2")
        if _finite(raw) and raw > 0:
            expectations.append({"face_id": patch["face_id"], "state": "measured",
                                 "source_area_mm2": float(raw), "source_area": float(raw) * scale,
                                 "source_area_units": area_unit, "reimport_required": False})
        else:
            expectations.append({"face_id": patch["face_id"], "state": "unknown",
                                 "source_area_mm2": None, "source_area": None,
                                 "source_area_units": area_unit, "reimport_required": True})
    return expectations


def preprocessing_report(project, cad, index, diagnostics=None):
    """Assemble the additive ``export_report.json`` preprocessing block."""
    units = project.get("units", "mm-N-s")
    length_unit, area_unit, scale = UNITS_AREA.get(units, ("mm", "mm^2", 1.0))
    expectations = {entry["face_id"]: entry for entry in source_area_expectations(project, index)}
    index_faces = {f.get("id"): f for f in index.get("faces", [])}
    cad_faces = {}
    for brep in (cad.get("breps", []) if isinstance(cad, dict) else []):
        for face in brep.get("faces", []):
            cad_faces[face.get("brep_id")] = face
    if diagnostics is None:
        diagnostics = cad_diagnostics(cad)

    refinement_enabled = bool(project.get("refinement_enabled", True))
    patches = []
    for patch in project.get("patches", []):
        if not patch.get("active", True):
            continue
        face_id = patch["face_id"]
        index_face = index_faces.get(face_id, {})
        cad_face = cad_faces.get(face_id, {})
        surface = cad_face.get("surface", {})
        counts = index_face.get("control_point_counts") or {}
        spans = index_face.get("knot_spans") or {}
        grid = final_grid(index, face_id, patch, refinement_enabled)
        expectation = expectations.get(face_id, {})
        original_degrees = surface.get("degrees")
        original_degrees = list(original_degrees) if isinstance(original_degrees, (list, tuple)) \
            and len(original_degrees) == 2 else None
        requested_degree = [patch.get("degree_u"), patch.get("degree_v")]
        requested_insert = [patch.get("insert_u"), patch.get("insert_v")]
        # With refinement disabled RefinementModeler does not run, so the exported
        # target is the original degree with zero insertions. The raw project request
        # stays visible in its own fields.
        target_degree = requested_degree if refinement_enabled else original_degrees
        insert_per_span = requested_insert if refinement_enabled else [0, 0]
        patches.append({
            "face_id": face_id,
            "source_area": expectation.get("source_area"),
            "source_area_mm2": expectation.get("source_area_mm2"),
            "source_area_units": area_unit,
            "source_area_state": expectation.get("state", "unknown"),
            "original_degree": original_degrees,
            "original_control_points": {"u": counts.get("u"), "v": counts.get("v"),
                                        "total": counts.get("total")},
            "original_positive_knot_spans": {"u": spans.get("u"), "v": spans.get("v")},
            "target_degree": target_degree,
            "insert_per_span": insert_per_span,
            "requested_target_degree": requested_degree,
            "requested_insert_per_span": requested_insert,
            "final_grid_control_points": list(grid) if grid else None,
            "final_grid_is_estimate": refinement_enabled,
            "requested_quadrature_per_span": patch.get("quadrature"),
        })

    measured = bool(patches) and all(p["source_area_state"] == "measured" for p in patches)
    return {
        "units": units,
        "length_unit": length_unit,
        "area_unit": area_unit,
        "area_scale_from_mm2": scale,
        "active_patch_ids": [p["face_id"] for p in patches],
        "source_area": {
            "state": "measured" if measured else "unknown",
            "source": SOURCE_AREA_SOURCE,
            "reimport_required": not measured,
            "note": ("Each active patch source area is the trimmed OCCT face area, scaled by the "
                     "length unit squared." if measured else SOURCE_AREA_UNKNOWN_NOTE),
        },
        "patches": patches,
        "analysis_refinement": {
            "enabled": refinement_enabled,
            "note": ("Target degree and knot insertions applied by RefinementModeler; "
                     "final_grid_control_points is a nominal estimate of the exported control net."
                     if refinement_enabled else
                     "Refinement is disabled; the original knot vectors are exported unchanged."),
        },
        "quadrature": {
            "requested_per_span": {str(p["face_id"]): p["requested_quadrature_per_span"] for p in patches},
            "note": ("Requested Gauss points per knot span; 0 selects the modeler default. "
                     "Actual quadrature counts are reported by MainKratos --check after Initialize."),
        },
        "display_sampling": {
            "refinement": project.get("output", {}).get("refinement"),
            "note": DISPLAY_SAMPLING_NOTE,
        },
        "diagnostics": diagnostics,
    }
