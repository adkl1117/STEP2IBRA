"""Named face/edge groups: one deterministic expansion for every consumer.

This module is stdlib-only so ``project`` (validation, GUI-safe), ``bundle`` and the
FreeCAD panel all expand groups the same way. It is the single source of truth for:

* group membership resolution against the geometry ``index.json``;
* the active analysis domain derived from ``patches[].active``;
* deterministic, de-duplicated condition targets.

Nothing here stores geometry evidence. ``index.json`` owns ids, topology and
diagnostics; the project owns the group definition and the condition reference.
"""
from __future__ import annotations

KINDS = ("face", "edge")
TARGET_KEYS = ("target_kind", "target_id")
SIDES = {"u0": [0, -1], "u1": [1, -1], "v0": [-1, 0], "v1": [-1, 1],
         "00": [0, 0], "10": [1, 0], "01": [0, 1], "11": [1, 1], "all": [-1, -1]}


def expand(groups, index):
    """Return ``{group_id: record}`` with resolved members and dangling member ids.

    Members are sorted in ascending integer order so every consumer expands the same
    way. Missing members are reported, never silently dropped.
    """
    faces = {f["id"] for f in index.get("faces", [])}
    edges = {e["id"] for e in index.get("edges", [])}
    result = {}
    for group in groups or []:
        if not isinstance(group, dict) or not isinstance(group.get("id"), int):
            continue
        kind = group.get("kind")
        available = faces if kind == "face" else edges if kind == "edge" else set()
        members = [m for m in group.get("members", []) if isinstance(m, int) and not isinstance(m, bool)]
        resolved = sorted(set(members))
        result[group["id"]] = {"id": group["id"], "name": group.get("name", ""), "kind": kind,
                               "members": resolved,
                               "missing": [m for m in resolved if m not in available]}
    return result


def resolve(record, groups, index):
    """Return ``{"kind", "ids", "source", "missing", "group"}`` for one condition record.

    ``source`` is ``"direct"`` or ``"group:<id>"``. ``ids`` keeps a deterministic
    ascending order (group members are sorted, so a group is order-independent).
    ``missing`` lists indices that do not exist in the current geometry cache; the
    caller reports them as errors. A record carries either a group reference or a
    direct target, never both.
    """
    if "group_id" in record:
        group = groups.get(record["group_id"])
        if group is None:
            return {"kind": None, "ids": [], "source": f"group:{record['group_id']}",
                    "missing": [], "group": None}
        return {"kind": group["kind"], "ids": list(group["members"]), "source": f"group:{group['id']}",
                "missing": list(group.get("missing") or []), "group": group}
    kind = record.get("target_kind")
    ident = record.get("target_id")
    available = {f["id"] for f in index.get("faces", [])} if kind == "face" else \
                {e["id"] for e in index.get("edges", [])} if kind == "edge" else set()
    missing = [] if not isinstance(ident, int) or isinstance(ident, bool) or ident in available else [ident]
    return {"kind": kind, "ids": [ident], "source": "direct", "missing": missing, "group": None}


def target_kinds(record, groups, index):
    """The distinct face/edge kinds referenced by a record (group or direct)."""
    if "group_id" in record:
        group = groups.get(record["group_id"])
        return {group["kind"]} if group and group.get("kind") in KINDS else set()
    kind = record.get("target_kind")
    return {kind} if kind in KINDS else set()


def active_face_ids(project):
    """Face ids whose patch is part of the analysis domain."""
    return {p["face_id"] for p in project.get("patches", []) if p.get("active", True)}


def face_is_active(face_id, active_faces):
    return face_id in active_faces


def edge_is_active(edge, active_faces):
    """A one-sided boundary is active when at least one adjacent face is."""
    return any(fid in active_faces for fid in edge.get("face_ids", []))


def coupling_is_active(edge, active_faces):
    """A confirmed interface requires both sides inside the domain."""
    face_ids = edge.get("face_ids", [])
    return len(face_ids) == 2 and all(fid in active_faces for fid in face_ids)


def group_summary(group):
    """One-line, GUI-ready group summary that never hides missing members."""
    missing = group.get("missing") or []
    text = f"{group['kind']} · {len(group['members'])} member(s)"
    if missing:
        text += f" · MISSING {missing}"
    return text


# --------------------------------------------------------------------------- #
# Strong-enforcement control-net layout (shared by validation and export)
# --------------------------------------------------------------------------- #

def control_grid(index, face_id):
    """Original ``(nu, nv)`` control-point counts of a face, or ``None``.

    Reads the additive ``control_point_counts`` metadata. An index built before that
    metadata existed has no reliable grid, so the caller must require a rebuild
    instead of guessing a factorization.
    """
    for face in index.get("faces", []):
        if face["id"] == face_id:
            counts = face.get("control_point_counts")
            if isinstance(counts, dict):
                return counts.get("u"), counts.get("v")
            return None
    return None


def final_grid(index, face_id, patch, refinement_enabled):
    """Control-net size after the refinement actually exported for this face.

    Kratos ``RefinementModeler`` elevates the degree and inserts knots without
    changing the number of positive-length spans, so
    ``n_final = n_original + degree_delta * spans + insert_per_span * spans``
    (per axis). Spans per axis are stored in the index; without them (or without the
    counts) the grid is unknown and the caller reports that a rebuild is required.
    """
    if not refinement_enabled:
        return control_grid(index, face_id)
    if not isinstance(patch, dict):
        return None
    counts = control_grid(index, face_id)
    if counts is None:
        return None
    source_degrees = next((f.get("degrees") for f in index.get("faces", [])
                           if f["id"] == face_id), None)
    if not source_degrees or len(source_degrees) != 2:
        return None
    spans_meta = next((f.get("knot_spans") or {} for f in index.get("faces", [])
                       if f["id"] == face_id), None)
    if spans_meta is None:
        return None
    grid = []
    for axis, original in (("u", counts[0]), ("v", counts[1])):
        spans = spans_meta.get(axis)
        if not isinstance(original, int) or not isinstance(spans, int) or spans < 1:
            return None
        degrees = patch.get("degree_" + axis)
        inserts = patch.get("insert_" + axis)
        if not isinstance(degrees, int) or not isinstance(inserts, int):
            return None
        delta = degrees - source_degrees[0 if axis == "u" else 1]
        if delta < 0:
            return None
        grid.append(original + delta * spans + inserts * spans)
    return grid[0], grid[1]


def row_locator(local):
    """Classify one ``local_parameters`` pair.

    ``-1`` marks "the whole axis"; a 0/1 marker names the low/high boundary. Returns
    ``("row", marker)`` for a u-fixed boundary, ``("column", marker)`` for a v-fixed
    boundary, ``("all", None)`` for the whole patch, or ``None``.
    """
    if not isinstance(local, (list, tuple)) or len(local) != 2:
        return None
    u, v = local
    if u == -1 and v == -1:
        return "all", None
    if u == -1 and isinstance(v, int) and not isinstance(v, bool) and v >= 0:
        return "column", v
    if v == -1 and isinstance(u, int) and not isinstance(u, bool) and u >= 0:
        return "row", u
    if isinstance(u, int) and not isinstance(u, bool) and u >= 0 and \
            isinstance(v, int) and not isinstance(v, bool) and v >= 0:
        # A natural corner: both parametric markers are present (SIDES 00/10/01/11).
        return "corner", (u, v)
    return None


def _boundary_index(marker, total):
    if total < 1:
        return None
    return 0 if marker == 0 else total - 1


def _adjacent_index(marker, total):
    """Neighbouring control row copied by ``clamp``: ``1`` for low, ``n-2`` for high."""
    if total < 2:
        return None
    return 1 if marker == 0 else total - 2


def build_region(grid, local, clamp=False):
    """Exact set of ``(u, v)`` control indices selected by one face/local entry.

    ``clamp`` adds the neighbouring row/column of the fixed boundary exactly once: the
    low boundary uses index ``1`` and the high boundary uses ``n-2``.
    """
    if grid is None:
        return None
    nu, nv = grid
    if nu < 1 or nv < 1:
        return None
    located = row_locator(local)
    if located is None:
        return None
    axis, marker = located
    if axis == "all":
        return {(u, v) for u in range(nu) for v in range(nv)}
    if axis == "corner":
        u_index = _boundary_index(marker[0], nu)
        v_index = _boundary_index(marker[1], nv)
        if u_index is None or v_index is None:
            return None
        # A single control point; clamp is meaningless at a corner (rejected upstream).
        return {(u_index, v_index)}
    total = nu if axis == "row" else nv
    boundary = _boundary_index(marker, total)
    if boundary is None:
        return None
    index_set = {boundary}
    if clamp:
        adjacent = _adjacent_index(marker, total)
        if adjacent is not None:
            index_set.add(adjacent)
    if axis == "row":
        return {(u, v) for u in index_set for v in range(nv)}
    return {(u, v) for u in range(nu) for v in index_set}


def target_locations(item, index, groups):
    """Every ``(face_id, local_parameters)`` an essential-boundary record selects.

    A direct face target with a ``side`` yields that one natural boundary; a direct
    edge target yields the edge's own ``strong_local``; a named group is expanded to
    **all** of its members. Returns ``None`` when any member has no statically
    derivable location, so a mixed group fails as a whole instead of silently
    dropping the member that could not be resolved.
    """
    if "group_id" in item:
        group = groups.get(item["group_id"])
        if group is None:
            return None
        if group["kind"] == "face":
            side = item.get("side")
            if side not in SIDES:
                return None
            return [(member, list(SIDES[side])) for member in group["members"]]
        locations = []
        for member in group["members"]:
            found = _edge_location(member, index)
            if found is None:
                return None
            locations.append(found)
        return locations
    if item.get("target_kind") == "face":
        side = item.get("side")
        if side not in SIDES:
            return None
        return [(item["target_id"], list(SIDES[side]))]
    found = _edge_location(item.get("target_id"), index)
    return [found] if found else None


def _edge_location(edge_id, index):
    for edge in index.get("edges", []):
        if edge["id"] == edge_id and edge.get("strong_local") is not None and edge.get("face_ids"):
            return edge["face_ids"][0], list(edge["strong_local"])
    return None


def strong_spec(item, index, groups, project=None):
    """Resolved control-net spec for one strong record.

    ``locations`` holds ``(face_id, local_parameters, region, component_mask)`` where
    ``region`` is an exact set of final-grid ``(u, v)`` indices. ``reason`` is ``None``
    on success, ``"location"`` when the target has no natural boundary, or
    ``"grid"`` when the geometry cache lacks the control-net metadata needed to decide
    overlap (the caller then requires a rebuild instead of guessing).
    """
    component_mask = tuple(value is not None for value in item["value"])
    locations = target_locations(item, index, groups)
    if not locations:
        return {"locations": [], "reason": "location"}
    patches = {patch.get("face_id"): patch for patch in (project or {}).get("patches", [])}
    refinement = (project or {}).get("refinement_enabled", True)
    resolved, reason = [], None
    for face_id, local in locations:
        grid = final_grid(index, face_id, patches.get(face_id), refinement)
        region = build_region(grid, local, bool(item.get("clamp")))
        if region is None:
            reason = "grid"
            continue
        resolved.append((face_id, local, region, component_mask))
    if resolved and reason is None:
        return {"locations": resolved, "reason": None}
    return {"locations": resolved, "reason": reason or "grid"}


def regions_overlap(a, b):
    """True when two exact ``(u, v)`` index sets share at least one control point."""
    if not a or not b:
        return False
    return not a.isdisjoint(b)
