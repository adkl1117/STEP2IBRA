"""P0-6 shard C1 tests: the backend JSON bridge and the FreeCAD-free result-layer core.

Two halves, deliberately separated:

* :mod:`kratos_iga.results_cli` is exercised **as a real subprocess with the backend
  Python** against the three shipped P0-7 ``results.vtkhdf`` files. Nothing is mocked:
  the CLI reads the native file through ``results.py``/h5py, and every reported number
  is compared with the same dataset read directly here. Covered: metadata and frame,
  the scale 1 / scale 0 writer contract, per-component extrema on the displayed
  geometry, the mixed triangle/quad mesh, record-index step slicing on a two-step copy,
  the ``m-N-s`` CAD conversion, display-cap refusal, wrong project / wrong CAD /
  non-solve / missing-file refusals, malformed requests, and the "no file is written"
  guarantee.
* :mod:`kratos_iga.result_layer` is tested through its pure helpers only
  (colour ramp, native-cell triangulation, plan building, extrema markers, ownership
  predicate) plus an import-purity check that the module needs neither h5py, numpy,
  FreeCAD nor Part. Mock objects are used **only** for the ownership predicate; the
  real FreeCAD drawing is accepted by the host smoke test, never by a mock here.

Every file this test creates lives in a temporary directory. No file under ``runtime/``
is modified.
"""
import json
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "freecad/Mod/KratosIBRA"))

from kratos_iga import result_layer  # noqa: E402
from kratos_iga import results_cli  # noqa: E402

try:
    import h5py
except ImportError:  # pragma: no cover - the backend venv provides h5py
    h5py = None

try:
    from kratos_iga import results
except ImportError:  # pragma: no cover - results.py refuses without h5py
    results = None

CLI = ROOT / "freecad/Mod/KratosIBRA/kratos_iga/results_cli.py"
RUNTIME = ROOT / "runtime/p0-7-review"
CASES = {
    "cantilever": RUNTIME / "cantilever-run-example",
    "two_patch": RUNTIME / "two-patch-host-check-4",
    "perforated": RUNTIME / "perforated-rhs-check-2" / "case",
}
COPY_INPUTS = ("run_report.json", "export_report.json", "project.ibra-project.json",
               "geometry.cad.json", "ProjectParameters.json", "results.vtkhdf")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def available_cases():
    return {name: directory for name, directory in CASES.items()
            if (directory / "results.vtkhdf").is_file()
            and (directory / "project.ibra-project.json").is_file()
            and (directory / "geometry.cad.json").is_file()}


def run_cli(request, timeout=180):
    return subprocess.run([sys.executable, str(CLI)], input=json.dumps(request),
                          capture_output=True, text=True, encoding="utf-8",
                          timeout=timeout)


def cli_payload(process):
    """The single bounded JSON object on stdout, plus its exact line count."""
    return json.loads(process.stdout)


def request_for(case_dir, action, **extra):
    request = {"action": action, "run_dir": str(Path(case_dir).resolve()),
               "current_project": load_json(Path(case_dir) / "project.ibra-project.json"),
               "current_cad_mm": load_json(Path(case_dir) / "geometry.cad.json")}
    request.update(extra)
    return request


def copy_case(case_dir, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name in COPY_INPUTS:
        source = Path(case_dir) / name
        if source.is_file():
            shutil.copy2(source, destination / name)
    return destination


def write_native(path, *, points, connectivity, offsets, types, displacement, times,
                 step_offsets):
    """Write one minimal but fully self-consistent VTKHDF 2.4 file."""
    handle = h5py.File(str(path), "w")
    try:
        group = handle.create_group(results.VTKHDF_GROUP)
        group.attrs["Version"] = np.asarray((2, 4), dtype=np.int64)
        group.attrs["Type"] = results.SUPPORTED_TYPE
        group.create_dataset("Points", data=np.asarray(points, dtype=float))
        group.create_dataset("Connectivity", data=np.asarray(connectivity, dtype=np.int64))
        group.create_dataset("Offsets", data=np.asarray(offsets, dtype=np.int64))
        group.create_dataset("Types", data=np.asarray(types, dtype=np.uint8))
        group.create_dataset("NumberOfPoints", data=(len(points),))
        group.create_dataset("NumberOfCells", data=(len(types),))
        group.create_dataset("NumberOfConnectivityIds", data=(len(connectivity),))
        point_data = group.create_group("PointData")
        point_data.create_dataset(results.DISPLACEMENT,
                                  data=np.asarray(displacement, dtype=float))
        steps = group.create_group("Steps")
        steps.create_dataset("Values", data=np.asarray(times, dtype=float))
        steps.attrs["NSteps"] = np.int64(len(times))
        offsets_group = steps.create_group("PointDataOffsets")
        offsets_group.create_dataset(results.DISPLACEMENT,
                                     data=np.asarray(step_offsets, dtype=np.int64))
    finally:
        handle.close()
    return Path(path)


def native_arrays(case_dir):
    with h5py.File(Path(case_dir) / "results.vtkhdf", "r") as handle:
        group = handle[results.VTKHDF_GROUP]
        return {
            "points": np.asarray(group["Points"][...], dtype=float),
            "connectivity": np.asarray(group["Connectivity"][...], dtype=np.int64),
            "offsets": np.asarray(group["Offsets"][...], dtype=np.int64),
            "types": np.asarray(group["Types"][...], dtype=np.int64),
            "displacement": np.asarray(group["PointData"][results.DISPLACEMENT][...],
                                       dtype=float),
            "times": np.asarray(group["Steps"]["Values"][...], dtype=float),
        }


def scale_cad_to_metres(cad):
    """Independent re-implementation of bundle.make_bundle's m-N-s CAD conversion.

    This is the *fixture* builder only: the code under test must reproduce it on its
    own from the millimetre CAD, so a mistake in either implementation shows up as a
    failed association instead of cancelling out.
    """
    scaled = json.loads(json.dumps(cad))
    for brep in scaled["breps"]:
        entities = [face["surface"] for face in brep["faces"]]
        entities += [edge["3d_curve"] for edge in brep["edges"] if "3d_curve" in edge]
        for entity in entities:
            for control_point in entity["control_points"]:
                control_point[1][:3] = [value * 0.001 for value in control_point[1][:3]]
    scaled["tolerances"]["model_tolerance"] *= 0.001
    return scaled


# --------------------------------------------------------------------------- #
# real P0-7 files through the CLI subprocess
# --------------------------------------------------------------------------- #

@unittest.skipUnless(h5py is not None and results is not None,
                     "the backend venv with h5py is required for the CLI tests")
class RealCaseCliTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.cases = available_cases()
        if not cls.cases:
            raise unittest.SkipTest(f"no P0-7 run directory under {RUNTIME}")

    def report_exercised(self, name):
        if name not in self.cases:
            self.skipTest(f"the {name} P0-7 run directory is absent")

    def test_metadata_matches_the_native_file(self):
        for name, case_dir in self.cases.items():
            with self.subTest(case=name):
                process = run_cli(request_for(case_dir, "metadata"))
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(process.stderr, "")
                payload = cli_payload(process)
                self.assertIs(payload["ok"], True)
                self.assertEqual(payload["mapping_state"], "unavailable")
                self.assertEqual(payload["mapping_note"].count("no face/UV"), 1)
                native = native_arrays(case_dir)
                self.assertEqual(payload["vtkhdf"]["points"], native["points"].shape[0])
                self.assertEqual(payload["vtkhdf"]["cells"], native["types"].size)
                self.assertEqual(payload["vtkhdf"]["connectivity_ids"],
                                 native["connectivity"].size)
                self.assertEqual(payload["vtkhdf"]["steps"], native["times"].size)
                self.assertTrue(payload["case"]["solved_ready"])
                self.assertEqual(payload["case"]["mode"], "solve")
                self.assertIs(payload["case"]["move_mesh_flag"], True)

    def test_scale_one_reproduces_the_stored_native_points(self):
        """The stored Points already carry the first step: scale 1 must not add it again."""
        for name, case_dir in self.cases.items():
            with self.subTest(case=name):
                process = run_cli(request_for(case_dir, "frame", step=0, scale=1.0,
                                              component="magnitude"))
                self.assertEqual(process.returncode, 0, process.stderr)
                payload = cli_payload(process)
                native = native_arrays(case_dir)
                np.testing.assert_allclose(np.asarray(payload["points"]), native["points"],
                                           rtol=0.0, atol=1e-9)
                magnitude = np.linalg.norm(native["displacement"][:native["points"].shape[0]],
                                           axis=1)
                np.testing.assert_allclose(np.asarray(payload["values"]), magnitude,
                                           rtol=0.0, atol=1e-12)

    def test_scale_zero_recovers_the_undeformed_reference(self):
        for name, case_dir in self.cases.items():
            with self.subTest(case=name):
                process = run_cli(request_for(case_dir, "frame", step=0, scale=0.0,
                                              component="Z"))
                self.assertEqual(process.returncode, 0, process.stderr)
                payload = cli_payload(process)
                native = native_arrays(case_dir)
                count = native["points"].shape[0]
                reference = native["points"] - native["displacement"][:count]
                np.testing.assert_allclose(np.asarray(payload["points"]), reference,
                                           rtol=0.0, atol=1e-9)
                np.testing.assert_allclose(np.asarray(payload["values"]),
                                           native["displacement"][:count, 2],
                                           rtol=0.0, atol=1e-12)
        # The cantilever tip is the concrete measured case: -0.02 mm at step 1, 0.0 at
        # scale 0, and the same point's Z displacement.
        if "cantilever" in self.cases:
            case_dir = self.cases["cantilever"]
            deformed = cli_payload(run_cli(request_for(case_dir, "frame", step=0, scale=1.0,
                                                       component="Z")))
            reference = cli_payload(run_cli(request_for(case_dir, "frame", step=0, scale=0.0,
                                                         component="Z")))
            tip = deformed["extrema"]["Z"]["min_index"]
            self.assertEqual(reference["extrema"]["Z"]["min_index"], tip)
            self.assertAlmostEqual(deformed["points"][tip][2], -0.02000000000080121,
                                   places=10)
            self.assertAlmostEqual(reference["points"][tip][2], 0.0, places=12)
            self.assertAlmostEqual(deformed["step"]["max_abs_displacement"],
                                   0.02000000000080121, places=12)

    def test_every_component_extremum_lies_on_the_displayed_geometry(self):
        case_dir = self.cases.get("cantilever") or next(iter(self.cases.values()))
        for component in ("X", "Y", "Z", "magnitude"):
            with self.subTest(component=component):
                process = run_cli(request_for(case_dir, "frame", step=0, scale=1.0,
                                              component=component))
                self.assertEqual(process.returncode, 0, process.stderr)
                payload = cli_payload(process)
                points = np.asarray(payload["points"])
                values = np.asarray(payload["values"])
                entry = payload["extrema"][component]
                self.assertGreaterEqual(entry["max"], entry["min"])
                self.assertAlmostEqual(entry["max"], values[entry["max_index"]], places=12)
                self.assertAlmostEqual(entry["min"], values[entry["min_index"]], places=12)
                np.testing.assert_allclose(entry["max_position"], points[entry["max_index"]],
                                           rtol=0.0, atol=1e-12)
                np.testing.assert_allclose(entry["min_position"], points[entry["min_index"]],
                                           rtol=0.0, atol=1e-12)

    def test_mesh_is_exactly_the_native_connectivity(self):
        for name, case_dir in self.cases.items():
            with self.subTest(case=name):
                payload = cli_payload(run_cli(request_for(case_dir, "frame", step=0,
                                                          component="X")))
                native = native_arrays(case_dir)
                self.assertEqual(payload["mesh"]["connectivity"],
                                 native["connectivity"].tolist())
                self.assertEqual(payload["mesh"]["offsets"], native["offsets"].tolist())
                self.assertEqual(payload["mesh"]["types"], native["types"].tolist())
                self.assertEqual(payload["mesh"]["cells"], native["types"].size)
                self.assertEqual(payload["mesh"]["mapping_state"], "unavailable")
        if "perforated" in self.cases:
            payload = cli_payload(run_cli(request_for(self.cases["perforated"], "frame",
                                                      step=0, component="X")))
            self.assertEqual(payload["mesh"]["cell_types"], {"5": 16, "9": 76})

    def test_cli_writes_nothing_into_the_run_directory(self):
        case_dir = self.cases.get("cantilever") or next(iter(self.cases.values()))
        before = {entry.name: entry.stat().st_mtime_ns for entry in case_dir.iterdir()}
        run_cli(request_for(case_dir, "metadata"))
        run_cli(request_for(case_dir, "frame", step=0, component="Z"))
        after = {entry.name: entry.stat().st_mtime_ns for entry in case_dir.iterdir()}
        self.assertEqual(before, after)


# --------------------------------------------------------------------------- #
# record-index step slicing on a two-step copy
# --------------------------------------------------------------------------- #

@unittest.skipUnless(h5py is not None and results is not None, "h5py is required")
class StepSlicingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ibra-c1-steps-")
        self.addCleanup(self.temp.cleanup)
        if "perforated" not in available_cases():
            self.skipTest("the perforated two-step P0-7 run directory is absent")
        self.case = copy_case(CASES["perforated"], Path(self.temp.name) / "case")
        native = native_arrays(self.case)
        count = native["points"].shape[0]
        pattern = np.zeros((count, 3))
        pattern[:, 0] = np.linspace(0.0, 0.0009, count)
        pattern[:, 1] = -0.0005
        pattern[:, 2] = 0.0007
        with h5py.File(self.case / "results.vtkhdf", "r+") as handle:
            group = handle[results.VTKHDF_GROUP]
            displacement = group["PointData"][results.DISPLACEMENT][...]
            displacement[count:2 * count] = pattern
            group["PointData"][results.DISPLACEMENT][...] = displacement
            group["Steps"]["Values"][...] = np.asarray((1.0, 2.0))
        self.first = native["displacement"][:count]
        self.pattern = pattern
        self.count = count

    def frame(self, **extra):
        process = run_cli(request_for(self.case, "frame", **extra))
        self.assertEqual(process.returncode, 0, process.stderr)
        return cli_payload(process)

    def test_metadata_reports_two_recorded_steps(self):
        payload = cli_payload(run_cli(request_for(self.case, "metadata")))
        self.assertEqual(payload["vtkhdf"]["steps"], 2)
        self.assertEqual(payload["vtkhdf"]["times"], [1.0, 2.0])
        self.assertEqual(payload["vtkhdf"]["step_point_counts"], [self.count, self.count])

    def test_step_index_selects_its_own_record_block(self):
        first = self.frame(step=0, component="X")
        second = self.frame(step=1, component="X")
        self.assertEqual(first["step"]["index"], 0)
        self.assertEqual(second["step"]["index"], 1)
        self.assertEqual(first["step"]["time"], 1.0)
        self.assertEqual(second["step"]["time"], 2.0)
        np.testing.assert_allclose(np.asarray(first["values"]), self.first[:, 0],
                                   rtol=0.0, atol=1e-12)
        np.testing.assert_allclose(np.asarray(second["values"]), self.pattern[:, 0],
                                   rtol=0.0, atol=1e-12)
        # The second block's Y/Z components are visible through their own components.
        np.testing.assert_allclose(np.asarray(self.frame(step=1, component="Y")["values"]),
                                   self.pattern[:, 1], rtol=0.0, atol=1e-12)
        np.testing.assert_allclose(np.asarray(self.frame(step=1, component="Z")["values"]),
                                   self.pattern[:, 2], rtol=0.0, atol=1e-12)

    def test_scale_multiplies_only_the_selected_step(self):
        reference = np.asarray(self.frame(step=1, scale=0.0, component="X")["points"])
        same = np.asarray(self.frame(step=0, scale=0.0, component="X")["points"])
        np.testing.assert_allclose(reference, same, rtol=0.0, atol=1e-12)
        native = native_arrays(self.case)
        undeformed = native["points"] - self.first
        np.testing.assert_allclose(reference, undeformed, rtol=0.0, atol=1e-9)

    def test_out_of_range_step_is_refused(self):
        process = run_cli(request_for(self.case, "frame", step=2, component="X"))
        self.assertEqual(process.returncode, 1)
        payload = cli_payload(process)
        self.assertIs(payload["ok"], False)
        self.assertIn("step", payload["error"]["message"])
        self.assertNotEqual(process.stderr, "")


# --------------------------------------------------------------------------- #
# misuse and untrustworthy runs fail closed
# --------------------------------------------------------------------------- #

@unittest.skipUnless(h5py is not None and results is not None, "h5py is required")
class RefusalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if "cantilever" not in available_cases():
            raise unittest.SkipTest("the cantilever P0-7 run directory is absent")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ibra-c1-refuse-")
        self.addCleanup(self.temp.cleanup)

    def assert_refused(self, process, code=1, fragment=None):
        self.assertEqual(process.returncode, code, process.stderr)
        payload = cli_payload(process)
        self.assertIs(payload["ok"], False)
        self.assertNotEqual(process.stderr, "")
        self.assertEqual(process.stdout.count("\n"), 1)
        if fragment:
            self.assertIn(fragment, process.stderr)

    def test_metadata_and_frame_refuse_a_wrong_cad(self):
        request = request_for(CASES["cantilever"], "metadata")
        request["current_cad_mm"]["breps"][0]["faces"][0]["surface"]["control_points"][0][1][0] += 0.5
        self.assert_refused(run_cli(request), fragment="CAD")
        frame = request_for(CASES["cantilever"], "frame", step=0, component="Z")
        frame["current_cad_mm"]["breps"][0]["faces"][0]["surface"]["control_points"][0][1][0] -= 0.25
        self.assert_refused(run_cli(frame), fragment="CAD")

    def test_metadata_and_frame_refuse_a_wrong_project(self):
        request = request_for(CASES["cantilever"], "metadata")
        request["current_project"]["solver"]["time_step"] = 99.0
        self.assert_refused(run_cli(request), fragment="project")
        frame = request_for(CASES["cantilever"], "frame", step=0)
        frame["current_project"]["loads"] = []
        self.assert_refused(run_cli(frame), fragment="project")

    def test_non_solve_run_is_refused_for_both_actions(self):
        case = copy_case(CASES["cantilever"], Path(self.temp.name) / "check-only")
        report = load_json(case / "run_report.json")
        report["mode"] = "check"
        (case / "run_report.json").write_text(json.dumps(report), encoding="utf-8")
        self.assert_refused(run_cli(request_for(case, "metadata")), fragment="solve")
        self.assert_refused(run_cli(request_for(case, "frame", step=0)), fragment="solve")

    def test_missing_run_directory_and_missing_result_are_refused(self):
        request = request_for(CASES["cantilever"], "metadata")
        request["run_dir"] = str(Path(self.temp.name) / "absent")
        self.assert_refused(run_cli(request))
        case = copy_case(CASES["cantilever"], Path(self.temp.name) / "no-result")
        (case / "results.vtkhdf").unlink()
        self.assert_refused(run_cli(request_for(case, "metadata")))

    def test_unassociated_exported_model_is_refused(self):
        case = copy_case(CASES["cantilever"], Path(self.temp.name) / "unassociated")
        exported = load_json(case / "geometry.cad.json")
        exported["breps"][0]["faces"][0]["surface"]["control_points"][0][1][1] += 1.0
        (case / "geometry.cad.json").write_text(json.dumps(exported), encoding="utf-8")
        # The open model is the untouched original; only the exported copy differs.
        request = request_for(CASES["cantilever"], "metadata")
        request["run_dir"] = str(case.resolve())
        self.assert_refused(run_cli(request), fragment="CAD")

    def test_malformed_requests_exit_two(self):
        process = subprocess.run([sys.executable, str(CLI)], input="{not json",
                                 capture_output=True, text=True, encoding="utf-8")
        self.assert_refused(process, code=2)
        process = subprocess.run([sys.executable, str(CLI)], input=json.dumps(
            request_for(CASES["cantilever"], "draw")), capture_output=True, text=True,
            encoding="utf-8")
        self.assert_refused(process, code=2)
        broken = request_for(CASES["cantilever"], "metadata")
        broken.pop("current_project")
        process = subprocess.run([sys.executable, str(CLI)], input=json.dumps(broken),
                                 capture_output=True, text=True, encoding="utf-8")
        self.assert_refused(process, code=2)
        process = subprocess.run([sys.executable, str(CLI)], input="",
                                 capture_output=True, text=True, encoding="utf-8")
        self.assert_refused(process, code=2)


# --------------------------------------------------------------------------- #
# display caps
# --------------------------------------------------------------------------- #

@unittest.skipUnless(h5py is not None and results is not None, "h5py is required")
class DisplayCapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if "cantilever" not in available_cases():
            raise unittest.SkipTest("the cantilever P0-7 run directory is absent")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ibra-c1-caps-")
        self.addCleanup(self.temp.cleanup)
        self.base = native_arrays(CASES["cantilever"])["points"]

    def points_copy(self, count):
        copies = int(math.ceil(count / self.base.shape[0]))
        blocks = [self.base + np.asarray((0.0, 0.0, 0.0005 * index))
                  for index in range(copies)]
        return np.vstack(blocks)[:count]

    def synthetic_case(self, name, cells):
        case = copy_case(CASES["cantilever"], Path(self.temp.name) / name)
        points = self.points_copy(max(cells * 4, 4))
        count = points.shape[0]
        connectivity = []
        for index in range(cells):
            start = (index * 4) % count
            connectivity.extend([start, (start + 1) % count, (start + 2) % count,
                                 (start + 3) % count])
        write_native(case / "results.vtkhdf", points=points, connectivity=connectivity,
                     offsets=list(range(0, 4 * cells + 1, 4)), types=[9] * cells,
                     displacement=np.zeros((count, 3)), times=[1.0], step_offsets=[0])
        return case

    def test_over_cell_cap_is_refused_but_metadata_still_works(self):
        case = self.synthetic_case("cells", results_cli.MAX_DISPLAY_CELLS + 1)
        metadata = run_cli(request_for(case, "metadata"))
        self.assertEqual(metadata.returncode, 0, metadata.stderr)
        self.assertEqual(cli_payload(metadata)["vtkhdf"]["cells"],
                         results_cli.MAX_DISPLAY_CELLS + 1)
        process = run_cli(request_for(case, "frame", step=0, component="Z"))
        self.assertEqual(process.returncode, 1)
        self.assertIs(cli_payload(process)["ok"], False)
        self.assertIn("display cap", process.stderr)

    def test_over_point_cap_is_refused(self):
        case = self.synthetic_case("points", 1)
        points = self.points_copy(results_cli.MAX_DISPLAY_POINTS + 1)
        count = points.shape[0]
        write_native(case / "results.vtkhdf", points=points,
                     connectivity=[0, 1, 2, 3], offsets=[0, 4], types=[9],
                     displacement=np.zeros((count, 3)), times=[1.0], step_offsets=[0])
        self.assertEqual(run_cli(request_for(case, "metadata")).returncode, 0)
        process = run_cli(request_for(case, "frame", step=0, component="Z"))
        self.assertEqual(process.returncode, 1)
        self.assertIn("point display cap", process.stderr)


# --------------------------------------------------------------------------- #
# m-N-s unit conversion
# --------------------------------------------------------------------------- #

@unittest.skipUnless(h5py is not None and results is not None, "h5py is required")
class UnitConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ibra-c1-units-")
        self.addCleanup(self.temp.cleanup)

    def test_cad_in_export_units_never_mutates_its_input(self):
        cad = load_json(CASES["cantilever"] / "geometry.cad.json")
        before = json.dumps(cad, sort_keys=True)
        mm_project = {"units": "mm-N-s"}
        untouched = results_cli.cad_in_export_units(mm_project, cad)
        self.assertEqual(json.dumps(untouched, sort_keys=True), before)
        self.assertEqual(json.dumps(cad, sort_keys=True), before)
        metre_project = {"units": "m-N-s"}
        converted = results_cli.cad_in_export_units(metre_project, cad)
        self.assertEqual(json.dumps(cad, sort_keys=True), before)
        self.assertEqual(json.dumps(converted, sort_keys=True),
                         json.dumps(scale_cad_to_metres(cad), sort_keys=True))
        self.assertAlmostEqual(converted["tolerances"]["model_tolerance"],
                               cad["tolerances"]["model_tolerance"] * 0.001, places=15)
        self.assertEqual(results_cli.cad_in_export_units(None, cad), cad)

    @unittest.skipUnless("cantilever" in available_cases(),
                         "the cantilever P0-7 run directory is absent")
    def test_metre_project_frame_matches_the_millimetre_frame(self):
        source = CASES["cantilever"]
        case = copy_case(source, Path(self.temp.name) / "metre-case")
        project = load_json(source / "project.ibra-project.json")
        project["units"] = "m-N-s"
        report = load_json(source / "export_report.json")
        report["geometry_units"] = "m"
        cad_mm = load_json(source / "geometry.cad.json")
        (case / "project.ibra-project.json").write_text(json.dumps(project),
                                                       encoding="utf-8")
        (case / "export_report.json").write_text(json.dumps(report), encoding="utf-8")
        (case / "geometry.cad.json").write_text(json.dumps(scale_cad_to_metres(cad_mm)),
                                                encoding="utf-8")
        run_report = load_json(source / "run_report.json")
        run_report["max_abs_displacement"] *= 0.001
        (case / "run_report.json").write_text(json.dumps(run_report), encoding="utf-8")
        with h5py.File(case / "results.vtkhdf", "r+") as handle:
            group = handle[results.VTKHDF_GROUP]
            group["Points"][...] = np.asarray(group["Points"][...], dtype=float) * 0.001
            group["PointData"][results.DISPLACEMENT][...] = np.asarray(
                group["PointData"][results.DISPLACEMENT][...], dtype=float) * 0.001

        # The FreeCAD cache CAD is millimetres; the CLI must convert it in memory.
        request = {"action": "metadata", "run_dir": str(case.resolve()),
                   "current_project": project, "current_cad_mm": cad_mm}
        metadata = run_cli(request)
        self.assertEqual(metadata.returncode, 0, metadata.stderr)
        described = cli_payload(metadata)["case"]
        self.assertEqual(described["geometry_units"], "m")
        self.assertEqual(described["unit_scale_to_mm"], 1000.0)
        self.assertTrue(described["solved_ready"])

        frame_request = dict(request, action="frame", step=0, scale=1.0, component="Z")
        metre = cli_payload(run_cli(frame_request))
        millimetre = cli_payload(run_cli(request_for(source, "frame", step=0, scale=1.0,
                                                     component="Z")))
        self.assertEqual(metre["unit"], "mm")
        self.assertEqual(metre["step"]["source_unit"], "m")
        self.assertEqual(metre["step"]["unit_factor"], 1000.0)
        np.testing.assert_allclose(np.asarray(metre["points"]),
                                   np.asarray(millimetre["points"]), rtol=0.0, atol=1e-9)
        np.testing.assert_allclose(np.asarray(metre["values"]),
                                   np.asarray(millimetre["values"]), rtol=0.0, atol=1e-12)
        self.assertEqual(metre["run_report_consistency"], "match")

        # The same metre CAD passed as if it were the millimetre cache would be scaled
        # twice and must be refused, never silently displayed.
        twice = dict(request, action="frame", step=0,
                     current_cad_mm=scale_cad_to_metres(cad_mm))
        process = run_cli(twice)
        self.assertEqual(process.returncode, 1)
        self.assertIn("CAD", process.stderr)


# --------------------------------------------------------------------------- #
# result-layer pure core (no FreeCAD, no h5py)
# --------------------------------------------------------------------------- #

class _FakePropertyObject:
    """Only enough FreeCAD object surface for the ownership predicate.

    This is not FreeCAD acceptance: it proves that ``_mark``/``owns`` key on the
    reserved marker value. The real document layer is accepted by the host smoke test.
    """

    def __init__(self, **props):
        self.__dict__["props"] = dict(props)

    def addProperty(self, kind, name, group):
        self.props.setdefault(name, None)

    def setEditorMode(self, name, mode):
        self.props.setdefault(name + "__mode", mode)

    def __getattr__(self, name):
        props = self.__dict__.get("props", {})
        if name in props:
            return props[name]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        props = self.__dict__.get("props")
        if props is not None and name in props:
            props[name] = value
        else:
            self.__dict__[name] = value


def sample_frame(*, points=None, values=None, connectivity=None, offsets=None, types=None,
                 component="magnitude", extrema=None):
    points = points if points is not None else [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]
    values = values if values is not None else [0.0, 1.0, 2.0, 3.0]
    return {
        "ok": True,
        "points": points,
        "values": values,
        "mesh": {
            "connectivity": connectivity if connectivity is not None else [0, 1, 2, 3],
            "offsets": offsets if offsets is not None else [0, 4],
            "types": types if types is not None else [9],
        },
        "step": {"index": 0, "time": 1.0, "scale": 1.0, "component": component,
                 "requested_unit": "mm"},
        "extrema": extrema if extrema is not None else {
            "magnitude": {"min": 0.0, "max": 3.0, "min_index": 0, "max_index": 3,
                          "min_position": [0, 0, 0], "max_position": [0, 1, 0],
                          "unit": "mm"}},
    }


class ResultLayerPureTests(unittest.TestCase):
    def test_gradient_is_fixed_and_clamped(self):
        self.assertEqual(result_layer.gradient_color(0.0), (0.0, 0.0, 1.0))
        self.assertEqual(result_layer.gradient_color(0.5), (0.0, 1.0, 0.0))
        self.assertEqual(result_layer.gradient_color(1.0), (1.0, 0.0, 0.0))
        self.assertEqual(result_layer.gradient_color(-5.0), (0.0, 0.0, 1.0))
        self.assertEqual(result_layer.gradient_color(5.0), (1.0, 0.0, 0.0))
        reds = [result_layer.gradient_color(step / 10.0)[0] for step in range(11)]
        self.assertEqual(reds, sorted(reds))
        self.assertTrue(all(0.0 <= channel <= 1.0
                            for step in range(21)
                            for channel in result_layer.gradient_color(step / 20.0)))

    def test_colors_for_values_handles_a_constant_range(self):
        colors, low, high, constant = result_layer.colors_for_values([2.0, 2.0, 2.0])
        self.assertEqual(colors, [result_layer.gradient_color(0.5)] * 3)
        self.assertEqual((low, high), (2.0, 2.0))
        self.assertTrue(constant)
        colors, low, high, constant = result_layer.colors_for_values([0.0, 1.0])
        self.assertEqual(colors[0], (0.0, 0.0, 1.0))
        self.assertEqual(colors[1], (1.0, 0.0, 0.0))
        self.assertFalse(constant)
        with self.assertRaises(result_layer.ResultLayerError):
            result_layer.colors_for_values([0.0, float("nan")])

    def test_cell_faces_keeps_native_topology(self):
        quad = result_layer.cell_faces([0, 1, 2, 3], [0, 4], [9], [0.0, 1.0, 2.0, 3.0])
        self.assertEqual(quad["faces"], [[0, 1, 2, 3]])
        self.assertEqual(quad["face_values"], [1.5])
        split = result_layer.cell_faces([0, 1, 2, 3], [0, 4], [9], [0.0, 1.0, 2.0, 3.0],
                                        split_quads=True)
        self.assertEqual(split["faces"], [[0, 1, 2], [0, 2, 3]])
        self.assertEqual(split["triangles_count"], 2)
        triangle = result_layer.cell_faces([0, 1, 2], [0, 3], [5], [1.0, 2.0, 3.0])
        self.assertEqual(triangle["faces"], [[0, 1, 2]])
        mixed = result_layer.cell_faces([0, 1, 2, 0, 1, 2, 3], [0, 3, 7], [5, 9],
                                        [0.0, 1.0, 2.0, 3.0])
        self.assertEqual(mixed["faces"], [[0, 1, 2], [0, 1, 2, 3]])
        self.assertEqual(mixed["cells"], 2)

    def test_cell_faces_refuses_malformed_native_arrays(self):
        values = [0.0, 1.0, 2.0, 3.0]
        for name, call in {
                "unsupported type": lambda: result_layer.cell_faces([0, 1, 2], [0, 3], [12], values),
                "wrong width": lambda: result_layer.cell_faces([0, 1, 2], [0, 3], [9], values),
                "offsets not at zero": lambda: result_layer.cell_faces([0, 1, 2, 3], [1, 5], [9], values),
                "offsets end mismatch": lambda: result_layer.cell_faces([0, 1, 2, 3], [0, 3], [9], values),
                "offset count mismatch": lambda: result_layer.cell_faces([0, 1, 2, 3], [0, 4], [9, 9], values),
                "index out of range": lambda: result_layer.cell_faces([0, 1, 2, 9], [0, 4], [9], values),
                "non-integer type": lambda: result_layer.cell_faces([0, 1, 2, 3], [0, 4], [9.5], values),
        }.items():
            with self.subTest(case=name):
                with self.assertRaises(result_layer.ResultLayerError):
                    call()

    def test_extrema_markers_report_positions_and_values(self):
        extrema = {"Z": {"min": -0.02, "max": 0.0, "min_index": 7, "max_index": 2,
                         "min_position": [1.0, 2.0, -0.02], "max_position": [0.0, 0.0, 0.0],
                         "unit": "mm"}}
        markers = result_layer.extrema_markers(extrema, "Z")
        self.assertEqual([marker["kind"] for marker in markers], ["max", "min"])
        self.assertEqual(markers[0]["position"], [0.0, 0.0, 0.0])
        self.assertEqual(markers[1]["value"], -0.02)
        self.assertEqual(markers[1]["index"], 7)
        self.assertEqual(result_layer.extrema_markers(extrema, "Q"), [])
        self.assertEqual(result_layer.extrema_markers(None, "Z"), [])

    def test_build_render_plan_maps_colours_and_markers(self):
        plan = result_layer.build_render_plan(sample_frame())
        self.assertEqual(plan["component"], "magnitude")
        self.assertEqual(plan["faces"], [[0, 1, 2, 3]])
        self.assertEqual(len(plan["face_colors"]), len(plan["faces"]))
        self.assertEqual(plan["points_count"], 4)
        self.assertEqual(plan["cells_count"], 1)
        self.assertEqual(plan["mapping_state"], "unavailable")
        self.assertEqual(plan["point_value_range"], [0.0, 3.0])
        self.assertEqual([marker["kind"] for marker in plan["markers"]], ["max", "min"])
        self.assertGreater(plan["marker_radius"], 0.0)

    def test_build_render_plan_refuses_failed_oversized_or_mismatched_frames(self):
        with self.assertRaises(result_layer.ResultLayerError):
            result_layer.build_render_plan(dict(sample_frame(), ok=False))
        with self.assertRaises(result_layer.ResultLayerError):
            result_layer.build_render_plan(sample_frame(values=[0.0, 1.0]))
        with self.assertRaises(result_layer.ResultLayerError):
            result_layer.build_render_plan(sample_frame(), max_cells=0)
        with self.assertRaises(result_layer.ResultLayerError):
            result_layer.build_render_plan(sample_frame(), max_points=3)
        with self.assertRaises(result_layer.ResultLayerError):
            result_layer.build_render_plan(sample_frame(component="Q"))
        with self.assertRaises(result_layer.ResultLayerError):
            result_layer.build_render_plan(sample_frame(values=[0.0, 1.0, 2.0, float("inf")]))

    def test_ownership_predicate_uses_the_marker_value(self):
        ours = _FakePropertyObject()
        result_layer.ResultLayer._mark(ours)
        self.assertTrue(result_layer.ResultLayer.owns(ours))
        self.assertEqual(getattr(ours, result_layer.MARKER_PROPERTY),
                         result_layer.MARKER_VALUE)
        foreign = _FakePropertyObject(IbraResultTag="someone-else")
        self.assertFalse(result_layer.ResultLayer.owns(foreign))
        unmarked = _FakePropertyObject()
        self.assertFalse(result_layer.ResultLayer.owns(unmarked))

    def test_cap_constants_match_the_cli(self):
        self.assertEqual(result_layer.MAX_DRAW_CELLS, results_cli.MAX_DISPLAY_CELLS)
        self.assertEqual(result_layer.MAX_DRAW_POINTS, results_cli.MAX_DISPLAY_POINTS)
        self.assertEqual(result_layer.GROUP_NAME, "IBRA_Result")
        self.assertTrue(result_layer.SURFACE_NAME.startswith(result_layer.PREFIX))
        self.assertTrue(result_layer.EXTREMA_NAME.startswith(result_layer.PREFIX))

    def test_module_imports_without_h5py_numpy_freecad_or_part(self):
        plugin = ROOT / "freecad/Mod/KratosIBRA"
        code = (
            "import sys\n"
            "blocked = {'h5py', 'numpy', 'FreeCAD', 'FreeCADGui', 'Part', 'PySide'}\n"
            "class Blocker:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] in blocked:\n"
            "            raise ImportError('blocked ' + name)\n"
            "        return None\n"
            "sys.meta_path.insert(0, Blocker())\n"
            f"sys.path.insert(0, r'{plugin}')\n"
            "import kratos_iga.result_layer as layer\n"
            "assert layer.MAX_DRAW_CELLS == 10000\n"
            "assert 'h5py' not in sys.modules and 'numpy' not in sys.modules\n"
            "assert 'FreeCAD' not in sys.modules and 'Part' not in sys.modules\n"
            "print('ok')\n")
        process = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                 text=True, encoding="utf-8")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), "ok")


@unittest.skipUnless(h5py is not None and results is not None, "h5py is required")
class ResultLayerOnRealFrameTests(unittest.TestCase):
    """The layer's pure core against a real CLI frame - still no FreeCAD object."""

    @classmethod
    def setUpClass(cls):
        if "cantilever" not in available_cases():
            raise unittest.SkipTest("the cantilever P0-7 run directory is absent")

    def test_real_quad_frame_becomes_quad_faces_and_split_triangles(self):
        case = CASES["cantilever"]
        process = run_cli(request_for(case, "frame", step=0, scale=1.0, component="Z"))
        self.assertEqual(process.returncode, 0, process.stderr)
        frame = cli_payload(process)
        plan = result_layer.build_render_plan(frame, component="Z")
        self.assertEqual(plan["cells_count"], 400)
        self.assertEqual(plan["faces_count"], 400)
        self.assertEqual(plan["triangles_count"], 0)
        self.assertEqual(len(plan["face_colors"]), 400)
        self.assertEqual(plan["points_count"], 1600)
        self.assertEqual([marker["kind"] for marker in plan["markers"]], ["max", "min"])
        split = result_layer.build_render_plan(frame, component="Z", split_quads=True)
        self.assertEqual(split["faces_count"], 800)
        self.assertEqual(split["triangles_count"], 800)
        summary = result_layer.summary_from_plan(plan, plan["faces_count"], 0)
        self.assertEqual(summary["mapping_state"], "unavailable")
        json.dumps(summary, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
