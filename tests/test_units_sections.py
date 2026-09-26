"""P0-1 contract tests: schema-2 migration, sections, and atomic unit conversion.

Pure-Python tests run without Kratos. ``RuntimeEquivalenceTests`` exports and solves
the distributed cantilever fixture in mm-N-s and m-N-s and compares the two models in
common units; it is skipped automatically when the geometry backend is unavailable.
"""
from copy import deepcopy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"freecad/Mod/KratosIBRA"))
sys.path.insert(0, str(ROOT/"freecad/Mod/KratosIBRA/backend"))
from kratos_iga.project import (SCHEMA, convert_project_units, migrate_project,
                                new_project, read_json, read_project, validate)
from kratos_iga.bundle import make_bundle, export_bundle

EXAMPLE = ROOT/"freecad/Mod/KratosIBRA/examples/cantilever/cantilever.ibra-project.json"


def synthetic_index(source_sha256, face_ids=(1,)):
    return {"source_sha256": source_sha256, "geometry_sha256": None,
            "faces": [{"id": fid, "degrees": [1, 1], "min_continuity": [9, 9]} for fid in face_ids],
            "edges": []}


def synthetic_project(face_ids=(1,)):
    return {"schema_version": SCHEMA, "source_step": "", "source_sha256": "abc", "profile": "",
            "units": "mm-N-s",
            "materials": [{"id": 1, "name": "Steel", "young": 200000.0, "poisson": 0.0, "density": 7.85e-9}],
            "sections": [{"id": 1, "name": "thin", "thickness": 0.1},
                         {"id": 2, "name": "thick", "thickness": 0.5}],
            "refinement_enabled": False,
            "patches": [{"face_id": fid, "material_id": 1, "section_id": (i % 2) + 1,
                         "element": "IgaMembraneElement", "degree_u": 1, "degree_v": 1,
                         "insert_u": 0, "insert_v": 0, "quadrature": 0}
                        for i, fid in enumerate(face_ids)],
            "supports": [], "loads": [], "initial_conditions": [], "couplings": [],
            "solver": {"type": "static", "analysis_type": "linear", "start_time": 0.0,
                       "end_time": 1.0, "time_step": 1.0},
            "output": {"vtk": False, "refinement": 4}}


class MigrationTests(unittest.TestCase):
    def test_mm_legacy_migrates_without_rescaling(self):
        raw = read_json(EXAMPLE)
        self.assertEqual(raw["schema_version"], 1)
        self.assertIn("thickness", raw["materials"][0])
        migrated = migrate_project(raw)
        self.assertEqual(migrated["schema_version"], SCHEMA)
        self.assertNotIn("thickness", migrated["materials"][0])
        self.assertEqual(migrated["sections"], [{"id": 1, "name": "Steel", "thickness": 0.1}])
        self.assertEqual(migrated["patches"][0]["section_id"], migrated["patches"][0]["material_id"])
        self.assertEqual(migrated["materials"][0]["young"], 200000.0)
        self.assertEqual(migrated["materials"][0]["density"], 7.85e-9)
        self.assertEqual(migrated["loads"][0]["value"], [0.0, 0.0, -0.001])
        # The source document is never mutated by migration.
        self.assertIn("thickness", raw["materials"][0])

    def test_metre_legacy_migrates_without_rescaling(self):
        raw = read_json(EXAMPLE)
        raw["units"] = "m-N-s"
        migrated = migrate_project(raw)
        self.assertEqual(migrated["units"], "m-N-s")
        self.assertEqual(migrated["materials"][0]["young"], 200000.0)
        self.assertEqual(migrated["materials"][0]["density"], 7.85e-9)
        self.assertEqual(migrated["sections"][0]["thickness"], 0.1)
        self.assertEqual(migrated["loads"][0]["value"], [0.0, 0.0, -0.001])

    def test_read_project_migrates_example(self):
        project = read_project(EXAMPLE)
        self.assertEqual(project["schema_version"], SCHEMA)
        self.assertEqual(project["sections"][0]["thickness"], 0.1)
        self.assertNotIn("thickness", project["materials"][0])

    def test_migration_is_idempotent(self):
        once = read_project(EXAMPLE)
        twice = migrate_project(once)
        self.assertEqual(once, twice)

    def test_new_project_has_sections(self):
        project = new_project()
        self.assertEqual(project["schema_version"], SCHEMA)
        self.assertNotIn("thickness", project["materials"][0])
        self.assertEqual(project["sections"][0]["thickness"], 1.0)
        self.assertEqual(project["patches"], [])

    def test_make_bundle_accepts_legacy_schema_without_mutating(self):
        legacy = {"schema_version": 1, "source_step": "", "source_sha256": "abc", "profile": "",
                  "units": "mm-N-s",
                  "materials": [{"id": 1, "name": "Steel", "young": 200000.0, "poisson": 0.0,
                                 "density": 7.85e-9, "thickness": 0.1}],
                  "refinement_enabled": False,
                  "patches": [{"face_id": 1, "material_id": 1, "element": "IgaMembraneElement",
                               "degree_u": 1, "degree_v": 1, "insert_u": 0, "insert_v": 0, "quadrature": 0}],
                  "supports": [], "loads": [], "initial_conditions": [], "couplings": [],
                  "solver": {"type": "static", "analysis_type": "linear", "start_time": 0.0,
                             "end_time": 1.0, "time_step": 1.0},
                  "output": {"vtk": False, "refinement": 4}}
        bundle = make_bundle(legacy, {"breps": [], "tolerances": {"model_tolerance": 0.001}},
                             synthetic_index("abc", (1,)))
        variables = bundle["Materials.json"]["properties"][0]["Material"]["Variables"]
        self.assertEqual(variables["THICKNESS"], 0.1)
        self.assertEqual(variables["YOUNG_MODULUS"], 200000.0)
        self.assertIn("thickness", legacy["materials"][0])  # input untouched


class SectionTests(unittest.TestCase):
    def test_same_material_with_independent_sections(self):
        index = synthetic_index("abc", (1, 2))
        project = synthetic_project((1, 2))
        bundle = make_bundle(project, {"breps": [], "tolerances": {"model_tolerance": 0.001}}, index)
        thickness = {entry["model_part_name"]: entry["Material"]["Variables"]["THICKNESS"]
                     for entry in bundle["Materials.json"]["properties"]}
        self.assertEqual(thickness["IgaModelPart.Patch_1"], 0.1)
        self.assertEqual(thickness["IgaModelPart.Patch_2"], 0.5)
        modulus = {entry["Material"]["Variables"]["YOUNG_MODULUS"]
                   for entry in bundle["Materials.json"]["properties"]}
        self.assertEqual(modulus, {200000.0})

    def test_undefined_section_is_rejected(self):
        index = synthetic_index("abc", (1,))
        project = synthetic_project((1,))
        project["patches"][0]["section_id"] = 99
        errors, _ = validate(project, index)
        self.assertTrue(any("section ID" in e for e in errors))

    def test_nonpositive_section_thickness_is_rejected(self):
        index = synthetic_index("abc", (1,))
        project = synthetic_project((1,))
        project["sections"][0]["thickness"] = 0.0
        errors, _ = validate(project, index)
        self.assertTrue(any("thickness" in e for e in errors))


class UnitConversionTests(unittest.TestCase):
    def setUp(self):
        self.mm = read_project(EXAMPLE)

    def test_mm_to_m_factors(self):
        m = convert_project_units(self.mm, "m-N-s")
        self.assertEqual(m["units"], "m-N-s")
        self.assertEqual(m["materials"][0]["young"], 200000.0 * 1e6)
        self.assertAlmostEqual(m["materials"][0]["density"], 7.85e-9 * 1e12)
        self.assertEqual(m["materials"][0]["poisson"], 0.0)
        self.assertAlmostEqual(m["sections"][0]["thickness"], 0.1 * 1e-3)
        self.assertAlmostEqual(m["loads"][0]["value"][2], -0.001 * 1e3)
        self.assertEqual(m["supports"][0]["value"], [0.0, 0.0, 0.0])

    def test_conversion_does_not_touch_input(self):
        before = deepcopy(self.mm)
        convert_project_units(self.mm, "m-N-s")
        self.assertEqual(self.mm, before)

    def test_round_trip_is_reversible(self):
        m = convert_project_units(self.mm, "m-N-s")
        back = convert_project_units(m, "mm-N-s")
        self.assertEqual(back["units"], "mm-N-s")
        self.assertAlmostEqual(back["materials"][0]["young"], self.mm["materials"][0]["young"], delta=1e-6)
        self.assertAlmostEqual(back["materials"][0]["density"], self.mm["materials"][0]["density"], delta=1e-20)
        self.assertAlmostEqual(back["sections"][0]["thickness"], self.mm["sections"][0]["thickness"], delta=1e-12)
        self.assertAlmostEqual(back["loads"][0]["value"][2], self.mm["loads"][0]["value"][2], delta=1e-12)

    def test_same_units_returns_equal_copy(self):
        same = convert_project_units(self.mm, "mm-N-s")
        self.assertEqual(same, self.mm)
        self.assertIsNot(same, self.mm)

    def test_unknown_units_rejected(self):
        with self.assertRaises(ValueError):
            convert_project_units(self.mm, "cm-kg-s")

    def test_rotational_coupling_blocks_conversion_atomically(self):
        project = deepcopy(self.mm)
        project["couplings"] = [{"edge_id": 3, "enabled": True, "penalty": 1e7, "rotation": True}]
        before = deepcopy(project)
        with self.assertRaises(ValueError) as context:
            convert_project_units(project, "m-N-s")
        self.assertIn("rotational coupling", str(context.exception))
        self.assertEqual(project, before)

    def test_displacement_only_coupling_converts_penalty(self):
        project = deepcopy(self.mm)
        project["couplings"] = [{"edge_id": 3, "enabled": True, "penalty": 1e7, "rotation": False}]
        converted = convert_project_units(project, "m-N-s")
        self.assertEqual(converted["couplings"][0]["penalty"], 1e7 * 1e6)

    def test_unknown_load_variable_rejected_atomically(self):
        project = deepcopy(self.mm)
        project["loads"][0]["variable"] = "MOMENT"
        before = deepcopy(project)
        with self.assertRaises(ValueError):
            convert_project_units(project, "m-N-s")
        self.assertEqual(project, before)

    def test_dead_load_scales_by_target_measure(self):
        base = {"schema_version": SCHEMA, "units": "mm-N-s", "materials": [], "sections": [],
                "supports": [], "initial_conditions": [], "couplings": [], "loads": [
                    {"variable": "DEAD_LOAD", "target_kind": "face", "value": [0.0, 0.0, -3.0]}]}
        face = convert_project_units(base, "m-N-s")["loads"][0]["value"]
        self.assertAlmostEqual(face[2], -3.0 * 1e6)
        base["loads"][0]["target_kind"] = "edge"
        edge = convert_project_units(base, "m-N-s")["loads"][0]["value"]
        self.assertAlmostEqual(edge[2], -3.0 * 1e3)

    def test_initial_fields_scale_with_length(self):
        base = {"schema_version": SCHEMA, "units": "mm-N-s", "materials": [], "sections": [],
                "supports": [], "loads": [], "couplings": [],
                "initial_conditions": [{"variable": "DISPLACEMENT", "target_kind": "face", "value": [1.0, None, 0.0]},
                                       {"variable": "VELOCITY", "target_kind": "face", "value": [0.0, 2.0, 0.0]},
                                       {"variable": "ACCELERATION", "target_kind": "face", "value": [0.0, 0.0, 3.0]}]}
        converted = convert_project_units(base, "m-N-s")["initial_conditions"]
        self.assertEqual(converted[0]["value"], [0.001, None, 0.0])
        self.assertEqual(converted[1]["value"], [0.0, 0.002, 0.0])
        self.assertAlmostEqual(converted[2]["value"][2], 0.003)

    def test_stored_penalty_scales_for_every_method(self):
        base = {"schema_version": SCHEMA, "units": "mm-N-s", "materials": [], "sections": [],
                "loads": [], "initial_conditions": [], "couplings": [],
                "supports": [{"method": "penalty", "value": [0.0, 0.0, 0.0], "penalty": 1e7},
                             {"method": "strong", "value": [0.0, 0.0, 0.0], "penalty": 1e7}]}
        converted = convert_project_units(base, "m-N-s")["supports"]
        # The stored factor keeps its physical meaning if the method is switched later.
        self.assertEqual(converted[0]["penalty"], 1e7 * 1e6)
        self.assertEqual(converted[1]["penalty"], 1e7 * 1e6)

    def test_schema1_conversion_migrates_material_thickness(self):
        raw = read_json(EXAMPLE)
        self.assertEqual(raw["schema_version"], 1)
        converted = convert_project_units(raw, "m-N-s")
        self.assertEqual(converted["schema_version"], SCHEMA)
        self.assertNotIn("thickness", converted["materials"][0])
        self.assertAlmostEqual(converted["sections"][0]["thickness"], 0.1 * 1e-3)
        self.assertEqual(converted["materials"][0]["young"], 200000.0 * 1e6)
        self.assertAlmostEqual(converted["loads"][0]["value"][2], -0.001 * 1e3)
        self.assertEqual(raw["schema_version"], 1)  # input untouched
        self.assertEqual(raw["materials"][0]["thickness"], 0.1)

    def test_schema1_same_unit_still_migrates(self):
        raw = read_json(EXAMPLE)
        same = convert_project_units(raw, "mm-N-s")
        self.assertEqual(same["schema_version"], SCHEMA)
        self.assertEqual(same["sections"][0]["thickness"], 0.1)
        self.assertEqual(same["materials"][0]["young"], 200000.0)
        self.assertEqual(raw["schema_version"], 1)

    def test_missing_schema_rejected(self):
        with self.assertRaises(ValueError):
            convert_project_units({"units": "mm-N-s"}, "m-N-s")

    def test_unsupported_schema_rejected(self):
        with self.assertRaises(ValueError):
            convert_project_units({"schema_version": 99, "units": "mm-N-s"}, "m-N-s")

    def test_overflow_rejected_atomically(self):
        project = deepcopy(self.mm)
        project["materials"][0]["density"] = 1e300  # x1e12 overflows
        before = deepcopy(project)
        with self.assertRaises(ValueError) as context:
            convert_project_units(project, "m-N-s")
        self.assertIn("overflow", str(context.exception))
        self.assertEqual(project, before)

    def test_line_load_wrong_target_rejected(self):
        project = deepcopy(self.mm)
        project["loads"][0].update(variable="LINE_LOAD", target_kind="face")
        before = deepcopy(project)
        with self.assertRaises(ValueError):
            convert_project_units(project, "m-N-s")
        self.assertEqual(project, before)

    def test_initial_field_wrong_target_rejected(self):
        project = deepcopy(self.mm)
        project["initial_conditions"] = [
            {"variable": "DISPLACEMENT", "target_kind": "edge", "value": [0.0, 0.0, 0.0]}]
        before = deepcopy(project)
        with self.assertRaises(ValueError):
            convert_project_units(project, "m-N-s")
        self.assertEqual(project, before)


class RuntimeEquivalenceTests(unittest.TestCase):
    """Real Kratos check: the mm and m models must describe the same cantilever.

    Skipped when the OCP geometry backend or the Kratos runtime is unavailable.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from cad_pipeline.api import convert_step
            from kratos_iga.worker import build_index
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"geometry backend unavailable: {exc}")
        cls.base = read_project(EXAMPLE)
        cls.cad, report = convert_step(Path(cls.base["source_step"]))
        cls._tmp = tempfile.TemporaryDirectory()
        cls.index = build_index(cls.cad, report, Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def solve(self, project):
        folder = Path(tempfile.mkdtemp(dir=self._tmp.name))/"case"
        export_bundle(project, self.cad, self.index, folder)
        run = subprocess.run([sys.executable, str(folder/"MainKratos.py")],
                             capture_output=True, text=True, timeout=90)
        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
        report = read_json(folder/"run_report.json")
        materials = (folder/"Materials.json").read_text(encoding="utf-8")
        return report, json.loads(materials)["properties"]

    def test_metre_model_matches_millimetre_model(self):
        mm_report, mm_materials = self.solve(self.base)
        metre = convert_project_units(self.base, "m-N-s")
        m_report, m_materials = self.solve(metre)

        expected_mm = 0.02
        mm_value = mm_report["max_abs_displacement"]
        m_value = m_report["max_abs_displacement"]
        self.assertAlmostEqual(mm_value, expected_mm, delta=expected_mm*1e-6)
        # Compare both models in common units with one shared relative tolerance,
        # so the metre case is not checked with a 1000x looser millimetre delta.
        self.assertAlmostEqual(m_value*1e3, mm_value, delta=expected_mm*1e-6)
        self.assertAlmostEqual(m_value, expected_mm*1e-3, delta=expected_mm*1e-3*1e-6)

        mm_vars = {e["model_part_name"]: e["Material"]["Variables"] for e in mm_materials}
        m_vars = {e["model_part_name"]: e["Material"]["Variables"] for e in m_materials}
        self.assertEqual(mm_vars["IgaModelPart.Patch_2"]["THICKNESS"], 0.1)
        self.assertAlmostEqual(m_vars["IgaModelPart.Patch_2"]["THICKNESS"], 1e-4)
        self.assertEqual(mm_vars["IgaModelPart.Patch_2"]["YOUNG_MODULUS"], 200000.0)
        self.assertEqual(m_vars["IgaModelPart.Patch_2"]["YOUNG_MODULUS"], 2.0e11)
        self.assertAlmostEqual(mm_vars["IgaModelPart.Patch_2"]["DENSITY"], 7.85e-9)
        self.assertAlmostEqual(m_vars["IgaModelPart.Patch_2"]["DENSITY"], 7850.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
