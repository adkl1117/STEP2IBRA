"""Read-only, fail-closed reader for one run directory's ``results.vtkhdf`` (P0-6 S1).

This module exists for the **backend** Python (h5py/numpy), never for FreeCAD's
bundled Python, which has no h5py. It returns bounded, already-validated numpy
arrays and plain Python values so a GUI can consume them without touching HDF5.

What it establishes (and what it refuses to invent):

* The file is a VTKHDF ``UnstructuredGrid`` version 2.4 whose every count, shape,
  offset, connectivity index, cell type and numeric value is self-consistent and
  finite. Any malformed count, out-of-range index, non-monotone time, unsupported
  cell type, wrong format version, non-finite sample or oversize array raises
  :class:`ResultSchemaError`; nothing is repaired or clamped.
* The reference sampling geometry is reconstructed once as
  ``reference_points = stored_points - first_step_DISPLACEMENT`` and each frame is
  ``display_points = reference_points + scale * selected_step_DISPLACEMENT``.
  **Provenance:** that the stored ``Points`` already carry the first output step's
  displacement is a property of the target Kratos 10.4.3 writer, read off its source
  (the geometry group is written once, from the moved mesh) and confirmed on the three
  shipped P0-7 results (cantilever tip, two-patch shell, perforated plate). It is
  *not* proved by any arithmetic identity inside a single file: this module
  deliberately does not assert the tautology ``reference + first == Points``, and the
  CAD bounding-box bound below is only a plausibility check on the reconstructed
  geometry. A plain VTKHDF that merely looks self-consistent cannot certify the
  semantics of the writer that produced it.
* Only native ``results.vtkhdf`` is read. No sampling file, custom VTK output or
  sidecar record is produced or consumed.
* The plain VTKHDF carries **no face/UV mapping**. The reader reports
  ``mapping_state == "unavailable"`` and never pretends the display cells are the
  trimmed integration domain.

Model association is a full content comparison. The current project and CAD are
compared field by field against the exported copies; only a short enumerated list of
non-analysis project metadata is skipped - the two relocatable path fields
(``source_step``, ``profile``) and two legacy export-metadata fields that this module
never reads or checks - so moving a project directory cannot cause a false rejection
while ``solver.time_step``, ``loads``, ``supports``, ``materials`` and every other
analysis field must still match exactly. The CAD is compared in full. A missing
current project/CAD, an undecidable geometry, a ``run_report.json`` whose ``mode`` is
not ``solve``, or a case without ``move_mesh_flag == True`` all refuse to hand out a
solved frame.
"""
from copy import deepcopy
import json
import math
from pathlib import Path

import numpy as np

try:
    import h5py
except ImportError as error:  # pragma: no cover - the backend venv provides h5py
    raise ImportError(
        "kratos_iga.results needs h5py; run it with the backend Python "
        "(FreeCAD's bundled Python has no h5py).") from error

VTKHDF_GROUP = "VTKHDF"
SUPPORTED_TYPE = "UnstructuredGrid"
SUPPORTED_VERSIONS = ((2, 4),)
VTK_TRIANGLE = 5
VTK_QUAD = 9
CELL_WIDTHS = {VTK_TRIANGLE: 3, VTK_QUAD: 4}
DISPLACEMENT = "DISPLACEMENT"
COMPONENTS = ("X", "Y", "Z", "magnitude")
# A 2D shell is embedded in 3D; the displacement always carries three components.
EXPECTED_DISPLACEMENT_COMPONENTS = 3

# Bounded reads: a run directory that exceeds any of these is refused instead of
# being loaded into memory.
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_POINTS = 8_000_000
MAX_CELLS = 4_000_000
MAX_STEPS = 4096
MAX_CELL_WIDTH = 8
MAX_DISPLACEMENT_BYTES = 256 * 1024 * 1024
READ_CHUNK_ROWS = 262_144
# Upper bound for the (already validated) connectivity array.
MAX_CONNECTIVITY = 64_000_000

# Tolerances used only to compare a measured value with the run report, never to
# repair or extrapolate data.
COMPARISON_FLOOR = 1.0e-12
COMPARISON_RELATIVE = 1.0e-9
# The reconstructed reference sampling points must stay inside the CAD's
# control-point bounding box (export unit), expanded by this fraction of the box
# diagonal. Measured on the three shipped P0-7 results the reference sits inside the
# box to within 1e-15 mm, so 1% is a generous plausibility bound. It bounds the mesh;
# it does not prove the writer's mesh contract.
BOUNDS_RELATIVE_MARGIN = 1.0e-2

REQUIRED_DATASETS = (
    "Points", "Connectivity", "Offsets", "Types",
    "NumberOfPoints", "NumberOfCells", "NumberOfConnectivityIds",
)

# Exactly these project fields are excluded from the association comparison, and
# nothing else. ``source_step``/``profile`` carry only a relocatable path, and the two
# legacy export-metadata fields below hold values this module never reads or checks.
# Every other project field - most importantly ``solver`` (including ``time_step``),
# ``loads``, ``supports``, ``materials``, ``sections``, ``patches``, ``groups``,
# ``couplings`` and ``output`` - takes part in a full deep comparison. Substring or
# token matching is deliberately not used: it silently dropped ``solver.time_step``
# (the name contains "step") and would drop any field whose name happens to contain a
# path word.
PROJECT_EXCLUDED_TOP_LEVEL = ("source_step", "profile")
PROJECT_EXCLUDED_METADATA = ("source_sha256", "geometry_sha256")
# The converted CAD document is compared in full: it carries geometry only, with no
# path or export-metadata field to exclude. (Its ``curve_direction`` and
# ``relative_direction`` fields are orientation data, not paths, and stay compared.)


class ResultError(Exception):
    """Base class for every refusal raised by this module."""


class MissingInputError(ResultError):
    """A file the caller needs is absent."""


class ResultSchemaError(ResultError):
    """The VTKHDF/JSON content is malformed, unsupported or self-inconsistent."""


class ResultLimitError(ResultError):
    """The content exceeds a bounded-read limit."""


class AssociationError(ResultError):
    """The result does not verifiably belong to the caller's current model."""


def _default_limits():
    return {"max_file_bytes": MAX_FILE_BYTES, "max_points": MAX_POINTS,
            "max_cells": MAX_CELLS, "max_steps": MAX_STEPS,
            "max_cell_width": MAX_CELL_WIDTH,
            "max_connectivity": MAX_CONNECTIVITY,
            "max_displacement_bytes": MAX_DISPLACEMENT_BYTES}


# --------------------------------------------------------------------------- #
# small validation helpers
# --------------------------------------------------------------------------- #

def _count_at(group, name):
    """One VTKHDF count dataset: exactly one finite, non-negative integer."""
    if name not in group:
        raise ResultSchemaError(f"the VTKHDF group has no {name!r} dataset")
    raw = np.asarray(group[name][...])
    if raw.size != 1:
        raise ResultSchemaError(f"{name} must hold exactly one value, got shape {raw.shape}")
    value = raw.reshape(-1)[0]
    if not np.isfinite(value):
        raise ResultSchemaError(f"{name} is not finite: {value!r}")
    as_float = float(value)
    if as_float != math.floor(as_float) or as_float < 0.0:
        raise ResultSchemaError(f"{name} is not a non-negative integer: {value!r}")
    return int(as_float)


def _monotone_int_offsets(dataset, name, expected_last=None):
    raw = np.asarray(dataset[...])
    if raw.size == 0:
        raise ResultSchemaError(f"{name} is empty")
    if raw.dtype.kind not in "iu":
        raise ResultSchemaError(f"{name} must be an integer array, got {raw.dtype}")
    if raw.size > 1 and np.any(np.diff(raw) < 0):
        raise ResultSchemaError(f"{name} must be non-decreasing")
    if np.any(raw < 0):
        raise ResultSchemaError(f"{name} must not contain negative offsets")
    values = [int(v) for v in raw.reshape(-1)]
    if expected_last is not None and values[-1] != int(expected_last):
        raise ResultSchemaError(
            f"{name} ends at {values[-1]}, expected {int(expected_last)}")
    return values


def _sample_is_finite(dataset, name, columns):
    """Stream a 2-D dataset in row blocks and prove every sample is finite."""
    rows = int(dataset.shape[0])
    for start in range(0, rows, READ_CHUNK_ROWS):
        stop = min(start + READ_CHUNK_ROWS, rows)
        block = np.asarray(dataset[start:stop])
        if block.shape != (stop - start, columns):
            raise ResultSchemaError(
                f"{name} block {start}:{stop} has shape {block.shape}, expected "
                f"({stop - start}, {columns})")
        if not np.all(np.isfinite(block)):
            raise ResultSchemaError(f"{name} contains a non-finite sample")


def _as_python(value):
    """JSON-safe scalars only (no numpy types, no NaN/inf)."""
    if isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if not math.isfinite(number):
            raise ResultSchemaError(f"refusing to emit a non-finite number: {number!r}")
        return number
    raise ResultSchemaError(f"refusing to emit a non-scalar value: {type(value).__name__}")


def _finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(float(value)):
        raise ResultSchemaError(f"{name} must be a finite number, got {value!r}")
    return float(value)


# --------------------------------------------------------------------------- #
# content-only model association
# --------------------------------------------------------------------------- #

def _strip_project_metadata(value):
    """Drop exactly the enumerated non-analysis project fields, top level only.

    Nothing else is filtered and no field is matched by substring: a relocatable path
    or an exported metadata value is skipped, while every analysis field (including
    ``solver.time_step``) survives into the deep comparison.
    """
    if not isinstance(value, dict):
        raise AssociationError(
            "the project must be a JSON object to be compared")
    result = {}
    for key, item in value.items():
        name = str(key)
        if name in PROJECT_EXCLUDED_TOP_LEVEL or name in PROJECT_EXCLUDED_METADATA:
            continue
        result[name] = deepcopy(item)
    return result


def comparable_project(project):
    """The caller-visible, comparable project content (no relocatable metadata)."""
    return _strip_project_metadata(project)


def comparable_cad(cad):
    """The CAD content, compared in full: there is no CAD field to exclude."""
    if not isinstance(cad, dict):
        raise AssociationError("the CAD must be a JSON object to be compared")
    return deepcopy(cad)


def _first_difference(left, right, path="$"):
    """First content difference between the exported (left) and current (right) model."""
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            if key not in left:
                return f"{path}.{key} exists only in the current model"
            if key not in right:
                return f"{path}.{key} exists only in the exported model"
            found = _first_difference(left[key], right[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return f"{path} has {len(right)} entries, the exported model has {len(left)}"
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            found = _first_difference(left_item, right_item, f"{path}[{index}]")
            if found:
                return found
        return None
    if isinstance(left, bool) != isinstance(right, bool):
        return f"{path} is {right!r}, the exported model has {left!r}"
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if float(left) != float(right):
            return f"{path} is {right!r}, the exported model has {left!r}"
        return None
    if left != right:
        return f"{path} is {right!r}, the exported model has {left!r}"
    return None


def cad_control_points(cad):
    """All surface control points of a converted CAD document, or ``None``."""
    if not isinstance(cad, dict):
        return None
    points = []
    for brep in cad.get("breps") or []:
        for face in brep.get("faces") or []:
            surface = face.get("surface") or {}
            for control_point in surface.get("control_points") or []:
                if isinstance(control_point, (list, tuple)) and len(control_point) >= 2:
                    position = control_point[1]
                    if isinstance(position, (list, tuple)) and len(position) >= 3:
                        points.append([float(position[0]), float(position[1]),
                                       float(position[2])])
    if not points:
        return None
    array = np.asarray(points, dtype=float)
    if not np.all(np.isfinite(array)):
        raise ResultSchemaError("the CAD control points contain a non-finite coordinate")
    return array


def _cad_is_structural(cad):
    """A converted CAD document must expose at least one NURBS surface."""
    if not isinstance(cad, dict):
        return False
    for brep in cad.get("breps") or []:
        for face in brep.get("faces") or []:
            surface = face.get("surface")
            if isinstance(surface, dict) and surface.get("control_points") \
                    and surface.get("knot_vectors"):
                return True
    return False


# --------------------------------------------------------------------------- #
# VTKHDF validation
# --------------------------------------------------------------------------- #

class _Structure:
    """Everything read out of one validated VTKHDF group."""

    def __init__(self):
        self.points = None
        self.connectivity = None
        self.cell_offsets = None
        self.cell_types = None
        self.displacement = None
        self.step_times = []
        self.step_bounds = []
        self.cell_widths = []
        self.types_histogram = {}
        self.number_of_points = 0
        self.number_of_cells = 0
        self.number_of_connectivity_ids = 0
        self.file_bytes = 0
        self.hdf5_version = None
        self.warnings = []

    def metadata(self):
        return {
            "group": VTKHDF_GROUP,
            "format_type": SUPPORTED_TYPE,
            "format_version": list(SUPPORTED_VERSIONS[0]),
            "hdf5_version": self.hdf5_version,
            "file_bytes": self.file_bytes,
            "points": self.number_of_points,
            "cells": self.number_of_cells,
            "connectivity_ids": self.number_of_connectivity_ids,
            "cell_types": self.types_histogram,
            "cell_widths": list(self.cell_widths),
            "point_data_variables": [DISPLACEMENT],
            "displacement_components": EXPECTED_DISPLACEMENT_COMPONENTS,
            "steps": len(self.step_times),
            "times": [_as_python(t) for t in self.step_times],
            "step_point_counts": [stop - start for start, stop in self.step_bounds],
            "topology_static": True,
            "warnings": list(self.warnings),
        }


def _open_group(path):
    """Validate the file-level VTKHDF envelope and return ``(handle, group, size)``."""
    path = Path(path)
    if not path.is_file():
        raise MissingInputError(f"no VTKHDF file at {path}")
    try:
        size = path.stat().st_size
    except OSError as error:
        raise MissingInputError(f"cannot stat {path}: {error}") from error
    if size <= 0:
        raise ResultSchemaError(f"{path} is empty")
    handle = h5py.File(path, "r")
    if VTKHDF_GROUP not in handle:
        handle.close()
        raise ResultSchemaError(f"{path} has no {VTKHDF_GROUP!r} group")
    group = handle[VTKHDF_GROUP]
    if not isinstance(group, h5py.Group):
        handle.close()
        raise ResultSchemaError(f"{VTKHDF_GROUP!r} is not a group")
    attributes = dict(group.attrs)
    if str(attributes.get("Type", "")) != SUPPORTED_TYPE:
        handle.close()
        raise ResultSchemaError(
            f"{VTKHDF_GROUP}.Type is {attributes.get('Type')!r}, "
            f"only {SUPPORTED_TYPE!r} is supported")
    try:
        version = tuple(int(v) for v in np.asarray(attributes.get("Version", [])).reshape(-1))
    except (TypeError, ValueError) as error:
        handle.close()
        raise ResultSchemaError(
            f"{VTKHDF_GROUP}.Version is not an integer pair: "
            f"{attributes.get('Version')!r}") from error
    if version not in SUPPORTED_VERSIONS:
        handle.close()
        raise ResultSchemaError(
            f"{VTKHDF_GROUP}.Version is {version!r}, supported versions are "
            f"{SUPPORTED_VERSIONS!r}")
    return handle, group, size


def read_vtkhdf_structure(path, limits=None):
    """Validate one VTKHDF file and return its fully materialised structure."""
    limits = dict(_default_limits(), **(limits or {}))
    handle, group, size = _open_group(path)
    try:
        if size > limits["max_file_bytes"]:
            raise ResultLimitError(
                f"{path} is {size} bytes, above the {limits['max_file_bytes']} byte limit")
        for name in REQUIRED_DATASETS:
            if name not in group:
                raise ResultSchemaError(f"the VTKHDF group has no {name!r} dataset")

        structure = _Structure()
        structure.file_bytes = int(size)
        structure.hdf5_version = h5py.version.hdf5_version

        number_of_points = _count_at(group, "NumberOfPoints")
        number_of_cells = _count_at(group, "NumberOfCells")
        number_of_connectivity_ids = _count_at(group, "NumberOfConnectivityIds")
        if number_of_points <= 0:
            raise ResultSchemaError("NumberOfPoints must be positive")
        if number_of_cells <= 0:
            raise ResultSchemaError("NumberOfCells must be positive")
        if number_of_connectivity_ids <= 0:
            raise ResultSchemaError("NumberOfConnectivityIds must be positive")
        if number_of_points > limits["max_points"]:
            raise ResultLimitError(
                f"NumberOfPoints {number_of_points} is above the {limits['max_points']} limit")
        if number_of_cells > limits["max_cells"]:
            raise ResultLimitError(
                f"NumberOfCells {number_of_cells} is above the {limits['max_cells']} limit")
        if number_of_connectivity_ids > limits["max_connectivity"]:
            raise ResultLimitError(
                f"NumberOfConnectivityIds {number_of_connectivity_ids} is above the "
                f"{limits['max_connectivity']} limit")

        points = np.asarray(group["Points"][...])
        if points.shape != (number_of_points, 3):
            raise ResultSchemaError(
                f"Points has shape {points.shape}, expected ({number_of_points}, 3)")
        if points.dtype.kind not in "fiu":
            raise ResultSchemaError(f"Points must be numeric, got {points.dtype}")
        points = points.astype(float)
        if not np.all(np.isfinite(points)):
            raise ResultSchemaError("Points contains a non-finite coordinate")

        connectivity = np.asarray(group["Connectivity"][...])
        if connectivity.ndim != 1 or connectivity.size != number_of_connectivity_ids:
            raise ResultSchemaError(
                f"Connectivity has shape {connectivity.shape}, expected "
                f"({number_of_connectivity_ids},)")
        if connectivity.dtype.kind not in "iu":
            raise ResultSchemaError(
                f"Connectivity must be an integer array, got {connectivity.dtype}")
        connectivity = connectivity.astype(np.int64)
        if connectivity.size and (int(connectivity.min()) < 0
                                  or int(connectivity.max()) >= number_of_points):
            raise ResultSchemaError(
                f"Connectivity references a point index outside [0, {number_of_points})")

        cell_offsets = _monotone_int_offsets(
            group["Offsets"], "Offsets", expected_last=number_of_connectivity_ids)
        if len(cell_offsets) != number_of_cells + 1:
            raise ResultSchemaError(
                f"Offsets has {len(cell_offsets)} entries, expected {number_of_cells + 1}")

        cell_types = np.asarray(group["Types"][...])
        if cell_types.ndim != 1 or cell_types.size != number_of_cells:
            raise ResultSchemaError(
                f"Types has shape {cell_types.shape}, expected ({number_of_cells},)")
        if cell_types.dtype.kind not in "iu":
            raise ResultSchemaError(f"Types must be an integer array, got {cell_types.dtype}")
        types_histogram = {}
        cell_widths = []
        for index, raw_type in enumerate(cell_types.tolist()):
            kind = int(raw_type)
            if kind not in CELL_WIDTHS:
                raise ResultSchemaError(
                    f"cell {index} has unsupported VTK type {kind}; only "
                    f"{VTK_TRIANGLE} (triangle) and {VTK_QUAD} (quad) are supported")
            width = CELL_WIDTHS[kind]
            span = cell_offsets[index + 1] - cell_offsets[index]
            if span != width:
                raise ResultSchemaError(
                    f"cell {index} (type {kind}) spans {span} connectivity ids, expected {width}")
            if width > limits["max_cell_width"]:
                raise ResultLimitError(
                    f"cell width {width} is above the {limits['max_cell_width']} limit")
            cell_widths.append(width)
            types_histogram[str(kind)] = types_histogram.get(str(kind), 0) + 1
        structure.types_histogram = types_histogram
        structure.cell_widths = sorted(set(cell_widths))

        # --- displacement and its step bookkeeping --------------------------- #
        if "PointData" not in group:
            raise ResultSchemaError("the VTKHDF group has no PointData group")
        point_data = group["PointData"]
        if DISPLACEMENT not in point_data:
            raise ResultSchemaError(f"PointData has no {DISPLACEMENT!r} dataset")
        displacement = point_data[DISPLACEMENT]
        if len(displacement.shape) != 2 \
                or displacement.shape[1] != EXPECTED_DISPLACEMENT_COMPONENTS:
            raise ResultSchemaError(
                f"{DISPLACEMENT} has shape {displacement.shape}, expected (rows, "
                f"{EXPECTED_DISPLACEMENT_COMPONENTS})")
        if displacement.dtype.kind not in "fiu":
            raise ResultSchemaError(f"{DISPLACEMENT} must be numeric, got {displacement.dtype}")
        if displacement.nbytes > limits["max_displacement_bytes"]:
            raise ResultLimitError(
                f"{DISPLACEMENT} is {displacement.nbytes} bytes, above the "
                f"{limits['max_displacement_bytes']} byte limit")

        if "Steps" not in group:
            raise ResultSchemaError("the VTKHDF group has no Steps group")
        steps = group["Steps"]
        if "Values" not in steps:
            raise ResultSchemaError("Steps has no Values dataset")
        values = np.asarray(steps["Values"][...])
        if values.ndim != 1:
            raise ResultSchemaError(f"Steps/Values has shape {values.shape}, expected (steps,)")
        if values.dtype.kind not in "fiu":
            raise ResultSchemaError(f"Steps/Values must be numeric, got {values.dtype}")
        values = values.astype(float)
        if values.size == 0:
            raise ResultSchemaError("Steps/Values is empty")
        if values.size > limits["max_steps"]:
            raise ResultLimitError(
                f"{values.size} steps is above the {limits['max_steps']} limit")
        if not np.all(np.isfinite(values)):
            raise ResultSchemaError("Steps/Values contains a non-finite time")
        if values.size > 1 and np.any(np.diff(values) < 0.0):
            raise ResultSchemaError("Steps/Values is not non-decreasing in time")
        recorded = steps.attrs.get("NSteps")
        if recorded is not None:
            recorded_count = int(recorded)
            if recorded_count > int(values.size):
                raise ResultSchemaError(
                    f"Steps.NSteps is {recorded_count}, Values holds only "
                    f"{int(values.size)}; the file is truncated")
            if recorded_count != int(values.size):
                # The shipped Kratos 10.4.3 writer fixes up NSteps one step late, so a
                # single-step lag is a known, reportable defect rather than corruption.
                lag_warning = (f"Steps.NSteps is {recorded_count} but Values holds "
                               f"{int(values.size)} steps (known writer bookkeeping "
                               f"defect); the step list is taken from Values and the "
                               f"PointDataOffsets")
                structure.warnings.append(lag_warning)
        step_times = [float(v) for v in values.tolist()]

        if "PointDataOffsets" not in steps:
            raise ResultSchemaError("Steps has no PointDataOffsets group")
        offsets_group = steps["PointDataOffsets"]
        if DISPLACEMENT not in offsets_group:
            raise ResultSchemaError(
                f"Steps/PointDataOffsets has no {DISPLACEMENT!r} dataset")
        raw_offsets = np.asarray(offsets_group[DISPLACEMENT][...])
        if raw_offsets.ndim != 1 or raw_offsets.size != len(step_times):
            raise ResultSchemaError(
                f"Steps/PointDataOffsets/{DISPLACEMENT} has shape {raw_offsets.shape}, "
                f"expected ({len(step_times)},)")
        if raw_offsets.dtype.kind not in "iu":
            raise ResultSchemaError(
                f"Steps/PointDataOffsets/{DISPLACEMENT} must be an integer array, "
                f"got {raw_offsets.dtype}")
        raw_offsets = raw_offsets.astype(np.int64)
        if np.any(raw_offsets < 0):
            raise ResultSchemaError("Steps/PointDataOffsets contains a negative offset")
        rows, columns = int(displacement.shape[0]), int(displacement.shape[1])
        row_offsets = [int(v) for v in raw_offsets.tolist()]
        if row_offsets[0] != 0:
            raise ResultSchemaError(
                f"Steps/PointDataOffsets/{DISPLACEMENT} starts at {row_offsets[0]}, expected 0")
        if rows != number_of_points * len(step_times):
            raise ResultSchemaError(
                f"{DISPLACEMENT} has {rows} rows, expected NumberOfPoints "
                f"({number_of_points}) x steps ({len(step_times)}) = "
                f"{number_of_points * len(step_times)}")
        step_bounds = []
        for index, start in enumerate(row_offsets):
            stop = row_offsets[index + 1] if index + 1 < len(row_offsets) else rows
            if stop > rows:
                raise ResultSchemaError(
                    f"step {index} ends at row {stop}, beyond the {rows} displacement rows")
            if stop - start != number_of_points:
                raise ResultSchemaError(
                    f"step {index} spans {stop - start} rows, expected {number_of_points}")
            step_bounds.append((start, stop))
        structure.step_bounds = step_bounds
        structure.step_times = step_times

        _sample_is_finite(displacement, DISPLACEMENT, columns)
        displacement = np.asarray(displacement[...], dtype=float)

        structure.points = points
        structure.connectivity = connectivity
        structure.cell_offsets = np.asarray(cell_offsets, dtype=np.int64)
        structure.cell_types = cell_types
        structure.displacement = displacement
        structure.number_of_points = number_of_points
        structure.number_of_cells = number_of_cells
        structure.number_of_connectivity_ids = number_of_connectivity_ids
        return structure
    finally:
        handle.close()


# --------------------------------------------------------------------------- #
# run-directory context
# --------------------------------------------------------------------------- #

def _load_optional_json(path, name, reasons):
    path = Path(path)
    if not path.is_file():
        reasons.append(f"{name} is missing")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        reasons.append(f"{name} is unreadable: {error}")
        return None
    if not isinstance(payload, dict):
        reasons.append(f"{name} is not a JSON object")
        return None
    return payload


class CaseInputs:
    """The four JSON inputs of one run directory, each independently optional."""

    def __init__(self, run_dir):
        self.run_dir = Path(run_dir)
        self.reasons = []
        self.run_report = _load_optional_json(self.run_dir / "run_report.json",
                                              "run_report.json", self.reasons)
        self.export_report = _load_optional_json(self.run_dir / "export_report.json",
                                                 "export_report.json", self.reasons)
        self.project = _load_optional_json(self.run_dir / "project.ibra-project.json",
                                            "project.ibra-project.json", self.reasons)
        self.cad = _load_optional_json(self.run_dir / "geometry.cad.json",
                                       "geometry.cad.json", self.reasons)
        self.parameters = _load_optional_json(self.run_dir / "ProjectParameters.json",
                                              "ProjectParameters.json", self.reasons)

    def move_mesh_flag(self):
        settings = (self.parameters or {}).get("solver_settings")
        if not isinstance(settings, dict):
            return None
        value = settings.get("move_mesh_flag")
        return bool(value) if isinstance(value, bool) else None

    def geometry_units(self):
        units = (self.export_report or {}).get("geometry_units")
        if units in ("mm", "m"):
            return units
        project_units = (self.project or {}).get("units")
        if project_units == "m-N-s":
            return "m"
        if project_units == "mm-N-s":
            return "mm"
        return None


class ResultsContext:
    """One run directory plus the caller's current model, validated for association."""

    def __init__(self, run_dir, *, current_project=None, current_cad=None,
                 vtkhdf=None, parameters=None):
        self.run_dir = Path(run_dir)
        if not self.run_dir.is_dir():
            raise MissingInputError(f"no run directory at {self.run_dir}")
        self.reasons = []
        self.warnings = []
        self.inputs = CaseInputs(self.run_dir)
        self.reasons.extend(self.inputs.reasons)

        self.results_path = Path(vtkhdf) if vtkhdf is not None else self.run_dir / "results.vtkhdf"
        if not self.results_path.is_file():
            raise MissingInputError(f"no results.vtkhdf at {self.results_path}")

        self.move_mesh_flag = self._resolve_move_mesh_flag(parameters)
        self.geometry_units = self.inputs.geometry_units()
        if self.geometry_units is None:
            self.reasons.append(
                "the geometry units are unknown (no export_report.geometry_units and no "
                "project units)")

        self.mode = None
        if self.inputs.run_report is None:
            self.reasons.append("run_report.json is missing, so the solve phase is unknown")
        else:
            self.mode = self.inputs.run_report.get("mode")
            if self.mode != "solve":
                self.reasons.append(
                    f"run_report.mode is {self.mode!r}; solved results may not be displayed")

        self.current_project = current_project
        self.current_cad = current_cad
        if current_project is None or current_cad is None:
            self.reasons.append(
                "the current project and CAD are required to associate a result with the "
                "open model")
        else:
            self._check_association()

    def _resolve_move_mesh_flag(self, parameters):
        if parameters is not None:
            raw = parameters.get("move_mesh_flag") if isinstance(parameters, dict) \
                else getattr(parameters, "move_mesh_flag", None)
            if raw is None:
                self.reasons.append("the caller's parameters do not declare move_mesh_flag")
                return None
            if not isinstance(raw, bool):
                self.reasons.append(f"move_mesh_flag must be a boolean, got {raw!r}")
                return None
            return raw
        flag = self.inputs.move_mesh_flag()
        if flag is None:
            self.reasons.append(
                "ProjectParameters.json does not declare a boolean "
                "solver_settings.move_mesh_flag")
        return flag

    def _check_association(self):
        if self.inputs.project is None or self.inputs.cad is None:
            self.reasons.append(
                "the exported project/CAD is missing, so the result cannot be associated")
            return
        if not _cad_is_structural(self.current_cad):
            self.reasons.append(
                "the current CAD has no NURBS surface; the geometry cannot be checked")
            return
        difference = _first_difference(_strip_project_metadata(self.inputs.project),
                                       _strip_project_metadata(self.current_project))
        if difference:
            self.reasons.append(
                "the current project does not match the exported project: " + difference)
            return
        difference = _first_difference(comparable_cad(self.inputs.cad),
                                       comparable_cad(self.current_cad))
        if difference:
            self.reasons.append("the current CAD does not match the exported CAD: " + difference)
            return
        self.warnings.append(
            "the full current project (analysis fields) and the full CAD matched the "
            "exported model field by field; only the enumerated relocatable project "
            "metadata was skipped")

    def unit_scale_to_mm(self):
        if self.geometry_units == "mm":
            return 1.0
        if self.geometry_units == "m":
            return 1000.0
        raise ResultError("the geometry units are unknown; length conversion is not defined")

    def solved_ready(self):
        """True only when association, solve phase and the writer contract all hold."""
        return (not self.reasons and self.move_mesh_flag is True
                and self.mode == "solve" and self.geometry_units in ("mm", "m"))

    def describe(self):
        return {
            "run_dir": str(self.run_dir),
            "results_path": str(self.results_path),
            "mode": self.mode,
            "move_mesh_flag": self.move_mesh_flag,
            "geometry_units": self.geometry_units,
            "unit_scale_to_mm": (self.unit_scale_to_mm()
                                 if self.geometry_units in ("mm", "m") else None),
            "solved_ready": self.solved_ready(),
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "run_report_present": self.inputs.run_report is not None,
            "export_report_present": self.inputs.export_report is not None,
            "exported_project_present": self.inputs.project is not None,
            "exported_cad_present": self.inputs.cad is not None,
        }


# --------------------------------------------------------------------------- #
# public reader
# --------------------------------------------------------------------------- #

class ResultsReader:
    """Read one validated run directory. Every refusal is a :class:`ResultError`."""

    def __init__(self, run_dir, *, current_project=None, current_cad=None,
                 vtkhdf=None, parameters=None, limits=None):
        self.limits = dict(_default_limits(), **(limits or {}))
        self.context = ResultsContext(run_dir, current_project=current_project,
                                      current_cad=current_cad, vtkhdf=vtkhdf,
                                      parameters=parameters)
        self._structure = None

    # -- validation -------------------------------------------------------- #
    def touch(self):
        """Validate the file without computing a frame; idempotent."""
        return self._structure_or_read().metadata()

    def _structure_or_read(self):
        if self._structure is None:
            self._structure = read_vtkhdf_structure(self.context.results_path, self.limits)
        return self._structure

    def _baseline(self):
        """Reference sampling points: stored points minus the first step's displacement.

        This is the writer contract read off the target Kratos 10.4.3 code (geometry is
        written once from ``Points``) together with the measured behaviour of the three
        shipped P0-7 results: the stored ``Points`` already carry the first output
        step's displacement, so the undeformed sampling geometry is recovered by one
        subtraction. The identity ``reference + first == Points`` holds by construction
        and proves nothing; it is deliberately not asserted here.
        """
        structure = self._structure_or_read()
        first = structure.displacement[0:structure.number_of_points]
        reference = structure.points - first
        if not np.all(np.isfinite(reference)):
            raise ResultSchemaError(
                "stored Points minus the first step displacement is not finite; the "
                "move_mesh_flag=True mesh contract cannot be honoured")
        return reference, first

    def _geometry_check(self, reference):
        """Bound the reference sampling points with the CAD control-point box.

        This is a plausibility bound on the reconstructed geometry, not a proof that the
        stored ``Points`` were deformed: the writer contract itself is established by the
        target Kratos code and the three shipped cases, never by a self-consistent file
        alone.
        """
        control_points = cad_control_points(self.context.current_cad) \
            if self.context.current_cad is not None else None
        if control_points is None:
            return {"status": "unavailable",
                    "reason": "the CAD carries no control points to bound the sampling domain"}
        lower = control_points.min(axis=0)
        upper = control_points.max(axis=0)
        diagonal = float(np.linalg.norm(upper - lower))
        margin = BOUNDS_RELATIVE_MARGIN * max(diagonal, 1.0)
        if np.any(reference < lower - margin) or np.any(reference > upper + margin):
            overshoot = float(max(0.0, np.max(lower - reference), np.max(reference - upper)))
            raise ResultSchemaError(
                "the reference sampling points leave the CAD control-point bounding box by "
                f"{overshoot!r} (allowed margin {margin!r}); the display geometry is not "
                "plausibly this model")
        return {"status": "within_bounds", "margin": margin,
                "lower": [float(v) for v in lower], "upper": [float(v) for v in upper],
                "reason": ("reference sampling points stay inside the CAD control-point "
                           "bounding box; this bounds the mesh, it does not prove the writer "
                           "contract, and face/UV mapping is still unavailable")}

    # -- operations -------------------------------------------------------- #
    def metadata(self):
        structure = self._structure_or_read()
        return {
            "case": self.context.describe(),
            "vtkhdf": structure.metadata(),
            "mapping_state": "unavailable",
            "mapping_note": ("a plain VTKHDF carries no face/UV mapping; the display cells "
                             "are not the trimmed integration domain"),
        }

    def displacement_extrema(self, step, component, *, scale=1.0, unit="mm"):
        """Component extrema of one step, with positions on the **displayed** geometry.

        ``max_position``/``min_position`` belong to ``reference + scale * step`` in the
        requested unit, so a scaled display and the reported extreme always agree. They
        are never taken from the stored first-step ``Points``.
        """
        structure = self._structure_or_read()
        self._check_step(step)
        if component not in COMPONENTS:
            raise ResultSchemaError(
                f"component must be one of {COMPONENTS}, got {component!r}")
        scale = _finite_number(scale, "scale")
        if scale < 0.0:
            raise ResultSchemaError(f"scale must not be negative, got {scale!r}")
        if unit not in ("mm", "m"):
            raise ResultSchemaError(f"unit must be 'mm' or 'm', got {unit!r}")
        reference, _first = self._baseline()
        rows = structure.displacement[slice(*structure.step_bounds[step])]
        display = reference + scale * rows
        values = self._component_array(rows, component)
        factor = self.context.unit_scale_to_mm() * (1.0 if unit == "mm" else 0.001)
        entries = {}
        for name in COMPONENTS:
            column = self._component_array(rows, name)
            if column.size == 0:
                raise ResultSchemaError(f"the {name} displacement of step {step} is empty")
            if not np.all(np.isfinite(column)):
                raise ResultSchemaError(
                    f"the {name} displacement of step {step} contains a non-finite value")
            high = int(np.argmax(column))
            low = int(np.argmin(column))
            entries[name] = {
                "min": float(column[low]) * factor,
                "max": float(column[high]) * factor,
                "min_index": low,
                "max_index": high,
                "max_position": [float(v) for v in display[high] * factor],
                "min_position": [float(v) for v in display[low] * factor],
                "unit": unit,
            }
        if not np.all(np.isfinite(values)):
            raise ResultSchemaError(
                f"the {component} displacement of step {step} contains a non-finite value")
        return entries

    @staticmethod
    def _component_array(rows, component):
        if component == "magnitude":
            return np.sqrt(rows[:, 0] ** 2 + rows[:, 1] ** 2 + rows[:, 2] ** 2)
        return rows[:, COMPONENTS.index(component)]

    def component_values(self, step, component):
        structure = self._structure_or_read()
        self._check_step(step)
        if component not in COMPONENTS:
            raise ResultSchemaError(
                f"component must be one of {COMPONENTS}, got {component!r}")
        rows = structure.displacement[slice(*structure.step_bounds[step])]
        values = self._component_array(rows, component)
        if not np.all(np.isfinite(values)):
            raise ResultSchemaError(
                f"the {component} displacement of step {step} contains a non-finite value")
        return np.ascontiguousarray(values, dtype=float)

    def _check_step(self, step):
        structure = self._structure_or_read()
        if isinstance(step, bool) or not isinstance(step, int):
            raise ResultSchemaError(f"step must be an integer index, got {step!r}")
        if step < 0 or step >= len(structure.step_bounds):
            raise ResultSchemaError(
                f"step {step} is outside [0, {len(structure.step_bounds)})")
        return structure

    def read_frame(self, *, step=0, scale=1.0, component="magnitude", unit="mm",
                   require_solved=True):
        """Return one display frame in the requested length unit.

        ``scale`` multiplies the selected step's displacement only; ``0.0`` restores
        the reference sampling geometry and ``1.0`` reproduces the stored points of
        the corresponding step.
        """
        structure = self._check_step(step)
        if require_solved and not self.context.solved_ready():
            raise AssociationError(
                "this run directory cannot be displayed as a solved result: "
                + "; ".join(self.context.reasons or ["unsupported writer contract"]))
        scale = _finite_number(scale, "scale")
        if scale < 0.0:
            raise ResultSchemaError(f"scale must not be negative, got {scale!r}")
        if unit not in ("mm", "m"):
            raise ResultSchemaError(f"unit must be 'mm' or 'm', got {unit!r}")

        reference, _first = self._baseline()
        geometry_check = self._geometry_check(reference)
        rows = structure.displacement[slice(*structure.step_bounds[step])]
        display = reference + scale * rows
        if not np.all(np.isfinite(display)):
            raise ResultSchemaError(
                f"the deformed points of step {step} at scale {scale} are not finite")

        report = self.context.inputs.run_report or {}
        reported = report.get("max_abs_displacement")
        frame_max = float(np.max(np.abs(rows))) if rows.size else 0.0
        consistency = "unavailable"
        reported_value = None
        if isinstance(reported, (int, float)) and not isinstance(reported, bool) \
                and math.isfinite(float(reported)):
            tolerance = COMPARISON_FLOOR + COMPARISON_RELATIVE * max(
                abs(float(reported)), frame_max)
            consistency = "match" if abs(frame_max - float(reported)) <= tolerance \
                else "mismatch"
            reported_value = float(reported)

        factor = self.context.unit_scale_to_mm() * (1.0 if unit == "mm" else 0.001)
        # Extrema are computed on the displayed geometry for this step and scale, so the
        # reported position cannot lag behind a scaled or later-step frame.
        extrema = self.displacement_extrema(step, component, scale=scale, unit=unit)
        values = self.component_values(step, component)
        return {
            "case": self.context.describe(),
            "step": {
                "index": int(step),
                "time": float(structure.step_times[step]),
                "scale": scale,
                "component": component,
                "requested_unit": unit,
                "source_unit": self.context.geometry_units,
                "unit_factor": factor,
                "max_abs_displacement": frame_max,
                "max_abs_displacement_unit": self.context.geometry_units,
            },
            "extrema": extrema,
            "run_report_max_abs_displacement": reported_value,
            "run_report_consistency": consistency,
            "points_reference": np.ascontiguousarray(reference * factor),
            "points_display": np.ascontiguousarray(display * factor),
            "displacement": np.ascontiguousarray(rows * factor),
            "displacement_values": np.ascontiguousarray(values * factor),
            "mesh": {
                "connectivity": structure.connectivity,
                "offsets": structure.cell_offsets,
                "types": structure.cell_types,
                "cells": structure.number_of_cells,
                "points": structure.number_of_points,
                "cell_types": structure.types_histogram,
                "mapping_state": "unavailable",
            },
            "unit": unit,
            "geometry_check": geometry_check,
            "mapping_state": "unavailable",
            "warnings": list(self.context.warnings),
        }


def read_case(run_dir, *, current_project, current_cad, vtkhdf=None, parameters=None,
              limits=None):
    """Convenience constructor for a reader on one run directory."""
    return ResultsReader(run_dir, current_project=current_project, current_cad=current_cad,
                         vtkhdf=vtkhdf, parameters=parameters, limits=limits)
