"""P0-5 pressure/time-table tests (pure Python: no OCP, no Kratos).

Covers the amplitude table, the additive ``reference_pressure`` load schema, unit
conversion, and the exported process/condition mapping. The real OCP+Kratos host
stages live in ``runtime/p0-5-review/backend_*.py``.
"""
from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "freecad/Mod/KratosIBRA"))

from kratos_iga.bundle import export_bundle, make_bundle  # noqa: E402
from kratos_iga.project import (amplitude_errors, convert_project_units, load_kind_error,  # noqa: E402
                                new_project, validate)
from kratos_iga.runtime import ibra_amplitude as amplitude  # noqa: E402


def synthetic_cad(faces=(2, 4)):
    def face(brep_id):
        return {"brep_id": brep_id,
                "surface": {"degrees": [1, 1], "is_rational": False,
                            "knot_vectors": [[-5.0, -5.0, 5.0, 5.0], [-2.0, -2.0, 2.0, 2.0]],
                            "control_points": [[1, [0.0, 0.0, 0.0, 1.0]],
                                               [2, [10.0, 0.0, 0.0, 1.0]],
                                               [3, [0.0, 4.0, 0.0, 1.0]],
                                               [4, [10.0, 4.0, 0.0, 1.0]]]},
                "boundary_loops": [{"loop_type": "outer", "trimming_curves": [{
                    "trim_index": 0, "parameter_curve": {
                        "degree": 1, "is_rational": False, "knot_vector": [0.0, 0.0, 1.0, 1.0],
                        "control_points": [[0, [0.0, 0.0, 0.0, 1.0]],
                                           [1, [1.0, 1.0, 0.0, 1.0]]]}}]}]}
    return {"breps": [{"edges": [], "faces": [face(fid) for fid in faces]}],
            "tolerances": {"model_tolerance": 0.001}}


def synthetic_index(areas=(32.0, 40.0), faces=(2, 4)):
    entries = []
    for fid, area in zip(faces, areas):
        entry = {"id": fid, "degrees": [1, 1], "min_continuity": [99, 99],
                 "control_point_counts": {"u": 2, "v": 2, "total": 4},
                 "knot_spans": {"u": 1, "v": 1}}
        if area is not None:
            entry["source_area_mm2"] = area
        entries.append(entry)
    return {"source_sha256": "src", "geometry_sha256": "geo", "faces": entries,
            "edges": [{"id": 11, "kind": "edge", "face_ids": [4],
                       "points": [[20.0, 1.0, 0.0], [20.0, 0.0, 0.0]], "strong_local": None}]}


def synthetic_project(units="mm-N-s"):
    project = new_project()
    project.update({"source_step": "x.step", "source_sha256": "src",
                    "geometry_sha256": "geo", "units": units, "refinement_enabled": True})
    project["patches"] = [
        {"face_id": 2, "material_id": 1, "section_id": 1, "element": "Shell3pElement",
         "degree_u": 2, "degree_v": 2, "insert_u": 1, "insert_v": 1, "quadrature": 0, "active": True},
        {"face_id": 4, "material_id": 1, "section_id": 1, "element": "Shell3pElement",
         "degree_u": 2, "degree_v": 2, "insert_u": 1, "insert_v": 1, "quadrature": 0, "active": True}]
    project["groups"] = []
    project["supports"] = []
    project["loads"] = []
    project["output"] = {"vtk": False, "refinement": 4}
    return project


def pressure(name="Top_pressure", target_id=2, value=2.0, **extra):
    record = {"name": name, "kind": "reference_pressure", "target_kind": "face",
              "target_id": target_id, "pressure": value, "interval": [0.0, "End"]}
    record.update(extra)
    return record


def vector_load(name="tip", variable="SURFACE_LOAD", target_id=2, **extra):
    record = {"name": name, "target_kind": "face", "target_id": target_id,
              "variable": variable, "value": [0.0, 0.0, -1.0], "interval": [0.0, "End"]}
    record.update(extra)
    return record


def load_units(bundle, name="Load_1"):
    return [entry for entry in bundle["physics.iga.json"]["element_condition_list"]
            if entry["iga_model_part"] == name]


def load_process(bundle, name="Load_1"):
    return [entry for entry in bundle["ProjectParameters.json"]["processes"]["loads_process_list"]
            if entry["Parameters"]["model_part_name"] == "IgaModelPart." + name][0]["Parameters"]


class AmplitudeTableTests(unittest.TestCase):
    def test_parse_endpoints_and_interpolation(self):
        rows = amplitude.parse([[0.0, 0.0], [0.25, 1.0], [1.0, 0.5]])
        self.assertEqual(len(rows), 3)
        self.assertEqual(amplitude.evaluate(rows, 0.0), 0.0)
        self.assertEqual(amplitude.evaluate(rows, 0.25), 1.0)
        self.assertEqual(amplitude.evaluate(rows, 1.0), 0.5)
        self.assertAlmostEqual(amplitude.evaluate(rows, 0.125), 0.5)
        self.assertAlmostEqual(amplitude.evaluate(rows, 0.625), 0.75)

    def test_outside_table_is_exactly_zero(self):
        rows = amplitude.parse([[0.2, 3.0], [0.8, 3.0]])
        self.assertEqual(amplitude.evaluate(rows, 0.1), 0.0)
        self.assertEqual(amplitude.evaluate(rows, 0.9), 0.0)

    def test_malformed_tables_are_rejected_not_repaired(self):
        bad = [
            [[0.0, 1.0]],
            [[0.0, 1.0], [0.0, 0.0]],
            [[0.0, 1.0], [1.0, 1.0], [0.5, 0.0]],
            [[0.0, 1.0], [1.0, float("nan")]],
            [[0.0, 1.0], [float("inf"), 0.0]],
            [[0.0, 1.0], [1.0, True]],
            [[0.0, 1.0], [1.0, "x"]],
            [[0.0, 1.0, 2.0], [1.0, 0.0]],
            "not-a-list",
        ]
        for points in bad:
            with self.assertRaises(amplitude.AmplitudeTableError, msg=repr(points)):
                amplitude.parse(points)

    def test_table_is_never_auto_sorted(self):
        with self.assertRaises(amplitude.AmplitudeTableError):
            amplitude.parse([[1.0, 1.0], [0.0, 0.0]])


class PressureValidationTests(unittest.TestCase):
    def test_valid_reference_pressure_passes(self):
        project = synthetic_project()
        project["loads"] = [pressure(normal_flip=True, amplitude={"points": [[0.0, 0.0], [1.0, 1.0]]})]
        self.assertEqual(validate(project, synthetic_index())[0], [])

    def test_pressure_must_be_finite_signed_number(self):
        for value in (float("nan"), float("inf"), "2.0", True, None):
            project = synthetic_project()
            project["loads"] = [pressure(value=value)]
            errors = validate(project, synthetic_index())[0]
            self.assertTrue(any("pressure must be" in error for error in errors), (value, errors))
            # A finite signed scalar (including a negative value) is accepted.
        project = synthetic_project()
        project["loads"] = [pressure(value=-1.5)]
        self.assertEqual(validate(project, synthetic_index())[0], [])

    def test_normal_flip_must_be_boolean(self):
        project = synthetic_project()
        project["loads"] = [pressure(normal_flip=1)]
        self.assertTrue(any("normal_flip must be a boolean" in error
                            for error in validate(project, synthetic_index())[0]))

    def test_variable_or_value_conflict_is_rejected(self):
        project = synthetic_project()
        project["loads"] = [dict(pressure(), variable="SURFACE_LOAD")]
        self.assertTrue(any("must not carry" in error
                            for error in validate(project, synthetic_index())[0]))

    def test_edge_target_is_rejected(self):
        project = synthetic_project()
        project["loads"] = [pressure(target_id=11)]
        project["loads"][0].update(target_kind="edge")
        self.assertTrue(any("requires a face or face group" in error
                            for error in validate(project, synthetic_index())[0]))

    def test_follower_and_unrecognized_kinds_are_rejected(self):
        for kind in ("follower_pressure", "current_normal_pressure", "pressure_follower", "magic"):
            project = synthetic_project()
            project["loads"] = [dict(pressure(), kind=kind)]
            errors = validate(project, synthetic_index())[0]
            self.assertTrue(any("not supported" in error or "unrecognized" in error
                                for error in errors), (kind, errors))

    def test_amplitude_domain_must_lie_within_the_interval(self):
        project = synthetic_project()
        project["loads"] = [pressure(amplitude={"points": [[-1.0, 0.0], [1.0, 1.0]]})]
        self.assertTrue(any("amplitude table domain" in error
                            for error in validate(project, synthetic_index())[0]))
        project = synthetic_project()
        project["loads"] = [pressure(interval=[0.2, 0.8],
                                     amplitude={"points": [[0.3, 0.0], [0.7, 1.0]]})]
        self.assertEqual(validate(project, synthetic_index())[0], [])
        project = synthetic_project()
        project["loads"] = [pressure(interval=[0.2, 0.8],
                                     amplitude={"points": [[0.1, 0.0], [0.7, 1.0]]})]
        self.assertTrue(any("amplitude table domain" in error
                            for error in validate(project, synthetic_index())[0]))

    def test_vector_loads_keep_legacy_behaviour_and_accept_amplitude(self):
        project = synthetic_project()
        project["loads"] = [vector_load()]
        self.assertEqual(validate(project, synthetic_index())[0], [])
        project["loads"] = [vector_load(amplitude={"points": [[0.0, 0.0], [1.0, 2.0]]})]
        self.assertEqual(validate(project, synthetic_index())[0], [])
        self.assertIsNone(load_kind_error(vector_load()))

    def test_amplitude_helper_returns_errors_for_bad_points(self):
        load = vector_load(amplitude={"points": [[0.0, 1.0], [0.0, 0.0]]})
        self.assertTrue(amplitude_errors(load, {"start_time": 0.0, "end_time": 1.0}))


class UnitConversionTests(unittest.TestCase):
    @staticmethod
    def legacy_payload(load_extra=None):
        """Minimal schema-3 conversion payload with NO solver (legacy caller)."""
        load = {"name": "Plate", "target_kind": "face", "target_id": 2,
                "variable": "SURFACE_LOAD", "value": [0.0, 0.0, -1.0], "interval": [0.0, "End"]}
        if load_extra:
            load.update(load_extra)
        return {"schema_version": 3, "units": "mm-N-s",
                "materials": [{"id": 1, "name": "S", "young": 200000.0, "poisson": 0.3,
                               "density": 7.85e-9}],
                "sections": [{"id": 1, "name": "S", "thickness": 0.1}],
                "patches": [], "supports": [], "loads": [load], "initial_conditions": [],
                "couplings": []}

    def test_legacy_payload_without_solver_and_without_amplitude_converts(self):
        converted = convert_project_units(self.legacy_payload(), "m-N-s", synthetic_index())
        self.assertEqual(converted["units"], "m-N-s")
        self.assertEqual(converted["loads"][0]["value"], [0.0, 0.0, -1.0e6])

    def test_amplitude_without_solver_fails_with_a_clear_error(self):
        payload = self.legacy_payload({"amplitude": {"points": [[0.0, 1.0], [1.0, 2.0]]}})
        before = copy.deepcopy(payload)
        with self.assertRaises(ValueError) as context:
            convert_project_units(payload, "m-N-s", synthetic_index())
        self.assertIn("amplitude", str(context.exception).lower())
        self.assertEqual(payload, before)

    def test_pressure_uses_stress_exponent_and_table_is_unchanged(self):
        project = synthetic_project("mm-N-s")
        table = {"points": [[0.0, 0.0], [1.0, 2.0]]}
        project["loads"] = [pressure(value=2.0, normal_flip=True, amplitude=copy.deepcopy(table))]
        converted = convert_project_units(project, "m-N-s", synthetic_index())
        # stress in m-N-s is numerically larger by 1e6 (1 N/mm^2 = 1e6 N/m^2)
        self.assertAlmostEqual(converted["loads"][0]["pressure"], 2.0e6)
        self.assertEqual(converted["loads"][0]["amplitude"], table)
        self.assertEqual(converted["loads"][0]["interval"], [0.0, "End"])
        self.assertTrue(converted["loads"][0]["normal_flip"])
        back = convert_project_units(converted, "mm-N-s", synthetic_index())
        self.assertAlmostEqual(back["loads"][0]["pressure"], 2.0)

    def test_invalid_pressure_rejects_atomically(self):
        project = synthetic_project()
        project["loads"] = [pressure(value=float("nan"))]
        before = copy.deepcopy(project)
        with self.assertRaises(ValueError):
            convert_project_units(project, "m-N-s", synthetic_index())
        self.assertEqual(project, before)

    def test_legacy_vector_conversion_unchanged(self):
        project = synthetic_project("mm-N-s")
        project["loads"] = [vector_load()]
        converted = convert_project_units(project, "m-N-s", synthetic_index())
        # a stress-valued surface load scales by 1e6 from mm-N-s to m-N-s
        self.assertEqual(converted["loads"][0]["value"], [0.0, 0.0, -1.0e6])


class PressureExportTests(unittest.TestCase):
    def test_pressure_process_mapping_and_expected_area(self):
        project = synthetic_project()
        project["loads"] = [pressure(value=-3.0, normal_flip=True)]
        bundle = make_bundle(project, synthetic_cad(), synthetic_index())
        units = load_units(bundle)
        self.assertEqual([entry["geometry_type"] for entry in units], ["GeometrySurface"])
        self.assertEqual(units[0]["brep_ids"], [2])
        params = load_process(bundle)
        self.assertEqual(params["mode"], "reference_pressure")
        self.assertEqual(params["pressure"], -3.0)
        self.assertTrue(params["normal_flip"])
        self.assertEqual(params["expected_area"], 32.0)
        self.assertEqual(params["expected_areas"], {"2": 32.0})
        self.assertNotIn("variable_name", params)
        self.assertNotIn("value", params)
        self.assertIsNone(params["amplitude"])
        self.assertNotIn("PRESSURE", str(params))

    def test_mm_pressure_expected_area_scales_with_length_squared(self):
        project = synthetic_project("m-N-s")
        project["loads"] = [pressure()]
        bundle = make_bundle(project, synthetic_cad(), synthetic_index())
        params = load_process(bundle)
        self.assertAlmostEqual(params["expected_area"], 32.0e-6)
        self.assertEqual(params["expected_area_units"], "m^2")

    def test_group_target_splits_by_requested_patch_quadrature_and_sums_one_process(self):
        project = synthetic_project()
        project["patches"][0]["quadrature"] = 2
        project["patches"][1]["quadrature"] = 0
        project["groups"] = [{"id": 1, "name": "Top", "kind": "face", "members": [2, 4, 4, 2]}]
        project["loads"] = [{"name": "Top_pressure", "kind": "reference_pressure", "group_id": 1,
                             "pressure": 1.0, "interval": [0.0, "End"]}]
        bundle = make_bundle(project, synthetic_cad(), synthetic_index())
        units = load_units(bundle)
        # Per-face quadrature units are preserved, deduplicated (2 and 4 once each).
        self.assertEqual(len(units), 2)
        ids = sorted(entry["brep_ids"] for entry in units)
        self.assertEqual(ids, [[2], [4]])
        quadrature = {tuple(entry["brep_ids"]): entry["parameters"].get(
            "number_of_integration_points_per_span") for entry in units}
        self.assertEqual(quadrature[(2,)], 2)
        self.assertIsNone(quadrature[(4,)])
        # Exactly one process for the record, with the summed expected area.
        processes = [entry for entry in
                     bundle["ProjectParameters.json"]["processes"]["loads_process_list"]
                     if entry["Parameters"]["model_part_name"] == "IgaModelPart.Load_1"]
        self.assertEqual(len(processes), 1)
        params = processes[0]["Parameters"]
        self.assertEqual(params["expected_area"], 72.0)
        self.assertEqual(params["expected_areas"], {"2": 32.0, "4": 40.0})

    def test_group_members_are_deduplicated(self):
        project = synthetic_project()
        project["groups"] = [{"id": 1, "name": "Top", "kind": "face", "members": [4, 2, 2, 4]}]
        project["loads"] = [{"name": "Top_pressure", "kind": "reference_pressure", "group_id": 1,
                             "pressure": 1.0, "interval": [0.0, "End"]}]
        bundle = make_bundle(project, synthetic_cad(), synthetic_index())
        self.assertEqual(load_units(bundle)[0]["brep_ids"], [2, 4])

    def test_missing_source_area_is_a_clear_reimport_error(self):
        project = synthetic_project()
        project["loads"] = [pressure()]
        with self.assertRaises(ValueError) as context:
            make_bundle(project, synthetic_cad(), synthetic_index(areas=(None, 40.0)))
        self.assertIn("Reimport", str(context.exception))

    def test_vector_and_pressure_on_one_face_warn_about_summing(self):
        project = synthetic_project()
        project["loads"] = [vector_load(), pressure()]
        errors, warnings = validate(project, synthetic_index())
        self.assertEqual(errors, [])
        self.assertTrue(any("reference_pressure" in warning and "added" in warning
                            for warning in warnings), warnings)

    def test_vector_load_process_is_unchanged_apart_from_amplitude(self):
        project = synthetic_project()
        project["loads"] = [vector_load()]
        bundle = make_bundle(project, synthetic_cad(), synthetic_index())
        params = load_process(bundle)
        self.assertEqual(params["mode"], "load")
        self.assertEqual(params["variable_name"], "SURFACE_LOAD")
        self.assertEqual(params["value"], [0.0, 0.0, -1.0])
        self.assertIsNone(params["amplitude"])

    def test_move_mesh_flag_stays_historical_true_for_every_analysis_type(self):
        # The coordinator reverted the conditional flag: move_mesh must remain True
        # (including dynamic). The multi-step static+linear fix lives in MainKratos.
        for solver_type, analysis_type in (("static", "linear"), ("static", "non_linear"),
                                           ("dynamic", "linear"), ("dynamic", "non_linear")):
            project = synthetic_project()
            project["solver"]["type"] = solver_type
            project["solver"]["analysis_type"] = analysis_type
            bundle = make_bundle(project, synthetic_cad(), synthetic_index())
            settings = bundle["ProjectParameters.json"]["solver_settings"]
            self.assertEqual(settings["analysis_type"], analysis_type)
            self.assertIs(settings["move_mesh_flag"], True, (solver_type, analysis_type))

    def test_export_copies_the_amplitude_helper(self):
        project = synthetic_project()
        project["loads"] = [pressure()]
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp) / "case"
            export_bundle(project, synthetic_cad(), synthetic_index(), case)
            self.assertTrue((case / "ibra_amplitude.py").is_file())
            self.assertTrue((case / "ibra_case_process.py").is_file())
            self.assertTrue((case / "MainKratos.py").is_file())
            self.assertIn("reference_pressure",
                          (case / "ibra_case_process.py").read_text(encoding="utf-8"))


def _import_case_process():
    import importlib.util
    import types as _types
    names = ("KratosMultiphysics", "KratosMultiphysics.IgaApplication")
    saved = {name: sys.modules.get(name) for name in names}

    class _Process:
        pass

    km = _types.ModuleType("KratosMultiphysics")
    km.Process = _Process
    km.KratosGlobals = _types.SimpleNamespace(GetVariable=lambda name: name)
    km.IntervalUtility = object
    km.Vector = lambda values: list(values)
    km.TIME = "TIME"
    sys.modules["KratosMultiphysics"] = km
    sys.modules["KratosMultiphysics.IgaApplication"] = _types.ModuleType(
        "KratosMultiphysics.IgaApplication")
    spec = importlib.util.spec_from_file_location(
        "ibra_case_process_under_test",
        ROOT / "freecad/Mod/KratosIBRA/kratos_iga/runtime/ibra_case_process.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def restore():
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

    return module, restore


class _FakeNode:
    def __init__(self, ident, current, initial, bad=False):
        self.Id = ident
        self.X, self.Y, self.Z = current
        self._initial = initial
        self._bad = bad

    def GetInitialPosition(self):
        if self._bad:
            raise RuntimeError("initial position unavailable")
        return type("P", (), {"X": self._initial[0], "Y": self._initial[1],
                              "Z": self._initial[2]})()


class _FakeMatrix:
    """Kratos returns a matrix indexable as ``m[i, j]``."""

    def __init__(self, rows):
        self._rows = rows

    def __getitem__(self, key):
        if isinstance(key, tuple):
            i, j = key
            return self._rows[i][j]
        return self._rows[key]


class _FakeGeometry:
    def __init__(self, nodes, jacobian=None, domain=None, ips=1):
        self._nodes = nodes
        self._jacobian = jacobian
        self._domain = domain
        self._ips = ips

    def IntegrationPointsNumber(self):
        return self._ips

    def PointsNumber(self):
        return len(self._nodes)

    def __getitem__(self, index):
        return self._nodes[index]

    def Jacobian(self, _index):
        if isinstance(self._jacobian, Exception):
            raise self._jacobian
        return self._jacobian

    def DomainSize(self):
        if isinstance(self._domain, Exception):
            raise self._domain
        if callable(self._domain):
            return self._domain(all(node.X == node._initial[0] for node in self._nodes))
        return self._domain


class ReferenceStateTests(unittest.TestCase):
    """The X0 freeze/restore transaction must be exception-safe and area-correct."""

    def setUp(self):
        self.module, restore = _import_case_process()
        self.addCleanup(restore)
        self.jacobian = _FakeMatrix([[1.0, 0.0], [0.0, 1.0], [0.0, 0.0]])

    def test_jacobian_exception_restores_every_coordinate(self):
        first = _FakeNode(1, (5.0, 6.0, 7.0), (0.0, 0.0, 0.0))
        second = _FakeNode(2, (8.0, 9.0, 10.0), (1.0, 1.0, 1.0))
        geometry = _FakeGeometry([first, second], jacobian=RuntimeError("boom"), domain=32.0)
        with self.assertRaises(RuntimeError):
            self.module.reference_state(geometry)
        self.assertEqual((first.X, first.Y, first.Z), (5.0, 6.0, 7.0))
        self.assertEqual((second.X, second.Y, second.Z), (8.0, 9.0, 10.0))

    def test_partial_freeze_failure_restores_already_frozen_nodes(self):
        first = _FakeNode(1, (5.0, 6.0, 7.0), (0.0, 0.0, 0.0))
        second = _FakeNode(2, (8.0, 9.0, 10.0), (1.0, 1.0, 1.0), bad=True)
        geometry = _FakeGeometry([first, second], jacobian=self.jacobian, domain=32.0)
        with self.assertRaises(RuntimeError):
            self.module.reference_state(geometry)
        self.assertEqual((first.X, first.Y, first.Z), (5.0, 6.0, 7.0))
        self.assertEqual((second.X, second.Y, second.Z), (8.0, 9.0, 10.0))

    def test_area_is_measured_in_the_reference_configuration(self):
        # DomainSize is 10 only while every node sits at its initial position; a
        # current/displaced configuration would report 40.
        node = _FakeNode(1, (0.5, 0.0, 0.0), (0.0, 0.0, 0.0))
        geometry = _FakeGeometry([node], jacobian=self.jacobian,
                                 domain=lambda frozen: 10.0 if frozen else 40.0)
        normal, area = self.module.reference_state(geometry)
        self.assertEqual(area, 10.0)
        self.assertEqual((node.X, node.Y, node.Z), (0.5, 0.0, 0.0))
        self.assertAlmostEqual(normal, [0.0, 0.0, 1.0])

    def test_shared_node_is_frozen_and_restored_once(self):
        node = _FakeNode(7, (2.0, 3.0, 4.0), (0.0, 0.0, 0.0))
        geometry = _FakeGeometry([node, node], jacobian=self.jacobian, domain=10.0)
        self.module.reference_state(geometry)
        self.assertEqual((node.X, node.Y, node.Z), (2.0, 3.0, 4.0))

    def test_non_single_integration_point_is_rejected(self):
        geometry = _FakeGeometry([_FakeNode(1, (0.0,) * 3, (0.0,) * 3)], ips=2)
        with self.assertRaises(RuntimeError):
            self.module.reference_state(geometry)

    def test_missing_or_nonfinite_expected_area_is_rejected(self):
        process = self.module.CaseProcess.__new__(self.module.CaseProcess)
        for value in (None, float("nan"), -1.0, 0.0):
            process.expected_area = value
            with self.assertRaises(RuntimeError):
                process._initialize_reference([])


if __name__ == "__main__":
    unittest.main(verbosity=2)
