"""Reusable document-data owner for the IBRA analysis project (P0-3).

Design contract
---------------
* The analysis project is stored in **simple FreeCAD properties** on one
  ``App::FeaturePython`` owner object per document, so a native ``.FCStd`` save and
  reopen keeps it without any addon-specific persistence format.
* ``index.json`` (geometry evidence) and the ``source_sha256``/``geometry_sha256``
  checks stay where they are; this module stores their values plus the entity
  association and never re-derives them.
* No hashing system is added. Geometry-change detection reuses the existing
  checks and compares the live shape's full native BREP export against the
  converter's own reference BREP file token-by-token
  (``kratos_iga.brep_compare``), so it survives a second process and detects
  same-area/same-count edits.
* Live shapes are **never** refreshed from the cache or overwritten.
* The owner object is per document; no module-level project state is shared
  between documents. A native-save observer runs the GUI's committed-state
  provider *before* serialization and flags/refuses instead of silently saving
  stale data.

The module imports ``FreeCAD`` only; it must not import FreeCADGui, OCP or Kratos.
"""
from __future__ import annotations

import json
import os
import weakref
from typing import Any, Callable

import FreeCAD as App

try:  # normal package import
    from . import brep_compare as _brep_compare
except ImportError:  # document.py loaded as a standalone file (the tests do this);
    # load the sibling by path so an installed workbench copy can never shadow it.
    import importlib.util as _importlib_util

    _spec = _importlib_util.spec_from_file_location(
        "ibra_brep_compare_under_test",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "brep_compare.py"))
    _brep_compare = _importlib_util.module_from_spec(_spec)
    _spec.loader.exec_module(_brep_compare)

OWNER_NAME = "IBRA_Project"
PROP_GROUP = "IBRA"
_REQUIRED_PROPERTIES = ("ProjectJSON", "SchemaVersion", "SourceStep", "SourceSha256",
                        "GeometrySha256", "CacheDir", "EntityMap", "SyncError")


class DocumentError(RuntimeError):
    """Invalid document state that the caller must resolve explicitly."""


class SyncRefused(DocumentError):
    """A native save was refused because the committed state could not be written."""


# --------------------------------------------------------------------------- owner
def _is_owner(obj: Any) -> bool:
    try:
        properties = set(obj.PropertiesList)
    except Exception:  # noqa: BLE001 - non-owner objects are simply not owners
        return False
    return all(name in properties for name in _REQUIRED_PROPERTIES)


def owners(doc: Any) -> list:
    return [obj for obj in doc.Objects if _is_owner(obj)]


def find_owner(doc: Any):
    found = owners(doc)
    if len(found) > 1:
        raise DocumentError(
            f"Document {doc.Name!r} has {len(found)} IBRA project owners "
            f"({', '.join(o.Name for o in found)}); exactly one is required.")
    return found[0] if found else None


def ensure_owner(doc: Any):
    """Return the single owner, creating it when absent. Never duplicates it."""
    owner = find_owner(doc)
    if owner is not None:
        return owner
    owner = doc.addObject("App::FeaturePython", OWNER_NAME)
    label = "IBRA Analysis Project"
    for name, ptype in (("ProjectJSON", "App::PropertyString"), ("SchemaVersion", "App::PropertyInteger"),
                        ("SourceStep", "App::PropertyString"), ("SourceSha256", "App::PropertyString"),
                        ("GeometrySha256", "App::PropertyString"), ("CacheDir", "App::PropertyString"),
                        ("EntityMap", "App::PropertyString"), ("SyncError", "App::PropertyString")):
        owner.addProperty(ptype, name, PROP_GROUP)
        if name == "ProjectJSON":
            owner.setEditorMode(name, 2) if hasattr(owner, "setEditorMode") else None
    owner.Label = label
    return owner


def remove_owner(doc: Any) -> bool:
    """Explicitly drop the association object. Returns True when one was removed."""
    owner = find_owner(doc)
    if owner is None:
        return False
    doc.removeObject(owner.Name)
    return True


# --------------------------------------------------------------------------- write
def _dump_json(value: Any, label: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DocumentError(f"{label} cannot be stored: {exc}") from exc


def _capture_references(doc: Any, cache_dir: str, entries: list[dict]) -> list[dict]:
    """Export a reference BREP for entries that have none, from the live object.

    Read-only with respect to the live shape. Measured on FreeCAD 1.1.3: a rebuilt
    ``Part.makePolygon`` does NOT serialize identically to the live ``Part::Feature``
    wire (the feature carries a Location section), so a captured BRep is used
    instead of a reconstructed edge; the index polyline points stay as metadata.
    """
    if not cache_dir:
        return entries
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError:
        return entries
    for entry in entries:
        if entry.get("reference"):
            continue
        obj = doc.getObject(entry.get("object", ""))
        if obj is None or obj.Shape.isNull():
            continue
        filename = f"{entry['kind']}_{entry['brep_id']}.brep"
        try:
            obj.Shape.exportBrep(os.path.join(cache_dir, filename))
        except Exception:  # noqa: BLE001 - left without a reference, which blocks
            continue
        entry["reference"] = filename
    return entries


def upsert(doc: Any, *, project: Any = None, source_step: str | None = None,
           source_sha256: str | None = None, geometry_sha256: str | None = None,
           entity_map: Any = None, cache_dir: str | None = None,
           sync_error: str | None = None):
    """Store the committed analysis data on the document owner.

    Every value is validated and serialized **before** any property is touched, so a
    rejected field leaves the stored state exactly as it was. The function never
    reads or writes the ``Shape`` of any object, so a user's geometry edits can never
    be lost here.
    """
    project_json = None
    entity_json = None
    schema_version = None
    if project is not None:
        if not isinstance(project, dict):
            raise DocumentError("The analysis project must be a mapping.")
        # Prevalidate everything that could fail before a single property is written,
        # so a rejected field never leaves a partial commit behind.
        try:
            schema_version = int(project.get("schema_version", 0))
        except (TypeError, ValueError) as exc:
            raise DocumentError(f"The project schema version is not an integer: {exc}") from exc
        project_json = _dump_json(project, "The analysis project")
    if entity_map is not None:
        if not isinstance(entity_map, (list, tuple)):
            raise DocumentError("The entity association must be a list.")
        cleaned = []
        for index, entry in enumerate(entity_map):
            if not isinstance(entry, dict):
                raise DocumentError(f"Entity association entry {index} must be a mapping.")
            if entry.get("kind") not in ("face", "edge", "coupling"):
                raise DocumentError(f"Entity association entry {index} has an unknown kind.")
            if not isinstance(entry.get("brep_id"), int):
                raise DocumentError(f"Entity association entry {index} needs an integer brep_id.")
            if not entry.get("object"):
                raise DocumentError(f"Entity association entry {index} needs an object name.")
            if not (entry.get("reference") or entry.get("points")):
                raise DocumentError(f"Entity association entry {index} has no reference geometry.")
            cleaned.append(entry)
        if cache_dir:
            cleaned = _capture_references(doc, cache_dir, cleaned)
        entity_json = _dump_json(cleaned, "The entity association")
    owner = ensure_owner(doc)
    if project_json is not None:
        owner.ProjectJSON = project_json
        owner.SchemaVersion = schema_version
    if source_step is not None:
        owner.SourceStep = str(source_step)
    if source_sha256 is not None:
        owner.SourceSha256 = str(source_sha256)
    if geometry_sha256 is not None:
        owner.GeometrySha256 = str(geometry_sha256)
    if entity_json is not None:
        owner.EntityMap = entity_json
    if cache_dir is not None:
        owner.CacheDir = str(cache_dir)
    if sync_error is not None:
        owner.SyncError = str(sync_error)
    return owner


def clear_sync_error(doc: Any):
    owner = find_owner(doc)
    if owner is not None and owner.SyncError:
        owner.SyncError = ""


# --------------------------------------------------------------------------- read
class DocumentState:
    """A read-only view of one document's stored association."""

    def __init__(self, doc: Any):
        self.doc_name = doc.Name
        self.diagnostics: list[str] = []
        owner = find_owner(doc)
        self.owner_name = owner.Name if owner is not None else None
        self.project = None
        self.entity_map: list[dict] = []
        self.source_step = ""
        self.source_sha256 = ""
        self.geometry_sha256 = ""
        self.cache_dir = ""
        self.sync_error = ""
        if owner is None:
            self.diagnostics.append("No stored IBRA project in this document.")
            return
        self.source_step = owner.SourceStep
        self.source_sha256 = owner.SourceSha256
        self.geometry_sha256 = owner.GeometrySha256
        self.cache_dir = owner.CacheDir
        self.sync_error = owner.SyncError
        self.schema_version = int(owner.SchemaVersion)
        if owner.ProjectJSON:
            try:
                loaded = json.loads(owner.ProjectJSON)
            except ValueError as exc:
                self.diagnostics.append(
                    f"The stored project JSON is corrupt ({exc}). Reimport the source STEP or "
                    "restore a saved project; the document has NOT been reset.")
            else:
                if isinstance(loaded, dict):
                    self.project = loaded
                else:
                    self.diagnostics.append("The stored project JSON is not an object; it was not loaded.")
        else:
            self.diagnostics.append("The stored project JSON is empty.")
        if owner.EntityMap:
            try:
                entries = json.loads(owner.EntityMap)
            except ValueError as exc:
                self.diagnostics.append(f"The stored entity association is corrupt ({exc}).")
            else:
                if isinstance(entries, list):
                    for index, entry in enumerate(entries):
                        if not isinstance(entry, dict):
                            self.diagnostics.append(
                                f"Entity association entry {index} is not an object; it was kept out of "
                                "the comparison but is reported, not silently dropped.")
                            continue
                        problems = []
                        if entry.get("kind") not in ("face", "edge", "coupling"):
                            problems.append(f"unknown kind {entry.get('kind')!r}")
                        if not isinstance(entry.get("brep_id"), int):
                            problems.append("missing integer brep_id")
                        if not entry.get("object"):
                            problems.append("missing object name")
                        if not (entry.get("reference") or entry.get("points")):
                            problems.append("no reference geometry or polyline")
                        if problems:
                            self.diagnostics.append(
                                f"Entity association entry {index} is invalid ({'; '.join(problems)}); "
                                "it is kept for explicit rebind, not dropped.")
                        self.entity_map.append(entry)
                else:
                    self.diagnostics.append("The stored entity association is not a list.")
        if self.sync_error:
            self.diagnostics.append(f"The last native save could not commit the edited state: {self.sync_error}")
        if not self.source_step:
            self.diagnostics.append("No source STEP path is stored; the project cannot be reimported.")
        elif not os.path.isfile(self.source_step):
            self.diagnostics.append(f"The stored source STEP {self.source_step!r} is missing.")
        if self.cache_dir and not os.path.isdir(self.cache_dir):
            self.diagnostics.append(f"The stored conversion cache {self.cache_dir!r} is missing; "
                                    "geometry comparisons cannot be rebuilt from it.")

    def has_errors(self) -> bool:
        return bool(self.diagnostics)


def read(doc: Any) -> DocumentState:
    return DocumentState(doc)


# --------------------------------------------------------------------------- entities
def entity_map_from_index(index: dict, object_names: dict[tuple[str, int], str]) -> list[dict]:
    """Build the association from the converter index and the document objects.

    ``object_names`` maps ``(kind, brep_id)`` to the FreeCAD object Name. Reference
    files reuse the converter's own face BREPs (``index['faces'][].brep_file``), so no
    additional geometry copy is created.
    """
    face_files = {f["id"]: f.get("brep_file", "") for f in index.get("faces", [])}
    entries = []
    for face in index.get("faces", []):
        key = ("face", face["id"])
        if key in object_names:
            entries.append({"kind": "face", "brep_id": face["id"], "object": object_names[key],
                            "reference": face_files.get(face["id"], "")})
    for edge in index.get("edges", []):
        key = (edge["kind"], edge["id"])
        if key in object_names:
            entry = {"kind": edge["kind"], "brep_id": edge["id"], "object": object_names[key],
                     "reference": ""}
            points = edge.get("points")
            if isinstance(points, list) and len(points) >= 2:
                entry["points"] = [[float(v) for v in p[:3]] for p in points]
            entries.append(entry)
    return entries


def _load_shape(path: str):
    from Part import Shape
    shape = Shape()
    shape.read(path)
    if shape.isNull():
        raise DocumentError(f"reference BREP is empty: {path}")
    return shape


def check_entities(doc: Any, state: DocumentState | None = None, *, cache_dir: str | None = None) -> list[str]:
    """Compare live objects with the stored association and reference BREPs.

    Returns human-readable diagnostics. It only *reads* live shapes: a modified
    object is reported, never refreshed from the cache or overwritten.
    """
    import os

    state = state or read(doc)
    diagnostics = list(state.diagnostics)
    base = cache_dir if cache_dir is not None else state.cache_dir
    for entry in state.entity_map:
        name = entry.get("object", "")
        label = f"{entry.get('kind')} {entry.get('brep_id')}"
        obj = doc.getObject(name) if name else None
        if obj is None:
            diagnostics.append(f"{label}: the associated object {name!r} is missing from the document.")
            continue
        previous = entry.get("placement")
        if previous is not None and str(obj.Placement) != previous:
            diagnostics.append(f"{label}: object {name!r} was moved since it was associated.")
        live = obj.Shape
        if live.isNull():
            diagnostics.append(f"{label}: object {name!r} has no shape.")
            continue
        reference_shape, problem = _reference_shape(entry, base)
        if reference_shape is None:
            diagnostics.append(f"{label}: {problem}")
            continue
        differences = _shape_differences(live, reference_shape)
        if differences:
            diagnostics.append(f"{label}: object {name!r} no longer matches the stored reference "
                               f"({'; '.join(differences)}). Rebind the entity before exporting.")
    return diagnostics


def _reference_shape(entry: dict, base: str):
    """Rebuild the stored reference shape for one association entry.

    Faces use the converter's own reference BREP; edges use the polyline points the
    index recorded, rebuilt with ``Part.makePolygon``. A missing reference is
    reported, never guessed.
    """
    import os

    reference = entry.get("reference") or ""
    if reference:
        path = reference if os.path.isabs(reference) else os.path.join(base or "", reference)
        if not os.path.isfile(path):
            return None, (f"the reference geometry {reference!r} is missing (cache cleaned?). "
                          "Reimport the source STEP.")
        try:
            return _load_shape(path), ""
        except Exception as exc:  # noqa: BLE001 - reported, never hidden
            return None, f"the reference geometry could not be read ({type(exc).__name__}: {exc})."
    points = entry.get("points")
    if isinstance(points, list) and len(points) >= 2:
        # The points remain useful metadata, but a rebuilt polygon does not
        # serialize identically to the live feature, so this cannot be used as an
        # acceptance comparison. It is reported as unknown, which blocks.
        return None, (f"no captured reference BREP for object {entry.get('object')!r} "
                      "(only index polyline points are stored); re-associate it with the "
                      "conversion cache available.")
    return None, (f"no reference geometry is stored for object {entry.get('object')!r}; "
                  "reimport the source STEP to rebuild the comparison.")


def _shape_differences(live: Any, reference: Any) -> list[str]:
    """Compare a live shape with its stored reference by full BREP token stream.

    Both sides go through the same native writer (``exportBrepToString()`` writes
    ``TopTools_FormatVersion_VERSION_1``), so a clean FCStd save/reopen -- which adds an
    identity ``Location`` record -- normalizes away. Every geometry token is preserved and
    structural ids/flags are compared exactly; there is no sampling, bbox, area or count
    surrogate. A reference outside the verified grammar is reported as an
    unknown/rebind diagnostic, never silently accepted.
    """
    try:
        live_text = live.exportBrepToString()
        reference_text = reference.exportBrepToString()
    except Exception as exc:  # noqa: BLE001 - reported, never hidden
        return [f"the shape BREP could not be exported ({type(exc).__name__}: {exc})"]
    try:
        outcome = _brep_compare.compare(live_text, reference_text)
    except _brep_compare.BrepCompareError as error:
        return [f"the shape BREP is not in the verified V1 comparison grammar ({error}); "
                "rebind the entity"]
    if outcome["equal"]:
        return []
    return [_brep_compare.describe_difference(live_text, reference_text)]


def entities_need_rebind(doc: Any, state: DocumentState | None = None, **kwargs) -> bool:
    return bool(check_entities(doc, state, **kwargs))


# --------------------------------------------------------------------------- save hook
class SaveHook:
    """Explicit lifecycle handle for one document's native-save observer."""

    def __init__(self, doc: Any, provider: Callable, on_error: str):
        self.doc = doc
        self.provider = provider
        self.on_error = on_error
        self.calls: list[str] = []
        self.last_error = ""
        self.observer = _SaveObserver(self)
        self._closed = False

    def close(self):
        if self._closed:
            return
        try:
            App.removeDocumentObserver(self.observer)
        finally:
            self._closed = True
            _INSTALLED.pop(id(self.doc), None)
            try:
                _INSTALLED_BY_NAME.pop(self.doc.Name, None)
            except Exception:  # noqa: BLE001 - document already torn down
                pass

    def handle_save(self, doc) -> None:
        if self._closed or doc is not self.doc:
            return
        self.calls.append(doc.Name)
        try:
            payload = self.provider(doc)
            if payload is None:
                return
            if not isinstance(payload, dict):
                raise DocumentError("The save provider must return a mapping of committed fields or None.")
            upsert(doc, sync_error="", **payload)
            self.last_error = ""
        except Exception as exc:  # noqa: BLE001 - every commit failure must be visible
            message = f"{type(exc).__name__}: {exc}"
            self.last_error = message
            try:
                upsert(doc, sync_error=message)
            except Exception:  # noqa: BLE001
                pass
            # FreeCAD swallows exceptions raised inside a document observer, so the
            # observer cannot abort the write itself: it records the failure and
            # leaves the previously stored JSON untouched. Callers that must block
            # the save use commit_before_save() *before* doc.save(). ``on_error`` names
            # this intent, but the honest guarantee in this build is the visible flag.
            if self.on_error == "block":
                raise SyncRefused(
                    f"Native save refusal requested but not enforceable: the committed analysis state "
                    f"could not be written ({message}). The failure is flagged in SyncError.") from exc


def commit_before_save(doc: Any, provider: Callable) -> None:
    """Commit the edited state before a native save, refusing on failure.

    The GUI's save command should call this first, then ``doc.save()``. Unlike the
    observer it runs in the caller's stack, so a failure raises :class:`SyncRefused`
    and the save can be aborted with a visible message.
    """
    try:
        payload = provider(doc)
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller, never silent
        message = f"{type(exc).__name__}: {exc}"
        upsert(doc, sync_error=message)
        raise SyncRefused(
            f"Save cancelled: the committed analysis state could not be written ({message}).") from exc
    if payload is None:
        return
    if not isinstance(payload, dict):
        raise SyncRefused("The save provider must return a mapping of committed fields or None.")
    upsert(doc, sync_error="", **payload)


class _SaveObserver:
    """Thin adapter; the module-level observer is global by FreeCAD API design but
    carries no project state and ignores every document except its own."""

    def __init__(self, hook: SaveHook):
        self._hook = weakref.ref(hook)

    def slotStartSaveDocument(self, doc, file_name):
        hook = self._hook()
        if hook is not None:
            hook.handle_save(doc)

    def slotFinishSaveDocument(self, doc, file_name):
        return None


class _LifecycleObserver:
    """Closes hooks when their document is closed or deleted.

    Module-level because FreeCAD exposes only a global observer API, but it holds no
    project data and only releases the per-document hooks, so no state crosses
    documents.
    """

    def slotDeletedDocument(self, doc):
        _release_for(doc)

    def slotCloseDocument(self, doc):
        _release_for(doc)


def _release_for(doc) -> None:
    try:
        name = doc.Name
    except Exception:  # noqa: BLE001 - a document already being torn down has no name
        return
    hook = _INSTALLED_BY_NAME.get(name)
    if hook is not None:
        hook.close()


_LIFECYCLE = None


def _ensure_lifecycle() -> None:
    global _LIFECYCLE
    if _LIFECYCLE is None:
        _LIFECYCLE = _LifecycleObserver()
        App.addDocumentObserver(_LIFECYCLE)


def shutdown() -> None:
    """Explicit module teardown: remove the lifecycle observer and every hook."""
    global _LIFECYCLE
    for hook in list(_INSTALLED.values()):
        hook.close()
    if _LIFECYCLE is not None:
        try:
            App.removeDocumentObserver(_LIFECYCLE)
        finally:
            _LIFECYCLE = None


_INSTALLED: dict[int, SaveHook] = {}
_INSTALLED_BY_NAME: dict[str, SaveHook] = {}


def install_save_observer(doc: Any, provider: Callable, *, on_error: str = "block") -> SaveHook:
    """Run ``provider(doc)`` inside the native save, before serialization.

    ``provider`` returns a mapping with any of ``project``, ``source_step``,
    ``source_sha256``, ``geometry_sha256``, ``entity_map``, ``cache_dir`` or ``None``
    to skip. On a provider failure the hook records ``SyncError`` and leaves the
    previously stored JSON untouched.

    Evidence (FreeCAD 1.1.3, ``runtime/p0-3-review/document_native_probe.py``): an
    exception raised from this observer is **swallowed** by ``Document.save()``, so
    ``on_error="block"`` cannot itself abort the write; it only records the flag. The
    reliable block path is :func:`commit_before_save`, run by the caller before
    ``doc.save()``. Installing twice for the same document replaces the first hook.
    """
    if on_error not in ("block", "flag"):
        raise DocumentError("on_error must be 'block' or 'flag'.")
    _ensure_lifecycle()
    existing = _INSTALLED_BY_NAME.get(doc.Name)
    if existing is not None and not existing._closed:
        existing.close()
    hook = SaveHook(doc, provider, on_error)
    App.addDocumentObserver(hook.observer)
    _INSTALLED[id(doc)] = hook
    _INSTALLED_BY_NAME[doc.Name] = hook
    return hook


def remove_save_observer(hook: SaveHook) -> None:
    hook.close()


def installed_hook(doc: Any):
    return _INSTALLED.get(id(doc))
