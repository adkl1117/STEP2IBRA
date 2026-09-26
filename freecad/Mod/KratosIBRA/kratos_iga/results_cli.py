"""Backend JSON bridge from FreeCAD's h5py-less Python to the native VTKHDF reader.

FreeCAD 1.1.3's bundled Python has no h5py, so the FreeCAD process cannot read
``results.vtkhdf`` itself. This module is the other half of that boundary: it runs with
the **backend** Python (the same interpreter that produced the run), reads one bounded
JSON request on stdin and writes exactly one bounded JSON object on stdout. Nothing is
written to disk and no sampling file, custom VTK or sidecar record is produced or
consumed - the only result source is the Kratos IGA module's own ``results.vtkhdf``.

Request (one JSON object on stdin)::

    {"action": "metadata" | "frame",
     "run_dir": "<one isolated run directory>",
     "current_project": {<the open project>},
     "current_cad_mm": {<the FreeCAD cache CAD, always in mm>},
     "step": 0, "scale": 1.0, "component": "magnitude"}

``current_cad_mm`` may also be spelled ``current_cad``; either way it is the CAD the
FreeCAD cache holds, which is always in millimetres. When the project units are
``m-N-s`` the exported case was written in metres, so this module deep-copies the CAD
**in memory only** and scales the surface / 3D-curve control points and
``tolerances.model_tolerance`` by 0.001 exactly like :func:`kratos_iga.bundle.make_bundle`,
then hands the converted copy to :func:`kratos_iga.results.read_case` for the full
content comparison. The caller's CAD is never mutated and never re-written.

Association is fail-closed for **both** actions: a run whose project/CAD does not match
the open model field by field, a run that was not a ``solve``, a run without
``move_mesh_flag == True``, or an unreadable/malformed file refuses with a non-empty
stderr line and a non-zero exit code. A frame above the display caps refuses as well:
truncating cells would silently display a different domain than the file holds.

Exit codes: ``0`` success, ``1`` expected refusal (wrong model, non-solve, malformed or
oversized result, backend without h5py), ``2`` malformed request. On any non-zero exit
stdout still carries exactly one JSON object of the shape
``{"ok": false, "action": ..., "error": {"type": ..., "message": ...}}`` and stderr
carries the readable one-line reason.

The plain VTKHDF carries no face/UV mapping. Every response therefore states
``mapping_state: "unavailable"``: the display cells are the native sampling elements,
never the trimmed integration domain.
"""
import json
from copy import deepcopy
from pathlib import Path
import sys

# Import the reader next to us whether this file is imported as a package member or
# executed as a script (FreeCAD starts the backend Python with the file path).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from kratos_iga import results
except ImportError as error:  # pragma: no cover - the backend venv always has h5py
    results = None
    RESULTS_IMPORT_ERROR = error
else:
    RESULTS_IMPORT_ERROR = None

ACTIONS = ("metadata", "frame")
# Bound the request and the response so a malformed caller cannot exhaust memory, and
# so a huge mesh cannot be handed to the GUI in one string.
MAX_REQUEST_BYTES = 64 * 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
# Display caps for one frame. A larger mesh is refused, never truncated: half a mesh is
# a different domain and would be a false picture of the result.
MAX_DISPLAY_CELLS = 10_000
MAX_DISPLAY_POINTS = 50_000
# The frame is consumed by FreeCAD, whose cache geometry is in millimetres.
FRAME_UNIT = "mm"
EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_BAD_REQUEST = 2
MAX_ERROR_CHARS = 4000


class RequestError(Exception):
    """The caller's request is malformed; nothing is read and nothing is shown."""


class BridgeError(Exception):
    """The request is well-formed but the result cannot be handed out."""


# --------------------------------------------------------------------------- #
# request / response plumbing
# --------------------------------------------------------------------------- #

def read_stdin_request(stream=None):
    """Read at most :data:`MAX_REQUEST_BYTES` from ``stdin`` and return the text."""
    stream = getattr(stream, "buffer", stream) if stream is not None else sys.stdin.buffer
    raw = stream.read(MAX_REQUEST_BYTES + 1)
    if isinstance(raw, bytes):
        if len(raw) > MAX_REQUEST_BYTES:
            raise RequestError(
                f"the request on stdin is above the {MAX_REQUEST_BYTES} byte limit")
        try:
            raw = raw.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise RequestError(f"the request on stdin is not UTF-8: {error}") from error
    elif len(raw) > MAX_REQUEST_BYTES:
        raise RequestError(
            f"the request on stdin is above the {MAX_REQUEST_BYTES} byte limit")
    return raw


def parse_request(raw):
    """Validate the request envelope; field-level validation stays in ``results.py``."""
    if not isinstance(raw, str) or not raw.strip():
        raise RequestError("the request on stdin is empty")
    try:
        request = json.loads(raw)
    except ValueError as error:
        raise RequestError(f"stdin is not valid JSON: {error}") from error
    if not isinstance(request, dict):
        raise RequestError("the request must be a JSON object")
    action = request.get("action")
    if action not in ACTIONS:
        raise RequestError(f"action must be one of {ACTIONS}, got {action!r}")
    run_dir = request.get("run_dir")
    if not isinstance(run_dir, str) or not run_dir.strip():
        raise RequestError("run_dir must be a non-empty path string")
    if not isinstance(request.get("current_project"), dict):
        raise RequestError("current_project must be the current project JSON object")
    cad = request.get("current_cad_mm", request.get("current_cad"))
    if not isinstance(cad, dict):
        raise RequestError(
            "current_cad_mm must be the FreeCAD cache CAD JSON object (millimetres)")
    return request


def cad_in_export_units(project, cad_mm):
    """Deep-copy the current CAD and express it in the project's export unit.

    The FreeCAD cache CAD is always millimetres; an ``m-N-s`` project was exported in
    metres by :func:`kratos_iga.bundle.make_bundle`. The transformation is the same one
    ``make_bundle`` applies - surface and 3D-curve control points plus
    ``tolerances.model_tolerance`` times 0.001 - so the content comparison in
    ``results.py`` compares like with like. The input is never mutated.
    """
    cad = deepcopy(cad_mm)
    units = project.get("units") if isinstance(project, dict) else None
    if units != "m-N-s":
        return cad
    scale = 0.001
    for brep in cad.get("breps") or []:
        if not isinstance(brep, dict):
            continue
        entities = [face.get("surface") for face in brep.get("faces") or []
                    if isinstance(face, dict)]
        entities += [edge.get("3d_curve") for edge in brep.get("edges") or []
                     if isinstance(edge, dict) and "3d_curve" in edge]
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            for control_point in entity.get("control_points") or []:
                if not isinstance(control_point, (list, tuple)) or len(control_point) < 2:
                    continue
                position = control_point[1]
                if isinstance(position, list) and len(position) >= 3:
                    position[:3] = [float(value) * scale for value in position[:3]]
    tolerances = cad.get("tolerances")
    if isinstance(tolerances, dict):
        tolerance = tolerances.get("model_tolerance")
        if isinstance(tolerance, (int, float)) and not isinstance(tolerance, bool):
            tolerances["model_tolerance"] = float(tolerance) * scale
    return cad


def encode_payload(payload):
    """Serialise one JSON-safe payload under the response byte cap."""
    try:
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise BridgeError(f"the result payload is not JSON-serialisable: {error}") from error
    if len(text.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise BridgeError(
            f"the result payload is above the {MAX_RESPONSE_BYTES} byte response limit")
    return text


# --------------------------------------------------------------------------- #
# actions
# --------------------------------------------------------------------------- #

def _reader(request):
    if results is None:  # pragma: no cover - the backend venv always has h5py
        raise BridgeError(
            "the backend result reader is unavailable (h5py missing): "
            f"{RESULTS_IMPORT_ERROR}")
    project = request["current_project"]
    cad = cad_in_export_units(
        project, request.get("current_cad_mm", request.get("current_cad")))
    return results.ResultsReader(request["run_dir"], current_project=project,
                                 current_cad=cad)


def _require_solved(reader):
    """Both actions refuse a run that is not a verifiably solved result."""
    if reader.context.solved_ready():
        return
    reasons = reader.context.reasons or [
        "the run directory is not a verifiably solved result"]
    raise results.AssociationError("refusing to display this run: " + "; ".join(reasons))


def metadata_payload(reader):
    document = reader.metadata()
    payload = {"ok": True, "action": "metadata",
               "run_dir": str(reader.context.run_dir)}
    payload.update(document)
    return payload


def frame_payload(reader, request):
    document = reader.metadata()
    recorded = document["vtkhdf"]
    if int(recorded["cells"]) > MAX_DISPLAY_CELLS:
        raise results.ResultLimitError(
            f"the result has {int(recorded['cells'])} cells, above the "
            f"{MAX_DISPLAY_CELLS} cell display cap; refusing to draw a truncated domain")
    if int(recorded["points"]) > MAX_DISPLAY_POINTS:
        raise results.ResultLimitError(
            f"the result has {int(recorded['points'])} points, above the "
            f"{MAX_DISPLAY_POINTS} point display cap; refusing to draw a truncated domain")
    frame = reader.read_frame(step=request.get("step", 0), scale=request.get("scale", 1.0),
                              component=request.get("component", "magnitude"),
                              unit=FRAME_UNIT)
    mesh = frame["mesh"]
    return {
        "ok": True,
        "action": "frame",
        "run_dir": str(reader.context.run_dir),
        "case": frame["case"],
        "mapping_state": frame["mapping_state"],
        "mapping_note": document["mapping_note"],
        "unit": frame["unit"],
        "step": frame["step"],
        "extrema": frame["extrema"],
        # numpy -> JSON arrays happens exactly here; every value was already proved
        # finite by results.py, so allow_nan=False cannot hide a bad sample.
        "points": frame["points_display"].tolist(),
        "values": frame["displacement_values"].tolist(),
        "mesh": {
            "connectivity": mesh["connectivity"].tolist(),
            "offsets": mesh["offsets"].tolist(),
            "types": mesh["types"].tolist(),
            "cells": int(mesh["cells"]),
            "points": int(mesh["points"]),
            "cell_types": mesh["cell_types"],
            "mapping_state": mesh["mapping_state"],
        },
        "display_limits": {"max_cells": MAX_DISPLAY_CELLS,
                           "max_points": MAX_DISPLAY_POINTS},
        "geometry_check": frame["geometry_check"],
        "run_report_max_abs_displacement": frame["run_report_max_abs_displacement"],
        "run_report_consistency": frame["run_report_consistency"],
        "warnings": frame["warnings"],
    }


def handle(request):
    """Run one validated request and return the JSON-safe payload."""
    reader = _reader(request)
    _require_solved(reader)
    if request["action"] == "metadata":
        return metadata_payload(reader)
    return frame_payload(reader, request)


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #

def _shorten(message):
    text = str(message) or "no detail"
    if len(text) > MAX_ERROR_CHARS:
        text = text[:MAX_ERROR_CHARS] + " …"
    return text


def emit_error(action, error, code):
    """Write exactly one JSON error object to stdout plus one readable stderr line."""
    name = type(error).__name__
    message = _shorten(error)
    payload = {"ok": False, "action": action,
               "error": {"type": name, "message": message}}
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):  # pragma: no cover - a name/message is always text
        text = json.dumps({"ok": False, "action": action,
                           "error": {"type": name,
                                     "message": "the error could not be described"}})
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    sys.stderr.write(f"results_cli: {name}: {message}\n")
    sys.stderr.flush()
    return code


def main(argv=None):
    action = None
    try:
        request = parse_request(read_stdin_request())
        action = request["action"]
        payload = handle(request)
    except RequestError as error:
        return emit_error(action, error, EXIT_BAD_REQUEST)
    except Exception as error:  # noqa: BLE001 - every refusal is a bounded JSON report
        return emit_error(action, error, EXIT_REFUSED)
    try:
        text = encode_payload(payload)
    except BridgeError as error:
        return emit_error(action, error, EXIT_REFUSED)
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
