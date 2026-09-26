"""P0-3 controlled snapshot tests.

Pure entry-normalization tests run under any interpreter; the capture tests need the
native FreeCAD runtime (``FreeCAD`` + ``Part``) and are skipped elsewhere. No
converter, GUI or modeler is exercised: the snapshot only writes a STEP file, and the
tests read it back with ``Part.read`` to check global coordinates, face counts and
that source objects were not touched.
"""
from __future__ import annotations

import copy
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "freecad/Mod/KratosIBRA"))

from kratos_iga import snapshots  # noqa: E402
from kratos_iga.snapshots import SnapshotError, capture_face_snapshot, normalize_face_entries  # noqa: E402

try:
    import FreeCAD as App
    import Part
    HAVE_FREECAD = True
except ImportError:  # pragma: no cover - depends on the interpreter
    App = Part = None
    HAVE_FREECAD = False

VENV_PYTHON = ROOT.parent / "STEP2IBRA/.venv/Scripts/python.exe"


def test_root():
    root = Path(tempfile.gettempdir()) / "p0-3-snapshot-tests"
    root.mkdir(parents=True, exist_ok=True)
    return root


class EntryNormalizationTests(unittest.TestCase):
    def test_list_dict_and_tuple_forms_are_equivalent(self):
        expected = [{"kind": "face", "id": 2, "object": "FaceA"}]
        faces, display = normalize_face_entries([
            {"kind": "face", "id": 2, "object": "FaceA"},
            {"kind": "edge", "id": 3, "object": "EdgeDisplay"},
            {"kind": "coupling", "id": 9, "object": "CouplingDisplay"}])
        self.assertEqual(faces, expected)
        self.assertEqual([(entry["kind"], entry["id"]) for entry in display],
                         [("edge", 3), ("coupling", 9)])
        self.assertEqual(normalize_face_entries({"face:2": "FaceA"})[0], expected)
        self.assertEqual(normalize_face_entries({("face", 2): "FaceA"})[0], expected)
        self.assertEqual(normalize_face_entries({("face", 2): "FaceA",
                                                 ("coupling", 9): "Coup"})[0], expected)
        self.assertEqual(normalize_face_entries({"coupling:9": "Coup",
                                                 "face:2": "FaceA"})[0], expected)

    def test_panel_and_document_entity_map_forms_are_accepted(self):
        # document.entity_map_from_index() emits {"kind", "brep_id", "object", "reference", ...}
        document_entries = [
            {"kind": "face", "brep_id": 2, "object": "FaceA", "reference": "face_2.brep"},
            {"kind": "edge", "brep_id": 3, "object": "EdgeDisplay", "reference": "",
             "points": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]},
            {"kind": "coupling", "brep_id": 9, "object": "CouplingDisplay", "reference": ""}]
        faces, display = normalize_face_entries(document_entries)
        self.assertEqual([(entry["kind"], entry["id"]) for entry in faces], [("face", 2)])
        self.assertEqual([(entry["kind"], entry["id"]) for entry in display],
                         [("edge", 3), ("coupling", 9)])
        # panel.entities uses ('face'|kind, id) tuples, including ('coupling', id)
        panel = {("face", 2): "FaceA", ("edge", 3): "EdgeDisplay", ("coupling", 9): "Coup"}
        faces, display = normalize_face_entries(panel)
        self.assertEqual([entry["id"] for entry in faces], [2])
        self.assertEqual([entry["kind"] for entry in display], ["edge", "coupling"])
        # id and brep_id may both be present when they agree
        faces, _display = normalize_face_entries([{"kind": "face", "id": 2, "brep_id": 2,
                                                   "object": "FaceA"}])
        self.assertEqual(faces[0]["id"], 2)

    def test_invalid_entries_are_rejected(self):
        for bad, fragment in (
                (None, "must list"),
                ([], "no face entry"),
                ([{"kind": "solid", "id": 2, "object": "A"}], "unsupported entry kind"),
                ([{"kind": "face", "id": True, "object": "A"}], "non-boolean integer"),
                ([{"kind": "face", "id": 2, "object": " "}], "non-empty object name"),
                ([{"kind": "face", "id": 2, "object": None}], "non-empty object name"),
                ([{"kind": "face", "object": "A"}], "needs an id or brep_id"),
                ([{"kind": "face", "id": 2, "brep_id": 3, "object": "A"}], "conflicting id"),
                ([{"kind": "face", "id": 2, "object": "A"},
                  {"kind": "face", "id": 2, "object": "B"}], "duplicate face entries"),
                ([{"kind": "face", "id": 2, "object": "A"},
                  {"kind": "face", "id": 3, "object": "A"}], "more than one face entry"),
                ({"face: 2": "A"}, "not canonical"),
                ({"coupling: 9": "A", "face:2": "B"}, "not canonical"),
                ({"coupling:09": "A", "face:2": "B"}, "not canonical"),
                ({"face:2": ""}, "non-empty object name"),
                ({("face", 2, 3): "A"}, "must be (kind, id)"),
                ({("coupling", True): "A", ("face", 2): "B"}, "non-boolean integer")):
            with self.subTest(entry=bad):
                with self.assertRaises(SnapshotError) as caught:
                    normalize_face_entries(bad)
                self.assertIn(fragment, str(caught.exception))

    def test_module_import_is_stdlib_only(self):
        self.assertFalse(hasattr(snapshots, "Part"))
        self.assertFalse(hasattr(snapshots, "FreeCAD"))
        source = Path(snapshots.__file__).read_text(encoding="utf-8")
        for line in source.splitlines():
            if "import Part" in line or "from Part import" in line:
                self.assertTrue(line.startswith(" "), f"Part import is not function-local: {line!r}")
            if line.startswith(("import FreeCAD", "from FreeCAD")):
                self.fail(f"module-level FreeCAD import: {line!r}")
        if not HAVE_FREECAD:
            self.assertNotIn("Part", sys.modules)
            self.assertNotIn("FreeCAD", sys.modules)


@unittest.skipUnless(HAVE_FREECAD, "native FreeCAD runtime required")
class SnapshotCaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = test_root() / uuid.uuid4().hex
        self.directory.mkdir(parents=True)
        self.document_name = "snapshot-" + uuid.uuid4().hex[:8]
        self.doc = App.newDocument(self.document_name)

    def tearDown(self):
        try:
            App.closeDocument(self.document_name)
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(self.directory, ignore_errors=True)

    def add_plane(self, name, length=10.0, width=5.0, placement=(0.0, 0.0, 0.0), shape=None):
        obj = self.doc.addObject("Part::Feature", name)
        obj.Shape = shape if shape is not None else Part.makePlane(length, width)
        obj.Placement.Base = App.Vector(*placement)
        self.doc.recompute()
        return obj

    def destination(self, name="snapshot.step"):
        return self.directory / name

    def read_back(self, path):
        return Part.read(str(path))

    def bbox(self, shape):
        box = shape.BoundBox
        return tuple(round(value, 6) for value in
                     (box.XMin, box.XMax, box.YMin, box.YMax, box.ZMin, box.ZMax))

    @staticmethod
    def entry(ident, name):
        return {"kind": "face", "id": ident, "object": name}

    def test_global_coordinates_are_written_once(self):
        self.add_plane("FaceA", placement=(3.0, 4.0, 0.0))
        metadata = capture_face_snapshot(self.doc, [self.entry(2, "FaceA")], self.destination())
        self.assertEqual(metadata["face_total"], 1)
        back = self.read_back(self.destination())
        self.assertEqual(len(back.Faces), 1)
        self.assertEqual(self.bbox(back), (3.0, 13.0, 4.0, 9.0, 0.0, 0.0))

    def test_every_mapped_face_is_captured_active_or_not(self):
        self.add_plane("FaceA", 1.0, 1.0, placement=(0.0, 0.0, 0.0))
        self.add_plane("FaceB", 2.0, 2.0, placement=(5.0, 0.0, 0.0))
        entries = [self.entry(2, "FaceA"), self.entry(4, "FaceB")]
        metadata = capture_face_snapshot(self.doc, entries, self.destination())
        self.assertEqual(metadata["face_total"], 2)
        self.assertEqual([item["object"] for item in metadata["objects"]], ["FaceA", "FaceB"])
        back = self.read_back(self.destination())
        self.assertEqual(len(back.Faces), 2)
        self.assertEqual(self.bbox(back), (0.0, 7.0, 0.0, 2.0, 0.0, 0.0))

    def test_edge_and_coupling_display_are_excluded_from_the_actual_mapping(self):
        self.add_plane("FaceA", 1.0, 1.0, placement=(2.0, 0.0, 0.0))
        edge_display = self.doc.addObject("Part::Feature", "EdgeDisplay")
        edge_display.Shape = Part.makePolygon([App.Vector(0, 0, 0), App.Vector(1, 0, 0),
                                               App.Vector(0, 0, 0)])
        coupling_display = self.doc.addObject("Part::Feature", "CouplingDisplay")
        coupling_display.Shape = Part.makePolygon([App.Vector(0, 0, 0), App.Vector(0, 1, 0)])
        unrelated = self.add_plane("Unrelated", 3.0, 3.0, placement=(50.0, 0.0, 0.0))
        self.doc.recompute()
        # the actual panel.entities form, including ('coupling', id)
        panel = {("face", 2): "FaceA", ("edge", 3): "EdgeDisplay", ("coupling", 9): "CouplingDisplay"}
        metadata = capture_face_snapshot(self.doc, panel, self.destination())
        self.assertEqual(metadata["face_total"], 1)
        self.assertEqual(metadata["excluded_display_entries"],
                         [{"kind": "edge", "id": 3, "object": "EdgeDisplay"},
                          {"kind": "coupling", "id": 9, "object": "CouplingDisplay"}])
        back = self.read_back(self.destination())
        self.assertEqual(len(back.Faces), 1)
        self.assertEqual(self.bbox(back), (2.0, 3.0, 0.0, 1.0, 0.0, 0.0))
        self.assertEqual(self.bbox(unrelated.Shape), (50.0, 53.0, 0.0, 3.0, 0.0, 0.0))

    def test_document_entity_map_form_with_brep_id_and_reference_is_captured(self):
        self.add_plane("FaceA", 1.0, 2.0, placement=(1.0, 0.0, 0.0))
        document_entries = [
            {"kind": "face", "brep_id": 2, "object": "FaceA", "reference": "face_2.brep"},
            {"kind": "coupling", "brep_id": 9, "object": "MissingDisplay", "reference": ""}]
        metadata = capture_face_snapshot(self.doc, document_entries, self.destination())
        self.assertEqual(metadata["face_total"], 1)
        self.assertEqual(metadata["objects"][0]["entry"], "face:2")
        self.assertEqual(metadata["excluded_display_entries"][0]["kind"], "coupling")
        self.assertEqual(len(self.read_back(self.destination()).Faces), 1)

    def test_invalid_shape_is_rejected_before_export(self):
        collinear = Part.Face(Part.makePolygon([App.Vector(0, 0, 0), App.Vector(1, 0, 0),
                                                App.Vector(2, 0, 0), App.Vector(0, 0, 0)]))
        self.assertFalse(collinear.isValid())  # tiny real invalid-face fixture
        self.add_plane("Broken", shape=collinear)
        with self.assertRaises(SnapshotError) as caught:
            capture_face_snapshot(self.doc, [self.entry(2, "Broken")], self.destination())
        self.assertIn("invalid Shape", str(caught.exception))
        self.assertFalse(self.destination().exists())

    def test_linked_and_container_objects_are_rejected_as_unverified(self):
        target = self.add_plane("FaceA")
        link = self.doc.addObject("App::Link", "FaceLink")
        link.LinkedObject = target
        group = self.doc.addObject("App::Part", "Group")
        self.doc.recompute()
        for name in ("FaceLink", "Group"):
            with self.subTest(obj=name):
                with self.assertRaises(SnapshotError) as caught:
                    capture_face_snapshot(self.doc, [self.entry(2, name)], self.destination())
                self.assertIn("only top-level Part::Feature", str(caught.exception))

    def test_face_nested_in_transformed_app_part_is_rejected(self):
        face = self.add_plane("FaceA", 1.0, 1.0)
        container = self.doc.addObject("App::Part", "Shifted")
        container.Placement.Base = App.Vector(10.0, 0.0, 0.0)
        container.addObject(face)
        self.doc.recompute()
        self.assertTrue(face.InList)
        with self.assertRaises(SnapshotError) as caught:
            capture_face_snapshot(self.doc, [self.entry(2, "FaceA")], self.destination())
        message = str(caught.exception)
        self.assertIn("transforming container", message)
        self.assertIn("Shifted", message)
        self.assertFalse(self.destination().exists())

    def test_group_under_transformed_part_is_rejected_recursively(self):
        face = self.add_plane("FaceA", 1.0, 1.0)
        group = self.doc.addObject("App::DocumentObjectGroup", "IBRA_Geometry")
        group.addObject(face)
        container = self.doc.addObject("App::Part", "Moved")
        container.Placement.Base = App.Vector(0.0, 7.0, 0.0)
        container.addObject(group)
        self.doc.recompute()
        with self.assertRaises(SnapshotError) as caught:
            capture_face_snapshot(self.doc, [self.entry(2, "FaceA")], self.destination())
        self.assertIn("Moved", str(caught.exception))

    def test_ordinary_document_group_and_identity_container_are_accepted(self):
        grouped = self.add_plane("GroupedFace", 1.0, 1.0, placement=(2.0, 0.0, 0.0))
        group = self.doc.addObject("App::DocumentObjectGroup", "IBRA_Geometry")
        group.addObject(grouped)
        identity_part = self.doc.addObject("App::Part", "IdentityContainer")
        identity_part.addObject(self.add_plane("PartFace", 1.0, 1.0, placement=(4.0, 0.0, 0.0)))
        self.doc.recompute()
        metadata = capture_face_snapshot(
            self.doc, {("face", 2): "GroupedFace", ("face", 4): "PartFace"}, self.destination())
        self.assertEqual(metadata["face_total"], 2)
        self.assertEqual(self.bbox(self.read_back(self.destination())),
                         (2.0, 5.0, 0.0, 1.0, 0.0, 0.0))

    def test_edited_face_split_into_several_faces_is_captured_whole(self):
        split = Part.makeCompound([Part.makePlane(1.0, 1.0), Part.makePlane(1.0, 1.0)])
        self.add_plane("Split", shape=split)
        metadata = capture_face_snapshot(self.doc, [self.entry(2, "Split")], self.destination())
        self.assertEqual(metadata["objects"][0]["shape_type"], "Compound")
        self.assertEqual(metadata["face_total"], 2)
        self.assertEqual(len(self.read_back(self.destination()).Faces), 2)

    def test_missing_object_rejected_without_output(self):
        self.add_plane("FaceA")
        with self.assertRaises(SnapshotError) as caught:
            capture_face_snapshot(self.doc, [self.entry(2, "Absent")], self.destination())
        self.assertIn("Absent", str(caught.exception))
        self.assertFalse(self.destination().exists())
        self.assertEqual([p.name for p in self.directory.iterdir()], [])

    def test_null_and_non_face_shapes_are_rejected(self):
        self.add_plane("FaceA")
        self.add_plane("Solid", shape=Part.makeBox(1.0, 1.0, 1.0))
        self.add_plane("Wire", shape=Part.makePolygon(
            [App.Vector(0, 0, 0), App.Vector(1, 0, 0), App.Vector(0, 0, 0)]))
        self.add_plane("Mixed", shape=Part.makeCompound(
            [Part.makePlane(1.0, 1.0), Part.makeBox(1.0, 1.0, 1.0)]))
        self.doc.addObject("App::FeaturePython", "NoShape")
        for name, fragment in (("Solid", "is a Solid"), ("Wire", "is a Wire"),
                               ("Mixed", "compound containing"),
                               ("NoShape", "only top-level Part::Feature")):
            with self.subTest(obj=name):
                with self.assertRaises(SnapshotError) as caught:
                    capture_face_snapshot(self.doc, [self.entry(2, name)], self.destination())
                self.assertIn(fragment, str(caught.exception))
                self.assertFalse(self.destination().exists())

    def test_destination_created_during_export_is_not_overwritten(self):
        self.add_plane("FaceA")
        destination = self.destination()
        original_export = Part.export

        def racing_export(objects, path):
            original_export(objects, path)              # real STEP goes to the temp sibling
            destination.write_text("intruder", encoding="utf-8")   # destination appears now

        Part.export = racing_export
        try:
            with self.assertRaises(SnapshotError) as caught:
                capture_face_snapshot(self.doc, [self.entry(2, "FaceA")], destination)
        finally:
            Part.export = original_export
        self.assertIn("appeared while exporting", str(caught.exception))
        self.assertEqual(destination.read_text(encoding="utf-8"), "intruder")
        leftovers = [path.name for path in self.directory.iterdir()
                     if path.name.startswith(".ibra-snapshot-")]
        self.assertEqual(leftovers, [])

    def test_existing_destination_is_never_overwritten(self):
        self.add_plane("FaceA")
        destination = self.destination()
        destination.write_text("source payload", encoding="utf-8")
        with self.assertRaises(SnapshotError) as caught:
            capture_face_snapshot(self.doc, [self.entry(2, "FaceA")], destination)
        self.assertIn("already exists", str(caught.exception))
        self.assertEqual(destination.read_text(encoding="utf-8"), "source payload")

    def test_source_paths_are_protected(self):
        self.add_plane("FaceA")
        source = self.destination("source.step")
        source.write_text("original source", encoding="utf-8")
        with self.assertRaises(SnapshotError) as caught:
            capture_face_snapshot(self.doc, [self.entry(2, "FaceA")], source,
                                  source_paths=[source])
        self.assertIn("already exists", str(caught.exception))
        self.assertEqual(source.read_text(encoding="utf-8"), "original source")
        fresh = self.destination("fresh.step")
        capture_face_snapshot(self.doc, [self.entry(2, "FaceA")], fresh,
                              source_paths=[source, fresh.with_name("elsewhere.step")])
        self.assertTrue(fresh.is_file())
        self.assertEqual(source.read_text(encoding="utf-8"), "original source")

    def test_source_objects_and_document_are_unchanged(self):
        face_a = self.add_plane("FaceA", placement=(3.0, 4.0, 0.0))
        face_b = self.add_plane("FaceB", 2.0, 2.0, placement=(-1.0, 0.0, 0.0))

        def fingerprint(obj):
            return (obj.Shape.ShapeType, len(obj.Shape.Faces), self.bbox(obj.Shape),
                    tuple(obj.Placement.Base), tuple(obj.Placement.Rotation.Q))

        before = {name: fingerprint(self.doc.getObject(name)) for name in ("FaceA", "FaceB")}
        object_count = len(self.doc.Objects)
        capture_face_snapshot(self.doc, [self.entry(2, "FaceA"), self.entry(4, "FaceB")],
                              self.destination())
        after = {name: fingerprint(self.doc.getObject(name)) for name in ("FaceA", "FaceB")}
        self.assertEqual(after, before)
        self.assertEqual(len(self.doc.Objects), object_count)
        self.assertEqual(self.bbox(face_a.Shape), (3.0, 13.0, 4.0, 9.0, 0.0, 0.0))
        self.assertEqual(tuple(face_b.Placement.Base), (-1.0, 0.0, 0.0))

    def test_atomic_publish_leaves_no_temporary_file(self):
        self.add_plane("FaceA")
        capture_face_snapshot(self.doc, [self.entry(2, "FaceA")], self.destination())
        names = sorted(path.name for path in self.directory.iterdir())
        self.assertEqual(names, ["snapshot.step"])
        self.assertEqual([name for name in names if name.startswith(".ibra-snapshot-")], [])


if __name__ == "__main__":
    unittest.main()
