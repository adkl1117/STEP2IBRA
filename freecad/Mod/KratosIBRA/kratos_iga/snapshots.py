"""Controlled geometry snapshot: capture live mapped analysis faces into a new STEP.

FreeCAD-side helper for P0-3. It does **not** convert or re-map anything: it writes a
new STEP file from the current face objects, and the GUI then runs the existing worker
conversion (``action == "convert"``) on that file exactly as it does for a user STEP.
No converter code is duplicated here.

Rules enforced by this module:

* the caller passes an explicit document and the existing entity mapping entries
  (``{"kind", "brep_id", "object"}`` from ``document.entity_map_from_index`` or the
  panel's ``(kind, id) -> object name`` mapping);
* **every** mapped ``face`` entry is captured, active or not (activity is a project
  domain choice, not a snapshot filter);
* ``edge`` and ``coupling`` display polylines, owners, preview and unrelated objects are
  never scanned or written — only the named face objects are used, and the excluded
  display entries are reported with their kind;
* only top-level ``Part::Feature`` objects are accepted (the plugin's flat face
  objects). ``App::Link``/``App::Part``/nested transform composition is not verified
  and is rejected; a face nested under any ancestor with a non-identity ``Placement``
  is refused because exporting the face alone would drop that transform, while the
  plugin's non-transforming ``App::DocumentObjectGroup`` (``IBRA_Geometry``) stays
  accepted;
* a missing object, a null or invalid ``Shape``, a Solid/Wire/Vertex, or a Compound
  containing anything that is not a Face is a hard error, never a silent omission;
* an edited face split into several Faces (Compound/Shell of Faces) is captured as all
  of its face parts;
* the live ``Shape`` + ``Placement`` are written once in global coordinates and the
  source objects are not modified, sewn, healed or recomputed;
* the destination must be a new file: the source is never overwritten, the STEP is
  first written to a temporary sibling and then published atomically **and without
  clobbering** a file that appeared meanwhile;
* no manifest, hash or persistent side file is produced.

The module stays importable with the standard library alone: ``Part`` is imported only
inside :func:`capture_face_snapshot`.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

FACE_FORMATS = ("Face", "Shell", "Compound")  # Compound additionally needs face children
# Entry kinds the panel/document mapping actually carries: faces are captured, the
# display-only edge and coupling polylines are reported as excluded.
CAPTURED_KINDS = ("face",)
DISPLAY_KINDS = ("edge", "coupling")
ENTRY_KINDS = CAPTURED_KINDS + DISPLAY_KINDS
# Verified placement scope: the plugin creates flat, top-level Part::Feature objects.
# App::Link / App::Part / nested transform composition is not verified and is rejected.
VERIFIED_TYPE_ID = "Part::Feature"
IDENTITY_NOTE = ("live FreeCAD objects captured; every binding stays pending and no "
                 "old/new id or bounding-box identity is claimed")
PLACEMENT_SCOPE = "top-level Part::Feature Shape+Placement applied exactly once"


class SnapshotError(ValueError):
    """The snapshot request cannot be satisfied without losing or inventing geometry."""


# --------------------------------------------------------------------------- #
# entries
# --------------------------------------------------------------------------- #

def normalize_face_entries(entries):
    """Return ``(face_entries, display_entries)`` from the supported entry forms.

    Accepted forms match what the panel and the document association actually produce:

    * a list of ``{"kind": "face", "brep_id": 2, "object": "FaceA", ...}`` objects
      (``brep_id`` is the document field; ``id`` is accepted as an equivalent alias,
      and both at once must agree);
    * a ``{"face:2": "FaceA", "coupling:9": "Coup"}`` mapping (canonical
      ``kind:id`` strings; ``coupling`` keys are canonicalised here because
      :mod:`kratos_iga.bindings` only handles face/edge);
    * a ``{("face", 2): "FaceA", ("coupling", 9): "Coup"}`` mapping.

    ``face`` entries are captured. ``edge`` and ``coupling`` entries are display
    polylines that never enter the snapshot; they are returned separately with their
    kind preserved so the caller can see exactly what was left out. Face entries must
    have unique keys and unique object names.
    """
    if entries is None:
        raise SnapshotError("entries must list the mapped face objects (document + mapping).")
    normalized = []
    if isinstance(entries, dict):
        for key, object_name in entries.items():
            if isinstance(key, tuple):
                if len(key) != 2:
                    raise SnapshotError(f"entry key {key!r} must be (kind, id).")
                kind, ident = key
                _check_kind_id(kind, ident)
            elif isinstance(key, str):
                kind, ident = _parse_string_key(key)
            else:
                raise SnapshotError(f"entry key {key!r} must be 'face:2' or ('face', 2).")
            normalized.append({"kind": kind, "id": ident,
                               "object": _object_name(object_name, repr(key))})
    elif isinstance(entries, (list, tuple)):
        for position, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise SnapshotError(f"entries[{position}] must be an object with kind/id/object.")
            kind = entry.get("kind")
            ident = _entry_ident(entry, position)
            _check_kind_id(kind, ident)
            normalized.append({"kind": kind, "id": ident,
                               "object": _object_name(entry.get("object"), f"entries[{position}]")})
    else:
        raise SnapshotError("entries must be a list of objects or a kind:id -> object mapping.")

    faces = [entry for entry in normalized if entry["kind"] in CAPTURED_KINDS]
    display = [entry for entry in normalized if entry["kind"] in DISPLAY_KINDS]
    if not faces:
        raise SnapshotError("no face entry to capture; the snapshot would be empty.")
    keys = [f"{entry['kind']}:{entry['id']}" for entry in faces]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise SnapshotError(f"duplicate face entries {duplicates}; keep one entry per face id.")
    names = [entry["object"] for entry in faces]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise SnapshotError(f"the same object {repeated} is mapped by more than one face entry; "
                            "the snapshot cannot tell which face it is.")
    return faces, display


def _check_kind_id(kind, ident):
    if kind not in ENTRY_KINDS:
        raise SnapshotError(f"unsupported entry kind {kind!r}; expected one of {ENTRY_KINDS}.")
    if not isinstance(ident, int) or isinstance(ident, bool):
        raise SnapshotError(f"entry id {ident!r} must be a non-boolean integer.")


def _object_name(name, label):
    if not isinstance(name, str) or not name.strip():
        raise SnapshotError(f"{label} needs a non-empty object name.")
    return name


def _entry_ident(entry, position):
    """``id`` or ``brep_id`` (the document field); both must agree when present."""
    has_id, has_brep = "id" in entry, "brep_id" in entry
    if not has_id and not has_brep:
        raise SnapshotError(f"entries[{position}] needs an id or brep_id.")
    if has_id and has_brep and entry["id"] != entry["brep_id"]:
        raise SnapshotError(f"entries[{position}] has conflicting id {entry['id']!r} and "
                            f"brep_id {entry['brep_id']!r}; keep one value.")
    return entry["id"] if has_id else entry["brep_id"]


def _parse_string_key(key):
    """Canonical ``kind:id`` string, including ``coupling:`` handled locally."""
    if key.startswith("coupling:"):
        raw = key.split(":", 1)[1]
        try:
            ident = int(raw)
        except ValueError as error:
            raise SnapshotError(f"Mapping key {key!r} has a non-integer id {raw!r}.") from error
        if f"coupling:{ident}" != key:
            raise SnapshotError(f"Mapping key {key!r} is not canonical; write it as "
                                f"'coupling:{ident}' without spaces or leading zeros.")
        return "coupling", ident
    try:
        from .bindings import RebindError, parse_key
    except ImportError as error:  # pragma: no cover - package layout only
        raise SnapshotError("kratos_iga.bindings is not importable.") from error
    try:
        return parse_key(key)
    except RebindError as error:
        raise SnapshotError(str(error)) from error


# --------------------------------------------------------------------------- #
# capture
# --------------------------------------------------------------------------- #

def _validated_faces(name, shape):
    """Return ``(faces, shape_type)`` for a capturable shape or raise."""
    if shape is None:
        raise SnapshotError(f"{name!r} has no Shape; select a face object, not a container or "
                            "preview object.")
    try:
        if shape.isNull():
            raise SnapshotError(f"{name!r} has a null Shape; nothing to capture.")
        if not shape.isValid():
            raise SnapshotError(f"{name!r} has an invalid Shape; repair or rebuild it before "
                                "snapshotting (the snapshot never heals or alters geometry).")
        shape_type = shape.ShapeType
        faces = list(shape.Faces)
    except SnapshotError:
        raise
    except Exception as error:  # noqa: BLE001 - a broken Shape property
        raise SnapshotError(f"{name!r} does not expose a usable Shape: {error}") from error
    if shape_type == "Compound":
        children = list(shape.SubShapes)
        if not children:
            raise SnapshotError(f"{name!r} is an empty compound; nothing to capture.")
        unexpected = sorted({child.ShapeType for child in children if child.ShapeType != "Face"})
        if unexpected:
            raise SnapshotError(
                f"{name!r} is a compound containing {unexpected}; select only its face parts "
                "(a Solid or mixed shape must be extracted explicitly before snapshotting).")
    elif shape_type not in ("Face", "Shell"):
        raise SnapshotError(f"{name!r} is a {shape_type}, not a face; only Face, Shell or a "
                            "compound of Faces can be captured.")
    if not faces:
        raise SnapshotError(f"{name!r} contains no face; select a face object.")
    return faces, shape_type


def _placement_record(obj):
    placement = getattr(obj, "Placement", None)
    if placement is None:
        return None
    try:
        rotation = list(placement.Rotation.Q)
        base = list(placement.Base)
    except Exception:  # noqa: BLE001 - unusual object without a real Placement
        return None
    return {"base": [float(v) for v in base], "rotation": [float(v) for v in rotation]}


def _is_identity_placement(placement, tolerance=1e-12):
    """True only for a provably identity placement; unknown placements count as transforming."""
    try:
        base = placement.Base
        rotation = placement.Rotation.Q
        return (abs(base.x) <= tolerance and abs(base.y) <= tolerance and abs(base.z) <= tolerance
                and abs(rotation[0]) <= tolerance and abs(rotation[1]) <= tolerance
                and abs(rotation[2]) <= tolerance
                and abs(abs(rotation[3]) - 1.0) <= tolerance)
    except Exception:  # noqa: BLE001 - fail closed on an unreadable placement
        return False


def _transforming_ancestors(obj):
    """Ancestors whose non-identity Placement would be dropped by ``Part.export``.

    Walks ``InList`` recursively. Plain grouping containers without a ``Placement``
    (the plugin's ``IBRA_Geometry`` is an ``App::DocumentObjectGroup``) never count, so
    the ordinary plugin structure stays accepted.
    """
    found, seen = [], set()
    stack = list(getattr(obj, "InList", []) or [])
    while stack:
        parent = stack.pop()
        if parent is None:
            continue
        name = getattr(parent, "Name", None)
        if name in seen:
            continue
        seen.add(name)
        placement = getattr(parent, "Placement", None)
        if placement is not None and not _is_identity_placement(placement):
            found.append(parent)
        stack.extend(getattr(parent, "InList", []) or [])
    return found


def capture_face_snapshot(document, entries, destination, source_paths=(), *, echo=False):
    """Write the mapped live face objects to ``destination`` as one new STEP file.

    Returns source metadata describing exactly which live objects were used. The
    destination must not exist and must not be one of ``source_paths``; the file is
    written to a temporary sibling and then published atomically **without replacing**
    a file that appeared in the meantime. Raises :class:`SnapshotError` (with the
    offending object named) and leaves no output on any failure.
    """
    if document is None:
        raise SnapshotError("an explicit FreeCAD document is required.")
    destination = Path(destination)
    if not destination.name:
        raise SnapshotError("destination must name a new STEP file.")
    parent = destination.parent
    if not parent.is_dir():
        raise SnapshotError(f"the destination directory {parent} does not exist.")
    if destination.exists():
        raise SnapshotError(f"the destination {destination} already exists; choose a new file so "
                            "no source is overwritten.")
    resolved = destination.resolve()
    for source in source_paths or ():
        if source and Path(source).resolve() == resolved:
            raise SnapshotError(f"the destination {destination} is the source file {source}; "
                                "choose a new path.")

    faces, display = normalize_face_entries(entries)
    from Part import export as export_step  # FreeCAD Part; imported only when capturing

    objects, metadata = [], []
    for entry in faces:
        name = entry["object"]
        obj = document.getObject(name)
        if obj is None:
            raise SnapshotError(f"{name!r} is not an object in document {document.Name!r}.")
        type_id = getattr(obj, "TypeId", "")
        if type_id != VERIFIED_TYPE_ID:
            raise SnapshotError(
                f"{name!r} is a {type_id or 'object without TypeId'}; only top-level "
                f"{VERIFIED_TYPE_ID} objects are verified for snapshots. App::Link, App::Part "
                "and nested transform composition are not verified - map the source face object "
                "or extract the face explicitly.")
        transforming = _transforming_ancestors(obj)
        if transforming:
            containers = ", ".join(f"{getattr(item, 'Name', '?')!r} ({item.TypeId})"
                                   for item in transforming)
            raise SnapshotError(
                f"{name!r} is nested in a transforming container: {containers}. Exporting the face "
                "alone would drop that placement, so the snapshot refuses it. Move the face to the "
                "top level (or extract it) instead of relying on an unverified parent transform.")
        shape_faces, shape_type = _validated_faces(name, getattr(obj, "Shape", None))
        objects.append(obj)
        metadata.append({"entry": f"face:{entry['id']}", "kind": "face", "id": entry["id"],
                         "object": name, "shape_type": shape_type,
                         "face_count": len(shape_faces),
                         "placement": _placement_record(obj)})

    if echo:
        print(f"snapshot: {len(objects)} object(s), "
              f"{sum(item['face_count'] for item in metadata)} face(s) -> {destination}")

    handle, temporary_name = tempfile.mkstemp(dir=str(parent), prefix=".ibra-snapshot-",
                                              suffix=".step")
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        export_step(objects, str(temporary))
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise SnapshotError(f"STEP export produced no content for {destination}.")
        _publish_no_clobber(temporary, destination)
    except SnapshotError:
        _discard(temporary)
        raise
    except Exception as error:  # noqa: BLE001 - OCCT/FreeCAD export failure
        _discard(temporary)
        raise SnapshotError(f"STEP export failed for {destination}: {error}") from error

    return {"destination": str(resolved), "objects": metadata,
            "face_total": sum(item["face_count"] for item in metadata),
            "excluded_display_entries": [{"kind": entry["kind"], "id": entry["id"],
                                          "object": entry["object"]} for entry in display],
            "placement_scope": PLACEMENT_SCOPE,
            "placed_globally_once": True, "note": IDENTITY_NOTE}


def _publish_no_clobber(temporary, destination):
    """Publish ``temporary`` as ``destination`` without ever replacing an existing file.

    ``os.replace`` would silently overwrite a file that appeared while the STEP was
    being written, so the name is claimed atomically: a hard link first (fails with
    ``FileExistsError`` when the target exists), otherwise an ``O_EXCL`` reservation
    followed by the move onto our own reservation.
    """
    existing = f"the destination {destination} appeared while exporting; it was left untouched. " \
               "Choose a new file."
    try:
        os.link(str(temporary), str(destination))
    except FileExistsError as error:
        raise SnapshotError(existing) from error
    except OSError:
        try:
            handle = os.open(str(destination), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise SnapshotError(existing) from error
        os.close(handle)
        try:
            os.replace(str(temporary), str(destination))
        except OSError:
            _discard(destination)
            raise
    else:
        _discard(temporary)


def _discard(path):
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
