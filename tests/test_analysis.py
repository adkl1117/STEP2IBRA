"""Contract and real Kratos regression tests using only distributed fixtures."""
from copy import deepcopy
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"freecad/Mod/KratosIBRA"))
sys.path.insert(0, str(ROOT/"freecad/Mod/KratosIBRA/backend"))
from kratos_iga.project import read_json, validate
from kratos_iga.bundle import make_bundle, export_bundle


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from kratos_iga.project import read_project
        from cad_pipeline.api import convert_step
        from kratos_iga.worker import build_index
        cls.base = read_project(ROOT/"freecad/Mod/KratosIBRA/examples/cantilever/cantilever.ibra-project.json")
        cls.cad, report = convert_step(Path(cls.base["source_step"]))
        with tempfile.TemporaryDirectory() as folder:
            cls.index = build_index(cls.cad, report, Path(folder))

    def test_geometry_unchanged_in_mm(self):
        result = make_bundle(self.base, self.cad, self.index)
        self.assertEqual(result["geometry.cad.json"], self.cad)
        self.assertEqual(result["refinements.iga.json"]["refinements"][0]["parameters"]["increase_degree_u"], 2)

    def test_metres_scale_only_physical_coordinates(self):
        p = deepcopy(self.base)
        p["units"] = "m-N-s"
        geometry = make_bundle(p, self.cad, self.index)["geometry.cad.json"]
        surface = geometry["breps"][0]["faces"][0]["surface"]
        self.assertAlmostEqual(max(c[1][0] for c in surface["control_points"]), .01)
        self.assertEqual(surface["knot_vectors"], self.cad["breps"][0]["faces"][0]["surface"]["knot_vectors"])
        self.assertEqual(geometry["breps"][0]["faces"][0]["boundary_loops"], self.cad["breps"][0]["faces"][0]["boundary_loops"])

    def test_reject_partial_penalty_and_bad_intervals(self):
        p = deepcopy(self.base)
        p["supports"][0].update(method="penalty", value=[0.,None,0.], clamp=False, interval=[.2,.8])
        errors, _ = validate(p, self.index)
        self.assertTrue(any("constrain all XYZ" in e for e in errors))
        self.assertTrue(any("entire analysis interval" in e for e in errors))

    def test_reject_trimmed_strong_edge(self):
        index = deepcopy(self.index)
        index["edges"][0]["strong_local"] = None
        errors, _ = validate(self.base, index)
        self.assertTrue(any("complete natural" in e for e in errors))

    def test_reject_bad_data(self):
        p = deepcopy(self.base)
        p["materials"][0]["young"] = float("nan")
        p["patches"][0]["degree_u"] = 1
        p["loads"][0]["target_id"] = 999999
        p["source_sha256"] = "changed"
        self.assertGreaterEqual(len(validate(p, self.index)[0]), 4)

    def test_reject_static_velocity(self):
        p = deepcopy(self.base)
        p["initial_conditions"] = [dict(name="v0", target_kind="face", target_id=2, variable="VELOCITY", value=[0.,0.,1.])]
        self.assertTrue(any("Static analysis" in e for e in validate(p, self.index)[0]))

    def test_reject_c0_shell_after_elevation(self):
        index = deepcopy(self.index)
        index["faces"][0]["min_continuity"] = [0,99]
        self.assertTrue(any("C¹" in e for e in validate(self.base, index)[0]))

    def test_reject_changed_profile_geometry(self):
        p = deepcopy(self.base)
        p["geometry_sha256"] = "changed"
        self.assertTrue(any("Converted geometry" in e for e in validate(p, self.index)[0]))

    def test_export_preserves_existing_case(self):
        with tempfile.TemporaryDirectory(dir=ROOT/"tests") as tmp:
            sentinel = Path(tmp)/"existing.txt"
            sentinel.write_text("keep")
            with self.assertRaises(ValueError):
                export_bundle(self.base, self.cad, self.index, tmp)
            self.assertEqual(sentinel.read_text(), "keep")

    def test_cantilever_analytical_solution(self):
        with tempfile.TemporaryDirectory(dir=ROOT/"tests") as tmp:
            folder = Path(tmp)/"case"
            export_bundle(self.base, self.cad, self.index, folder)
            result = subprocess.run([sys.executable, str(folder/"MainKratos.py")], capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            report = read_json(folder/"run_report.json")
            expected = .001 * 10**3 / (3 * 200000 * (1 * .1**3 / 12))
            self.assertAlmostEqual(report["max_abs_displacement"], expected, delta=expected*1e-6)
            self.assertTrue((folder/"results.vtkhdf").is_file())
            from kratos_iga.project import read_project
            snapshot = read_project(folder/"project.ibra-project.json")
            self.assertEqual(snapshot["source_step"], self.base["source_step"])
            self.assertFalse(Path(read_json(folder/"project.ibra-project.json")["source_step"]).is_absolute())

    def test_dynamic_initial_field(self):
        p = deepcopy(self.base)
        p["solver"].update(type="dynamic", analysis_type="non_linear", end_time=.02, time_step=.01)
        p["loads"] = []
        p["initial_conditions"] = [dict(name="v0", target_kind="face", target_id=2, variable="VELOCITY", value=[0.,0.,.001])]
        p["output"]["vtk"] = False
        with tempfile.TemporaryDirectory(dir=ROOT/"tests") as tmp:
            folder = Path(tmp)/"case"
            export_bundle(p, self.cad, self.index, folder)
            result = subprocess.run([sys.executable, str(folder/"MainKratos.py")], capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            report = read_json(folder/"run_report.json")
            self.assertTrue(math.isfinite(report["max_abs_displacement"]))
            self.assertGreater(report["max_abs_displacement"], 0)

    def test_initial_once_and_timed_load_clear(self):
        import KratosMultiphysics as KM
        path = ROOT/"freecad/Mod/KratosIBRA/kratos_iga/runtime/ibra_case_process.py"
        spec = importlib.util.spec_from_file_location("ibra_case_process", path)
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)
        model = KM.Model()
        part = model.CreateModelPart("Test")
        part.AddNodalSolutionStepVariable(KM.DISPLACEMENT)
        part.SetBufferSize(2)
        node = part.CreateNewNode(1, 0, 0, 0)
        prop = part.CreateNewProperties(0)
        part.CreateNewNode(2, 1, 0, 0)
        condition = part.CreateNewCondition("LineCondition2D2N", 1, [1,2], prop)
        initial = runtime.CaseProcess(model, KM.Parameters(json.dumps(dict(mode="initial", model_part_name="Test", variable_name="DISPLACEMENT", value=[.2,None,0.]))))
        initial.ExecuteInitialize()
        self.assertEqual(node.GetSolutionStepValue(KM.DISPLACEMENT_X), .2)
        node.SetSolutionStepValue(KM.DISPLACEMENT_X, .7)
        initial.ExecuteInitializeSolutionStep()
        self.assertEqual(node.GetSolutionStepValue(KM.DISPLACEMENT_X), .7)
        load = runtime.CaseProcess(model, KM.Parameters(json.dumps(dict(mode="load", model_part_name="Test", variable_name="DISPLACEMENT", value=[1.,2.,3.], interval=[.2,.5]))))
        part.ProcessInfo[KM.TIME] = .3
        load.ExecuteInitializeSolutionStep()
        self.assertEqual(list(condition.GetValue(KM.DISPLACEMENT)), [1.,2.,3.])
        part.ProcessInfo[KM.TIME] = .6
        load.ExecuteInitializeSolutionStep()
        self.assertEqual(list(condition.GetValue(KM.DISPLACEMENT)), [0.,0.,0.])


if __name__ == "__main__":
    unittest.main(verbosity=2)
