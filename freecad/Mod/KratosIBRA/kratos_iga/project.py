"""Project validation; stdlib only, usable in FreeCAD and the backend."""
import json
import math
import os
from copy import deepcopy
from pathlib import Path

from .coupling_diagnostics import (ORIGINS as COUPLING_ORIGINS, STATES as COUPLING_STATES,
                                   UNREVIEWED_ORIGINS, evidence_errors,
                                   missing_evidence_message, new_candidate,
                                   normalize_coupling)
from .groups import (KINDS as GROUP_KINDS, SIDES, TARGET_KEYS, active_face_ids,
                     coupling_is_active, edge_is_active, expand, regions_overlap,
                     resolve, row_locator, strong_spec, target_kinds, target_locations)

# The amplitude table lives in the runtime directory (it is copied into every export
# next to ibra_case_process.py). Import it relatively; fall back to a file-path load
# for loaders that exec the source file without the package on sys.path.
try:
    from .runtime import ibra_amplitude
except Exception:  # noqa: BLE001 - namespace-package fallback
    import importlib.util as _importlib_util
    _AMPLITUDE_PATH = Path(__file__).resolve().parent / "runtime" / "ibra_amplitude.py"
    _amplitude_spec = _importlib_util.spec_from_file_location(
        "kratos_iga_runtime_ibra_amplitude", _AMPLITUDE_PATH)
    ibra_amplitude = _importlib_util.module_from_spec(_amplitude_spec)
    _amplitude_spec.loader.exec_module(ibra_amplitude)

SCHEMA = 3
LEGACY_SCHEMA = 1
# Every schema this build can read; ``migrate_project`` brings them all to ``SCHEMA``.
SUPPORTED_SCHEMAS = (1, 2, 3)
ELEMENTS = {"Shell3pElement": 2, "IgaMembraneElement": 1}
UNIT_SYSTEMS = ("mm-N-s", "m-N-s")
# Load kinds. ``vector`` (or absent) is the unchanged legacy record; the only new
# kind is dead/reference pressure. Follower/current-normal pressure is explicitly
# unsupported until it is numerically validated with load stiffness.
LOAD_KINDS = ("vector", "reference_pressure")
UNSUPPORTED_LOAD_KINDS = ("follower_pressure", "current_normal_pressure", "pressure_follower",
                          "follower", "current_normal")
# value_in_target = value_in_source * ratio ** length_exponent, ratio being the size
# of one source length unit expressed in target length units.
_UNIT_RATIO = {("mm-N-s", "m-N-s"): 1e-3, ("m-N-s", "mm-N-s"): 1e3}
# Exponents follow from force (N) and time (s) being fixed; the mass unit is then
# N*s^2/L, so mass density scales as L**-4.
_UNIT_EXPONENT = {"length": 1, "line_load": -1, "stress": -2, "density": -4}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def read_project(path):
    path = Path(path).resolve()
    project = read_json(path)
    for key in ("source_step", "profile"):
        if project.get(key):
            project[key] = str((path.parent / project[key]).resolve())
    return migrate_project(project)


def migrate_project(project):
    """Return a schema-3 copy of a project without changing any physical value.

    Schema 1 stored one thickness per material. Migration gives each legacy material
    its own section with the identical numerical thickness and points every patch at
    the section matching its material, so the resolved Kratos properties are unchanged.

    Schema 2 introduced interface decisions. Migration normalises every coupling,
    including schema-2 files saved before the interface state existed: the legacy
    ``enabled`` flag is translated into ``state``/``origin`` and removed, so a saved
    project has exactly one source of truth. A legacy ``enabled=true`` was written
    automatically by ``populate()``, so it is marked unreviewed rather than treated as
    a user confirmation.

    Schema 3 adds the analysis domain and named groups. Every patch without ``active``
    becomes active, so a migrated project exports exactly the same entities as before;
    ``groups`` defaults to empty and every legacy condition keeps its direct target
    reference (``target_kind``/``target_id`` are not rewritten).
    """
    if project.get("schema_version") not in SUPPORTED_SCHEMAS:
        raise ValueError("Unsupported project schema version.")
    migrated = deepcopy(project)
    if migrated.get("schema_version") == LEGACY_SCHEMA:
        sections = []
        for material in migrated.get("materials", []):
            sections.append({"id": material["id"], "name": material.get("name", "Section"),
                             "thickness": material.pop("thickness", None)})
        migrated["sections"] = sections
        migrated["schema_version"] = 2
    if migrated.get("schema_version") == 2:
        migrated["schema_version"] = SCHEMA
    for patch in migrated.get("patches", []):
        patch.setdefault("section_id", patch.get("material_id"))
        patch.setdefault("active", True)
    migrated.setdefault("groups", [])
    couplings = []
    for record in migrated.get("couplings", []):
        canonical = normalize_coupling(record)
        if canonical is None:
            raise ValueError("Unsupported coupling record in the project file.")
        couplings.append(canonical)
    migrated["couplings"] = couplings
    return migrated


def write_project(path, project):
    path = Path(path).resolve()
    data = deepcopy(project)
    for key in ("source_step", "profile"):
        if data.get(key):
            try:
                data[key] = Path(os.path.relpath(data[key], path.parent)).as_posix()
            except ValueError:  # Different Windows drives cannot use relative paths.
                data[key] = str(Path(data[key]).resolve())
    write_json(path, data)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(tmp, path)


def new_project():
    return {"schema_version": SCHEMA, "source_step": "", "source_sha256": "", "profile": "",
            "units": "mm-N-s", "materials": [{"id": 1, "name": "Steel", "young": 210000.0,
                "poisson": 0.3, "density": 7.85e-9}],
            "sections": [{"id": 1, "name": "Steel 1.0 mm", "thickness": 1.0}],
            "refinement_enabled": False,
            "patches": [], "groups": [], "supports": [], "loads": [], "initial_conditions": [],
            "couplings": [], "solver": {"type": "static", "analysis_type": "linear",
                "start_time": 0.0, "end_time": 1.0, "time_step": 1.0},
            "output": {"vtk": True, "refinement": 4}}


def populate(project, index):
    project["patches"] = [{"face_id": f["id"], "material_id": 1, "section_id": 1,
        "element": "Shell3pElement", "active": True,
        "degree_u": max(2, f["degrees"][0]), "degree_v": max(2, f["degrees"][1]),
        "insert_u": 1, "insert_v": 1, "quadrature": 0} for f in index["faces"]]
    project["couplings"] = [new_candidate(e["id"]) for e in index["edges"]
                            if e["kind"] == "coupling"]


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _group_errors(project, index):
    """Validate group definitions and return ``(groups, errors, warnings)``."""
    errors, warnings = [], []
    groups = {}
    for group in project.get("groups", []):
        if not isinstance(group, dict):
            errors.append("Every named group must be an object with an id, a name, a kind and members.")
            continue
        ident = group.get("id")
        if not isinstance(ident, int) or isinstance(ident, bool):
            errors.append("Every named group needs an integer ID.")
            continue
        label = group.get("name") or f"group {ident}"
        if ident in groups:
            errors.append(f"Duplicate group ID {ident}: each named group may appear at most once.")
            continue
        groups[ident] = group
        if not isinstance(group.get("name"), str) or not group["name"].strip():
            errors.append(f"Group {ident}: a name is required.")
        if group.get("kind") not in GROUP_KINDS:
            errors.append(f"Group {ident} ({label}): kind must be one of {', '.join(GROUP_KINDS)}.")
            continue
        members = group.get("members")
        if not isinstance(members, list) or not members:
            errors.append(f"Group {ident} ({label}): the member list must contain at least one "
                          f"{group['kind']} id.")
            continue
        available = ({f["id"] for f in index.get("faces", [])} if group["kind"] == "face"
                     else {e["id"] for e in index.get("edges", [])})
        # Face and edge ids come from separate counters and may legitimately collide,
        # so a wrong-kind member is only one that exists *solely* as the other kind.
        other_kind = ({e["id"] for e in index.get("edges", [])} if group["kind"] == "face"
                      else {f["id"] for f in index.get("faces", [])})
        wrong_kind = [m for m in members if isinstance(m, int) and not isinstance(m, bool)
                      and m in other_kind and m not in available]
        invalid = [m for m in members if not isinstance(m, int) or isinstance(m, bool)]
        dangling = sorted({m for m in members if isinstance(m, int) and not isinstance(m, bool)
                           and m not in available})
        if invalid:
            errors.append(f"Group {ident} ({label}): members must be integer {group['kind']} IDs.")
        if wrong_kind:
            errors.append(f"Group {ident} ({label}) is a {group['kind']} group but lists edge "
                          f"ID(s) {sorted(wrong_kind)}.")
        if dangling:
            errors.append(f"Group {ident} ({label}): member ID(s) {dangling} are not in the current "
                          "geometry cache. Reimport the source STEP or remove them from the group.")
    return groups, errors, warnings


def condition_targets(record, groups, index):
    """Resolve one condition record into concrete target ids.

    Returns ``(kind, ids, missing)``; ``missing`` are ids the caller must report. A
    record that carries both a group reference and a direct target is contradictory and
    is reported by the caller. This wrapper keeps every consumer on ``groups.resolve``.
    """
    resolved = resolve(record, groups, index)
    return resolved["kind"], resolved["ids"], resolved["missing"]


def _time_reaches(interval, solver):
    return solver["end_time"] if interval[1] == "End" else interval[1]


def strong_overlap(record, other):
    """Shared control point, shared component and overlapping active time.

    Both records carry every ``(face, local, region, mask)`` entry their targets
    resolve to (group members included); ``region`` is an exact set of final-grid
    ``(u, v)`` control indices, so a u0/v0 corner, a whole-face selection and a
    clamped neighbour row are compared at the control-point level.
    """
    if not (set(record["components"]) & set(other["components"])):
        return None
    for face_id, local, region, mask in record["locations"]:
        for other_face, other_local, other_region, other_mask in other["locations"]:
            if face_id != other_face:
                continue
            if not regions_overlap(region, other_region):
                continue
            shared_components = sorted({i for i, on in enumerate(mask) if on}
                                       & {i for i, on in enumerate(other_mask) if on})
            if min(_time_reaches(record["interval"], record["solver"]),
                   _time_reaches(other["interval"], other["solver"])) >= \
                    max(record["interval"][0], other["interval"][0]):
                return {"face": face_id, "components": shared_components}
    return None


def strong_resolution(record, index, groups):
    """Control-net spec for one strong record, for callers outside validation."""
    spec = strong_spec(record, index, groups)
    return {"locations": spec["locations"], "unknown": spec["unknown"],
            "components": [i for i, value in enumerate(record["value"]) if value is not None],
            "interval": record.get("interval", [0, "End"]), "name": record.get("name", "")}


def load_kind_error(load):
    """Explicit error for an unsupported/unrecognized load kind, else ``None``."""
    kind = load.get("kind", "vector")
    name = load.get("name")
    if kind in UNSUPPORTED_LOAD_KINDS:
        return (f"{name}: {kind!r} pressure (follower/current-normal) is not supported; "
                "use kind='reference_pressure' for dead pressure.")
    if kind not in LOAD_KINDS:
        return (f"{name}: unrecognized load kind {kind!r}; expected 'vector' or "
                "'reference_pressure'.")
    return None


def amplitude_errors(load, solver):
    """Validate the optional amplitude table; returns a list of error strings."""
    amplitude = load.get("amplitude")
    if amplitude is None:
        return []
    name = load.get("name")
    if not isinstance(amplitude, dict) or "points" not in amplitude:
        return [f"{name}: amplitude must be an object with a 'points' list."]
    try:
        rows = ibra_amplitude.parse(amplitude["points"])
    except ibra_amplitude.AmplitudeTableError as error:
        return [f"{name}: amplitude: {error}."]
    interval = load.get("interval")
    if not isinstance(interval, (list, tuple)) or len(interval) != 2:
        return [f"{name}: amplitude requires a valid [start, End] active interval."]
    analysis_start = solver.get("start_time")
    analysis_end = solver.get("end_time")
    record_start = interval[0]
    record_end = analysis_end if interval[1] == "End" else interval[1]
    if not (ibra_amplitude.is_finite_number(analysis_start)
            and ibra_amplitude.is_finite_number(analysis_end)
            and ibra_amplitude.is_finite_number(record_start)
            and ibra_amplitude.is_finite_number(record_end)):
        return [f"{name}: amplitude requires finite analysis and record interval bounds."]
    # The table must lie inside BOTH the analysis interval and the record interval.
    lower = max(analysis_start, record_start)
    upper = min(analysis_end, record_end)
    if lower > upper:
        return [f"{name}: the active interval does not intersect the analysis interval."]
    if rows[0][0] < lower or rows[-1][0] > upper:
        return [f"{name}: the amplitude table domain [{rows[0][0]}, {rows[-1][0]}] must lie "
                f"within the active interval [{lower}, {upper}]; widen the interval or narrow "
                "the table."]
    return []


def validate(project, index):
    errors, warnings = [], []
    def require(test, message):
        if not test:
            errors.append(message)
    require(project.get("schema_version") == SCHEMA, "Unsupported project schema version.")
    require(project.get("source_sha256") == index.get("source_sha256"), "The STEP checksum does not match the geometry cache. Reimport the source geometry.")
    require(project.get("geometry_sha256", index.get("geometry_sha256")) == index.get("geometry_sha256"), "Converted geometry has changed, possibly due to a modified profile. Reassign analysis conditions.")
    require(project.get("units") in UNIT_SYSTEMS, "Select a consistent unit system.")
    refinement = project.get("refinement_enabled", True)  # Legacy projects always applied refinement.
    require(type(refinement) is bool, "Refinement enabled must be a boolean.")
    faces = {f["id"]: f for f in index["faces"]}
    edges = {e["id"]: e for e in index["edges"]}
    materials = {m["id"]: m for m in project["materials"]}
    require(len(materials) == len(project["materials"]), "Material IDs must be unique.")
    for m in materials.values():
        require(all(finite(m.get(k)) and m[k] > 0 for k in ("young", "density")), f"Material {m['id']}: Young's modulus and density must be finite and positive.")
        require(finite(m.get("poisson")) and -1 < m["poisson"] < 0.5, f"Material {m['id']}: Poisson's ratio must lie in (-1, 0.5).")
    sections = {sec["id"]: sec for sec in project.get("sections", [])}
    require(len(sections) == len(project.get("sections", [])), "Section IDs must be unique.")
    for sec in sections.values():
        require(finite(sec.get("thickness")) and sec["thickness"] > 0, f"Section {sec['id']}: thickness must be finite and positive.")
    patch_ids = [p["face_id"] for p in project["patches"]]
    require(len(set(patch_ids)) == len(patch_ids) and set(patch_ids) == set(faces), "Assign exactly one element formulation, material and section to each imported analysis patch.")
    active_faces = active_face_ids(project)
    require(bool(active_faces), "The analysis domain needs at least one active patch; all patches are currently excluded.")
    groups, group_errors, group_warnings = _group_errors(project, index)
    errors.extend(group_errors)
    warnings.extend(group_warnings)
    for p in project["patches"]:
        if p["face_id"] not in faces:
            continue
        require(type(p.get("active", True)) is bool, f"Patch {p['face_id']}: 'active' must be a boolean.")
        require(p["material_id"] in materials, f"Patch {p['face_id']} references an undefined material ID.")
        require(p.get("section_id") in sections, f"Patch {p['face_id']} references an undefined section ID.")
        require(p["element"] in ELEMENTS, "Supported formulations: Kirchhoff–Love shell and membrane.")
        require(p["element"] != "Shell3pElement" or min(faces[p["face_id"]].get("min_continuity", [99,99])) >= 1,
                f"Patch {p['face_id']} has internal continuity C⁰ or lower; the shell requires C¹. Degree elevation does not improve existing continuity. Reparameterize, remove redundant knots where valid, or select a membrane formulation if physically appropriate.")
        for d, original in zip(("u", "v"), faces[p["face_id"]]["degrees"]):
            if not refinement:
                require(original >= ELEMENTS.get(p["element"], 1),
                        f"Patch {p['face_id']}: refinement is disabled and the original degree is below the formulation minimum. Enable Refinement to elevate the degree.")
                continue
            require(type(p["degree_"+d]) is int and max(original, ELEMENTS.get(p["element"], 1)) <= p["degree_"+d] <= 8,
                    f"Patch {p['face_id']}: target degrees must meet the original and formulation-specific minimum degrees and must not exceed 8.")
            require(type(p["insert_"+d]) is int and 0 <= p["insert_"+d] <= 100, "Knot insertions per span must be integers from 0 to 100.")
        require(type(p["quadrature"]) is int and 0 <= p["quadrature"] <= 20, "Quadrature points per span must be 0 (default) or an integer from 1 to 20.")
    s = project["solver"]
    require(s["type"] in ("static", "dynamic"), "Select static or dynamic analysis.")
    require(s["analysis_type"] in ("linear", "non_linear"), "Invalid analysis formulation.")
    require(all(finite(s.get(k)) for k in ("start_time", "end_time", "time_step")) and s["end_time"] > s["start_time"] and 0 < s["time_step"] <= s["end_time"]-s["start_time"], "The time increment must be positive and must not exceed the analysis duration.")
    strong_records, load_targets, loaded_targets, initial_assignments = [], {}, {}, {}
    for category in ("supports", "loads", "initial_conditions"):
        names = set()
        for item in project[category]:
            require(item["name"] not in names, f"Condition names must be unique within {category}.")
            names.add(item["name"])
            if category == "loads":
                kind_error = load_kind_error(item)
                if kind_error:
                    errors.append(kind_error)
                    continue
            if category == "loads" and item.get("kind") == "reference_pressure":
                values = None
            else:
                values = item["value"]
                require(len(values) == 3 and all(v is None or finite(v) for v in values) and any(v is not None for v in values), f"{item['name']}: specify at least one finite component.")
            if "group_id" in item and any(key in item for key in TARGET_KEYS):
                errors.append(f"{item['name']}: this condition stores both a named-group reference and a "
                              "direct target. Keep exactly one target source.")
            resolved = resolve(item, groups, index)
            kind, ids, source = resolved["kind"], resolved["ids"], resolved["source"]
            if resolved["group"] is None and "group_id" in item:
                errors.append(f"{item['name']}: the referenced group {item['group_id']} no longer exists. "
                              "Delete the condition or point it at an existing group.")
                continue
            if kind not in GROUP_KINDS:
                errors.append(f"{item['name']}: the target is undefined or is not a one-sided boundary.")
                continue
            if not ids or any(not isinstance(i, int) or isinstance(i, bool) for i in ids):
                errors.append(f"{item['name']}: the target is undefined or is not a one-sided boundary.")
                continue
            if resolved["missing"]:
                if resolved["group"] is not None:
                    errors.append(f"{item['name']}: group {item['group_id']} ({resolved['group']['name']}) has "
                                  f"member ID(s) {resolved['missing']} that are not in the current geometry cache. "
                                  "Reimport the source STEP or repair the group.")
                else:
                    errors.append(f"{item['name']}: target {kind} {resolved['missing'][0]} is not in the "
                                  "current geometry cache.")
                continue
            wrong = []
            for ident in ids:
                edge = edges.get(ident) if kind == "edge" else None
                if kind == "face" and ident in faces:
                    continue
                if edge is not None and edge.get("kind") == "edge":
                    continue
                wrong.append(ident)
            if wrong:
                errors.append(f"{item['name']}: target {kind} {wrong[0]} is not a one-sided boundary. "
                              "Define two-sided interfaces in the Interfaces page.")
                continue
            inactive = [ident for ident in ids
                        if not ((ident in active_faces) if kind == "face"
                                else edge_is_active(edges[ident], active_faces))]
            if inactive:
                errors.append(f"{item['name']}: target {kind} {inactive[0]} is outside the active analysis "
                              "domain. Activate the patch or remove the condition.")
            if category != "initial_conditions":
                a, b = item["interval"]
                require(finite(a) and (b == "End" or finite(b) and b >= a), f"{item['name']}: invalid active time interval.")
                require(finite(a) and a >= s["start_time"] and a <= s["end_time"] and (b == "End" or finite(b) and b <= s["end_time"]), f"{item['name']}: the active interval must lie within the analysis interval.")
            if category == "supports":
                require(item["method"] in ("strong", "penalty"), "Invalid essential-boundary-condition enforcement method.")
                if item["method"] == "penalty":
                    require(kind == "edge", "Penalty enforcement requires a one-sided boundary curve.")
                    require(all(v is not None for v in values), "Penalty supports constrain all XYZ components simultaneously. Use strong enforcement for individual components.")
                    require(finite(item["penalty"]) and item["penalty"] > 0, "The penalty factor must be positive.")
                    require(item["interval"] == [s["start_time"], "End"], "Penalty supports currently require the entire analysis interval.")
                    require(not item.get("clamp"), "Penalty supports do not provide adjacent-row clamping.")
                else:
                    locations = target_locations(item, index, groups)
                    require(locations is not None, f"{item['name']}: strong enforcement requires a complete natural parametric boundary or corner on every target. Use penalty enforcement on general trimming curves.")
                    if locations is None:
                        continue
                    if item.get("clamp"):
                        corner = any(row_locator(local)[0] == "corner"
                                     for _face, local in locations)
                        if corner:
                            errors.append(f"{item['name']}: adjacent-row clamping is not defined at a "
                                          "natural corner; a corner is a single control point.")
                            continue
                    spec = strong_spec(item, index, groups, project)
                    if spec["reason"] == "grid":
                        errors.append(f"{item['name']}: the geometry cache has no control-net counts/spans for "
                                      "its target. Reimport the source STEP to rebuild the cache before "
                                      "validating strong enforcement.")
                        continue
                    if spec["reason"] == "location" or not spec["locations"]:
                        errors.append(f"{item['name']}: strong enforcement needs a natural parametric boundary "
                                      "or corner on every target; at least one target has none. Use penalty "
                                      "enforcement on general trimming curves.")
                        continue
                    strong_records.append({"name": item["name"], "locations": spec["locations"],
                                           "components": [i for i, v in enumerate(values) if v is not None],
                                           "interval": item["interval"], "solver": s})
            elif category == "loads":
                if item.get("kind") == "reference_pressure":
                    if "variable" in item or "value" in item:
                        errors.append(f"{item['name']}: reference_pressure must not carry "
                                      "'variable' or 'value'; it is fully described by 'pressure'.")
                    require(kind == "face", f"{item['name']}: reference pressure requires a face "
                                            "or face group target.")
                    require(finite(item.get("pressure")), f"{item['name']}: pressure must be a "
                                                           "finite signed number.")
                    if "normal_flip" in item:
                        require(type(item["normal_flip"]) is bool,
                                f"{item['name']}: normal_flip must be a boolean.")
                    errors.extend(amplitude_errors(item, s))
                    for ident in ids:
                        key = (kind, ident, "reference_pressure")
                        previous = load_targets.get(key)
                        if previous is not None:
                            warnings.append(
                                f"{previous[0]} and {item['name']} both apply reference pressure to "
                                f"{kind} {ident}. Multiple pressure records are summed, not replaced; "
                                "both act once.")
                        load_targets[key] = (item["name"], "reference_pressure")
                        other = loaded_targets.get((kind, ident))
                        if other is not None and other[1] != "reference_pressure":
                            warnings.append(
                                f"Loads {other[1]} ({other[0]}) and reference_pressure "
                                f"({item['name']}) both target {kind} {ident}. Both are added by the "
                                "solver; confirm the combined magnitude.")
                        loaded_targets[(kind, ident)] = (item["name"], "reference_pressure")
                    continue
                require(item["variable"] in ("LINE_LOAD", "SURFACE_LOAD", "DEAD_LOAD"), "Invalid load variable.")
                require(item["variable"] != "LINE_LOAD" or kind == "edge", "LINE_LOAD requires a boundary curve.")
                require(item["variable"] != "SURFACE_LOAD" or kind == "face", "SURFACE_LOAD requires a surface patch.")
                require(all(v is not None for v in values), "Specify all XYZ load components; zero values are permitted.")
                errors.extend(amplitude_errors(item, s))
                for ident in ids:
                    key = (kind, ident, item["variable"])
                    previous = load_targets.get(key)
                    if previous is not None:
                        warnings.append(
                            f"{previous[0]} and {item['name']} both load {kind} {ident} with "
                            f"{item['variable']}. Multiple load records are summed, not replaced; both act once.")
                    load_targets[key] = (item["name"], item["variable"])
                    other = loaded_targets.get((kind, ident))
                    if other is not None and other[1] != item["variable"]:
                        warnings.append(
                            f"Loads {other[1]} ({other[0]}) and {item['variable']} ({item['name']}) both target "
                            f"{kind} {ident}. Different load kinds are added by the solver; confirm the combined "
                            "magnitude.")
                    loaded_targets[(kind, ident)] = (item["name"], item["variable"])
            else:
                require(kind == "face", "Initial fields are currently assigned to all control points of a patch.")
                require(item["variable"] in ("DISPLACEMENT", "VELOCITY", "ACCELERATION"), "Invalid initial-field variable.")
                require(s["type"] == "dynamic" or item["variable"] == "DISPLACEMENT", "Static analysis does not support initial velocity or acceleration.")
                require(s["analysis_type"] == "non_linear" or item["variable"] != "DISPLACEMENT" or all(v in (None, 0) for v in values), "Nonzero initial displacement requires nonlinear analysis.")
                for ident in ids:
                    key = (ident, item["variable"])
                    mask = frozenset(i for i, v in enumerate(values) if v is not None)
                    other = initial_assignments.get(key)
                    if other is not None:
                        shared = sorted(mask & other[1])
                        if shared:
                            names = ", ".join("XYZ"[c] for c in shared)
                            errors.append(f"{item['name']} and {other[0]} both set {item['variable']} "
                                          f"component(s) {names} on patch {ident}. The later value would "
                                          "overwrite the earlier one; keep one assignment per component.")
                        else:
                            warnings.append(f"{item['name']} and {other[0]} set complementary components of "
                                            f"{item['variable']} on patch {ident}; both are applied once.")
                        initial_assignments[key] = (other[0], other[1] | mask)
                    else:
                        initial_assignments[key] = (item["name"], mask)
    if len(active_faces) < len(faces):
        excluded = sorted(set(faces) - active_faces)
        warnings.append(f"{len(excluded)} patch(es) are outside the analysis domain and are exported as "
                        f"reference geometry only: {excluded}.")
    for i, record in enumerate(strong_records):
        for other in strong_records[:i]:
            conflict = strong_overlap(record, other)
            if conflict is not None:
                components = ", ".join("XYZ"[c] for c in conflict["components"])
                errors.append(f"{record['name']} and {other['name']} prescribe the same component "
                              f"({components}) on the same control point of patch {conflict['face']} "
                              "over an overlapping active interval. Edit the existing essential boundary "
                              "condition.")
                break
    if project["initial_conditions"]:
        per_face = {}
        for (face_id, variable), (_name, mask) in sorted(initial_assignments.items()):
            per_face.setdefault(face_id, set()).add(variable)
        shared = {face: variables for face, variables in per_face.items() if len(variables) > 1}
        if shared:
            warnings.append("Initial fields on one patch share the same control points: "
                            + "; ".join(f"patch {face}: {', '.join(sorted(v))}" for face, v in sorted(shared.items()))
                            + ". Adjacent patches may share boundary control points; confirm the intended values.")
    seen_interfaces = set()
    for c in project["couplings"]:
        if not isinstance(c, dict) or "edge_id" not in c:
            errors.append("Every interface needs an edge id, a state and an origin.")
            continue
        edge_id = c["edge_id"]
        if edge_id in seen_interfaces:
            errors.append(f"Duplicate interface ID {edge_id}: each interface may appear at most once.")
        seen_interfaces.add(edge_id)
        if "enabled" in c:
            errors.append(f"Interface {edge_id} still carries the legacy 'enabled' flag alongside 'state'. "
                          "Reopen or re-save the project so it has a single source of truth.")
        if c.get("state") not in COUPLING_STATES:
            errors.append(f"Interface {edge_id}: unknown state {c.get('state')!r}; "
                          f"expected one of {', '.join(COUPLING_STATES)}.")
            continue
        if c.get("origin") not in COUPLING_ORIGINS:
            errors.append(f"Interface {edge_id}: unknown origin {c.get('origin')!r}; "
                          f"expected one of {', '.join(COUPLING_ORIGINS)}.")
        edge = edges.get(edge_id)
        if edge is None or edge.get("kind") != "coupling":
            errors.append(f"Interface {edge_id}: the stored interface is missing or is not a two-sided "
                          "coupling in the current geometry cache. Reimport the source STEP.")
            continue
        require(finite(c.get("penalty")) and c["penalty"] > 0,
                f"Interface {edge_id}: the interface penalty factor must be finite and positive.")
        if c["state"] == "confirmed":
            if not coupling_is_active(edge, active_faces):
                inactive = [fid for fid in edge.get("face_ids", []) if fid not in active_faces]
                errors.append(f"Interface {edge_id} is confirmed but its face(s) {inactive} are outside the "
                              "active analysis domain. Activate both patches or exclude the interface.")
            if c.get("origin") in UNREVIEWED_ORIGINS:
                warnings.append(
                    f"Interface {edge_id} was enabled automatically by an older version and has not been "
                    "reviewed. Confirm its connection type or exclude it before relying on it.")
            errors.extend(evidence_errors(edge_id, edge.get("diagnostics"),
                                          index.get("geometry_sha256"), edge))
            if not c.get("rotation"):
                warnings.append(f"Interface {edge_id} enforces displacement continuity only. Enable rotational "
                                "coupling where shell bending continuity is required.")
        if c.get("rotation") and c["state"] != "excluded" and edge_id in edges:
            patch_map = {p["face_id"]: p for p in project["patches"]}
            require(all(patch_map.get(fid, {}).get("element") == "Shell3pElement" for fid in edges[edge_id]["face_ids"]),
                    "Rotational coupling requires the shell formulation on both sides of an interface.")
    # P0-3 fail-closed gate: a binding that could not be proven unchanged must block
    # the export until the user explicitly rebinds or drops it. Only P0-3 change here.
    for category in ("supports", "loads", "initial_conditions"):
        for item in project[category]:
            reason = item.get("needs_rebind")
            if reason:
                errors.append(f"{item['name']}: this binding is pending rebind ({reason}); "
                              "resolve it in Rebind before exporting.")
    for group in project.get("groups", []):
        if isinstance(group, dict) and group.get("needs_rebind"):
            errors.append(f"Group {group.get('id')} ({group.get('name')}): pending rebind "
                          f"({group['needs_rebind']}); resolve it before exporting.")
    if project.get("geometry_needs_rebind"):
        errors.append(f"The geometry changed and no binding was reviewed: {project['geometry_needs_rebind']}. "
                      "Restore or rebind explicitly before exporting; the previous cache must not be re-exported.")
    require(type(project["output"]["refinement"]) is int and 1 <= project["output"]["refinement"] <= 100, "Visualization subdivision must be an integer from 1 to 100.")
    if not project["supports"]:
        warnings.append("No essential boundary conditions are defined; the static system will generally contain rigid-body modes.")
    if not project["loads"] and not project["initial_conditions"]:
        warnings.append("No loads or initial fields are defined.")
    warnings.append("mm-N-s preserves STEP coordinates in millimetres; m-N-s scales geometry by 0.001 at export. Material, section thickness and load values must be dimensionally consistent with the selected unit system.")
    return errors, list(dict.fromkeys(warnings))


def unit_scale(source_units, target_units):
    """Return the multiplicative factor for each dimensional kind between unit systems.

    Kinds are ``length``, ``line_load``, ``stress`` and ``density``. The mapping is the
    single source of truth used by ``convert_project_units`` and by GUI defaults, so new
    material/section rows are created in the project's current unit system.
    """
    if source_units not in UNIT_SYSTEMS or target_units not in UNIT_SYSTEMS:
        raise ValueError("Select a consistent unit system.")
    if source_units == target_units:
        return {kind: 1.0 for kind in _UNIT_EXPONENT}
    ratio = _UNIT_RATIO[(source_units, target_units)]
    return {kind: ratio ** exponent for kind, exponent in _UNIT_EXPONENT.items()}


def convert_project_units(project, target_units, index=None):
    """Return a copy of ``project`` with every physical value converted to ``target_units``.

    The input is first migrated to the current schema, so a schema-1 project converts its
    legacy per-material thickness as well. Any unsupported schema, non-finite value or
    unsupported variable/target kind raises before a converted project is returned; the
    input project is never modified.

    A rotational coupling is rejected. The Kratos 10.4.3 ``CouplingPenaltyCondition``
    consumes a single ``PENALTY_FACTOR`` for both displacement and rotational continuity,
    while the two roles carry different dimensions, so no single conversion factor is
    valid. (``PENALTY_ROTATION_FACTOR`` is registered in the application variables, but
    the 10.4.3 condition does not consume it.)
    """
    if target_units not in UNIT_SYSTEMS:
        raise ValueError("Select a consistent unit system.")
    project = migrate_project(project)
    source_units = project.get("units")
    if source_units not in UNIT_SYSTEMS:
        raise ValueError("Select a consistent unit system.")
    if source_units == target_units:
        return project
    scale = unit_scale(source_units, target_units)
    index = index if index is not None else {"faces": [], "edges": []}
    groups = expand(project.get("groups", []), index)

    def require_finite(value, label):
        if not finite(value):
            raise ValueError(f"{label} must be finite before unit conversion.")

    def target_kind(record, label):
        """Resolved target kind, using the same expansion as validation and export."""
        if "group_id" in record:
            group = groups.get(record["group_id"])
            if group is None:
                raise ValueError(f"{label} references group {record['group_id']}, which does not exist; "
                                 "repair the group before converting units.")
            if group["missing"]:
                raise ValueError(f"{label} references group {record['group_id']} with member ID(s) "
                                 f"{group['missing']} that are not in the current geometry cache.")
            return group["kind"]
        kind = record.get("target_kind")
        if kind not in GROUP_KINDS:
            raise ValueError(f"{label} has no resolvable face or edge target.")
        return kind

    def scaled(value, kind, label):
        if value is None:
            return None
        if not finite(value):
            raise ValueError(f"Only finite physical values can be converted: {label}.")
        result = value * scale[kind]
        if not math.isfinite(result):
            raise ValueError(f"Unit conversion overflowed a finite value: {label}.")
        return result

    def scaled_vector(values, kind, label):
        return [scaled(value, kind, label) for value in values]

    # Validate every value and target kind before producing the converted copy, so a
    # rejection leaves both the input and any previously converted project untouched.
    for material in project.get("materials", []):
        require_finite(material.get("young"), f"Material {material.get('id')} Young's modulus")
        require_finite(material.get("density"), f"Material {material.get('id')} density")
    for section in project.get("sections", []):
        require_finite(section.get("thickness"), f"Section {section.get('id')} thickness")
    for support in project.get("supports", []):
        if support.get("method") not in ("strong", "penalty"):
            raise ValueError("Unsupported essential-boundary enforcement method for unit conversion.")
        if "penalty" in support:
            require_finite(support["penalty"], "Support penalty factor")
    for load in project.get("loads", []):
        kind_error = load_kind_error(load)
        if kind_error:
            raise ValueError(kind_error)
        # A legacy conversion payload need not carry a solver when no amplitude is
        # used; only touch it when an actual amplitude must be validated (a malformed
        # amplitude without a solver still fails with a clear message).
        if load.get("amplitude") is not None:
            amplitude_problems = amplitude_errors(load, project.get("solver", {}))
            if amplitude_problems:
                raise ValueError(" ".join(amplitude_problems))
        if load.get("kind") == "reference_pressure":
            if "variable" in load or "value" in load:
                raise ValueError(f"{load.get('name')}: reference_pressure must not carry "
                                 "'variable' or 'value'.")
            if target_kind(load, f"Load {load.get('name')!r}") != "face":
                raise ValueError("reference_pressure unit conversion requires a face or face "
                                 "group target.")
            require_finite(load.get("pressure"), "reference pressure")
            if "normal_flip" in load and type(load["normal_flip"]) is not bool:
                raise ValueError("normal_flip must be a boolean.")
            continue
        variable = load.get("variable")
        if variable not in ("LINE_LOAD", "SURFACE_LOAD", "DEAD_LOAD"):
            raise ValueError(f"Unsupported load variable for unit conversion: {variable!r}")
        kind = target_kind(load, f"Load {load.get('name')!r}")
        if variable == "LINE_LOAD" and kind != "edge":
            raise ValueError("LINE_LOAD unit conversion requires an edge target.")
        if variable == "SURFACE_LOAD" and kind != "face":
            raise ValueError("SURFACE_LOAD unit conversion requires a face target.")
    for initial in project.get("initial_conditions", []):
        if initial.get("variable") not in ("DISPLACEMENT", "VELOCITY", "ACCELERATION"):
            raise ValueError(f"Unsupported initial-field variable for unit conversion: {initial.get('variable')!r}")
        if target_kind(initial, f"Initial field {initial.get('name')!r}") != "face":
            raise ValueError("Initial-field unit conversion requires a face target.")
    for coupling in project.get("couplings", []):
        if "penalty" in coupling:
            require_finite(coupling["penalty"], "Interface penalty factor")
        if coupling.get("rotation"):
            raise ValueError(
                "Cannot convert units while a rotational coupling exists. The Kratos 10.4.3 "
                "CouplingPenaltyCondition uses one shared PENALTY_FACTOR for displacement and "
                "rotational continuity, but a displacement penalty is a surface stiffness "
                "[F/L^2] while the rotational term requires a force [F]; no single factor is "
                "valid. Disable rotational coupling (or convert with a backend whose condition "
                "truly consumes a separate rotational penalty property) before switching unit "
                "systems.")

    converted = deepcopy(project)
    for material in converted.get("materials", []):
        material["young"] = scaled(material["young"], "stress", "Young's modulus")
        material["density"] = scaled(material["density"], "density", "density")
    for section in converted.get("sections", []):
        section["thickness"] = scaled(section["thickness"], "length", "section thickness")
    for support in converted.get("supports", []):
        support["value"] = scaled_vector(support["value"], "length", "support displacement")
        # The stored factor keeps its meaning if the enforcement method is switched later.
        if "penalty" in support:
            support["penalty"] = scaled(support["penalty"], "stress", "support penalty factor")
    for load in converted.get("loads", []):
        if load.get("kind") == "reference_pressure":
            # Pressure is a stress (F/L^2); the amplitude table times/factors and the
            # interval are dimensionless/time and stay untouched.
            load["pressure"] = scaled(load["pressure"], "stress", "reference pressure")
            continue
        kind = target_kind(load, f"Load {load.get('name')!r}")
        edge_measure = load["variable"] == "LINE_LOAD" or (
            load["variable"] == "DEAD_LOAD" and kind == "edge")
        load["value"] = scaled_vector(load["value"],
                                      "line_load" if edge_measure else "stress", "load")
    for initial in converted.get("initial_conditions", []):
        initial["value"] = scaled_vector(initial["value"], "length", "initial field")
    for coupling in converted.get("couplings", []):
        if "penalty" in coupling:
            coupling["penalty"] = scaled(coupling["penalty"], "stress", "interface penalty factor")
    converted["units"] = target_units
    return converted


def strong_location(item, index, groups=None):
    """First natural ``(face_id, local_parameters)`` of a strong record, or ``None``.

    This single-location view exists for compatibility; export and validation use
    ``groups.target_locations`` so **every** group member is expanded, never just the
    first one.
    """
    locations = target_locations(item, index, groups or {})
    return locations[0] if locations else None
