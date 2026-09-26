"""MainKratos static+linear independent-step reset tests (fake Kratos, no runtime).

The exported ``MainKratos.CheckedAnalysis`` resets every node to its initial position
and zeroes the buffered ``DISPLACEMENT`` before the base class applies prescribed BCs,
loads and Predict — but ONLY when ``solver_type == "static"`` AND
``analysis_type == "linear"``. These tests exercise the exact gate, the
reset-before-super order and the preservation of prescribed values, without Kratos.
"""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "freecad/Mod/KratosIBRA"))

SUPER_CALLS = []


def _import_main_kratos():
    names = ("KratosMultiphysics", "KratosMultiphysics.IgaApplication",
             "KratosMultiphysics.LinearSolversApplication",
             "KratosMultiphysics.StructuralMechanicsApplication",
             "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_analysis")
    saved = {name: sys.modules.get(name) for name in names}

    class StructuralMechanicsAnalysis:
        on_super = None

        def __init__(self, model, parameters):
            self.model = model
            self.parameters = parameters

        def Initialize(self):
            pass

        def InitializeSolutionStep(self):
            SUPER_CALLS.append("super")
            if StructuralMechanicsAnalysis.on_super is not None:
                StructuralMechanicsAnalysis.on_super()

    km = types.ModuleType("KratosMultiphysics")
    km.KratosGlobals = types.SimpleNamespace(GetVariable=lambda name: name)
    km.TIME = "TIME"
    km.DISPLACEMENT = "DISPLACEMENT"
    km.DISPLACEMENT_X = km.DISPLACEMENT_Y = km.DISPLACEMENT_Z = "DISPLACEMENT"
    km.Vector = lambda values: list(values)
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
        "ibra_main_kratos_steps",
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


class _Node:
    def __init__(self, ident, current, initial):
        self.Id = ident
        self.X, self.Y, self.Z = current
        self._initial = initial
        self.steps = {}

    def GetInitialPosition(self):
        return types.SimpleNamespace(X=self._initial[0], Y=self._initial[1], Z=self._initial[2])

    def SetSolutionStepValue(self, _variable, step, value):
        self.steps[step] = list(value)


class _Part:
    def __init__(self, nodes, buffer_size=3):
        self.Nodes = nodes
        self._buffer = buffer_size

    def GetBufferSize(self):
        return self._buffer


def _parameters(solver_type="static", analysis_type="linear"):
    return {"solver_settings": {"solver_type": _String(solver_type),
                                "analysis_type": _String(analysis_type),
                                "model_part_name": _String("IgaModelPart")}}


class StaticLinearResetTests(unittest.TestCase):
    def setUp(self):
        self.module, self.base, restore = _import_main_kratos()
        self.addCleanup(restore)
        self.base.on_super = None
        SUPER_CALLS.clear()

    def _analysis(self, nodes, solver_type="static", analysis_type="linear"):
        return self.module.CheckedAnalysis({"IgaModelPart": _Part(nodes)},
                                           _parameters(solver_type, analysis_type), "solve")

    def test_gate_is_exactly_static_and_linear(self):
        for solver_type, analysis_type, expected in (
                ("static", "linear", True), ("static", "non_linear", False),
                ("dynamic", "linear", False), ("dynamic", "non_linear", False)):
            analysis = self._analysis([], solver_type, analysis_type)
            self.assertEqual(analysis._is_independent_static_linear(), expected,
                             (solver_type, analysis_type))

    def test_unreadable_settings_never_guess(self):
        analysis = self.module.CheckedAnalysis({}, {}, "solve")
        self.assertFalse(analysis._is_independent_static_linear())

    def test_reset_runs_before_super_only_for_static_linear(self):
        for solver_type, analysis_type, resets in (
                ("static", "linear", True), ("dynamic", "linear", False),
                ("static", "non_linear", False)):
            node = _Node(1, (0.5, 0.6, 0.7), (0.0, 0.0, 0.0))
            node.steps[0] = [0.9, 0.9, 0.9]
            node.steps[1] = [0.8, 0.8, 0.8]
            analysis = self._analysis([node], solver_type, analysis_type)
            seen = {}

            def on_super():
                seen["coords"] = (node.X, node.Y, node.Z)
                seen["disp"] = {step: list(value) for step, value in node.steps.items()}

            self.base.on_super = on_super
            analysis._independent_static_linear = analysis._is_independent_static_linear()
            analysis.InitializeSolutionStep()
            self.base.on_super = None
            self.assertIn("super", SUPER_CALLS[-1:])
            if resets:
                self.assertEqual(seen["coords"], (0.0, 0.0, 0.0))
                self.assertTrue(all(value == [0.0, 0.0, 0.0]
                                    for value in seen["disp"].values()), seen)
            else:
                self.assertEqual(seen["coords"], (0.5, 0.6, 0.7))
                self.assertEqual(seen["disp"][0], [0.9, 0.9, 0.9])

    def test_support_prescribed_values_are_applied_after_the_reset(self):
        node = _Node(1, (0.5, 0.6, 0.7), (0.0, 0.0, 0.0))
        analysis = self._analysis([node])
        analysis._independent_static_linear = True
        # The base class (assign_vector_variable_process) writes the prescribed value.
        self.base.on_super = lambda: node.SetSolutionStepValue("DISPLACEMENT", 0, [0.01, 0.0, 0.0])
        analysis.InitializeSolutionStep()
        self.base.on_super = None
        self.assertEqual(node.steps[0], [0.01, 0.0, 0.0])

    def test_reset_is_not_run_when_the_gate_is_false(self):
        node = _Node(1, (0.5, 0.6, 0.7), (0.0, 0.0, 0.0))
        node.steps[0] = [0.9, 0.9, 0.9]
        analysis = self._analysis([node], "dynamic", "linear")
        analysis._independent_static_linear = False
        analysis.InitializeSolutionStep()
        self.assertEqual((node.X, node.Y, node.Z), (0.5, 0.6, 0.7))
        self.assertEqual(node.steps[0], [0.9, 0.9, 0.9])


if __name__ == "__main__":
    unittest.main(verbosity=2)
