"""P0-3 contract tests: schema-3 migration, active domain, named groups, conflicts.

Pure-Python tests. ``ExportDomainTests`` uses the real converter output for the
distributed cantilever fixture (OCP present in the backend venv) and asserts the
Kratos *file-level* semantics of the active subset and group expansion. It does not
start a solver; the real Kratos initialization check is a separate bounded script.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"freecad/Mod/KratosIBRA"))
sys.path.insert(0, str(ROOT/"freecad/Mod/KratosIBRA/backend"))

from kratos_iga.bundle import make_bundle
from kratos_iga.groups import (build_region, expand, final_grid, regions_overlap,
                               strong_spec, target_locations)
from kratos_iga.project import (SCHEMA, convert_project_units, migrate_project, new_project,
                                read_json, read_project, validate)

EXAMPLE = ROOT/"freecad/Mod/KratosIBRA/examples/cantilever/cantilever.ibra-project.json"


def synthetic_index(shas=("src", "geo")):
    faces = {}
    for fid in (1, 2):
        faces[fid] = {"id": fid, "degrees": [2, 2], "min_continuity": [2, 2],
                      "control_points": 12, "control_point_counts": {"u": 4, "v": 3, "total": 12},
                      "knot_spans": {"u": 1, "v": 2}}
    edges = {}
    for eid, face_ids, strong in ((4, [1], [0, -1]), (5, [2], [0, -1]), (6, [2], [1, -1]),
                                  (7, [1, 2], None)):
        edges[eid] = {"id": eid, "kind": "coupling" if len(face_ids) == 2 else "edge",
                      "face_ids": face_ids, "strong_local": strong}
    return {"source_sha256": shas[0], "geometry_sha256": shas[1],
            "faces": list(faces.values()), "edges": list(edges.values())}


def synthetic_project(face_ids=(1, 2)):
    return {"schema_version": SCHEMA, "source_step": "", "source_sha256": "src", "profile": "",
            "geometry_sha256": "geo", "units": "mm-N-s",
            "materials": [{"id": 1, "name": "Steel", "young": 200000.0, "poisson": 0.0,
                           "density": 7.85e-9}],
            "sections": [{"id": 1, "name": "thin", "thickness": 0.1}],
            "refinement_enabled": False,
            "patches": [{"face_id": fid, "material_id": 1, "section_id": 1, "active": True,
                         "element": "Shell3pElement", "degree_u": 2, "degree_v": 2,
                         "insert_u": 0, "insert_v": 0, "quadrature": 0} for fid in face_ids],
            "groups": [], "supports": [], "loads": [], "initial_conditions": [], "couplings": [],
            "solver": {"type": "static", "analysis_type": "linear", "start_time": 0.0,
                       "end_time": 1.0, "time_step": 1.0},
            "output": {"vtk": False, "refinement": 4}}


def support_face(name, face_id, components, reference=None, side="u0", clamp=False):
    record = {"name": name, "target_kind": "face", "target_id": face_id, "method": "strong",
              "value": list(components), "side": side, "clamp": clamp,
              "interval": [0.0, "End"]}
    if reference is not None:
        record["value"] = reference
    return record


def initial(name, face_id, variable, components):
    return {"name": name, "target_kind": "face", "target_id": face_id, "variable": variable,
            "value": list(components)}


def error_text(errors, needle):
    return any(needle in error for error in errors)


class MigrationTests(unittest.TestCase):
    def test_schema2_migrates_to_schema3_without_changing_decisions(self):
        legacy = synthetic_project()
        legacy["schema_version"] = 2
        for patch in legacy["patches"]:
            patch.pop("active")
        legacy.pop("groups")
        legacy["supports"] = [support_face("S", 1, [0, None, None])]
        legacy["couplings"] = [{"edge_id": 7, "state": "pending", "origin": "geometry",
                                "penalty": 1e7, "rotation": False}]
        before = deepcopy(legacy)
        migrated = migrate_project(legacy)
        self.assertEqual(migrated["schema_version"], SCHEMA)
        self.assertEqual(migrated["groups"], [])
        self.assertTrue(all(patch["active"] for patch in migrated["patches"]))
        self.assertEqual(migrated["couplings"], before["couplings"])
        self.assertEqual(migrated["supports"], before["supports"])
        self.assertEqual(legacy, before)  # input untouched

    def test_schema1_migrates_sequentially_to_schema3(self):
        raw = read_json(EXAMPLE)
        self.assertEqual(raw["schema_version"], 1)
        migrated = migrate_project(raw)
        self.assertEqual(migrated["schema_version"], SCHEMA)
        self.assertTrue(all(patch["active"] for patch in migrated["patches"]))
        self.assertEqual(migrated["sections"][0]["thickness"], 0.1)
        self.assertEqual(migrated["materials"][0]["young"], 200000.0)

    def test_unsupported_schema_refused(self):
        with self.assertRaises(ValueError):
            migrate_project({"schema_version": 99, "patches": []})

    def test_new_project_has_groups_and_active_patches(self):
        project = new_project()
        self.assertEqual(project["groups"], [])
        self.assertEqual(project["schema_version"], SCHEMA)


class ActiveDomainTests(unittest.TestCase):
    def setUp(self):
        self.index = synthetic_index()

    def test_inactive_patch_outside_domain_is_reported_with_condition_name(self):
        project = synthetic_project()
        project["patches"][1]["active"] = False
        project["loads"] = [{"name": "Tip", "target_kind": "face", "target_id": 2,
                             "variable": "SURFACE_LOAD", "value": [0, 0, 1], "interval": [0.0, "End"]}]
        errors, warnings = validate(project, self.index)
        self.assertTrue(error_text(errors, "Tip"))
        self.assertTrue(error_text(errors, "outside the active analysis domain"))
        self.assertTrue(any("reference geometry only" in w for w in warnings))

    def test_all_patches_inactive_is_refused(self):
        project = synthetic_project()
        for patch in project["patches"]:
            patch["active"] = False
        errors, _ = validate(project, self.index)
        self.assertTrue(error_text(errors, "at least one active patch"))

    def test_confirmed_coupling_with_inactive_side_is_refused(self):
        project = synthetic_project()
        project["patches"][1]["active"] = False
        project["couplings"] = [{"edge_id": 7, "state": "confirmed", "origin": "user",
                                 "penalty": 1e7, "rotation": False}]
        errors, _ = validate(project, self.index)
        self.assertTrue(error_text(errors, "Interface 7"))
        self.assertTrue(error_text(errors, "outside the active analysis domain"))

    def test_active_flag_must_be_boolean(self):
        project = synthetic_project()
        project["patches"][0]["active"] = "yes"
        errors, _ = validate(project, self.index)
        self.assertTrue(error_text(errors, "'active' must be a boolean"))


class GroupTests(unittest.TestCase):
    def setUp(self):
        self.index = synthetic_index()
        self.project = synthetic_project()

    def test_group_validation_requires_name_kind_and_members(self):
        self.project["groups"] = [{"id": 1, "name": "", "kind": "face", "members": []},
                                  {"id": 2, "name": "E", "kind": "edge", "members": [4, 5]},
                                  {"id": 2, "name": "dup", "kind": "face", "members": [1]}]
        errors, _ = validate(self.project, self.index)
        self.assertTrue(error_text(errors, "name is required"))
        self.assertTrue(error_text(errors, "at least one face id"))
        self.assertTrue(error_text(errors, "Duplicate group ID 2"))

    def test_group_member_of_wrong_kind_is_reported(self):
        self.project["groups"] = [{"id": 1, "name": "F", "kind": "face", "members": [1, 4]}]
        errors, _ = validate(self.project, self.index)
        self.assertTrue(error_text(errors, "lists edge ID(s) [4]"))

    def test_dangling_member_is_reported_and_condition_names_the_group(self):
        self.project["groups"] = [{"id": 1, "name": "Rim", "kind": "edge", "members": [4, 99]}]
        self.project["loads"] = [{"name": "Edge load", "group_id": 1, "variable": "LINE_LOAD",
                                  "value": [0, 0, -1], "interval": [0.0, "End"]}]
        errors, _ = validate(self.project, self.index)
        self.assertTrue(error_text(errors, "member ID(s) [99]"))
        self.assertTrue(error_text(errors, "Edge load"))

    def test_missing_group_reference_is_reported_with_condition_name(self):
        self.project["loads"] = [{"name": "Ghost", "group_id": 5, "variable": "SURFACE_LOAD",
                                  "value": [0, 0, 1], "interval": [0.0, "End"]}]
        errors, _ = validate(self.project, self.index)
        self.assertTrue(error_text(errors, "Ghost"))
        self.assertTrue(error_text(errors, "no longer exists"))

    def test_contradictory_group_and_direct_target_refused(self):
        self.project["groups"] = [{"id": 1, "name": "F", "kind": "face", "members": [1]}]
        self.project["loads"] = [{"name": "Both", "group_id": 1, "target_kind": "face",
                                  "target_id": 2, "variable": "SURFACE_LOAD", "value": [0, 0, 1],
                                  "interval": [0.0, "End"]}]
        errors, _ = validate(self.project, self.index)
        self.assertTrue(error_text(errors, "Both"))
        self.assertTrue(error_text(errors, "both a named-group reference and a direct target"))

    def test_expansion_is_deterministic_and_deduplicated(self):
        groups = [{"id": 1, "name": "E", "kind": "edge", "members": [5, 4, 5]}]
        resolved = expand(groups, self.index)
        self.assertEqual(resolved[1]["members"], [4, 5])

    def test_direct_target_still_validated(self):
        self.project["loads"] = [{"name": "Tip", "target_kind": "face", "target_id": 99,
                                  "variable": "SURFACE_LOAD", "value": [0, 0, 1],
                                  "interval": [0.0, "End"]}]
        errors, _ = validate(self.project, self.index)
        self.assertTrue(error_text(errors, "not in the current geometry cache"))

    def test_coupling_edge_is_not_a_one_sided_target(self):
        self.project["loads"] = [{"name": "Seam", "target_kind": "edge", "target_id": 7,
                                  "variable": "LINE_LOAD", "value": [0, 0, -1],
                                  "interval": [0.0, "End"]}]
        errors, _ = validate(self.project, self.index)
        self.assertTrue(error_text(errors, "Seam"))
        self.assertTrue(error_text(errors, "not a one-sided boundary"))


class ConflictTests(unittest.TestCase):
    def setUp(self):
        self.index = synthetic_index()
        self.project = synthetic_project()

    def test_same_component_overlapping_time_is_a_conflict(self):
        self.project["supports"] = [support_face("S1", 1, [0, None, None]),
                                    support_face("S2", 1, [0, None, None])]
        errors, _ = validate(self.project, self.index)
        self.assertTrue(error_text(errors, "S2 and S1"))
        self.assertTrue(error_text(errors, "same component (X)"))

    def test_complementary_components_are_allowed(self):
        self.project["supports"] = [support_face("Sx", 1, [0, None, None]),
                                    support_face("Sy", 1, [None, 0, None])]
        errors, _ = validate(self.project, self.index)
        self.assertFalse(error_text(errors, "same component"))

    def test_disjoint_time_intervals_do_not_conflict(self):
        self.project["supports"] = [support_face("S1", 1, [0, None, None]),
                                    support_face("S2", 1, [0, None, None])]
        self.project["supports"][0]["interval"] = [0.0, 0.4]
        self.project["supports"][1]["interval"] = [0.5, "End"]
        errors, _ = validate(self.project, self.index)
        self.assertFalse(error_text(errors, "same component"))

    def test_u0_and_v0_share_the_corner_control_point(self):
        self.project["supports"] = [support_face("U0", 1, [0, None, None], side="u0"),
                                    support_face("V0", 1, [0, None, None], side="v0")]
        errors, _ = validate(self.project, self.index)
        self.assertTrue(error_text(errors, "same component (X)"))

    def test_whole_face_covers_an_edge_selection(self):
        self.project["supports"] = [support_face("All", 1, [0, None, None], side="all"),
                                    support_face("Edge", 1, [0, None, None], side="u0")]
        errors, _ = validate(self.project, self.index)
        self.assertTrue(error_text(errors, "same component (X)"))

    def test_opposite_edges_do_not_conflict(self):
        self.project["supports"] = [support_face("U0", 1, [0, None, None], side="u0"),
                                    support_face("U1", 1, [0, None, None], side="u1")]
        errors, _ = validate(self.project, self.index)
        self.assertFalse(error_text(errors, "same component"))

    def test_group_of_faces_expands_every_member(self):
        self.project["groups"] = [{"id": 1, "name": "Rim", "kind": "face", "members": [1, 2]}]
        self.project["supports"] = [
            {"name": "G", "group_id": 1, "side": "u0", "method": "strong",
             "value": [0, None, None], "clamp": False, "interval": [0.0, "End"]},
            support_face("Direct", 2, [0, None, None], side="u0")]
        errors, _ = validate(self.project, self.index)
        self.assertTrue(error_text(errors, "Direct and G"))

    def test_clamped_low_boundary_copies_row_1(self):
        self.project["supports"] = [support_face("ClampLow", 1, [0, None, None], side="u0", clamp=True)]
        spec = strong_spec(self.project["supports"][0], self.index, {}, self.project)
        region = spec["locations"][0][2]
        self.assertIn((0, 0), region)      # boundary row u0
        self.assertIn((1, 0), region)      # clamped neighbour of the low boundary
        self.assertNotIn((2, 0), region)   # not a speculative offset
        # No conflict with a record that only touches u1's own boundary row.
        self.project["supports"].append(support_face("U1", 1, [0, None, None], side="u1"))
        self.assertFalse(error_text(validate(self.project, self.index)[0], "same component"))

    def test_clamped_high_boundary_copies_n_minus_2(self):
        self.project["supports"] = [support_face("ClampHigh", 1, [0, None, None], side="u1", clamp=True)]
        spec = strong_spec(self.project["supports"][0], self.index, {}, self.project)
        region = spec["locations"][0][2]
        self.assertIn((3, 0), region)      # boundary row n-1
        self.assertIn((2, 0), region)      # neighbour n-2
        self.assertNotIn((1, 0), region)

    def test_row_and_column_always_intersect_at_the_corner(self):
        index = synthetic_index()
        index["faces"][0]["control_point_counts"] = {"u": 4, "v": 4, "total": 16}
        self.project["supports"] = [support_face("U2", 1, [0, None, None], side="u0"),
                                    support_face("V1", 1, [0, None, None], side="v0")]
        self.project["supports"][0]["side"] = "u0"
        # u0 is a row, v0 is a column: they share exactly the (0,0) control point.
        errors, _ = validate(self.project, index)
        self.assertTrue(error_text(errors, "same component (X)"),
                        "perpendicular boundaries share a corner control point")

    def test_disjoint_components_on_the_corner_still_allowed(self):
        index = synthetic_index()
        self.project["supports"] = [support_face("U0", 1, [0, None, None], side="u0"),
                                    support_face("V0", 1, [None, 0, None], side="v0")]
        errors, _ = validate(self.project, index)
        self.assertFalse(error_text(errors, "same component"))

    def test_all_four_corners_map_to_single_control_points(self):
        from kratos_iga.groups import SIDES
        corners = {"00": (0, 0), "10": (3, 0), "01": (0, 2), "11": (3, 2)}
        for side, expected in corners.items():
            region = build_region((4, 3), list(SIDES[side]))
            self.assertEqual(region, {expected}, f"side {side}")
        self.assertTrue(regions_overlap(build_region((4, 3), [0, 0]),
                                        build_region((4, 3), [0, -1])))
        self.assertTrue(regions_overlap(build_region((4, 3), [1, 1]),
                                        build_region((4, 3), [-1, -1])))
        self.assertFalse(regions_overlap(build_region((4, 3), [0, 0]),
                                         build_region((4, 3), [1, 1])))

    def test_corner_clamp_is_refused(self):
        self.project["supports"] = [support_face("Corner", 1, [0, None, None], side="00", clamp=True)]
        errors, _ = validate(self.project, self.index)
        self.assertTrue(error_text(errors, "Corner"))
        self.assertTrue(error_text(errors, "not defined at a natural corner"))

    def test_two_corners_same_component_conflict(self):
        self.project["supports"] = [support_face("C00", 1, [0, None, None], side="00"),
                                    support_face("C00b", 1, [0, None, None], side="00")]
        self.assertTrue(error_text(validate(self.project, self.index)[0], "same component (X)"))

    def test_missing_control_net_metadata_requires_rebuild(self):
        sparse = synthetic_index()
        for face in sparse["faces"]:
            face.pop("control_point_counts")
            face.pop("knot_spans")
        self.project["supports"] = [support_face("S", 1, [0, None, None], side="u0")]
        errors, _ = validate(self.project, sparse)
        self.assertTrue(error_text(errors, "no control-net counts/spans"))
        self.assertTrue(error_text(errors, "Reimport the source STEP"))

    def test_whole_face_covers_an_edge_selection(self):
        self.project["supports"] = [support_face("All", 1, [0, None, None], side="all"),
                                    support_face("Edge", 1, [0, None, None], side="u0")]
        errors, _ = validate(self.project, self.index)
        self.assertTrue(error_text(errors, "same component (X)"))

    def test_opposite_edges_do_not_conflict(self):
        self.project["supports"] = [support_face("U0", 1, [0, None, None], side="u0"),
                                    support_face("U1", 1, [0, None, None], side="u1")]
        errors, _ = validate(self.project, self.index)
        self.assertFalse(error_text(errors, "same component"))

    def test_group_of_faces_expands_every_member(self):
        self.project["groups"] = [{"id": 1, "name": "Rim", "kind": "face", "members": [1, 2]}]
        self.project["supports"] = [
            {"name": "G", "group_id": 1, "side": "u0", "method": "strong",
             "value": [0, None, None], "clamp": False, "interval": [0.0, "End"]},
            support_face("Direct", 2, [0, None, None], side="u0")]
        errors, _ = validate(self.project, self.index)
        self.assertTrue(error_text(errors, "Direct and G"))

    def test_group_with_a_non_natural_member_fails_as_a_whole(self):
        # The group mixes a natural boundary (edge 4) with a two-sided coupling (edge 7).
        self.project["groups"] = [{"id": 1, "name": "Mixed", "kind": "edge", "members": [4, 7]}]
        self.project["supports"] = [{"name": "Mixed support", "group_id": 1, "method": "strong",
                                     "value": [0, None, None], "clamp": False,
                                     "interval": [0.0, "End"]}]
        errors, _ = validate(self.project, self.index)
        self.assertTrue(error_text(errors, "Mixed support"))
        self.assertTrue(error_text(errors, "not a one-sided boundary"))
        # A group whose member exists but has no natural location also fails as a whole
        # rather than dropping the member that cannot be resolved.
        index = synthetic_index()
        index["edges"].append({"id": 8, "kind": "edge", "face_ids": [1], "strong_local": None})
        self.project["groups"] = [{"id": 2, "name": "Trim", "kind": "edge", "members": [4, 8]}]
        self.project["supports"] = [{"name": "Trim support", "group_id": 2, "method": "strong",
                                     "value": [0, None, None], "clamp": False,
                                     "interval": [0.0, "End"]}]
        errors, _ = validate(self.project, index)
        self.assertTrue(error_text(errors, "Trim support"))
        self.assertTrue(error_text(errors, "complete natural parametric boundary"))

    def test_complementary_initial_components_allowed_same_component_refused(self):
        self.project["initial_conditions"] = [initial("IX", 1, "DISPLACEMENT", [0.0, None, None])]
        errors, _ = validate(self.project, self.index)
        self.assertFalse(errors)

        self.project["initial_conditions"].append(initial("IY", 1, "DISPLACEMENT", [None, 0.0, None]))
        errors, warnings = validate(self.project, self.index)
        self.assertFalse(error_text(errors, "would overwrite"))
        self.assertTrue(any("complementary components" in w for w in warnings))

        self.project["initial_conditions"].append(initial("IX2", 1, "DISPLACEMENT", [0.0, None, None]))
        errors, _ = validate(self.project, self.index)
        self.assertTrue(error_text(errors, "IX2 and IX"))
        self.assertTrue(error_text(errors, "component(s) X"))


class StrongRecordFidelityTests(unittest.TestCase):
    """Each support record keeps its own values, components and interval."""

    def setUp(self):
        self.index = synthetic_index()
        self.project = synthetic_project()

    def bundle(self):
        from kratos_iga.bundle import make_bundle
        cad = {"breps": [], "tolerances": {"model_tolerance": 0.001}}
        return make_bundle(self.project, cad, self.index)

    def test_records_are_not_merged_and_intervals_are_exact(self):
        self.project["supports"] = [support_face("Early", 1, [0.0, None, None]),
                                    support_face("Late", 1, [0.0, None, None])]
        self.project["supports"][0]["interval"] = [0.0, 0.4]
        self.project["supports"][1]["interval"] = [0.5, "End"]
        processes = [entry for entry in self.bundle()["ProjectParameters.json"]["processes"]["constraints_process_list"]
                     if entry["Parameters"].get("variable_name") == "DISPLACEMENT"
                     and entry["Parameters"]["model_part_name"].startswith("IgaModelPart.Support_")]
        intervals = [entry["Parameters"]["interval"] for entry in processes]
        self.assertEqual(intervals, [[0.0, 0.4], [0.5, "End"]])
        names = [entry["Parameters"]["model_part_name"] for entry in processes]
        self.assertEqual(len(set(names)), 2)

    def test_complementary_records_keep_their_own_components(self):
        self.project["supports"] = [support_face("Sx", 1, [0.0, None, None]),
                                    support_face("Sy", 1, [None, 0.0, None])]
        processes = [entry["Parameters"] for entry in self.bundle()["ProjectParameters.json"]["processes"]["constraints_process_list"]
                     if entry["Parameters"]["model_part_name"].startswith("IgaModelPart.Support_")]
        self.assertEqual(len(processes), 2)
        self.assertEqual([p["constrained"] for p in processes], [[True, False, False], [False, True, False]])
        self.assertEqual([p["value"] for p in processes], [[0.0, None, None], [None, 0.0, None]])

    def test_support_and_penalty_names_do_not_collide(self):
        self.project["supports"] = [support_face("Strong", 1, [0.0, None, None]),
                                    {"name": "Penalty", "target_kind": "edge", "target_id": 4,
                                     "method": "penalty", "value": [0.0, 0.0, 0.0], "penalty": 1e7,
                                     "interval": [0.0, "End"]}]
        names = sorted({entry["iga_model_part"] for entry in self.bundle()["physics.iga.json"]["element_condition_list"]
                        if entry["iga_model_part"].startswith("Support_")})
        self.assertEqual(names, ["Support_1", "Support_2"])

    def test_grouped_strong_support_shares_one_part_across_members(self):
        self.project["groups"] = [{"id": 1, "name": "Edges", "kind": "edge", "members": [4, 5]}]
        self.project["supports"] = [{"name": "G", "group_id": 1, "method": "strong",
                                     "value": [0.0, None, None], "clamp": False,
                                     "interval": [0.0, "End"]}]
        bundle = self.bundle()
        units = [entry for entry in bundle["physics.iga.json"]["element_condition_list"]
                 if entry["iga_model_part"] == "Support_1"]
        self.assertEqual([entry["geometry_type"] for entry in units],
                         ["GeometrySurfaceNodes", "GeometrySurfaceNodes"])
        self.assertEqual({entry["brep_ids"][0] for entry in units}, {1, 2})
        processes = [entry["Parameters"] for entry in bundle["ProjectParameters.json"]["processes"]["constraints_process_list"]
                     if entry["Parameters"]["model_part_name"] == "IgaModelPart.Support_1"]
        self.assertEqual(len(processes), 1)

    def test_final_grid_uses_degree_delta_and_inserts_per_span(self):
        from kratos_iga.groups import final_grid
        face = self.index["faces"][0]
        patch = {"face_id": 1, "degree_u": 3, "degree_v": 2, "insert_u": 2, "insert_v": 0}
        # n_u = 4 + (3-2)*1 + 2*1 = 7; n_v = 3 + 0*2 + 0*2 = 3
        self.assertEqual(final_grid(self.index, 1, patch, True), (7, 3))
        self.assertEqual(final_grid(self.index, 1, patch, False), (4, 3))


class UnitConversionGroupTests(unittest.TestCase):
    def test_grouped_edge_load_uses_the_resolved_edge_measure(self):
        index = synthetic_index()
        project = synthetic_project()
        project["groups"] = [{"id": 1, "name": "E", "kind": "edge", "members": [4, 5]}]
        project["loads"] = [{"name": "Rim", "group_id": 1, "variable": "LINE_LOAD",
                             "value": [0, 0, -1], "interval": [0.0, "End"]}]
        converted = convert_project_units(project, "m-N-s", index)
        self.assertAlmostEqual(converted["loads"][0]["value"][2], -1 * 1e3)

    def test_grouped_face_load_uses_surface_measure(self):
        index = synthetic_index()
        project = synthetic_project()
        project["groups"] = [{"id": 1, "name": "F", "kind": "face", "members": [1]}]
        project["loads"] = [{"name": "Top", "group_id": 1, "variable": "SURFACE_LOAD",
                             "value": [0, 0, -2], "interval": [0.0, "End"]}]
        converted = convert_project_units(project, "m-N-s", index)
        self.assertAlmostEqual(converted["loads"][0]["value"][2], -2 * 1e6)

    def test_dead_load_group_kind_selects_the_measure(self):
        index = synthetic_index()
        edge = synthetic_project()
        edge["groups"] = [{"id": 1, "name": "E", "kind": "edge", "members": [4]}]
        edge["loads"] = [{"name": "E", "group_id": 1, "variable": "DEAD_LOAD",
                          "value": [0, 0, -3], "interval": [0.0, "End"]}]
        self.assertAlmostEqual(convert_project_units(edge, "m-N-s", index)["loads"][0]["value"][2], -3e3)
        face = synthetic_project()
        face["groups"] = [{"id": 1, "name": "F", "kind": "face", "members": [1]}]
        face["loads"] = [{"name": "F", "group_id": 1, "variable": "DEAD_LOAD",
                          "value": [0, 0, -3], "interval": [0.0, "End"]}]
        self.assertAlmostEqual(convert_project_units(face, "m-N-s", index)["loads"][0]["value"][2], -3e6)

    def test_missing_group_blocks_conversion(self):
        index = synthetic_index()
        project = synthetic_project()
        project["loads"] = [{"name": "Ghost", "group_id": 9, "variable": "LINE_LOAD",
                             "value": [0, 0, -1], "interval": [0.0, "End"]}]
        with self.assertRaises(ValueError):
            convert_project_units(project, "m-N-s", index)


def two_patch_index(index):
    """A second, identical patch derived from the real converter index.

    The active-domain and group assertions below are about *file-level* selection
    semantics, not about a particular CAD model, so one real patch is duplicated
    deterministically (new ids, same control-net sizes) to obtain a two-patch case.
    """
    import copy
    clone = copy.deepcopy(index)
    first = clone["faces"][0]
    new_id = max(face["id"] for face in clone["faces"]) + 1
    second = copy.deepcopy(first)
    second["id"] = new_id
    second["brep_file"] = first["brep_file"]
    # A coupling between the two patches, built from the fixture's own boundary edge.
    boundary = next(edge for edge in clone["edges"] if edge["kind"] == "edge")
    coupling_id = max(edge["id"] for edge in clone["edges"]) + 1
    coupling = copy.deepcopy(boundary)
    coupling.update({"id": coupling_id, "kind": "coupling",
                     "face_ids": [first["id"], new_id], "strong_local": None})
    clone["faces"].append(second)
    clone["edges"].append(coupling)
    return clone


class ExportDomainTests(unittest.TestCase):
    """Real converter output: file-level Kratos semantics of the active subset."""

    @classmethod
    def setUpClass(cls):
        from cad_pipeline.api import convert_step
        from kratos_iga.worker import build_index
        cls.base = read_project(EXAMPLE)
        cls.cad, report = convert_step(Path(cls.base["source_step"]))
        cls.folder = Path(tempfile.mkdtemp(prefix="p0-3-index-"))
        cls.real_index = build_index(cls.cad, report, cls.folder)
        cls.index = two_patch_index(cls.real_index)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    def two_patch_project(self):
        project = deepcopy(self.base)
        face = self.real_index["faces"][0]
        second = {"face_id": self.index["faces"][1]["id"], "material_id": 1, "section_id": 1,
                  "active": True, "element": "IgaMembraneElement", "degree_u": 1, "degree_v": 1,
                  "insert_u": 0, "insert_v": 0, "quadrature": 0}
        project["patches"] = [dict(project["patches"][0], element="IgaMembraneElement",
                                   degree_u=1, degree_v=1, insert_u=0, insert_v=0), second]
        project["refinement_enabled"] = True
        project["supports"] = []
        project["loads"] = []
        project["initial_conditions"] = []
        project["couplings"] = []
        project["geometry_sha256"] = self.index["geometry_sha256"]
        project["source_sha256"] = self.index["source_sha256"]
        return project

    def bundle(self, project, index=None):
        return make_bundle(project, self.cad, index if index is not None else self.index)

    def test_active_subset_excludes_elements_materials_refinement_and_vtk(self):
        project = self.two_patch_project()
        active_face = project["patches"][0]["face_id"]
        inactive_face = project["patches"][1]["face_id"]
        for patch in project["patches"]:
            patch["active"] = patch["face_id"] == active_face
        project["output"]["vtk"] = True
        bundle = self.bundle(project)

        ids = [entry["brep_ids"] for entry in bundle["physics.iga.json"]["element_condition_list"]
               if entry["geometry_type"] == "GeometrySurface"]
        self.assertEqual(ids, [[active_face]])
        parts = {entry["model_part_name"] for entry in bundle["Materials.json"]["properties"]}
        self.assertIn(f"IgaModelPart.Patch_{active_face}", parts)
        self.assertNotIn(f"IgaModelPart.Patch_{inactive_face}", parts)
        refined = {brep for entry in bundle["refinements.iga.json"]["refinements"]
                   for brep in entry["brep_ids"]}
        self.assertEqual(refined, {active_face})
        vtk = [entry for entry in bundle["ProjectParameters.json"]["output_processes"]["output_process_list"]
               if entry["python_module"] == "iga_vtk_output_process"][0]
        self.assertEqual(vtk["Parameters"]["brep_surface_ids"], [active_face])
        # Reference geometry is complete and byte-identical apart from unit scaling.
        self.assertEqual(bundle["geometry.cad.json"], self.cad)
        self.assertEqual(bundle["export_report.json"]["active_patches"], [active_face])
        self.assertTrue(any("reference geometry" in w for w in bundle["export_report.json"]["warnings"]))

    def test_grouped_edge_load_targets_every_member_once(self):
        project = self.two_patch_project()
        boundaries = [e for e in self.index["edges"] if e["kind"] == "edge"]
        self.assertTrue(boundaries, "fixture needs at least one one-sided boundary")
        members = [e["id"] for e in boundaries[:2]]
        project["groups"] = [{"id": 1, "name": "Rim", "kind": "edge", "members": members + members[:1]}]
        project["loads"] = [{"name": "Rim load", "group_id": 1, "variable": "LINE_LOAD",
                             "value": [0.0, 0.0, -0.001], "interval": [0.0, "End"]}]
        bundle = self.bundle(project)
        units = [entry for entry in bundle["physics.iga.json"]["element_condition_list"]
                 if entry["iga_model_part"] == "Load_1"]
        self.assertEqual([entry["geometry_type"] for entry in units], ["SurfaceEdge"])
        self.assertEqual(units[0]["brep_ids"], sorted(set(members)))
        report = bundle["export_report.json"]
        self.assertIn("1", report["groups"])
        self.assertEqual(report["groups"]["1"]["members"], sorted(set(members)))

    def test_grouped_face_load_on_two_faces(self):
        project = self.two_patch_project()
        members = sorted(f["id"] for f in self.index["faces"])
        project["groups"] = [{"id": 2, "name": "Top", "kind": "face", "members": members}]
        project["loads"] = [{"name": "Top", "group_id": 2, "variable": "SURFACE_LOAD",
                             "value": [0.0, 0.0, -0.001], "interval": [0.0, "End"]}]
        bundle = self.bundle(project)
        units = [entry for entry in bundle["physics.iga.json"]["element_condition_list"]
                 if entry["iga_model_part"] == "Load_1"]
        self.assertEqual(units[0]["geometry_type"], "GeometrySurface")
        self.assertEqual(units[0]["brep_ids"], members)

    def test_overlapping_loads_are_summed_and_reported(self):
        project = self.two_patch_project()
        face_id = self.index["faces"][0]["id"]
        project["loads"] = [
            {"name": "L1", "target_kind": "face", "target_id": face_id,
             "variable": "SURFACE_LOAD", "value": [0.0, 0.0, -0.001], "interval": [0.0, "End"]},
            {"name": "L2", "target_kind": "face", "target_id": face_id,
             "variable": "SURFACE_LOAD", "value": [0.0, 0.0, -0.002], "interval": [0.0, "End"]}]
        bundle = self.bundle(project)
        units = [entry["iga_model_part"] for entry in bundle["physics.iga.json"]["element_condition_list"]
                 if entry["iga_model_part"] in ("Load_1", "Load_2")]
        self.assertEqual(units, ["Load_1", "Load_2"])
        self.assertTrue(any("Overlapping loads" in w for w in bundle["export_report.json"]["warnings"]))

    def test_export_blocks_unresolved_group_member(self):
        project = self.two_patch_project()
        member = next(e["id"] for e in self.index["edges"] if e["kind"] == "edge")
        project["groups"] = [{"id": 1, "name": "Rim", "kind": "edge", "members": [member, 99999]}]
        project["loads"] = [{"name": "Rim load", "group_id": 1, "variable": "LINE_LOAD",
                             "value": [0.0, 0.0, -0.001], "interval": [0.0, "End"]}]
        with self.assertRaises(ValueError) as context:
            self.bundle(project)
        self.assertIn("Rim load", str(context.exception))

    def test_grouped_strong_support_expands_every_member(self):
        project = self.two_patch_project()
        members = sorted(f["id"] for f in self.index["faces"])
        self.assertEqual(len(members), 2)
        project["supports"] = [{"name": "G", "group_id": 1, "side": "u0", "method": "strong",
                                "value": [0.0, 0.0, 0.0], "clamp": False, "interval": [0.0, "End"]}]
        project["groups"] = [{"id": 1, "name": "Rim", "kind": "face", "members": members}]
        bundle = self.bundle(project)
        supports = [entry for entry in bundle["physics.iga.json"]["element_condition_list"]
                    if entry["iga_model_part"].startswith("Support_")]
        supported = {entry["brep_ids"][0] for entry in supports}
        self.assertEqual(supported, set(members))
        self.assertEqual(len(supports), 2)  # one entry per member, not only the first
        self.assertEqual({entry["iga_model_part"] for entry in supports}, {"Support_1"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
