"""P0-3 binding/export integration checks on a real controlled STEP snapshot.

Owned by the independent verification task; adds no production code.

Strategy: the same two-face strip is written twice with a **different split
location** (12 mm vs 14 mm) and converted by the real OCP pipeline, so the new cache
describes genuinely different geometry while keeping the same id space. The old
project is marked "geometry changed", its export is blocked, and it is only unblocked
by an explicit, complete remap built from `bindings.stage_rebind` output.

Assertions are about *exported semantics* (validate errors, bundle files), never about
mirroring the remap implementation:

* equal ids plus changed geometry stay blocked;
* an incomplete mapping fails atomically and changes nothing;
* a complete explicit mapping preserves units / materials / sections / `active` /
  degrees / refinement / intervals / grouped targets;
* regenerated couplings stay `pending` and are not exported;
* the exported geometry carries only the NEW coordinates, no stale cache.

The real Kratos initialization check lives in ``runtime/p0-3-review/run_rebind_init.py``
and is handed to the host (real OCP conversion, then Kratos in a separate process).
"""
from __future__ import annotations

import copy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"tests"))
sys.path.insert(0, str(ROOT/"freecad/Mod/KratosIBRA"))
sys.path.insert(0, str(ROOT/"freecad/Mod/KratosIBRA/backend"))

try:
    from cad_pipeline.api import convert_step  # noqa: F401  (probe import)
except Exception as exc:  # noqa: BLE001
    raise unittest.SkipTest(f"OCP geometry backend unavailable: {exc}")

from kratos_iga.bindings import (RebindError, apply_rebind, mark_geometry_changed,
                                 stage_rebind, target_key)
from kratos_iga.bundle import make_bundle
from kratos_iga.project import new_project, populate, validate
from kratos_iga.worker import build_index

STRIP_WIDTH = 1.0
STRIP_LENGTH = 20.0
LOAD_Z = -0.001


def rect_face(x0, x1):
    from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,
                                    BRepBuilderAPI_MakeWire)
    from OCP.gp import gp_Pnt
    points = [gp_Pnt(x0, 0.0, 0.0), gp_Pnt(x1, 0.0, 0.0),
              gp_Pnt(x1, STRIP_WIDTH, 0.0), gp_Pnt(x0, STRIP_WIDTH, 0.0)]
    edges = [BRepBuilderAPI_MakeEdge(points[i], points[(i + 1) % 4]).Edge() for i in range(4)]
    wire = BRepBuilderAPI_MakeWire()
    for edge in edges:
        wire.Add(edge)
    return BRepBuilderAPI_MakeFace(wire.Wire()).Face()


def write_split_strip(path: Path, split_at: float) -> Path:
    """Two coplanar, edge-adjacent faces meeting at ``split_at`` along x."""
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    writer = STEPControl_Writer()
    writer.Transfer(rect_face(0.0, split_at), STEPControl_AsIs)
    writer.Transfer(rect_face(split_at, STRIP_LENGTH), STEPControl_AsIs)
    writer.Write(str(path))
    return Path(path)


def control_polygon_length(edge) -> float:
    points = [control[1][:3] for control in edge["3d_curve"]["control_points"]]
    return sum(sum((b[i] - a[i]) ** 2 for i in range(3)) ** 0.5
               for a, b in zip(points, points[1:]))


def boundary_edges(cad):
    return {edge["brep_id"]: edge for brep in cad["breps"] for edge in brep["edges"]}


def face_x_extent(cad, face_id):
    face = next(f for brep in cad["breps"] for f in brep["faces"] if f["brep_id"] == face_id)
    xs = [control[1][0] for control in face["surface"]["control_points"]]
    return min(xs), max(xs)


def positional_mapping(plan, new_face_ids, new_boundary_ids):
    """An explicit positional selection; no identity claim is made anywhere."""
    mapping = {}
    old_faces = sorted(entry["key"] for entry in plan["targets"] if entry["kind"] == "face")
    new_faces = sorted(new_face_ids)
    assert len(old_faces) == len(new_faces), (old_faces, new_faces)
    for key, new_id in zip(old_faces, new_faces):
        mapping[key] = target_key("face", new_id)
    old_edges = sorted(entry["key"] for entry in plan["targets"] if entry["kind"] == "edge")
    new_edges = sorted(new_boundary_ids)
    assert len(old_edges) == len(new_edges), (old_edges, new_edges)
    for key, new_id in zip(old_edges, new_edges):
        mapping[key] = target_key("edge", new_id)
    return mapping


class ControlledSnapshotTestCase(unittest.TestCase):
    """Real conversion of the OLD (12 mm split) and NEW (14 mm split) caches."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="p0-3-bind-export-"))
        cls.old_step = write_split_strip(cls.root/"old.step", 12.0)
        cls.new_step = write_split_strip(cls.root/"new.step", 14.0)
        cls.old_cad, cls.old_index = cls._convert(cls.old_step, cls.root/"old-index")
        cls.new_cad, cls.new_index = cls._convert(cls.new_step, cls.root/"new-index")
        cls.old_face_ids = sorted(f["id"] for f in cls.old_index["faces"])
        cls.new_face_ids = sorted(f["id"] for f in cls.new_index["faces"])
        cls.old_boundary_ids = sorted(e["id"] for e in cls.old_index["edges"]
                                      if e["kind"] == "edge")
        cls.new_boundary_ids = sorted(e["id"] for e in cls.new_index["edges"]
                                      if e["kind"] == "edge")
        cls.old_project = cls._old_project()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    @staticmethod
    def _convert(step, folder):
        folder = Path(folder)
        # build_index writes the per-face OCCT BREP files into this folder.
        folder.mkdir(parents=True, exist_ok=True)
        cad, report = convert_step(step)
        return cad, build_index(cad, report, folder)

    @classmethod
    def _old_project(cls):
        """A small two-patch shell project with material/section/refinement/load group."""
        project = new_project()
        project["source_step"] = str(cls.old_step)
        project["source_sha256"] = cls.old_index["source_sha256"]
        project["geometry_sha256"] = cls.old_index["geometry_sha256"]
        project["units"] = "mm-N-s"
        project["refinement_enabled"] = True
        project["materials"] = [{"id": 1, "name": "Steel", "young": 200000.0, "poisson": 0.0,
                                 "density": 7.85e-9}]
        project["sections"] = [{"id": 1, "name": "t=0.1", "thickness": 0.1}]
        populate(project, cls.old_index)
        for patch in project["patches"]:
            patch.update({"element": "IgaMembraneElement", "degree_u": 3, "degree_v": 1,
                          "insert_u": 8, "insert_v": 0, "quadrature": 2})
        # One patch keeps an individual degree and refinement so per-face preservation
        # is observable after the remap.
        project["patches"][-1].update({"degree_u": 2, "insert_u": 3})
        project["groups"] = [{"id": 1, "name": "Rim", "kind": "edge",
                              "members": list(cls.old_boundary_ids), "origin": "user"}]
        project["loads"] = [{"name": "Rim load", "group_id": 1, "variable": "LINE_LOAD",
                             "value": [0.0, 0.0, LOAD_Z], "interval": [0.0, 0.6]}]
        project["couplings"] = [{"edge_id": edge["id"], "state": "confirmed", "origin": "user",
                                 "penalty": 1e7, "rotation": False}
                                for edge in cls.old_index["edges"] if edge["kind"] == "coupling"]
        return project

    def blocked_project(self):
        return mark_geometry_changed(self.old_project, "controlled snapshot 12mm -> 14mm")

    def load_units(self, bundle):
        return [entry for entry in bundle["physics.iga.json"]["element_condition_list"]
                if entry["iga_model_part"] == "Load_1"]

    def reference_load_length(self, cad, boundary_ids):
        """Exact summed length of the boundary edges the grouped load acts on.

        Only one-sided boundary curves are counted: a derived coupling curve sits on
        the same geometry but is a distinct entity the load does not target.
        """
        edges = boundary_edges(cad)
        return sum(control_polygon_length(edges[edge_id]) for edge_id in sorted(boundary_ids))


class BlockedGateTests(ControlledSnapshotTestCase):
    """Equal ids plus changed geometry must not export the old cache."""

    def test_marked_project_reports_export_errors(self):
        project = self.blocked_project()
        errors, _ = validate(project, self.old_index)
        self.assertTrue(any("no binding was reviewed" in error for error in errors), errors)
        self.assertTrue(any("pending rebind" in error for error in errors), errors)

    def test_bundle_refuses_the_marked_project(self):
        project = self.blocked_project()
        with self.assertRaises(ValueError) as context:
            make_bundle(project, self.old_cad, self.old_index)
        self.assertIn("no binding was reviewed", str(context.exception))

    def test_same_ids_do_not_satisfy_the_gate(self):
        # The new cache keeps the same face/edge id space, which is exactly the trap:
        # feeding the old project the new cache must still fail on the hash gate.
        errors, _ = validate(self.old_project, self.new_index)
        self.assertTrue(any("checksum" in error or "Converted geometry has changed" in error
                            for error in errors), errors)
        with self.assertRaises(ValueError):
            make_bundle(self.old_project, self.new_cad, self.new_index)


class AtomicRemapTests(ControlledSnapshotTestCase):
    """An incomplete or invalid mapping must change nothing."""

    def test_incomplete_mapping_is_atomic(self):
        project = self.blocked_project()
        plan = stage_rebind(project, self.old_index)
        mapping = positional_mapping(plan, self.new_face_ids, self.new_boundary_ids)
        mapping.pop(sorted(mapping)[0])
        before_project = copy.deepcopy(project)
        before_index = copy.deepcopy(self.new_index)
        with self.assertRaises(RebindError) as context:
            apply_rebind(project, self.old_index, self.new_index, mapping)
        self.assertIn("Incomplete remap", str(context.exception))
        self.assertEqual(project, before_project)
        self.assertEqual(self.new_index, before_index)

    def test_plan_requires_every_old_binding(self):
        plan = stage_rebind(self.blocked_project(), self.old_index)
        for face_id in self.old_face_ids:
            self.assertIn(target_key("face", face_id), plan["required_keys"])
        for edge_id in self.old_boundary_ids:
            self.assertIn(target_key("edge", edge_id), plan["required_keys"])
        self.assertTrue(all(entry["referenced_by"] for entry in plan["targets"]))
        # Confirmed interfaces are reported as "regenerate", never inherited silently.
        self.assertTrue(plan["couplings"])
        self.assertTrue(all(entry["decision"] == "regenerate_pending"
                            for entry in plan["couplings"]))


class CompleteRemapExportTests(ControlledSnapshotTestCase):
    """One complete explicit remap, checked through the exported bundle."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project = mark_geometry_changed(cls.old_project, "controlled snapshot 12mm -> 14mm")
        cls.plan = stage_rebind(cls.project, cls.old_index)
        cls.mapping = positional_mapping(cls.plan, cls.new_face_ids, cls.new_boundary_ids)
        cls.result = apply_rebind(cls.project, cls.old_index, cls.new_index, cls.mapping,
                                  source_step=str(cls.new_step))
        cls.remapped = cls.result["project"]
        cls.bundle = make_bundle(cls.remapped, cls.new_cad, cls.new_index)

    def test_remap_makes_the_project_exportable(self):
        errors, _ = validate(self.remapped, self.new_index)
        self.assertEqual(errors, [])
        self.assertEqual(self.remapped["source_sha256"], self.new_index["source_sha256"])
        self.assertEqual(self.remapped["geometry_sha256"], self.new_index["geometry_sha256"])
        self.assertNotIn("geometry_needs_rebind", self.remapped)
        for category in ("supports", "loads", "initial_conditions"):
            for item in self.remapped[category]:
                self.assertNotIn("needs_rebind", item)
        for group in self.remapped["groups"]:
            self.assertNotIn("needs_rebind", group)

    def test_units_materials_sections_solver_unchanged(self):
        self.assertEqual(self.remapped["units"], self.old_project["units"])
        self.assertEqual(self.remapped["materials"], self.old_project["materials"])
        self.assertEqual(self.remapped["sections"], self.old_project["sections"])
        self.assertEqual(self.remapped["solver"], self.old_project["solver"])
        self.assertEqual(self.remapped["refinement_enabled"],
                         self.old_project["refinement_enabled"])

    def test_patch_choices_and_intervals_survive(self):
        old_by_face = {patch["face_id"]: patch for patch in self.old_project["patches"]}
        new_by_face = {patch["face_id"]: patch for patch in self.remapped["patches"]}
        for old_key, new_key in self.result["summary"]["mapping"].items():
            if not old_key.startswith("face:"):
                continue
            old_patch = old_by_face[int(old_key.split(":")[1])]
            new_patch = new_by_face[int(new_key.split(":")[1])]
            for field in ("material_id", "section_id", "element", "active",
                          "degree_u", "degree_v", "insert_u", "insert_v", "quadrature"):
                self.assertEqual(new_patch[field], old_patch[field],
                                 f"{field} not preserved for {old_key} -> {new_key}")
        self.assertEqual(self.remapped["loads"][0]["interval"], [0.0, 0.6])
        self.assertEqual(self.remapped["loads"][0]["value"], [0.0, 0.0, LOAD_Z])
        self.assertEqual(len(self.remapped["patches"]), len(self.new_face_ids))

    def test_grouped_load_targets_the_new_geometry(self):
        units = self.load_units(self.bundle)
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["geometry_type"], "SurfaceEdge")
        self.assertEqual(units[0]["brep_ids"], sorted(self.new_boundary_ids))

    def test_pending_interfaces_are_not_emitted(self):
        physics = self.bundle["physics.iga.json"]["element_condition_list"]
        self.assertTrue(physics)
        couplings = [entry for entry in physics if entry["iga_model_part"].startswith("Coupling_")]
        self.assertEqual(couplings, [], "a regenerated pending interface must not be exported")
        self.assertTrue({coupling["state"] for coupling in self.remapped["couplings"]}
                        <= {"pending"})

    def test_active_subset_still_governs_export(self):
        surfaces = [entry["brep_ids"] for entry in
                    self.bundle["physics.iga.json"]["element_condition_list"]
                    if entry["geometry_type"] == "GeometrySurface"]
        active_faces = sorted(patch["face_id"] for patch in self.remapped["patches"]
                              if patch.get("active", True))
        self.assertEqual(surfaces, [[face_id] for face_id in active_faces])
        # Every new face gets exactly one patch and the domain covers them here.
        self.assertEqual(len(active_faces), len(self.new_face_ids))
        # Deactivating a patch must keep governing the export: either its elements are
        # gone from the surface list, or the export is refused because the grouped load
        # no longer has an active domain on that side. Both outcomes are the gate.
        refused, excluded = 0, []
        for patch in self.remapped["patches"]:
            remapped = copy.deepcopy(self.remapped)
            next(p for p in remapped["patches"]
                 if p["face_id"] == patch["face_id"])["active"] = False
            try:
                trimmed = make_bundle(remapped, self.new_cad, self.new_index)
            except ValueError as error:
                self.assertIn("outside the active analysis domain", str(error))
                refused += 1
                continue
            surfaces = [entry["brep_ids"] for entry in
                        trimmed["physics.iga.json"]["element_condition_list"]
                        if entry["geometry_type"] == "GeometrySurface"]
            self.assertNotIn([patch["face_id"]], surfaces)
            excluded.append(patch["face_id"])
        self.assertTrue(refused or excluded)

    def test_exported_geometry_carries_only_new_coordinates(self):
        # The old cache splits at 12 mm, the new cache at 14 mm; mapped targets were
        # selected positionally, so any inherited old coordinate would be a defect.
        def internal_splits(cad, face_ids):
            spans = [face_x_extent(cad, face_id) for face_id in face_ids]
            shared = set()
            for span in spans:
                for value in span:
                    if all(round(value, 9) in {round(other, 9) for other in other_span}
                           for other_span in spans):
                        shared.add(round(value, 9))
            return sorted(shared)

        self.assertIn(12.0, internal_splits(self.old_cad, self.old_face_ids))
        self.assertIn(14.0, internal_splits(self.new_cad, self.new_face_ids))
        geometry = self.bundle["geometry.cad.json"]
        self.assertEqual(geometry, self.new_cad)
        self.assertNotEqual(geometry, self.old_cad)
        self.assertNotIn(12.0, internal_splits(geometry, self.new_face_ids))
        spans = [face_x_extent(geometry, face_id) for face_id in self.new_face_ids]
        touching = [span for span in spans
                    if abs(span[1] - 14.0) < 1e-9 or abs(span[0] - 14.0) < 1e-9]
        self.assertEqual(len(touching), 2, f"the 14 mm split must be shared by both faces: {spans}")
        for span in spans:
            self.assertGreaterEqual(round(span[0], 9), 0.0)
            self.assertLessEqual(round(span[1], 9), STRIP_LENGTH)

    def test_load_edge_reference_length_for_force_check(self):
        expected = self.reference_load_length(self.new_cad, self.new_boundary_ids)
        self.assertAlmostEqual(expected, 44.0, places=9)
        edges = boundary_edges(self.new_cad)
        self.assertEqual(len(self.new_boundary_ids), 8)
        for edge_id in self.new_boundary_ids:
            self.assertGreater(control_polygon_length(edges[edge_id]), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
