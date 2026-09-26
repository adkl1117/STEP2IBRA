"""P0-4 preprocessing statistics tests (pure Python, no OCP, no Kratos).

They cover the pre-export diagnostics, the independent source-area expectations and
the additive export_report block. The real Kratos area comparison lives in the
bounded host runner ``runtime/p0-4-review/backend_area_matrix.py`` because it needs a
separate OCP and Kratos process.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "freecad/Mod/KratosIBRA"))

from kratos_iga import inspection, worker  # noqa: E402
from kratos_iga.bundle import make_bundle  # noqa: E402
from kratos_iga.project import new_project  # noqa: E402


def synthetic_cad(weight=1.0, knots_u=(-5.0, -5.0, 5.0, 5.0), knots_v=(-2.0, -2.0, 2.0, 2.0),
                  degrees=(1, 1)):
    return {"breps": [{"faces": [{
        "brep_id": 2,
        "surface": {"degrees": list(degrees), "is_rational": False,
                    "knot_vectors": [list(knots_u) if isinstance(knots_u, (list, tuple)) else knots_u,
                                     list(knots_v) if isinstance(knots_v, (list, tuple)) else knots_v],
                    "control_points": [[1, [0.0, 0.0, 0.0, weight]],
                                       [2, [10.0, 0.0, 0.0, weight]],
                                       [3, [0.0, 4.0, 0.0, weight]],
                                       [4, [10.0, 4.0, 0.0, weight]]]},
        "boundary_loops": [{"loop_type": "outer", "trimming_curves": [{
            "trim_index": 0,
            "parameter_curve": {"degree": 1, "is_rational": False,
                                "knot_vector": [0.0, 0.0, 1.0, 1.0],
                                "control_points": [[0, [0.0, 0.0, 0.0, 1.0]],
                                                   [1, [1.0, 1.0, 0.0, 1.0]]]}}]}],
    }]}]}


def synthetic_index(measured=True):
    face = {"id": 2, "degrees": [1, 1], "min_continuity": [99, 99],
            "control_point_counts": {"u": 2, "v": 2, "total": 4},
            "knot_spans": {"u": 1, "v": 1}}
    if measured:
        face["source_area_mm2"] = 32.0
    return {"source_sha256": "src", "geometry_sha256": "geo", "faces": [face], "edges": []}


def synthetic_project(units="mm-N-s"):
    project = new_project()
    project.update({"source_step": "x.step", "source_sha256": "src", "geometry_sha256": "geo",
                    "units": units, "refinement_enabled": True})
    project["patches"] = [{"face_id": 2, "material_id": 1, "section_id": 1,
                           "element": "Shell3pElement", "degree_u": 2, "degree_v": 2,
                           "insert_u": 1, "insert_v": 1, "quadrature": 0, "active": True}]
    project["supports"] = []
    project["loads"] = []
    project["output"] = {"vtk": False, "refinement": 4}
    return project


class PositiveSpanTests(unittest.TestCase):
    def test_repeated_end_knots_are_one_span(self):
        self.assertEqual(inspection.positive_span_count([-5.0, -5.0, 5.0, 5.0]), 1)

    def test_repeated_interior_knots_are_valid_multiplicity(self):
        self.assertEqual(inspection.positive_span_count([-5.0, -5.0, -2.0, 0.0, 5.0, 5.0]), 3)

    def test_all_equal_knots_have_no_positive_span(self):
        self.assertEqual(inspection.positive_span_count([-5.0, -5.0, -5.0, -5.0]), 0)

    def test_malformed_vectors_return_none(self):
        self.assertIsNone(inspection.positive_span_count([0.0, float("nan"), 1.0]))
        self.assertIsNone(inspection.positive_span_count([0.0, 1.0, 0.5]))


class CadDiagnosticsTests(unittest.TestCase):
    def test_repeated_knots_are_reported_not_rejected(self):
        diagnostics = inspection.cad_diagnostics(
            synthetic_cad(knots_u=(-5.0, -5.0, -2.0, 0.0, 5.0, 5.0)))
        self.assertEqual(diagnostics["errors"], [])
        self.assertTrue(diagnostics["repeated_knots_present"])
        face = diagnostics["faces"]["2"]
        self.assertEqual(face["positive_knot_spans"], [3, 1])
        self.assertEqual(face["repeated_knots"]["u"], {"-5.0": 2, "5.0": 2})

    def test_valid_data_has_no_errors(self):
        self.assertEqual(inspection.cad_diagnostics(synthetic_cad())["errors"], [])

    def test_non_finite_and_non_positive_weights_are_rejected(self):
        for weight in (float("nan"), 0.0, -1.0):
            errors = inspection.cad_diagnostics(synthetic_cad(weight=weight))["errors"]
            self.assertTrue(any("weight" in error for error in errors), errors)

    def test_non_finite_knot_is_rejected(self):
        errors = inspection.cad_diagnostics(
            synthetic_cad(knots_u=(-5.0, -5.0, float("inf"), 5.0, 5.0)))["errors"]
        self.assertTrue(any("non-finite" in error for error in errors), errors)

    def test_no_positive_span_is_rejected(self):
        errors = inspection.cad_diagnostics(
            synthetic_cad(knots_u=(-5.0, -5.0, -5.0, -5.0)))["errors"]
        self.assertTrue(any("positive-length span" in error for error in errors), errors)

    def test_bad_degree_is_rejected(self):
        errors = inspection.cad_diagnostics(synthetic_cad(degrees=(0, 1)))["errors"]
        self.assertTrue(any("positive integers" in error for error in errors), errors)

    def test_diagnostics_do_not_modify_cad(self):
        cad = synthetic_cad()
        before = copy.deepcopy(cad)
        inspection.cad_diagnostics(cad)
        self.assertEqual(cad, before)


class SourceAreaExpectationTests(unittest.TestCase):
    def test_measured_area_is_scaled_by_length_squared(self):
        mm = inspection.source_area_expectations(synthetic_project("mm-N-s"),
                                                 synthetic_index(measured=True))[0]
        m = inspection.source_area_expectations(synthetic_project("m-N-s"),
                                                synthetic_index(measured=True))[0]
        self.assertEqual(mm["state"], "measured")
        self.assertEqual(mm["source_area"], 32.0)
        self.assertEqual(m["source_area"], 32.0e-6)
        self.assertAlmostEqual(m["source_area"] / mm["source_area"], 1.0e-6)

    def test_legacy_cache_is_unknown_and_never_fabricated(self):
        entry = inspection.source_area_expectations(synthetic_project(),
                                                    synthetic_index(measured=False))[0]
        self.assertEqual(entry["state"], "unknown")
        self.assertTrue(entry["reimport_required"])
        self.assertIsNone(entry["source_area"])


class PreprocessingReportTests(unittest.TestCase):
    def test_analysis_quadrature_and_display_sampling_are_separate(self):
        report = inspection.preprocessing_report(synthetic_project(), synthetic_cad(),
                                                 synthetic_index())
        patch = report["patches"][0]
        self.assertEqual(patch["original_degree"], [1, 1])
        self.assertEqual(patch["target_degree"], [2, 2])
        self.assertEqual(patch["insert_per_span"], [1, 1])
        self.assertEqual(patch["final_grid_control_points"], [4, 4])
        self.assertTrue(patch["final_grid_is_estimate"])
        self.assertEqual(patch["requested_quadrature_per_span"], 0)
        self.assertEqual(report["display_sampling"]["refinement"], 4)
        self.assertNotIn("refinement", report["quadrature"])
        self.assertNotEqual(report["display_sampling"]["refinement"],
                            patch["requested_quadrature_per_span"])

    def test_legacy_index_reports_unknown_not_verified(self):
        report = inspection.preprocessing_report(synthetic_project(), synthetic_cad(),
                                                 synthetic_index(measured=False))
        self.assertEqual(report["source_area"]["state"], "unknown")
        self.assertTrue(report["source_area"]["reimport_required"])
        self.assertIsNone(report["patches"][0]["source_area"])
        self.assertEqual(report["patches"][0]["source_area_state"], "unknown")


class BundleReportTests(unittest.TestCase):
    def test_export_report_preserves_old_fields_and_adds_preprocessing(self):
        report = make_bundle(synthetic_project(), synthetic_cad(),
                             synthetic_index())["export_report.json"]
        for key in ("errors", "warnings", "runtime_target", "source_sha256", "geometry_units",
                    "active_patches", "total_patches", "groups", "excluded_from_export", "note"):
            self.assertIn(key, report)
        self.assertEqual(report["active_patches"], [2])
        self.assertEqual(report["runtime_target"], "Kratos 10.4.3")
        self.assertEqual(report["preprocessing"]["source_area"]["state"], "measured")
        self.assertEqual(report["preprocessing"]["patches"][0]["source_area"], 32.0)

    def test_legacy_cache_exports_unknown_source_area(self):
        report = make_bundle(synthetic_project(), synthetic_cad(),
                             synthetic_index(measured=False))["export_report.json"]
        self.assertEqual(report["preprocessing"]["source_area"]["state"], "unknown")
        self.assertTrue(report["preprocessing"]["source_area"]["reimport_required"])
        self.assertIsNone(report["preprocessing"]["patches"][0]["source_area"])

    def test_malformed_cad_is_rejected_and_not_repaired(self):
        cad = synthetic_cad(weight=float("nan"))
        before = copy.deepcopy(cad)
        with self.assertRaises(ValueError) as context:
            make_bundle(synthetic_project(), cad, synthetic_index())
        self.assertIn("malformed", str(context.exception))
        self.assertEqual(cad, before)


class SourceFaceAreaGuardTests(unittest.TestCase):
    def _install_fake_ocp(self, area):
        saved = {name: sys.modules.get(name) for name in ("OCP", "OCP.GProp", "OCP.BRepGProp")}

        class FakeProperties:
            def __init__(self):
                self.mass = 0.0

            def Mass(self):
                return self.mass

        class FakeBRepGProp:
            @staticmethod
            def SurfaceProperties_s(_shape, properties):
                properties.mass = area

        gprop = types.ModuleType("OCP.GProp")
        gprop.GProp_GProps = FakeProperties
        brepgprop = types.ModuleType("OCP.BRepGProp")
        brepgprop.BRepGProp = FakeBRepGProp
        sys.modules["OCP"] = types.ModuleType("OCP")
        sys.modules["OCP.GProp"] = gprop
        sys.modules["OCP.BRepGProp"] = brepgprop

        def restore():
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.addCleanup(restore)

    def test_finite_positive_area_is_returned(self):
        self._install_fake_ocp(32.0)
        self.assertEqual(worker.source_face_area_mm2(object(), 2), 32.0)

    def test_non_finite_or_non_positive_area_raises(self):
        for area in (float("nan"), 0.0, -3.0):
            self._install_fake_ocp(area)
            with self.assertRaises(ValueError):
                worker.source_face_area_mm2(object(), 2)


class CadDiagnosticsMalformedTests(unittest.TestCase):
    def test_malformed_knot_containers_report_errors_without_crashing(self):
        for knots in ([1.0, "a", 2.0], None, "not-a-list", {"a": 1}, [1.0, None]):
            errors = inspection.cad_diagnostics(synthetic_cad(knots_u=knots))["errors"]
            self.assertTrue(errors, f"no error for knots={knots!r}")

    def test_none_and_missing_degrees_are_rejected(self):
        cad = synthetic_cad()
        cad["breps"][0]["faces"][0]["surface"]["degrees"] = None
        self.assertTrue(inspection.cad_diagnostics(cad)["errors"])
        cad = synthetic_cad()
        del cad["breps"][0]["faces"][0]["surface"]["degrees"]
        self.assertTrue(inspection.cad_diagnostics(cad)["errors"])

    def test_none_control_points_are_rejected(self):
        cad = synthetic_cad()
        cad["breps"][0]["faces"][0]["surface"]["control_points"] = None
        self.assertTrue(inspection.cad_diagnostics(cad)["errors"])

    def test_empty_and_non_dict_surface_do_not_crash(self):
        cad = {"breps": [{"faces": [{"brep_id": 2, "surface": {}}]}]}
        self.assertTrue(inspection.cad_diagnostics(cad)["errors"])

    def test_non_dict_loop_curve_and_parameter_curve_do_not_crash(self):
        cad = synthetic_cad()
        cad["breps"][0]["faces"][0]["boundary_loops"] = [None, {"trimming_curves": [
            "bad", {"trim_index": 0, "parameter_curve": [1, 2]}]}]
        self.assertTrue(inspection.cad_diagnostics(cad)["errors"])

    def test_non_object_cad_is_rejected_with_a_diagnostic_not_accepted(self):
        for cad in (None, [], "cad", 3):
            diagnostics = inspection.cad_diagnostics(cad)
            self.assertTrue(diagnostics["errors"], cad)
            self.assertIn("must be an object", diagnostics["errors"][0])
            with self.assertRaises(ValueError):
                make_bundle(synthetic_project(), cad, synthetic_index())


class RefinementDisabledTests(unittest.TestCase):
    def test_target_fields_are_original_and_zero_with_requested_kept(self):
        project = synthetic_project()
        project["refinement_enabled"] = False
        patch = inspection.preprocessing_report(project, synthetic_cad(),
                                                synthetic_index())["patches"][0]
        self.assertEqual(patch["original_degree"], [1, 1])
        self.assertEqual(patch["target_degree"], [1, 1])
        self.assertEqual(patch["insert_per_span"], [0, 0])
        self.assertEqual(patch["requested_target_degree"], [2, 2])
        self.assertEqual(patch["requested_insert_per_span"], [1, 1])
        self.assertFalse(patch["final_grid_is_estimate"])
        self.assertEqual(patch["final_grid_control_points"], [2, 2])

    def test_enabled_refinement_keeps_requested_values_as_target(self):
        patch = inspection.preprocessing_report(synthetic_project(), synthetic_cad(),
                                                synthetic_index())["patches"][0]
        self.assertEqual(patch["target_degree"], [2, 2])
        self.assertEqual(patch["insert_per_span"], [1, 1])
        self.assertTrue(patch["final_grid_is_estimate"])


def _import_main_kratos():
    names = ("KratosMultiphysics", "KratosMultiphysics.IgaApplication",
             "KratosMultiphysics.LinearSolversApplication",
             "KratosMultiphysics.StructuralMechanicsApplication",
             "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_analysis")
    saved = {name: sys.modules.get(name) for name in names}

    class StructuralMechanicsAnalysis:
        def __init__(self, model, parameters):
            self.model = model
            self.parameters = parameters

    km = types.ModuleType("KratosMultiphysics")
    km.KratosGlobals = types.SimpleNamespace(GetVariable=lambda name: name)
    km.TIME = "TIME"
    km.DISPLACEMENT = "DISPLACEMENT"
    km.DISPLACEMENT_X = "DISPLACEMENT_X"
    km.DISPLACEMENT_Y = "DISPLACEMENT_Y"
    km.DISPLACEMENT_Z = "DISPLACEMENT_Z"
    iga = types.ModuleType("KratosMultiphysics.IgaApplication")
    lin = types.ModuleType("KratosMultiphysics.LinearSolversApplication")
    sma = types.ModuleType("KratosMultiphysics.StructuralMechanicsApplication")
    analysis = types.ModuleType(
        "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_analysis")
    analysis.StructuralMechanicsAnalysis = StructuralMechanicsAnalysis
    sma.structural_mechanics_analysis = analysis
    sys.modules.update({
        "KratosMultiphysics": km,
        "KratosMultiphysics.IgaApplication": iga,
        "KratosMultiphysics.LinearSolversApplication": lin,
        "KratosMultiphysics.StructuralMechanicsApplication": sma,
        "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_analysis": analysis,
    })
    spec = importlib.util.spec_from_file_location(
        "ibra_main_kratos_under_test",
        ROOT / "freecad/Mod/KratosIBRA/kratos_iga/runtime/MainKratos.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def restore():
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

    return module, restore


class MainKratosGuardTests(unittest.TestCase):
    """The Initialize-time guard is what raises before RunSolutionLoop."""

    def setUp(self):
        self.module, restore = _import_main_kratos()
        self.addCleanup(restore)

    def test_fail_status_raises_before_the_solution_loop(self):
        report = {"area_check": {"status": "fail", "mismatches": [
            {"face_id": 2, "actual_area": 40.0, "expected_area": 38.72,
             "relative_error": 0.033}]}}
        with self.assertRaises(self.module.AreaCheckMismatch):
            self.module.enforce_report(report)

    def test_pass_and_unknown_do_not_raise(self):
        for status in ("pass", "unknown"):
            self.module.enforce_report({"area_check": {"status": status, "mismatches": []}})

    def test_missing_area_check_is_safe(self):
        self.module.enforce_report({})

    def test_integration_counts_histogram_and_per_entity_are_reported(self):
        class FakeGeometry:
            def __init__(self, count):
                self.count = count

            def IntegrationPointsNumber(self):
                if self.count is None:
                    raise RuntimeError("unavailable")
                return self.count

        class FakeEntity:
            def __init__(self, ident, count):
                self.Id = ident
                self._geometry = FakeGeometry(count)

            def GetGeometry(self):
                return self._geometry

        entities = [FakeEntity(1, 1), FakeEntity(2, 1), FakeEntity(3, 4), FakeEntity(4, None)]
        counts = self.module._integration_counts(entities)
        self.assertEqual(counts["entities"], 4)
        self.assertEqual(counts["total"], 6)
        self.assertEqual(counts["distinct"], [1, 4])
        self.assertEqual(counts["histogram"], {"1": 2, "4": 1})
        self.assertEqual([row["id"] for row in counts["per_entity"]], [1, 2, 3])
        self.assertEqual(counts["unavailable_ids"], [4])


class _FakeString:
    def __init__(self, value):
        self.value = value

    def GetString(self):
        return self.value


def _mock_environment(specs, extra_patch_ids=()):
    class Geometry:
        def __init__(self, area):
            self._area = area

        def DomainSize(self):
            return self._area

        def IntegrationPointsNumber(self):
            return 1

        def PointsNumber(self):
            return 0

    class Entity:
        def __init__(self, ident, area):
            self.Id = ident
            self._geometry = Geometry(area)

        def GetGeometry(self):
            return self._geometry

    class Node:
        Id = 1

        def HasDofFor(self, _variable):
            return False

        def GetSolutionStepValue(self, _variable):
            return [0.0, 0.0, 0.0]

    class SubPart:
        def __init__(self, elements):
            self.Elements = elements
            self.Conditions = []

        def NumberOfNodes(self):
            return len(self.Elements)

        def NumberOfElements(self):
            return len(self.Elements)

        def NumberOfConditions(self):
            return 0

    class RefinedGeometry:
        def __init__(self, face_id):
            self.Id = face_id

        def PolynomialDegree(self, _axis):
            return 2

        def PointsNumberInDirection(self, _axis):
            return 4

        def KnotsU(self):
            return [-5.0, 0.0, 5.0]

        def KnotsV(self):
            return [-2.0, 0.0, 2.0]

    class Part:
        def __init__(self):
            self.Nodes = [Node()]
            self.Elements = []
            self.Conditions = []
            self.ProcessInfo = {"TIME": 0.0}
            self.Geometries = []
            self._subs = {}
            for spec in specs:
                elements = [Entity(1000 + spec["face_id"], spec["actual_area"])]
                self.Elements.extend(elements)
                self._subs[f"Patch_{spec['face_id']}"] = SubPart(elements)
                self.Geometries.append(RefinedGeometry(spec["face_id"]))
            for face_id in extra_patch_ids:
                self._subs[f"Patch_{face_id}"] = SubPart([])
                self.Geometries.append(RefinedGeometry(face_id))

        def NumberOfNodes(self):
            return len(self.Nodes)

        def NumberOfElements(self):
            return len(self.Elements)

        def NumberOfConditions(self):
            return 0

        def HasSubModelPart(self, name):
            return name in self._subs

        def GetSubModelPart(self, name):
            return self._subs[name]

        def GetSubModelPartNames(self):
            return list(self._subs)

    model = {"IgaModelPart": Part()}
    parameters = {"solver_settings": {"model_part_name": _FakeString("IgaModelPart")}}
    return model, parameters


def _manifest(patches, overall="measured"):
    return {"preprocessing": {"source_area": {"state": overall}, "patches": patches}}


def _meta(face_id, area, state="measured"):
    return {"face_id": face_id, "source_area": area, "source_area_state": state,
            "final_grid_control_points": [4, 4], "target_degree": [2, 2],
            "requested_quadrature_per_span": 0}


class CollectReportMixedTests(unittest.TestCase):
    """Per-patch authority, coverage and NaN/inf normalization in collect_report."""

    def setUp(self):
        self.module, restore = _import_main_kratos()
        self.addCleanup(restore)

    def _report(self, specs, patches, overall="measured", extra_patch_ids=()):
        model, parameters = _mock_environment(specs, extra_patch_ids)
        return self.module.collect_report(model, parameters, "initialize",
                                          _manifest(patches, overall))

    def test_known_mismatch_fails_even_when_another_patch_is_unknown(self):
        report = self._report(
            [{"face_id": 2, "actual_area": 40.0}, {"face_id": 4, "actual_area": 5.0}],
            [_meta(2, 38.72), _meta(4, None, state="unknown")], overall="mixed")
        self.assertEqual(report["area_check"]["status"], "fail")
        self.assertEqual([m["face_id"] for m in report["area_check"]["mismatches"]], [2])
        self.assertEqual(report["patches"][0]["area_match"], False)
        self.assertIsNone(report["patches"][1]["area_match"])

    def test_mixed_cache_without_mismatch_is_unknown_not_pass(self):
        report = self._report(
            [{"face_id": 2, "actual_area": 40.0}, {"face_id": 4, "actual_area": 5.0}],
            [_meta(2, 40.0), _meta(4, None, state="unknown")], overall="mixed")
        self.assertEqual(report["area_check"]["status"], "unknown")
        self.assertEqual(report["area_check"]["patch_state_summary"], "mixed")

    def test_coverage_incomplete_is_unknown(self):
        report = self._report(
            [{"face_id": 2, "actual_area": 40.0}], [_meta(2, 40.0)],
            extra_patch_ids=[4])
        self.assertEqual(report["area_check"]["status"], "unknown")
        self.assertFalse(report["patch_coverage"]["complete"])
        self.assertEqual(report["patch_coverage"]["missing_in_metadata"], [4])

    def test_fully_measured_and_matching_passes(self):
        report = self._report([{"face_id": 2, "actual_area": 40.0}], [_meta(2, 40.0)])
        self.assertEqual(report["area_check"]["status"], "pass")
        self.assertTrue(report["patch_coverage"]["complete"])

    def test_nan_and_inf_expected_are_normalized_and_fail(self):
        for bad in (float("nan"), float("inf"), float("-inf"), -1.0):
            report = self._report([{"face_id": 2, "actual_area": 40.0}], [_meta(2, bad)])
            patch = report["patches"][0]
            self.assertIsNone(patch["expected_area"], bad)
            self.assertTrue(patch["expected_area_invalid"], bad)
            self.assertEqual(patch["area_match"], False, bad)
            self.assertEqual(report["area_check"]["status"], "fail", bad)
            json.dumps(report, allow_nan=False)  # must not raise

    def test_measured_patch_without_declared_area_fails(self):
        report = self._report([{"face_id": 2, "actual_area": 40.0}], [_meta(2, None)])
        self.assertEqual(report["area_check"]["status"], "fail")
        self.assertTrue(report["patches"][0]["expected_area_invalid"])

    def test_legacy_unknown_without_declared_area_is_unknown_not_fail(self):
        report = self._report([{"face_id": 2, "actual_area": 40.0}],
                              [_meta(2, None, state="unknown")], overall="unknown")
        self.assertEqual(report["area_check"]["status"], "unknown")
        self.assertIsNone(report["patches"][0]["area_match"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
