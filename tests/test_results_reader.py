"""P0-6 S1 read-only result contract tests.

Covers the three shipped P0-7 run directories (skipped when absent, but every run
reports which ones it actually exercised), the first-step mesh contract that must
not apply the displacement twice, content-only model association, malformed /
non-finite / bad-offset refusal, and a synthetic two-step file whose offset slicing
is checked exactly. Nothing here writes outside a temporary directory and no file
under ``runtime/`` is modified.
"""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "freecad/Mod/KratosIBRA"))

from kratos_iga import results

try:
    import h5py
except ImportError:  # pragma: no cover - the backend venv provides h5py
    h5py = None

RUNTIME = ROOT / "runtime/p0-7-review"
CASES = {
    "cantilever": RUNTIME / "cantilever-run-example",
    "two_patch": RUNTIME / "two-patch-host-check-4",
    "perforated": RUNTIME / "perforated-rhs-check-2" / "case",
}
# The perforated case pair was solved in a later run than the run_report.json left
# beside it, so its report is not a usable cross-check (observed 0.0 vs 3.79e-3).
REPORT_CROSS_CHECK = ("cantilever", "two_patch")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def case_inputs(case_dir):
    exported_project = load_json(case_dir / "project.ibra-project.json")
    exported_cad = load_json(case_dir / "geometry.cad.json")
    run_report = load_json(case_dir / "run_report.json")
    return exported_project, exported_cad, run_report


def reader_for(case_dir, *, project=None, cad=None, **kwargs):
    exported_project, exported_cad, _report = case_inputs(case_dir)
    return results.ResultsReader(
        case_dir,
        current_project=exported_project if project is None else project,
        current_cad=exported_cad if cad is None else cad,
        **kwargs)


# --------------------------------------------------------------------------- #
# synthetic VTKHDF fixtures
# --------------------------------------------------------------------------- #

def write_vtkhdf(path, *, points, connectivity, offsets, types, displacement,
                 step_times, step_offsets, version=(2, 4), cell_type_override=None,
                 non_finite_at=None, point_rows=None):
    """Write one VTKHDF file under test. All arrays are given by the caller."""
    handle = h5py.File(str(path), "w")
    try:
        group = handle.create_group(results.VTKHDF_GROUP)
        group.attrs["Version"] = np.asarray(version, dtype=np.int64)
        group.attrs["Type"] = results.SUPPORTED_TYPE
        group.create_dataset("Points", data=np.asarray(points, dtype=float))
        group.create_dataset("Connectivity", data=np.asarray(connectivity, dtype=np.int64))
        group.create_dataset("Offsets", data=np.asarray(offsets, dtype=np.int64))
        types_array = np.asarray(types, dtype=np.uint8)
        if cell_type_override is not None:
            types_array = np.asarray(cell_type_override, dtype=np.uint8)
        group.create_dataset("Types", data=types_array)
        group.create_dataset("NumberOfPoints", data=(len(points),))
        group.create_dataset("NumberOfCells", data=(len(types_array),))
        group.create_dataset("NumberOfConnectivityIds", data=(len(connectivity),))
        payload = np.asarray(displacement, dtype=float).copy()
        if non_finite_at is not None:
            row, column = non_finite_at
            payload[row, column] = np.nan
        if point_rows is not None:
            payload = payload[:point_rows]
        point_data = group.create_group("PointData")
        point_data.create_dataset(results.DISPLACEMENT, data=payload)
        steps = group.create_group("Steps")
        times = steps.create_dataset("Values", data=np.asarray(step_times, dtype=float))
        steps.attrs["NSteps"] = np.int64(len(step_times))
        offsets_group = steps.create_group("PointDataOffsets")
        offsets_group.create_dataset(results.DISPLACEMENT,
                                     data=np.asarray(step_offsets, dtype=np.int64))
        for name in ("PartOffsets", "NumberOfParts", "PointOffsets", "CellOffsets",
                     "ConnectivityIdOffsets"):
            steps.create_dataset(name, data=np.zeros(len(step_times), dtype=np.int64))
        del times
    finally:
        handle.close()
    return Path(path)


def copy_case_for_tampering(case_dir, destination, mutate):
    """Copy a run directory's JSON inputs, then write a mutated VTKHDF file."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("run_report.json", "export_report.json", "project.ibra-project.json",
                 "geometry.cad.json", "ProjectParameters.json"):
        source = Path(case_dir) / name
        if source.is_file():
            shutil.copy2(source, destination / name)
    shutil.copy2(Path(case_dir) / "results.vtkhdf", destination / "results.vtkhdf")
    mutate(destination / "results.vtkhdf")
    return destination


# --------------------------------------------------------------------------- #
# real P0-7 run directories
# --------------------------------------------------------------------------- #

@unittest.skipUnless(h5py is not None, "h5py is required for the result reader")
class ShippedCaseTests(unittest.TestCase):
    def test_shipped_cases_are_readable(self):
        exercised = []
        skipped = []
        for name, case_dir in CASES.items():
            if not (case_dir / "results.vtkhdf").is_file():
                skipped.append(name)
                continue
            reader = reader_for(case_dir)
            metadata = reader.metadata()
            self.assertEqual(metadata["mapping_state"], "unavailable", name)
            self.assertTrue(metadata["case"]["solved_ready"], (name, metadata["case"]))
            self.assertTrue(metadata["case"]["move_mesh_flag"], name)
            self.assertEqual(metadata["case"]["mode"], "solve", name)
            self.assertGreaterEqual(metadata["vtkhdf"]["steps"], 1, name)
            self.assertGreater(metadata["vtkhdf"]["points"], 0, name)
            for kind in metadata["vtkhdf"]["cell_types"]:
                self.assertIn(int(kind), (results.VTK_TRIANGLE, results.VTK_QUAD), name)
            frame = reader.read_frame(step=0, scale=1.0, component="magnitude")
            self.assertTrue(np.all(np.isfinite(frame["points_display"])), name)
            self.assertEqual(frame["mesh"]["mapping_state"], "unavailable", name)
            self.assertTrue(np.all(np.isfinite(frame["displacement_values"])), name)
            exercised.append(
                f"  {name}: points={metadata['vtkhdf']['points']} "
                f"cells={metadata['vtkhdf']['cells']} "
                f"types={metadata['vtkhdf']['cell_types']} "
                f"steps={metadata['vtkhdf']['steps']} "
                f"times={metadata['vtkhdf']['times']} "
                f"len_unit={frame['case']['geometry_units']}")
        print(f"[P0-6 S1] shipped cases exercised: {exercised or 'none'}; skipped: "
              f"{skipped or 'none'}")
        self.assertTrue(exercised or skipped)
        if not exercised:
            self.skipTest("no shipped P0-7 results.vtkhdf is available")


@unittest.skipUnless(h5py is not None, "h5py is required for the result reader")
class CantileverContractTests(unittest.TestCase):
    """"First-step coordinates must not be deformed twice" on the real cantilever."""

    def setUp(self):
        self.case_dir = CASES["cantilever"]
        if not (self.case_dir / "results.vtkhdf").is_file():
            self.skipTest("the shipped cantilever results.vtkhdf is unavailable")
        self.reader = reader_for(self.case_dir)
        self.structure = self.reader._structure_or_read()

    def test_scale_zero_restores_the_reference_and_scale_one_reproduces_stored(self):
        stored = self.structure.points
        first = self.structure.displacement[0:self.structure.number_of_points]
        baseline = self.reader._baseline()[0]
        # The shipped file's first-step displacement is non-zero and the stored Points
        # already carry it (measured fact of these three runs), so scale 0 and scale 1
        # must differ by exactly that displacement.
        self.assertGreater(float(np.abs(first).max()), 1.0e-6)

        frame_zero = self.reader.read_frame(step=0, scale=0.0, component="magnitude")
        frame_one = self.reader.read_frame(step=0, scale=1.0, component="magnitude")
        self.assertTrue(np.allclose(frame_zero["points_display"], baseline, rtol=0.0,
                                    atol=1.0e-12))
        self.assertTrue(np.allclose(frame_one["points_display"], stored, rtol=0.0,
                                    atol=1.0e-12))
        # Scale 0 must not reproduce the deformed stored points.
        self.assertFalse(np.allclose(frame_zero["points_display"], stored, rtol=0.0,
                                     atol=1.0e-9))
        # Scale 1 must differ from scale 0, and a twice-applied displacement must not
        # appear in either frame.
        self.assertFalse(np.allclose(frame_zero["points_display"],
                                     frame_one["points_display"], rtol=0.0, atol=1.0e-12))
        doubled = baseline + 2.0 * first
        self.assertFalse(np.allclose(frame_one["points_display"], doubled, rtol=0.0,
                                     atol=1.0e-9))

    def test_tip_displacement_is_baseline_offset(self):
        """Measured shipped values: the tip is deformed in Points and restored at 0."""
        stored = self.structure.points
        first = self.structure.displacement[0:self.structure.number_of_points]
        baseline = self.reader._baseline()[0]
        tip = int(np.argmax(stored[:, 0]))
        self.assertAlmostEqual(float((-first[tip])[2]), 0.02000000000080121, places=12)
        self.assertAlmostEqual(float(baseline[tip][2]), 0.0, places=12)
        self.assertAlmostEqual(float(stored[tip][2]), -0.02000000000080121, places=12)
        frame_zero = self.reader.read_frame(step=0, scale=0.0, component="Z")
        frame_one = self.reader.read_frame(step=0, scale=1.0, component="Z")
        self.assertAlmostEqual(float(frame_zero["points_display"][tip][2]), 0.0, places=12)
        self.assertAlmostEqual(float(frame_one["points_display"][tip][2]),
                               -0.02000000000080121, places=12)

    def test_maximum_matches_the_run_report(self):
        report = load_json(self.case_dir / "run_report.json")
        frame = self.reader.read_frame(step=0, scale=1.0, component="magnitude")
        self.assertEqual(frame["run_report_consistency"], "match")
        self.assertAlmostEqual(frame["step"]["max_abs_displacement"],
                               float(report["max_abs_displacement"]), places=12)
        self.assertAlmostEqual(frame["extrema"]["magnitude"]["max"],
                               float(report["max_abs_displacement"]), places=12)
        self.assertEqual(frame["step"]["unit_factor"], 1.0)
        self.assertEqual(frame["step"]["source_unit"], "mm")

    def test_extrema_carry_index_and_physical_position(self):
        frame = self.reader.read_frame(step=0, scale=1.0, component="Z")
        entry = frame["extrema"]["Z"]
        stored = self.structure.points
        self.assertAlmostEqual(entry["min"], -0.02000000000080121, places=12)
        self.assertAlmostEqual(float(stored[entry["min_index"]][2]), entry["min"], places=12)
        self.assertAlmostEqual(entry["min_position"][2], entry["min"], places=12)
        self.assertEqual(entry["max_index"], int(np.argmax(
            self.structure.displacement[:self.structure.number_of_points, 2])))

    def test_component_values_track_the_selected_component(self):
        frame = self.reader.read_frame(step=0, scale=1.0, component="X")
        values = self.reader.component_values(0, "X")
        self.assertTrue(np.allclose(frame["displacement_values"], values, rtol=0.0,
                                    atol=1.0e-15))
        self.assertTrue(np.allclose(self.reader.component_values(0, "magnitude"),
                                    np.linalg.norm(
                                        self.structure.displacement[
                                            :self.structure.number_of_points], axis=1),
                                    rtol=0.0, atol=1.0e-15))

    def test_extrema_position_follows_step_and_scale(self):
        """The reported extreme position is on the displayed geometry, not the raw file."""
        base = self.reader.read_frame(step=0, scale=0.0, component="Z")
        scaled = self.reader.read_frame(step=0, scale=2.0, component="Z")
        index = base["extrema"]["Z"]["min_index"]
        self.assertEqual(index, scaled["extrema"]["Z"]["min_index"])
        self.assertAlmostEqual(base["extrema"]["Z"]["min_position"][2], 0.0, places=12)
        self.assertAlmostEqual(scaled["extrema"]["Z"]["min_position"][2],
                               2.0 * base["extrema"]["Z"]["min"], places=12)
        # The extreme value itself is the source displacement of the selected component,
        # so scaling does not change it; only the displayed position moves.
        self.assertAlmostEqual(scaled["extrema"]["Z"]["min"],
                               base["extrema"]["Z"]["min"], places=12)
        # The scaled position is the displayed coordinate, not the stored first-step one.
        stored = self.structure.points
        self.assertAlmostEqual(float(stored[index][2]), -0.02000000000080121, places=12)
        self.assertAlmostEqual(scaled["extrema"]["Z"]["min_position"][2],
                               -0.04000000000160242, places=12)
        self.assertNotAlmostEqual(scaled["extrema"]["Z"]["min_position"][2],
                                  float(stored[index][2]), places=6)

    def test_mapping_and_trim_are_not_claimed(self):
        frame = self.reader.read_frame(step=0, scale=1.0)
        self.assertEqual(frame["mapping_state"], "unavailable")
        self.assertEqual(frame["mesh"]["mapping_state"], "unavailable")
        metadata = self.reader.metadata()
        self.assertEqual(metadata["mapping_state"], "unavailable")
        self.assertNotIn("sampling", metadata)
        self.assertIn("trimmed integration domain", metadata["mapping_note"])


@unittest.skipUnless(h5py is not None, "h5py is required for the result reader")
class ReportCrossCheckTests(unittest.TestCase):
    def test_shipped_reports_agree_where_the_report_belongs_to_the_result(self):
        checked = []
        for name in REPORT_CROSS_CHECK:
            case_dir = CASES[name]
            if not (case_dir / "results.vtkhdf").is_file():
                continue
            reader = reader_for(case_dir)
            frame = reader.read_frame(step=0, scale=1.0, component="magnitude")
            self.assertEqual(frame["run_report_consistency"], "match", name)
            checked.append(name)
        print(f"[P0-6 S1] run_report cross-checked: {checked or 'none'}")
        if not checked:
            self.skipTest("no cross-checkable shipped case is available")

    def test_perforated_case_reads_with_the_writer_step_lag_reported(self):
        """The shipped perforated file has two steps but NSteps=1; it must still read."""
        case_dir = CASES["perforated"]
        if not (case_dir / "results.vtkhdf").is_file():
            self.skipTest("the shipped perforated results.vtkhdf is unavailable")
        reader = reader_for(case_dir)
        metadata = reader.metadata()
        self.assertTrue(metadata["case"]["solved_ready"], metadata["case"]["reasons"])
        self.assertEqual(metadata["vtkhdf"]["steps"], 2)
        self.assertEqual(metadata["vtkhdf"]["step_point_counts"], [352, 352])
        self.assertEqual(metadata["vtkhdf"]["cell_types"], {"5": 16, "9": 76})
        self.assertTrue(any("NSteps is 1" in warning
                            for warning in metadata["vtkhdf"]["warnings"]),
                        metadata["vtkhdf"]["warnings"])
        # The stale report beside it was written by a different solve, so the reader
        # reports the mismatch instead of claiming agreement.
        frame = reader.read_frame(step=1, scale=1.0, component="magnitude")
        self.assertEqual(frame["run_report_consistency"], "mismatch")


# --------------------------------------------------------------------------- #
# model association (full content comparison)
# --------------------------------------------------------------------------- #

@unittest.skipUnless(h5py is not None, "h5py is required for the result reader")
class AssociationTests(unittest.TestCase):
    def setUp(self):
        self.case_dir = CASES["cantilever"]
        if not (self.case_dir / "results.vtkhdf").is_file():
            self.skipTest("the shipped cantilever results.vtkhdf is unavailable")
        self.project, self.cad, _report = case_inputs(self.case_dir)

    def test_matching_model_is_accepted(self):
        reader = reader_for(self.case_dir)
        self.assertTrue(reader.context.solved_ready())
        self.assertEqual(reader.context.reasons, [])

    def test_moved_directory_does_not_cause_a_false_rejection(self):
        moved = dict(self.project)
        moved["source_step"] = r"D:\somewhere\else\cantilever.step"
        moved["profile"] = r"D:\other\cache\default.json"
        reader = results.ResultsReader(self.case_dir, current_project=moved,
                                       current_cad=self.cad)
        self.assertTrue(reader.context.solved_ready(), reader.context.reasons)

    def test_wrong_project_content_is_rejected(self):
        wrong = json.loads(json.dumps(self.project))
        wrong["patches"][0]["degree_u"] = 4
        reader = results.ResultsReader(self.case_dir, current_project=wrong,
                                       current_cad=self.cad)
        self.assertFalse(reader.context.solved_ready())
        self.assertTrue(any("does not match the exported project" in reason
                            for reason in reader.context.reasons),
                        reader.context.reasons)
        with self.assertRaises(results.AssociationError):
            reader.read_frame(step=0, scale=1.0)
        self.assertTrue(any("patches[0].degree_u" in reason
                            for reason in reader.context.reasons),
                        reader.context.reasons)

    def test_wrong_cad_content_is_rejected(self):
        wrong = json.loads(json.dumps(self.cad))
        wrong["breps"][0]["faces"][0]["surface"]["control_points"][0][1][0] = 99.0
        reader = results.ResultsReader(self.case_dir, current_project=self.project,
                                       current_cad=wrong)
        self.assertFalse(reader.context.solved_ready())
        self.assertTrue(any("does not match the exported CAD" in reason
                            for reason in reader.context.reasons),
                        reader.context.reasons)

    def test_missing_current_model_is_rejected(self):
        reader = results.ResultsReader(self.case_dir)
        self.assertFalse(reader.context.solved_ready())
        self.assertTrue(any("current project and CAD are required" in reason
                            for reason in reader.context.reasons),
                        reader.context.reasons)
        with self.assertRaises(results.AssociationError):
            reader.read_frame(step=0, scale=1.0)
        # Metadata inspection is still allowed without a current model.
        self.assertEqual(reader.metadata()["mapping_state"], "unavailable")

    def test_non_solve_report_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            case = Path(folder) / "case"
            shutil.copytree(self.case_dir, case)
            report = load_json(case / "run_report.json")
            report["mode"] = "initialize"
            (case / "run_report.json").write_text(json.dumps(report), encoding="utf-8")
            reader = results.ResultsReader(case, current_project=self.project,
                                           current_cad=self.cad)
            self.assertFalse(reader.context.solved_ready())
            self.assertTrue(any("may not be displayed" in reason
                                for reason in reader.context.reasons),
                            reader.context.reasons)
            with self.assertRaises(results.AssociationError):
                reader.read_frame(step=0, scale=1.0, require_solved=True)
            metadata = reader.metadata()
            self.assertFalse(metadata["case"]["solved_ready"])

    def test_move_mesh_flag_false_is_rejected(self):
        reader = results.ResultsReader(self.case_dir, current_project=self.project,
                                       current_cad=self.cad,
                                       parameters={"move_mesh_flag": False})
        self.assertFalse(reader.context.solved_ready())
        with self.assertRaises(results.AssociationError):
            reader.read_frame(step=0, scale=1.0)

    def test_move_mesh_flag_must_be_declared(self):
        with tempfile.TemporaryDirectory() as folder:
            case = Path(folder) / "case"
            shutil.copytree(self.case_dir, case)
            (case / "ProjectParameters.json").unlink()
            reader = results.ResultsReader(case, current_project=self.project,
                                           current_cad=self.cad)
            self.assertFalse(reader.context.solved_ready())
            self.assertTrue(any("move_mesh_flag" in reason
                                for reason in reader.context.reasons),
                            reader.context.reasons)

    def test_only_enumerated_project_metadata_is_skipped(self):
        comparable = results.comparable_project(self.project)
        # Exactly the relocatable path fields and the legacy export-metadata fields named
        # by the module are skipped; nothing is matched by substring.
        for key in results.PROJECT_EXCLUDED_TOP_LEVEL + results.PROJECT_EXCLUDED_METADATA:
            self.assertNotIn(key, comparable, key)
        # Every analysis field survives, including solver.time_step (the name contains
        # "step" but it is not a path) and the physics records.
        for key in ("solver", "loads", "supports", "materials", "sections", "patches",
                    "groups", "couplings", "output", "initial_conditions",
                    "refinement_enabled", "units", "schema_version"):
            self.assertIn(key, comparable, key)
        self.assertEqual(comparable["solver"], self.project["solver"])
        self.assertEqual(comparable["solver"]["time_step"],
                         self.project["solver"]["time_step"])
        # Nested analysis fields are copied, not filtered: a value that merely sounds
        # like a path must still be compared.
        nested = {"solver": {"time_step": 0.5, "file_tolerance": 1.0e-6},
                  "loads": [{"source": "pressure", "value": [1.0, 0.0, 0.0]}]}
        self.assertEqual(results.comparable_project(nested), nested)

    def test_cad_is_compared_in_full(self):
        comparable = results.comparable_cad(self.cad)
        self.assertEqual(comparable, self.cad)
        # Direction data that a broad path-word filter would have dropped stays present
        # and is a real comparison input (the rejection test below exercises it).
        def direction_fields(value, acc=None):
            acc = [] if acc is None else acc
            if isinstance(value, dict):
                for key, item in value.items():
                    if "direction" in key:
                        acc.append(key)
                    direction_fields(item, acc)
            elif isinstance(value, list):
                for item in value:
                    direction_fields(item, acc)
            return acc

        self.assertTrue(direction_fields(comparable))

    def test_changed_time_step_is_rejected(self):
        wrong = json.loads(json.dumps(self.project))
        wrong["solver"]["time_step"] = 0.25
        reader = results.ResultsReader(self.case_dir, current_project=wrong,
                                       current_cad=self.cad)
        self.assertFalse(reader.context.solved_ready())
        self.assertTrue(any("solver.time_step" in reason
                            for reason in reader.context.reasons),
                        reader.context.reasons)
        with self.assertRaises(results.AssociationError):
            reader.read_frame(step=0, scale=1.0)

    def test_changed_analysis_field_is_rejected(self):
        for mutate, expected in (
                (lambda model: model["solver"].__setitem__("end_time", 2.0), "solver.end_time"),
                (lambda model: model["output"].__setitem__("refinement", 3), "output.refinement"),
                (lambda model: model["materials"][0].__setitem__("young", 1.0),
                 "materials[0].young"),
                (lambda model: model["supports"][0].__setitem__("method", "penalty"),
                 "supports[0].method"),
                (lambda model: model["loads"][0].__setitem__("kind", "load"),
                 "loads[0].kind"),
        ):
            wrong = json.loads(json.dumps(self.project))
            mutate(wrong)
            reader = results.ResultsReader(self.case_dir, current_project=wrong,
                                           current_cad=self.cad)
            self.assertFalse(reader.context.solved_ready(), expected)
            self.assertTrue(any(expected in reason for reason in reader.context.reasons),
                            (expected, reader.context.reasons))

    def test_changed_cad_field_with_a_path_like_name_is_rejected(self):
        """A field a broad path-word filter would have skipped must still reject."""
        wrong = json.loads(json.dumps(self.cad))
        curves = wrong["breps"][0]["faces"][0].get("boundary_loops") or []
        edited = False
        for loop in curves:
            for curve in loop.get("trimming_curves") or []:
                if "curve_direction" in curve:
                    curve["curve_direction"] = -float(curve["curve_direction"])
                    edited = True
                    break
            if edited:
                break
        if not edited:
            # A CAD without a trimming curve still has to reject an added field.
            wrong["breps"][0]["faces"][0]["extra_geometry_note"] = "changed"
        reader = results.ResultsReader(self.case_dir, current_project=self.project,
                                       current_cad=wrong)
        self.assertFalse(reader.context.solved_ready())
        self.assertTrue(any("does not match the exported CAD" in reason
                            for reason in reader.context.reasons),
                        reader.context.reasons)

    def test_changed_cad_control_point_is_rejected(self):
        wrong = json.loads(json.dumps(self.cad))
        wrong["breps"][0]["faces"][0]["surface"]["control_points"][0][1][1] = 5.0
        reader = results.ResultsReader(self.case_dir, current_project=self.project,
                                       current_cad=wrong)
        self.assertFalse(reader.context.solved_ready())
        self.assertTrue(any("does not match the exported CAD" in reason
                            for reason in reader.context.reasons),
                        reader.context.reasons)


# --------------------------------------------------------------------------- #
# malformed content must be refused
# --------------------------------------------------------------------------- #

@unittest.skipUnless(h5py is not None, "h5py is required for the result reader")
class MalformedResultTests(unittest.TestCase):
    def setUp(self):
        self.case_dir = CASES["cantilever"]
        if not (self.case_dir / "results.vtkhdf").is_file():
            self.skipTest("the shipped cantilever results.vtkhdf is unavailable")
        self.exported_project, self.exported_cad, _report = case_inputs(self.case_dir)

    def _reader(self, case):
        return results.ResultsReader(case, current_project=self.exported_project,
                                     current_cad=self.exported_cad)

    def _tampered(self, mutate):
        folder = tempfile.mkdtemp(prefix="ibra-s1-")
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        return copy_case_for_tampering(self.case_dir, Path(folder) / "case", mutate)

    @staticmethod
    def _write_dataset(path, name, data):
        with h5py.File(str(path), "r+") as handle:
            group = handle[results.VTKHDF_GROUP]
            del group[name]
            group.create_dataset(name, data=data)

    def test_unsupported_version_is_rejected(self):
        def mutate(path):
            with h5py.File(str(path), "r+") as handle:
                handle[results.VTKHDF_GROUP].attrs["Version"] = np.asarray([1, 0],
                                                                          dtype=np.int64)
        case = self._tampered(mutate)
        with self.assertRaises(results.ResultSchemaError):
            self._reader(case).touch()

    def test_wrong_format_type_is_rejected(self):
        def mutate(path):
            with h5py.File(str(path), "r+") as handle:
                handle[results.VTKHDF_GROUP].attrs["Type"] = "PolyData"
        case = self._tampered(mutate)
        with self.assertRaises(results.ResultSchemaError):
            self._reader(case).touch()

    def test_unsupported_cell_type_is_rejected(self):
        def mutate(path):
            with h5py.File(str(path), "r+") as handle:
                handle[results.VTKHDF_GROUP]["Types"][0] = 12  # VTK_HEXAHEDRON
        case = self._tampered(mutate)
        with self.assertRaises(results.ResultSchemaError):
            self._reader(case).touch()

    def test_non_finite_displacement_is_rejected(self):
        def mutate(path):
            with h5py.File(str(path), "r+") as handle:
                handle[results.VTKHDF_GROUP]["PointData"][results.DISPLACEMENT][3, 1] = np.nan
        case = self._tampered(mutate)
        with self.assertRaises(results.ResultSchemaError):
            self._reader(case).touch()

    def test_non_finite_point_is_rejected(self):
        def mutate(path):
            with h5py.File(str(path), "r+") as handle:
                handle[results.VTKHDF_GROUP]["Points"][7, 2] = np.inf
        case = self._tampered(mutate)
        with self.assertRaises(results.ResultSchemaError):
            self._reader(case).touch()

    def test_broken_offsets_are_rejected(self):
        def mutate(path):
            with h5py.File(str(path), "r+") as handle:
                offsets = handle[results.VTKHDF_GROUP]["Offsets"]
                offsets[10] = offsets[9]  # an empty cell
        case = self._tampered(mutate)
        with self.assertRaises(results.ResultSchemaError):
            self._reader(case).touch()

    def test_offset_end_mismatch_is_rejected(self):
        def mutate(path):
            with h5py.File(str(path), "r+") as handle:
                offsets = handle[results.VTKHDF_GROUP]["Offsets"]
                offsets[-1] = int(offsets[-1]) - 1
        case = self._tampered(mutate)
        with self.assertRaises(results.ResultSchemaError):
            self._reader(case).touch()

    def test_connectivity_index_out_of_range_is_rejected(self):
        def mutate(path):
            with h5py.File(str(path), "r+") as handle:
                handle[results.VTKHDF_GROUP]["Connectivity"][0] = 10_000_000
        case = self._tampered(mutate)
        with self.assertRaises(results.ResultSchemaError):
            self._reader(case).touch()

    def test_connectivity_count_mismatch_is_rejected(self):
        def mutate(path):
            with h5py.File(str(path), "r+") as handle:
                del handle[results.VTKHDF_GROUP]["Connectivity"]
                handle[results.VTKHDF_GROUP].create_dataset(
                    "Connectivity", data=np.zeros(8, dtype=np.int64))
        case = self._tampered(mutate)
        with self.assertRaises(results.ResultSchemaError):
            self._reader(case).touch()

    def test_non_monotone_time_is_rejected(self):
        def mutate(path):
            with h5py.File(str(path), "r+") as handle:
                values = handle[results.VTKHDF_GROUP]["Steps"]["Values"]
                if values.shape[0] > 1:
                    values[1] = float(values[0]) - 1.0
                else:
                    values.resize((2,))
                    values[1] = float(values[0]) - 1.0
        case = self._tampered(mutate)
        with self.assertRaises(results.ResultSchemaError):
            self._reader(case).touch()

    def test_displacement_row_count_mismatch_is_rejected(self):
        def mutate(path):
            with h5py.File(str(path), "r+") as handle:
                pd = handle[results.VTKHDF_GROUP]["PointData"]
                data = np.asarray(pd[results.DISPLACEMENT][...])
                del pd[results.DISPLACEMENT]
                pd.create_dataset(results.DISPLACEMENT, data=data[:-1])
        case = self._tampered(mutate)
        with self.assertRaises(results.ResultSchemaError):
            self._reader(case).touch()

    def test_step_offset_mismatch_is_rejected(self):
        def mutate(path):
            with h5py.File(str(path), "r+") as handle:
                pdo = handle[results.VTKHDF_GROUP]["Steps"]["PointDataOffsets"]
                del pdo[results.DISPLACEMENT]
                pdo.create_dataset(results.DISPLACEMENT, data=np.asarray([1],
                                                                        dtype=np.int64))
        case = self._tampered(mutate)
        with self.assertRaises(results.ResultSchemaError):
            self._reader(case).touch()

    def test_missing_displacement_is_rejected(self):
        def mutate(path):
            with h5py.File(str(path), "r+") as handle:
                del handle[results.VTKHDF_GROUP]["PointData"][results.DISPLACEMENT]
        case = self._tampered(mutate)
        with self.assertRaises(results.ResultSchemaError):
            self._reader(case).touch()

    def test_missing_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            case = Path(folder) / "case"
            case.mkdir()
            with self.assertRaises(results.MissingInputError):
                self._reader(case)

    def test_size_limit_is_enforced(self):
        def mutate(path):
            pass
        case = self._tampered(mutate)
        reader = results.ResultsReader(case, current_project=self.exported_project,
                                       current_cad=self.exported_cad,
                                       limits={"max_file_bytes": 1024})
        with self.assertRaises(results.ResultLimitError):
            reader.touch()

    def test_point_limit_is_enforced(self):
        def mutate(path):
            pass
        case = self._tampered(mutate)
        reader = results.ResultsReader(case, current_project=self.exported_project,
                                       current_cad=self.exported_cad,
                                       limits={"max_points": 10})
        with self.assertRaises(results.ResultLimitError):
            reader.touch()

    def test_reference_points_far_outside_the_model_box_are_refused(self):
        """The CAD bound is a plausibility guard on the reconstructed sampling mesh.

        It is exercised directly: an arbitrary VTKHDF cannot prove the writer's mesh
        contract, and this test does not claim that it does.
        """
        reader = self._reader(self.case_dir)
        reference, _first = reader._baseline()
        control_points = results.cad_control_points(self.exported_cad)
        diagonal = float(np.linalg.norm(control_points.max(axis=0)
                                        - control_points.min(axis=0)))
        displaced = reference.copy()
        displaced[:, 2] -= diagonal  # far outside any allowed margin
        with self.assertRaises(results.ResultSchemaError):
            reader._geometry_check(displaced)
        # The shipped reference itself is inside the bound.
        self.assertEqual(reader._geometry_check(reference)["status"], "within_bounds")

    def test_geometry_check_is_unavailable_without_cad_control_points(self):
        reader = results.ResultsReader(self.case_dir, current_project=self.exported_project,
                                       current_cad=self.exported_cad)
        reference, _first = reader._baseline()
        reader.context.current_cad = {"breps": []}
        self.assertEqual(reader._geometry_check(reference)["status"], "unavailable")

    def test_invalid_scale_and_component_are_rejected(self):
        reader = self._reader(self.case_dir)
        for scale in (-1.0, float("nan"), float("inf"), "1.0", True):
            with self.assertRaises(results.ResultSchemaError):
                reader.read_frame(step=0, scale=scale)
        for component in ("x", "MAGNITUDE", None):
            with self.assertRaises(results.ResultSchemaError):
                reader.read_frame(step=0, component=component)
        for step in (-1, 5, 1.0, "0"):
            with self.assertRaises(results.ResultSchemaError):
                reader.read_frame(step=step)

    def test_metadata_is_json_safe(self):
        payload = self._reader(self.case_dir).metadata()
        text = json.dumps(payload)
        self.assertIn("mapping_state", json.loads(text))


# --------------------------------------------------------------------------- #
# synthetic two-step slicing
# --------------------------------------------------------------------------- #

@unittest.skipUnless(h5py is not None, "h5py is required for the result reader")
class TwoStepSliceTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="ibra-s1-2step-"))
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.case_dir = CASES["cantilever"]
        if not (self.case_dir / "results.vtkhdf").is_file():
            self.skipTest("the shipped cantilever inputs are unavailable")
        self.exported_project, self.exported_cad, _report = case_inputs(self.case_dir)

        self.points = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0],
                                [1.0, 1.0, 0.0], [0.0, 1.0, 0.0]], dtype=float)
        self.displacement = np.array([
            [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0],
            [0.0, 0.0, -0.5], [0.0, 0.0, -0.25], [0.0, 0.0, -0.75], [0.0, 0.0, -1.0],
        ], dtype=float)
        self.vtkhdf = write_vtkhdf(
            self.folder / "results.vtkhdf", points=self.points, connectivity=[0, 1, 2, 3],
            offsets=[0, 4], types=[results.VTK_QUAD], displacement=self.displacement,
            step_times=[0.0, 0.5], step_offsets=[0, 4])
        for name in ("run_report.json", "export_report.json", "project.ibra-project.json",
                     "geometry.cad.json", "ProjectParameters.json"):
            shutil.copy2(self.case_dir / name, self.folder / name)
        report = load_json(self.folder / "run_report.json")
        report["max_abs_displacement"] = 1.0
        (self.folder / "run_report.json").write_text(json.dumps(report), encoding="utf-8")

    def reader(self, **kwargs):
        return results.ResultsReader(self.folder, current_project=self.exported_project,
                                     current_cad=self.exported_cad, **kwargs)

    def test_two_steps_are_sliced_independently(self):
        reader = self.reader()
        metadata = reader.metadata()
        self.assertEqual(metadata["vtkhdf"]["steps"], 2)
        self.assertEqual(metadata["vtkhdf"]["times"], [0.0, 0.5])
        self.assertEqual(metadata["vtkhdf"]["step_point_counts"], [4, 4])
        self.assertEqual(metadata["vtkhdf"]["points"], 4)
        self.assertEqual(metadata["vtkhdf"]["cells"], 1)
        self.assertEqual(metadata["vtkhdf"]["cell_types"], {"9": 1})

        first = reader.read_frame(step=0, scale=1.0, component="Z")
        second = reader.read_frame(step=1, scale=1.0, component="Z")
        self.assertAlmostEqual(first["step"]["max_abs_displacement"], 0.0)
        self.assertAlmostEqual(second["step"]["max_abs_displacement"], 1.0)
        self.assertEqual(first["run_report_consistency"], "mismatch")
        self.assertEqual(second["run_report_consistency"], "match")
        # The first step has zero displacement, so its display equals the reference
        # sampling geometry; the second step's tip is displaced by -1.0 mm.
        self.assertTrue(np.allclose(first["points_display"], self.points))
        tip = 3
        self.assertAlmostEqual(float(second["points_display"][tip][2]), -1.0)
        self.assertAlmostEqual(float(second["displacement_values"][tip]), -1.0)
        self.assertAlmostEqual(second["extrema"]["Z"]["min"], -1.0)
        # The extreme position is the displayed coordinate of that step (reference plus
        # the selected step's displacement), not the reference or the first step.
        self.assertAlmostEqual(second["extrema"]["Z"]["min_position"][2], -1.0)
        self.assertEqual(second["extrema"]["Z"]["min_index"], tip)

    def test_scale_interpolates_only_the_selected_step(self):
        reader = self.reader()
        half = reader.read_frame(step=1, scale=0.5, component="Z")
        self.assertAlmostEqual(float(half["points_display"][3][2]), -0.5)
        zero = reader.read_frame(step=1, scale=0.0, component="Z")
        self.assertTrue(np.allclose(zero["points_display"], self.points))

    def test_triangle_and_quad_mix_is_sliced_correctly(self):
        path = self.folder / "mixed.vtkhdf"
        write_vtkhdf(path, points=self.points, connectivity=[0, 1, 2, 3, 1, 2, 3],
                     offsets=[0, 4, 7], types=[results.VTK_QUAD, results.VTK_TRIANGLE],
                     displacement=self.displacement, step_times=[0.0, 0.5],
                     step_offsets=[0, 4])
        reader = results.ResultsReader(self.folder, current_project=self.exported_project,
                                       current_cad=self.exported_cad, vtkhdf=path)
        metadata = reader.metadata()
        self.assertEqual(metadata["vtkhdf"]["cell_types"], {"9": 1, "5": 1})
        self.assertEqual(metadata["vtkhdf"]["cell_widths"], [3, 4])
        self.assertEqual(metadata["vtkhdf"]["steps"], 2)
        frame = reader.read_frame(step=1, scale=1.0)
        self.assertEqual(list(frame["mesh"]["types"]), [9, 5])

    def test_three_steps_use_recorded_offsets(self):
        path = self.folder / "three.vtkhdf"
        stacked = np.vstack([self.displacement,
                             self.displacement[4:] * 2.0])
        write_vtkhdf(path, points=self.points, connectivity=[0, 1, 2, 3],
                     offsets=[0, 4], types=[results.VTK_QUAD], displacement=stacked,
                     step_times=[0.0, 0.25, 0.5], step_offsets=[0, 4, 8])
        reader = results.ResultsReader(self.folder, current_project=self.exported_project,
                                       current_cad=self.exported_cad, vtkhdf=path)
        metadata = reader.metadata()
        self.assertEqual(metadata["vtkhdf"]["steps"], 3)
        third = reader.read_frame(step=2, scale=1.0, component="Z")
        self.assertAlmostEqual(third["step"]["max_abs_displacement"], 2.0)
        # The reference is still taken from the *first* step, never the selected one.
        self.assertAlmostEqual(float(third["points_display"][3][2]), -2.0)

    def test_bad_per_step_offset_length_is_rejected(self):
        path = self.folder / "bad-offsets.vtkhdf"
        write_vtkhdf(path, points=self.points, connectivity=[0, 1, 2, 3],
                     offsets=[0, 4], types=[results.VTK_QUAD], displacement=self.displacement,
                     step_times=[0.0, 0.5], step_offsets=[0])
        reader = results.ResultsReader(self.folder, current_project=self.exported_project,
                                       current_cad=self.exported_cad, vtkhdf=path)
        with self.assertRaises(results.ResultSchemaError):
            reader.touch()

    def test_nsteps_lag_is_reported_and_a_truncated_count_is_rejected(self):
        """The shipped writer fixes NSteps up one step late; a larger count is refused."""
        lagging = self.folder / "lag.vtkhdf"
        write_vtkhdf(lagging, points=self.points, connectivity=[0, 1, 2, 3],
                     offsets=[0, 4], types=[results.VTK_QUAD], displacement=self.displacement,
                     step_times=[0.0, 0.5], step_offsets=[0, 4])
        with h5py.File(str(lagging), "r+") as handle:
            handle[results.VTKHDF_GROUP]["Steps"].attrs["NSteps"] = np.int64(1)
        reader = results.ResultsReader(self.folder, current_project=self.exported_project,
                                       current_cad=self.exported_cad, vtkhdf=lagging)
        metadata = reader.metadata()
        self.assertEqual(metadata["vtkhdf"]["steps"], 2)
        self.assertEqual(metadata["vtkhdf"]["times"], [0.0, 0.5])
        self.assertTrue(any("NSteps is 1" in warning
                            for warning in metadata["vtkhdf"]["warnings"]),
                        metadata["vtkhdf"]["warnings"])

        truncated = self.folder / "truncated.vtkhdf"
        write_vtkhdf(truncated, points=self.points, connectivity=[0, 1, 2, 3],
                     offsets=[0, 4], types=[results.VTK_QUAD], displacement=self.displacement,
                     step_times=[0.0, 0.5], step_offsets=[0, 4])
        with h5py.File(str(truncated), "r+") as handle:
            handle[results.VTKHDF_GROUP]["Steps"].attrs["NSteps"] = np.int64(3)
        reader = results.ResultsReader(self.folder, current_project=self.exported_project,
                                       current_cad=self.exported_cad, vtkhdf=truncated)
        with self.assertRaises(results.ResultSchemaError):
            reader.touch()


if __name__ == "__main__":
    unittest.main(verbosity=2)
