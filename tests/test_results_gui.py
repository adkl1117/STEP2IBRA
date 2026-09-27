"""P0-6 shard C2 tests: the panel result section's wiring, failure policy and parsing.

These are **static, lightweight tests**. They import ``kratos_iga.gui`` under stubbed
``FreeCAD``/``FreeCADGui``/``Part``/``PySide`` modules so the FreeCAD-free result helpers,
the shared :class:`ResultState`, the request/response plumbing and the widget structure
can be exercised in the backend venv. They are not native GUI acceptance: the real
FreeCAD document drawing, visibility restore and native-VTKHDF frames are accepted by the
host smoke test and the coordinator's FreeCAD run.

Covered here:

* the exact backend request per action and the strict stdout response parser;
* the bounded ``subprocess`` runner (success, refusal, timeout) with a stub CLI;
* that the panel and its controller share **one** ``ResultState`` and that a failed
  load/draw removes the previously drawn result instead of keeping it;
* the four independent evidence levels (a solve can never promote the reference level);
* the separated force / equilibrium text (penalty raw right-hand sides are never shown
  as a physical reaction);
* the Analysis-page result controls exist with the ``face/UV unavailable`` note.

Every file this test creates lives in a temporary directory. No file under ``runtime/``
is modified.
"""
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "freecad/Mod/KratosIBRA"
sys.path.insert(0, str(PLUGIN))


# --------------------------------------------------------------------------- #
# import gui.py without a real FreeCAD/Qt (the helpers under test never touch them)
# --------------------------------------------------------------------------- #

def _module(name):
    module = types.ModuleType(name)
    module.__getattr__ = lambda attribute: None
    return module


def _install_stubs():
    if "FreeCAD" in sys.modules:
        return
    class _WidgetBase:
        pass

    pyside = types.ModuleType("PySide")
    qtcore = types.ModuleType("PySide.QtCore")
    qtgui = types.ModuleType("PySide.QtGui")
    qtwidgets = types.ModuleType("PySide.QtWidgets")
    qtwidgets.QWidget = _WidgetBase
    qtwidgets.QDockWidget = _WidgetBase
    pyside.QtCore = qtcore
    pyside.QtGui = qtgui
    pyside.QtWidgets = qtwidgets
    sys.modules.update({
        "FreeCAD": _module("FreeCAD"),
        "FreeCADGui": _module("FreeCADGui"),
        "Part": _module("Part"),
        "PySide": pyside,
    })


_install_stubs()
from kratos_iga import gui  # noqa: E402


# --------------------------------------------------------------------------- #
# fixture payloads
# --------------------------------------------------------------------------- #

def metadata_payload(run_dir="C:/runs/ibra_run_1"):
    return {
        "ok": True, "action": "metadata", "run_dir": run_dir,
        "case": {"run_dir": run_dir, "mode": "solve", "move_mesh_flag": True,
                 "geometry_units": "mm", "unit_scale_to_mm": 1.0, "solved_ready": True,
                 "reasons": [], "warnings": []},
        "vtkhdf": {"points": 4, "cells": 1, "connectivity_ids": 4, "steps": 1,
                   "times": [1.0], "cell_types": {"9": 1}, "file_bytes": 123},
        "mapping_state": "unavailable",
        "mapping_note": ("a plain VTKHDF carries no face/UV mapping; the display cells are "
                         "not the trimmed integration domain"),
    }


def frame_payload(component="Z"):
    return {
        "ok": True, "action": "frame", "run_dir": "C:/runs/ibra_run_1", "unit": "mm",
        "step": {"index": 0, "time": 1.0, "scale": 1.0, "component": component,
                 "requested_unit": "mm", "source_unit": "mm", "unit_factor": 1.0},
        "extrema": {component: {"min": -0.02, "max": 0.0, "min_index": 3, "max_index": 0,
                                "min_position": [1.0, 2.0, -0.02],
                                "max_position": [0.0, 0.0, 0.0], "unit": "mm"}},
        "points": [[0, 0, 0]] * 4, "values": [0.0, 0.0, 0.0, -0.02],
        "mesh": {"connectivity": [0, 1, 2, 3], "offsets": [0, 4], "types": [9],
                 "cells": 1, "points": 4, "cell_types": {"9": 1},
                 "mapping_state": "unavailable"},
    }


def forces_report(raw_penalty=None):
    return {
        "mode": "solve",
        "forces": {
            "status": "measured", "schema": "ibra.run_forces/v1", "step_count": 1,
            "measured_at": "FinalizeSolutionStep",
            "last": {
                "step": 1.0, "time": 1.0, "mode": "solve", "status": "measured",
                "strong_supports": {"status": "measured",
                                    "total": [-0.0034, 0.0, 0.001]},
                "applied_loads": {"status": "measured", "total": [0.0, 0.0, -0.001]},
                "penalty_supports": {"status": "measured" if raw_penalty else "none",
                                     "raw_total": raw_penalty,
                                     "reaction_status": "unavailable",
                                     "physical_reaction": None},
                "penalty_couplings": {"status": "none", "raw_total": None,
                                      "reaction_status": "unavailable"},
                "equilibrium": {
                    "status": "unverified", "signed_residual_relative": 1.0,
                    "coverage": {"signed_complete": True, "penalty_present": bool(raw_penalty)},
                    "convergence": {"verified": False, "solver_type": "static",
                                    "analysis_type": "linear",
                                    "solve_reported_converged": True},
                },
            },
        },
        "checks": {"area": "pass"},
    }


# --------------------------------------------------------------------------- #
# request / response / bounded runner
# --------------------------------------------------------------------------- #

class ResultRequestTests(unittest.TestCase):
    def test_metadata_request_carries_only_the_actions_own_fields(self):
        request = gui.build_result_request("metadata", " C:/run ", {"units": "mm-N-s"},
                                           {"breps": []})
        self.assertEqual(sorted(request), ["action", "current_cad_mm", "current_project",
                                           "run_dir"])
        self.assertEqual(request["run_dir"], " C:/run ")
        self.assertNotIn("step", request)
        self.assertNotIn("scale", request)
        self.assertNotIn("component", request)

    def test_frame_request_carries_step_scale_and_component(self):
        request = gui.build_result_request("frame", "C:/run", {"units": "mm-N-s"},
                                           {"breps": []}, step=2, scale=3.5, component="X")
        self.assertEqual(request["step"], 2)
        self.assertEqual(request["scale"], 3.5)
        self.assertEqual(request["component"], "X")

    def test_request_refuses_bad_inputs(self):
        for name, call in {
                "action": lambda: gui.build_result_request("draw", "C:/run", {}, {}),
                "empty run dir": lambda: gui.build_result_request("metadata", "", {}, {}),
                "project": lambda: gui.build_result_request("metadata", "C:/run", None, {}),
                "cad": lambda: gui.build_result_request("metadata", "C:/run", {}, None),
                "component": lambda: gui.build_result_request("frame", "C:/run", {}, {},
                                                              component="Q"),
                "negative step": lambda: gui.build_result_request("frame", "C:/run", {}, {},
                                                                  step=-1),
                "bool step": lambda: gui.build_result_request("frame", "C:/run", {}, {},
                                                              step=True),
                "negative scale": lambda: gui.build_result_request("frame", "C:/run", {}, {},
                                                                   scale=-1.0),
                "nan scale": lambda: gui.build_result_request("frame", "C:/run", {}, {},
                                                              scale=float("nan")),
        }.items():
            with self.subTest(case=name):
                with self.assertRaises(gui.ResultViewError):
                    call()


class ResultPayloadTests(unittest.TestCase):
    def test_accepts_a_matching_ok_payload(self):
        payload = gui.parse_result_payload(json.dumps(metadata_payload()), "metadata")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["vtkhdf"]["steps"], 1)

    def test_refuses_a_refused_payload_with_its_readable_message(self):
        text = json.dumps({"ok": False, "action": "metadata",
                           "error": {"type": "AssociationError",
                                     "message": "the current CAD does not match"}})
        with self.assertRaises(gui.ResultViewError) as caught:
            gui.parse_result_payload(text, "metadata")
        self.assertIn("does not match", str(caught.exception))

    def test_refuses_wrong_action_empty_and_malformed_stdout(self):
        for name, text, action in (
                ("wrong action", json.dumps(metadata_payload()), "frame"),
                ("empty", "", "metadata"),
                ("not json", "{not json", "metadata"),
                ("not an object", json.dumps([1, 2, 3]), "metadata")):
            with self.subTest(case=name):
                with self.assertRaises(gui.ResultViewError):
                    gui.parse_result_payload(text, action)


class RunResultCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ibra-c2-cli-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def script(self, name, body):
        path = self.folder / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_success_passes_the_request_on_stdin_and_returns_the_payload(self):
        script = self.script("echo.py", (
            "import json, sys\n"
            "request = json.loads(sys.stdin.read())\n"
            "print(json.dumps({'ok': True, 'action': request['action'], 'echo': request}))\n"))
        request = gui.build_result_request("frame", "C:/run", {"units": "mm-N-s"}, {},
                                           step=2, scale=1.5, component="Y")
        payload = gui.run_result_cli(sys.executable, script, request, timeout=30)
        self.assertEqual(payload["echo"]["run_dir"], "C:/run")
        self.assertEqual(payload["echo"]["step"], 2)
        self.assertEqual(payload["echo"]["component"], "Y")

    def test_nonzero_exit_refuses_and_never_returns_a_payload(self):
        script = self.script("refuse.py", (
            "import json, sys\n"
            "request = json.loads(sys.stdin.read())\n"
            "sys.stdout.write(json.dumps({'ok': False, 'action': request['action'],\n"
            "    'error': {'type': 'AssociationError', 'message': 'wrong CAD'}}))\n"
            "sys.stderr.write('results_cli: AssociationError: wrong CAD\\n')\n"
            "sys.exit(1)\n"))
        with self.assertRaises(gui.ResultViewError) as caught:
            gui.run_result_cli(sys.executable, script,
                               gui.build_result_request("metadata", "C:/run", {}, {}),
                               timeout=30)
        self.assertIn("wrong CAD", str(caught.exception))
        self.assertIn("exit 1", str(caught.exception))

    def test_timeout_is_bounded(self):
        script = self.script("slow.py", "import time\ntime.sleep(30)\n")
        with self.assertRaises(gui.ResultViewError) as caught:
            gui.run_result_cli(sys.executable, script,
                               gui.build_result_request("metadata", "C:/run", {}, {}),
                               timeout=0.5)
        self.assertIn("did not answer", str(caught.exception))

    def test_default_timeout_is_bounded(self):
        self.assertLessEqual(gui.RESULT_CLI_TIMEOUT_SECONDS, 30.0)
        self.assertGreater(gui.RESULT_CLI_TIMEOUT_SECONDS, 0.0)


# --------------------------------------------------------------------------- #
# the shared state and the failure-clears-old-result policy
# --------------------------------------------------------------------------- #

class _LocalizedHost:
    """Expose the panel's real language helpers to the result-only fixtures."""
    tr = gui.Panel.tr
    _text = gui.Panel._text
    _label = gui.Panel._label
    _hint = gui.Panel._hint
    _button = gui.Panel._button
    set_message = gui.Panel.set_message

    def __init__(self):
        self.language = "en"
        gui.i18n.set_language("en")
        self._text_registry = gui.i18n_qt.TextRegistry()


class _Host(_LocalizedHost):
    """Only the panel methods the result state construction actually needs."""

    _fetch_result_payload = gui.Panel._fetch_result_payload
    _render_result_frame = gui.Panel._render_result_frame
    _clear_result_objects = gui.Panel._clear_result_objects
    use_last_result_run = gui.Panel.use_last_result_run
    browse_result_run = gui.Panel.browse_result_run
    load_result_metadata = gui.Panel.load_result_metadata
    draw_result_frame = gui.Panel.draw_result_frame
    clear_result_display = gui.Panel.clear_result_display


class SharedStateTests(unittest.TestCase):
    def test_panel_state_and_controller_state_are_one_object(self):
        host = _Host()
        state = gui.Panel._build_result_state(host)
        self.assertIs(host.result_state, state)
        self.assertIs(host.result_controller.state, host.result_state)

    def test_controller_uses_the_injected_state(self):
        injected = gui.ResultState()
        controller = gui.ResultController(fetch=lambda request: {}, render=lambda *a: {},
                                          unrender=lambda: None, state=injected)
        self.assertIs(controller.state, injected)


class _Recorder:
    def __init__(self):
        self.rendered = []
        self.unrendered = 0

    def unrender(self):
        self.unrendered += 1

    def render(self, payload, component, cad_objects):
        self.rendered.append((component, tuple(cad_objects)))
        return {"cells": payload["mesh"]["cells"], "faces_drawn": 1, "faces_skipped": 0,
                "mapping_state": "unavailable", "mapping_note": "no face/UV mapping",
                "notes": []}


class FailureClearsOldResultTests(unittest.TestCase):
    def setUp(self):
        self.state = gui.ResultState()
        self.recorder = _Recorder()
        self.payloads = {"metadata": metadata_payload(), "frame": frame_payload()}
        self.requests = []

        def fetch(request):
            self.requests.append(request)
            if self.failing:
                raise gui.ResultViewError("backend refused")
            return self.payloads[request["action"]]

        self.failing = False
        self.controller = gui.ResultController(fetch=fetch, render=self.recorder.render,
                                               unrender=self.recorder.unrender,
                                               state=self.state)

    def draw(self):
        return self.controller.draw_frame({}, {}, step=0, scale=1.0, component="Z",
                                          cad_objects=["IBRA_Face_2"])

    def test_successful_load_and_draw_share_the_state(self):
        self.controller.load_metadata("C:/run", {}, {})
        payload, summary = self.draw()
        self.assertIs(self.controller.state, self.state)
        self.assertEqual(self.state.run_dir, "C:/run")
        self.assertIs(self.state.frame, payload)
        self.assertEqual(self.requests[-1]["action"], "frame")
        self.assertEqual(self.recorder.rendered, [("Z", ("IBRA_Face_2",))])

    def test_frame_failure_removes_the_previous_drawing_and_metadata(self):
        self.controller.load_metadata("C:/run", {}, {})
        self.draw()
        self.assertIsNotNone(self.state.frame)
        self.failing = True
        with self.assertRaises(gui.ResultViewError):
            self.draw()
        # The stale picture must not survive a refused new frame.
        self.assertIsNone(self.state.frame)
        self.assertIsNone(self.state.summary)
        self.assertIsNone(self.state.metadata)
        self.assertEqual(self.recorder.unrendered, 1)
        self.assertEqual(self.state.error, "backend refused")

    def test_metadata_failure_clears_any_drawn_result(self):
        self.controller.load_metadata("C:/run", {}, {})
        self.draw()
        self.failing = True
        with self.assertRaises(gui.ResultViewError):
            self.controller.load_metadata("C:/run", {}, {})
        self.assertIsNone(self.state.frame)
        self.assertIsNone(self.state.metadata)
        self.assertEqual(self.recorder.unrendered, 1)

    def test_render_failure_also_clears_the_previous_drawing(self):
        self.controller.load_metadata("C:/run", {}, {})
        self.draw()
        self.controller._render = lambda *args: (_ for _ in ()).throw(
            gui.ResultViewError("not drawable"))
        with self.assertRaises(gui.ResultViewError):
            self.draw()
        self.assertIsNone(self.state.frame)
        self.assertEqual(self.recorder.unrendered, 1)


# --------------------------------------------------------------------------- #
# panel-level stale-result refusal: every failed attempt clears the old drawing
# --------------------------------------------------------------------------- #

class _FakeLayer:
    """Stands in for ResultLayer: records clears without touching FreeCAD."""

    def __init__(self):
        self.doc = None
        self.clear_calls = 0

    def clear(self, doc=None):
        self.clear_calls += 1
        self.doc = None


class _FakeView:
    def __init__(self):
        self.text = ""

    def setPlainText(self, text):
        self.text = text

    def setText(self, text):
        self.text = text


class _FakeLog:
    def __init__(self):
        self.lines = []

    def appendPlainText(self, text):
        self.lines.append(text)


class _FakeText:
    def __init__(self, value=""):
        self.value = value

    def text(self):
        return self.value


class _PanelHost(_LocalizedHost):
    """Runs the real panel result methods against fakes; no Qt, no FreeCAD."""

    _fetch_result_payload = gui.Panel._fetch_result_payload
    _render_result_frame = gui.Panel._render_result_frame
    _clear_result_objects = gui.Panel._clear_result_objects
    _build_result_state = gui.Panel._build_result_state
    load_result_metadata = gui.Panel.load_result_metadata
    draw_result_frame = gui.Panel.draw_result_frame
    clear_result_display = gui.Panel.clear_result_display
    _render_result_views = gui.Panel._render_result_views
    _refresh_result_evidence = gui.Panel._refresh_result_evidence
    _read_result_run_report = gui.Panel._read_result_run_report
    _read_reference_evidence = gui.Panel._read_reference_evidence
    _selected_result_step = gui.Panel._selected_result_step
    _selected_result_scale = gui.Panel._selected_result_scale
    _selected_result_component = gui.Panel._selected_result_component
    _populate_result_steps = gui.Panel._populate_result_steps
    _known_run_dirs = gui.Panel._known_run_dirs
    refresh_result_run_dirs = gui.Panel.refresh_result_run_dirs
    selected_result_run_dir = gui.Panel.selected_result_run_dir
    guard = gui.Panel.guard

    def __init__(self, run_dir=""):
        super().__init__()
        self.process = None
        self.project = {}
        self.last_run = None
        self.last_export = None
        self.doc = None
        self.cache = None
        self.errors = []
        self._run_dir = run_dir
        self.result_layer = _FakeLayer()
        self._build_result_state()
        self.result_metadata_view = _FakeView()
        self.result_extrema_view = _FakeView()
        self.result_evidence_view = _FakeView()
        self.result_forces_view = _FakeView()
        self.result_status = _FakeView()
        self.status = _FakeView()
        self.log = _FakeLog()
        self.backend_python = _FakeText("python")
        self.sync = lambda *args, **kwargs: None
        self._result_cad_mm = lambda: {}
        self.selected_result_run_dir = lambda: self._run_dir

    def error(self, exception):
        self.errors.append(str(exception))

    def seed_drawn_result(self, run_dir="C:/old"):
        """Simulate a previously displayed result on the fake layer."""
        self.result_state.load_metadata(str(run_dir), metadata_payload())
        self.result_state.load_frame(frame_payload(), {"faces_drawn": 1})
        self.result_state.run_report = {"mode": "solve"}
        self.result_layer.doc = object()  # something is on screen
        self.result_layer.clear_calls = 0


class PanelStaleResultRefusalTests(unittest.TestCase):
    """A refused load/draw must remove the old owned result, never leave it visible."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ibra-c2-panel-")
        self.addCleanup(self.temp.cleanup)
        self.run_dir = Path(self.temp.name) / "ibra_run_old"
        self.run_dir.mkdir()
        (self.run_dir / "run_report.json").write_text(
            json.dumps({"mode": "solve", "max_abs_displacement": 0.02}), encoding="utf-8")
        (self.run_dir / "example_verification.json").write_text(
            json.dumps({"mode": "solve", "expected_abs_tip_displacement_mm": 0.02,
                        "relative_error": 4.0e-11}), encoding="utf-8")

    def test_preflight_process_failure_clears_the_old_drawing(self):
        host = _PanelHost(str(self.run_dir))
        host.seed_drawn_result()
        host.process = object()  # a background task is running
        host.load_result_metadata()
        self.assertEqual(host.result_layer.clear_calls, 1)
        self.assertIsNone(host.result_state.frame)
        self.assertIsNone(host.result_state.metadata)
        self.assertTrue(host.errors)

    def test_preflight_run_directory_failure_clears_and_resets_evidence(self):
        host = _PanelHost("")  # nothing selected
        host.seed_drawn_result()
        host.load_result_metadata()
        self.assertEqual(host.result_layer.clear_calls, 1)
        self.assertIsNone(host.result_state.frame)
        self.assertIsNone(host.result_state.metadata)
        self.assertIsNone(host.result_state.run_dir)
        self.assertIsNone(host.result_state.run_report)
        self.assertIsNone(host.result_state.reference_evidence)
        self.assertTrue(host.errors)

    def test_missing_run_directory_clears_the_old_drawing(self):
        host = _PanelHost(str(Path(self.temp.name) / "absent"))
        host.seed_drawn_result()
        host.load_result_metadata()
        self.assertEqual(host.result_layer.clear_calls, 1)
        self.assertIsNone(host.result_state.frame)
        self.assertIsNone(host.result_state.metadata)
        self.assertTrue(host.errors)

    def test_project_sync_failure_clears_the_old_drawing(self):
        host = _PanelHost(str(self.run_dir))
        host.seed_drawn_result()

        def failing_sync(*args, **kwargs):
            raise ValueError("The displayed CAD geometry has changed.")

        host.sync = failing_sync
        host.load_result_metadata()
        self.assertEqual(host.result_layer.clear_calls, 1)
        self.assertIsNone(host.result_state.frame)
        self.assertIsNone(host.result_state.metadata)
        # The selected run is a real directory, so its own evidence is preserved.
        self.assertEqual(host.result_state.run_dir, str(self.run_dir))
        self.assertEqual(host.result_state.run_report["mode"], "solve")
        self.assertIsNotNone(host.result_state.reference_evidence)
        self.assertTrue(any("CAD geometry" in message for message in host.errors))

    def test_current_cad_failure_clears_the_old_drawing(self):
        host = _PanelHost(str(self.run_dir))
        host.seed_drawn_result()

        def failing_cad():
            raise ValueError("The current cache has no geometry.cad.json")

        host._result_cad_mm = failing_cad
        host.load_result_metadata()
        self.assertEqual(host.result_layer.clear_calls, 1)
        self.assertIsNone(host.result_state.frame)
        self.assertIsNone(host.result_state.metadata)
        self.assertEqual(host.result_state.run_report["mode"], "solve")
        self.assertTrue(host.errors)

    def test_wrong_model_refusal_clears_the_old_drawing(self):
        host = _PanelHost(str(self.run_dir))
        host.seed_drawn_result()

        def refusing_fetch(request):
            raise gui.ResultViewError("the current CAD does not match the exported CAD")

        host.result_controller._fetch = refusing_fetch
        host.load_result_metadata()
        # The controller clears on its own refusal and the panel clears again; both are the
        # same idempotent cleanup, and the point is that the old drawing is gone.
        self.assertGreaterEqual(host.result_layer.clear_calls, 1)
        self.assertIsNone(host.result_state.frame)
        self.assertIsNone(host.result_state.metadata)
        # Preserved: the selected run's own report/reference evidence still describes it.
        self.assertEqual(host.result_state.run_dir, str(self.run_dir))
        self.assertEqual(host.result_state.run_report["mode"], "solve")
        self.assertEqual(host.result_state.reference_evidence["relative_error"], 4.0e-11)
        self.assertTrue(any("does not match" in message for message in host.errors))

    def test_loaded_run_evidence_but_no_valid_run_resets_old_identity(self):
        host = _PanelHost(str(Path(self.temp.name) / "absent"))
        host.seed_drawn_result()
        host.result_state.run_report = {"mode": "solve"}
        host.result_state.reference_evidence = {"mode": "solve"}
        host.load_result_metadata()
        self.assertIsNone(host.result_state.run_dir)
        self.assertIsNone(host.result_state.run_report)
        self.assertIsNone(host.result_state.reference_evidence)

    def test_draw_preflight_failure_clears_the_old_drawing(self):
        host = _PanelHost(str(self.run_dir))
        host.seed_drawn_result()
        host.process = object()  # a background task is running
        host.draw_result_frame()
        self.assertEqual(host.result_layer.clear_calls, 1)
        self.assertIsNone(host.result_state.frame)
        self.assertIsNone(host.result_state.metadata)
        self.assertTrue(host.errors)

    def test_draw_without_metadata_clears_a_stale_visible_layer(self):
        host = _PanelHost(str(self.run_dir))
        host.result_state.reset()
        host.result_layer.doc = object()  # a stale layer with no metadata
        host.result_layer.clear_calls = 0
        host.draw_result_frame()
        self.assertEqual(host.result_layer.clear_calls, 1)
        self.assertIsNone(host.result_state.frame)
        self.assertTrue(host.errors)


# --------------------------------------------------------------------------- #
# four independent evidence levels
# --------------------------------------------------------------------------- #

class EvidenceLevelTests(unittest.TestCase):
    def test_static_validation_reflects_the_real_validate_call(self):
        self.assertEqual(gui.result_evidence_levels()["static"]["state"], "unknown")
        self.assertEqual(gui.result_evidence_levels(validate_result=True)["static"]["state"],
                         "pass")
        self.assertEqual(gui.result_evidence_levels(validate_result=False)["static"]["state"],
                         "fail")

    def test_initialize_is_read_from_this_runs_report(self):
        levels = gui.result_evidence_levels(run_report={"mode": "initialize"})
        self.assertEqual(levels["initialize"]["state"], "pass")
        self.assertEqual(levels["solve"]["state"], "unknown")

    def test_solve_needs_mode_solve_and_the_cli_ready_flag(self):
        report = {"mode": "solve"}
        self.assertEqual(gui.result_evidence_levels(run_report=report)["solve"]["state"],
                         "unknown")
        self.assertEqual(gui.result_evidence_levels(
            run_report=report, cli_case={"solved_ready": True})["solve"]["state"], "pass")
        self.assertEqual(gui.result_evidence_levels(
            run_report=report, cli_case={"solved_ready": False})["solve"]["state"], "fail")

    def test_a_solved_ready_result_never_promotes_the_reference_level(self):
        levels = gui.result_evidence_levels(run_report={"mode": "solve"},
                                            cli_case={"solved_ready": True})
        self.assertEqual(levels["solve"]["state"], "pass")
        self.assertEqual(levels["reference"]["state"], "unknown")

    def test_only_explicit_reference_evidence_passes_or_fails(self):
        verified = gui.result_evidence_levels(
            reference_evidence={"expected_abs_tip_displacement_mm": 0.02,
                                "relative_error": 4.0e-11, "mode": "solve"})
        self.assertEqual(verified["reference"]["state"], "pass")
        failed = gui.result_evidence_levels(
            reference_evidence={"expected_abs_tip_displacement_mm": 0.02,
                                "relative_error": 1.0e-2, "mode": "solve"})
        self.assertEqual(failed["reference"]["state"], "fail")
        silent = gui.result_evidence_levels(
            reference_evidence={"mode": "solve", "max_abs_displacement": 0.02})
        self.assertEqual(silent["reference"]["state"], "unknown")

    def test_evidence_text_lists_all_four_levels_and_denies_promotion(self):
        text = gui.format_result_evidence(gui.result_evidence_levels(
            run_report={"mode": "solve"}, cli_case={"solved_ready": True}))
        for label in ("static validation", "initialization", "solve",
                      "reference case verification"):
            self.assertIn(label, text)
        self.assertIn("drawn frame", text)
        self.assertIn("unknown", text)


# --------------------------------------------------------------------------- #
# separated force / equilibrium evidence
# --------------------------------------------------------------------------- #

class ForcesFormatTests(unittest.TestCase):
    def test_absent_forces_block_is_unknown_and_never_invented(self):
        text = gui.format_result_forces({"mode": "solve", "checks": {"area": "pass"}})
        self.assertIn("no forces block", text)
        self.assertIn("unknown", text)

    def test_real_force_fields_and_statuses_are_shown(self):
        text = gui.format_result_forces(forces_report())
        self.assertIn("strong supports: status=measured", text)
        self.assertIn("[-0.0034, 0, 0.001]", text)
        self.assertIn("applied loads: status=measured", text)
        self.assertIn("equilibrium: status=unverified", text)
        self.assertIn("signed complete=True", text)
        self.assertIn("convergence: verified=False", text)

    def test_penalty_raw_total_is_never_presented_as_a_reaction(self):
        text = gui.format_result_forces(forces_report(raw_penalty=[0.0, 0.0, -0.1068]))
        self.assertIn("raw_total=[0, 0, -0.1068]", text)
        self.assertIn("physical reaction=unavailable", text)
        self.assertIn("raw and unsigned", text)


# --------------------------------------------------------------------------- #
# lightweight widget structure (fake Qt; not a GUI acceptance)
# --------------------------------------------------------------------------- #

class _Proxy:
    """A callable/attribute proxy for widget members the builder only records."""

    def __init__(self, owner, name):
        self.owner = owner
        self.name = name

    def __call__(self, *args, **kwargs):
        self.owner.calls.append((self.name, args, kwargs))
        return self

    def __getattr__(self, name):
        return _Proxy(self.owner, f"{self.name}.{name}")


class _FakeWidget:
    """A recorder-only widget with the few return values the builder reads."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.calls = []
        self.items = []
        self._text = args[0] if args and isinstance(args[0], str) else ""
        self._index = 0
        self._value = 0.0
        self._checked = False
        self.editable = False
        self.readonly = False

    def __getattr__(self, name):
        return _Proxy(self, name)

    def addItem(self, text, data=None):
        self.items.append((text, data))
        return None

    def count(self):
        return len(self.items)

    def currentText(self):
        return self.items[self._index][0] if self._index < len(self.items) else self._text

    def setCurrentText(self, text):
        self._text = text

    def currentData(self):
        return self.items[self._index][1] if self._index < len(self.items) else None

    def setCurrentIndex(self, index):
        self._index = index

    def currentIndex(self):
        return self._index

    def findData(self, data):
        for index, (_, value) in enumerate(self.items):
            if value == data:
                return index
        return -1

    def setText(self, text):
        self._text = text

    def text(self):
        return self._text

    def setValue(self, value):
        self._value = value

    def value(self):
        return self._value

    def setEditable(self, flag):
        self.editable = flag

    def setReadOnly(self, flag):
        self.readonly = flag

    def setChecked(self, flag):
        self._checked = flag

    def isChecked(self):
        return self._checked


class _FakeLayout:
    def __init__(self, *args, **kwargs):
        self.widgets = []
        self.layouts = []

    def addWidget(self, *args, **kwargs):
        self.widgets.append(args[0] if args else None)

    def addLayout(self, *args, **kwargs):
        self.layouts.append(args[0] if args else None)

    def addStretch(self, *args, **kwargs):
        return None


class _FakeQt:
    QComboBox = _FakeWidget
    QDoubleSpinBox = _FakeWidget
    QSpinBox = _FakeWidget
    QLabel = _FakeWidget
    QPlainTextEdit = _FakeWidget
    QPushButton = _FakeWidget
    QHBoxLayout = _FakeLayout
    QVBoxLayout = _FakeLayout
    QWidget = _FakeWidget


class ResultSectionStructureTests(unittest.TestCase):
    def test_result_section_builds_the_required_controls(self):
        original = gui.W
        gui.W = _FakeQt
        try:
            host = _Host()
            gui.Panel._build_result_state(host)
            layout = _FakeLayout()
            gui.Panel.build_result_section(host, layout)
        finally:
            gui.W = original
        self.assertTrue(host.result_run_dir.editable)
        self.assertEqual([data for _, data in host.result_component.items],
                         list(gui.RESULT_COMPONENTS))
        for attribute in ("result_step", "result_scale", "result_load_button",
                          "result_draw_button", "result_clear_button", "result_status",
                          "result_metadata_view", "result_extrema_view",
                          "result_evidence_view", "result_forces_view"):
            self.assertTrue(hasattr(host, attribute), attribute)
        note = host.result_mapping_note.text()
        self.assertIn("face/UV unavailable", note)
        self.assertIn("NOT the trimmed integration domain", note)


if __name__ == "__main__":
    unittest.main()
