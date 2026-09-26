"""P0-3 rebinding gate tests (pure Python, backend interpreter).

They cover the fail-closed export gate and the explicit-rebind lifecycle without
FreeCAD: a pending binding must block, and resolving it must clear the block while
preserving the rest of the project.
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "freecad/Mod/KratosIBRA"))

from kratos_iga.project import new_project, validate  # noqa: E402


def index():
    faces = [{"id": 2, "degrees": [2, 2], "min_continuity": [99, 99],
              "control_point_counts": {"u": 3, "v": 3}, "knot_spans": {"u": 1, "v": 1}},
             {"id": 4, "degrees": [2, 2], "min_continuity": [99, 99],
              "control_point_counts": {"u": 3, "v": 3}, "knot_spans": {"u": 1, "v": 1}}]
    return {"source_sha256": "src", "geometry_sha256": "geo", "faces": faces,
            "edges": [{"id": 9, "kind": "coupling", "face_ids": [2, 4], "points": [[0, 0, 0], [0, 1, 0]],
                       "strong_local": None},
                      {"id": 11, "kind": "edge", "face_ids": [4], "points": [[20, 1, 0], [20, 0, 0]],
                       "strong_local": None}]}


def project():
    p = new_project()
    p.update({"source_step": "x.step", "source_sha256": "src", "geometry_sha256": "geo"})
    p["patches"] = [{"face_id": 2, "material_id": 1, "section_id": 1, "element": "Shell3pElement",
                     "degree_u": 3, "degree_v": 3, "insert_u": 4, "insert_v": 0, "quadrature": 0,
                     "active": True},
                    {"face_id": 4, "material_id": 1, "section_id": 1, "element": "Shell3pElement",
                     "degree_u": 3, "degree_v": 3, "insert_u": 4, "insert_v": 0, "quadrature": 0,
                     "active": True}]
    p["refinement_enabled"] = True
    p["output"] = {"vtk": False, "refinement": 4}
    p["groups"] = [{"id": 1, "name": "Rim", "kind": "edge", "members": [11], "origin": "user"}]
    p["supports"] = [{"name": "root", "target_kind": "face", "target_id": 2, "side": "u0",
                      "method": "strong", "value": [0.0, 0.0, 0.0], "clamp": True,
                      "interval": [0.0, "End"]}]
    p["loads"] = [{"name": "rim load", "group_id": 1, "variable": "LINE_LOAD",
                   "value": [0.0, 0.0, -0.001], "interval": [0.0, "End"]}]
    return p


class RebindingGateTests(unittest.TestCase):
    def test_clean_project_passes(self):
        self.assertEqual(validate(project(), index())[0], [])

    def test_pending_condition_blocks_export(self):
        p = project()
        p["supports"][0]["needs_rebind"] = "the stored geometry no longer matches"
        errors = validate(p, index())[0]
        self.assertTrue(any("pending rebind" in e and "root" in e for e in errors), errors)

    def test_pending_initial_and_load_block(self):
        p = project()
        p["loads"][0]["needs_rebind"] = "geometry changed"
        p["initial_conditions"] = [{"name": "d0", "target_kind": "face", "target_id": 2,
                                    "variable": "DISPLACEMENT", "value": [0.0, 0.0, 0.0],
                                    "needs_rebind": "geometry changed"}]
        errors = validate(p, index())[0]
        self.assertTrue(any("rim load" in e for e in errors), errors)
        self.assertTrue(any("d0" in e for e in errors), errors)

    def test_pending_group_blocks_even_when_members_still_exist(self):
        p = project()
        p["groups"][0]["needs_rebind"] = "geometry changed"
        errors = validate(p, index())[0]
        self.assertTrue(any("Group 1" in e and "pending rebind" in e for e in errors), errors)

    def test_explicit_rebind_clears_the_gate_and_preserves_everything_else(self):
        p = project()
        p["groups"][0]["needs_rebind"] = "geometry changed"
        p["supports"][0]["needs_rebind"] = "geometry changed"
        self.assertTrue(validate(p, index())[0])
        # Explicit rebind: new target chosen by the user, reason cleared.
        p["supports"][0].pop("needs_rebind")
        p["supports"][0].update({"target_kind": "face", "target_id": 4, "side": "u0"})
        p["groups"][0].pop("needs_rebind")
        p["groups"][0]["members"] = [11]
        self.assertEqual(validate(p, index())[0], [])
        self.assertEqual(p["loads"][0]["group_id"], 1)
        self.assertEqual(p["patches"][1]["active"], True)

    def test_same_id_alone_is_not_an_acceptance(self):
        # A coincidentally equal id must not clear a pending binding: only the
        # explicit rebind action removes the marker.
        p = project()
        p["loads"][0]["needs_rebind"] = "geometry changed"
        p["loads"][0]["group_id"] = 1
        self.assertTrue(validate(p, index())[0])

    def test_dropping_a_pending_binding_removes_it(self):
        p = project()
        p["loads"][0]["needs_rebind"] = "geometry changed"
        p["loads"] = [r for r in p["loads"] if not r.get("needs_rebind")]
        self.assertEqual(validate(p, index())[0], [])

    def test_changed_geometry_with_zero_conditions_still_blocks(self):
        p = project()
        p["supports"] = []
        p["loads"] = []
        p["groups"] = []
        self.assertEqual(validate(p, index())[0], [])
        p["geometry_needs_rebind"] = "the stored geometry no longer matches; rebind explicitly"
        errors = validate(p, index())[0]
        self.assertTrue(any("geometry changed" in e for e in errors), errors)

    def test_migration_keeps_old_projects_exportable(self):
        from kratos_iga.project import migrate_project
        legacy = {"schema_version": 2, "couplings": [], "patches": [{"face_id": 2, "material_id": 1,
                  "section_id": 1, "element": "IgaMembraneElement", "degree_u": 2, "degree_v": 2,
                  "insert_u": 0, "insert_v": 0, "quadrature": 0}],
                  "materials": [{"id": 1, "name": "S", "young": 1.0, "poisson": 0.3, "density": 1.0}],
                  "sections": [{"id": 1, "name": "S", "thickness": 0.1}], "supports": [], "loads": [],
                  "initial_conditions": [], "solver": {"type": "static", "analysis_type": "linear",
                  "start_time": 0.0, "end_time": 1.0, "time_step": 1.0},
                  "output": {"vtk": False, "refinement": 4}}
        migrated = migrate_project(copy.deepcopy(legacy))
        self.assertEqual(migrated["schema_version"], 3)
        self.assertEqual(migrated["groups"], [])
        self.assertTrue(migrated["patches"][0]["active"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
