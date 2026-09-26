"""P0-6 shard B2 tests: the run_report force evidence built from the Kratos model.

These are pure-logic tests on the exported ``MainKratos`` module with a fake Kratos
package, so they run without Kratos or FreeCAD. They cover exactly the shard contract:

* strong supports: only genuinely fixed translational DOFs, deduplicated across
  ``Support_*`` sub-model parts by (node id, DOF); non-fixed components excluded;
* penalty supports/couplings: raw condition right-hand side kept separate and marked
  ``unavailable`` as a signed reaction, never mixed into the fixed-node REACTION total;
* applied loads: real ``condition.CalculateRightHandSide`` resultants; a wrong length or
  a non-finite entry fails that item closed (never a pressure x area substitute);
* the measurement is taken before the base ``FinalizeSolutionStep`` releases the fixity;
* equilibrium can never be ``pass`` for a static linear solve and only ``pass`` with
  complete signed coverage plus verified convergence;
* every emitted value is JSON-finite (``allow_nan=False`` must not raise).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "freecad/Mod/KratosIBRA"))


def _import_main_kratos():
    names = ("KratosMultiphysics", "KratosMultiphysics.IgaApplication",
             "KratosMultiphysics.LinearSolversApplication",
             "KratosMultiphysics.StructuralMechanicsApplication",
             "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_analysis")
    saved = {name: sys.modules.get(name) for name in names}

    class StructuralMechanicsAnalysis:
        on_finalize = None

        def __init__(self, model, parameters):
            self.model = model
            self.parameters = parameters

        def Initialize(self):
            pass

        def InitializeSolutionStep(self):
            pass

        def SolveSolutionStep(self):
            return True

        def FinalizeSolutionStep(self):
            if StructuralMechanicsAnalysis.on_finalize is not None:
                StructuralMechanicsAnalysis.on_finalize()

    km = types.ModuleType("KratosMultiphysics")
    km.KratosGlobals = types.SimpleNamespace(GetVariable=lambda name: name)
    km.TIME = "TIME"
    km.STEP = "STEP"
    km.DISPLACEMENT = "DISPLACEMENT"
    km.DISPLACEMENT_X = km.DISPLACEMENT_Y = km.DISPLACEMENT_Z = "DISPLACEMENT"
    km.Vector = lambda values=None: list(values) if values is not None else []
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
        "ibra_main_kratos_forces",
        ROOT / "freecad/Mod/KratosIBRA/kratos_iga/runtime/MainKratos.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def restore():
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

    return module, StructuralMechanicsAnalysis, restore


class _String:
    def __init__(self, value):
        self.value = value

    def GetString(self):
        return self.value


class FakeNode:
    def __init__(self, ident, fixed_axes=(), reactions=None):
        self.Id = ident
        self.fixed = set(fixed_axes)
        self.reactions = dict(reactions or {})

    def IsFixed(self, variable):
        return variable == "DISPLACEMENT_" + variable[-1] and variable[-1] in self.fixed

    def GetSolutionStepValue(self, variable):
        if variable.startswith("REACTION_"):
            return self.reactions.get(variable[-1], 0.0)
        return [0.0, 0.0, 0.0]


class FakeGeometry:
    def __init__(self, points):
        self._points = points

    def PointsNumber(self):
        return self._points


class FakeCondition:
    def __init__(self, ident, rhs, points=None, error=None):
        self.Id = ident
        self._rhs = rhs
        self._points = points if points is not None else (max(1, len(rhs or ())) // 3)
        self._error = error

    def GetGeometry(self):
        return FakeGeometry(self._points)

    def CalculateRightHandSide(self, vector, process_info):
        if self._error is not None:
            raise self._error
        vector.extend(self._rhs)


class FakeSubPart:
    def __init__(self, nodes=(), conditions=()):
        self.Nodes = list(nodes)
        self.Conditions = list(conditions)

    def NumberOfNodes(self):
        return len(self.Nodes)

    def NumberOfConditions(self):
        return len(self.Conditions)


class FakePart:
    def __init__(self, sub_parts, process_info=None):
        self._subs = dict(sub_parts)
        self.Nodes = []
        for sub_part in self._subs.values():
            for node in sub_part.Nodes:
                if node not in self.Nodes:
                    self.Nodes.append(node)
        self.ProcessInfo = {"TIME": 1.0, "STEP": 1, "NL_ITERATION_NUMBER": None,
                            "RESIDUAL_NORM": None}
        self.ProcessInfo.update(process_info or {})

    def HasSubModelPart(self, name):
        return name in self._subs

    def GetSubModelPart(self, name):
        return self._subs[name]


def _row(model_part_name, module="assign_vector_variable_process", **extra):
    parameters = {"model_part_name": "IgaModelPart." + model_part_name}
    parameters.update(extra)
    return {"python_module": module, "kratos_module": "KratosMultiphysics",
            "Parameters": parameters}


def _parameters(constraints=(), loads=(), analysis_type="linear", solver_type="static",
                compute_reactions=True, include_compute_reactions=True):
    settings = {"model_part_name": "IgaModelPart", "analysis_type": analysis_type,
                "solver_type": solver_type, "max_iteration": 40}
    if include_compute_reactions:
        settings["compute_reactions"] = compute_reactions
    return {"solver_settings": settings,
            "processes": {"constraints_process_list": list(constraints),
                          "loads_process_list": list(loads)}}


def _measure(module, part, parameters, converged=True):
    model = {"IgaModelPart": part}
    return module.measure_step_forces(model, parameters, "solve", converged)


class ForceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.module, self.base, restore = _import_main_kratos()
        self.addCleanup(restore)
        self.base.on_finalize = None

    # ---------------------------------------------------------- strong supports
    def _dedup_fixture(self, penalty_rhs=(0.0, 0.0, -5.0)):
        n1 = FakeNode(1, "XYZ", {"X": 1.0, "Y": 2.0, "Z": 3.0})
        n2 = FakeNode(2, "XYZ", {"X": 4.0, "Y": 5.0, "Z": 6.0})
        n3 = FakeNode(3, "X", {"X": 7.0})
        support_1 = FakeSubPart([n1, n2])
        support_2 = FakeSubPart([n1, n3], [FakeCondition(11, list(penalty_rhs), points=1)])
        part = FakePart({"Support_1": support_1, "Support_2": support_2})
        parameters = _parameters(
            constraints=[_row("Support_1"),
                         _row("Support_2", module="assign_vector_variable_to_conditions_process")])
        return part, parameters

    def test_fixed_dof_dedup_across_sub_model_parts(self):
        part, parameters = self._dedup_fixture()
        strong = _measure(self.module, part, parameters)["strong_supports"]
        self.assertEqual(strong["status"], "measured")
        self.assertEqual(strong["total"], [12.0, 7.0, 9.0])
        self.assertEqual(len(strong["reactions"]), 7)
        self.assertEqual(strong["duplicates_removed"], 3)          # n1 X/Y/Z seen twice
        self.assertEqual(strong["non_fixed_components_excluded"], 2)  # n3 Y/Z
        self.assertEqual(strong["sub_model_parts"]["Support_1"]["unique_dofs"], 6)
        self.assertEqual(strong["sub_model_parts"]["Support_2"]["unique_dofs"], 1)
        keys = [(row["node_id"], row["component"]) for row in strong["reactions"]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_non_finite_reaction_makes_strong_unavailable(self):
        node = FakeNode(5, "X", {"X": float("nan")})
        part = FakePart({"Support_1": FakeSubPart([node])})
        parameters = _parameters(constraints=[_row("Support_1")])
        strong = _measure(self.module, part, parameters)["strong_supports"]
        self.assertEqual(strong["status"], "unavailable")
        self.assertIsNone(strong["total"])
        self.assertIn("not finite", strong["reason"])
        json.dumps(strong, allow_nan=False)

    def test_compute_reactions_disabled_is_unavailable(self):
        node = FakeNode(5, "X", {"X": 1.0})
        part = FakePart({"Support_1": FakeSubPart([node])})
        disabled = _measure(self.module, part, _parameters(
            constraints=[_row("Support_1")], compute_reactions=False))["strong_supports"]
        self.assertEqual(disabled["status"], "unavailable")
        self.assertIn("compute_reactions", disabled["reason"])
        # Absent means the Kratos solver default (true) applies and is recorded as such.
        absent = _measure(self.module, part, _parameters(
            constraints=[_row("Support_1")], include_compute_reactions=False))["strong_supports"]
        self.assertEqual(absent["status"], "measured")
        self.assertFalse(absent["compute_reactions"]["declared"])

    def test_no_fixed_dof_reports_unavailable_never_zero(self):
        node = FakeNode(5, ())
        part = FakePart({"Support_1": FakeSubPart([node])})
        parameters = _parameters(constraints=[_row("Support_1")])
        strong = _measure(self.module, part, parameters)["strong_supports"]
        self.assertEqual(strong["status"], "unavailable")
        self.assertIsNone(strong["total"])
        self.assertIn("no fixed translational DOF", strong["reason"])

    # ------------------------------------------------------- penalty vs strong
    def test_penalty_raw_rhs_is_separate_from_strong_reactions(self):
        part, parameters = self._dedup_fixture(penalty_rhs=(0.0, 0.0, -5.0))
        measurement = _measure(self.module, part, parameters)
        strong = measurement["strong_supports"]
        penalty = measurement["penalty_supports"]
        self.assertEqual(strong["total"], [12.0, 7.0, 9.0])       # the -5 penalty is NOT here
        self.assertEqual(penalty["status"], "measured")
        self.assertEqual(penalty["raw_total"], [0.0, 0.0, -5.0])
        self.assertEqual(penalty["reaction_status"], "unavailable")
        self.assertIsNone(penalty["physical_reaction"])
        self.assertIn("not confirmed", penalty["reason"])
        self.assertEqual(list(penalty["sub_model_parts"]), ["Support_2"])

    def test_coupling_penalty_is_separate_from_supports(self):
        node = FakeNode(1, "X", {"X": 2.0})
        part = FakePart({
            "Support_1": FakeSubPart([node]),
            "Coupling_14": FakeSubPart([node], [FakeCondition(21, [1.0, 0.0, 0.0], points=1)]),
        })
        parameters = _parameters(constraints=[
            _row("Support_1"),
            _row("Coupling_14",
                 module="assign_vector_variable_and_constraints_to_conditions_process"),
        ])
        measurement = _measure(self.module, part, parameters)
        self.assertEqual(measurement["strong_supports"]["total"], [2.0, 0.0, 0.0])
        self.assertEqual(measurement["penalty_couplings"]["raw_total"], [1.0, 0.0, 0.0])
        self.assertEqual(measurement["penalty_couplings"]["reaction_status"], "unavailable")
        self.assertEqual(measurement["penalty_supports"]["status"], "none")

    # ------------------------------------------------------------ applied loads
    def test_wrong_rhs_length_fails_the_load_item_closed(self):
        good = FakeCondition(1, [0.0, 0.0, -1.0], points=1)
        bad = FakeCondition(2, [0.0, 0.0, -1.0, 9.0], points=1)
        part = FakePart({"Load_1": FakeSubPart([FakeNode(1)], [good, bad])})
        parameters = _parameters(loads=[_row("Load_1", module="ibra_case_process")])
        loads = _measure(self.module, part, parameters)["applied_loads"]
        self.assertEqual(loads["status"], "unavailable")
        self.assertIsNone(loads["total"])
        self.assertEqual(loads["conditions_measured"], 1)
        self.assertEqual(loads["conditions_unavailable"], 1)
        row = loads["sub_model_parts"]["Load_1"]["rows"][1]
        self.assertEqual(row["status"], "unavailable")
        self.assertEqual(row["rhs_size"], 4)
        self.assertEqual(row["expected_size"], 3)
        self.assertIn("!= 3 * 1", row["reason"])
        json.dumps(loads, allow_nan=False)

    def test_non_finite_rhs_fails_the_load_item_closed(self):
        bad = FakeCondition(3, [0.0, float("inf"), 0.0], points=1)
        part = FakePart({"Load_1": FakeSubPart([FakeNode(1)], [bad])})
        parameters = _parameters(loads=[_row("Load_1", module="ibra_case_process")])
        loads = _measure(self.module, part, parameters)["applied_loads"]
        self.assertEqual(loads["status"], "unavailable")
        self.assertIsNone(loads["total"])
        self.assertIn("not finite",
                      loads["sub_model_parts"]["Load_1"]["rows"][0]["reason"])

    def test_raised_rhs_is_reported_not_propagated(self):
        bad = FakeCondition(4, None, points=1, error=RuntimeError("boom"))
        part = FakePart({"Load_1": FakeSubPart([FakeNode(1)], [bad])})
        parameters = _parameters(loads=[_row("Load_1", module="ibra_case_process")])
        loads = _measure(self.module, part, parameters)["applied_loads"]
        self.assertEqual(loads["status"], "unavailable")
        self.assertIn("boom", loads["sub_model_parts"]["Load_1"]["rows"][0]["reason"])

    def test_measured_load_is_the_rhs_resultant(self):
        conditions = [FakeCondition(1, [0.0, 0.0, -0.4], points=1),
                      FakeCondition(2, [0.0, 0.0, -0.6], points=1)]
        part = FakePart({"Load_1": FakeSubPart([FakeNode(1)], conditions)})
        parameters = _parameters(loads=[_row("Load_1", module="ibra_case_process")])
        loads = _measure(self.module, part, parameters)["applied_loads"]
        self.assertEqual(loads["status"], "measured")
        self.assertEqual(loads["total"], [0.0, 0.0, -1.0])

    # ----------------------------------------------------------- ordering hook
    def test_measurement_runs_before_the_base_releases_fixity(self):
        node = FakeNode(1, "XYZ", {"X": 1.0, "Y": 2.0, "Z": 3.0})
        part = FakePart({"Support_1": FakeSubPart([node])})
        parameters = _parameters(constraints=[_row("Support_1")])
        analysis = self.module.CheckedAnalysis({"IgaModelPart": part}, parameters, "solve")
        released = {"done": False}

        def release():
            released["done"] = True
            node.fixed.clear()          # assign_scalar_variable_process frees fixity here

        self.base.on_finalize = release
        analysis.FinalizeSolutionStep()
        self.assertTrue(released["done"])
        self.assertEqual(node.fixed, set())
        self.assertEqual(len(analysis.ibra_step_forces), 1)
        measured = analysis.ibra_step_forces[0]
        self.assertEqual(measured["strong_supports"]["status"], "measured")
        self.assertEqual(measured["strong_supports"]["total"], [1.0, 2.0, 3.0])

    # --------------------------------------------------------------- equilibrium
    def test_linear_equilibrium_is_never_pass(self):
        strong = FakeNode(1, "Z", {"Z": 0.001})
        part = FakePart({"Support_1": FakeSubPart([strong]),
                         "Load_1": FakeSubPart([strong], [FakeCondition(1, [0.0, 0.0, -0.001],
                                                                       points=1)])})
        parameters = _parameters(constraints=[_row("Support_1")],
                                 loads=[_row("Load_1", module="ibra_case_process")],
                                 analysis_type="linear")
        equilibrium = _measure(self.module, part, parameters)["equilibrium"]
        self.assertEqual(equilibrium["status"], "unverified")
        self.assertTrue(equilibrium["coverage"]["signed_complete"])
        self.assertIn("0.252%", equilibrium["reason"])
        self.assertFalse(equilibrium["convergence"]["verified"])

    def test_penalty_present_blocks_a_pass_even_with_a_tiny_residual(self):
        node = FakeNode(1, "Z", {"Z": 0.001})
        part = FakePart({
            "Support_1": FakeSubPart([node]),
            "Support_2": FakeSubPart([node], [FakeCondition(7, [0.0, 0.0, -0.001], points=1)]),
            "Load_1": FakeSubPart([node], [FakeCondition(1, [0.0, 0.0, 0.0], points=1)]),
        })
        parameters = _parameters(
            constraints=[_row("Support_1"),
                         _row("Support_2", module="assign_vector_variable_to_conditions_process")],
            loads=[_row("Load_1", module="ibra_case_process")],
            analysis_type="non_linear")
        equilibrium = _measure(self.module, part, parameters, converged=True)["equilibrium"]
        self.assertEqual(equilibrium["status"], "unverified")
        self.assertFalse(equilibrium["coverage"]["signed_complete"])
        self.assertEqual(equilibrium["raw_vector_including_penalty"], [0.0, 0.0, 0.0])

    def test_verified_converged_complete_residual_can_pass(self):
        node = FakeNode(1, "Z", {"Z": 0.001})
        part = FakePart({"Support_1": FakeSubPart([node]),
                         "Load_1": FakeSubPart([node], [FakeCondition(1, [0.0, 0.0, -0.001],
                                                                       points=1)])},
                        process_info={"NL_ITERATION_NUMBER": 4, "RESIDUAL_NORM": 1.0e-12})
        parameters = _parameters(constraints=[_row("Support_1")],
                                 loads=[_row("Load_1", module="ibra_case_process")],
                                 analysis_type="non_linear")
        measurement = _measure(self.module, part, parameters, converged=True)
        equilibrium = measurement["equilibrium"]
        self.assertTrue(equilibrium["convergence"]["verified"])
        self.assertEqual(equilibrium["status"], "pass")
        self.assertEqual(measurement["status"], "measured")

    def test_missing_load_keeps_equilibrium_unavailable(self):
        node = FakeNode(1, "Z", {"Z": 0.001})
        part = FakePart({"Support_1": FakeSubPart([node])})
        parameters = _parameters(constraints=[_row("Support_1")],
                                 loads=[_row("Load_1", module="ibra_case_process")],
                                 analysis_type="non_linear")
        measurement = _measure(self.module, part, parameters, converged=True)
        self.assertEqual(measurement["applied_loads"]["status"], "unavailable")
        self.assertEqual(measurement["equilibrium"]["status"], "unavailable")
        self.assertEqual(measurement["status"], "partial")

    # ------------------------------------------------------------ report plumbing
    def test_forces_section_is_json_finite(self):
        node = FakeNode(1, "XYZ", {"X": float("nan"), "Y": 0.0, "Z": 0.0})
        part = FakePart({"Support_1": FakeSubPart([node]),
                         "Load_1": FakeSubPart([node], [FakeCondition(1, [0.0, float("nan"), 0.0],
                                                                       points=1)])})
        parameters = _parameters(constraints=[_row("Support_1")],
                                 loads=[_row("Load_1", module="ibra_case_process")])
        measurement = _measure(self.module, part, parameters)
        section = self.module.summarize_forces([measurement])
        text = json.dumps(section, allow_nan=False)          # must not raise
        self.assertIn("\"schema\": \"ibra.run_forces/v1\"", text)
        # The non-finite inputs are normalized to null everywhere, never NaN/inf.
        self.assertIsNone(section["last"]["strong_supports"]["total"])
        self.assertIsNone(section["last"]["applied_loads"]["total"])
        self.assertIsNone(section["last"]["equilibrium"]["signed_vector"])
        self.assertNotIn("NaN", text)
        self.assertNotIn("Infinity", text)

    def test_summarize_forces_without_steps_is_unavailable(self):
        section = self.module.summarize_forces([])
        self.assertEqual(section["status"], "unavailable")
        self.assertEqual(section["step_count"], 0)
        self.assertEqual(section["steps"], [])
        self.assertIsNone(section["last"])
        json.dumps(section, allow_nan=False)

    def test_finalize_report_preserves_p0_4_fields_and_adds_forces(self):
        node = FakeNode(1, "Z", {"Z": 0.001})
        part = FakePart({"Support_1": FakeSubPart([node]),
                         "Load_1": FakeSubPart([node], [FakeCondition(1, [0.0, 0.0, -0.001],
                                                                       points=1)])})
        initial = {"mode": "initialize", "nodes": 1, "max_abs_displacement": 0.0,
                   "time": 0.0, "patches": [{"face_id": 2}],
                   "area_check": {"status": "pass", "tolerance_relative": 1.0e-6,
                                  "mismatches": []},
                   "checks": {"area": "pass"}, "forces": {"status": "unavailable"}}
        parameters = {"solver_settings": {"model_part_name": _String("IgaModelPart")}}
        measurement = _measure(self.module, part, _parameters(
            constraints=[_row("Support_1")], loads=[_row("Load_1", module="ibra_case_process")]))
        report = self.module.finalize_report(initial, {"IgaModelPart": part}, parameters,
                                             [measurement])
        self.assertEqual(report["mode"], "solve")
        self.assertEqual(report["area_check"]["status"], "pass")     # P0-4 field intact
        self.assertEqual(report["patches"], [{"face_id": 2}])
        self.assertEqual(report["checks"], {"area": "pass"})
        self.assertEqual(report["forces"]["status"], "measured")
        self.assertEqual(report["forces"]["step_count"], 1)
        self.assertEqual(report["forces"]["last"]["strong_supports"]["total"], [0.0, 0.0, 0.001])
        json.dumps(report, allow_nan=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
