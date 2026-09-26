"""P0-2 coupling decision and geometry-evidence tests.

Three layers:

* ``CouplingContractTests`` - stdlib unit tests over a small hand-built index.
* ``ConverterEvidenceTests`` - real two adjacent STEP faces through the real
  OCP converter and worker index; no synthetic CAD-only acceptance.
* ``KratosTwoPatchTests`` - real Kratos 10.4.3 exports and solves: two-patch
  against single-patch reference, penalty scan and reversed parameterisation.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "freecad/Mod/KratosIBRA"))
sys.path.insert(0, str(ROOT / "freecad/Mod/KratosIBRA/backend"))

from coupling_fixtures import (LENGTH, P_BEND, P_TENSION, bending_reference, converted,
                               coupling_edges, make_project, tension_reference,
                               write_strip_step)  # noqa: E402
from kratos_iga.bundle import export_bundle, make_bundle  # noqa: E402
from kratos_iga.coupling_diagnostics import summarize  # noqa: E402
from kratos_iga.project import migrate_project, new_project, validate  # noqa: E402

GAP_OK = {"geometry_sha256": "geo", "sample_count": 41, "tolerance": 1.0e-3,
          "max_paired_gap": 1.0e-9, "parameter_mismatch": 2.0e-9, "max_nearest_segment_gap": 1.0e-9,
          "param_correspondence": "index_aligned_same_direction",
          "orientation_source": "pcurve_direction_vs_shared_3d_curve",
          "max_curve_error": 1.0e-9,
          "sides": [{"face_id": 2, "trim_index": 4, "curve_origin": "embedded_edge",
                     "direction": "forward", "curve_error": 1.0e-9, "degenerate": False},
                    {"face_id": 4, "trim_index": 4, "curve_origin": "embedded_edge",
                     "direction": "reversed", "curve_error": 1.0e-9, "degenerate": False}],
          "status": "ok", "detail": "sampled on both source surfaces",
          "note": "sampled; approximation, not whole-edge or mechanical proof"}


def unit_index(diagnostics=None):
    """Small index with one coupling edge, for contract-level tests."""
    diagnostics = copy.deepcopy(GAP_OK if diagnostics is None else diagnostics)
    if isinstance(diagnostics, dict):
        diagnostics.setdefault("geometry_sha256", "geo")
    return {"source_sha256": "src", "geometry_sha256": "geo",
            "faces": [{"id": 2, "degrees": [2, 2], "min_continuity": [99, 99],
                       "control_points": 9, "control_point_counts": {"u": 3, "v": 3, "total": 9},
                       "knot_spans": {"u": 1, "v": 1}},
                      {"id": 4, "degrees": [2, 2], "min_continuity": [99, 99],
                       "control_points": 9, "control_point_counts": {"u": 3, "v": 3, "total": 9},
                       "knot_spans": {"u": 1, "v": 1}}],
            "edges": [{"id": 9, "kind": "coupling", "face_ids": [2, 4], "points": [[0, 0, 0], [0, 1, 0]],
                       "strong_local": None, "diagnostics": diagnostics},
                      {"id": 11, "kind": "edge", "face_ids": [4], "points": [[20, 1, 0], [20, 0, 0]],
                       "strong_local": None}]}


def unit_cad():
    """Minimal valid converted CAD matching ``unit_index()`` (faces 2 and 4).

    The coupling contract tests assert physics/materials assembly, not geometry, but
    the export now runs read-only CAD diagnostics and rejects a non-object document.
    These tests therefore carry the smallest well-formed two-face CAD.
    """
    def surface():
        return {"degrees": [2, 2], "is_rational": False,
                "knot_vectors": [[-5.0, -5.0, -5.0, 5.0, 5.0, 5.0],
                                 [-2.0, -2.0, -2.0, 2.0, 2.0, 2.0]],
                "control_points": [[1, [0.0, 0.0, 0.0, 1.0]], [2, [10.0, 0.0, 0.0, 1.0]],
                                   [3, [0.0, 4.0, 0.0, 1.0]], [4, [10.0, 4.0, 0.0, 1.0]]]}

    def curve():
        return {"trim_index": 0, "curve_direction": True,
                "parameter_curve": {"degree": 1, "is_rational": False,
                                    "knot_vector": [0.0, 0.0, 1.0, 1.0],
                                    "active_range": [0.0, 1.0],
                                    "control_points": [[0, [0.0, 0.0, 0.0, 1.0]],
                                                       [1, [1.0, 1.0, 0.0, 1.0]]]}}

    return {"breps": [{"faces": [
        {"brep_id": face_id, "surface": surface(),
         "boundary_loops": [{"loop_type": "outer", "trimming_curves": [curve()]}]}
        for face_id in (2, 4)]}]}


def unit_project(index, state="pending", origin="geometry"):
    project = new_project()
    project.update({"source_step": "x.step", "source_sha256": "src", "geometry_sha256": "geo"})
    project["patches"] = [{"face_id": 2, "material_id": 1, "section_id": 1, "element": "Shell3pElement",
                           "degree_u": 3, "degree_v": 3, "insert_u": 4, "insert_v": 0, "quadrature": 0},
                          {"face_id": 4, "material_id": 1, "section_id": 1, "element": "Shell3pElement",
                           "degree_u": 3, "degree_v": 3, "insert_u": 4, "insert_v": 0, "quadrature": 0}]
    project["couplings"] = [{"edge_id": 9, "state": state, "origin": origin,
                             "penalty": 1e7, "rotation": False}]
    project["supports"] = [{"name": "root", "target_kind": "face", "target_id": 2, "side": "u0",
                            "method": "strong", "value": [0.0, 0.0, 0.0], "clamp": True,
                            "interval": [0.0, "End"]}]
    project["loads"] = [{"name": "tip", "target_kind": "edge", "target_id": 11, "variable": "LINE_LOAD",
                         "value": [0.0, 0.0, -0.001], "interval": [0.0, "End"]}]
    project["refinement_enabled"] = True
    project["output"] = {"vtk": False, "refinement": 4}
    return project


def coupling_conditions(bundle):
    return [u for u in bundle["physics.iga.json"]["element_condition_list"]
            if u["geometry_type"] == "SurfaceEdgeSurfaceEdge"]


class CouplingContractTests(unittest.TestCase):
    def test_populate_candidates_are_pending(self):
        from kratos_iga.project import populate
        project = new_project()
        populate(project, unit_index())
        self.assertEqual(len(project["couplings"]), 1)
        record = project["couplings"][0]
        self.assertEqual((record["state"], record["origin"]), ("pending", "geometry"))
        self.assertNotIn("enabled", record)

    def test_new_project_has_no_couplings(self):
        self.assertEqual(new_project()["couplings"], [])

    def test_pending_and_excluded_are_not_exported(self):
        for state in ("pending", "excluded"):
            project = unit_project(unit_index(), state=state)
            self.assertEqual(coupling_conditions(make_bundle(project, unit_cad(), unit_index())), [],
                             f"{state} must never enter physics")

    def test_confirmed_is_exported(self):
        project = unit_project(unit_index(), state="confirmed", origin="user")
        units = coupling_conditions(make_bundle(project, unit_cad(), unit_index()))
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["geometry_type"], "SurfaceEdgeSurfaceEdge")
        self.assertEqual(make_bundle(project, unit_cad(), unit_index())["Materials.json"]["properties"][-1]
                         ["Material"]["Variables"]["PENALTY_FACTOR"], 1e7)

    def test_legacy_enabled_migrated_as_unreviewed(self):
        legacy = {"schema_version": 2, "couplings": [
            {"edge_id": 9, "enabled": True, "penalty": 3e6, "rotation": True}]}
        migrated = migrate_project(legacy)
        record = migrated["couplings"][0]
        self.assertEqual(record["state"], "confirmed")
        self.assertEqual(record["origin"], "legacy_enabled_unreviewed")
        self.assertEqual(record["penalty"], 3e6)
        self.assertTrue(record["rotation"])
        self.assertNotIn("enabled", record)
        project = unit_project(unit_index(), state="confirmed", origin="legacy_enabled_unreviewed")
        warnings = validate(project, unit_index())[1]
        self.assertTrue(any("not been reviewed" in w for w in warnings))

    def test_legacy_disabled_migrated_as_excluded(self):
        legacy = {"schema_version": 2, "couplings": [
            {"edge_id": 9, "enabled": False, "penalty": 1e7, "rotation": False}]}
        record = migrate_project(legacy)["couplings"][0]
        self.assertEqual((record["state"], record["origin"]), ("excluded", "legacy_disabled"))
        self.assertEqual(record["penalty"], 1e7)
        self.assertFalse(record["rotation"])

    def test_schema1_legacy_couplings_keep_values(self):
        legacy = {"schema_version": 1, "materials": [{"id": 1, "name": "S", "young": 1.0,
                   "poisson": 0.3, "density": 1.0, "thickness": 0.25}],
                  "patches": [{"face_id": 2, "material_id": 1}],
                  "couplings": [{"edge_id": 9, "enabled": True, "penalty": 5e5, "rotation": False}]}
        migrated = migrate_project(legacy)
        self.assertEqual(migrated["schema_version"], 3)
        self.assertEqual(migrated["sections"], [{"id": 1, "name": "S", "thickness": 0.25}])
        record = migrated["couplings"][0]
        self.assertEqual(record["penalty"], 5e5)
        self.assertEqual(record["state"], "confirmed")

    def test_unknown_state_rejected(self):
        project = unit_project(unit_index(), state="active")
        errors = validate(project, unit_index())[0]
        self.assertTrue(any("unknown state" in e for e in errors), errors)

    def test_duplicate_interface_rejected(self):
        project = unit_project(unit_index(), state="confirmed", origin="user")
        project["couplings"].append(dict(project["couplings"][0]))
        errors = validate(project, unit_index())[0]
        self.assertTrue(any("Duplicate interface ID" in e for e in errors), errors)

    def test_enabled_alongside_state_rejected(self):
        project = unit_project(unit_index(), state="confirmed", origin="user")
        project["couplings"][0]["enabled"] = True
        errors = validate(project, unit_index())[0]
        self.assertTrue(any("single source of truth" in e for e in errors), errors)

    def test_unresolved_interface_rejected(self):
        project = unit_project(unit_index(), state="confirmed", origin="user")
        project["couplings"][0]["edge_id"] = 999
        errors = validate(project, unit_index())[0]
        self.assertTrue(any("Reimport the source STEP" in e for e in errors), errors)

    def test_missing_and_stale_diagnostics_rejected(self):
        index = unit_index()
        index["edges"][0]["diagnostics"] = None
        project = unit_project(index, state="confirmed", origin="user")
        self.assertTrue(any("no usable" in e for e in validate(project, index)[0]))
        stale = unit_index({"geometry_sha256": "other"})
        project = unit_project(stale, state="confirmed", origin="user")
        self.assertTrue(any("different geometry" in e for e in validate(project, stale)[0]))

    def test_bad_gap_and_degenerate_side_rejected(self):
        bad = unit_index(copy.deepcopy(GAP_OK))
        bad["edges"][0]["diagnostics"]["max_paired_gap"] = float("nan")
        project = unit_project(bad, state="confirmed", origin="user")
        self.assertTrue(any("invalid max_paired_gap" in e for e in validate(project, bad)[0]))
        degenerate = unit_index(copy.deepcopy(GAP_OK))
        degenerate["edges"][0]["diagnostics"]["sides"][1]["degenerate"] = True
        project = unit_project(degenerate, state="confirmed", origin="user")
        self.assertTrue(any("usable trimming curve" in e for e in validate(project, degenerate)[0]))

    def test_pending_does_not_need_diagnostics(self):
        index = unit_index()
        index["edges"][0]["diagnostics"] = None
        project = unit_project(index, state="pending", origin="geometry")
        self.assertEqual(validate(project, index)[0], [])

    def test_migration_does_not_hide_contradictory_or_incomplete_records(self):
        mixed = migrate_project({"schema_version": 2, "couplings": [
            {"edge_id": 9, "state": "confirmed", "enabled": True, "penalty": 1e7}]})["couplings"][0]
        self.assertIn("enabled", mixed)          # not silently removed
        self.assertEqual(mixed["state"], "confirmed")
        project = unit_project(unit_index(), state="confirmed", origin="user")
        project["couplings"][0] = mixed
        self.assertTrue(any("single source of truth" in e for e in validate(project, unit_index())[0]))
        for incomplete in ({"edge_id": 9}, {"edge_id": 9, "enabled": "false"},
                           {"edge_id": 9, "enabled": 1}):
            record = migrate_project({"schema_version": 2, "couplings": [incomplete]})["couplings"][0]
            self.assertNotEqual(record.get("state"), "confirmed", record)
            project = unit_project(unit_index(), state="pending")
            project["couplings"] = [record]
            self.assertTrue(any("unknown state" in e for e in validate(project, unit_index())[0]), record)

    def test_evidence_face_ids_must_match_current_edge(self):
        wrong = unit_index(copy.deepcopy(GAP_OK))
        wrong["edges"][0]["diagnostics"]["sides"][1]["face_id"] = 99
        project = unit_project(wrong, state="confirmed", origin="user")
        self.assertTrue(any("does not match the current interface faces" in e
                            for e in validate(project, wrong)[0]))

    def test_unknown_parameter_correspondence_rejected(self):
        unknown = unit_index(copy.deepcopy(GAP_OK))
        unknown["edges"][0]["diagnostics"]["param_correspondence"] = "unknown"
        project = unit_project(unknown, state="confirmed", origin="user")
        self.assertTrue(any("parameter correspondence" in e for e in validate(project, unknown)[0]))

    def test_summary_is_sampled_and_has_no_mechanical_claim(self):
        text = summarize(9, GAP_OK)
        self.assertIn("sampled", text)
        for forbidden in ("weld", "hinge", "perfect", "full-edge"):
            self.assertNotIn(forbidden, text.lower())


class ConverterEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        folder = Path(cls.tmp.name)
        cls.split_step = write_strip_step(folder / "split.step", split=True)
        cls.single_step = write_strip_step(folder / "single.step", split=False)
        cls.reverse_step = write_strip_step(folder / "reverse.step", split=True, reverse_second=True)
        cls.split_cad, cls.split_index = converted(cls.split_step, folder / "cache_split")
        cls.single_cad, cls.single_index = converted(cls.single_step, folder / "cache_single")
        cls.reverse_cad, cls.reverse_index = converted(cls.reverse_step, folder / "cache_reverse")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_two_adjacent_faces_produce_one_interface(self):
        self.assertEqual(len(self.split_index["faces"]), 2)
        self.assertEqual(len(coupling_edges(self.split_index)), 1)

    def test_index_owns_both_sides_directions_and_gap(self):
        diagnostics = coupling_edges(self.split_index)[0]["diagnostics"]
        self.assertEqual(diagnostics["status"], "ok", diagnostics.get("detail"))
        self.assertEqual(diagnostics["geometry_sha256"], self.split_index["geometry_sha256"])
        self.assertGreaterEqual(diagnostics["sample_count"], 2)
        self.assertTrue(0.0 <= diagnostics["max_paired_gap"] <= diagnostics["tolerance"])
        self.assertTrue(0.0 <= diagnostics["max_curve_error"] <= diagnostics["tolerance"])
        sides = diagnostics["sides"]
        self.assertEqual(len(sides), 2)
        self.assertEqual([s["face_id"] for s in sides], coupling_edges(self.split_index)[0]["face_ids"])
        for side in sides:
            self.assertIsInstance(side["trim_index"], int)
            self.assertIn(side["curve_origin"], ("boundary_loop", "embedded_edge", "embedded_loop"))
            self.assertIn(side["direction"], ("forward", "reversed"))
            self.assertFalse(side["degenerate"])
        self.assertIn("sampled", diagnostics["note"])

    def test_confirmed_real_interface_passes_validation(self):
        project = make_project(self.split_step, self.split_index, state="confirmed")
        errors, _ = validate(project, self.split_index)
        self.assertEqual(errors, [])
        bundle = make_bundle(project, self.split_cad, self.split_index)
        self.assertEqual(len([u for u in bundle["physics.iga.json"]["element_condition_list"]
                              if u["geometry_type"] == "SurfaceEdgeSurfaceEdge"]), 1)

    def test_pending_real_interface_is_not_exported(self):
        project = make_project(self.split_step, self.split_index, state="pending")
        bundle = make_bundle(project, self.split_cad, self.split_index)
        self.assertEqual([u for u in bundle["physics.iga.json"]["element_condition_list"]
                          if u["geometry_type"] == "SurfaceEdgeSurfaceEdge"], [])

    def test_reversed_parameterisation_still_diagnosed(self):
        self.assertEqual(len(coupling_edges(self.reverse_index)), 1)
        diagnostics = coupling_edges(self.reverse_index)[0]["diagnostics"]
        self.assertEqual(diagnostics["status"], "ok", diagnostics.get("detail"))
        directions = [s["direction"] for s in diagnostics["sides"]]
        self.assertTrue(all(d in ("forward", "reversed") for d in directions))
        self.assertLessEqual(diagnostics["max_paired_gap"], diagnostics["tolerance"])


class KratosTwoPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        folder = Path(cls.tmp.name)
        cls.split_step = write_strip_step(folder / "split.step", split=True)
        cls.single_step = write_strip_step(folder / "single.step", split=False)
        cls.reverse_step = write_strip_step(folder / "reverse_solve.step", split=True, reverse_second=True)
        cls.split_cad, cls.split_index = converted(cls.split_step, folder / "cache_split")
        cls.single_cad, cls.single_index = converted(cls.single_step, folder / "cache_single")
        cls.reverse_cad, cls.reverse_index = converted(cls.reverse_step, folder / "cache_reverse_solve")
        cls.solves = {}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def solve(self, name, project, cad, index):
        if name in self.solves:
            return self.solves[name]
        case = Path(tempfile.mkdtemp(prefix=f"p0-2-{name}-", dir=self.tmp.name))
        export_bundle(project, cad, index, case)
        run = subprocess.run([sys.executable, str(case / "MainKratos.py")], cwd=str(case),
                             capture_output=True, text=True, timeout=90)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        report = json.loads((case / "run_report.json").read_text(encoding="utf-8"))
        self.solves[name] = report
        return report

    def test_two_patch_bending_matches_single_and_reference(self):
        single = self.solve("single_bend", make_project(self.single_step, self.single_index,
                                                        state="pending"),
                            self.single_cad, self.single_index)
        two = self.solve("two_bend", make_project(self.split_step, self.split_index,
                                                  state="confirmed", rotation=True),
                         self.split_cad, self.split_index)
        reference = bending_reference()
        self.assertLess(abs(single["max_abs_displacement"] - reference) / reference, 1.0e-4)
        self.assertLess(abs(two["max_abs_displacement"] - reference) / reference, 1.0e-4)
        self.assertLess(abs(two["max_abs_displacement"] - single["max_abs_displacement"]) / reference, 1.0e-4)

    def test_displacement_only_tension_is_stable(self):
        single = self.solve("single_tension",
                            make_project(self.single_step, self.single_index, state="pending",
                                         load=(P_TENSION, 0.0, 0.0)),
                            self.single_cad, self.single_index)
        two = self.solve("two_tension",
                         make_project(self.split_step, self.split_index, state="confirmed",
                                      rotation=False, load=(P_TENSION, 0.0, 0.0)),
                         self.split_cad, self.split_index)
        reference = tension_reference()
        for report in (single, two):
            self.assertLess(abs(report["max_abs_displacement"] - reference) / reference, 0.05)
        self.assertLess(abs(two["max_abs_displacement"] - single["max_abs_displacement"]) / reference, 0.01)

    def test_reversed_parameterisation_solve_matches_forward(self):
        forward = self.solve("two_bend_rev_ref",
                             make_project(self.split_step, self.split_index, state="confirmed",
                                          rotation=True), self.split_cad, self.split_index)
        reverse = self.solve("two_bend_reversed",
                             make_project(self.reverse_step, self.reverse_index, state="confirmed",
                                          rotation=True), self.reverse_cad, self.reverse_index)
        reference = bending_reference()
        self.assertLess(abs(forward["max_abs_displacement"] - reference) / reference, 1.0e-4)
        self.assertLess(abs(reverse["max_abs_displacement"] - reference) / reference, 1.0e-4)
        self.assertLess(abs(reverse["max_abs_displacement"] - forward["max_abs_displacement"]) / reference, 1.0e-4)

    def test_penalty_scan_is_finite_and_recorded(self):
        reference = bending_reference()
        values = {}
        for penalty in (1.0e4, 1.0e8):
            report = self.solve(f"two_bend_p{penalty:.0e}",
                                make_project(self.split_step, self.split_index, state="confirmed",
                                             rotation=True, penalty=penalty),
                                self.split_cad, self.split_index)
            tip = report["max_abs_displacement"]
            self.assertTrue(tip == tip and abs(tip) < float("inf"))
            # Loose sanity band only: high penalty degrades accuracy, so monotonic
            # accuracy must not be asserted.
            self.assertLess(abs(tip - reference) / reference, 0.05)
            values[penalty] = tip
        self.assertEqual(set(values), {1.0e4, 1.0e8})


class ParameterCorrespondenceTests(unittest.TestCase):
    """In-process unit tests for parameter handling; no OCP and no Kratos process."""

    @staticmethod
    def _plane():
        return {"is_trimmed": True, "is_rational": False, "degrees": [1, 1],
                "knot_vectors": [[0.0, 0.0, 1.0, 1.0], [0.0, 0.0, 1.0, 1.0]],
                "control_points": [[1, [0.0, 0.0, 0.0, 1.0]], [2, [1.0, 0.0, 0.0, 1.0]],
                                   [3, [0.0, 1.0, 0.0, 1.0]], [4, [1.0, 1.0, 0.0, 1.0]]]}

    @staticmethod
    def _face(brep_id, pcurve, trim_index=0):
        face = {"brep_id": brep_id, "swapped_surface_normal": False, "surface": ParameterCorrespondenceTests._plane(),
                "boundary_loops": [], "embedded_loops": [], "embedded_points": []}
        face["embedded_edges"] = [{"trim_index": trim_index, "curve_direction": True,
                                   "parameter_curve": pcurve}]
        return face

    def _run(self, side_a, side_b):
        import numpy as np
        from cad_pipeline import nurbs
        from kratos_iga import worker
        reference = {"degree": 1, "knot_vector": [0.0, 0.0, 1.0, 1.0], "active_range": [0.0, 1.0],
                     "control_points": [[90, [0.5, 0.0, 0.0, 1.0]], [91, [0.5, 1.0, 0.0, 1.0]]]}
        edge = {"brep_id": 9, "3d_curve": reference,
                "topology": [{"brep_id": 2, "trim_index": 0, "relative_direction": True},
                             {"brep_id": 4, "trim_index": 0, "relative_direction": True}]}
        faces = {2: self._face(2, side_a), 4: self._face(4, side_b)}
        return worker.coupling_diagnostics(faces, edge, nurbs, np, tolerance=1.0e-6, geometry_sha256="g")

    def test_uniform_reversed_pair_is_accepted(self):
        uniform_a = {"is_rational": False, "degree": 1, "knot_vector": [0.0, 0.0, 1.0, 1.0],
                     "active_range": [0.0, 1.0],
                     "control_points": [[10, [0.5, 0.0, 0.0, 1.0]], [11, [0.5, 1.0, 0.0, 1.0]]]}
        uniform_b = {"is_rational": False, "degree": 1, "knot_vector": [0.0, 0.0, 1.0, 1.0],
                     "active_range": [0.0, 1.0],
                     "control_points": [[20, [0.5, 1.0, 0.0, 1.0]], [21, [0.5, 0.0, 0.0, 1.0]]]}
        result = self._run(uniform_a, uniform_b)
        self.assertEqual(result["status"], "ok", result.get("detail"))
        self.assertEqual(result["param_correspondence"], "index_aligned_opposite_direction")
        self.assertEqual([s["direction"] for s in result["sides"]], ["forward", "reversed"])
        self.assertLessEqual(result["max_nearest_segment_gap"], result["tolerance"])

    def test_nonuniform_parameterization_is_reported_as_correspondence_not_gap(self):
        uniform = {"is_rational": False, "degree": 1, "knot_vector": [0.0, 0.0, 1.0, 1.0],
                   "active_range": [0.0, 1.0],
                   "control_points": [[10, [0.5, 0.0, 0.0, 1.0]], [11, [0.5, 1.0, 0.0, 1.0]]]}
        nonuniform = {"is_rational": False, "degree": 1, "knot_vector": [0.0, 0.0, 0.9, 1.0, 1.0],
                      "active_range": [0.0, 1.0],
                      "control_points": [[20, [0.5, 0.0, 0.0, 1.0]], [21, [0.5, 0.5, 0.0, 1.0]],
                                         [22, [0.5, 1.0, 0.0, 1.0]]]}
        result = self._run(uniform, nonuniform)
        # The two curves are geometrically the same segment, so the spatial
        # approximation stays tiny while the parameter pairing does not.
        self.assertLessEqual(result["max_nearest_segment_gap"], result["tolerance"])
        self.assertGreater(result["parameter_mismatch"], result["tolerance"])
        self.assertEqual(result["status"], "parameter_correspondence_mismatch")
        self.assertIn("unresolved", result["detail"])

    def test_coincident_and_missing_reference_are_not_ok(self):
        degenerate = {"is_rational": False, "degree": 1, "knot_vector": [0.0, 0.0, 1.0, 1.0],
                      "active_range": [0.0, 1.0],
                      "control_points": [[10, [0.5, 0.5, 0.0, 1.0]], [11, [0.5, 0.5, 0.0, 1.0]]]}
        good = {"is_rational": False, "degree": 1, "knot_vector": [0.0, 0.0, 1.0, 1.0],
                "active_range": [0.0, 1.0],
                "control_points": [[20, [0.5, 0.0, 0.0, 1.0]], [21, [0.5, 1.0, 0.0, 1.0]]]}
        result = self._run(degenerate, good)
        self.assertNotEqual(result["status"], "ok")
        self.assertTrue(any(s["degenerate"] for s in result["sides"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
