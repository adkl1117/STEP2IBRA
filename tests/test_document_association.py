"""P0-3 document-association tests.

These exercise the real FreeCAD document/property API. Run them with FreeCAD's
bundled Python (the backend venv has no FreeCAD and will skip the module):

    "<FreeCAD>/bin/python.exe" -m unittest discover -s tests -p "test_document_association.py" -v

Set ``IBRA_RUN_NATIVE_PROBE=1`` to also run the two-process native save/reopen
probe through ``runtime/p0-3-review/document_native_host.py`` (bounded 45 s per
child). No existing installation is touched and no GUI is used.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "freecad/Mod/KratosIBRA"))

IMPORT_ERROR = ""
try:
    import FreeCAD as App  # noqa: F401
    import Part  # noqa: F401
    # Import the file under test directly: FreeCAD's own Mod directory may already
    # provide an installed kratos_iga, and this stage must never test that copy.
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "ibra_document_under_test", ROOT / "freecad/Mod/KratosIBRA/kratos_iga/document.py")
    docmod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(docmod)
    assert Path(docmod.__file__).resolve().is_relative_to(ROOT), docmod.__file__
    HAVE_FREECAD = True
except Exception as exc:  # noqa: BLE001
    import traceback
    IMPORT_ERROR = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    HAVE_FREECAD = False


def box(doc, name, x=0.0):
    obj = doc.addObject("Part::Box", name)
    obj.Placement.Base.x = x
    return obj


@unittest.skipUnless(HAVE_FREECAD, f"FreeCAD is not importable here: {IMPORT_ERROR}")
class DocumentAssociationTests(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("IbraDocTest")
        self.addCleanup(self._close_document)

    def _close_document(self):
        try:
            name = self.doc.Name if self.doc is not None else None
        except Exception:  # noqa: BLE001 - the document may already be deleted
            name = None
        if name and name in App.listDocuments():
            App.closeDocument(name)

    def _source(self):
        handle, name = tempfile.mkstemp(suffix=".step")
        os.close(handle)
        self.addCleanup(lambda: Path(name).unlink(missing_ok=True))
        return name

    def test_upsert_is_idempotent_and_single_owner(self):
        box(self.doc, "Face_A")
        docmod.upsert(self.doc, project={"schema_version": 3, "patches": []})
        docmod.upsert(self.doc, source_sha256="src", geometry_sha256="geo")
        self.assertEqual(len(docmod.owners(self.doc)), 1)
        state = docmod.read(self.doc)
        self.assertEqual(state.source_sha256, "src")
        self.assertEqual(state.geometry_sha256, "geo")
        self.assertEqual(state.project["schema_version"], 3)

    def test_duplicate_owner_is_rejected(self):
        docmod.upsert(self.doc, project={"schema_version": 3})
        other = self.doc.addObject("App::FeaturePython", "IBRA_Project_Stray")
        for name, ptype in (("ProjectJSON", "App::PropertyString"), ("SchemaVersion", "App::PropertyInteger"),
                            ("SourceStep", "App::PropertyString"), ("SourceSha256", "App::PropertyString"),
                            ("GeometrySha256", "App::PropertyString"), ("CacheDir", "App::PropertyString"),
                            ("EntityMap", "App::PropertyString"), ("SyncError", "App::PropertyString")):
            other.addProperty(ptype, name, "IBRA")
        with self.assertRaises(docmod.DocumentError):
            docmod.find_owner(self.doc)
        with self.assertRaises(docmod.DocumentError):
            docmod.ensure_owner(self.doc)

    def test_two_documents_do_not_share_state(self):
        box(self.doc, "Face_A")
        docmod.upsert(self.doc, project={"schema_version": 3, "marker": "one"}, source_sha256="one")
        other = App.newDocument("IbraDocOther")
        self.addCleanup(lambda: App.closeDocument(other.Name) if other.Name in App.listDocuments() else None)
        docmod.upsert(other, project={"schema_version": 3, "marker": "two"}, source_sha256="two")
        self.assertEqual(docmod.read(self.doc).project["marker"], "one")
        self.assertEqual(docmod.read(other).project["marker"], "two")
        self.assertIsNot(docmod.find_owner(self.doc), docmod.find_owner(other))

    def test_corrupt_json_is_diagnosed_without_reset(self):
        owner = docmod.upsert(self.doc, project={"schema_version": 3})
        owner.ProjectJSON = "{broken"
        owner.EntityMap = "not-json"
        state = docmod.read(self.doc)
        self.assertIsNone(state.project)
        self.assertTrue(any("corrupt" in d for d in state.diagnostics), state.diagnostics)
        self.assertTrue(state.has_errors())
        self.assertIsNotNone(docmod.find_owner(self.doc))  # never reset to a new project

    def test_clean_fcstd_roundtrip_has_no_false_mismatch(self):
        """A clean save/reopen must compare equal: FCStd adds an identity Location."""
        import Part

        handle, name = tempfile.mkstemp(suffix=".brep")
        os.close(handle)
        reference = Path(name)
        self.addCleanup(lambda: reference.unlink(missing_ok=True))
        feature = self.doc.addObject("Part::Feature", "IBRA_Face_2")
        feature.Shape = Part.makePlane(10.0, 1.0)
        self.doc.recompute()
        feature.Shape.exportBrep(str(reference))
        docmod.upsert(self.doc, project={"schema_version": 3}, source_step=self._source(),
                      entity_map=[{"kind": "face", "brep_id": 2, "object": feature.Name,
                                   "reference": reference.name, "placement": str(feature.Placement)}],
                      cache_dir=str(reference.parent))
        self.assertEqual(docmod.check_entities(self.doc), [])
        stored = docmod.read(self.doc).project
        handle, name = tempfile.mkstemp(suffix=".FCStd")
        os.close(handle)
        target = Path(name)
        self.addCleanup(lambda: target.unlink(missing_ok=True))
        self.doc.saveAs(str(target))
        self.doc = None
        App.closeDocument("IbraDocTest")
        reopened = App.openDocument(str(target))
        try:
            state = docmod.read(reopened)
            self.assertEqual(state.project, stored)
            self.assertEqual(docmod.check_entities(reopened), [],
                             docmod.check_entities(reopened))
        finally:
            App.closeDocument(reopened.Name)

    def test_entity_comparison_reports_edits_and_never_overwrites(self):
        handle, name = tempfile.mkstemp(suffix=".brep")
        os.close(handle)
        reference = Path(name)
        self.addCleanup(lambda: reference.unlink(missing_ok=True))
        obj = box(self.doc, "Face_A")
        self.doc.recompute()
        obj.Shape.exportBrep(str(reference))
        docmod.upsert(self.doc, project={"schema_version": 3}, source_step=self._source(),
                      entity_map=[{"kind": "face", "brep_id": 1, "object": obj.Name,
                                   "reference": reference.name, "placement": str(obj.Placement)}],
                      cache_dir=str(reference.parent))
        self.assertEqual(docmod.check_entities(self.doc), [])
        before = obj.Shape.Volume
        obj.Length = obj.Length.Value + 3.0
        self.doc.recompute()
        diagnostics = docmod.check_entities(self.doc)
        self.assertTrue(any("no longer matches the stored reference" in d for d in diagnostics), diagnostics)
        after = obj.Shape.Volume
        docmod.upsert(self.doc, source_sha256="again")
        self.assertEqual(obj.Shape.Volume, after)
        self.assertNotEqual(before, after)

    @classmethod
    def tearDownClass(cls):
        docmod.shutdown()

    def test_same_invariants_shifted_hole_requires_rebind(self):
        import Part

        def holed(x):
            face = Part.makePlane(20.0, 20.0)
            hole = Part.makeCylinder(2.0, 5.0, App.Vector(x, 10.0, -1.0))
            return face.cut(hole)

        reference_shape = holed(5.0)
        live_shape = holed(15.0)  # same area, bbox and face count, different geometry
        self.assertEqual(len(reference_shape.Faces), len(live_shape.Faces))
        self.assertAlmostEqual(reference_shape.Area, live_shape.Area, places=9)
        self.assertNotEqual(reference_shape.exportBrepToString(), live_shape.exportBrepToString())
        handle, name = tempfile.mkstemp(suffix=".brep")
        os.close(handle)
        reference = Path(name)
        self.addCleanup(lambda: reference.unlink(missing_ok=True))
        reference_shape.exportBrep(str(reference))
        obj = self.doc.addObject("Part::Feature", "Face_A")
        obj.Shape = live_shape
        self.doc.recompute()
        docmod.upsert(self.doc, source_step=self._source(),
                      entity_map=[{"kind": "face", "brep_id": 1, "object": obj.Name,
                                   "reference": reference.name, "placement": str(obj.Placement)}],
                      cache_dir=str(reference.parent))
        diagnostics = docmod.check_entities(self.doc)
        self.assertTrue(any("Rebind the entity" in d for d in diagnostics), diagnostics)

    def test_edge_association_uses_index_points(self):
        import Part

        points = [[0.0, 0.0, 0.0], [0.0, 5.0, 0.0], [0.0, 10.0, 0.0]]
        obj = self.doc.addObject("Part::Feature", "Boundary_7")
        obj.Shape = Part.makePolygon([App.Vector(*p) for p in points])
        self.doc.recompute()
        entries = docmod.entity_map_from_index(
            {"faces": [], "edges": [{"id": 7, "kind": "edge", "points": points}]},
            {("edge", 7): obj.Name})
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["points"], points)
        docmod.upsert(self.doc, project={"schema_version": 3}, source_step=self._source(), entity_map=entries)
        # Without a captured reference the comparison must block, not fake a match.
        self.assertTrue(any("no captured reference" in d for d in docmod.check_entities(self.doc)))
        handle, name = tempfile.mkstemp(suffix=".brep")
        os.close(handle)
        self.addCleanup(lambda: Path(name).unlink(missing_ok=True))
        docmod.upsert(self.doc, entity_map=entries, cache_dir=str(Path(name).parent))
        self.assertTrue(entries[0]["reference"].endswith(".brep"), entries[0])
        self.assertEqual(docmod.check_entities(self.doc), [])
        obj.Shape = Part.makePolygon([App.Vector(0.0, 0.0, 0.0), App.Vector(0.0, 5.0, 0.0),
                                      App.Vector(0.0, 9.0, 0.0)])
        self.doc.recompute()
        self.assertTrue(any("Rebind the entity" in d for d in docmod.check_entities(self.doc)))

    def test_upsert_prevalidates_and_does_not_partially_commit(self):
        owner = docmod.upsert(self.doc, project={"schema_version": 3, "marker": "kept"})
        before = owner.ProjectJSON
        with self.assertRaises(docmod.DocumentError):
            docmod.upsert(self.doc, project={"schema_version": 4, "marker": "new"},
                          entity_map=[{"kind": "face", "brep_id": "not-an-int", "object": "X"}])
        self.assertEqual(owner.ProjectJSON, before)

    def test_invalid_schema_version_leaves_stored_data_unchanged(self):
        owner = docmod.upsert(self.doc, project={"schema_version": 3, "marker": "kept"})
        before_project = owner.ProjectJSON
        before_schema = owner.SchemaVersion
        with self.assertRaises(docmod.DocumentError):
            docmod.upsert(self.doc, project={"schema_version": "not-an-int", "marker": "new"})
        self.assertEqual(owner.ProjectJSON, before_project)
        self.assertEqual(owner.SchemaVersion, before_schema)

    def test_two_documents_same_ids_keep_distinct_shapes(self):
        import Part

        first = self.doc
        other = App.newDocument("IbraDocOther")
        self.addCleanup(lambda: App.closeDocument(other.Name) if other.Name in App.listDocuments() else None)
        obj_a = first.addObject("Part::Feature", "IBRA_Face_2")
        obj_a.Shape = Part.makeBox(1, 1, 1)
        obj_b = other.addObject("Part::Feature", "IBRA_Face_2")
        obj_b.Shape = Part.makeBox(1, 1, 1)
        translated = obj_b.Shape.copy()
        translated.translate(App.Vector(50, 0, 0))
        obj_b.Shape = translated
        first.recompute()
        other.recompute()
        docmod.upsert(first, project={"schema_version": 3, "marker": "first"})
        docmod.upsert(other, project={"schema_version": 3, "marker": "second"})
        self.assertNotAlmostEqual(obj_a.Shape.BoundBox.XMin, obj_b.Shape.BoundBox.XMin)
        self.assertEqual(docmod.read(first).project["marker"], "first")
        self.assertEqual(docmod.read(other).project["marker"], "second")
        self.assertIsNot(docmod.find_owner(first), docmod.find_owner(other))

    def test_read_reports_malformed_entity_entries(self):
        owner = docmod.upsert(self.doc, project={"schema_version": 3})
        owner.EntityMap = json.dumps([{"kind": "nonsense", "brep_id": 1, "object": "A"},
                                      "not-an-object"])
        state = docmod.read(self.doc)
        self.assertTrue(any("unknown kind" in d for d in state.diagnostics), state.diagnostics)
        self.assertTrue(any("not an object" in d for d in state.diagnostics), state.diagnostics)

    def test_observer_flags_upsert_failure(self):
        owner = docmod.upsert(self.doc, project={"schema_version": 3, "marker": "stored"})
        hook = docmod.install_save_observer(
            self.doc, lambda d: {"entity_map": [{"kind": "face", "brep_id": "bad", "object": "A"}]},
            on_error="flag")
        hook.handle_save(self.doc)
        self.assertIn("brep_id", hook.last_error)
        self.assertIn("brep_id", owner.SyncError)
        self.assertEqual(json.loads(owner.ProjectJSON)["marker"], "stored")
        hook.close()

    def test_closing_document_releases_hook(self):
        hook = docmod.install_save_observer(self.doc, lambda d: None)
        self.assertIs(docmod.installed_hook(self.doc), hook)
        App.closeDocument(self.doc.Name)
        self.assertIsNone(docmod.installed_hook(self.doc))

    def test_moved_object_and_missing_reference_are_diagnosed(self):
        handle, name = tempfile.mkstemp(suffix=".brep")
        os.close(handle)
        reference = Path(name)
        obj = box(self.doc, "Face_A")
        self.doc.recompute()
        obj.Shape.exportBrep(str(reference))
        docmod.upsert(self.doc, source_step=self._source(),
                      entity_map=[{"kind": "face", "brep_id": 1, "object": obj.Name,
                                   "reference": reference.name, "placement": str(obj.Placement)}],
                      cache_dir=str(reference.parent))
        reference.unlink()
        obj.Placement.Base.y = 4.0
        diagnostics = docmod.check_entities(self.doc)
        self.assertTrue(any("missing" in d.lower() for d in diagnostics), diagnostics)
        self.assertTrue(any("moved" in d for d in diagnostics), diagnostics)

    def test_save_observer_flag_mode_records_error(self):
        owner = docmod.upsert(self.doc, project={"schema_version": 3, "marker": "stored"})

        def failing(doc):
            raise RuntimeError("provider exploded")

        hook = docmod.install_save_observer(self.doc, failing, on_error="flag")
        hook.handle_save(self.doc)  # flag mode: no exception, error visible on the owner
        self.assertIn("provider exploded", owner.SyncError)
        self.assertEqual(json.loads(owner.ProjectJSON)["marker"], "stored")
        hook.close()
        self.assertIsNone(docmod.installed_hook(self.doc))

    def test_commit_before_save_blocks_and_observer_exception_is_recorded(self):
        owner = docmod.upsert(self.doc, project={"schema_version": 3, "marker": "stored"})

        def failing(doc):
            raise RuntimeError("provider exploded")

        # The reliable block path: the caller's own stack.
        with self.assertRaises(docmod.SyncRefused):
            docmod.commit_before_save(self.doc, failing)
        self.assertIn("provider exploded", owner.SyncError)
        self.assertEqual(json.loads(owner.ProjectJSON)["marker"], "stored")

        # The observer cannot abort a native save in this build; record what happens
        # instead of asserting a block that FreeCAD does not provide.
        hook = docmod.install_save_observer(self.doc, failing, on_error="block")
        handle, name = tempfile.mkstemp(suffix=".FCStd")
        os.close(handle)
        target = Path(name)
        try:
            self.doc.saveAs(str(target))
        except Exception:  # noqa: BLE001
            pass
        self.assertIn("provider exploded", hook.last_error)
        self.assertEqual(json.loads(owner.ProjectJSON)["marker"], "stored")
        hook.close()

    def test_second_process_native_save_reopen(self):
        host = ROOT / "runtime/p0-3-review/document_native_host.py"
        backend = ROOT.parent / "STEP2IBRA/.venv/Scripts/python.exe"
        run = subprocess.run([str(backend if backend.is_file() else sys.executable), str(host)],
                             capture_output=True, text=True, timeout=200)
        self.assertEqual(run.returncode, 0, (run.stdout or "") + (run.stderr or ""))

    def test_real_fixture_gate_clean_passes_and_modified_face8_differs(self):
        """Gate: every mapped entity of the clean fixture compares equal, and the
        modified fixture differs only in face 8. Both sides are exported through the
        same native writer from the fixture's own EntityMap/CacheDir references."""
        fixture = ROOT / "runtime/p0-3-review/gui/p0-3-gui-45n11yol"
        if not fixture.is_dir():
            self.skipTest("P0-3 review fixture is absent")
        for fcstd, expected_different in (("p03_clean.FCStd", set()),
                                          ("p03_modified.FCStd", {"face 8"})):
            path = fixture / fcstd
            if not path.is_file():
                self.skipTest(f"{fcstd} is absent")
            doc = App.openDocument(str(path))
            try:
                state = docmod.read(doc)
                self.assertEqual(len(state.entity_map), 11,
                                 [entry.get("object") for entry in state.entity_map])
                different = []
                for entry in state.entity_map:
                    label = f"{entry.get('kind')} {entry.get('brep_id')}"
                    obj = doc.getObject(entry.get("object", ""))
                    reference, _problem = docmod._reference_shape(entry, state.cache_dir)
                    if obj is None or reference is None:
                        different.append(label)
                        continue
                    if docmod._shape_differences(obj.Shape, reference):
                        different.append(label)
                self.assertEqual(set(different), expected_different, different)
                compared = [d for d in docmod.check_entities(doc)
                            if "no longer matches the stored reference" in d]
                if expected_different:
                    self.assertTrue(compared, docmod.check_entities(doc))
                else:
                    self.assertEqual(compared, [])
            finally:
                App.closeDocument(doc.Name)

    def test_unsupported_reference_brep_reports_rebind_not_sampling(self):
        class _UnsupportedShape:
            def exportBrepToString(self):
                return "CASCADE Topology V3, (c) Open Cascade\nLocations 0\n"

        differences = docmod._shape_differences(_UnsupportedShape(), _UnsupportedShape())
        self.assertTrue(differences)
        self.assertIn("verified V1 comparison grammar", differences[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
