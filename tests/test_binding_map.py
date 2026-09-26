"""P0-3 explicit rebind helper tests (pure stdlib, no FreeCAD/OCP/Kratos).

They exercise the physical contract of ``kratos_iga.bindings``: a renamed cache still
preserves the per-face material/section/active/refinement choices only through an
explicit map; equal ids are never identity proof; incomplete, wrong-kind,
non-boundary and colliding maps fail atomically; drops are reported, never silent;
interfaces are regenerated as pending candidates.
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "freecad/Mod/KratosIBRA"))

from kratos_iga.bindings import (RebindError, apply_rebind, mark_geometry_changed,  # noqa: E402
                                 mark_geometry_missing, parse_key, stage_rebind, target_key)
from kratos_iga.project import validate  # noqa: E402

MAPPING = {"face:2": "face:12", "face:4": "face:14", "face:6": "face:16",
           "edge:3": "edge:13", "edge:5": "edge:15"}


def face(ident, degrees=(2, 2)):
    return {"id": ident, "degrees": list(degrees), "min_continuity": [99, 99],
            "control_points": 9, "control_point_counts": {"u": 3, "v": 3, "total": 9},
            "knot_spans": {"u": 1, "v": 1}}


def edge(ident, face_ids, kind="edge"):
    return {"id": ident, "kind": kind, "face_ids": list(face_ids),
            "points": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
            "strong_local": [0, -1] if kind == "edge" else None, "diagnostics": None}


def old_index():
    return {"source_sha256": "OLD_SRC", "geometry_sha256": "OLD_GEO",
            "faces": [face(2), face(4), face(6)],
            "edges": [edge(3, [2]), edge(5, [4]), edge(9, [2, 4], kind="coupling")]}


def new_index():
    return {"source_sha256": "NEW_SRC", "geometry_sha256": "NEW_GEO",
            "faces": [face(12), face(14), face(16)],
            "edges": [edge(13, [12]), edge(15, [14]), edge(19, [12, 14], kind="coupling")]}


def same_id_index():
    """Same numeric ids, different cache hashes: still a different geometry."""
    return {"source_sha256": "NEW_SRC", "geometry_sha256": "NEW_GEO",
            "faces": [face(2), face(4), face(6)],
            "edges": [edge(3, [2]), edge(5, [4]), edge(9, [2, 4], kind="coupling")]}


def old_project():
    return {
        "schema_version": 3, "source_step": "old_snapshot.step", "source_sha256": "OLD_SRC",
        "profile": "", "units": "mm-N-s",
        "materials": [{"id": 1, "name": "Steel", "young": 210000.0, "poisson": 0.3,
                       "density": 7.85e-09},
                      {"id": 2, "name": "Alu", "young": 70000.0, "poisson": 0.33,
                       "density": 2.7e-09}],
        "sections": [{"id": 1, "name": "t=1", "thickness": 1.0},
                     {"id": 2, "name": "t=2.5", "thickness": 2.5}],
        "refinement_enabled": True,
        "patches": [
            {"face_id": 2, "material_id": 1, "section_id": 1, "element": "Shell3pElement",
             "active": True, "degree_u": 3, "degree_v": 2, "insert_u": 2, "insert_v": 1,
             "quadrature": 4},
            {"face_id": 4, "material_id": 2, "section_id": 2, "element": "Shell3pElement",
             "active": False, "degree_u": 2, "degree_v": 2, "insert_u": 0, "insert_v": 0,
             "quadrature": 0},
            {"face_id": 6, "material_id": 1, "section_id": 2, "element": "Shell3pElement",
             "active": True, "degree_u": 2, "degree_v": 2, "insert_u": 1, "insert_v": 1,
             "quadrature": 0}],
        "groups": [{"id": 1, "name": "Loaded edges", "kind": "edge", "members": [3, 5, 3]},
                   {"id": 2, "name": "Top faces", "kind": "face", "members": [2, 6]}],
        "supports": [{"name": "Clamp", "target_kind": "face", "target_id": 2, "side": "u0",
                      "method": "strong", "value": [0.0, 0.0, 0.0], "interval": [0.0, "End"],
                      "clamp": True}],
        "loads": [{"name": "Edge load", "target_kind": "edge", "target_id": 3,
                   "variable": "LINE_LOAD", "value": [0.0, 0.0, -1.0], "interval": [0.0, 1.0]},
                  {"name": "Group load", "group_id": 1, "variable": "LINE_LOAD",
                   "value": [0.0, 0.0, -0.5], "interval": [0.25, "End"]}],
        "initial_conditions": [{"name": "Pre", "target_kind": "face", "target_id": 4,
                                "variable": "DISPLACEMENT", "value": [0.0, 0.0, 0.0]}],
        "couplings": [{"edge_id": 9, "state": "confirmed", "origin": "user",
                       "penalty": 123.0, "rotation": True}],
        "solver": {"type": "static", "analysis_type": "linear", "start_time": 0.0,
                   "end_time": 1.0, "time_step": 1.0},
        "output": {"vtk": False, "refinement": 4},
    }


def patches_by_face(project):
    return {patch["face_id"]: patch for patch in project["patches"]}


class RebindPlanTests(unittest.TestCase):
    def test_plan_enumerates_every_binding_and_never_auto_accepts(self):
        project = old_project()
        plan = stage_rebind(project, old_index())
        self.assertEqual(plan["required_keys"],
                         ["edge:3", "edge:5", "face:2", "face:4", "face:6"])
        self.assertNotIn("mapping", plan)  # the plan proposes nothing
        self.assertIn("explicit mapping only", plan["identity_policy"])
        for patch in plan["patches"]:
            self.assertIn("preserved", patch)
        self.assertEqual(patches_by_face(project)[2]["material_id"], 1)

    def test_equal_ids_are_not_identity(self):
        project = old_project()
        plan = stage_rebind(project, same_id_index())
        self.assertEqual(plan["required_keys"],
                         ["edge:3", "edge:5", "face:2", "face:4", "face:6"])
        with self.assertRaises(RebindError):
            apply_rebind(project, old_index(), same_id_index(), {})

    def test_group_members_dedupe_and_map_multiple(self):
        plan = stage_rebind(old_project(), old_index())
        group = next(g for g in plan["groups"] if g["id"] == 1)
        self.assertEqual(group["members"], ["edge:3", "edge:5"])  # 3 appeared twice
        result = apply_rebind(old_project(), old_index(), new_index(), MAPPING)["project"]
        self.assertEqual(set(patches_by_face(result)), {12, 14, 16})
        groups = {g["id"]: g for g in result["groups"]}
        self.assertEqual(groups[1]["members"], [13, 15])
        self.assertEqual(groups[2]["members"], [12, 16])
        group_load = next(load for load in result["loads"] if load["name"] == "Group load")
        self.assertEqual(group_load["group_id"], 1)  # group identity stable, members remapped


class RebindApplyTests(unittest.TestCase):
    def test_renamed_cache_preserves_per_face_choices(self):
        project = old_project()
        old_units = copy.deepcopy(project["units"])
        old_materials = copy.deepcopy(project["materials"])
        old_sections = copy.deepcopy(project["sections"])
        old_solver = copy.deepcopy(project["solver"])
        old_interval = copy.deepcopy(project["loads"][0]["interval"])
        outcome = apply_rebind(project, old_index(), new_index(), MAPPING,
                               source_step="new_snapshot.step")
        result, summary = outcome["project"], outcome["summary"]
        self.assertEqual(result["source_step"], "new_snapshot.step")
        self.assertEqual(result["source_sha256"], "NEW_SRC")
        self.assertEqual(result["geometry_sha256"], "NEW_GEO")
        self.assertNotIn("geometry_needs_rebind", result)
        self.assertEqual(result["units"], old_units)
        self.assertEqual(result["materials"], old_materials)
        self.assertEqual(result["sections"], old_sections)
        self.assertEqual(result["solver"], old_solver)
        patches = patches_by_face(result)
        self.assertEqual(patches[12]["material_id"], 1)
        self.assertEqual(patches[12]["section_id"], 1)
        self.assertEqual(patches[12]["degree_u"], 3)
        self.assertEqual(patches[12]["insert_u"], 2)
        self.assertEqual(patches[12]["quadrature"], 4)
        self.assertEqual(patches[14]["material_id"], 2)
        self.assertEqual(patches[14]["section_id"], 2)
        self.assertFalse(patches[14]["active"])          # inactive choice preserved
        self.assertEqual(patches[16]["section_id"], 2)
        self.assertTrue(patches[16]["active"])
        support = result["supports"][0]
        self.assertEqual(support["target_id"], 12)
        self.assertEqual(support["side"], "u0")           # preserved; validated later
        self.assertTrue(support["clamp"])
        direct_load = next(load for load in result["loads"] if load["name"] == "Edge load")
        self.assertEqual(direct_load["target_id"], 13)
        self.assertEqual(direct_load["interval"], old_interval)   # values/intervals untouched
        initial = result["initial_conditions"][0]
        self.assertEqual(initial["target_id"], 14)
        for category in ("supports", "loads", "initial_conditions"):
            for record in result[category]:
                self.assertNotIn("needs_rebind", record)
        for group in result["groups"]:
            self.assertNotIn("needs_rebind", group)
        self.assertEqual(summary["new_patches"],
                         [{"face_id": 12, "source": "mapped from face:2"},
                          {"face_id": 14, "source": "mapped from face:4"},
                          {"face_id": 16, "source": "mapped from face:6"}])

    def test_new_face_without_a_source_gets_a_documented_default(self):
        project = old_project()
        outcome = apply_rebind(project, old_index(), new_index(),
                               {key: value for key, value in MAPPING.items()
                                if key in ("face:2", "face:4", "edge:3", "edge:5")},
                               drop_targets=["face:6"])
        result = outcome["project"]
        default = patches_by_face(result)[16]
        self.assertEqual(default["material_id"], 1)       # first project material
        self.assertEqual(default["section_id"], 1)
        self.assertEqual(default["element"], "Shell3pElement")
        self.assertEqual(default["degree_u"], 2)          # element minimum
        self.assertEqual(default["insert_u"], 1)
        self.assertTrue(default["active"])
        self.assertIn(16, outcome["summary"]["unmapped_new_faces"])

    def test_incomplete_mapping_is_atomic(self):
        project = old_project()
        index = old_index()
        before = copy.deepcopy(project)
        mapping = {key: value for key, value in MAPPING.items() if key != "edge:5"}
        with self.assertRaises(RebindError):
            apply_rebind(project, index, new_index(), mapping)
        self.assertEqual(project, before)
        with self.assertRaises(RebindError):
            apply_rebind(project, index, new_index(), dict(MAPPING, **{"face:99": "face:12"}))
        with self.assertRaises(RebindError):
            apply_rebind(project, index, new_index(), dict(MAPPING, **{"face:2": "face:99"}))
        self.assertEqual(project, before)

    def test_wrong_kind_and_nonboundary_rejected(self):
        project, index, new = old_project(), old_index(), new_index()
        with self.assertRaises(RebindError):
            apply_rebind(project, index, new, dict(MAPPING, **{"face:2": "edge:13"}))
        with self.assertRaises(RebindError):
            apply_rebind(project, index, new, dict(MAPPING, **{"edge:3": "edge:19"}))
        with self.assertRaises(RebindError):
            apply_rebind(project, index, new, dict(MAPPING, **{"edge:3": "edge:99"}))
        with self.assertRaises(RebindError):
            apply_rebind(project, index, new, dict(MAPPING, **{"edge:3": "face:12"}))

    def test_colliding_face_mapping_rejected(self):
        with self.assertRaises(RebindError):
            apply_rebind(old_project(), old_index(), new_index(),
                         dict(MAPPING, **{"face:4": "face:12"}))

    def test_explicit_drops_are_reported(self):
        project = old_project()
        outcome = apply_rebind(project, old_index(), new_index(),
                               {key: value for key, value in MAPPING.items()
                                if key not in ("edge:3", "edge:5", "face:6")},
                               drop_targets=["edge:3", "edge:5", "face:6"])
        summary, result = outcome["summary"], outcome["project"]
        self.assertEqual(sorted(summary["dropped_targets"]), ["edge:3", "edge:5", "face:6"])
        self.assertEqual({m["member"] for m in summary["dropped_group_members"]}, {3, 5, 6})
        self.assertEqual([g["group_id"] for g in summary["dropped_groups"]], [1])  # empty after drops
        self.assertEqual({c["name"] for c in summary["dropped_conditions"]},
                         {"Edge load", "Group load"})
        self.assertEqual([p["face_id"] for p in summary["dropped_patches"]], [6])
        dropped_patch = summary["dropped_patches"][0]
        self.assertEqual(dropped_patch["section_id"], 2)
        groups = [g["id"] for g in result["groups"]]
        self.assertNotIn(1, groups)                       # nothing silently kept
        self.assertEqual({patch["face_id"] for patch in result["patches"]}, {12, 14, 16})
        self.assertNotIn("Edge load", [load["name"] for load in result["loads"]])
        self.assertNotIn("Group load", [load["name"] for load in result["loads"]])
        self.assertEqual(result["supports"][0]["target_id"], 12)

    def test_unknown_drop_key_rejected(self):
        with self.assertRaises(RebindError):
            apply_rebind(old_project(), old_index(), new_index(), MAPPING,
                         drop_targets=["edge:99"])

    def test_pending_dangling_reference_requires_an_explicit_map(self):
        project = old_project()
        project["loads"].append({"name": "Stale load", "target_kind": "edge",
                                 "target_id": 7, "variable": "LINE_LOAD",
                                 "value": [0.0, 0.0, -1.0], "interval": [0.0, 1.0],
                                 "needs_rebind": "the converted geometry changed"})
        plan = stage_rebind(project, old_index())
        self.assertIn("edge:7", plan["required_keys"])
        with self.assertRaises(RebindError):
            apply_rebind(project, old_index(), new_index(), MAPPING)
        outcome = apply_rebind(project, old_index(), new_index(),
                               dict(MAPPING, **{"edge:7": "edge:13"}))
        stale = next(load for load in outcome["project"]["loads"] if load["name"] == "Stale load")
        self.assertEqual(stale["target_id"], 13)
        self.assertNotIn("needs_rebind", stale)

    def test_unexplained_dangling_reference_is_an_error(self):
        project = old_project()
        project["loads"].append({"name": "Stale load", "target_kind": "edge",
                                 "target_id": 8, "variable": "LINE_LOAD",
                                 "value": [0.0, 0.0, -1.0], "interval": [0.0, 1.0]})
        with self.assertRaises(RebindError):
            stage_rebind(project, old_index())

    def test_couplings_regenerate_pending_never_inherit(self):
        outcome = apply_rebind(old_project(), old_index(), new_index(), MAPPING)
        result, summary = outcome["project"], outcome["summary"]
        self.assertEqual(result["couplings"],
                         [{"edge_id": 19, "state": "pending", "origin": "geometry",
                           "penalty": 1e7, "rotation": False}])
        self.assertEqual(summary["regenerated_couplings"],
                         [{"edge_id": 9, "old_state": "confirmed", "old_origin": "user",
                           "new_state": "pending", "new_origin": "geometry"}])

    def test_inputs_are_never_mutated(self):
        project, index, new = old_project(), old_index(), new_index()
        mapping = dict(MAPPING)
        snapshots = [copy.deepcopy(project), copy.deepcopy(index), copy.deepcopy(new),
                     copy.deepcopy(mapping)]
        apply_rebind(project, index, new, mapping, source_step="snapshot.step")
        self.assertEqual([project, index, new, mapping], snapshots)


class MissingGeometryTests(unittest.TestCase):
    def test_mark_geometry_changed_is_fail_closed(self):
        project = old_project()
        before = copy.deepcopy(project)
        marked = mark_geometry_changed(project, "the converted geometry changed")
        self.assertEqual(project, before)                 # input untouched
        self.assertEqual(marked["geometry_needs_rebind"], "the converted geometry changed")
        for category in ("supports", "loads", "initial_conditions"):
            for record in marked[category]:
                self.assertEqual(record["needs_rebind"], "the converted geometry changed")
        for group in marked["groups"]:
            self.assertEqual(group["needs_rebind"], "the converted geometry changed")
        errors, _warnings = validate(marked, old_index())
        text = " ".join(errors)
        self.assertIn("pending rebind", text)
        self.assertIn("no binding was reviewed", text)

    def test_mark_geometry_missing_names_the_source(self):
        marked = mark_geometry_missing(old_project())
        self.assertIn("old_snapshot.step", marked["geometry_needs_rebind"])
        self.assertIn("missing", marked["geometry_needs_rebind"])


class MalformedProjectTests(unittest.TestCase):
    """Malformed old data must fail explicitly, never be dropped or collapsed."""

    def assert_stage_fails(self, mutate, fragment):
        project = old_project()
        mutate(project)
        before = copy.deepcopy(project)
        with self.assertRaises(RebindError) as caught:
            stage_rebind(project, old_index())
        self.assertIn(fragment, str(caught.exception))
        self.assertEqual(project, before)
        with self.assertRaises(RebindError):
            apply_rebind(project, old_index(), new_index(), MAPPING)
        self.assertEqual(project, before)

    def test_duplicate_group_ids_fail(self):
        self.assert_stage_fails(lambda p: p["groups"].append(copy.deepcopy(p["groups"][0])),
                                "duplicate group id")

    def test_group_with_boolean_id_fails(self):
        self.assert_stage_fails(lambda p: p["groups"][0].update(id=True), "invalid id")

    def test_group_members_container_and_items_fail(self):
        self.assert_stage_fails(lambda p: p["groups"][0].update(members="edge:3"),
                                "members must be a list")
        self.assert_stage_fails(lambda p: p["groups"][0]["members"].append(True),
                                "non-integer member")
        self.assert_stage_fails(lambda p: p["groups"][0]["members"].append(3.5),
                                "non-integer member")

    def test_empty_old_group_fails_with_a_repair_diagnostic(self):
        project = old_project()
        project["groups"][0]["members"] = []
        before = copy.deepcopy(project)
        with self.assertRaises(RebindError) as caught:
            stage_rebind(project, old_index())
        message = str(caught.exception)
        self.assertIn("has no members", message)
        self.assertIn("never dropped silently", message)
        self.assertNotIn("explicitly dropped", message)
        self.assertEqual(project, before)

    def test_duplicate_patch_face_fails(self):
        self.assert_stage_fails(lambda p: p["patches"].append(copy.deepcopy(p["patches"][0])),
                                "duplicate face_id")

    def test_patch_with_boolean_face_id_fails(self):
        self.assert_stage_fails(lambda p: p["patches"][0].update(face_id=True), "invalid face_id")

    def test_non_dict_items_and_non_list_containers_fail(self):
        self.assert_stage_fails(lambda p: p["loads"].append("oops"), "project.loads[2]")
        self.assert_stage_fails(lambda p: p["groups"].append(["face", 2]), "project.groups[2]")
        self.assert_stage_fails(lambda p: p.update(supports={"name": "Clamp"}),
                                "must be a list")

    def test_duplicate_index_ids_fail(self):
        index = old_index()
        index["faces"].append(copy.deepcopy(index["faces"][0]))
        with self.assertRaises(RebindError) as caught:
            stage_rebind(old_project(), index)
        self.assertIn("duplicate face id", str(caught.exception))
        index = old_index()
        index["edges"].append(copy.deepcopy(index["edges"][0]))
        with self.assertRaises(RebindError) as caught:
            stage_rebind(old_project(), index)
        self.assertIn("duplicate edge id", str(caught.exception))

    def test_malformed_old_data_is_atomic_for_every_input(self):
        project = old_project()
        project["patches"].append(copy.deepcopy(project["patches"][0]))
        project_before = copy.deepcopy(project)
        index, new, mapping = old_index(), new_index(), dict(MAPPING)
        others = [copy.deepcopy(index), copy.deepcopy(new), copy.deepcopy(mapping)]
        with self.assertRaises(RebindError):
            apply_rebind(project, index, new, mapping)
        self.assertEqual(project, project_before)
        self.assertEqual([index, new, mapping], others)

    def test_missing_group_members_are_reported_not_dropped(self):
        project = old_project()
        project["groups"].append({"id": 3, "name": "Ghost faces", "kind": "face",
                                  "members": [2, 99]})
        plan = stage_rebind(project, old_index())
        ghost = next(group for group in plan["groups"] if group["id"] == 3)
        self.assertEqual(ghost["missing"], [99])
        self.assertIn("face:99", plan["required_keys"])

    def test_pending_dangling_group_member_remains_remappable(self):
        project = old_project()
        project["groups"][0]["members"] = [3, 5, 7]
        project["groups"][0]["needs_rebind"] = "the converted geometry changed"
        plan = stage_rebind(project, old_index())
        self.assertIn("edge:7", plan["required_keys"])
        outcome = apply_rebind(project, old_index(), new_index(),
                               dict(MAPPING, **{"edge:7": "edge:13"}))
        group = next(g for g in outcome["project"]["groups"] if g["id"] == 1)
        self.assertEqual(group["members"], [13, 15])       # 3 and 7 both map onto 13, deduped
        self.assertNotIn("needs_rebind", group)


class KeyHelperTests(unittest.TestCase):
    def test_keys_are_kind_qualified_and_canonical(self):
        self.assertEqual(target_key("face", 2), "face:2")
        self.assertEqual(parse_key("edge:3"), ("edge", 3))
        for bad in ("2", "face:", "face:x", "face:02", "node:2", 2, "face:2:3"):
            with self.assertRaises(RebindError):
                parse_key(bad)
        with self.assertRaises(RebindError):
            target_key("coupling", 9)

    def test_whitespace_and_alias_keys_are_rejected_everywhere(self):
        for bad in ("face: 2", "face:2 ", " face:2", "face:+2", "face:2.0",
                    "face:-0", "edge: 3"):
            with self.assertRaises(RebindError):
                parse_key(bad)
        with self.assertRaises(RebindError):
            apply_rebind(old_project(), old_index(), new_index(),
                         dict(MAPPING, **{"face: 2": "face:12"}))
        with self.assertRaises(RebindError):
            apply_rebind(old_project(), old_index(), new_index(), MAPPING,
                         drop_targets=["edge: 5"])


if __name__ == "__main__":
    unittest.main()
