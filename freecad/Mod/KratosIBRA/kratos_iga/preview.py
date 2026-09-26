"""P0-4 inspectable preprocessing: pure preview geometry, statistics and glyphs.

This module is the single place that turns the *current* project assignment
(``project`` + ``index`` + cached ``geometry.cad.json``) into:

* NURBS surface samples and analytic frames (``surface_frame``) evaluated from the
  cached CAD surface — never from a display triangulation and never through OCP or
  Kratos;
* support / load / background-frame glyph specifications;
* material and section legend rows with the exact current references;
* the separated analysis-refinement / quadrature / display-sampling report and the
  zero-span / non-positive-weight diagnostics.

No ``OCP`` and no ``KratosMultiphysics`` import happens here. ``FreeCAD`` and
``Part`` are imported lazily inside :class:`PreviewLayer` only, so every pure
function runs unmodified under the backend venv and under FreeCAD's Python.

The module is display-only: it never writes ``geometry.cad.json``, never adds a
hash/manifest, and never mutates the project. Geometry-changing diagnostics are
reported, never silently repaired.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .groups import (SIDES, edge_is_active, expand, final_grid, resolve, row_locator,
                     target_locations)

try:  # the runtime helper may be a plain file in the runtime/ folder (no package)
    from .runtime import ibra_amplitude
except ImportError:  # pragma: no cover - package layout only
    import importlib.util as _importlib_util
    _AMPLITUDE_PATH = Path(__file__).resolve().parent / "runtime" / "ibra_amplitude.py"
    _amplitude_spec = _importlib_util.spec_from_file_location(
        "kratos_iga_preview_ibra_amplitude", _AMPLITUDE_PATH)
    ibra_amplitude = _importlib_util.module_from_spec(_amplitude_spec)
    _amplitude_spec.loader.exec_module(ibra_amplitude)

EPS = 1.0e-12
# Length of the longest load arrow, as a fraction of the model bounding-box diagonal.
MAX_ARROW_FRACTION = 0.18
BOUNDARY_SAMPLES = 17
AXIS_COLORS = {"X": (0.86, 0.20, 0.18), "Y": (0.20, 0.62, 0.24), "Z": (0.20, 0.32, 0.86)}
PALETTE = (
    (0.93, 0.84, 0.55), (0.88, 0.72, 0.52), (0.62, 0.78, 0.62), (0.86, 0.62, 0.66),
    (0.72, 0.68, 0.86), (0.60, 0.74, 0.80), (0.84, 0.82, 0.58), (0.70, 0.70, 0.70),
)
STRONG_COLOR = (0.14, 0.56, 0.32)
PENALTY_COLOR = (0.18, 0.38, 0.78)
INACTIVE_COLOR = (0.55, 0.55, 0.55)
CLAMP_COLOR = (0.92, 0.55, 0.12)
FRAME_U_COLOR = (0.86, 0.20, 0.18)
FRAME_V_COLOR = (0.20, 0.62, 0.24)
FRAME_N_COLOR = (0.16, 0.22, 0.72)
LOAD_COLORS = {"LINE_LOAD": (0.90, 0.40, 0.10), "SURFACE_LOAD": (0.20, 0.50, 0.85),
               "DEAD_LOAD": (0.55, 0.30, 0.75)}
PRESSURE_COLOR = (0.82, 0.20, 0.36)
ZERO_COLOR = (0.85, 0.15, 0.60)


# --------------------------------------------------------------------------- #
# unit labels
# --------------------------------------------------------------------------- #

def unit_labels(units):
    """Display units for the two supported systems (force N and time s fixed)."""
    if units == "m-N-s":
        return {"length": "m", "stress": "N/m^2", "density": "N*s^2/m^4", "line_load": "N/m",
                "LINE_LOAD": "N/m", "SURFACE_LOAD": "N/m^2",
                "DEAD_LOAD": "N/m (edge) / N/m^2 (face)"}
    return {"length": "mm", "stress": "N/mm^2", "density": "t/mm^3", "line_load": "N/mm",
            "LINE_LOAD": "N/mm", "SURFACE_LOAD": "N/mm^2",
            "DEAD_LOAD": "N/mm (edge) / N/mm^2 (face)"}


def load_dimension(variable, kind):
    """The physical dimension actually consumed by the target, per ``project.py``.

    ``convert_project_units`` treats an edge ``DEAD_LOAD`` as a line load and a face
    ``DEAD_LOAD`` as a stress, so the preview must not label every DEAD_LOAD "N".
    """
    if variable == "LINE_LOAD" or (variable == "DEAD_LOAD" and kind == "edge"):
        return "line_load"
    return "stress"


def load_family(variable, kind):
    """A scale family keyed by the actual dimension; DEAD_LOAD edge/face split."""
    if variable == "DEAD_LOAD":
        return "DEAD_LOAD_EDGE" if kind == "edge" else "DEAD_LOAD_FACE"
    return variable


def load_units(units):
    labels = unit_labels(units)
    return {"LINE_LOAD": labels["line_load"], "SURFACE_LOAD": labels["stress"],
            "DEAD_LOAD_EDGE": labels["line_load"], "DEAD_LOAD_FACE": labels["stress"]}


def is_pressure(record):
    """True for the additive ``reference_pressure`` record (legacy vectors have no kind)."""
    return isinstance(record, dict) and record.get("kind") == "reference_pressure"


def amplitude_metadata(record):
    """Read-only amplitude-table metadata for labels (never implies a current time).

    The preview draws the *base* pressure at factor 1 and reports the stored knots and
    domain; it never substitutes an interpolated value without a time readout.
    """
    amplitude = record.get("amplitude")
    if amplitude is None:
        return {"present": False, "valid": True, "points": [], "knots": [], "domain": None,
                "note": "no amplitude table: constant factor 1 over the active interval"}
    if not isinstance(amplitude, dict) or "points" not in amplitude:
        return {"present": True, "valid": False, "points": [], "knots": [], "domain": None,
                "note": "amplitude is present but malformed (no points list); reported as "
                        "INVALID, not as constant 1"}
    try:
        rows = ibra_amplitude.parse(amplitude["points"])
    except Exception as error:  # noqa: BLE001 - the shared parser rejects and never repairs
        return {"present": True, "valid": False, "points": [], "knots": [], "domain": None,
                "note": f"amplitude is INVALID ({error}); reported, never repaired or treated "
                        "as constant 1"}
    knots = [[time, factor] for time, factor in rows]
    times = [time for time, _factor in rows]
    return {"present": True, "valid": True, "points": knots, "knots": times,
            "domain": [min(times), max(times)],
            "note": (f"piecewise-linear factor, stored knots t={times}; outside the table the "
                     "factor is 0. Preview draws the base load (factor 1), not a current-time "
                     "value; follow-up times are not implied.")}


def pressure_glyphs(project, index, cad, groups):
    """Reference-normal pressure arrows sampled pointwise from the cached CAD NURBS.

    Positive ``pressure`` follows the unit ``du x dv`` (the Kratos parameterization),
    ``normal_flip`` reverses it, and every sampled point gets its own normal so a curved
    face is never reduced to one centre normal. Only faces/face groups are valid; an
    edge target is reported as an error instead of being drawn.
    """
    active = {patch["face_id"] for patch in project.get("patches", []) if patch.get("active", True)}
    surfaces = face_surface_map(cad)
    units = load_units(project.get("units", "mm-N-s"))
    glyphs = []
    for position, item in enumerate(project.get("loads", []), 1):
        if not is_pressure(item):
            continue
        group = groups.get(item.get("group_id")) if "group_id" in item else None
        target_kind = group["kind"] if group else item.get("target_kind")
        name = item.get("name", f"Pressure_{position}")
        flip_raw = item.get("normal_flip", False)
        flip_valid = type(flip_raw) is bool
        flip = flip_raw if flip_valid else False
        pressure_raw = item.get("pressure")
        pressure_valid = (type(pressure_raw) in (int, float) and math.isfinite(pressure_raw))
        pressure = float(pressure_raw) if pressure_valid else None
        # Magnitude sign: positive p follows +normal, negative p follows -normal, and
        # normal_flip independently reverses the reference direction.
        sign = (1.0 if (pressure is None or pressure >= 0.0) else -1.0) * (-1.0 if flip else 1.0)
        magnitude = abs(pressure) if pressure is not None else 0.0
        glyph = {"category": "load", "record": position, "name": name, "pressure": True,
                 "kind": "reference_pressure", "variable": None, "dimension": "stress",
                 "family": "SURFACE_LOAD", "value": None, "normal_flip": flip, "sign": sign,
                 "pressure_value": pressure, "magnitude": magnitude, "direction": None,
                 "unit": units["SURFACE_LOAD"], "interval": list(item.get("interval", [])),
                 "source": "direct", "missing": [], "targets": [], "blocked": None,
                 "amplitude": amplitude_metadata(item)}
        if not flip_valid:
            glyph["blocked"] = "normal_flip must be a JSON boolean (true/false)"
        if pressure is None:
            glyph["blocked"] = glyph["blocked"] or ("pressure must be a finite signed scalar; "
                                                    "booleans are not numbers")
        if glyph["blocked"]:
            glyphs.append(glyph)
            continue
        if target_kind != "face":
            glyph["blocked"] = ("reference pressure requires a face or face group target; "
                                "an edge target is not supported")
            glyphs.append(glyph)
            continue
        if pressure is None:
            glyph["blocked"] = "pressure must be a finite signed scalar"
            glyphs.append(glyph)
            continue
        resolved = resolve(item, groups, index)
        glyph["source"] = resolved["source"]
        glyph["missing"] = list(resolved["missing"])
        for ident in resolved["ids"]:
            face = surfaces.get(ident)
            if face is None:
                glyph["blocked"] = glyph["blocked"] or f"face {ident} is not in the cached CAD"
                continue
            surface = face["surface"]
            knots_u, knots_v = surface["knot_vectors"]
            anchors, arrows = [], []
            for fu in (0.2, 0.4, 0.6, 0.8):
                for fv in (0.2, 0.4, 0.6, 0.8):
                    u = float(knots_u[0]) + (float(knots_u[-1]) - float(knots_u[0])) * fu
                    v = float(knots_v[0]) + (float(knots_v[-1]) - float(knots_v[0])) * fv
                    try:
                        frame = surface_frame(surface, [u, v])
                    except ValueError:
                        continue
                    if frame["normal"] is None:
                        continue
                    anchors.append(frame["point"])
                    arrows.append({"point": frame["point"], "uv": [u, v],
                                   "direction": [sign * component for component in frame["normal"]]})
            glyph["targets"].append({
                "kind": "face", "id": ident, "active": ident in active,
                "anchors": anchors, "pressure_arrows": arrows,
                "outline": natural_boundary(surface, SIDES["all"]),
                "label": f"face {ident} (pointwise X0 reference normal u x v)",
            })
        glyphs.append(glyph)
    return glyphs


# The preview samples the cached CAD, whose coordinates are always millimetres, so a
# reported display/arrow length is in mm even when the project uses m-N-s.
DISPLAY_LENGTH_UNIT = "mm"


# --------------------------------------------------------------------------- #
# NURBS evaluation (point + analytic first derivatives)
# --------------------------------------------------------------------------- #

def _find_span(last_control_point, degree, parameter, knots):
    if parameter >= knots[last_control_point + 1]:
        return last_control_point
    if parameter <= knots[degree]:
        return degree
    low, high = degree, last_control_point + 1
    middle = (low + high) // 2
    while parameter < knots[middle] or parameter >= knots[middle + 1]:
        if parameter < knots[middle]:
            high = middle
        else:
            low = middle
        middle = (low + high) // 2
    return middle


def _basis_derivatives(span, parameter, degree, knots):
    """Return ``ders`` with ``ders[k][j]`` = k-th derivative of basis j (k=0,1).

    The NURBS Book algorithm A2.3 specialised to first derivatives; it is the
    analytic derivative of the same basis :mod:`cad_pipeline.nurbs` evaluates, so
    the preview frame is a NURBS derivative and not a finite difference.
    """
    ndu = [[0.0] * (degree + 1) for _ in range(degree + 1)]
    left = [0.0] * (degree + 1)
    right = [0.0] * (degree + 1)
    ndu[0][0] = 1.0
    for j in range(1, degree + 1):
        left[j] = parameter - knots[span + 1 - j]
        right[j] = knots[span + j] - parameter
        saved = 0.0
        for r in range(j):
            ndu[j][r] = right[r + 1] + left[j - r]
            temp = ndu[r][j - 1] / ndu[j][r] if ndu[j][r] != 0.0 else 0.0
            ndu[r][j] = saved + right[r + 1] * temp
            saved = left[j - r] * temp
        ndu[j][j] = saved
    ders = [[0.0] * (degree + 1) for _ in range(2)]
    for j in range(degree + 1):
        ders[0][j] = ndu[j][degree]
    a = [[0.0, 0.0], [0.0, 0.0]]
    for r in range(degree + 1):
        s1, s2 = 0, 1
        a[0][0] = 1.0
        for k in range(1, 2):
            d = 0.0
            rk, pk = r - k, degree - k
            if r >= k:
                a[s2][0] = a[s1][0] / ndu[pk + 1][rk]
                d = a[s2][0] * ndu[rk][pk]
            j1 = 1 if rk >= -1 else -rk
            j2 = k - 1 if r - 1 <= pk else degree - r
            for j in range(j1, j2 + 1):
                a[s2][j] = (a[s1][j] - a[s1][j - 1]) / ndu[pk + 1][rk + j]
                d += a[s2][j] * ndu[rk + j][pk]
            if r <= pk:
                a[s2][k] = -a[s1][k - 1] / ndu[pk + 1][r]
                d += a[s2][k] * ndu[r][pk]
            ders[k][r] = d
            s1, s2 = s2, s1
    factor = degree
    for k in range(1, 2):
        for j in range(degree + 1):
            ders[k][j] *= factor
        factor *= (degree - k)
    return ders


def _surface_arrays(surface):
    degree_u, degree_v = (int(value) for value in surface["degrees"])
    knots_u = np.asarray(surface["knot_vectors"][0], dtype=np.float64)
    knots_v = np.asarray(surface["knot_vectors"][1], dtype=np.float64)
    count_u = len(knots_u) - degree_u - 1
    count_v = len(knots_v) - degree_v - 1
    if count_u < 1 or count_v < 1:
        raise ValueError("the NURBS surface has fewer control points than its degree")
    if len(surface["control_points"]) != count_u * count_v:
        raise ValueError("the NURBS control-point count disagrees with its knot vectors")
    points = np.empty((count_u, count_v, 3), dtype=np.float64)
    weights = np.ones((count_u, count_v), dtype=np.float64)
    rational = bool(surface.get("is_rational", True))
    for j in range(count_v):
        for i in range(count_u):
            record = surface["control_points"][j * count_u + i]
            values = record[-1] if isinstance(record[-1], list) else record
            points[i, j] = [float(values[0]), float(values[1]), float(values[2])]
            weights[i, j] = float(values[3]) if rational and len(values) > 3 else 1.0
    return degree_u, degree_v, knots_u, knots_v, points, weights


def surface_frame(surface, uv):
    """Return the analytic rational NURBS frame at ``(u, v)``.

    ``point`` is ``S``, ``du``/``dv`` are the analytic first partial derivatives and
    ``normal`` is the unit ``du x dv`` — exactly the surface parameterization Kratos
    reads. The CAD-local ``swapped_surface_normal`` flag is *not* consulted here; it
    is reported separately by the caller. A degenerate (zero-length) ``du x dv``
    yields ``normal=None`` instead of a fabricated direction.
    """
    u, v = float(uv[0]), float(uv[1])
    if not (math.isfinite(u) and math.isfinite(v)):
        raise ValueError("the frame parameter must be finite")
    degree_u, degree_v, knots_u, knots_v, points, weights = _surface_arrays(surface)
    if not (np.all(np.isfinite(knots_u)) and np.all(np.isfinite(knots_v))):
        raise ValueError("the NURBS knot vector is not finite")
    span_u = _find_span(points.shape[0] - 1, degree_u, u, knots_u)
    span_v = _find_span(points.shape[1] - 1, degree_v, v, knots_v)
    basis_u = _basis_derivatives(span_u, u, degree_u, knots_u)
    basis_v = _basis_derivatives(span_v, v, degree_v, knots_v)
    first_u, first_v = span_u - degree_u, span_v - degree_v
    numerator = np.zeros(3)
    derivative_u = np.zeros(3)
    derivative_v = np.zeros(3)
    weight = derivative_weight_u = derivative_weight_v = 0.0
    for j in range(degree_v + 1):
        for i in range(degree_u + 1):
            w = weights[first_u + i, first_v + j]
            xyz = points[first_u + i, first_v + j]
            n_u0, n_u1 = basis_u[0][i], basis_u[1][i]
            n_v0, n_v1 = basis_v[0][j], basis_v[1][j]
            weight += n_u0 * n_v0 * w
            derivative_weight_u += n_u1 * n_v0 * w
            derivative_weight_v += n_u0 * n_v1 * w
            numerator += n_u0 * n_v0 * w * xyz
            derivative_u += n_u1 * n_v0 * w * xyz
            derivative_v += n_u0 * n_v1 * w * xyz
    if abs(weight) <= EPS:
        raise ValueError("the NURBS surface evaluates to a zero rational denominator")
    point = numerator / weight
    du = (derivative_u - point * derivative_weight_u) / weight
    dv = (derivative_v - point * derivative_weight_v) / weight
    cross = np.cross(du, dv)
    length = float(np.linalg.norm(cross))
    normal = (cross / length).tolist() if length > EPS else None
    return {"uv": [u, v], "point": point.tolist(), "du": du.tolist(), "dv": dv.tolist(),
            "normal": normal, "normal_length": length, "degenerate": normal is None}


def surface_point(surface, uv):
    return surface_frame(surface, uv)["point"]


def surface_midpoint(surface):
    knots_u = surface["knot_vectors"][0]
    knots_v = surface["knot_vectors"][1]
    return [(float(knots_u[0]) + float(knots_u[-1])) / 2.0,
            (float(knots_v[0]) + float(knots_v[-1])) / 2.0]


def _sample_parameter(first, last, samples):
    if samples < 2:
        return [float(first)]
    return [float(first) + (float(last) - float(first)) * i / (samples - 1)
            for i in range(samples)]


def natural_boundary(surface, local, samples=BOUNDARY_SAMPLES):
    """Polylines of one natural parametric boundary/corner of a cached CAD surface.

    ``local`` uses the project's ``SIDES`` convention. A row fixes ``u``, a column
    fixes ``v``, a corner is a single point and ``[-1, -1]`` returns the four
    boundary curves. The result is evaluated from the NURBS surface, so it is the
    analysis boundary, not the display triangulation.
    """
    located = row_locator(local)
    if located is None:
        return []
    knots_u = surface["knot_vectors"][0]
    knots_v = surface["knot_vectors"][1]
    u0, u1 = float(knots_u[0]), float(knots_u[-1])
    v0, v1 = float(knots_v[0]), float(knots_v[-1])
    axis, marker = located
    if axis == "all":
        return [natural_boundary(surface, side)[0] for side in
                (SIDES["u0"], SIDES["v1"], SIDES["u1"], SIDES["v0"])]
    if axis == "corner":
        u = u0 if marker[0] == 0 else u1
        v = v0 if marker[1] == 0 else v1
        return [[surface_point(surface, [u, v])]]
    if axis == "row":
        u = u0 if marker == 0 else u1
        return [[surface_point(surface, [u, value])
                 for value in _sample_parameter(v0, v1, samples)]]
    v = v0 if marker == 0 else v1
    return [[surface_point(surface, [value, v])
             for value in _sample_parameter(u0, u1, samples)]]


def boundary_frame(surface, local):
    """Frame at the representative midpoint of a natural boundary/corner target."""
    located = row_locator(local)
    if located is None:
        return None
    axis, marker = located
    knots_u = surface["knot_vectors"][0]
    knots_v = surface["knot_vectors"][1]
    u0, u1 = float(knots_u[0]), float(knots_u[-1])
    v0, v1 = float(knots_v[0]), float(knots_v[-1])
    if axis == "all":
        return surface_frame(surface, [(u0 + u1) / 2.0, (v0 + v1) / 2.0])
    if axis == "corner":
        u = u0 if marker[0] == 0 else u1
        v = v0 if marker[1] == 0 else v1
        return surface_frame(surface, [u, v])
    if axis == "row":
        u = u0 if marker == 0 else u1
        return surface_frame(surface, [u, (v0 + v1) / 2.0])
    v = v0 if marker == 0 else v1
    return surface_frame(surface, [(u0 + u1) / 2.0, v])


# --------------------------------------------------------------------------- #
# read-only diagnostics (report, never repair)
# --------------------------------------------------------------------------- #

def span_diagnostics(knots):
    """Positive / negative / non-finite span report for one knot vector.

    Repeated interior knots are normal multiplicity and are *not* degenerate spans:
    a zero-length interval from a repetition is counted as ``equal`` and only
    negative intervals, non-finite knots or the complete absence of a positive span
    are errors.
    """
    try:
        values = [float(value) for value in knots]
    except (TypeError, ValueError):
        return {"positive": 0, "equal": 0, "negative": [], "nonfinite": [],
                "errors": ["the knot vector is not a list of finite numbers"]}
    nonfinite = [i for i, value in enumerate(values) if not math.isfinite(value)]
    negative = [i for i in range(len(values) - 1) if values[i + 1] < values[i]]
    equal = [i for i in range(len(values) - 1) if values[i + 1] == values[i]]
    positive = sum(1 for i in range(len(values) - 1) if values[i + 1] > values[i])
    errors = []
    if nonfinite:
        errors.append(f"non-finite knot value(s) at position {nonfinite}")
    if negative:
        errors.append(f"knot vector decreases at interval(s) {negative}")
    if positive == 0:
        errors.append("the knot vector has no positive-length span")
    return {"positive": positive, "equal": len(equal), "negative": negative,
            "nonfinite": nonfinite, "errors": errors}


def weight_diagnostics(surface):
    """Non-positive / non-finite control-point weights of a cached CAD surface."""
    nonpositive, nonfinite, values = [], [], []
    for position, record in enumerate(surface.get("control_points", [])):
        raw = record[-1] if isinstance(record[-1], list) else record
        try:
            weight = float(raw[3]) if len(raw) > 3 else 1.0
        except (TypeError, ValueError, IndexError):
            nonfinite.append(position)
            continue
        values.append(weight)
        if not math.isfinite(weight):
            nonfinite.append(position)
        elif weight <= 0.0:
            nonpositive.append(position)
    return {"nonpositive": nonpositive, "nonfinite": nonfinite,
            "min": min(values) if values else None, "max": max(values) if values else None,
            "errors": ([f"non-finite control-point weight(s) at {nonfinite}"] if nonfinite else [])
                      + ([f"non-positive control-point weight(s) at {nonpositive}"] if nonpositive else [])}


def face_surface_map(cad):
    return {face["brep_id"]: face for brep in cad.get("breps", []) for face in brep.get("faces", [])}


def geometry_diagnostics(cad):
    """Per-face knot/weight diagnostics of the cached CAD; never modifies it."""
    faces, errors = [], []
    for face_id in sorted(face_surface_map(cad)):
        surface = face_surface_map(cad)[face_id]["surface"]
        knots = [span_diagnostics(axis) for axis in surface.get("knot_vectors", [])]
        weights = weight_diagnostics(surface)
        face_errors = [f"face {face_id} u: {e}" for e in knots[0]["errors"]] + \
                      [f"face {face_id} v: {e}" for e in knots[1]["errors"]] + \
                      [f"face {face_id}: {e}" for e in weights["errors"]]
        errors.extend(face_errors)
        faces.append({"face_id": face_id, "knots": {"u": knots[0], "v": knots[1]},
                      "weights": weights, "errors": face_errors})
    return {"faces": faces, "errors": errors}


# --------------------------------------------------------------------------- #
# refinement / quadrature / display sampling (kept separate)
# --------------------------------------------------------------------------- #

def refinement_report(project, index):
    """The three independent preprocessing scales, never substituted for each other.

    ``analysis`` carries original -> target degree, the positive span count and the
    control-point estimate from :func:`kratos.groups.final_grid` (the same function
    the strong-enforcement overlap check uses). ``quadrature`` is only the requested
    points per span. ``display`` is the VTK sampling and is explicitly display-only.
    """
    faces = {face["id"]: face for face in index.get("faces", [])}
    patches = {patch.get("face_id"): patch for patch in project.get("patches", [])}
    refinement = bool(project.get("refinement_enabled", True))
    rows = []
    for face_id in sorted(faces):
        face = faces[face_id]
        patch = patches.get(face_id, {})
        original = list(face.get("degrees") or [])
        spans = face.get("knot_spans") or {}
        counts = face.get("control_point_counts") or {}
        target = ([patch.get("degree_u"), patch.get("degree_v")] if refinement
                  else list(original))
        estimate = final_grid(index, face_id, patch, refinement) if refinement else \
            (counts.get("u"), counts.get("v"))
        reason = None
        if estimate is None or estimate[0] is None or estimate[1] is None:
            detail = []
            if not counts:
                detail.append("no control-point counts")
            if refinement and not spans:
                detail.append("no positive-span counts")
            reason = "the geometry cache lacks " + " and ".join(detail or ["a usable grid"]) + \
                     "; reimport the source STEP to rebuild it"
        rows.append({
            "face_id": face_id, "active": bool(patch.get("active", True)),
            "original_degree": original,
            "target_degree": target,
            "degree_delta": [target[i] - original[i] for i in range(2)] if len(original) == 2 else None,
            "inserts": [patch.get("insert_u"), patch.get("insert_v")] if refinement else [0, 0],
            "spans": [spans.get("u"), spans.get("v")],
            "original_control_points": {"u": counts.get("u"), "v": counts.get("v"),
                                        "total": face.get("control_points")},
            "estimate_control_points": ({"u": estimate[0], "v": estimate[1],
                                         "total": estimate[0] * estimate[1]}
                                        if estimate and estimate[0] and estimate[1] else None),
            "estimate_is_authoritative": False,
            "min_continuity": list(face.get("min_continuity") or []),
            "reason": reason,
        })
    return {
        "analysis": {"refinement_enabled": refinement, "faces": rows,
                     "note": ("target degrees, per-span knot insertions and the nominal "
                              "control-net estimate; verify against the initialized model. "
                              "Repeated knots are normal multiplicity and do not create spans.")
                     if refinement else
                     ("refinement is disabled: the original degree and control net are "
                      "exported unchanged; target degrees are shown as the original values.")},
        "quadrature": {"faces": [{"face_id": patch.get("face_id"),
                                  "requested_points_per_span": patch.get("quadrature", 0)}
                                 for patch in sorted(project.get("patches", []),
                                                     key=lambda p: p.get("face_id", 0))],
                       "note": ("0 keeps the target default rule. This is the requested "
                                "points per span; the actual integration size exists only "
                                "after Kratos Initialize.")},
        "display": {"subdivision": project.get("output", {}).get("refinement"),
                    "note": ("display sampling only; it does not change analysis "
                             "insertion/degree or quadrature and is not the integration domain.")},
    }


def material_legend(project, index):
    """Exact current material/section assignment for every patch (ID, name, units)."""
    materials = {material.get("id"): material for material in project.get("materials", [])}
    sections = {section.get("id"): section for section in project.get("sections", [])}
    patches = {patch.get("face_id"): patch for patch in project.get("patches", [])}
    units = unit_labels(project.get("units", "mm-N-s"))
    rows, colors = [], {}
    for position, face in enumerate(sorted(index.get("faces", []), key=lambda f: f["id"])):
        patch = patches.get(face["id"], {})
        material = materials.get(patch.get("material_id"), {})
        section = sections.get(patch.get("section_id"), {})
        color = PALETTE[position % len(PALETTE)]
        colors[face["id"]] = color
        rows.append({
            "face_id": face["id"], "active": bool(patch.get("active", True)),
            "color": color,
            "material_id": patch.get("material_id"), "material_name": material.get("name", "?"),
            "young": material.get("young"), "young_unit": units["stress"],
            "section_id": patch.get("section_id"), "section_name": section.get("name", "?"),
            "thickness": section.get("thickness"), "thickness_unit": units["length"],
        })
    return {"rows": rows, "colors": colors,
            "note": ("material colour is a view-only palette; it is not written back to the "
                     "project and never changes Shape or Placement.")}


# --------------------------------------------------------------------------- #
# glyph specifications
# --------------------------------------------------------------------------- #

def _vector_from(value):
    return [float(component) if component is not None else 0.0 for component in value]


def _magnitude(value):
    return math.sqrt(sum(float(component) ** 2 for component in value if component is not None))


def _direction(value):
    magnitude = _magnitude(value)
    if magnitude <= EPS:
        return None, magnitude
    return [float(component or 0.0) / magnitude for component in value], magnitude


def _component_names(mask):
    return [axis for axis, on in zip("XYZ", mask) if on]


def model_diagonal(index):
    points = []
    for face in index.get("faces", []):
        points.extend(face.get("corners", {}).values())
    for edge in index.get("edges", []):
        points.extend(edge.get("points") or [])
    if not points:
        return 0.0
    coordinates = list(zip(*[[float(c) for c in point[:3]] for point in points]))
    return math.sqrt(sum((max(axis) - min(axis)) ** 2 for axis in coordinates))


def _polyline_fraction(points, fraction):
    if not points:
        return None
    if len(points) == 1:
        return list(points[0][:3])
    lengths = [math.dist(points[i][:3], points[i + 1][:3]) for i in range(len(points) - 1)]
    total = sum(lengths)
    if total <= EPS:
        return list(points[0][:3])
    target = total * fraction
    travelled = 0.0
    for i, length in enumerate(lengths):
        if travelled + length >= target:
            local = (target - travelled) / length if length > EPS else 0.0
            return [points[i][axis] + (points[i + 1][axis] - points[i][axis]) * local
                    for axis in range(3)]
        travelled += length
    return list(points[-1][:3])


def support_glyphs(project, index, cad, groups):
    """Specifications for strong/penalty support markers of the current assignment."""
    active = {patch["face_id"] for patch in project.get("patches", []) if patch.get("active", True)}
    surfaces = face_surface_map(cad)
    edges = {edge["id"]: edge for edge in index.get("edges", [])}
    glyphs = []
    for position, item in enumerate(project.get("supports", []), 1):
        resolved = resolve(item, groups, index)
        method = item.get("method", "strong")
        glyph = {"category": "support", "record": position, "name": item.get("name", f"Support_{position}"),
                 "method": method, "value": list(item.get("value", [])),
                 "interval": list(item.get("interval", [])), "source": resolved["source"],
                 "missing": list(resolved["missing"]), "targets": [], "blocked": None}
        if method == "strong":
            from .groups import strong_spec
            spec = strong_spec(item, index, groups, project)
            if not spec["locations"]:
                glyph["blocked"] = ("strong enforcement has no natural boundary/corner on "
                                    "its target" if spec.get("reason") == "location" else
                                    "the geometry cache has no control-net counts/spans for "
                                    "this target; reimport the source STEP")
                glyphs.append(glyph)
                continue
            for face_id, local, _region, mask in spec["locations"]:
                face = surfaces.get(face_id)
                if face is None:
                    glyph["blocked"] = glyph["blocked"] or f"face {face_id} is not in the cached CAD"
                    continue
                located = row_locator(local)
                corner = located is not None and located[0] == "corner"
                glyph["targets"].append({
                    "kind": "face", "id": face_id, "active": face_id in active,
                    "local": list(local), "corner": corner,
                    "clamp": bool(item.get("clamp")) and not corner,
                    "components": _component_names(mask),
                    "curves": natural_boundary(face["surface"], local),
                    "frame": boundary_frame(face["surface"], local),
                    "swapped_surface_normal": bool(face.get("swapped_surface_normal")),
                    "label": f"face {face_id} {local}",
                })
        else:
            component_mask = tuple(value is not None for value in item.get("value", []))
            for edge_id in resolved["ids"]:
                edge = edges.get(edge_id)
                if edge is None:
                    glyph["blocked"] = glyph["blocked"] or f"edge {edge_id} is not in the geometry cache"
                    continue
                glyph["targets"].append({
                    "kind": "edge", "id": edge_id,
                    "active": edge_is_active(edge, active), "local": None, "corner": False,
                    "clamp": False, "components": _component_names(component_mask),
                    "curves": [[list(point[:3]) for point in edge.get("points") or []]],
                    "frame": None, "swapped_surface_normal": None,
                    "label": f"edge {edge_id}",
                })
        glyphs.append(glyph)
    return glyphs


def load_glyphs(project, index, cad, groups):
    """Specifications for load arrows with per-family declared scales and units.

    Arrow length is proportional to the actual ``|value|`` inside one *dimension*
    family; the longest arrow in that family spans ``MAX_ARROW_FRACTION`` of the
    model diagonal and the resulting scale factor is reported. ``LINE_LOAD`` and an
    edge ``DEAD_LOAD`` share the force-per-length family; ``SURFACE_LOAD`` and a face
    ``DEAD_LOAD`` share the force-per-area family, exactly matching the unit
    conversion in :mod:`kratos_iga.project`. A zero vector is a distinct zero
    marker, not a zero-length arrow.
    """
    active = {patch["face_id"] for patch in project.get("patches", []) if patch.get("active", True)}
    surfaces = face_surface_map(cad)
    edges = {edge["id"]: edge for edge in index.get("edges", [])}
    diagonal = model_diagonal(index)
    units = load_units(project.get("units", "mm-N-s"))
    glyphs = []
    for position, item in enumerate(project.get("loads", []), 1):
        if is_pressure(item):
            continue  # additive reference_pressure records are handled by pressure_glyphs
        resolved = resolve(item, groups, index)
        value = _vector_from(item.get("value", [0.0, 0.0, 0.0]))
        direction, magnitude = _direction(value)
        family = load_family(item.get("variable"), resolved["kind"])
        glyph = {"category": "load", "record": position, "name": item.get("name", f"Load_{position}"),
                 "variable": item.get("variable"), "target_kind": resolved["kind"],
                 "dimension": load_dimension(item.get("variable"), resolved["kind"]),
                 "family": family, "value": value, "magnitude": magnitude,
                 "direction": direction, "unit": units.get(family, "?"),
                 "interval": list(item.get("interval", [])), "source": resolved["source"],
                 "missing": list(resolved["missing"]), "targets": [], "blocked": None,
                 "amplitude": amplitude_metadata(item)}
        for ident in resolved["ids"]:
            if resolved["kind"] == "edge":
                edge = edges.get(ident)
                if edge is None:
                    glyph["blocked"] = glyph["blocked"] or f"edge {ident} is not in the geometry cache"
                    continue
                polyline = [list(point[:3]) for point in edge.get("points") or []]
                glyph["targets"].append({
                    "kind": "edge", "id": ident, "active": edge_is_active(edge, active),
                    "anchors": [anchor for anchor in
                                (_polyline_fraction(polyline, fraction)
                                 for fraction in (0.15, 0.375, 0.625, 0.85)) if anchor],
                    "outline": [polyline], "label": f"edge {ident}",
                })
            else:
                face = surfaces.get(ident)
                if face is None:
                    glyph["blocked"] = glyph["blocked"] or f"face {ident} is not in the cached CAD"
                    continue
                surface = face["surface"]
                knots_u, knots_v = surface["knot_vectors"]
                anchors = []
                for fu in (0.25, 0.5, 0.75):
                    for fv in (0.25, 0.5, 0.75):
                        u = float(knots_u[0]) + (float(knots_u[-1]) - float(knots_u[0])) * fu
                        v = float(knots_v[0]) + (float(knots_v[-1]) - float(knots_v[0])) * fv
                        try:
                            anchors.append(surface_point(surface, [u, v]))
                        except ValueError:
                            continue
                glyph["targets"].append({
                    "kind": "face", "id": ident, "active": ident in active,
                    "anchors": anchors,
                    "outline": natural_boundary(surface, SIDES["all"]),
                    "label": f"face {ident} (background surface samples)",
                })
        glyphs.append(glyph)
    glyphs.extend(pressure_glyphs(project, index, cad, groups))
    # Declared scale per *dimension* family: the longest arrow spans a fixed fraction
    # of the 3D diagonal. Lengths are in the cached CAD mm; the load unit is separate.
    scales = {}
    for family in sorted({glyph["family"] for glyph in glyphs if glyph["family"]}):
        magnitudes = [glyph["magnitude"] for glyph in glyphs if glyph["family"] == family]
        maximum = max(magnitudes) if magnitudes else 0.0
        scales[family] = {
            "unit": units.get(family, "?"), "max_magnitude": maximum,
            "scale": (MAX_ARROW_FRACTION * diagonal / maximum) if maximum > EPS else None,
            "longest_arrow_mm": MAX_ARROW_FRACTION * diagonal if maximum > EPS else 0.0,
            "max_arrow_length_mm": MAX_ARROW_FRACTION * diagonal,
            "length_unit": DISPLAY_LENGTH_UNIT,
        }
    for glyph in glyphs:
        factor = scales.get(glyph["family"], {}).get("scale")
        glyph["scale"] = factor
        glyph["display_length"] = factor * glyph["magnitude"] if factor else 0.0
    return {"glyphs": glyphs, "scales": scales, "length_unit": DISPLAY_LENGTH_UNIT,
            "note": ("arrow length is proportional to |value| inside one dimension family; "
                     "each family has its own declared scale and unit and is never compared "
                     "with a differently dimensioned one. Lengths are displayed in mm "
                     "(cached CAD), not in the project length unit.")}


def frame_glyphs(project, index, cad, groups=None):
    """Background parameter frames: u, v and the unit u x v normal per active face."""
    active = {patch["face_id"] for patch in project.get("patches", []) if patch.get("active", True)}
    surfaces = face_surface_map(cad)
    glyphs = []
    for face in sorted(index.get("faces", []), key=lambda f: f["id"]):
        face_id = face["id"]
        record = surfaces.get(face_id)
        if record is None:
            glyphs.append({"face_id": face_id, "active": face_id in active, "blocked":
                           "face is not in the cached CAD", "frame": None})
            continue
        uv = surface_midpoint(record["surface"])
        try:
            frame = surface_frame(record["surface"], uv)
        except ValueError as error:
            glyphs.append({"face_id": face_id, "active": face_id in active,
                           "blocked": str(error), "frame": None})
            continue
        glyphs.append({
            "face_id": face_id, "active": face_id in active, "uv": uv, "frame": frame,
            "swapped_surface_normal": bool(record.get("swapped_surface_normal")),
            "blocked": None,
            "note": ("background-surface sample at the knot-domain midpoint; it can lie inside "
                     "a trimming hole or outside the trimmed domain. u x v follows the NURBS "
                     "parameterization Kratos reads, not the CAD face orientation."),
        })
    return glyphs


# --------------------------------------------------------------------------- #
# blockers
# --------------------------------------------------------------------------- #

FRAME_LEGEND = (
    {"axis": "u", "color": FRAME_U_COLOR, "label": "u tangent  dS/du"},
    {"axis": "v", "color": FRAME_V_COLOR, "label": "v tangent  dS/dv"},
    {"axis": "n", "color": FRAME_N_COLOR, "label": "normal  unit(du x dv)"},
)


def frame_legend():
    """The three independently coloured background-frame axes (GUI legend rows)."""
    return [dict(entry) for entry in FRAME_LEGEND]


def geometry_blocker(document, fingerprints, entities=None):
    """Read-only geometry/source diagnostic used to keep stale glyphs off screen.

    It compares the live analysis objects with the recorded display fingerprints only;
    it never mutates the project or the document. A missing object or a changed
    ``Shape``/``Placement`` blocks the preview so a geometry edit cannot be presented
    as a valid, unchanged assignment.
    """
    if document is None:
        return ""
    for name, signature in (fingerprints or {}).items():
        obj = document.getObject(name)
        if obj is None:
            return f"the displayed analysis object {name!r} is missing; reimport or restore the document"
        try:
            current = (obj.Shape.hashCode(), str(obj.Placement))
        except Exception as error:  # noqa: BLE001 - unreadable shape
            return f"the analysis object {name!r} has no readable Shape/Placement ({error})"
        if current != tuple(signature):
            return ("the displayed CAD geometry has changed; save the modified model as STEP and "
                    "reimport it before previewing")
    return ""


def project_blockers(project):
    """Reasons the preview must not present stale glyphs as valid.

    A geometry change marks every binding ``needs_rebind``; that is a valid
    diagnostic state, so the preview is blocked with that message instead of
    drawing glyphs against a geometry the project no longer matches.
    """
    reasons = []
    if project.get("geometry_needs_rebind"):
        reasons.append(str(project["geometry_needs_rebind"]))
    pending = [item.get("name", "?") for category in ("supports", "loads", "initial_conditions")
               for item in project.get(category, []) if item.get("needs_rebind")]
    pending += [f"group {group.get('id')}" for group in project.get("groups", [])
                if isinstance(group, dict) and group.get("needs_rebind")]
    if pending:
        reasons.append("pending rebind: " + ", ".join(sorted(pending)))
    return "; ".join(reasons)


# --------------------------------------------------------------------------- #
# document-side layer (FreeCAD/Part imported lazily)
# --------------------------------------------------------------------------- #

class PreviewLayer:
    """Owns only its own throw-away display objects in one FreeCAD document.

    The layer never registers anything in the panel's analysis entity map or display
    fingerprints, never adds ``IbraBrepId``, never writes the project and never
    exports a snapshot. ``clear`` removes exactly the objects this layer created
    (plus reloaded objects carrying its reserved name prefix) and restores every
    view colour it changed.
    """

    GROUP_NAME = "IBRA_Preview"
    PREFIX = "IBRA_Preview_"
    UNSELECTABLE = True
    # Ownership never relies on the name prefix alone: every object this layer creates
    # carries a reserved, persisted marker, and a foreign object that merely shares the
    # prefix is neither reused nor deleted.
    MARKER_PROPERTY = "IbraPreviewTag"
    MARKER_VALUE = "P0-4-display"
    SAVED_COLORS_PROPERTY = "IbraPreviewSavedColors"

    def __init__(self, tagger=None):
        self.tagger = tagger
        self.doc = None
        self.objects = {}            # (kind, key) -> object name
        self.restore_colors = {}     # analysis object name -> (r, g, b)
        self.notes = []
        self.report = None

    # -- document plumbing ------------------------------------------------- #
    def _live_document(self):
        import FreeCAD as App
        doc = self.doc
        if doc is None:
            return None
        try:
            if doc.Name in App.listDocuments():
                return doc
        except Exception:  # noqa: BLE001 - a deleted document object
            return None
        return None

    def _reset_records(self):
        self.objects = {}
        self.notes = []
        self.report = None

    @classmethod
    def _owns(cls, obj):
        return getattr(obj, cls.MARKER_PROPERTY, None) == cls.MARKER_VALUE

    @classmethod
    def _mark(cls, obj):
        if not hasattr(obj, cls.MARKER_PROPERTY):
            obj.addProperty("App::PropertyString", cls.MARKER_PROPERTY, "IBRA")
            obj.setEditorMode(cls.MARKER_PROPERTY, 1)
        setattr(obj, cls.MARKER_PROPERTY, cls.MARKER_VALUE)

    def _restore_saved_colors(self, doc):
        """Restore colours stored in **our own** preview group (survives save/reopen)."""
        group = doc.getObject(self.GROUP_NAME)
        if group is None or not self._owns(group):
            # Never read or act on a foreign object that merely uses the reserved name.
            return
        raw = getattr(group, self.SAVED_COLORS_PROPERTY, "")
        if not raw:
            return
        try:
            saved = json.loads(raw)
        except (TypeError, ValueError):
            saved = {}
        for name, color in saved.items():
            obj = doc.getObject(name)
            view = getattr(obj, "ViewObject", None) if obj is not None else None
            if view is not None:
                try:
                    view.ShapeColor = tuple(color)
                except Exception:  # noqa: BLE001
                    pass

    def _persist_saved_colors(self):
        group = self._group()
        if not hasattr(group, self.SAVED_COLORS_PROPERTY):
            group.addProperty("App::PropertyString", self.SAVED_COLORS_PROPERTY, "IBRA")
            group.setEditorMode(self.SAVED_COLORS_PROPERTY, 1)
        setattr(group, self.SAVED_COLORS_PROPERTY, json.dumps(self.restore_colors))

    def clear(self, doc=None):
        """Remove only marker-owned preview objects and restore every colour changed.

        ``doc`` may be an incoming document (e.g. a reopened one) so stale preview
        objects and a persisted material colour are cleaned on document restoration.
        """
        doc = doc if doc is not None else self._live_document()
        if doc is None:
            self._reset_records()
            self.restore_colors = {}
            self.doc = None
            return
        self._restore_saved_colors(doc)
        for name, color in list(self.restore_colors.items()):
            obj = doc.getObject(name)
            view = getattr(obj, "ViewObject", None) if obj is not None else None
            if view is not None:
                try:
                    view.ShapeColor = tuple(color)
                except Exception:  # noqa: BLE001
                    pass
        names = set(self.objects.values())
        try:
            for obj in list(doc.Objects):
                if obj.Name.startswith(self.PREFIX) and self._owns(obj):
                    names.add(obj.Name)
        except Exception:  # noqa: BLE001 - best effort while closing a document
            pass
        for name in names:
            obj = doc.getObject(name)
            if obj is not None and self._owns(obj):
                try:
                    doc.removeObject(name)
                except Exception:  # noqa: BLE001
                    pass
        group = doc.getObject(self.GROUP_NAME)
        if group is not None and self._owns(group) and not getattr(group, "Group", []):
            try:
                doc.removeObject(self.GROUP_NAME)
            except Exception:  # noqa: BLE001
                pass
        self.restore_colors = {}
        self._reset_records()
        self.doc = None

    def _ensure_group(self):
        """Return our own marked preview group; never adopt or mark a foreign one.

        A foreign ``IBRA_Preview`` object fails clear before anything is created, so the
        preview can neither reuse nor later delete it.
        """
        doc = self._live_document()
        group = doc.getObject(self.GROUP_NAME)
        if group is not None and not self._owns(group):
            raise RuntimeError(
                f"an unrelated object already uses the reserved name {self.GROUP_NAME!r}; the "
                "preview will not adopt, mark or delete it. Rename or remove it first")
        if group is None:
            group = doc.addObject("App::DocumentObjectGroup", self.GROUP_NAME)
            group.Label = "IBRA Preview (display only)"
            self._mark(group)
        return group

    def _group(self):
        return self._ensure_group()

    def _adopt(self, obj, kind, key, color=None, line_color=None, line_width=None,
               display_mode=None):
        self._mark(obj)
        obj.Label = f"{self.PREFIX}{kind}_{key}"
        view = getattr(obj, "ViewObject", None)
        if view is not None:
            try:
                if display_mode is not None:
                    view.DisplayMode = display_mode
                if color is not None:
                    view.ShapeColor = tuple(color)
                if line_color is not None:
                    view.LineColor = tuple(line_color)
                if line_width is not None:
                    view.LineWidth = float(line_width)
                if self.UNSELECTABLE:
                    view.Selectable = False
            except Exception as error:  # noqa: BLE001 - a view without the property
                self.notes.append(f"{obj.Name}: view styling limited ({error})")
        if self.tagger is not None:
            self.tagger(obj, kind, key)
        self.objects[(kind, key)] = obj.Name
        group = self._group()
        if obj not in group.Group:
            group.addObject(obj)
        return obj

    def _feature(self, name, shapes):
        import Part
        doc = self._live_document()
        if doc is None:
            return None
        obj = doc.getObject(name)
        if obj is not None and not self._owns(obj):
            self.notes.append(f"{name}: an unrelated object already uses this reserved name; "
                              "it was left untouched and no preview glyph was created")
            return None
        if obj is None:
            obj = doc.addObject("Part::Feature", name)
        if len(shapes) == 1:
            obj.Shape = shapes[0]
        else:
            obj.Shape = Part.makeCompound(shapes)
        self._mark(obj)
        return obj

    def _color_analysis_faces(self, face_colors, entities):
        """Recolour analysis face objects through ViewObject only, remembering originals."""
        doc = self._live_document()
        if doc is None:
            return 0
        changed = 0
        for (kind, ident), object_name in (entities or {}).items():
            if kind != "face":
                continue
            color = face_colors.get(ident)
            obj = doc.getObject(object_name)
            view = getattr(obj, "ViewObject", None) if obj is not None else None
            if color is None or view is None:
                continue
            if obj.Name not in self.restore_colors:
                try:
                    self.restore_colors[obj.Name] = tuple(view.ShapeColor)
                except Exception:  # noqa: BLE001
                    pass
            try:
                view.ShapeColor = tuple(color)
                changed += 1
            except Exception as error:  # noqa: BLE001
                self.notes.append(f"{obj.Name}: colour not applied ({error})")
        if changed:
            self._persist_saved_colors()
        return changed

    # -- geometry builders -------------------------------------------------- #
    @staticmethod
    def _arrow(origin, direction, length, radius):
        """A solid arrow of the true ``length`` from ``origin`` along ``direction``.

        The shaft/head radius is bounded by the length so a small load still gets a
        proportional (not inflated) arrow, and the total extent equals ``length``.
        """
        import FreeCAD as App
        import Part
        if length <= EPS or direction is None:
            return None
        radius = min(float(radius), max(length * 0.22, 1.0e-9))
        head = min(length * 0.4, max(radius * 4.0, length * 0.2))
        shaft = max(length - head, 1.0e-9)
        body = Part.makeCylinder(radius, shaft, App.Vector(0, 0, 0), App.Vector(0, 0, 1))
        tip = Part.makeCone(radius * 2.2, 0.0, head, App.Vector(0, 0, shaft), App.Vector(0, 0, 1))
        shape = body.fuse(tip)
        start = App.Vector(0, 0, 1)
        target = App.Vector(*[float(component) for component in direction])
        if target.Length <= EPS:
            return None
        target.normalize()
        angle = start.getAngle(target)
        if angle > 1.0e-9:
            axis = start.cross(target)
            if axis.Length <= 1.0e-9:
                axis = App.Vector(1, 0, 0)
            shape.rotate(App.Vector(0, 0, 0), axis, math.degrees(angle))
        shape.translate(App.Vector(*[float(component) for component in origin]))
        return shape

    @staticmethod
    def _wire(polyline):
        import FreeCAD as App
        import Part
        points = [App.Vector(*[float(component) for component in point[:3]]) for point in polyline
                  if point is not None]
        if len(points) < 2:
            return None
        return Part.makePolygon(points)

    def rebuild(self, doc, project, index, cad, toggles, entities=None):
        """Idempotently rebuild the enabled preview categories in ``doc``."""
        self.clear()
        self.doc = doc
        notes = []
        self.notes = notes
        report = {"blocked": project_blockers(project), "supports": 0, "loads": 0,
                  "frames": 0, "materials": 0, "notes": notes, "scales": {},
                  "inactive_support_targets": [], "inactive_load_targets": [], "inactive_frames": [],
                  "material_legend": None, "frame_legend": frame_legend(),
                  "diagnostics": geometry_diagnostics(cad),
                  "refinement": refinement_report(project, index),
                  "length_unit": DISPLAY_LENGTH_UNIT}
        self.report = report
        if report["blocked"]:
            notes.append("preview blocked: " + report["blocked"])
            return report
        try:
            self._ensure_group()
        except RuntimeError as error:
            report["blocked"] = str(error)
            notes.append("preview blocked: " + str(error))
            return report
        groups = expand(project.get("groups", []), index)
        diagonal = model_diagonal(index) or 1.0
        base_radius = max(diagonal * 0.004, 1.0e-6)

        if toggles.get("supports"):
            for glyph in support_glyphs(project, index, cad, groups):
                color = PENALTY_COLOR if glyph["method"] == "penalty" else STRONG_COLOR
                shapes = []
                for target in glyph["targets"]:
                    if not target["active"]:
                        report["inactive_support_targets"].append(
                            f"{glyph['name']} · {target['label']}")
                        notes.append(f"{glyph['name']} · {target['label']} · INACTIVE: outside the "
                                     "analysis domain, no glyph drawn")
                        continue
                    for curve in target["curves"]:
                        wire = self._wire(curve)
                        if wire is not None:
                            shapes.append(wire)
                    if target["corner"] and target["curves"]:
                        import Part
                        point = target["curves"][0][0]
                        shapes.append(Part.makeSphere(max(base_radius * 1.4, 1.0e-6),
                                                      self._vector(point)))
                    if target["clamp"]:
                        shapes.extend(self._clamp_marks(target, base_radius))
                    shapes.extend(self._component_marks(target, base_radius))
                    notes.append(
                        f"{glyph['name']} · {target['label']} · {glyph['method']} · "
                        f"components {target['components'] or 'all'}"
                        + (" · clamp adjacent control row (schematic diagram)" if target["clamp"] else "")
                        + (" · natural corner" if target["corner"] else ""))
                if glyph["blocked"]:
                    notes.append(f"{glyph['name']}: {glyph['blocked']}")
                if not shapes:
                    continue
                name = f"{self.PREFIX}Support_{glyph['record']}"
                obj = self._feature(name, shapes)
                if obj is not None:
                    self._adopt(obj, "support", glyph["record"], color=color,
                                line_color=color, line_width=2.4, display_mode="Flat Lines")
                    report["supports"] += 1

        if toggles.get("loads"):
            specs = load_glyphs(project, index, cad, groups)
            report["scales"] = specs["scales"]
            for glyph in specs["glyphs"]:
                color = PRESSURE_COLOR if glyph.get("pressure") else \
                    LOAD_COLORS.get(glyph["variable"], (0.5, 0.5, 0.5))
                shapes = []
                for target in glyph["targets"]:
                    if not target["active"]:
                        report["inactive_load_targets"].append(f"{glyph['name']} · {target['label']}")
                        notes.append(f"{glyph['name']} · {target['label']} · INACTIVE: outside the "
                                     "analysis domain, no glyph drawn")
                        continue
                    for outline in target.get("outline") or []:
                        wire = self._wire(outline)
                        if wire is not None:
                            shapes.append(wire)
                    if glyph.get("pressure"):
                        import Part
                        amplitude = glyph.get("amplitude") or {}
                        if glyph["magnitude"] <= EPS:
                            for anchor in target["anchors"]:
                                shapes.append(Part.makeSphere(max(base_radius * 1.5, 1.0e-6),
                                                              self._vector(anchor)))
                            notes.append(f"{glyph['name']} · {target['label']} · reference pressure "
                                         f"p=0 {glyph['unit']} · normal_flip={glyph['normal_flip']} · "
                                         "zero pressure marker; X0 reference normal u x v, not "
                                         "follower/current normal")
                            continue
                        tiny = 0.0 < glyph["display_length"] < base_radius
                        for arrow_spec in target.get("pressure_arrows") or []:
                            arrow = self._arrow(arrow_spec["point"], arrow_spec["direction"],
                                                glyph["display_length"], base_radius)
                            if arrow is not None:
                                shapes.append(arrow)
                        if tiny:
                            for anchor in target["anchors"]:
                                shapes.append(Part.makeSphere(max(base_radius * 1.8, 1.0e-6),
                                                              self._vector(anchor)))
                        notes.append(
                            f"{glyph['name']} · {target['label']} · reference_pressure "
                            f"p={glyph['pressure_value']:.6g} {glyph['unit']} · "
                            f"normal_flip={glyph['normal_flip']} · X0 reference normal u x v "
                            f"(parametric Kratos normal, not CAD OCCT orientation) · not "
                            f"follower/current normal · pointwise normals on the background surface "
                            f"(may lie in a hole) · true arrow {glyph['display_length']:.4g} "
                            f"{DISPLAY_LENGTH_UNIT} · {amplitude.get('note', '')}"
                            + (f" · knots t={amplitude['knots']} domain={amplitude['domain']}"
                               if amplitude.get("present") and amplitude.get("knots") else "")
                            + (f" · TINY LOAD marker (arrow below {base_radius:.3g} "
                               f"{DISPLAY_LENGTH_UNIT})" if tiny else ""))
                        continue
                    if glyph["direction"] is None:
                        import Part
                        for anchor in target["anchors"]:
                            shapes.append(Part.makeSphere(max(base_radius * 1.5, 1.0e-6),
                                                          self._vector(anchor)))
                        notes.append(f"{glyph['name']} · {target['label']} · zero load marker "
                                     f"(|value| = 0 {glyph['unit']})")
                        continue
                    tiny = 0.0 < glyph["display_length"] < base_radius
                    for anchor in target["anchors"]:
                        arrow = self._arrow(anchor, glyph["direction"], glyph["display_length"],
                                            base_radius)
                        if arrow is not None:
                            shapes.append(arrow)
                    if tiny:
                        import Part
                        for anchor in target["anchors"]:
                            shapes.append(Part.makeSphere(max(base_radius * 1.8, 1.0e-6),
                                                          self._vector(anchor)))
                    notes.append(
                        f"{glyph['name']} · {target['label']} · {glyph['variable']} "
                        f"({glyph['dimension']}) |value|={glyph['magnitude']:.6g} {glyph['unit']} · "
                        f"true arrow {glyph['display_length']:.4g} {DISPLAY_LENGTH_UNIT} at the "
                        f"declared scale · {(glyph.get('amplitude') or {}).get('note', '')}"
                        + (f" · TINY LOAD marker (arrow below {base_radius:.3g} {DISPLAY_LENGTH_UNIT})"
                           if tiny else ""))
                if glyph["blocked"]:
                    notes.append(f"{glyph['name']}: {glyph['blocked']}")
                if not shapes:
                    continue
                name = f"{self.PREFIX}Load_{glyph['record']}"
                obj = self._feature(name, shapes)
                if obj is not None:
                    self._adopt(obj, "load", glyph["record"], color=color, line_color=color,
                                line_width=2.6, display_mode="Flat Lines")
                    report["loads"] += 1

        if toggles.get("frame"):
            arrow_length = max(diagonal * 0.12, 1.0e-6)
            for glyph in frame_glyphs(project, index, cad):
                frame = glyph.get("frame")
                if frame is None:
                    notes.append(f"frame face {glyph['face_id']}: {glyph.get('blocked')}")
                    continue
                if not glyph["active"]:
                    report["inactive_frames"].append(glyph["face_id"])
                    notes.append(f"frame face {glyph['face_id']} · INACTIVE: outside the analysis "
                                 "domain, no frame drawn")
                    continue
                for axis, direction, axis_color in (
                        ("u", frame["du"], FRAME_U_COLOR), ("v", frame["dv"], FRAME_V_COLOR),
                        ("n", frame["normal"], FRAME_N_COLOR)):
                    arrow = self._arrow(frame["point"], direction, arrow_length, base_radius)
                    if arrow is None:
                        continue
                    name = f"{self.PREFIX}Frame_{glyph['face_id']}_{axis}"
                    obj = self._feature(name, [arrow])
                    if obj is not None:
                        self._adopt(obj, f"frame_{axis}", glyph["face_id"], color=axis_color,
                                    line_color=axis_color, line_width=2.0, display_mode="Flat Lines")
                        report["frames"] += 1
                notes.append(
                    f"frame face {glyph['face_id']} · uv={glyph['uv']} · independently coloured "
                    "u/v/normal background sample at the knot-domain midpoint"
                    + (" · CAD face orientation is swapped (CAD-local note only, not sent to Kratos)"
                       if glyph["swapped_surface_normal"] else "")
                    + (" · degenerate normal: no normal arrow" if frame["degenerate"] else "")
                    + " · the midpoint can lie inside a trimming hole or outside the trimmed domain")

        if toggles.get("materials"):
            legend = material_legend(project, index)
            report["material_legend"] = legend
            report["materials"] = self._color_analysis_faces(legend["colors"], entities)

        try:
            doc.recompute()
        except Exception as error:  # noqa: BLE001 - a recompute failure is reported
            notes.append(f"document recompute reported: {error}")
        return report

    @staticmethod
    def _vector(point):
        import FreeCAD as App
        return App.Vector(*[float(component) for component in point[:3]])

    def _clamp_marks(self, target, radius):
        """A clearly schematic second line for the clamped adjacent control row.

        The offset is a diagram, not the final control net: the preview does not
        fabricate control-point positions it cannot compute exactly.
        """
        shapes = []
        frame = target.get("frame")
        if not frame:
            return shapes
        normal = frame.get("normal")
        if not normal:
            return shapes
        for curve in target["curves"]:
            if len(curve) < 2:
                continue
            shifted = [[point[axis] + normal[axis] * radius * 3.0 for axis in range(3)]
                       for point in curve]
            wire = self._wire(shifted)
            if wire is not None:
                shapes.append(wire)
        return shapes

    def _component_marks(self, target, radius):
        import Part
        shapes = []
        if not target["components"]:
            return shapes
        frame = target.get("frame")
        if frame is not None:
            point = frame["point"]
        elif target["curves"] and target["curves"][0]:
            curve = target["curves"][0]
            point = curve[len(curve) // 2]
        else:
            return shapes
        length = radius * 6.0
        for name in target["components"]:
            direction = {"X": [1.0, 0.0, 0.0], "Y": [0.0, 1.0, 0.0], "Z": [0.0, 0.0, 1.0]}[name]
            arrow = self._arrow(point, direction, length, radius * 0.8)
            if arrow is not None:
                shapes.append(arrow)
        return shapes
