"""P0-3 explicit binding remap for a controlled geometry snapshot (stdlib only).

A controlled STEP snapshot is a *different geometry cache*. Nothing in this module
tries to prove that an old entity survived: equal ids, equal bounding boxes, equal
degree or equal area prove nothing about identity. Every old binding stays pending
until the caller supplies an explicit mapping, and every new face receives exactly
one patch.

Two stages, both non-mutating:

``stage_rebind(oldproject, oldindex)``
    Return a serialization-safe **plan**. It enumerates every old patch face, every
    named-group member and every direct condition target (supports / loads / initial
    fields) as a required key ``"face:<id>"`` or ``"edge:<id>"``, together with the
    material/section/active/refinement choice that must be preserved until the face
    is explicitly mapped. It never proposes a mapping of its own.

``apply_rebind(oldproject, oldindex, newindex, mapping, drop_targets=None, source_step=None)``
    Apply one complete mapping transactionally. ``mapping`` maps required keys to
    ``"face:<new_id>"`` / ``"edge:<new_id>"`` of the same kind; ``drop_targets`` names
    required keys the caller deliberately drops. On any incomplete, dangling,
    wrong-kind, non-boundary or ambiguous input it raises :class:`RebindError` and
    leaves both inputs untouched. On success it returns
    ``{"project": <fully mapped project>, "summary": <human-readable report>}`` with
    ``source_step`` / ``source_sha256`` / ``geometry_sha256`` updated and every
    ``needs_rebind`` marker cleared.

    Practical argument note: the positional order is
    ``(oldproject, oldindex, newindex, mapping, drop_targets, source_step)``;
    ``drop_targets`` may be any iterable of keys (or ``None``) and ``source_step`` may
    be ``None`` to keep the old path. Return value is a dict, not a bare project, so
    the GUI can show the summary and the export gate can take ``result["project"]``.

``mark_geometry_changed(project, reason)``
    Pure helper for the fail-closed gate when the saved geometry cannot be trusted
    (missing source STEP, unexplained snapshot): returns a copy with
    ``geometry_needs_rebind`` set and every condition and group marked
    ``needs_rebind``. ``project.validate`` already reports these as export errors.

State contract for interfaces (P0-2): a new cache regenerates its couplings as
``state="pending", origin="geometry"`` candidates. A previously confirmed interface is
never inherited mechanically, not even when the same edge id exists again.

This module intentionally does not validate strong-support ``side`` geometry and does
not export; ``project.validate`` remains the export gate.
"""
from __future__ import annotations

import copy

KINDS = ("face", "edge")
CONDITION_CATEGORIES = ("supports", "loads", "initial_conditions")
DEFAULT_ELEMENT = "Shell3pElement"
ELEMENT_MIN_DEGREE = {"Shell3pElement": 2, "IgaMembraneElement": 1}
IDENTITY_POLICY = ("explicit mapping only: equal ids, bounding boxes, degrees or areas "
                   "are not identity evidence")


class RebindError(ValueError):
    """A remap is incomplete, dangling, wrong-kind, non-boundary, or ambiguous."""


# --------------------------------------------------------------------------- #
# key helpers
# --------------------------------------------------------------------------- #

def target_key(kind, ident):
    """``("face", 2) -> "face:2"``. Raises for an unsupported kind or id."""
    if kind not in KINDS:
        raise RebindError(f"Unsupported target kind {kind!r}; expected one of {KINDS}.")
    if not isinstance(ident, int) or isinstance(ident, bool):
        raise RebindError(f"Target id for {kind!r} must be an integer, got {ident!r}.")
    return f"{kind}:{ident}"


def parse_key(key):
    """``"face:2" -> ("face", 2)``. Raises for anything else.

    The key must equal its canonical form exactly: whitespace aliases such as
    ``"face: 2"``, ``"face:2 "`` or ``"face:02"`` are rejected here rather than parsed
    into an id that later string comparisons cannot match.
    """
    if not isinstance(key, str) or key.count(":") != 1:
        raise RebindError(f"Mapping key {key!r} must look like 'face:2' or 'edge:3'.")
    kind, _, raw = key.partition(":")
    if kind not in KINDS:
        raise RebindError(f"Mapping key {key!r} has an unsupported kind {kind!r}.")
    try:
        ident = int(raw)
    except ValueError as error:
        raise RebindError(f"Mapping key {key!r} has a non-integer id {raw!r}.") from error
    if target_key(kind, ident) != key:
        raise RebindError(
            f"Mapping key {key!r} is not canonical; write it as "
            f"'{kind}:{ident}' without spaces or leading zeros.")
    return kind, ident


def _migrate(project):
    """Bring the current schema without changing any physical value."""
    if not isinstance(project, dict):
        raise RebindError("The old project must be a mapping.")
    try:
        from .project import migrate_project
    except ImportError as error:  # pragma: no cover - package layout only
        raise RebindError("kratos_iga.project is not importable next to bindings.py.") from error
    try:
        return migrate_project(project)
    except ValueError as error:
        raise RebindError(f"The old project cannot be migrated: {error}") from error


def _model_part_name(project):
    return project.get("solver", {}).get("model_part_name", "IgaModelPart")


def _records(project, field, label=None):
    """Return a strict list of object records; never silently skip malformed items."""
    return _list_field(project, field, label or f"project.{field}")


def _list_field(container, field, label):
    value = container.get(field, [])
    if not isinstance(value, list):
        raise RebindError(f"{label} must be a list, got {type(value).__name__}; repair the "
                          "project before remapping.")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise RebindError(f"{label}[{index}] is {type(item).__name__}, not an object; repair "
                              "the project before remapping.")
    return value


def _ident(record, field, label):
    ident = record.get(field)
    if not isinstance(ident, int) or isinstance(ident, bool):
        raise RebindError(f"{label} has invalid {field} {ident!r}; every id must be a non-boolean "
                          "integer. Repair the project before remapping.")
    return ident


def _face_records(index, label):
    faces = {}
    for position, face in enumerate(_list_field(index, "faces", f"{label}.faces")):
        ident = _ident(face, "id", f"{label}.faces[{position}]")
        if ident in faces:
            raise RebindError(f"{label}.faces has duplicate face id {ident}; rebuild the geometry "
                              "cache instead of remapping an ambiguous one.")
        faces[ident] = face
    return faces


def _face_ids(index, label="the index"):
    return set(_face_records(index, label))


def _edge_map(index, label="the index"):
    edges = {}
    for position, edge in enumerate(_list_field(index, "edges", f"{label}.edges")):
        ident = _ident(edge, "id", f"{label}.edges[{position}]")
        if ident in edges:
            raise RebindError(f"{label}.edges has duplicate edge id {ident}; rebuild the geometry "
                              "cache instead of remapping an ambiguous one.")
        edges[ident] = edge
    return edges


def _patch_map(project):
    """Every patch by face id; duplicate or malformed patch assignments fail explicitly."""
    patches = {}
    for position, patch in enumerate(_records(project, "patches")):
        ident = _ident(patch, "face_id", f"project.patches[{position}]")
        if ident in patches:
            raise RebindError(f"project.patches has duplicate face_id {ident}; keep exactly one "
                              "patch per face before remapping.")
        patches[ident] = patch
    return patches


def _groups(project):
    """Every group by id; duplicate ids, malformed groups and empty groups fail explicitly."""
    groups = {}
    for position, group in enumerate(_records(project, "groups")):
        ident = _ident(group, "id", f"project.groups[{position}]")
        if ident in groups:
            raise RebindError(f"project.groups has duplicate group id {ident}; keep one definition "
                              "before remapping.")
        kind = group.get("kind")
        if kind not in KINDS:
            raise RebindError(f"Group {ident} has kind {kind!r}; expected one of {KINDS}.")
        members = group.get("members")
        if not isinstance(members, list):
            raise RebindError(f"Group {ident} members must be a list, got "
                              f"{type(members).__name__}; repair the group before remapping.")
        for member in members:
            if not isinstance(member, int) or isinstance(member, bool):
                raise RebindError(f"Group {ident} has a non-integer member {member!r}; repair the "
                                  "group before remapping.")
        groups[ident] = group
    return groups


def _reference_label(group_id):
    return f"group:{group_id}"


def _index_hashes(index, label):
    source = index.get("source_sha256")
    geometry = index.get("geometry_sha256")
    if not source or not geometry:
        raise RebindError(f"{label} has no source_sha256/geometry_sha256; "
                          "rebuild the geometry cache before remapping.")
    return source, geometry


# --------------------------------------------------------------------------- #
# stage
# --------------------------------------------------------------------------- #

def stage_rebind(oldproject, oldindex):
    """Return the pending remap plan for ``oldproject`` against ``oldindex``.

    Every required key is a binding the caller must map or explicitly drop. The plan
    carries the preserved patch choices so the GUI can show what is at stake.
    """
    project = _migrate(oldproject)
    if not isinstance(oldindex, dict):
        raise RebindError("The old index must be a mapping.")
    _index_hashes(oldindex, "old index")
    old_faces = _face_ids(oldindex)
    old_edges = _edge_map(oldindex)

    targets = {}

    def require(kind, ident, reference):
        key = target_key(kind, ident)
        entry = targets.setdefault(key, {"key": key, "kind": kind, "old_id": ident,
                                         "referenced_by": [], "in_old_index": False})
        if reference not in entry["referenced_by"]:
            entry["referenced_by"].append(reference)
        entry["in_old_index"] = ident in (old_faces if kind == "face" else set(old_edges))
        return entry

    # 1) every old patch face, with the exact choices that must survive the remap
    patches = []
    for face_id, patch in _patch_map(project).items():
        require("face", face_id, "patch")
        preserved = {key: copy.deepcopy(value) for key, value in patch.items()
                     if key not in ("face_id", "needs_rebind")}
        patches.append({"face_id": face_id, "preserved": preserved,
                        "in_old_index": face_id in old_faces,
                        "pending_reason": patch.get("needs_rebind")})

    # 2) named-group members, by kind
    groups_map = _groups(project)
    groups = []
    for group_id, group in groups_map.items():
        kind = group["kind"]
        members, missing, wrong_kind = [], [], []
        for member in group["members"]:
            key = target_key(kind, member)
            require(kind, member, _reference_label(group_id))
            members.append(key)
            if kind == "face":
                if member not in old_faces:
                    missing.append(member)
            else:
                edge = old_edges.get(member)
                if edge is None:
                    missing.append(member)
                elif edge.get("kind") != "edge":
                    wrong_kind.append(member)
        if wrong_kind:
            raise RebindError(f"Group {group_id} is an edge group but member(s) {wrong_kind} "
                              "are not one-sided boundaries in the old cache.")
        if not members:
            raise RebindError(
                f"Group {group_id} ({group.get('name', '')}) has no members. Add members or delete "
                "the group definition before remapping; an empty group is never dropped silently.")
        groups.append({"id": group_id, "name": group.get("name", ""), "kind": kind,
                       "members": sorted(set(members)), "missing": sorted(missing),
                       "needs_rebind": group.get("needs_rebind")})

    # 3) direct condition targets (group references are handled through the group)
    conditions = []
    for category in CONDITION_CATEGORIES:
        for item in _records(project, category):
            record = {"category": category, "name": item.get("name", ""),
                      "needs_rebind": item.get("needs_rebind")}
            if "group_id" in item:
                group_id = item["group_id"]
                if group_id not in groups_map:
                    raise RebindError(f"{category} {record['name']!r} references unknown group "
                                      f"{group_id!r}.")
                record.update({"reference": "group", "group_id": group_id})
            else:
                kind, ident = item.get("target_kind"), item.get("target_id")
                if kind not in KINDS:
                    raise RebindError(f"{category} {record['name']!r} has target_kind "
                                      f"{kind!r}; expected face or edge.")
                if not isinstance(ident, int) or isinstance(ident, bool):
                    raise RebindError(f"{category} {record['name']!r} has a non-integer "
                                      f"target_id {ident!r}.")
                entry = require(kind, ident, f"{category}/{record['name']}")
                if not entry["in_old_index"] and not item.get("needs_rebind"):
                    raise RebindError(
                        f"{category} {record['name']!r} targets {kind} {ident}, which is absent "
                        "from the old cache and is not marked pending; resolve or drop it first.")
                record.update({"reference": "direct", "key": target_key(kind, ident)})
            conditions.append(record)

    # 4) interface decisions: reported, never inherited
    couplings = []
    for coupling in project.get("couplings", []):
        if not isinstance(coupling, dict) or not isinstance(coupling.get("edge_id"), int):
            raise RebindError("Every coupling record needs an integer edge_id.")
        couplings.append({"edge_id": int(coupling["edge_id"]),
                          "state": coupling.get("state"), "origin": coupling.get("origin"),
                          "decision": "regenerate_pending"})

    required = sorted(targets)
    plan = {
        "stage": "p0-3-rebind",
        "identity_policy": IDENTITY_POLICY,
        "model_part_name": _model_part_name(project),
        "old": {"source_step": project.get("source_step", ""),
                "source_sha256": oldindex.get("source_sha256"),
                "geometry_sha256": oldindex.get("geometry_sha256")},
        "required_keys": required,
        "targets": [targets[key] for key in required],
        "patches": patches,
        "groups": groups,
        "conditions": conditions,
        "couplings": couplings,
        "old_faces": sorted(old_faces),
        "old_boundary_edges": sorted(edge_id for edge_id, edge in old_edges.items()
                                     if edge.get("kind") == "edge"),
        "old_coupling_edges": sorted(edge_id for edge_id, edge in old_edges.items()
                                     if edge.get("kind") == "coupling"),
        "hint": ("Map every required key to the new face/edge of the same kind, or drop it "
                 "explicitly. Same ids are not accepted automatically."),
    }
    return plan


# --------------------------------------------------------------------------- #
# apply
# --------------------------------------------------------------------------- #

def _normalize_mapping(mapping):
    if not isinstance(mapping, dict):
        raise RebindError("mapping must be a dict of 'face:old' -> 'face:new'.")
    applied = {}
    new_face_sources = {}
    for key, value in mapping.items():
        kind, old_id = parse_key(key)
        if not isinstance(value, str):
            raise RebindError(f"mapping[{key!r}] must be a string like 'face:5'.")
        value_kind, new_id = parse_key(value)
        if value_kind != kind:
            raise RebindError(f"mapping[{key!r}] = {value!r} changes kind {kind} to {value_kind}; "
                              "a face maps to a face and an edge to an edge.")
        if kind == "face":
            if new_id in new_face_sources and new_face_sources[new_id] != old_id:
                raise RebindError(
                    f"Ambiguous mapping: old faces {new_face_sources[new_id]} and {old_id} both "
                    f"map to new face {new_id}. One new face cannot carry two old patches.")
            new_face_sources[new_id] = old_id
        applied[key] = value
    return applied


def _normalize_drops(drop_targets, mapping):
    drops = set()
    for key in (drop_targets or ()):
        parse_key(key)
        if key in mapping:
            raise RebindError(f"{key} is both mapped and dropped; choose one.")
        drops.add(key)
    return drops


def _default_patch(project, face):
    materials = [m["id"] for m in project.get("materials", [])
                 if isinstance(m, dict) and isinstance(m.get("id"), int)]
    sections = [s["id"] for s in project.get("sections", [])
                if isinstance(s, dict) and isinstance(s.get("id"), int)]
    degrees = face.get("degrees") if isinstance(face, dict) else None
    degrees = degrees if isinstance(degrees, (list, tuple)) and len(degrees) == 2 else (1, 1)
    minimum = ELEMENT_MIN_DEGREE[DEFAULT_ELEMENT]
    return {"face_id": face["id"], "material_id": materials[0] if materials else 1,
            "section_id": sections[0] if sections else 1, "element": DEFAULT_ELEMENT,
            "active": True, "degree_u": max(minimum, int(degrees[0])),
            "degree_v": max(minimum, int(degrees[1])), "insert_u": 1, "insert_v": 1,
            "quadrature": 0}


def apply_rebind(oldproject, oldindex, newindex, mapping, drop_targets=None, source_step=None):
    """Map ``oldproject`` onto ``newindex`` with an explicit, complete mapping.

    Raises :class:`RebindError` on any problem; both inputs are left unchanged.
    """
    old = _migrate(oldproject)
    plan = stage_rebind(old, oldindex)
    if not isinstance(newindex, dict):
        raise RebindError("The new index must be a mapping.")
    new_source, new_geometry = _index_hashes(newindex, "new index")
    new_faces = _face_records(newindex, "new index")
    new_edges = _edge_map(newindex, "new index")
    if not new_faces:
        raise RebindError("The new index has no faces; cannot assign a patch to 'every new face'.")

    applied = _normalize_mapping(mapping)
    drops = _normalize_drops(drop_targets, applied)
    required = set(plan["required_keys"])
    unknown = sorted((set(applied) | drops) - required)
    if unknown:
        raise RebindError(f"Unknown remap key(s) {unknown}; the plan lists the required keys.")
    missing = sorted(required - set(applied) - drops)
    if missing:
        raise RebindError(
            f"Incomplete remap: {len(missing)} required binding(s) neither mapped nor dropped: "
            f"{missing}. Nothing was changed.")

    # new-side validity: same kind, existing target, one-sided boundary for edges
    for key, value in applied.items():
        kind, _old_id = parse_key(key)
        value_kind, new_id = parse_key(value)
        if kind == "face":
            if new_id not in new_faces:
                raise RebindError(f"{key} maps to face {new_id}, which is not in the new index.")
        else:
            edge = new_edges.get(new_id)
            if edge is None:
                raise RebindError(f"{key} maps to edge {new_id}, which is not in the new index.")
            if edge.get("kind") != "edge":
                raise RebindError(f"{key} maps to edge {new_id}, which is not a one-sided "
                                  "boundary in the new cache (non-boundary mapping).")
            if not edge.get("face_ids"):
                raise RebindError(f"{key} maps to edge {new_id} without an adjacent face.")
    for key in drops:
        kind, old_id = parse_key(key)
        if kind == "edge":
            edge = _edge_map(oldindex).get(old_id)
            if edge is not None and edge.get("kind") != "edge":
                raise RebindError(f"{key} is not a one-sided boundary in the old cache; it cannot "
                                  "be dropped as a boundary binding.")

    # ---- build the new project from copies only --------------------------------
    result = copy.deepcopy(old)
    summary = {"identity_policy": IDENTITY_POLICY, "mapping": dict(sorted(applied.items())),
               "dropped_targets": sorted(drops), "dropped_conditions": [],
               "dropped_group_members": [], "dropped_groups": [], "dropped_patches": [],
               "new_patches": [], "regenerated_couplings": [], "unmapped_new_faces": []}

    old_patches = _patch_map(old)
    face_sources = {}
    for key, value in applied.items():
        kind, old_id = parse_key(key)
        if kind == "face":
            face_sources[parse_key(value)[1]] = old_id

    patches = []
    for new_id in sorted(new_faces):
        source = face_sources.get(new_id)
        if source is not None and source in old_patches:
            patch = {"face_id": new_id}
            patch.update({k: copy.deepcopy(v) for k, v in old_patches[source].items()
                          if k not in ("face_id", "needs_rebind")})
            patches.append(patch)
            summary["new_patches"].append({"face_id": new_id, "source": f"mapped from face:{source}"})
        else:
            patches.append(_default_patch(old, new_faces[new_id]))
            summary["new_patches"].append({"face_id": new_id, "source": "default"})
            summary["unmapped_new_faces"].append(new_id)
    result["patches"] = patches

    for face_id in sorted({parse_key(key)[1] for key in drops if key.startswith("face:")}):
        patch = old_patches.get(face_id)
        if patch is not None:
            summary["dropped_patches"].append(
                {"face_id": face_id, "material_id": patch.get("material_id"),
                 "section_id": patch.get("section_id"), "element": patch.get("element")})

    # groups: remap members by kind, dedupe, drop only when the caller dropped them all
    surviving_groups = {}
    kept_groups = []
    for group in plan["groups"]:
        members, dropped_members = [], []
        for key in group["members"]:
            kind, old_id = parse_key(key)
            if key in drops:
                dropped_members.append(old_id)
                summary["dropped_group_members"].append(
                    {"group_id": group["id"], "kind": kind, "member": old_id})
                continue
            mapped = applied.get(key)
            if mapped is None:
                raise RebindError(f"Group {group['id']} member {key} is neither mapped nor "
                                  "dropped.")  # defensive: required keys are checked above
            members.append(parse_key(mapped)[1])
        deduped = sorted(set(members))
        if not deduped:
            summary["dropped_groups"].append(
                {"group_id": group["id"], "name": group["name"],
                 "reason": "every member was explicitly dropped"})
            continue
        record = {"id": group["id"], "name": group["name"], "kind": group["kind"],
                  "members": deduped}
        kept_groups.append(record)
        surviving_groups[group["id"]] = record
    result["groups"] = kept_groups

    # conditions: direct targets remap; group references survive with their group
    for category in CONDITION_CATEGORIES:
        kept = []
        for item in old.get(category, []):
            if not isinstance(item, dict):
                continue
            record = copy.deepcopy(item)
            record.pop("needs_rebind", None)
            name = record.get("name", "")
            if "group_id" in record:
                group_id = record["group_id"]
                if group_id in surviving_groups:
                    kept.append(record)
                    continue
                old_group = next((g for g in plan["groups"] if g["id"] == group_id), None)
                if old_group is None:
                    raise RebindError(f"{category} {name!r} references group {group_id}, which does "
                                      "not exist in the old project.")
                summary["dropped_conditions"].append(
                    {"category": category, "name": name, "reason":
                     f"group {group_id} lost every member through explicit drops"})
                continue
            key = target_key(record.get("target_kind"), record.get("target_id"))
            if key in drops:
                summary["dropped_conditions"].append(
                    {"category": category, "name": name,
                     "reason": f"target {key} was explicitly dropped"})
                continue
            mapped = applied.get(key)
            if mapped is None:
                raise RebindError(f"{category} {name!r} target {key} is neither mapped nor dropped.")
            record["target_id"] = parse_key(mapped)[1]
            kept.append(record)
        result[category] = kept

    # interfaces: always regenerate as pending geometry candidates
    try:
        from .coupling_diagnostics import new_candidate
    except ImportError as error:  # pragma: no cover - package layout only
        raise RebindError("kratos_iga.coupling_diagnostics is not importable.") from error
    result["couplings"] = [new_candidate(edge["id"]) for edge in newindex.get("edges", [])
                           if isinstance(edge, dict) and edge.get("kind") == "coupling"
                           and isinstance(edge.get("id"), int)]
    for old_coupling in plan["couplings"]:
        summary["regenerated_couplings"].append(
            {"edge_id": old_coupling["edge_id"], "old_state": old_coupling["state"],
             "old_origin": old_coupling["origin"], "new_state": "pending",
             "new_origin": "geometry"})

    result["source_step"] = old.get("source_step", "") if source_step is None else str(source_step)
    result["source_sha256"] = new_source
    result["geometry_sha256"] = new_geometry
    result.pop("geometry_needs_rebind", None)
    summary["source_step"] = result["source_step"]
    summary["source_sha256"] = new_source
    summary["geometry_sha256"] = new_geometry
    summary["cleared_needs_rebind"] = True
    summary["note"] = ("Project.validate remains the export gate; support sides and control-net "
                       "overlap are validated later, not here.")
    return {"project": result, "summary": summary}


# --------------------------------------------------------------------------- #
# missing geometry
# --------------------------------------------------------------------------- #

def mark_geometry_changed(project, reason):
    """Return a copy whose bindings are all pending because the geometry is untrusted.

    Used when the saved source STEP is missing or the snapshot cannot be explained:
    nothing is inherited by a coincidentally equal id, and ``project.validate``
    reports the pending state as an export error.
    """
    if not isinstance(project, dict):
        raise RebindError("The project must be a mapping.")
    text = str(reason)
    marked = copy.deepcopy(project)
    marked["geometry_needs_rebind"] = text
    for category in CONDITION_CATEGORIES:
        for item in marked.get(category, []):
            if isinstance(item, dict):
                item["needs_rebind"] = text
    for group in marked.get("groups", []):
        if isinstance(group, dict):
            group["needs_rebind"] = text
    return marked


def mark_geometry_missing(project, source_step=""):
    """Convenience wrapper for the missing-source-STEP case."""
    name = source_step or project.get("source_step", "") if isinstance(project, dict) else ""
    return mark_geometry_changed(project, f"the stored source STEP {name!r} is missing")
