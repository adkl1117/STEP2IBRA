"""Native Qt dock inside FreeCAD. No web runtime or OCP DLL imports."""
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import math
import tempfile
import uuid
import os
import subprocess
import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui
try:
    from PySide import QtWidgets as W
except ImportError:
    W = QtGui
from .project import new_project, populate, read_json, write_json, validate, read_project, write_project, convert_project_units, unit_scale, SCHEMA, UNIT_SYSTEMS, load_kind_error, amplitude_errors
from .coupling_diagnostics import summarize as coupling_summary
from .groups import expand as expand_groups, resolve as resolve_group
from . import document as doc_owner
from . import preview as preview_module
from . import result_layer as result_layer_module
from . import run_dirs

BASE = Path(__file__).resolve().parents[1]
from .settings import load_settings, save_settings
_panel = None


def spin(value, lo=0, hi=100, decimals=None):
    widget = W.QSpinBox() if decimals is None else W.QDoubleSpinBox()
    widget.setRange(lo, hi)
    if decimals is not None:
        widget.setDecimals(decimals)
    widget.setValue(value)
    return widget


def button(text, callback):
    widget = W.QPushButton(text)
    widget.clicked.connect(callback)
    return widget


def element_selector():
    widget = W.QComboBox()
    widget.addItem("Kirchhoff–Love Shell", "Shell3pElement")
    widget.addItem("Membrane", "IgaMembraneElement")
    widget.setMinimumContentsLength(22)
    widget.setSizeAdjustPolicy(W.QComboBox.AdjustToMinimumContentsLengthWithIcon)
    return widget


def text_hint(text):
    label = W.QLabel(text)
    label.setWordWrap(True)
    label.setObjectName("hint")
    return label


# --------------------------------------------------------------------------- #
# P0-6 result display: backend bridge and panel state (FreeCAD/Qt-free core)
# --------------------------------------------------------------------------- #
# The only result source is the run's own native ``results.vtkhdf``. FreeCAD's bundled
# Python has no h5py, so the file is read by the existing backend Python through
# ``kratos_iga/results_cli.py``: one bounded JSON request on stdin, one bounded JSON
# object on stdout. No sampling file, custom VTK or sidecar record is written and a
# frame is never persisted to disk. Every helper below is FreeCAD-free and Qt-free so it
# can be unit-tested in the backend venv; the real drawing is done by
# :class:`kratos_iga.result_layer.ResultLayer`.
RESULT_ACTIONS = ("metadata", "frame")
RESULT_COMPONENTS = ("X", "Y", "Z", "magnitude")
RESULT_CLI_TIMEOUT_SECONDS = 30.0
# The bundled analytical cantilever example records this relative displacement error
# after a real solve; only an explicit record like it can raise the reference level.
REFERENCE_RELATIVE_ERROR = 1.0e-6


class ResultViewError(Exception):
    """The result section cannot show a trustworthy frame; nothing new is drawn."""


def build_result_request(action, run_dir, project, cad_mm, *, step=0, scale=1.0,
                         component="magnitude"):
    """The exact JSON request ``results_cli.py`` expects, per action.

    ``project`` and ``cad_mm`` are the current model and the current cache CAD in
    millimetres; the CLI performs the mm-to-export-unit conversion and the full content
    association in memory. Metadata carries no step/scale/component, so the request
    cannot pretend to describe a frame it did not ask for.
    """
    if action not in RESULT_ACTIONS:
        raise ResultViewError(f"action must be one of {RESULT_ACTIONS}, got {action!r}")
    if not isinstance(run_dir, str) or not run_dir.strip():
        raise ResultViewError("a non-empty run directory is required")
    if not isinstance(project, dict):
        raise ResultViewError("the current project is required to associate a result")
    if not isinstance(cad_mm, dict):
        raise ResultViewError("the current CAD (millimetres) is required to associate a result")
    request = {"action": action, "run_dir": str(run_dir),
               "current_project": project, "current_cad_mm": cad_mm}
    if action == "metadata":
        return request
    if component not in RESULT_COMPONENTS:
        raise ResultViewError(f"component must be one of {RESULT_COMPONENTS}, got {component!r}")
    if isinstance(step, bool) or not isinstance(step, int) or step < 0:
        raise ResultViewError(f"step must be a non-negative integer index, got {step!r}")
    try:
        scale = float(scale)
    except (TypeError, ValueError) as error:
        raise ResultViewError(f"scale must be a number, got {scale!r}") from error
    if not math.isfinite(scale) or scale < 0.0:
        raise ResultViewError(f"scale must be finite and >= 0, got {scale!r}")
    request.update({"step": int(step), "scale": scale, "component": component})
    return request


def parse_result_payload(text, expected_action):
    """Validate the one bounded JSON object ``results_cli.py`` wrote on stdout."""
    if not isinstance(text, str) or not text.strip():
        raise ResultViewError("the backend result CLI wrote nothing on stdout")
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise ResultViewError(
            f"the backend result CLI stdout is not one JSON object: {error}") from error
    if not isinstance(payload, dict):
        raise ResultViewError("the backend result CLI stdout is not a JSON object")
    action = payload.get("action")
    if action != expected_action:
        raise ResultViewError(
            f"the backend result CLI answered action {action!r}, expected {expected_action!r}")
    if payload.get("ok") is not True:
        error = payload.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        raise ResultViewError(
            message or f"the backend result CLI refused the {expected_action} request")
    return payload


def _cli_error_message(process):
    """The most readable bounded reason from a refused CLI process."""
    stdout = getattr(process, "stdout", "") or ""
    if stdout.strip():
        try:
            payload = json.loads(stdout)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
    stderr = (getattr(process, "stderr", "") or "").strip()
    if stderr:
        return stderr.splitlines()[-1]
    return f"exit code {getattr(process, 'returncode', 'unknown')}"


def run_result_cli(python, cli_path, request, *, timeout=RESULT_CLI_TIMEOUT_SECONDS,
                   runner=subprocess.run):
    """Run the accepted backend bridge with a bounded timeout and return its payload.

    The process is started with an explicit argument list (no shell), the request is
    handed over on stdin and the frame stays in the process streams. Any timeout, launch
    failure, non-zero exit code or ``ok`` other than ``true`` raises
    :class:`ResultViewError`; the caller must then remove any previously drawn result
    instead of keeping it as if it were the new frame.
    """
    try:
        body = json.dumps(request, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ResultViewError(f"the result request is not JSON-serialisable: {error}") from error
    try:
        process = runner([str(python), str(cli_path)], input=body, capture_output=True,
                         text=True, encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise ResultViewError(
            f"the backend result CLI did not answer within {timeout:g}s; nothing is "
            "displayed") from error
    except OSError as error:
        raise ResultViewError(f"the backend result CLI could not be started: {error}") from error
    if process.returncode != 0:
        raise ResultViewError(
            f"the backend result CLI refused (exit {process.returncode}): "
            f"{_cli_error_message(process)}")
    return parse_result_payload(process.stdout, request.get("action"))


def _format_value(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "unknown" if value is None else str(value)
    number = float(value)
    if not math.isfinite(number):
        return "non-finite"
    return f"{number:.6g}"


def _format_vector(value):
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return "unavailable"
    parts = []
    for component in value[:3]:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            return "unavailable"
        number = float(component)
        if not math.isfinite(number):
            return "unavailable"
        parts.append(f"{number:.6g}")
    return "[" + ", ".join(parts) + "]"


def _format_position(value):
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return "unavailable"
    return "(" + ", ".join(_format_value(component) for component in value[:3]) + ")"


def format_result_metadata(payload):
    """The bounded description of one loaded metadata payload."""
    if not isinstance(payload, dict):
        return "No result metadata is loaded."
    case = payload.get("case") if isinstance(payload.get("case"), dict) else {}
    vtkhdf = payload.get("vtkhdf") if isinstance(payload.get("vtkhdf"), dict) else {}
    lines = [
        f"run directory: {payload.get('run_dir', case.get('run_dir', 'unknown'))}",
        ("case: mode={} · solved_ready={} · move_mesh_flag={} · geometry units={} "
         "(1 unit = {} mm)").format(case.get("mode", "unknown"),
                                     case.get("solved_ready", "unknown"),
                                     case.get("move_mesh_flag", "unknown"),
                                     case.get("geometry_units", "unknown"),
                                     case.get("unit_scale_to_mm", "unknown")),
        ("native results.vtkhdf: points={} · cells={} · connectivity ids={} · steps={} · "
         "file bytes={}").format(vtkhdf.get("points", "unknown"),
                                  vtkhdf.get("cells", "unknown"),
                                  vtkhdf.get("connectivity_ids", "unknown"),
                                  vtkhdf.get("steps", "unknown"),
                                  vtkhdf.get("file_bytes", "unknown")),
        f"cell types: {vtkhdf.get('cell_types', 'unknown')}",
    ]
    times = vtkhdf.get("times")
    if isinstance(times, (list, tuple)) and times:
        lines.append("recorded step times: " + ", ".join(
            f"{index}: {_format_value(time)}" for index, time in enumerate(times)))
    lines.append("face/UV mapping: {} — {}".format(
        payload.get("mapping_state", "unavailable"),
        payload.get("mapping_note") or ("a plain VTKHDF carries no face/UV mapping; the "
                                        "display cells are the native sampling cells, not "
                                        "the trimmed integration domain")))
    for reason in case.get("reasons") or []:
        lines.append(f"association refusal: {reason}")
    for warning in case.get("warnings") or []:
        lines.append(f"association note: {warning}")
    return "\n".join(lines)


def format_result_extrema(frame, summary=None):
    """Extrema of the displayed component with their real positions."""
    if not isinstance(frame, dict):
        return "No result frame is drawn."
    step = frame.get("step") if isinstance(frame.get("step"), dict) else {}
    component = step.get("component", "magnitude")
    lines = ["step {index} · time={time} · scale={scale} · component={component}".format(
        index=step.get("index", "unknown"), time=_format_value(step.get("time")),
        scale=_format_value(step.get("scale")), component=component)]
    extrema = frame.get("extrema")
    entry = extrema.get(component) if isinstance(extrema, dict) else None
    if isinstance(entry, dict):
        unit = entry.get("unit", "")
        lines.append("min {} {} at {}".format(_format_value(entry.get("min")), unit,
                                              _format_position(entry.get("min_position"))))
        lines.append("max {} {} at {}".format(_format_value(entry.get("max")), unit,
                                              _format_position(entry.get("max_position"))))
    else:
        lines.append("extrema: unavailable for this component")
    if isinstance(summary, dict):
        lines.append("drawn: {} face(s) from {} native cell(s) · skipped={}".format(
            summary.get("faces_drawn", "unknown"), summary.get("cells", "unknown"),
            summary.get("faces_skipped", "unknown")))
        lines.append("display mapping: {} — {}".format(
            summary.get("mapping_state", "unavailable"), summary.get("mapping_note", "")))
        for note in summary.get("notes") or []:
            lines.append(f"note: {note}")
    return "\n".join(lines)


def _explicit_state(value):
    """Map an explicit verification value to pass/fail, or ``None`` when it is silent."""
    if value is True:
        return "pass"
    if value is False:
        return "fail"
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("pass", "passed", "verified", "ok", "true"):
            return "pass"
        if text in ("fail", "failed", "false"):
            return "fail"
    return None


def reference_verification_state(evidence):
    """Only an explicit reference verification record can raise this level.

    Nothing here is inferred from a zero exit code, from a solved phase or from a drawn
    frame. A record that carries no explicit comparison stays ``unknown``.
    """
    if not isinstance(evidence, dict):
        return {"state": "unknown",
                "source": "no explicit reference verification record for this run directory"}
    nested = evidence.get("reference_verification")
    if isinstance(nested, dict):
        raw = nested.get("status", nested.get("result", nested.get("passed")))
        state = _explicit_state(raw)
        if state:
            return {"state": state,
                    "source": f"explicit reference_verification record ({raw!r})"}
    checks = evidence.get("checks")
    if isinstance(checks, dict) and "reference" in checks:
        raw = checks.get("reference")
        state = _explicit_state(raw)
        if state:
            return {"state": state, "source": f"explicit checks.reference={raw!r}"}
    expected = evidence.get("expected_abs_tip_displacement_mm")
    error = evidence.get("relative_error")
    finite_expected = (isinstance(expected, (int, float)) and not isinstance(expected, bool)
                       and math.isfinite(float(expected)))
    finite_error = (isinstance(error, (int, float)) and not isinstance(error, bool)
                    and math.isfinite(float(error)))
    if finite_expected and finite_error and evidence.get("mode") == "solve":
        state = "pass" if float(error) <= REFERENCE_RELATIVE_ERROR else "fail"
        return {"state": state,
                "source": ("explicit example verification: analytic displacement "
                           f"{float(expected):g} mm vs the solved result, relative error "
                           f"{float(error):.3g}")}
    return {"state": "unknown",
            "source": "the record carries no explicit reference comparison"}


def result_evidence_levels(*, validate_result=None, run_report=None, cli_case=None,
                           reference_evidence=None):
    """Four independent evidence levels, each raised only by its own real evidence."""
    if validate_result is True:
        static = {"state": "pass",
                  "source": "validate() reported no errors for the current model"}
    elif validate_result is False:
        static = {"state": "fail",
                  "source": "validate() reported errors for the current model"}
    else:
        static = {"state": "unknown",
                  "source": "validate() has not been run for the current model"}

    mode = run_report.get("mode") if isinstance(run_report, dict) else None
    if mode == "initialize":
        initialize = {"state": "pass",
                      "source": "this run's run_report.json mode=initialize"}
    elif mode == "solve":
        initialize = {"state": "pass",
                      "source": ("this run's run_report.json mode=solve retains the pre-solve "
                                 "initialization block")}
    elif isinstance(run_report, dict):
        initialize = {"state": "unknown",
                      "source": f"this run's run_report.json mode={mode!r} is not a known phase"}
    else:
        initialize = {"state": "unknown",
                      "source": "no run_report.json exists for this run directory"}

    solved = cli_case.get("solved_ready") if isinstance(cli_case, dict) else None
    if mode != "solve":
        solve = {"state": "unknown",
                 "source": "this run's run_report.json does not report mode=solve"}
    elif solved is True:
        solve = {"state": "pass",
                 "source": ("mode=solve and the backend results CLI reports solved_ready with "
                            "the full project/CAD association")}
    elif solved is False:
        solve = {"state": "fail",
                 "source": ("mode=solve, but the backend results CLI refuses the result for the "
                            "open model")}
    else:
        solve = {"state": "unknown",
                 "source": ("mode=solve, but the backend results CLI has not validated this "
                            "result for the open model yet")}

    return {"static": static, "initialize": initialize, "solve": solve,
            "reference": reference_verification_state(reference_evidence)}


def format_result_evidence(levels):
    """The four independent levels as panel text; a frame never promotes a level."""
    lines = ["Independent evidence levels (each needs its own real evidence):"]
    for key, label in (("static", "static validation"), ("initialize", "initialization"),
                       ("solve", "solve"), ("reference", "reference case verification")):
        entry = levels.get(key) if isinstance(levels, dict) else None
        entry = entry if isinstance(entry, dict) else {}
        lines.append(f"{label}: {entry.get('state', 'unknown')} · "
                     f"{entry.get('source', 'no evidence')}")
    lines.append("Neither a zero exit code nor a drawn frame promotes a level; a level is "
                 "only shown when its own evidence exists. 'unknown' is not a pass.")
    return "\n".join(lines)


def _force_condition_line(entry, value_key="total"):
    if not isinstance(entry, dict):
        return "unavailable (field absent)"
    return (f"status={entry.get('status', 'unknown')} · "
            f"{value_key}={_format_vector(entry.get(value_key))} (project force unit)")


def _force_penalty_line(entry):
    if not isinstance(entry, dict):
        return "unavailable (field absent)"
    reaction = entry.get("reaction_status")
    if not reaction:
        reaction = "available" if entry.get("physical_reaction") is not None else "unavailable"
    line = (f"status={entry.get('status', 'unknown')} · "
            f"raw_total={_format_vector(entry.get('raw_total'))} (raw and unsigned, project "
            f"force unit) · physical reaction={reaction}")
    if entry.get("reason"):
        line += f" · reason={entry['reason']}"
    return line


def _force_equilibrium_line(entry):
    if not isinstance(entry, dict):
        return "unavailable (field absent)"
    coverage = entry.get("coverage") if isinstance(entry.get("coverage"), dict) else {}
    lines = ["status={} · signed residual relative={} · signed complete={} · penalty present={}"
             .format(entry.get("status", "unknown"),
                     _format_value(entry.get("signed_residual_relative")),
                     coverage.get("signed_complete", "unknown"),
                     coverage.get("penalty_present", "unknown"))]
    if entry.get("reason"):
        lines.append(f"  reason: {entry['reason']}")
    convergence = entry.get("convergence")
    if isinstance(convergence, dict):
        lines.append("  convergence: verified={} · solver={} · analysis={} · solve reported "
                     "converged={}".format(convergence.get("verified", "unknown"),
                                           convergence.get("solver_type", "unknown"),
                                           convergence.get("analysis_type", "unknown"),
                                           convergence.get("solve_reported_converged", "unknown")))
        if convergence.get("reason"):
            lines.append(f"  convergence reason: {convergence['reason']}")
    return "\n".join(lines)


def format_result_forces(run_report):
    """Strong/weak force and equilibrium evidence, field by field, never invented."""
    if not isinstance(run_report, dict):
        return ("Force / balance evidence: no run_report.json for this run directory; strong "
                "support reactions, applied loads and equilibrium stay unknown.")
    forces = run_report.get("forces")
    if not isinstance(forces, dict):
        return ("Force / balance evidence: this run_report.json carries no forces block; strong "
                "support reactions, applied loads and equilibrium stay unknown (they are never "
                "inferred from the displayed displacement).")
    lines = ["forces: status={} · schema={} · steps={}".format(
        forces.get("status", "unknown"), forces.get("schema", "unknown"),
        forces.get("step_count", "unknown"))]
    if forces.get("measured_at"):
        lines.append(f"measured at: {forces['measured_at']}")
    if forces.get("reason"):
        lines.append(f"forces reason: {forces['reason']}")
    last = forces.get("last")
    if not isinstance(last, dict):
        lines.append("last measured step: unavailable; no step has been finalized in this report")
        return "\n".join(lines)
    lines.append("last step: {} · time={} · mode={} · status={}".format(
        _format_value(last.get("step")), _format_value(last.get("time")),
        last.get("mode", "unknown"), last.get("status", "unknown")))
    lines.append("strong supports: " + _force_condition_line(last.get("strong_supports")))
    lines.append("applied loads: " + _force_condition_line(last.get("applied_loads")))
    lines.append("penalty supports: " + _force_penalty_line(last.get("penalty_supports")))
    lines.append("penalty couplings: " + _force_penalty_line(last.get("penalty_couplings")))
    lines.append("equilibrium: " + _force_equilibrium_line(last.get("equilibrium")))
    lines.append("The penalty right-hand sides are RAW and unsigned; they are never presented as "
                 "a physical reaction, and 'unverified' means no equilibrium pass is claimed.")
    return "\n".join(lines)


class ResultState:
    """Every piece of result-section state: no Qt, no FreeCAD, no file access."""

    def __init__(self):
        self.run_dir = None
        self.metadata = None
        self.frame = None
        self.summary = None
        self.run_report = None
        self.reference_evidence = None
        self.error = None
        self.status_text = None
        self.validate_result = None

    def reset(self):
        self.__init__()

    def clear_display(self, *, message=None, drop_metadata=True):
        """Drop the drawn frame; keep the run identity so its evidence still shows."""
        self.frame = None
        self.summary = None
        self.error = message
        self.status_text = None
        if drop_metadata:
            self.metadata = None

    def load_metadata(self, run_dir, payload):
        self.run_dir = str(run_dir)
        self.metadata = payload
        self.frame = None
        self.summary = None
        self.error = None
        self.status_text = None

    def load_frame(self, payload, summary):
        self.frame = payload
        self.summary = summary
        self.error = None

    def cli_case(self):
        metadata = self.metadata if isinstance(self.metadata, dict) else {}
        case = metadata.get("case")
        return case if isinstance(case, dict) else None

    def evidence(self):
        return result_evidence_levels(validate_result=self.validate_result,
                                      run_report=self.run_report,
                                      cli_case=self.cli_case(),
                                      reference_evidence=self.reference_evidence)


def _error_text(error):
    return str(error) or type(error).__name__


class ResultController:
    """Load one run's metadata and frame through injected, bounded operations.

    ``fetch(request)`` runs the backend CLI, ``render(frame, component, cad_objects)``
    hands a validated frame to the FreeCAD result layer, and ``unrender()`` removes the
    previously drawn result. On any failure the previous drawing is removed before the
    error propagates, so a stale picture can never look like the new result.

    The panel and this controller must share **one** :class:`ResultState`: the panel
    reads the state the controller writes. Pass the panel's state in via ``state``; a
    controller created without one owns a private state (used only by isolated tests).
    """

    def __init__(self, fetch, render, unrender, state=None):
        self._fetch = fetch
        self._render = render
        self._unrender = unrender
        self.state = state if state is not None else ResultState()

    def clear(self, *, message=None, drop_metadata=True):
        try:
            self._unrender()
        except Exception:  # noqa: BLE001 - cleanup must never hide the real error
            pass
        self.state.clear_display(message=message, drop_metadata=drop_metadata)

    def load_metadata(self, run_dir, project, cad_mm):
        request = build_result_request("metadata", run_dir, project, cad_mm)
        try:
            payload = self._fetch(request)
        except Exception as error:
            self.clear(message=_error_text(error), drop_metadata=True)
            raise
        self.state.load_metadata(run_dir, payload)
        return payload

    def draw_frame(self, project, cad_mm, *, step, scale, component, cad_objects):
        if not self.state.run_dir or self.state.metadata is None:
            raise ResultViewError("load the result metadata for a run directory first")
        request = build_result_request("frame", self.state.run_dir, project, cad_mm,
                                       step=step, scale=scale, component=component)
        try:
            payload = self._fetch(request)
        except Exception as error:
            self.clear(message=_error_text(error), drop_metadata=True)
            raise
        try:
            summary = self._render(payload, component, cad_objects)
        except Exception as error:
            self.clear(message=_error_text(error), drop_metadata=True)
            raise
        self.state.load_frame(payload, summary)
        return payload, summary


class VectorEditor(W.QWidget):
    def __init__(self, optional=False):
        super().__init__()
        layout = W.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.checks, self.values = [], []
        for axis in "XYZ":
            box = W.QWidget()
            column = W.QVBoxLayout(box)
            column.setContentsMargins(0, 0, 0, 0)
            check = W.QCheckBox("Displacement "+axis if optional else axis)
            check.setChecked(True)
            check.setEnabled(optional)
            value = W.QLineEdit("0.0")
            value.setMinimumWidth(60)
            check.toggled.connect(value.setEnabled)
            column.addWidget(check)
            column.addWidget(value)
            layout.addWidget(box)
            self.checks.append(check)
            self.values.append(value)

    def get(self):
        return [float(w.text()) if c.isChecked() else None for c, w in zip(self.checks, self.values)]

    def set(self, values):
        for value, check, edit in zip(values, self.checks, self.values):
            check.setChecked(value is not None)
            edit.setText(str(value if value is not None else 0.0))


class Panel(W.QDockWidget):
    def __init__(self, parent=None):
        super().__init__("IBRA Preprocessor for Kratos", parent)
        self.setObjectName("KratosIBRAPreprocessor")
        self.setMinimumWidth(690)
        self.user_data = Path(App.getUserAppDataDir())
        self.settings = load_settings(self.user_data, BASE)
        self.work_dir = Path(self.settings['work_dir'])
        self.project = new_project()
        self.index = {"faces": [], "edges": [], "source_sha256": ""}
        self.cache = None
        self.doc = None
        self.process = None
        self.last_export = None
        # One isolated run directory per Initialize/Run. ``last_export`` keeps pointing
        # at the read-only export; ``last_run`` is the only place a report or result is
        # ever read from, and it belongs to exactly one launch.
        self.last_run = None
        self.entities = {}
        self.display_fingerprints = {}
        self.pending_project = None
        self.reuse_target_document = None
        self.snapshot = None  # controlled-snapshot state; many entry points test it
        self.editing = {}
        self.cad = None
        self._cad_cache_signature = None
        # Display-only preview layer: its own registry, names, group and view styling.
        # It is never part of self.entities / self.display_fingerprints and never gets
        # IbraBrepId, so it cannot become an analysis target or a snapshot entry.
        self.preview_layer = preview_module.PreviewLayer(tagger=self.tag_preview)
        self.preview_objects = self.preview_layer.objects
        self.preview_report = None
        # Display-only result layer for one already-validated native results.vtkhdf frame.
        # It owns its own marked objects and restores the CAD visibility it changed; it is
        # never part of self.entities / self.display_fingerprints and never gets IbraBrepId.
        self.result_layer = result_layer_module.ResultLayer(None)
        self._build_result_state()
        self.initialized_report = None
        self.form = W.QWidget()
        self.form.setObjectName("igaPanel")
        self.setWidget(self.form)
        layout = W.QVBoxLayout(self.form)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(10)
        brand = W.QWidget()
        brand.setObjectName("brand")
        brand_layout = W.QHBoxLayout(brand)
        brand_layout.setContentsMargins(18, 13, 18, 13)
        brand_text = W.QVBoxLayout()
        brand_text.setSpacing(2)
        header = W.QLabel("IBRA Preprocessor")
        header.setObjectName("title")
        brand_text.addWidget(header)
        caption = W.QLabel("B-rep-based structural analysis for Kratos")
        caption.setObjectName("brandCaption")
        brand_text.addWidget(caption)
        brand_layout.addLayout(brand_text)
        brand_layout.addStretch()
        badge = W.QLabel("KRATOS Multiphysics")
        badge.setObjectName("brandTag")
        brand_layout.addWidget(badge)
        layout.addWidget(brand)
        top = W.QHBoxLayout()
        import_button = button("+ Import STEP", self.import_step)
        import_button.setObjectName("primary")
        top.addWidget(import_button)
        top.addWidget(button("Open Project", self.open_project))
        top.addWidget(button("Save Project", self.save_project))
        layout.addLayout(top)
        self.summary = text_hint("Import STEP geometry to define shell or membrane models on trimmed surfaces.")
        self.summary.setObjectName("projectSummary")
        layout.addWidget(self.summary)
        body = W.QHBoxLayout()
        body.setSpacing(10)
        self.navigation = W.QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setFixedWidth(132)
        self.navigation.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.navigation.setFocusPolicy(QtCore.Qt.NoFocus)
        body.addWidget(self.navigation)
        self.tabs = W.QTabWidget()
        self.tabs.tabBar().hide()
        body.addWidget(self.tabs, 1)
        layout.addLayout(body, 1)
        self.navigation.currentRowChanged.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self.navigation.setCurrentRow)
        self.build_geometry()
        self.build_materials()
        self.build_groups()
        self.build_boundary()
        self.build_initial()
        self.build_couplings()
        self.build_preview()
        self.build_output()
        self.navigation.setCurrentRow(0)
        self.status = W.QLabel("Ready | Import geometry to define the analysis model.")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = W.QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.log = W.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        self.log.setPlaceholderText("Geometry conversion, model validation and solver log")
        self.log.setMaximumBlockCount(1500)
        self.log.hide()
        self.log_toggle = button("Show Log", self.toggle_log)
        self.log_toggle.setObjectName("quiet")
        layout.addWidget(self.log_toggle, 0, QtCore.Qt.AlignRight)
        layout.addWidget(self.log)
        actions = W.QHBoxLayout()
        actions.addWidget(button("Validate Model", self.check_project))
        export_button = button("Export Analysis Model  →", self.export_case)
        export_button.setObjectName("export")
        actions.addWidget(export_button, 1)
        self.stop_button = button("Stop", self.cancel)
        self.stop_button.setObjectName("quiet")
        self.stop_button.setEnabled(False)
        actions.addWidget(self.stop_button)
        layout.addLayout(actions)
        from .theme import stylesheet
        self.form.setStyleSheet(stylesheet())

    def toggle_log(self):
        self.log.setVisible(not self.log.isVisible())
        self.log_toggle.setText("Hide Log" if self.log.isVisible() else "Show Log")

    def page(self, title):
        page = W.QWidget()
        page.setObjectName("contentPage")
        layout = W.QVBoxLayout(page)
        layout.setContentsMargins(16, 15, 16, 15)
        layout.setSpacing(10)
        titles = {"Geometry": ("GEOMETRY", "Geometry and Topology", "Geometry"),
                  "Materials": ("MATERIALS", "Constitutive Properties", "Materials"),
                  "Groups": ("GROUPS", "Named Groups and Rebinding", "Groups"),
                  "Boundary": ("BOUNDARY", "Boundary Conditions", "Boundary"),
                  "Initial": ("INITIAL STATE", "Initial Conditions", "Initial"),
                  "Interfaces": ("INTERFACES", "Patch Interfaces", "Interfaces"),
                  "Preview": ("PREVIEW", "Inspectable Preprocessing", "Preview"),
                  "Analysis": ("ANALYSIS", "Analysis and Output", "Output")}
        number = self.tabs.count()
        eyebrow = W.QLabel(titles[title][0])
        eyebrow.setObjectName("eyebrow")
        heading = W.QLabel(titles[title][1])
        heading.setObjectName("sectionTitle")
        layout.addWidget(eyebrow)
        layout.addWidget(heading)
        scroll = W.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, title)
        self.navigation.addItem(f"{number+1:02}  {titles[title][2]}")
        return layout

    def table(self, headers):
        table = W.QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(W.QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(36)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def build_geometry(self):
        layout = self.page("Geometry")
        layout.setAlignment(QtCore.Qt.AlignTop)
        self.geometry_table = self.table(["Type", "BRep ID", "Patch IDs / Degrees"])
        self.geometry_table.setMaximumHeight(240)
        self.geometry_table.setEditTriggers(W.QAbstractItemView.NoEditTriggers)
        self.geometry_table.setSelectionMode(W.QAbstractItemView.ExtendedSelection)
        self.geometry_table.itemSelectionChanged.connect(self.select_table_geometry)
        layout.addWidget(self.geometry_table)
        layout.addWidget(text_hint("Select patches or edges in the table or viewport; hold Ctrl for multiple entities. Orange curves indicate candidate interfaces. Display polylines do not replace the NURBS analysis geometry."))
        layout.addWidget(button("Open Cantilever Example", lambda: self.guard(lambda: self.load_project(BASE/"examples/cantilever/cantilever.ibra-project.json"))))
        self.selection_text = text_hint("No target entity selected")
        layout.addWidget(self.selection_text)
        self.profile = W.QLineEdit()
        self.profile.setPlaceholderText("Optional model-specific conversion profile (JSON)")
        layout.addWidget(self.profile)
        layout.addWidget(button("Select Conversion Profile", self.choose_profile))
        layout.addWidget(text_hint("Reimport after changing the profile. Face exclusion and duplicate-face handling follow the configured conversion pipeline."))
        refinement_header = W.QHBoxLayout()
        refinement_header.addWidget(W.QLabel("Refinement"))
        self.refinement_toggle = W.QCheckBox()
        self.refinement_toggle.setAccessibleName("Enable Refinement")
        self.refinement_toggle.setToolTip("Enable degree elevation and knot insertion. Unchecked uses the original geometry without refinement.")
        self.refinement_toggle.toggled.connect(self.toggle_refinement)
        refinement_header.addWidget(self.refinement_toggle)
        refinement_header.addStretch()
        layout.addLayout(refinement_header)
        self.refinement_content = W.QWidget()
        refinement_layout = W.QVBoxLayout(self.refinement_content)
        refinement_layout.setContentsMargins(0, 8, 0, 0)
        self.build_patches(refinement_layout)
        layout.addWidget(self.refinement_content)
        self.refinement_content.hide()
        layout.addStretch(1)

    def toggle_refinement(self, expanded):
        self.refinement_content.setVisible(expanded)
        self.project["refinement_enabled"] = expanded

    def build_materials(self):
        layout = self.page("Materials")
        layout.addWidget(text_hint("Constitutive law: isotropic linear elasticity under plane stress. Materials hold E, ν and ρ. Sections hold thickness and may be shared by several patches."))
        self.material_table = self.table(["ID", "Name", "E", "ν", "ρ"])
        layout.addWidget(self.material_table)
        row = W.QHBoxLayout()
        row.addWidget(button("Add Material", self.add_material))
        row.addWidget(button("Remove Selected Rows", lambda: self.delete_row(self.material_table)))
        layout.addLayout(row)
        layout.addWidget(W.QLabel("Sections"))
        self.section_table = self.table(["ID", "Name", "Thickness"])
        layout.addWidget(self.section_table)
        row = W.QHBoxLayout()
        row.addWidget(button("Add Section", self.add_section))
        row.addWidget(button("Remove Selected Rows", lambda: self.delete_row(self.section_table)))
        layout.addLayout(row)
        self.current_units = W.QLabel()
        self.current_units.setObjectName("hint")
        layout.addWidget(self.current_units)
        self.units = W.QComboBox()
        self.units.addItems(list(UNIT_SYSTEMS))
        self.units.currentIndexChanged.connect(lambda _index: self._update_unit_status())
        layout.addWidget(self.units)
        layout.addWidget(button("Convert Values to Selected Unit System", lambda: self.guard(self.convert_units)))
        layout.addWidget(text_hint("OCCT imports STEP coordinates in mm. The m-N-s system scales exported geometry by 0.001. Density in mm-N-s is expressed in tonne/mm³. Changing the unit system converts every physical value; it never rescales silently. Validate, save and export require the displayed target to be applied first."))
        layout.addWidget(W.QLabel("Patch Assignments"))
        self.assignment_table = self.table(["Face", "Material ID", "Section ID", "Element", "GP", "Active"])
        layout.addWidget(self.assignment_table)
        layout.addWidget(text_hint("These assignments remain active when Refinement is disabled. GP = 0 uses default quadrature."))

    def build_patches(self, layout):
        layout.addWidget(text_hint("Degrees p/q are target polynomial degrees. h-u/h-v specify knot insertions per existing span. GP = 0 retains the default quadrature rule."))
        self.patch_table = self.table(["Face", "Material ID", "Element", "p", "q", "h-u", "h-v", "GP"])
        for column in (1, 2, 7):
            self.patch_table.setColumnHidden(column, True)
        layout.addWidget(self.patch_table)
        self.bulk_material = spin(1,1,9999)
        self.bulk_element = element_selector()
        self.bulk_degree = spin(2,1,8)
        self.bulk_h = spin(1,0,100)
        batch = W.QFormLayout()
        batch.addRow("Material ID",self.bulk_material)
        batch.addRow("Element Formulation",self.bulk_element)
        dimensions = W.QWidget()
        row = W.QHBoxLayout(dimensions)
        row.setContentsMargins(0,0,0,0)
        row.addWidget(W.QLabel("Minimum Degree"))
        row.addWidget(self.bulk_degree)
        row.addWidget(W.QLabel("Knots per Span"))
        row.addWidget(self.bulk_h)
        batch.addRow(dimensions)
        layout.addLayout(batch)
        row = W.QHBoxLayout()
        row.addWidget(button("Select All",self.patch_table.selectAll))
        row.addWidget(button("Apply to Selected Patches",self.bulk_patch_settings),1)
        layout.addLayout(row)
        layout.addWidget(text_hint("The shell formulation requires degree ≥ 2 and internal C¹ continuity. Degree elevation preserves existing C⁰ continuity. Membranes require degree ≥ 1. Beam and volumetric formulations are outside the current scope."))

    def condition_common(self, category, title, layout=None):
        if layout is None:
            layout = self.page(title)
        target = W.QLineEdit()
        target.setReadOnly(True)
        target.setPlaceholderText("Select geometric entities, then assign the selection.")
        layout.addWidget(target)
        layout.addWidget(button("Assign Selected Entities", lambda: self.capture(category)))
        name = W.QLineEdit(title+"_1")
        form = W.QFormLayout()
        form.addRow("Name", name)
        group = W.QComboBox()
        group.addItem("(direct selection)", None)
        form.addRow("Named Group", group)
        layout.addLayout(form)
        setattr(self, category+"_target", target)
        setattr(self, category+"_name", name)
        setattr(self, category+"_targets", [])
        setattr(self, category+"_group", group)
        return layout, form

    def build_groups(self):
        layout = self.page("Groups")
        layout.addWidget(text_hint("Named groups hold explicit face or edge B-rep IDs. A condition either points at one group or at direct targets, never both. Missing members are shown as missing and block export - they are never silently removed."))
        self.group_table = self.table(["ID", "Name", "Kind", "Members", "Status"])
        layout.addWidget(self.group_table)
        row = W.QHBoxLayout()
        row.addWidget(button("Create Group from Selection", self.create_group_from_selection))
        row.addWidget(button("Rename Group", self.rename_group))
        row.addWidget(button("Remove Group", self.remove_group))
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(text_hint("Groups accept one-sided faces or boundary edges only. Interfaces, preview polylines and mixed selections are rejected explicitly."))
        layout.addWidget(W.QLabel("Pending rebind"))
        self.rebind_table = self.table(["Entry", "Kind", "Old target", "Reason", "Action"])
        layout.addWidget(self.rebind_table)
        rebind_row = W.QHBoxLayout()
        rebind_row.addWidget(button("Rebind to Current Selection", self.rebind_selected))
        rebind_row.addWidget(button("Drop Binding", self.drop_rebind_selected))
        rebind_row.addStretch(1)
        layout.addLayout(rebind_row)
        layout.addWidget(text_hint("After a geometry change every relevant binding is marked pending. Nothing is inherited from a coincidentally equal ID: rebind explicitly or drop it. Export stays blocked while this list is not empty."))

        layout.addWidget(W.QLabel("Controlled snapshot and explicit remap"))
        self.snapshot_table = self.table(["Old target", "Kind", "Referenced by", "New target", "Status"])
        layout.addWidget(self.snapshot_table)
        snapshot_row = W.QHBoxLayout()
        snapshot_row.addWidget(button("Capture Controlled Snapshot", self.capture_controlled_snapshot))
        snapshot_row.addWidget(button("Convert Snapshot (preview)", self.run_snapshot_conversion))
        snapshot_row.addWidget(button("Apply Explicit Mapping", self.apply_snapshot_mapping))
        snapshot_row.addWidget(button("Cancel Snapshot", self.cancel_snapshot))
        snapshot_row.addWidget(button("Recover for Snapshot", self.recover_document_for_snapshot))
        snapshot_row.addStretch(1)
        layout.addLayout(snapshot_row)
        self.snapshot_status = W.QLabel("No snapshot in progress.")
        self.snapshot_status.setWordWrap(True)
        layout.addWidget(self.snapshot_status)
        layout.addWidget(text_hint("The snapshot writes a new STEP under the work area and converts it with the existing worker. Every required old face/edge stays UNRESOLVED - even at an equal ID - until you choose a new same-kind target or explicitly drop it. Nothing is created, bound or overwritten until a complete mapping is applied; cancelling leaves the original document, source, cache and choices unchanged."))

    # ------------------------------------------------------------------ controlled snapshot
    def _snapshot_step_path(self):
        folder = self.work_dir / "snapshots"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"ibra-snapshot-{uuid.uuid4().hex[:8]}.step"

    def _snapshot_entries(self):
        association = self._entity_association()
        if association:
            return association
        return [{"kind": kind, "brep_id": ident, "object": name}
                for (kind, ident), name in self.entities.items()]

    def capture_controlled_snapshot(self):
        def operation():
            from .snapshots import capture_face_snapshot
            if self.process:
                raise ValueError("A background task is already running.")
            if self._pending_condition_edit():
                raise ValueError("Commit or cancel the open condition edit before snapshotting.")
            if self.snapshot:
                raise ValueError("A controlled snapshot is already pending; apply or cancel it before "
                                 "capturing another one.")
            if not self.doc or self.doc.Name not in App.listDocuments():
                raise ValueError("No FreeCAD document is associated with this panel.")
            # Commit the widget state, but do NOT require the old geometry fingerprint:
            # a changed shape is the intended input of this controlled path.
            self.sync(skip_geometry=True)
            destination = self._snapshot_step_path()
            meta = capture_face_snapshot(self.doc, self._snapshot_entries(), destination,
                                         source_paths=[self.project.get("source_step", "")])
            self.snapshot = {"step": str(destination), "meta": meta, "old_doc": self.doc.Name,
                             "old_project": deepcopy(self.project), "old_index": deepcopy(self.index),
                             "old_cache": str(self.cache) if self.cache else "",
                             "new_index": None, "new_cache": None, "plan": None}
            self.snapshot_status.setText(
                f"Snapshot captured: {meta['face_total']} face(s) written to {destination.name}. "
                "Convert it, then resolve every mapping below.")
            self.refresh_snapshot_table()
        self.guard(operation)

    def run_snapshot_conversion(self):
        def operation():
            if not self.snapshot:
                raise ValueError("Capture a controlled snapshot first.")
            if self.doc is None or self.doc.Name != self.snapshot.get("old_doc"):
                raise ValueError("The active document changed since this snapshot was captured; cancel it and "
                                 "capture again so the mapping cannot cross documents.")
            if self.index != self.snapshot["old_index"] or str(self.cache) != self.snapshot.get("old_cache"):
                raise ValueError("The geometry cache changed since this snapshot was captured; cancel it and "
                                 "capture again.")
            if self.process:
                raise ValueError("A background task is already running.")
            cache = self.work_dir / "cache" / ("snapshot-" + uuid.uuid4().hex[:8])
            cache.mkdir(parents=True, exist_ok=True)
            # Re-running the conversion replaces the previous preview instead of
            # leaking another one.
            if self.snapshot.get("preview_doc"):
                self._close_snapshot_preview()
                self.snapshot["new_index"] = None
                self.snapshot["plan"] = None
                self.refresh_snapshot_table()
            self.snapshot["new_cache"] = str(cache)
            self.snapshot_status.setText("Converting the snapshot with the existing worker...")
            self.start_worker({"action": "convert", "source_step": self.snapshot["step"],
                               "profile": self.profile.text().strip(), "cache_dir": str(cache)},
                              self.snapshot_converted)
        self.guard(operation)

    def snapshot_converted(self):
        def operation():
            from .bindings import stage_rebind
            if not self.snapshot:
                return  # cancelled while the worker was still running
            cache = Path(self.snapshot["new_cache"])
            new_index = read_json(cache / "index.json")
            self.snapshot["new_index"] = new_index
            self.snapshot["plan"] = stage_rebind(self.snapshot["old_project"], self.snapshot["old_index"])
            preview = self._build_snapshot_preview(cache, new_index)
            self.refresh_snapshot_table()
            self.snapshot_status.setText(
                f"Converted: {len(new_index['faces'])} new face(s). Preview document '{preview}' shows every "
                f"new face/edge with its kind and ID; {len(self.snapshot['plan']['required_keys'])} old "
                "target(s) must be mapped or dropped. Nothing has been changed yet.")
        self.guard(operation)

    def _build_snapshot_preview(self, cache, new_index):
        """Dedicated temporary preview document: converted faces + boundary polylines.

        It has no analysis owner and never receives an entity map, so it cannot
        pollute the project; it is closed on cancel or after a successful apply. No
        shape in the original document is moved or modified.
        """
        name = f"IBRA_Snapshot_Preview_{uuid.uuid4().hex[:6]}"
        doc = App.newDocument(name)
        root = doc.addObject("App::DocumentObjectGroup", "IBRA_Preview_Geometry")
        preview_objects = {}
        for face in new_index.get("faces", []):
            obj = doc.addObject("Part::Feature", f"IBRA_Face_{face['id']}")
            shape = Part.Shape()
            shape.read(str(cache / face["brep_file"]))
            obj.Shape = shape
            obj.Label = f"NEW face {face['id']}"
            obj.ViewObject.ShapeColor = (0.55, 0.85, 0.55)
            root.addObject(obj)
            preview_objects[("face", face["id"])] = obj.Name
        for edge in new_index.get("edges", []):
            obj = doc.addObject("Part::Feature",
                                f"IBRA_{'Coupling' if edge['kind'] == 'coupling' else 'Edge'}_{edge['id']}")
            obj.Shape = Part.makePolygon([App.Vector(*point) for point in edge["points"]])
            obj.Label = f"NEW {edge['kind']} {edge['id']}"
            obj.ViewObject.LineColor = (0.9, 0.4, 0.9) if edge["kind"] == "coupling" else (0.9, 0.6, 0.1)
            root.addObject(obj)
            preview_objects[(edge["kind"], edge["id"])] = obj.Name
        doc.recompute()
        self.snapshot["preview_doc"] = doc.Name
        self.snapshot["preview_objects"] = preview_objects
        return doc.Name

    def _close_snapshot_preview(self):
        if not self.snapshot:
            return
        name = self.snapshot.get("preview_doc")
        if name and name in App.listDocuments():
            App.closeDocument(name)
        self.snapshot["preview_doc"] = None

    def _highlight_snapshot_target(self, combo):
        if not self.snapshot:
            return
        choice = combo.currentData()
        Gui.Selection.clearSelection()
        preview = self.snapshot.get("preview_doc")
        if choice in (None, "__drop__") or not preview or preview not in App.listDocuments():
            return
        kind, ident = choice.split(":")
        obj_name = (self.snapshot.get("preview_objects") or {}).get((kind, int(ident)))
        if obj_name:
            Gui.Selection.addSelection(preview, obj_name, "")

    def refresh_snapshot_table(self):
        if not hasattr(self, "snapshot_table"):
            return
        self.snapshot_table.setRowCount(0)
        if not self.snapshot or not self.snapshot.get("plan"):
            return
        plan = self.snapshot["plan"]
        new_index = self.snapshot.get("new_index") or {}
        for target in plan["targets"]:
            combo = W.QComboBox()
            combo.addItem("(unresolved)", None)
            if target["kind"] == "face":
                for face in new_index.get("faces", []):
                    combo.addItem(f"face {face['id']}", f"face:{face['id']}")
            else:
                for edge in new_index.get("edges", []):
                    if edge.get("kind") == "edge":
                        combo.addItem(f"edge {edge['id']}", f"edge:{edge['id']}")
            combo.addItem("(drop)", "__drop__")
            combo.currentIndexChanged.connect(
                lambda _index, widget=combo: self._highlight_snapshot_target(widget))
            self.table_row(self.snapshot_table,
                           [target["key"], target["kind"],
                            ", ".join(target.get("referenced_by", [])) or "-", combo, "unresolved"],
                           (0, 1, 2, 4))
        self.snapshot_table.resizeColumnsToContents()

    def _snapshot_mapping(self):
        mapping, drop = {}, []
        for row in range(self.snapshot_table.rowCount()):
            key = self.snapshot_table.item(row, 0).text()
            choice = self.snapshot_table.cellWidget(row, 3).currentData()
            if choice == "__drop__":
                drop.append(key)
            elif choice:
                mapping[key] = choice
        return mapping, drop

    def apply_snapshot_mapping(self):
        def operation():
            from .bindings import apply_rebind
            if not self.snapshot or not self.snapshot.get("plan"):
                raise ValueError("Convert the snapshot before applying a mapping.")
            if self.doc is None or self.doc.Name != self.snapshot.get("old_doc"):
                raise ValueError("The active document changed since this snapshot was captured; the mapping is "
                                 "refused because a same-looking project file is not the same document.")
            if self.index != self.snapshot["old_index"] or str(self.cache) != self.snapshot.get("old_cache"):
                raise ValueError("The geometry cache changed since this snapshot was captured; the mapping is refused.")
            if self._pending_condition_edit():
                raise ValueError("Commit or cancel the open condition edit before applying a mapping.")
            # Commit the displayed widget edits first, WITHOUT the geometry guard, then
            # compare: a post-capture edit must refuse rather than be silently replaced
            # by the state captured earlier.
            self.sync(skip_geometry=True)
            if self.project != self.snapshot["old_project"]:
                raise ValueError("The project changed after this snapshot was captured; capture it again so no "
                                 "edit is silently overwritten by the captured state.")
            mapping, drop = self._snapshot_mapping()
            plan = self.snapshot["plan"]
            unresolved = [key for key in plan["required_keys"] if key not in mapping and key not in drop]
            if unresolved:
                raise ValueError("Every old target must be mapped or explicitly dropped; unresolved: "
                                 + ", ".join(sorted(unresolved)))
            # Pure: compute the real result first and review its named consequences.
            result = apply_rebind(self.snapshot["old_project"], self.snapshot["old_index"],
                                  self.snapshot["new_index"], mapping,
                                  drop_targets=drop or None, source_step=self.snapshot["step"])
            if not self._confirm_snapshot_summary(result["summary"]):
                self.snapshot_status.setText("Mapping cancelled; nothing was changed.")
                return
            self._bind_snapshot(result["project"], result["summary"])
            summary = result["summary"]
            self.snapshot_status.setText(
                f"Mapping applied. {len(summary.get('unmapped_new_faces', []))} new face(s) received default "
                f"patch settings; {len(summary.get('regenerated_couplings', []))} interface(s) regenerated as "
                "pending; the previous document remains open and unchanged.")
        self.guard(operation)

    def _confirm_snapshot_summary(self, summary):
        lines = []
        for item in summary.get("dropped_conditions", []):
            lines.append(f"condition {item.get('category')}/{item.get('name')}: {item.get('reason')}")
        for item in summary.get("dropped_groups", []):
            lines.append(f"group {item.get('group_id')} {item.get('name')}: {item.get('reason')}")
        for item in summary.get("dropped_group_members", []):
            lines.append(f"group {item.get('group_id')} member {item.get('kind')}:{item.get('member')} dropped")
        for item in summary.get("dropped_patches", []):
            lines.append(f"patch face {item.get('face_id')} settings dropped "
                         f"(material {item.get('material_id')}, section {item.get('section_id')})")
        for item in summary.get("new_patches", []):
            lines.append(f"new patch face {item.get('face_id')} from {item.get('source')}")
        for face_id in summary.get("unmapped_new_faces", []):
            lines.append(f"new face {face_id} gets default patch settings")
        for item in summary.get("regenerated_couplings", []):
            lines.append(f"interface {item.get('edge_id')} regenerated as pending")
        if not lines:
            return True
        box = W.QMessageBox(self)
        box.setWindowTitle("Confirm snapshot remap")
        box.setText("Review the named consequences of this mapping before committing.")
        box.setDetailedText("\n".join(lines))
        box.setStandardButtons(W.QMessageBox.Ok | W.QMessageBox.Cancel)
        execute = getattr(box, "exec", None) or getattr(box, "exec_")
        return execute() == W.QMessageBox.Ok

    def _bind_snapshot(self, new_project, summary):
        cache = Path(self.snapshot["new_cache"])
        index = self.snapshot["new_index"]
        old_doc = self.doc
        prior_hook = doc_owner.installed_hook(old_doc) if old_doc is not None else None
        prior_provider = prior_hook.provider if prior_hook is not None else None
        previous = {key: getattr(self, key) for key in
                    ("project", "cache", "import_cache", "index", "entities", "display_fingerprints")}
        existing_docs = set(App.listDocuments())
        self.detach_from_document()  # release the old document's save hook before switching
        self.project = new_project
        self.cache = cache
        self.import_cache = cache
        self.index = index
        self.entities = {}
        self.display_fingerprints = {}
        try:
            self.show_geometry()  # new snapshot document; the old one is untouched
            self.refresh()
            self.attach_to_document(strict=True)  # a silent failure is not a success here
        except Exception:
            created = set(App.listDocuments()) - existing_docs
            for key, value in previous.items():
                setattr(self, key, value)
            self.doc = old_doc
            for name in created:
                try:
                    App.closeDocument(name)
                except Exception:  # noqa: BLE001 - rollback must not mask the failure
                    pass
            if old_doc is not None and prior_provider is not None:
                doc_owner.install_save_observer(old_doc, prior_provider, on_error="flag")
            self.refresh()
            raise
        self.last_export = None
        self.last_run = None
        self.rebind_table.setRowCount(0)
        self._close_snapshot_preview()
        self.snapshot = None
        self.refresh_snapshot_table()
        self.status.setText("Controlled snapshot applied; the previous document remains open and unchanged.")

    def cancel_snapshot(self):
        if self.snapshot and self.process:
            raise ValueError("The snapshot conversion is still running; wait for it to finish before cancelling.")
        old_doc = (self.snapshot or {}).get("old_doc")
        self._close_snapshot_preview()
        self.snapshot = None
        if old_doc and old_doc in App.listDocuments():
            App.setActiveDocument(old_doc)
        if hasattr(self, "snapshot_status"):
            self.snapshot_status.setText(
                "Snapshot cancelled; the original document, source, cache and every choice are unchanged.")
        self.refresh_snapshot_table()

    def recover_document_for_snapshot(self):
        """Explicit recovery route for a changed FCStd whose restore is blocked.

        Binds the stored project, cache and live entity names in a pending state so a
        controlled snapshot can proceed. It never clears ``SyncError``, never touches
        a ``Shape``, and still refuses a corrupt stored project outright.
        """
        def operation():
            if self.process:
                raise ValueError("A background task is already running; wait for it before recovering.")
            if self.snapshot:
                raise ValueError("A controlled snapshot is pending; apply or cancel it before recovering.")
            if self._pending_condition_edit():
                raise ValueError("Commit or cancel the open condition edit before recovering.")
            doc = self._project_document(active_first=True)
            state = doc_owner.read(doc)
            if state.project is None or any("corrupt" in d for d in state.diagnostics):
                raise ValueError("The stored project is corrupt or missing: " + "; ".join(state.diagnostics))
            cache = Path(state.cache_dir) if state.cache_dir else None
            index = None
            if cache is not None and (cache / "index.json").is_file():
                try:
                    candidate = read_json(cache / "index.json")
                except Exception:  # noqa: BLE001
                    candidate = None
                if (isinstance(candidate, dict)
                        and candidate.get("source_sha256") == state.project.get("source_sha256")
                        and candidate.get("geometry_sha256") == state.project.get("geometry_sha256")):
                    index = candidate
            if index is None:
                raise ValueError("The document's conversion cache is missing or stale; a controlled snapshot "
                                 "needs the stored cache to preview the new geometry.")
            self._mark_all_pending(state.project, "recovered from a changed document; rebind explicitly")
            self._reset_panel_context(doc, cache, index, state.project, state.entity_map)
            self.refresh()
            self.attach_to_document()
            self.status.setText("Recovered the stored project in a pending state (SyncError kept). "
                                "Capture a controlled snapshot to remap explicitly.")
        self.guard(operation)

    def refresh_groups(self):
        self.group_table.setRowCount(0)
        if not hasattr(self, "group_table"):
            return
        groups = {group.get("id"): group for group in self.project.get("groups", [])}
        for group in self.project.get("groups", []):
            expanded = expand_groups([group], self.index).get(group.get("id"), {})
            missing = expanded.get("missing") or []
            status = f"missing {missing}" if missing else "ok"
            self.table_row(self.group_table, [group.get("id"), group.get("name"), group.get("kind"),
                                              str(group.get("members", [])), status], (0, 1, 2, 3, 4))
        self.group_rows = list(groups)

    def create_group_from_selection(self):
        def operation():
            targets = self.selections()
            if not targets:
                raise ValueError("Select analysis faces or one-sided boundaries in the document first.")
            kinds = {t["target_kind"] for t in targets}
            if kinds - {"face", "edge"}:
                raise ValueError("Groups accept faces or boundary edges only; interfaces are not group members.")
            if len(kinds) != 1:
                raise ValueError("Select either faces or boundary edges, not a mixture.")
            kind = kinds.pop()
            members = sorted({int(t["target_id"]) for t in targets})
            ids = [group.get("id", 0) for group in self.project.get("groups", [])]
            self.project.setdefault("groups", []).append(
                {"id": max(ids, default=0) + 1, "name": f"Group {max(ids, default=0) + 1}",
                 "kind": kind, "members": members, "origin": "user"})
            self.refresh_group_views()
            self.status.setText(f"Group created from selection: {kind} {members}")
        self.guard(operation)

    def _selected_group(self):
        row = self.group_table.currentRow() if hasattr(self, "group_table") else -1
        if row < 0:
            return None
        ident = int(self.group_table.item(row, 0).text())
        return next((group for group in self.project.get("groups", []) if group.get("id") == ident), None)

    def rename_group(self):
        def operation():
            group = self._selected_group()
            if group is None:
                raise ValueError("Select a group row first.")
            text, ok = W.QInputDialog.getText(self, "Rename Group", "Name", text=str(group.get("name", "")))
            if not ok or not text.strip():
                return
            group["name"] = text.strip()
            self.refresh_group_views()
        self.guard(operation)

    def remove_group(self):
        def operation():
            group = self._selected_group()
            if group is None:
                raise ValueError("Select a group row first.")
            self.project["groups"] = [g for g in self.project.get("groups", []) if g is not group]
            self.refresh_group_views()
        self.guard(operation)

    def rebind_entries(self):
        entries = []
        for category in ("supports", "loads", "initial_conditions"):
            for record in self.project[category]:
                if record.get("needs_rebind"):
                    entries.append((category, record))
        for group in self.project.get("groups", []):
            if isinstance(group, dict) and group.get("needs_rebind"):
                entries.append(("groups", group))
        return entries

    def refresh_rebind(self):
        if not hasattr(self, "rebind_table"):
            return
        self.rebind_table.setRowCount(0)
        for category, record in self.rebind_entries():
            label = record.get("name") or f"group {record.get('id')}"
            self.table_row(self.rebind_table, [category, record.get("target_kind", "group"),
                                               record.get("target_id", record.get("members")),
                                               record.get("needs_rebind"), "rebind or drop"], (0, 1, 2, 3, 4))

    def rebind_selected(self):
        def operation():
            row = self.rebind_table.currentRow()
            entries = self.rebind_entries()
            if row < 0 or row >= len(entries):
                raise ValueError("Select a pending entry first.")
            targets = self.selections()
            if not targets:
                raise ValueError("Select the replacement entity in the document first.")
            category, record = entries[row]
            if category == "groups":
                kinds = {t["target_kind"] for t in targets}
                if kinds - {"face", "edge"} or len(kinds) != 1:
                    raise ValueError("A group rebinds to faces or boundary edges of one kind.")
                record["kind"] = kinds.pop()
                record["members"] = sorted({int(t["target_id"]) for t in targets})
            else:
                if len(targets) != 1:
                    raise ValueError("Rebind a condition to exactly one target entity.")
                record.pop("group_id", None)
                record.update(targets[0])
            record.pop("needs_rebind", None)
            self.refresh_rebind()
            self.refresh_conditions(category) if category != "groups" else self.refresh_group_views()
        self.guard(operation)

    def drop_rebind_selected(self):
        def operation():
            row = self.rebind_table.currentRow()
            entries = self.rebind_entries()
            if row < 0 or row >= len(entries):
                raise ValueError("Select a pending entry first.")
            category, record = entries[row]
            if category == "groups":
                self.project["groups"] = [g for g in self.project.get("groups", []) if g is not record]
            else:
                self.project[category] = [r for r in self.project[category] if r is not record]
            self.refresh_rebind()
        self.guard(operation)

    def interval_editor(self, form, category):
        box = W.QWidget()
        row = W.QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        a, b = W.QLineEdit("0.0"), W.QLineEdit("End")
        row.addWidget(a)
        row.addWidget(W.QLabel("→"))
        row.addWidget(b)
        form.addRow("Active Time Interval", box)
        setattr(self, category+"_interval", (a, b))

    def condition_list(self, layout, category):
        row = W.QHBoxLayout()
        row.addWidget(button("Add / Update", lambda: self.add_condition(category)))
        row.addWidget(button("Remove", lambda: self.delete_condition(category)))
        row.addWidget(button("New", lambda: self.new_condition(category)))
        layout.addLayout(row)
        items = W.QListWidget()
        items.itemDoubleClicked.connect(lambda item: self.edit_condition(category, items.row(item)))
        layout.addWidget(items)
        layout.addWidget(text_hint("Double-click a condition to edit it. New exits the current edit mode."))
        setattr(self, category+"_list", items)

    def build_boundary(self):
        layout = self.page("Boundary")
        self.boundary_tabs = W.QTabWidget()
        self.boundary_tabs.setObjectName("boundaryTabs")
        for title, builder in [("Support", self.build_supports), ("Loads", self.build_loads)]:
            section = W.QWidget()
            section_layout = W.QVBoxLayout(section)
            section_layout.setContentsMargins(0, 12, 0, 0)
            builder(section_layout)
            self.boundary_tabs.addTab(section, title)
        layout.addWidget(self.boundary_tabs)

    def build_supports(self, layout):
        layout, form = self.condition_common("supports", "Support", layout)
        self.support_method = W.QComboBox()
        self.support_method.addItems(["Strong Enforcement | Control Points", "Penalty Enforcement | Boundary XYZ"])
        form.addRow("Enforcement", self.support_method)
        self.support_side = W.QComboBox()
        for label, value in [("U-min Boundary", "u0"), ("U-max Boundary", "u1"), ("V-min Boundary", "v0"), ("V-max Boundary", "v1"),
            ("Corner U-min / V-min", "00"), ("Corner U-max / V-min", "10"), ("Corner U-min / V-max", "01"), ("Corner U-max / V-max", "11"), ("All Control Points", "all")]:
            self.support_side.addItem(label, value)
        form.addRow("Parametric Location", self.support_side)
        self.interval_editor(form, "supports")
        self.support_vector = VectorEditor(True)
        layout.addWidget(self.support_vector)
        self.clamp = W.QCheckBox("Clamp Adjacent Control-Point Row")
        self.clamp.setToolTip("Applies to a complete natural parametric boundary. The adjacent control-point row receives the same prescribed displacement components and values.")
        layout.addWidget(self.clamp)
        self.penalty = W.QLineEdit("1e7")
        form.addRow("Penalty Factor", self.penalty)
        layout.addWidget(text_hint("Strong enforcement supports individual displacement components on natural boundaries and corners. General trimming curves require penalty enforcement, currently restricted to XYZ throughout the analysis interval."))
        self.condition_list(layout, "supports")
        self.support_method.currentIndexChanged.connect(self.update_support_method)
        self.update_support_method()

    def update_support_method(self):
        penalty = self.support_method.currentIndex() == 1
        self.penalty.setEnabled(penalty)
        self.support_side.setEnabled(not penalty)
        self.clamp.setEnabled(not penalty)
        if penalty:
            self.clamp.setChecked(False)
            self.supports_interval[0].setText(self.start_time.text() if hasattr(self,"start_time") else "0.0")
            self.supports_interval[1].setText("End")
        for w in self.supports_interval:
            w.setEnabled(not penalty)
        for check in self.support_vector.checks:
            check.setEnabled(not penalty)
            if penalty:
                check.setChecked(True)

    def bulk_patch_settings(self):
        rows = sorted({item.row() for item in self.patch_table.selectedItems()})
        if not rows:
            self.error("Select patch rows or use Select All before applying batch properties.")
            return
        faces = {f["id"]:f for f in self.index["faces"]}
        for row in rows:
            face = faces[int(self.patch_table.item(row,0).text())]
            self.patch_table.item(row,1).setText(str(self.bulk_material.value()))
            self.patch_table.cellWidget(row,2).setCurrentText(self.bulk_element.currentText())
            self.assignment_table.item(row,1).setText(str(self.bulk_material.value()))
            self.assignment_table.cellWidget(row,3).setCurrentText(self.bulk_element.currentText())
            minimum = max(self.bulk_degree.value(), 2 if self.bulk_element.currentData()=="Shell3pElement" else 1)
            for col, degree in zip((3,4),face["degrees"]):
                self.patch_table.item(row,col).setText(str(max(minimum,degree)))
            for col in (5,6):
                self.patch_table.item(row,col).setText(str(self.bulk_h.value()))
        self.status.setText(f"Material, formulation and refinement updated for {len(rows)} patches.")

    def build_loads(self, layout):
        layout, form = self.condition_common("loads", "Loads", layout)
        self.load_kind = W.QComboBox()
        self.load_kind.addItem("Global vector (LINE_LOAD / SURFACE_LOAD / DEAD_LOAD)", "vector")
        self.load_kind.addItem("Reference normal pressure (dead pressure)", "reference_pressure")
        self.load_kind.currentIndexChanged.connect(self.update_load_kind)
        form.addRow("Load Kind", self.load_kind)
        self.load_variable = W.QComboBox()
        self.load_variable.addItems(["LINE_LOAD", "SURFACE_LOAD", "DEAD_LOAD"])
        form.addRow("Variable", self.load_variable)
        self.interval_editor(form, "loads")
        self.load_vector = VectorEditor()
        layout.addWidget(self.load_vector)
        self.pressure_label = W.QLabel("Pressure (signed scalar, stress units)")
        self.load_pressure = W.QLineEdit("0.0")
        pressure_row = W.QHBoxLayout()
        pressure_row.addWidget(self.pressure_label)
        pressure_row.addWidget(self.load_pressure)
        layout.addLayout(pressure_row)
        self.load_normal_flip = W.QCheckBox("Flip reference normal (normal_flip: −n̂_ref)")
        layout.addWidget(self.load_normal_flip)
        self.load_kind_note = text_hint(
            "Positive pressure follows the X0 reference normal unit(du×dv) — the Kratos "
            "parameterization, not the CAD OCCT face orientation. This is a dead/reference "
            "pressure only; follower/current-normal pressure is NOT supported. The face "
            "midpoint sample may lie in a trimming hole.")
        layout.addWidget(self.load_kind_note)
        amplitude_header = W.QHBoxLayout()
        self.load_amplitude_enabled = W.QCheckBox("Piecewise-linear amplitude table (factor, dimensionless)")
        self.load_amplitude_enabled.toggled.connect(self._update_amplitude_editor)
        amplitude_header.addWidget(self.load_amplitude_enabled)
        layout.addLayout(amplitude_header)
        self.load_amplitude_table = self.table(["Time (s)", "Factor"])
        self.load_amplitude_table.setMaximumHeight(130)
        layout.addWidget(self.load_amplitude_table)
        amplitude_buttons = W.QHBoxLayout()
        amplitude_buttons.addWidget(button("Add Row", self.add_amplitude_row))
        amplitude_buttons.addWidget(button("Remove Row", lambda: self.delete_row(self.load_amplitude_table)))
        amplitude_buttons.addStretch(1)
        layout.addLayout(amplitude_buttons)
        layout.addWidget(text_hint(
            "Strictly increasing finite times, at least two points; linear interpolation; zero outside "
            "the table. Times are seconds and the factor is dimensionless — neither is unit-converted. "
            "The preview draws the base pressure (factor 1) and never implies a current time."))
        layout.addWidget(text_hint("LINE_LOAD: force per unit length. SURFACE_LOAD: force per unit area. DEAD_LOAD is integrated over the selected geometric measure. Loads are zero outside the active interval."))
        self.condition_list(layout, "loads")
        self.update_load_kind()

    def update_load_kind(self, *_args):
        pressure = self.load_kind.currentData() == "reference_pressure"
        self.load_variable.setEnabled(not pressure)
        self.load_vector.setVisible(not pressure)
        self.pressure_label.setVisible(pressure)
        self.load_pressure.setVisible(pressure)
        self.load_normal_flip.setVisible(pressure)
        self.load_kind_note.setVisible(pressure)
        self._update_amplitude_editor()

    def _update_amplitude_editor(self, *_args):
        # Available for BOTH vector and pressure loads (backend maps either).
        self.load_amplitude_table.setVisible(self.load_amplitude_enabled.isChecked())

    def add_amplitude_row(self):
        self.table_row(self.load_amplitude_table, ["0.0", "1.0"])

    def _pressure_value(self):
        try:
            value = float(self.load_pressure.text())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Pressure must be a finite signed scalar ({exc}).")
        if not math.isfinite(value):
            raise ValueError("Pressure must be finite.")
        return value

    def _amplitude_records(self):
        if not self.load_amplitude_enabled.isChecked():
            return None
        points = []
        for row in range(self.load_amplitude_table.rowCount()):
            try:
                time = float(self.load_amplitude_table.item(row, 0).text())
                factor = float(self.load_amplitude_table.item(row, 1).text())
            except (AttributeError, TypeError, ValueError) as exc:
                raise ValueError(f"Amplitude row {row + 1}: time and factor must be finite numbers ({exc}).")
            points.append([time, factor])
        if len(points) < 2:
            raise ValueError("An enabled amplitude table needs at least two time/factor points.")
        for previous, current in zip(points, points[1:]):
            if not (current[0] > previous[0]):
                raise ValueError("Amplitude times must be strictly increasing; duplicate or out-of-order "
                                 "times are rejected.")
        return {"points": points}

    def _set_amplitude_table(self, amplitude):
        self.load_amplitude_table.setRowCount(0)
        points = (amplitude or {}).get("points") if isinstance(amplitude, dict) else None
        self.load_amplitude_enabled.setChecked(bool(points))
        for pair in points or []:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                self.table_row(self.load_amplitude_table, [str(pair[0]), str(pair[1])])
        self._update_amplitude_editor()

    def build_initial(self):
        layout, form = self.condition_common("initial_conditions", "Initial")
        self.initial_variable = W.QComboBox()
        self.initial_variable.addItems(["DISPLACEMENT", "VELOCITY", "ACCELERATION"])
        form.addRow("Initial Field", self.initial_variable)
        self.initial_vector = VectorEditor(True)
        layout.addWidget(self.initial_vector)
        layout.addWidget(text_hint("Initial fields are assigned once to all control points of a patch without constraining degrees of freedom. Velocity and acceleration require dynamic analysis; nonzero initial displacement requires nonlinear analysis."))
        self.condition_list(layout, "initial_conditions")

    def build_couplings(self):
        layout = self.page("Interfaces")
        self.coupling_table = self.table(["Edge", "Adjacent Patches", "State", "Source", "Type",
                                          "Penalty Factor", "Sampled Pair Diagnostic"])
        layout.addWidget(self.coupling_table)
        actions = W.QHBoxLayout()
        actions.addWidget(button("Confirm Selected Interface", lambda: self.coupling_decision("confirmed", "user")))
        actions.addWidget(button("Exclude Selected Interface", lambda: self.coupling_decision("excluded", "user")))
        actions.addWidget(button("Reset to Candidate", lambda: self.coupling_decision("pending", "geometry")))
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addWidget(text_hint("Detected interfaces start as candidates and are never exported. Confirm the ones whose connection model you intend; only confirmed interfaces become Kratos conditions. 'Source' records whether the decision came from you or from an unreviewed legacy default. The pair diagnostic samples both trimming curves on their own source surfaces: it is a sampled gap and direction, not proof of full-edge coincidence, and it is not a weld or hinge model."))

    def coupling_decision(self, state, origin):
        row = self.coupling_table.currentRow()
        if row < 0:
            self.status.setText("Select an interface row first.")
            return
        edge_id = int(self.coupling_table.item(row, 0).text())
        record = next((c for c in self.project["couplings"] if c["edge_id"] == edge_id), None)
        if record is None:
            return
        try:
            record["penalty"] = float(self.coupling_table.item(row, 5).text())
        except (TypeError, ValueError):
            pass
        record["rotation"] = bool(self.coupling_table.cellWidget(row, 4).currentData())
        record["state"], record["origin"] = state, origin
        self.refresh()
        self.status.setText(f"Interface {edge_id}: {state} (source: {origin}).")

    def build_preview(self):
        layout = self.page("Preview")
        layout.addWidget(text_hint(
            "Display-only preprocessing inspection. Every glyph is generated from the current "
            "project assignment and the cached CAD surface; it never writes geometry.cad.json, "
            "never changes Shape or Placement, and never enters the analysis entity map or a "
            "controlled snapshot. Material colouring uses ViewObject only and is restored when "
            "the layer is switched off."))
        self.preview_supports = W.QCheckBox("Supports (strong / penalty, components, clamp)")
        self.preview_loads = W.QCheckBox("Loads (XYZ arrows, family scales/units, zero marker)")
        self.preview_frame = W.QCheckBox("Background parameter frame (u / v / normal)")
        self.preview_materials = W.QCheckBox("Material / Section legend (ViewObject colour)")
        for box in (self.preview_supports, self.preview_loads, self.preview_frame,
                    self.preview_materials):
            box.toggled.connect(self._preview_toggle_changed)
            layout.addWidget(box)
        row = W.QHBoxLayout()
        row.addWidget(button("Rebuild Preview", lambda: self.guard(self.refresh_preview)))
        row.addWidget(button("Clear Preview", lambda: self.guard(self.clear_preview)))
        row.addStretch(1)
        layout.addLayout(row)
        self.preview_status = text_hint("Preview is idle. Enable a layer to inspect the assignment.")
        layout.addWidget(self.preview_status)
        layout.addWidget(W.QLabel("Analysis refinement: original → target (estimate; verify after Initialize)"))
        self.preview_refinement_table = self.table(
            ["Face", "Active", "Orig p (u,v)", "Target p (u,v)", "Spans u/v", "Orig CP u×v",
             "Est. CP u×v", "Insert u/v", "min C"])
        self.preview_refinement_table.setMaximumHeight(190)
        layout.addWidget(self.preview_refinement_table)
        layout.addWidget(W.QLabel("Quadrature request (separate from refinement and display)"))
        self.preview_quadrature = text_hint("")
        layout.addWidget(self.preview_quadrature)
        layout.addWidget(W.QLabel("Display sampling (display only; not the integration domain)"))
        self.preview_display = text_hint("")
        layout.addWidget(self.preview_display)
        layout.addWidget(W.QLabel("Material / Section legend: exact current assignments"))
        self.preview_legend = text_hint("")
        layout.addWidget(self.preview_legend)
        layout.addWidget(W.QLabel("Geometry diagnostics (reported, never repaired)"))
        self.preview_diagnostics = text_hint("")
        layout.addWidget(self.preview_diagnostics)
        layout.addWidget(W.QLabel("Inactive (outside the analysis domain): no physics glyph drawn"))
        self.preview_inactive = text_hint("")
        layout.addWidget(self.preview_inactive)
        layout.addWidget(W.QLabel("Background-frame legend (independent axes)"))
        self.preview_frame_legend = text_hint("")
        layout.addWidget(self.preview_frame_legend)
        layout.addWidget(W.QLabel("Glyph notes (components, clamp, samples, scales)"))
        self.preview_notes = W.QPlainTextEdit()
        self.preview_notes.setReadOnly(True)
        self.preview_notes.setMaximumHeight(150)
        self.preview_notes.setPlaceholderText("Per-glyph notes appear here.")
        layout.addWidget(self.preview_notes)
        layout.addStretch(1)

    def tag_preview(self, obj, kind, key):
        """Register one display-only preview object.

        This is deliberately separate from :meth:`tag`: it adds no ``IbraBrepId`` / no
        ``IbraKind``, never writes ``self.entities`` or ``self.display_fingerprints`` and
        marks the object unselectable so it cannot be captured as a condition target.
        """
        self.preview_objects[(kind, key)] = obj.Name
        view = getattr(obj, "ViewObject", None)
        if view is not None:
            try:
                view.Selectable = False
            except Exception:  # noqa: BLE001 - a view without the property
                pass
        return obj.Name

    def _preview_toggles(self):
        return {"supports": self.preview_supports.isChecked(),
                "loads": self.preview_loads.isChecked(),
                "frame": self.preview_frame.isChecked(),
                "materials": self.preview_materials.isChecked()}

    def set_preview_toggles(self, supports=None, loads=None, frame=None, materials=None):
        """Programmatic toggle entry point (used by the isolated GUI probe)."""
        for box, value in ((self.preview_supports, supports), (self.preview_loads, loads),
                           (self.preview_frame, frame), (self.preview_materials, materials)):
            if value is None:
                continue
            box.blockSignals(True)
            box.setChecked(bool(value))
            box.blockSignals(False)
        self.refresh_preview()

    def _preview_toggle_changed(self, *_args):
        self.guard(self.refresh_preview)

    def _cad_geometry(self):
        if not self.cache:
            return None
        path = self.cache / "geometry.cad.json"
        if not path.is_file():
            return None
        signature = (str(path), path.stat().st_mtime_ns)
        if self._cad_cache_signature != signature:
            self.cad = read_json(path)
            self._cad_cache_signature = signature
        return self.cad

    def clear_preview(self, doc=None):
        """Remove every preview object this panel owns and restore all view colours.

        ``doc`` lets a document restoration clean stale preview objects that were
        loaded from a saved file before the layer is pointed at the new document.
        """
        self.preview_layer.clear(doc)
        self.preview_report = None
        if hasattr(self, "preview_status"):
            self._render_preview_report(None, "Preview cleared. No display object remains.")

    def _preview_geometry_blocker(self):
        """Read-only geometry/source diagnostic; never mutates the project or document."""
        reason = preview_module.geometry_blocker(self.doc, self.display_fingerprints, self.entities)
        if reason:
            return reason
        try:
            diagnostics = doc_owner.check_entities(self.doc)
        except Exception:  # noqa: BLE001 - a missing owner/cache is not a geometry claim
            diagnostics = []
        blocking = [text for text in diagnostics
                    if any(word in text for word in ("changed", "missing", "moved", "rebind",
                                                     "Rebind", "no shape", "reference"))]
        return "; ".join(blocking[:2])

    def _widget_assignment_drift(self):
        """Read-only widget vs committed-project comparison; never mutates state.

        The preview must never silently commit table edits. This builds the widget
        assignment without assigning it, returns a note when it differs, and leaves the
        committed project exactly as it was so the preview keeps showing the last
        explicit assignment.
        """
        if not self.project or not self.cache:
            return None
        def cell(table, row, col):
            return table.item(row, col).text().strip()
        try:
            materials = [{"id": int(cell(self.material_table, r, 0)), "name": cell(self.material_table, r, 1),
                **{key: float(cell(self.material_table, r, c)) for c, key in enumerate(("young", "poisson", "density"), 2)}} for r in range(self.material_table.rowCount())]
            sections = [{"id": int(cell(self.section_table, r, 0)), "name": cell(self.section_table, r, 1),
                "thickness": float(cell(self.section_table, r, 2))} for r in range(self.section_table.rowCount())]
            patches = [{"face_id": int(cell(self.patch_table, r, 0)), "material_id": int(cell(self.assignment_table, r, 1)),
                "section_id": int(cell(self.assignment_table, r, 2)),
                "element": self.assignment_table.cellWidget(r, 3).currentData(),
                "quadrature": int(cell(self.assignment_table, r, 4)),
                "active": bool(self.assignment_table.cellWidget(r, 5).isChecked()),
                **{key: int(cell(self.patch_table, r, c)) for c, key in enumerate(("degree_u", "degree_v", "insert_u", "insert_v"), 3)}} for r in range(self.patch_table.rowCount())]
            couplings = [{"edge_id": int(cell(self.coupling_table, r, 0)),
                "state": cell(self.coupling_table, r, 2), "origin": cell(self.coupling_table, r, 3),
                "penalty": float(cell(self.coupling_table, r, 5)),
                "rotation": bool(self.coupling_table.cellWidget(r, 4).currentData())}
                for r in range(self.coupling_table.rowCount())]
            solver = {"type": self.solver_type.currentData(), "analysis_type": self.analysis_type.currentData(),
                **{k: float(getattr(self, k).text()) for k in ("start_time", "end_time", "time_step")}}
            output = {"vtk": self.vtk.isChecked(), "refinement": self.output_refinement.value()}
        except Exception as exc:  # noqa: BLE001 - an invalid widget is reported, not applied
            return (f"unapplied table edit ({type(exc).__name__}: {exc}); the preview shows the last "
                    "committed assignment")
        candidate = dict(self.project)
        candidate.update({"materials": materials, "sections": sections,
                          "refinement_enabled": self.refinement_toggle.isChecked(),
                          "patches": patches, "couplings": couplings, "solver": solver,
                          "output": output})
        if candidate != self.project:
            return ("unapplied table edits; the preview shows the last committed assignment. "
                    "Press Validate Model or Export to apply them.")
        return None

    def refresh_preview(self):
        """Idempotently rebuild the enabled preview layers for the current document."""
        if not self.doc or self.doc.Name not in App.listDocuments():
            self.preview_layer.clear()
            self.preview_report = None
            self._render_preview_report(None, "Open a document and import geometry to preview.")
            return None
        if not self.cache or not self.index.get("faces"):
            self.preview_layer.clear()
            self.preview_report = None
            self._render_preview_report(None, "Import STEP geometry to preview the preprocessing.")
            return None
        blocker = self._preview_geometry_blocker()
        if blocker:
            self.clear_run_report()
            self.preview_layer.clear()
            self.preview_report = None
            self._render_preview_report(
                None, "Preview blocked by the document geometry/source diagnostic: " + blocker)
            return None
        cad = self._cad_geometry()
        if cad is None:
            self.preview_layer.clear()
            self.preview_report = None
            self._render_preview_report(
                None, "The cached geometry.cad.json is unavailable; reimport the source STEP.")
            return None
        drift = self._widget_assignment_drift()
        toggles = self._preview_toggles()
        if not any(toggles.values()):
            self.preview_layer.clear()
            self.preview_report = None
            self._render_preview_report(None, "All preview layers are off.")
            return None
        report = self.preview_layer.rebuild(self.doc, self.project, self.index, cad, toggles,
                                            entities=self.entities)
        report["widget_state"] = drift or "committed assignment"
        self.preview_report = report
        self._render_preview_report(report)
        return report

    def _render_preview_report(self, report, message=None):
        table = getattr(self, "preview_refinement_table", None)
        if report is None:
            if table is not None:
                table.setRowCount(0)
            for attribute in ("preview_quadrature", "preview_display", "preview_legend",
                              "preview_diagnostics", "preview_inactive", "preview_frame_legend"):
                widget = getattr(self, attribute, None)
                if widget is not None:
                    widget.setText("")
            notes = getattr(self, "preview_notes", None)
            if notes is not None:
                notes.setPlainText("")
            if getattr(self, "preview_status", None) is not None:
                self.preview_status.setText(message or "Preview is idle.")
            return
        refinement = report.get("refinement") or {}
        if table is not None:
            table.setRowCount(0)
            for row in (refinement.get("analysis") or {}).get("faces", []):
                estimate = row.get("estimate_control_points")
                self.table_row(table, [
                    row["face_id"], "yes" if row["active"] else "no",
                    f"{row['original_degree'][0]},{row['original_degree'][1]}",
                    f"{row['target_degree'][0]},{row['target_degree'][1]}",
                    f"{row['spans'][0]}/{row['spans'][1]}",
                    f"{row['original_control_points']['u']}×{row['original_control_points']['v']}",
                    (f"{estimate['u']}×{estimate['v']}" if estimate else "unknown"),
                    f"{row['inserts'][0]}/{row['inserts'][1]}",
                    f"{row['min_continuity'][0]},{row['min_continuity'][1]}"
                    if len(row["min_continuity"]) == 2 else "?"], ())
        quadrature = refinement.get("quadrature") or {}
        if getattr(self, "preview_quadrature", None) is not None:
            requested = ", ".join(f"face {entry['face_id']}: {entry['requested_points_per_span']}"
                                  for entry in quadrature.get("faces", [])) or "no active patch"
            self.preview_quadrature.setText(
                f"Requested points per span (0 = target default): {requested}. "
                + quadrature.get("note", ""))
        display = refinement.get("display") or {}
        if getattr(self, "preview_display", None) is not None:
            self.preview_display.setText(
                f"Visualization subdivision: {display.get('subdivision')}. "
                + display.get("note", ""))
        legend = report.get("material_legend")
        if getattr(self, "preview_legend", None) is not None:
            if legend is None:
                self.preview_legend.setText(
                    "Enable the Material / Section layer to colour the active faces and list the "
                    "exact current material and section references.")
            else:
                lines = [f"face {row['face_id']} ({'active' if row['active'] else 'inactive'}): "
                         f"material {row['material_id']} \"{row['material_name']}\" "
                         f"E={row['young']} {row['young_unit']} · section {row['section_id']} "
                         f"\"{row['section_name']}\" t={row['thickness']} {row['thickness_unit']}"
                         for row in legend["rows"]]
                self.preview_legend.setText("  |  ".join(lines) + "  —  " + legend["note"])
        diagnostics = report.get("diagnostics") or {}
        if getattr(self, "preview_diagnostics", None) is not None:
            errors = diagnostics.get("errors") or []
            self.preview_diagnostics.setText(
                "no zero-span, decreasing or non-finite knot, and no non-positive weight detected "
                "(repeated knots are normal multiplicity)." if not errors
                else "ERROR: " + "; ".join(errors))
        inactive = (report.get("inactive_support_targets") or []) + \
                   (report.get("inactive_load_targets") or [])
        inactive_frames = report.get("inactive_frames") or []
        if getattr(self, "preview_inactive", None) is not None:
            if not inactive and not inactive_frames:
                self.preview_inactive.setText(
                    "none: every drawn physics target is inside the active analysis domain.")
            else:
                self.preview_inactive.setText(
                    "no glyph: " + "; ".join(inactive + [f"frame face {face}"
                                                          for face in inactive_frames]))
        if getattr(self, "preview_frame_legend", None) is not None:
            entries = report.get("frame_legend") or []
            self.preview_frame_legend.setText("  |  ".join(
                f"{entry['axis']}: {entry['label']} rgb"
                f"{tuple(round(component, 2) for component in entry['color'])}"
                for entry in entries))
        notes_widget = getattr(self, "preview_notes", None)
        if notes_widget is not None:
            notes_widget.setPlainText("\n".join(report.get("notes") or []))
        if getattr(self, "preview_status", None) is not None:
            scales = report.get("scales") or {}
            length_unit = report.get("length_unit", "mm")
            scale_text = " ".join(
                f"{family}: 1 {entry['unit']} ↔ {entry['scale']:.4g} {length_unit}"
                for family, entry in sorted(scales.items()) if entry.get("scale")) or "no load scale"
            blocked = report.get("blocked")
            if blocked:
                self.preview_status.setText(f"Preview blocked by a valid diagnostic: {blocked}")
            elif message:
                self.preview_status.setText(message)
            else:
                text = (f"Preview: {report.get('supports', 0)} support · {report.get('loads', 0)} load "
                        f"· {report.get('frames', 0)} frame axis object(s); "
                        f"{report.get('materials', 0)} face(s) recoloured. {scale_text}.")
                text += f" Source: {report.get('widget_state', 'committed assignment')}."
                if inactive or inactive_frames:
                    text += f" {len(inactive) + len(inactive_frames)} inactive target(s) have no glyph."
                self.preview_status.setText(text)

    def _invalidate_run_report(self, *_args):
        """A relevant table cell changed: the initialized report is no longer current."""
        # The static-validation evidence belongs to the model that was validated; a changed
        # cell drops it back to unknown instead of leaving a stale pass on screen.
        self.result_state.validate_result = None
        self.clear_run_report()
        self._refresh_result_evidence()

    def clear_run_report(self):
        """Drop any initialized report; it belongs to one exact run directory."""
        self.initialized_report = None
        view = getattr(self, "run_report_view", None)
        if view is not None:
            view.setPlainText("")
        status = getattr(self, "run_report_status", None)
        if status is not None:
            status.setText("No initialized report. Export a model and press Initialize Exported Model.")
        path_label = getattr(self, "run_report_path", None)
        if path_label is not None:
            path_label.setText("Run directory: none yet.")

    def refresh_run_report(self):
        """Read the exact run directory's report after a run; read-only.

        Only ``self.last_run`` is ever read. A failed launch still shows that run's own
        report (if it wrote one) and can never re-display an earlier run's success.
        Known fields are rendered defensively: a field the report does not carry is
        shown as ``unknown`` instead of being guessed.
        """
        status = getattr(self, "run_report_status", None)
        view = getattr(self, "run_report_view", None)
        path_label = getattr(self, "run_report_path", None)
        if not self.last_run or not Path(self.last_run).is_dir():
            self.clear_run_report()
            return None
        folder = Path(self.last_run)
        report_path = folder / "run_report.json"
        source = "run_report.json"
        if not report_path.is_file():
            # The export-only report is a weak fallback: it describes the exported case,
            # not this run's solve, and is labelled as such below.
            report_path = folder / "export_report.json"
            source = "export_report.json"
        if path_label is not None:
            path_label.setText(f"Run directory: {folder}")
        if not report_path.is_file():
            self.initialized_report = None
            if view is not None:
                view.setPlainText("")
            if status is not None:
                status.setText(f"No run_report.json in this run yet ({folder.name}); press "
                               "Initialize Exported Model or Run Exported Analysis.")
            return None
        try:
            data = read_json(report_path)
        except Exception as exc:  # noqa: BLE001 - an unreadable report is reported
            self.initialized_report = None
            if view is not None:
                view.setPlainText("")
            if status is not None:
                status.setText(f"Unreadable {source} in {folder.name}: {exc}")
            return None
        self.initialized_report = data
        if status is not None:
            mode = data.get("mode")
            if source != "run_report.json":
                status.setText(f"Exported case only (this run produced no report): "
                               f"{folder.name} · {source}; initialized statistics are unknown")
            elif mode == "initialize":
                status.setText(f"Initialized run: {folder.name} · {source} · "
                               f"mode={mode} (pre-solve)")
            elif mode == "solve":
                status.setText(f"Solved run: {folder.name} · {source} · "
                               f"mode={mode}; area frozen from Initialize")
            else:
                status.setText(f"Run report: {folder.name} · {source} · mode={mode}")
        if view is not None:
            view.setPlainText(self._format_run_report(data))
        return data

    @staticmethod
    def _format_run_report(data):
        def known(key):
            return data.get(key, "unknown")
        area = data.get("area_check") or {}
        mode = data.get("mode")
        if mode == "solve":
            phase = "solve end (area frozen from Initialize)"
        elif mode == "initialize":
            phase = "Initialize (reference configuration, pre-solve)"
        else:
            phase = area.get("measured_at", "unknown")
        lines = [
            f"measurement phase: {phase}",
            f"mode: {known('mode')}",
            f"nodes: {known('nodes')}   elements: {known('elements')}   conditions: {known('conditions')}",
            f"active element nodes: {known('active_element_nodes')}   "
            f"active condition nodes: {known('active_condition_nodes')}",
        ]
        dofs = data.get("displacement_dofs") or {}
        lines.append("displacement DOFs total/fixed/free: "
                     f"{dofs.get('total', 'unknown')}/{dofs.get('fixed', 'unknown')}/"
                     f"{dofs.get('free', 'unknown')}"
                     + (f"  ({dofs['definition']})" if dofs.get("definition") else ""))
        for key, label in (("element_integration_points", "element integration points"),
                           ("condition_integration_points", "condition integration points")):
            counts = data.get(key) or {}
            lines.append(f"{label}: entities={counts.get('entities', 'unknown')} "
                         f"total={counts.get('total', 'unknown')} "
                         f"distinct={counts.get('distinct', 'unknown')}")
        lines.append(f"actual physical area: {known('actual_physical_area')} "
                     f"(finite={known('actual_physical_area_finite')})")
        area = data.get("area_check") or {}
        lines.append(f"area_check: {area.get('status', 'unknown')} · "
                     f"tolerance={area.get('tolerance_relative', 'unknown')} · "
                     f"{area.get('measured_at', 'unknown')}")
        if area.get("reason"):
            lines.append(f"area_check reason: {area['reason']}")
        for mismatch in area.get("mismatches") or []:
            lines.append(f"  mismatch patch {mismatch.get('face_id')}: "
                         f"actual={mismatch.get('actual_area')} "
                         f"expected={mismatch.get('expected_area')} "
                         f"rel={mismatch.get('relative_error')}")
        for patch in data.get("patches") or []:
            lines.append(
                f"patch {patch.get('face_id')}: elements={patch.get('elements', 'unknown')} "
                f"degree={patch.get('degree', 'unknown')} "
                f"control_points={patch.get('control_points', 'unknown')} "
                f"spans={patch.get('positive_knot_spans', 'unknown')} "
                f"actual_area={patch.get('actual_area', 'unknown')} "
                f"expected_area={patch.get('expected_area', 'unknown')} "
                f"area_match={patch.get('area_match', 'unknown')} "
                f"quadrature_per_span={patch.get('requested_quadrature_per_span', 'unknown')} "
                f"target_degree={patch.get('target_degree', 'unknown')} "
                f"grid_match={patch.get('grid_match', 'unknown')}")
        lines.append(f"Report phase: {phase}. Only the exact exported case is measured; this is "
                     "initialization, not a solve or a mechanical verification, unless the phase "
                     "says solve end. Fields absent from the report stay unknown.")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # P0-6 result display (native results.vtkhdf through the backend CLI)
    # ------------------------------------------------------------------ #
    def _build_result_state(self):
        """Create the result state and bind the controller to that exact object.

        The panel reads ``self.result_state``; the controller writes ``controller.state``.
        They must be the same instance, so this is the single place both are created.
        """
        self.result_state = ResultState()
        self.result_controller = ResultController(
            fetch=self._fetch_result_payload,
            render=self._render_result_frame,
            unrender=self._clear_result_objects,
            state=self.result_state)
        return self.result_state

    def build_result_section(self, layout):
        """The Analysis-page result controls. Widget construction only."""
        heading = W.QLabel("Result display · native results.vtkhdf read through the backend Python")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        run_row = W.QHBoxLayout()
        self.result_run_dir = W.QComboBox()
        self.result_run_dir.setEditable(True)
        self.result_run_dir.setToolTip(
            "One isolated run directory. The default is this panel's last run; older "
            "independent run directories beside the export can be selected as well.")
        run_row.addWidget(self.result_run_dir, 1)
        run_row.addWidget(button("Use Last Run", self.use_last_result_run))
        run_row.addWidget(button("Browse Run…", self.browse_result_run))
        layout.addLayout(run_row)
        options = W.QHBoxLayout()
        self.result_step = W.QComboBox()
        self.result_step.setToolTip(
            "The real recorded time-step index from the VTKHDF Steps/Values block; steps are "
            "never interpolated.")
        options.addWidget(W.QLabel("Step"))
        options.addWidget(self.result_step, 1)
        self.result_component = W.QComboBox()
        for component in RESULT_COMPONENTS:
            self.result_component.addItem(component, component)
        self.result_component.setToolTip("Displayed displacement component or magnitude.")
        options.addWidget(W.QLabel("Component"))
        options.addWidget(self.result_component)
        self.result_scale = spin(1.0, 0.0, 1000.0, 4)
        self.result_scale.setToolTip(
            "Multiplies only the selected step's displacement; 0 restores the reference "
            "sampling geometry.")
        options.addWidget(W.QLabel("Scale"))
        options.addWidget(self.result_scale)
        layout.addLayout(options)
        buttons = W.QHBoxLayout()
        self.result_load_button = button("Load Result Metadata", self.load_result_metadata)
        self.result_draw_button = button("Draw Result Frame", self.draw_result_frame)
        self.result_clear_button = button(
            "Clear Result Display",
            lambda: self.clear_result_display(message="Result display cleared."))
        for widget in (self.result_load_button, self.result_draw_button,
                       self.result_clear_button):
            buttons.addWidget(widget)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.result_status = text_hint(
            "No result is loaded. Choose a run directory and press Load Result Metadata.")
        layout.addWidget(self.result_status)
        self.result_mapping_note = text_hint(
            "face/UV unavailable: a plain VTKHDF carries no face/UV mapping, so the drawn "
            "native sampling cells are NOT the trimmed integration domain and a drawn frame is "
            "not a verification. The frame is read through the backend Python and is never "
            "written to disk.")
        layout.addWidget(self.result_mapping_note)
        self.result_metadata_view = W.QPlainTextEdit()
        self.result_metadata_view.setReadOnly(True)
        self.result_metadata_view.setMaximumHeight(150)
        self.result_metadata_view.setPlaceholderText(
            "Native results.vtkhdf metadata: case phase, association, point/cell counts and the "
            "real recorded step times appear here after Load Result Metadata.")
        layout.addWidget(self.result_metadata_view)
        self.result_extrema_view = W.QPlainTextEdit()
        self.result_extrema_view.setReadOnly(True)
        self.result_extrema_view.setMaximumHeight(110)
        self.result_extrema_view.setPlaceholderText(
            "Extrema of the displayed component with their real positions appear here after "
            "Draw Result Frame.")
        layout.addWidget(self.result_extrema_view)
        self.result_evidence_view = W.QPlainTextEdit()
        self.result_evidence_view.setReadOnly(True)
        self.result_evidence_view.setMaximumHeight(140)
        self.result_evidence_view.setPlaceholderText(
            "Static validation, initialization, solve and reference-case evidence are listed "
            "independently; a level is only shown when its own evidence exists.")
        layout.addWidget(self.result_evidence_view)
        self.result_forces_view = W.QPlainTextEdit()
        self.result_forces_view.setReadOnly(True)
        self.result_forces_view.setMaximumHeight(170)
        self.result_forces_view.setPlaceholderText(
            "Separated force / equilibrium evidence from run_report.json: strong support "
            "reactions, applied loads and the raw, unsigned penalty contributions, each with "
            "its own status. Penalty physics reactions are never invented.")
        layout.addWidget(self.result_forces_view)

    def _known_run_dirs(self):
        """Independent run directories created beside the current export, newest first."""
        export = Path(self.last_export) if self.last_export else None
        if export is None or not export.is_dir():
            return []
        try:
            found = [entry for entry in export.iterdir()
                     if entry.is_dir() and entry.name.startswith(run_dirs.RUN_PREFIX)]
        except OSError:
            return []
        return sorted(found, key=lambda entry: entry.name, reverse=True)

    def refresh_result_run_dirs(self):
        combo = getattr(self, "result_run_dir", None)
        if combo is None:
            return
        current = combo.currentText().strip()
        ordered = []
        for candidate in ([str(self.last_run)] if self.last_run else []) \
                + [str(entry) for entry in self._known_run_dirs()]:
            if candidate and candidate not in ordered:
                ordered.append(candidate)
        combo.blockSignals(True)
        combo.clear()
        for candidate in ordered:
            combo.addItem(candidate)
        if current:
            combo.setCurrentText(current)
        elif ordered:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def selected_result_run_dir(self):
        combo = getattr(self, "result_run_dir", None)
        if combo is None:
            return ""
        text = combo.currentText()
        return text.strip() if isinstance(text, str) else ""

    def use_last_result_run(self):
        if not self.last_run or not Path(self.last_run).is_dir():
            self.status.setText("No run directory is available yet; press Initialize or Run first.")
            return
        combo = getattr(self, "result_run_dir", None)
        if combo is not None:
            combo.setCurrentText(str(self.last_run))
        self.status.setText(f"Result run directory set to the last run: {self.last_run}")

    def browse_result_run(self):
        folder = W.QFileDialog.getExistingDirectory(
            self, "Select an Isolated Run Directory", str(self.work_dir))
        if not folder:
            return
        combo = getattr(self, "result_run_dir", None)
        if combo is not None:
            combo.setCurrentText(folder)
        self.status.setText(f"Result run directory selected: {folder}")

    def _read_result_run_report(self, run_dir):
        path = Path(run_dir) / "run_report.json"
        if not path.is_file():
            return None
        try:
            return read_json(path)
        except Exception:  # noqa: BLE001 - an unreadable report is shown as unknown
            return None

    def _read_reference_evidence(self, run_dir):
        """An explicit reference verification record, when the run directory carries one."""
        path = Path(run_dir) / "example_verification.json"
        if not path.is_file():
            return None
        try:
            return read_json(path)
        except Exception:  # noqa: BLE001
            return None

    def _result_cad_mm(self):
        """The current cache CAD in millimetres; the CLI converts it to the export unit."""
        if not self.cache:
            raise ValueError("Import STEP geometry first; the current CAD is required to "
                             "associate a result.")
        path = Path(self.cache) / "geometry.cad.json"
        if not path.is_file():
            raise ValueError(f"The current cache has no geometry.cad.json at {path}; reimport "
                             "the geometry.")
        return read_json(path)

    def _result_document(self):
        doc = self.doc if self.doc is not None and self.doc.Name in App.listDocuments() else None
        if doc is None:
            raise ValueError("The analysis document is closed. Reimport the geometry or reopen "
                             "the project before drawing results.")
        if self.result_layer.doc is not doc:
            try:
                self.result_layer.attach(doc)
            except Exception as error:  # noqa: BLE001
                raise ValueError(
                    f"The result layer could not attach to the document: {error}") from error
        return doc

    def _original_face_object_names(self):
        """Only the original face objects of ``self.entities`` may have visibility changed."""
        return [name for (kind, _ident), name in self.entities.items()
                if kind == "face" and name]

    def _fetch_result_payload(self, request):
        python = self.backend_python.text().strip()
        if not Path(python).is_file():
            raise ResultViewError("Backend Python was not found. Configure its executable in "
                                  "Analysis and Output.")
        cli = BASE / "kratos_iga" / "results_cli.py"
        if not cli.is_file():
            raise ResultViewError(f"The backend result bridge is missing: {cli}")
        return run_result_cli(python, cli, request, timeout=RESULT_CLI_TIMEOUT_SECONDS)

    def _render_result_frame(self, payload, component, cad_objects):
        self._result_document()
        return self.result_layer.apply_frame(payload, component=component,
                                             cad_objects=cad_objects)

    def _clear_result_objects(self, doc=None):
        """Remove only marker-owned result objects and restore the CAD visibility we changed.

        Both the layer's own document and an explicitly passed document are cleaned, so a
        document switch restores the old document and clears any stale marker-owned object.
        Foreign objects are never adopted or deleted (the layer refuses those by name).
        """
        targets = []
        for candidate in (self.result_layer.doc, doc):
            if candidate is None:
                continue
            if any(candidate is existing for existing in targets):
                continue
            targets.append(candidate)
        log = getattr(self, "log", None)
        if not targets:
            try:
                self.result_layer.clear(None)
            except Exception as error:  # noqa: BLE001
                if log is not None:
                    log.appendPlainText(f"Result display cleanup reported: {error}")
            return
        for target in targets:
            try:
                self.result_layer.clear(target)
            except Exception as error:  # noqa: BLE001 - best effort while closing a document
                if log is not None:
                    log.appendPlainText(f"Result display cleanup reported: {error}")

    def _selected_result_step(self):
        combo = getattr(self, "result_step", None)
        if combo is None:
            raise ValueError("No time-step selector is available.")
        data = combo.currentData()
        if data is None:
            text = combo.currentText().split("·")[0].strip()
            try:
                data = int(text)
            except (TypeError, ValueError):
                raise ValueError(
                    "Select a real time-step index from the loaded metadata.") from None
        if isinstance(data, bool) or not isinstance(data, int):
            try:
                data = int(data)
            except (TypeError, ValueError):
                raise ValueError("The selected time-step index is not an integer.") from None
        if data < 0:
            raise ValueError(f"The selected time-step index {data} is negative.")
        return data

    def _selected_result_scale(self):
        widget = getattr(self, "result_scale", None)
        if widget is None:
            return 1.0
        try:
            value = float(widget.value())
        except (TypeError, ValueError):
            raise ValueError("The deformation scale is not a number.") from None
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"The deformation scale must be finite and >= 0, got {value!r}.")
        return value

    def _selected_result_component(self):
        combo = getattr(self, "result_component", None)
        value = combo.currentData() if combo is not None else None
        if value is None and combo is not None:
            value = combo.currentText()
        if value not in RESULT_COMPONENTS:
            raise ValueError(f"Select a displacement component from {RESULT_COMPONENTS}.")
        return value

    def _populate_result_steps(self, payload):
        combo = getattr(self, "result_step", None)
        if combo is None:
            return
        vtkhdf = payload.get("vtkhdf") if isinstance(payload, dict) else None
        times = vtkhdf.get("times") if isinstance(vtkhdf, dict) else None
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for index, time in enumerate(times or []):
            combo.addItem(f"{index} · t={_format_value(time)}", index)
        if combo.count():
            position = combo.findData(previous)
            combo.setCurrentIndex(position if position >= 0 else 0)
        combo.blockSignals(False)

    def _refresh_result_evidence(self):
        view = getattr(self, "result_evidence_view", None)
        if view is None:
            return
        state = self.result_state
        run_dir = state.run_dir or (str(self.last_run) if self.last_run else "")
        run_report = state.run_report
        if run_dir and run_report is None:
            run_report = self._read_result_run_report(run_dir)
        reference = state.reference_evidence
        if run_dir and reference is None:
            reference = self._read_reference_evidence(run_dir)
        levels = result_evidence_levels(validate_result=state.validate_result,
                                        run_report=run_report, cli_case=state.cli_case(),
                                        reference_evidence=reference)
        view.setPlainText(format_result_evidence(levels))
        forces = getattr(self, "result_forces_view", None)
        if forces is not None:
            forces.setPlainText(format_result_forces(run_report))

    def _render_result_views(self):
        state = self.result_state
        metadata_view = getattr(self, "result_metadata_view", None)
        if metadata_view is not None:
            if state.metadata:
                metadata_view.setPlainText(format_result_metadata(state.metadata))
            else:
                metadata_view.setPlainText(state.error or "No result metadata is loaded.")
        extrema = getattr(self, "result_extrema_view", None)
        if extrema is not None:
            if state.frame:
                extrema.setPlainText(format_result_extrema(state.frame, state.summary))
            else:
                extrema.setPlainText(state.error or "No result frame is drawn.")
        status = getattr(self, "result_status", None)
        if status is not None:
            if state.error:
                status.setText(f"Result error: {state.error}")
            elif state.status_text:
                status.setText(state.status_text)
            elif state.metadata:
                status.setText("Result metadata loaded; press Draw Result Frame to display a "
                               "frame.")
            else:
                status.setText("No result is loaded. Choose a run directory and press Load "
                               "Result Metadata.")
        self._refresh_result_evidence()

    def clear_result_display(self, doc=None, message=None, drop_metadata=True, full=False):
        """Clear our marker-owned result objects and restore the CAD visibility we changed.

        ``full`` also forgets the loaded run identity (used when the model or document
        changes); the plain button keeps the run so its evidence stays visible.
        """
        self._clear_result_objects(doc)
        if full:
            self.result_state.reset()
            if message:
                self.result_state.error = message
        else:
            self.result_state.clear_display(message=message, drop_metadata=drop_metadata)
        self._render_result_views()

    def load_result_metadata(self):
        def operation():
            run_dir = self.selected_result_run_dir()
            selected_dir = Path(run_dir) if run_dir else None
            try:
                if self.process:
                    raise ValueError("A background task is already running; wait for it before "
                                     "loading results.")
                if not run_dir:
                    raise ValueError("Select an isolated run directory (or use the last run) "
                                     "first.")
                if not selected_dir.is_dir():
                    raise ValueError(f"The result run directory does not exist: {run_dir}")
                self.sync()
                cad = self._result_cad_mm()
                self.result_state.run_dir = str(run_dir)
                self.result_state.run_report = self._read_result_run_report(run_dir)
                self.result_state.reference_evidence = self._read_reference_evidence(run_dir)
                payload = self.result_controller.load_metadata(run_dir, self.project, cad)
            except Exception as error:
                # Every failed attempt removes a previously drawn result: a stale picture
                # must never survive a load refusal, including a preflight, project-sync or
                # current-CAD failure that happens before the backend is even called.
                self.clear_result_display(message=_error_text(error), drop_metadata=True)
                if selected_dir is not None and selected_dir.is_dir():
                    # Keep the selected run's own report/reference evidence on screen.
                    self.result_state.run_dir = str(run_dir)
                    self.result_state.run_report = self._read_result_run_report(run_dir)
                    self.result_state.reference_evidence = self._read_reference_evidence(run_dir)
                else:
                    # No valid run is selected; do not leave the previous run's evidence on
                    # screen as if it described this failed attempt.
                    self.result_state.run_dir = None
                    self.result_state.run_report = None
                    self.result_state.reference_evidence = None
                self._render_result_views()
                raise
            self._populate_result_steps(payload)
            self.result_state.status_text = (
                f"Result metadata loaded for {Path(run_dir).name}; "
                f"{len(payload.get('vtkhdf', {}).get('times') or [])} recorded step(s).")
            self._render_result_views()
            self.status.setText(self.result_state.status_text)
        self.guard(operation)

    def draw_result_frame(self):
        def operation():
            try:
                if self.process:
                    raise ValueError("A background task is already running; wait for it before "
                                     "drawing results.")
                if self.result_state.metadata is None:
                    raise ValueError("Load the result metadata for a run directory before "
                                     "drawing a frame.")
                self.sync()
                cad = self._result_cad_mm()
                step = self._selected_result_step()
                scale = self._selected_result_scale()
                component = self._selected_result_component()
                cad_objects = self._original_face_object_names()
                payload, _summary = self.result_controller.draw_frame(
                    self.project, cad, step=step, scale=scale, component=component,
                    cad_objects=cad_objects)
            except Exception as error:
                # Same rule as loading: a refused draw attempt removes the previous frame so
                # an old picture is never left looking like this attempt's result.
                self.clear_result_display(message=_error_text(error), drop_metadata=True)
                raise
            self.result_state.status_text = (
                "Result frame drawn: step {} · t={} · {} · scale {}. Native sampling cells "
                "only; face/UV mapping is unavailable.".format(
                    payload["step"]["index"], _format_value(payload["step"]["time"]),
                    payload["step"]["component"], _format_value(payload["step"]["scale"])))
            self._render_result_views()
            self.status.setText(self.result_state.status_text)
        self.guard(operation)

    def _refresh_run_report_and_results(self):
        """Run-completion hook: keep B1's report view and refresh the result evidence."""
        self.refresh_run_report()
        self.refresh_result_run_dirs()
        self._refresh_result_evidence()

    def build_output(self):
        layout = self.page("Analysis")
        form = W.QFormLayout()
        self.solver_type = W.QComboBox()
        self.solver_type.addItem("Static", "static")
        self.solver_type.addItem("Implicit Dynamic", "dynamic")
        self.analysis_type = W.QComboBox()
        self.analysis_type.addItem("Linear", "linear")
        self.analysis_type.addItem("Nonlinear", "non_linear")
        self.start_time, self.end_time, self.time_step = W.QLineEdit("0.0"), W.QLineEdit("1.0"), W.QLineEdit("1.0")
        for label, w in [("Solver", self.solver_type), ("Analysis", self.analysis_type), ("Start Time", self.start_time), ("End Time", self.end_time), ("Time Increment", self.time_step)]:
            form.addRow(label, w)
        self.vtk = W.QCheckBox("Export Displacement to VTKHDF")
        self.vtk.setChecked(True)
        self.output_refinement = spin(4, 1, 100)
        form.addRow(self.vtk)
        form.addRow("Visualization Subdivision", self.output_refinement)
        document_row = W.QHBoxLayout()
        document_row.addWidget(button("Save to Document", self.save_to_document))
        document_row.addWidget(button("Restore Document", self.restore_document))
        document_row.addStretch(1)
        layout.addLayout(document_row)
        layout.addWidget(text_hint("Save to Document writes the committed project into the open .FCStd through the native save observer. Restore Document is the document entry point; Open JSON stays a separate, explicit entry point - they are never chosen silently by timestamp."))
        layout.addLayout(form)
        self.backend_python = W.QLineEdit(self.settings["backend_python"])
        self.pipeline_root = W.QLineEdit(self.settings["pipeline_root"])
        form.addRow("Backend Python Executable", self.backend_python)
        form.addRow("Conversion Pipeline Directory", self.pipeline_root)
        self.work_directory = W.QLineEdit(str(self.work_dir))
        form.addRow("Working Directory", self.work_directory)
        layout.addWidget(button("Save Backend Configuration", lambda: self.guard(self.save_backend_configuration)))
        layout.addWidget(button("Check Backend Environment", lambda: self.guard(self.check_backend_environment)))
        layout.addWidget(text_hint("Export CAD geometry, refinement, constitutive properties, boundary conditions and solver configuration. Backend: Kratos 10.4.3 with the required structural analysis applications."))
        layout.addWidget(button("Export CAD Geometry Only", self.export_geometry))
        layout.addWidget(button("Initialize Exported Model", lambda: self.run_case(True)))
        layout.addWidget(button("Run Exported Analysis", lambda: self.run_case(False)))
        layout.addWidget(button("Stop Background Task", self.cancel))
        layout.addWidget(W.QLabel("Result of this run only (read-only report)"))
        self.run_report_path = text_hint("Run directory: none yet.")
        layout.addWidget(self.run_report_path)
        self.run_report_status = text_hint(
            "No initialized report. Export a model and press Initialize Exported Model.")
        layout.addWidget(self.run_report_status)
        self.run_report_view = W.QPlainTextEdit()
        self.run_report_view.setReadOnly(True)
        self.run_report_view.setMaximumHeight(240)
        self.run_report_view.setPlaceholderText(
            "Each Initialize/Run writes a new isolated run directory beside the export and copies "
            "only the exported inputs; this run's nodes, active nodes, DOF totals, integration "
            "counts, per-patch initialized degree/control points/area and the area_check status "
            "appear here. Fields the report does not carry are shown as unknown.")
        layout.addWidget(self.run_report_view)
        for table in (self.material_table, self.section_table, self.patch_table,
                      self.assignment_table):
            table.itemChanged.connect(self._invalidate_run_report)
        self.build_result_section(layout)
        self.refresh_result_run_dirs()
        layout.addStretch()

    def error(self, error):
        self.status.setText("Operation failed: "+str(error))
        self.log.appendPlainText(str(error))
        self.log.show()
        self.log_toggle.setText("Hide Log")

    def guard(self, callback):
        try:
            return callback()
        except Exception as exc:
            self.error(exc)
            return None

    def choose_profile(self):
        path, _ = W.QFileDialog.getOpenFileName(self, "Conversion Profile", str(Path.home()), "JSON (*.json)")
        if path:
            self.profile.setText(path)

    def import_step(self):
        def operation():
            # Guards live inside the guarded operation so a refusal is a visible status
            # message, not an exception escaping the button callback.
            if self.process:
                raise ValueError("A background task is already running; wait for it before importing.")
            if self.snapshot:
                raise ValueError("A controlled snapshot is pending; apply or cancel it before importing.")
            path, _ = W.QFileDialog.getOpenFileName(self, "Import STEP", str(Path.home()), "STEP (*.stp *.step *.STP *.STEP)")
            if path:
                self.pending_project = None
                self.start_import(path)
        self.guard(operation)

    def start_import(self, path):
        if self.process:
            raise ValueError("A background task is already running.")
        if self.snapshot:
            raise ValueError("A controlled snapshot is pending; apply or cancel it before importing "
                             "other geometry so its preview and mapping are not orphaned.")
        self.import_path = str(Path(path).resolve())
        cache = self.work_dir/"cache"/uuid.uuid4().hex
        cache.mkdir(parents=True)
        self.import_cache = cache
        self.start_worker({"action": "convert", "source_step": self.import_path,
            "profile": self.profile.text().strip(), "cache_dir": str(cache)}, self.import_finished)

    def start_worker(self, request, callback):
        request["pipeline_root"] = self.pipeline_root.text()
        root = self.work_dir/"requests"
        root.mkdir(parents=True, exist_ok=True)
        filename = root/(uuid.uuid4().hex+".json")
        write_json(filename, request)
        self.start_process([str(BASE/"kratos_iga"/"worker.py"), str(filename)], callback)

    def start_process(self, arguments, callback, completion=None):
        if self.process:
            raise ValueError("A background task is already running.")
        python = self.backend_python.text().strip()
        if not Path(python).is_file():
            raise ValueError("Backend Python was not found. Configure its executable in Analysis and Output.")
        environment = os.environ.copy()
        environment.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", OMP_NUM_THREADS="4")
        jobs = self.work_dir/"jobs"
        jobs.mkdir(parents=True, exist_ok=True)
        logfile = jobs/(uuid.uuid4().hex+".log")
        # File-backed logs also work where Qt's Windows named pipes are unavailable.
        with logfile.open("wb") as stream:
            proc = subprocess.Popen([python]+arguments, stdout=stream, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.process = proc
        self.stop_button.setEnabled(True)
        self.progress.show()
        self.tabs.setEnabled(False)
        self.status.setText("Background task in progress…")
        timer = QtCore.QTimer(self)
        offset = 0
        def poll():
            nonlocal offset
            with logfile.open("rb") as stream:
                stream.seek(offset)
                chunk = stream.read()
                offset = stream.tell()
            if chunk:
                self.log.appendPlainText(chunk.decode("utf-8", "replace").rstrip())
            code = proc.poll()
            if code is None:
                return
            timer.stop()
            timer.deleteLater()
            self.status.setText("Completed" if code == 0 else "Background task failed or was stopped. Inspect the log.")
            if code == 0:
                self.guard(callback)
            if completion is not None:
                # Runs for every exit code (a non-zero --check may still have written a
                # report), but never instead of the exit-0 callback and never for imports,
                # which do not pass a completion hook.
                self.guard(completion)
            self.process = None
            self.stop_button.setEnabled(False)
            self.progress.hide()
            self.tabs.setEnabled(True)
            if code != 0:
                self.log.show()
                self.log_toggle.setText("Hide Log")
        timer.timeout.connect(poll)
        timer.start(250)

    def cancel(self):
        if self.process:
            self.process.kill()

    def import_finished(self):
        new_index = read_json(self.import_cache/"index.json")
        if self.pending_project:
            # Validate BEFORE touching the existing document hook: a rejected project
            # must leave the previous association intact.
            if self.pending_project["source_sha256"] != new_index["source_sha256"]:
                raise ValueError("The source STEP file has changed.")
            if self.pending_project.get("geometry_sha256", new_index.get("geometry_sha256")) != new_index.get("geometry_sha256"):
                pending = self.pending_project
                reason = "the converted geometry changed; rebind explicitly"
                for category in ("supports", "loads", "initial_conditions"):
                    for record in pending.get(category, []):
                        record["needs_rebind"] = reason
                for group in pending.get("groups", []):
                    if isinstance(group, dict):
                        group["needs_rebind"] = reason
                pending["source_sha256"] = new_index["source_sha256"]
                pending["geometry_sha256"] = new_index.get("geometry_sha256")
                pending["geometry_needs_rebind"] = reason
                self.detach_from_document()
                self.project = pending
                self.pending_project = None
                self.status.setText("Geometry changed: every binding is pending rebind. "
                                    "Resolve them in Groups before exporting.")
            else:
                self.detach_from_document()
                self.project = self.pending_project
                self.pending_project = None
        else:
            self.detach_from_document()
            self.project = new_project()
            self.project.update({"source_step": self.import_path, "source_sha256": new_index["source_sha256"], "geometry_sha256": new_index.get("geometry_sha256"), "profile": self.profile.text().strip()})
            populate(self.project, new_index)
        self.cache = self.import_cache
        self.index = new_index
        self.show_geometry(reuse_document=self.reuse_target_document)
        self.reuse_target_document = None
        self.last_export = None
        self.last_run = None
        self.refresh_result_run_dirs()
        self._refresh_result_evidence()
        self.editing.clear()
        for category in ("supports", "loads", "initial_conditions"):
            setattr(self, category+"_targets", [])
            getattr(self, category+"_target").clear()
        self.refresh()
        self.attach_to_document()
        self.status.setText("Geometry imported | Define material properties and analysis conditions.")

    def show_geometry(self, reuse_document=None):
        reusing = (reuse_document is not None and reuse_document.Name in App.listDocuments())
        self.clear_preview(reuse_document if reusing else None)
        # A new/rebuilt model invalidates every drawn result: remove our marked objects and
        # restore the CAD visibility we changed, in the old and the incoming document.
        self.clear_result_display(reuse_document if reusing else None, full=True)
        doc = reuse_document if reusing else None
        if doc is None:
            # A fresh import always opens a new document. It never silently reuses
            # another project's document or its same-ID objects.
            doc = App.newDocument("KratosIBRA")
        self.doc = doc
        root = doc.getObject("IBRA_Geometry")
        if root is None:
            root = doc.addObject("App::DocumentObjectGroup", "IBRA_Geometry")
            root.Label = "IBRA Analysis Geometry"
        # Signatures are only carried over when the SAME document is reused; a fresh
        # import must baseline its own new objects, otherwise the previous document's
        # fingerprints would make the new geometry look edited.
        previous = dict(self.display_fingerprints) if reusing else {}
        self.entities = {}
        self.display_fingerprints = {}
        for face in self.index["faces"]:
            name = f"IBRA_Face_{face['id']}"
            obj = doc.getObject(name)
            if obj is None:
                shape = Part.Shape()
                shape.read(str(self.cache/face["brep_file"]))
                obj = doc.addObject("Part::Feature", name)
                obj.Shape = shape
                obj.Label = f"Patch {face['id']} · p{face['degrees'][0]} q{face['degrees'][1]}"
                obj.ViewObject.ShapeColor = (0.64, 0.80, 0.86)
                obj.ViewObject.LineColor = (0.16, 0.33, 0.41)
                self.tag(obj, "face", face["id"])
            else:
                # Reuse the live object: its shape is never overwritten here.
                self.entities[("face", face["id"])] = obj.Name
            if obj not in root.Group:
                root.addObject(obj)
        for edge in self.index["edges"]:
            name = f"IBRA_Edge_{edge['id']}"
            obj = doc.getObject(name)
            if obj is None:
                points = [App.Vector(*point) for point in edge["points"]]
                obj = doc.addObject("Part::Feature", name)
                obj.Shape = Part.makePolygon(points)
                obj.Label = ("Coupling " if edge["kind"] == "coupling" else "Boundary ")+str(edge["id"])
                obj.ViewObject.LineColor = (0.94, 0.51, 0.12) if edge["kind"] == "coupling" else (0.12, 0.52, 0.48)
                obj.ViewObject.LineWidth = 4.0 if edge["kind"] == "coupling" else 2.5
                self.tag(obj, edge["kind"], edge["id"])
            else:
                self.entities[(edge["kind"], edge["id"])] = obj.Name
            if obj not in root.Group:
                root.addObject(obj)
        doc.recompute()
        for key, name in self.entities.items():
            obj = doc.getObject(name)
            if obj is not None:
                # Existing objects keep their recorded fingerprint so a user edit is
                # still detected; only genuinely new objects get a fresh baseline.
                self.display_fingerprints[name] = previous.get(
                    name, (obj.Shape.hashCode(), str(obj.Placement)))
        Gui.activeDocument().activeView().viewAxonometric()
        Gui.activeDocument().activeView().fitAll()

    def tag(self, obj, kind, ident):
        obj.addProperty("App::PropertyInteger", "IbraBrepId", "IBRA")
        obj.IbraBrepId = ident
        obj.setEditorMode("IbraBrepId", 1)
        obj.addProperty("App::PropertyString", "IbraKind", "IBRA")
        obj.IbraKind = kind
        obj.setEditorMode("IbraKind", 1)
        obj.setEditorMode("Placement", 1)
        self.entities[(kind, ident)] = obj.Name
        self.display_fingerprints[obj.Name] = (obj.Shape.hashCode(), str(obj.Placement))

    def table_row(self, table, values, readonly=()):
        row = table.rowCount()
        table.insertRow(row)
        for col, value in enumerate(values):
            if isinstance(value, W.QWidget):
                table.setCellWidget(row, col, value)
            else:
                item = W.QTableWidgetItem(str(value))
                if col in readonly:
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                table.setItem(row, col, item)

    def refresh(self):
        self.geometry_table.blockSignals(True)
        self.geometry_table.setRowCount(0)
        self.geometry_rows = []
        for f in self.index["faces"]:
            self.table_row(self.geometry_table, ["Patch", f["id"], str(f["degrees"])])
            self.geometry_rows.append(("face", f["id"]))
        for e in self.index["edges"]:
            self.table_row(self.geometry_table, ["Interfaces" if e["kind"] == "coupling" else "Edge", e["id"], str(e["face_ids"])])
            self.geometry_rows.append((e["kind"], e["id"]))
        self.geometry_table.blockSignals(False)
        self.material_table.setRowCount(0)
        for m in self.project["materials"]:
            self.table_row(self.material_table, [m[k] for k in ("id", "name", "young", "poisson", "density")])
        self.section_table.setRowCount(0)
        for section in self.project.get("sections", []):
            self.table_row(self.section_table, [section[k] for k in ("id", "name", "thickness")])
        self.patch_table.setRowCount(0)
        self.assignment_table.setRowCount(0)
        for p in self.project["patches"]:
            element = element_selector()
            element.setCurrentIndex(element.findData(p["element"]))
            self.table_row(self.patch_table, [p["face_id"], p["material_id"], element, p["degree_u"], p["degree_v"], p["insert_u"], p["insert_v"], p["quadrature"]], (0,))
            assignment_element = element_selector()
            assignment_element.setCurrentIndex(assignment_element.findData(p["element"]))
            active = W.QCheckBox()
            active.setChecked(bool(p.get("active", True)))
            active.setToolTip("Inactive patches are excluded from physics, materials, refinement and VTK output; the CAD geometry stays as reference.")
            self.table_row(self.assignment_table, [p["face_id"], p["material_id"], p.get("section_id"), assignment_element, p["quadrature"], active], (0,))
            if not active.isChecked():
                for column in range(self.assignment_table.columnCount()):
                    item = self.assignment_table.item(self.assignment_table.rowCount()-1, column)
                    if item is not None:
                        item.setForeground(QtGui.QBrush(QtGui.QColor("#8a8a8a")))
        self.refinement_toggle.setChecked(self.project.get("refinement_enabled", True))
        self.refinement_content.setVisible(self.refinement_toggle.isChecked())
        edges = {e["id"]: e for e in self.index["edges"]}
        self.coupling_table.setRowCount(0)
        for c in self.project["couplings"]:
            edge = edges.get(c["edge_id"], {})
            rotation = W.QComboBox()
            rotation.addItem("Displacement only", False)
            rotation.addItem("Displacement + rotation", True)
            rotation.setCurrentIndex(1 if c.get("rotation") else 0)
            self.table_row(self.coupling_table, [c["edge_id"], str(edge.get("face_ids", [])),
                c.get("state", "?"), c.get("origin", "?"), rotation, c.get("penalty"),
                coupling_summary(c["edge_id"], edge.get("diagnostics"))], (0, 1, 2, 3, 6))
        self.refresh_group_choosers()
        for category in ("supports", "loads", "initial_conditions"):
            self.refresh_conditions(category)
        self.refresh_groups()
        self.refresh_rebind()
        self.units.setCurrentText(self.project["units"])
        self._update_unit_status()
        for key in ("start_time", "end_time", "time_step"):
            getattr(self, key).setText(str(self.project["solver"][key]))
        self.solver_type.setCurrentIndex(self.solver_type.findData(self.project["solver"]["type"]))
        self.analysis_type.setCurrentIndex(self.analysis_type.findData(self.project["solver"]["analysis_type"]))
        self.vtk.setChecked(self.project["output"]["vtk"])
        self.output_refinement.setValue(self.project["output"]["refinement"])
        self.summary.setText(f"{Path(self.project['source_step']).name}   |   Patches: {len(self.index['faces'])}   |   Interfaces: {len(self.project['couplings'])}")
        for table in (self.geometry_table, self.material_table, self.section_table, self.assignment_table, self.patch_table, self.coupling_table):
            table.resizeColumnsToContents()
        self.guard(self.refresh_preview)

    def sync(self, allow_pending_units=False, skip_geometry=False):
        if not self.cache:
            raise ValueError("Import STEP geometry first.")
        if not allow_pending_units:
            target = self.units.currentText()
            if target != self.project["units"]:
                raise ValueError(
                    f"The project still uses {self.project['units']} but {target} is selected. "
                    "Apply 'Convert Values to Selected Unit System' (or reset the selector) before "
                    "validating, saving or exporting.")
        if not self.doc or self.doc.Name not in App.listDocuments():
            raise ValueError("The analysis document is closed. Reimport geometry or reopen the project.")
        for name, signature in self.display_fingerprints.items():
            if skip_geometry:
                break
            obj = self.doc.getObject(name)
            if obj is None or (obj.Shape.hashCode(), str(obj.Placement)) != signature:
                raise ValueError("The displayed CAD geometry has changed. Save the modified model as STEP and reimport it.")
        before = deepcopy(self.project)
        def cell(table, row, col):
            return table.item(row, col).text().strip()
        self.project["materials"] = [{"id": int(cell(self.material_table, r, 0)), "name": cell(self.material_table, r, 1),
            **{key: float(cell(self.material_table, r, c)) for c, key in enumerate(("young", "poisson", "density"), 2)}} for r in range(self.material_table.rowCount())]
        self.project["sections"] = [{"id": int(cell(self.section_table, r, 0)), "name": cell(self.section_table, r, 1),
            "thickness": float(cell(self.section_table, r, 2))} for r in range(self.section_table.rowCount())]
        self.project["refinement_enabled"] = self.refinement_toggle.isChecked()
        self.project["patches"] = [{"face_id": int(cell(self.patch_table, r, 0)), "material_id": int(cell(self.assignment_table, r, 1)),
            "section_id": int(cell(self.assignment_table, r, 2)),
            "element": self.assignment_table.cellWidget(r, 3).currentData(),
            "quadrature": int(cell(self.assignment_table, r, 4)),
            "active": bool(self.assignment_table.cellWidget(r, 5).isChecked()),
            **{key: int(cell(self.patch_table, r, c)) for c, key in enumerate(("degree_u", "degree_v", "insert_u", "insert_v"), 3)}} for r in range(self.patch_table.rowCount())]
        self.project["couplings"] = [{"edge_id": int(cell(self.coupling_table, r, 0)),
            "state": cell(self.coupling_table, r, 2), "origin": cell(self.coupling_table, r, 3),
            "penalty": float(cell(self.coupling_table, r, 5)),
            "rotation": bool(self.coupling_table.cellWidget(r, 4).currentData())}
            for r in range(self.coupling_table.rowCount())]
        # The unit system is only changed through convert_units(); never relabel values here.
        self.project["solver"] = {"type": self.solver_type.currentData(), "analysis_type": self.analysis_type.currentData(),
            **{k: float(getattr(self, k).text()) for k in ("start_time", "end_time", "time_step")}}
        self.project["output"] = {"vtk": self.vtk.isChecked(), "refinement": self.output_refinement.value()}
        if self.project != before:
            # Any committed assignment edit invalidates the initialized report of the
            # previously exported case; it must never be shown as if it were current.
            self.clear_run_report()

    def _update_unit_status(self):
        current = self.project["units"]
        target = self.units.currentText()
        pending = "" if target == current else f"   |   Selected target: {target} (NOT applied)"
        self.current_units.setText(f"Current project units: {current}{pending}")

    def _pending_condition_edit(self):
        if self.editing:
            return True
        return any(getattr(self, category+"_targets")
                   for category in ("supports", "loads", "initial_conditions"))

    def _reset_condition_editor(self, category):
        setattr(self, category+"_targets", [])
        getattr(self, category+"_target").clear()
        titles = {"supports": "Support", "loads": "Loads", "initial_conditions": "Initial"}
        getattr(self, category+"_name").setText(titles[category]+"_1")
        if category == "supports":
            self.support_vector.set([None, None, None])
            self.supports_interval[0].setText("0.0")
            self.supports_interval[1].setText("End")
        elif category == "loads":
            self.load_vector.set([0.0, 0.0, 0.0])
            self.loads_interval[0].setText("0.0")
            self.loads_interval[1].setText("End")
            self.load_pressure.setText("0.0")
            self.load_normal_flip.setChecked(False)
            self.load_kind.setCurrentIndex(0)
            self.load_amplitude_enabled.setChecked(False)
            self.load_amplitude_table.setRowCount(0)
            self.update_load_kind()
        else:
            self.initial_vector.set([None, None, None])

    def _reset_condition_editors(self):
        for category in ("supports", "loads", "initial_conditions"):
            self.editing.pop(category, None)
            self._reset_condition_editor(category)

    def _clear_condition_selection(self, category):
        setattr(self, category+"_targets", [])
        getattr(self, category+"_target").clear()

    def new_condition(self, category):
        """Discard the in-progress edit and its captured selection ('New' button)."""
        self.editing.pop(category, None)
        self._reset_condition_editor(category)
        self.status.setText(f"Cleared the {category} editor; captured selection discarded.")

    def _pending_condition_edit(self):
        if self.editing:
            return True
        return any(getattr(self, category+"_targets")
                   for category in ("supports", "loads", "initial_conditions"))

    def convert_units(self):
        target = self.units.currentText()
        if target == self.project["units"]:
            self.status.setText(f"The project already uses {target}.")
            return
        try:
            if self._pending_condition_edit():
                raise ValueError(
                    "Apply or discard the boundary condition you are editing (Add/Update or New) "
                    "before converting units.")
            self.sync(allow_pending_units=True)
            source = self.project["units"]
            scale = unit_scale(source, target)
            # Validate every candidate (including the editor penalty) before mutating UI state.
            converted = convert_project_units(self.project, target)
            new_penalty = float(self.penalty.text()) * scale["stress"]
            if not math.isfinite(new_penalty):
                raise ValueError("The penalty factor field overflows the target unit system.")
        except Exception:
            self.units.setCurrentText(self.project["units"])
            raise
        # Commit only after every value parsed and converted successfully.
        self.project = converted
        self.penalty.setText(repr(new_penalty))
        self._reset_condition_editors()
        self.refresh()
        self.status.setText(f"Converted all physical values from the previous unit system to {target}.")

    def add_material(self):
        ids = [int(self.material_table.item(r, 0).text()) for r in range(self.material_table.rowCount())]
        scale = unit_scale("mm-N-s", self.project["units"])
        self.table_row(self.material_table, [max(ids, default=0)+1, "Material",
                                             210000.0 * scale["stress"], 0.3, 7.85e-9 * scale["density"]])

    def add_section(self):
        ids = [int(self.section_table.item(r, 0).text()) for r in range(self.section_table.rowCount())]
        scale = unit_scale("mm-N-s", self.project["units"])
        self.table_row(self.section_table, [max(ids, default=0)+1, "Section", 1.0 * scale["length"]])

    def delete_row(self, table):
        if table.currentRow() >= 0:
            table.removeRow(table.currentRow())

    def select_table_geometry(self):
        if not self.doc:
            return
        Gui.Selection.clearSelection()
        for row in sorted({i.row() for i in self.geometry_table.selectedItems()}):
            key = self.geometry_rows[row]
            Gui.Selection.addSelection(self.doc.Name, self.entities[key])

    def selections(self):
        result = []
        for selection in Gui.Selection.getSelectionEx():
            obj = selection.Object
            if not self.doc or obj.Document.Name != self.doc.Name or not hasattr(obj, "IbraBrepId"):
                continue
            kind, ident = obj.IbraKind, obj.IbraBrepId
            target = {"target_kind": kind, "target_id": ident}
            if kind == "face" and selection.SubElementNames:
                subs = selection.SubElementNames
                if len(subs) != 1:
                    raise ValueError("Assign subedges or vertices of a patch individually, or select independent boundary entities in the geometry table.")
                sub = obj.Shape.getElement(subs[0])
                if subs[0].startswith("Vertex"):
                    face = next(f for f in self.index["faces"] if f["id"] == ident)
                    matches = [side for side, xyz in face["corners"].items() if (sub.Point-App.Vector(*xyz)).Length < 1e-6]
                    if len(matches) != 1:
                        raise ValueError("The vertex does not uniquely match a natural NURBS corner. Strong enforcement at general trimming vertices is not supported.")
                    target["side"] = matches[0]
                elif subs[0].startswith("Edge"):
                    matches = []
                    for edge in self.index["edges"]:
                        if edge["kind"] != "edge" or edge["face_ids"] != [ident]:
                            continue
                        points = edge["points"]
                        if all(sub.distToShape(Part.Vertex(App.Vector(*p)))[0] < 1e-5 for p in points) and abs(sub.Length-sum((App.Vector(*b)-App.Vector(*a)).Length for a, b in zip(points, points[1:]))) < max(1e-4, sub.Length*.01):
                            matches.append(edge["id"])
                    if len(matches) != 1:
                        raise ValueError("Boundary matching is ambiguous. Select the boundary by its B-rep ID in the geometry table.")
                    target = {"target_kind": "edge", "target_id": matches[0]}
            if target not in result:
                result.append(target)
        return result

    def capture(self, category):
        def operation():
            self.clear_run_report()
            targets = self.selections()
            if not targets:
                raise ValueError("Select an analysis patch or a one-sided boundary.")
            if any(t["target_kind"] == "coupling" for t in targets):
                raise ValueError("Define interface conditions in Interfaces. For supports or loads, select a one-sided patch boundary.")
            if category == "initial_conditions" and any(t["target_kind"] != "face" for t in targets):
                raise ValueError("Select a patch for initial conditions.")
            setattr(self, category+"_targets", targets)
            getattr(self, category+"_target").setText("; ".join(f"{t['target_kind']} {t['target_id']}"+(" · corner "+t["side"] if "side" in t else "") for t in targets))
            if category == "supports" and len(targets) == 1 and "side" in targets[0]:
                self.support_side.setCurrentIndex(self.support_side.findData(targets[0]["side"]))
        self.guard(operation)

    def add_condition(self, category):
        def operation():
            name = getattr(self, category+"_name").text().strip()
            if not name:
                raise ValueError("A condition name is required.")
            combo = getattr(self, category+"_group", None)
            group_id = combo.currentData() if combo is not None else None
            base = {}
            if category == "supports":
                base.update({"method": "strong" if self.support_method.currentIndex() == 0 else "penalty",
                             "value": self.support_vector.get(), "penalty": float(self.penalty.text()),
                             "clamp": self.clamp.isChecked()})
            elif category == "loads":
                # The amplitude table is optional and available for BOTH kinds.
                amplitude = self._amplitude_records()
                if self.load_kind.currentData() == "reference_pressure":
                    base.update({"kind": "reference_pressure", "pressure": self._pressure_value(),
                                 "normal_flip": bool(self.load_normal_flip.isChecked())})
                else:
                    base.update({"variable": self.load_variable.currentText(),
                                 "value": self.load_vector.get()})
                if amplitude is not None:
                    base["amplitude"] = amplitude
            else:
                base.update({"variable": self.initial_variable.currentText(), "value": self.initial_vector.get()})
            if category != "initial_conditions":
                a, b = getattr(self, category+"_interval")
                base["interval"] = [float(a.text()), "End" if b.text().strip() == "End" else float(b.text())]
            if group_id is not None:
                if category == "initial_conditions":
                    raise ValueError("Named groups are not supported for initial fields.")
                records = [dict(base, name=name, group_id=int(group_id))]
            else:
                targets = getattr(self, category+"_targets")
                if not targets:
                    raise ValueError("Assign selected entities or choose a named group first.")
                records = []
                for index, target in enumerate(targets):
                    record = dict(base, name=name if len(targets) == 1 else name+"_"+str(index+1))
                    record.update(target)
                    if category == "supports":
                        record["side"] = target.get("side", self.support_side.currentData())
                    records.append(record)
            if category == "loads" and base.get("kind") == "reference_pressure":
                # Validate the target before any mutation: pressure is face-only and never
                # a corner/side, so an edge selection is rejected atomically.
                for record in records:
                    if "group_id" in record:
                        group = next((g for g in self.project.get("groups", [])
                                      if g.get("id") == record["group_id"]), None)
                        if group is None or group.get("kind") != "face":
                            raise ValueError("Reference pressure requires a face group or a face "
                                             "target; edge targets are not supported.")
                    elif record.get("target_kind") != "face":
                        raise ValueError("Reference pressure requires a face target; select a patch "
                                         "face or a face group, not an edge.")
                    if "side" in record:
                        raise ValueError("Reference pressure applies to the whole face, not a "
                                         "corner or natural side.")
            if category == "loads":
                # Record-local validation with the shared backend helpers, BEFORE any
                # pop/extend: a bad Add/Update leaves the project and the edit state intact.
                for record in records:
                    error = load_kind_error(record)
                    if error:
                        raise ValueError(error)
                    for message in amplitude_errors(record, self.project["solver"]):
                        raise ValueError(message)
            if category in self.editing:
                if len(records) != 1:
                    raise ValueError("Assign one target entity when editing a single condition.")
                self.project[category][self.editing.pop(category)] = records[0]
            else:
                self.project[category].extend(records)
            # The selection is committed; clear it so it cannot block a later unit conversion.
            self._clear_condition_selection(category)
            self.refresh_conditions(category)
            self.status.setText("Condition recorded | Geometric and formulation compatibility will be validated before export.")
        self.guard(operation)

    def _condition_label(self, record):
        if "group_id" in record:
            group = next((g for g in self.project.get("groups", []) if g.get("id") == record["group_id"]), None)
            if group is None:
                return f"group {record['group_id']} (missing)"
            expanded = expand_groups([group], self.index).get(group["id"], {})
            missing = expanded.get("missing") or []
            return (f"group {group['id']} \"{group.get('name')}\" → {len(expanded.get('members', []))} members"
                    + (f" (missing {missing})" if missing else ""))
        return f"{record.get('target_kind')} {record.get('target_id')}" + \
            (f" · corner {record['side']}" if record.get("side") else "")

    def _condition_kind_label(self, record):
        if record.get("kind") == "reference_pressure":
            return "reference_pressure"
        return str(record.get("method", record.get("variable", "?")))

    def _condition_value_label(self, record):
        amplitude = record.get("amplitude")
        points = amplitude.get("points") if isinstance(amplitude, dict) else None
        table = f" · table {len(points)} pts" if points else ""
        if record.get("kind") == "reference_pressure":
            return (f"p={record.get('pressure')} · flip={bool(record.get('normal_flip'))}"
                    f"{table or ' · constant 1'}")
        return f"{record.get('value')}{table}"

    def refresh_conditions(self, category):
        self.clear_run_report()
        items = getattr(self, category+"_list")
        items.clear()
        for r in self.project[category]:
            pending = " · PENDING REBIND" if r.get("needs_rebind") else ""
            items.addItem(f"{r['name']}  ·  {self._condition_label(r)}  ·  "
                          f"{self._condition_kind_label(r)}  {self._condition_value_label(r)}{pending}")

    def delete_condition(self, category):
        row = getattr(self, category+"_list").currentRow()
        if row >= 0:
            self.project[category].pop(row)
            self.editing.pop(category, None)
            self.refresh_conditions(category)

    def edit_condition(self, category, row):
        self.clear_run_report()
        record = self.project[category][row]
        self.editing[category] = row
        getattr(self, category+"_name").setText(record["name"])
        combo = getattr(self, category+"_group", None)
        if combo is not None:
            position = combo.findData(record.get("group_id"))
            combo.setCurrentIndex(position if position >= 0 else 0)
        if "group_id" in record:
            setattr(self, category+"_targets", [])
            getattr(self, category+"_target").setText(self._condition_label(record))
        else:
            setattr(self, category+"_targets", [{k: record[k] for k in ("target_kind", "target_id")}])
            getattr(self, category+"_target").setText(f"{record['target_kind']} {record['target_id']}")
        if category == "supports":
            self.support_method.setCurrentIndex(0 if record["method"] == "strong" else 1)
            self.support_side.setCurrentIndex(self.support_side.findData(record.get("side", "u0")))
            self.support_vector.set(record["value"])
            self.penalty.setText(str(record["penalty"]))
            self.clamp.setChecked(record.get("clamp", False))
        elif category == "loads":
            if record.get("kind") == "reference_pressure":
                self.load_kind.setCurrentIndex(self.load_kind.findData("reference_pressure"))
                self.load_pressure.setText(str(record.get("pressure", 0.0)))
                self.load_normal_flip.setChecked(bool(record.get("normal_flip")))
                self._set_amplitude_table(record.get("amplitude"))
                self.load_vector.set([0.0, 0.0, 0.0])
            else:
                self.load_kind.setCurrentIndex(self.load_kind.findData("vector"))
                self.load_variable.setCurrentText(record.get("variable", "SURFACE_LOAD"))
                self.load_vector.set(record.get("value", [0.0, 0.0, 0.0]))
                self.load_pressure.setText("0.0")
                self.load_normal_flip.setChecked(False)
                self._set_amplitude_table(record.get("amplitude"))
            self.update_load_kind()
        else:
            self.initial_variable.setCurrentText(record["variable"])
            self.initial_vector.set(record["value"])
        if category != "initial_conditions":
            for widget, value in zip(getattr(self, category+"_interval"), record["interval"]):
                widget.setText(str(value))

    def _project_document(self, *, active_first=False):
        active = App.ActiveDocument
        if active_first and active is not None and doc_owner.find_owner(active) is not None:
            return active
        doc = self.doc if self.doc and self.doc.Name in App.listDocuments() else None
        if doc is not None and doc_owner.find_owner(doc) is not None:
            return doc
        candidates = [d for d in App.listDocuments().values() if doc_owner.find_owner(d) is not None]
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise ValueError("No FreeCAD document carries an IBRA project. Use Open JSON, or import a STEP "
                             "to associate one.")
        raise ValueError("Several documents carry an IBRA project; open the intended document and retry.")

    def _entity_association(self):
        if not self.index or not self.entities:
            return []
        return doc_owner.entity_map_from_index(self.index, self.entities)

    def _committed_payload(self, doc):
        """Runs inside the native save, before serialization.

        A stale hook from another document/panel must never serialize this panel's
        project into that document, so the document identity is asserted first.
        """
        if doc is not self.doc:
            raise ValueError("a native-save observer for a different document is still installed")
        if self.process:
            raise ValueError("a background task is still running")
        if self._pending_condition_edit():
            raise ValueError("an uncommitted condition edit is open; press Add / Update or New first")
        self.sync()
        return {"project": self.project,
                "source_step": self.project.get("source_step", ""),
                "source_sha256": self.project.get("source_sha256", ""),
                "geometry_sha256": self.project.get("geometry_sha256", ""),
                "cache_dir": str(self.cache) if self.cache else ""}

    def attach_to_document(self, strict=False):
        if not self.doc or self.doc.Name not in App.listDocuments():
            if strict:
                raise ValueError("No FreeCAD document is available to associate.")
            return False
        state = doc_owner.read(self.doc)
        payload = {"project": self.project,
                   "source_step": self.project.get("source_step", ""),
                   "source_sha256": self.project.get("source_sha256", ""),
                   "geometry_sha256": self.project.get("geometry_sha256", ""),
                   "cache_dir": str(self.cache) if self.cache else ""}
        if not state.entity_map:
            # First association only: capture the entity map and its reference BReps.
            # Later saves must never rewrite that baseline.
            payload["entity_map"] = self._entity_association()
        try:
            doc_owner.upsert(self.doc, **payload)
        except Exception as exc:  # noqa: BLE001 - surfaced in the status line
            if strict:
                raise ValueError(f"Document association failed: {exc}") from exc
            self.status.setText(f"Document association failed: {exc}")
            return False
        hook = doc_owner.installed_hook(self.doc)
        if hook is None:
            doc_owner.install_save_observer(self.doc, self._committed_payload, on_error="flag")
        elif hook.provider is not self._committed_payload:
            doc_owner.remove_save_observer(hook)
            doc_owner.install_save_observer(self.doc, self._committed_payload, on_error="flag")
        return True

    def _reset_panel_context(self, doc, cache, index, project, entity_map):
        """Adopt a restored/recovered document context, clearing stale runtime state.

        Mirrors ``import_finished``: no stale export target, no stale editing or
        target selection, and display fingerprints only for the entities that exist
        now. Refuses while an uncommitted condition edit is open instead of dropping
        it silently.
        """
        if self._pending_condition_edit():
            raise ValueError("Commit or cancel the open condition edit before switching documents.")
        self.clear_preview(doc)
        # Switching the panel onto another document drops the previous result display and
        # restores the CAD visibility it had changed.
        self.clear_result_display(doc, full=True)
        if self.doc is not None and self.doc is not doc:
            self.detach_from_document(self.doc)
        self.doc = doc
        self.project = project
        self.cache = cache
        self.import_cache = cache
        self.cad = None
        self._cad_cache_signature = None
        self.index = index
        self.entities = {(entry.get("kind"), entry.get("brep_id")): entry.get("object")
                         for entry in entity_map if entry.get("object")}
        self.display_fingerprints = {}
        for name in self.entities.values():
            obj = doc.getObject(name)
            if obj is not None:
                self.display_fingerprints[name] = (obj.Shape.hashCode(), str(obj.Placement))
        self.last_export = None
        self.last_run = None
        self.clear_run_report()
        self.refresh_result_run_dirs()
        self._refresh_result_evidence()
        self.editing.clear()
        for category in ("supports", "loads", "initial_conditions"):
            setattr(self, category+"_targets", [])
            target = getattr(self, category+"_target", None)
            if target is not None:
                target.clear()

    def refresh_group_choosers(self):
        """Rebuild the condition group dropdowns, preserving the current selection.

        Called after create/rename/remove/changed members so a new group is
        immediately selectable without a full refresh (which would discard
        uncommitted table values).
        """
        for category in ("supports", "loads", "initial_conditions"):
            combo = getattr(self, category+"_group", None)
            if combo is None:
                continue
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(direct selection)", None)
            for group in self.project.get("groups", []):
                combo.addItem(f"{group.get('id')}: {group.get('name')} ({group.get('kind')})",
                              group.get("id"))
            position = combo.findData(current)
            combo.setCurrentIndex(position if position >= 0 else 0)
            combo.blockSignals(False)

    def refresh_group_views(self):
        """Refresh only group-related widgets and the condition labels."""
        self.refresh_groups()
        self.refresh_group_choosers()
        for category in ("supports", "loads", "initial_conditions"):
            self.refresh_conditions(category)

    def detach_from_document(self, doc=None):
        target = doc if doc is not None else self.doc
        if target is None:
            return
        hook = doc_owner.installed_hook(target)
        if hook is not None:
            doc_owner.remove_save_observer(hook)

    def save_to_document(self):
        def operation():
            if self.process:
                raise ValueError("A background task is already running; wait for it before saving.")
            # Save only the document this panel owns. Never copy this project into
            # whichever document happens to be active.
            doc = self.doc if self.doc and self.doc.Name in App.listDocuments() else None
            if doc is None or doc_owner.find_owner(doc) is None:
                raise ValueError("This panel is not associated with a FreeCAD document. "
                                 "Use Restore Document first.")
            active = App.ActiveDocument
            if active is not None and active is not doc:
                raise ValueError(f"The active document is '{active.Name}', but this panel owns '{doc.Name}'. "
                                 "Switch to the panel's document or use Restore Document before saving.")
            self.sync()  # commit the displayed edits before writing the document
            self.detach_from_document()
            self.detach_from_document(doc)
            self.doc = doc
            self.attach_to_document()
            # Real block path: commit in the caller's stack, then let the native save
            # serialize exactly what was committed.
            doc_owner.commit_before_save(doc, self._committed_payload)
            if doc.FileName:
                doc.save()
            else:
                path, _ = W.QFileDialog.getSaveFileName(self, "Save FreeCAD Document",
                                                        str(self.work_dir/"ibra.FCStd"),
                                                        "FreeCAD Document (*.FCStd)")
                if not path:
                    return
                doc.saveAs(path)
            state = doc_owner.read(doc)
            self.status.setText("Project written into the FreeCAD document."
                                + (f" Sync error: {state.sync_error}" if state.sync_error else ""))
        self.guard(operation)

    def _mark_all_pending(self, project, reason):
        for category in ("supports", "loads", "initial_conditions"):
            for record in project.get(category, []):
                record["needs_rebind"] = reason
        for group in project.get("groups", []):
            if isinstance(group, dict):
                group["needs_rebind"] = reason
        # Even with zero conditions and groups, a raw changed shape must not be
        # silently re-exported from the previous cache.
        project["geometry_needs_rebind"] = reason

    def restore_document(self):
        def operation():
            if self.process:
                raise ValueError("A background task is already running; wait for it before restoring.")
            if self.snapshot:
                raise ValueError("A controlled snapshot is pending; apply or cancel it before restoring.")
            # Refuse BEFORE any mutation: a rejected restore must not touch the current
            # document, project, cache, entities, hook or export target.
            if self._pending_condition_edit():
                raise ValueError("Commit or cancel the open condition edit before restoring.")
            doc = self._project_document(active_first=True)
            state = doc_owner.read(doc)
            if state.project is None:
                raise ValueError("; ".join(state.diagnostics) or "The document stores no project.")
            blocking = [d for d in state.diagnostics
                        if "corrupt" in d or "missing" in d or "could not commit" in d]
            if blocking:
                raise ValueError("Restore blocked by document diagnostics: " + "; ".join(blocking))
            if not state.source_step:
                raise ValueError("The document stores no source STEP; reimport the geometry first.")
            diagnostics = doc_owner.check_entities(doc)
            # Everything below is staged locally; only _reset_panel_context performs the
            # atomic switch (it detaches the old hook and assigns doc/project/cache/...).
            index = None
            cache = Path(state.cache_dir) if state.cache_dir else None
            if cache is not None and (cache / "index.json").is_file():
                try:
                    candidate = read_json(cache / "index.json")
                except Exception:  # noqa: BLE001 - unreadable cache -> rebuild path
                    candidate = None
                if (isinstance(candidate, dict)
                        and candidate.get("source_sha256") == state.project.get("source_sha256")
                        and candidate.get("geometry_sha256") == state.project.get("geometry_sha256")):
                    index = candidate
            if index is not None:
                staged_project = deepcopy(state.project)
                if diagnostics:
                    self._mark_all_pending(staged_project,
                                           "the stored geometry no longer matches; rebind explicitly")
                self._reset_panel_context(doc, cache, index, staged_project, state.entity_map)
                self.refresh()
                self.attach_to_document()
                self.status.setText("Document restored from its saved cache; existing objects, groups and the "
                                    "entity association were reused."
                                    + (" Some bindings are pending rebind." if diagnostics else ""))
                return
            staged_project = deepcopy(state.project)
            if diagnostics:
                self._mark_all_pending(staged_project, "the stored geometry no longer matches; rebind explicitly")
            # Rebuild mode: keep the current context until the import callback succeeds.
            self.reuse_target_document = doc
            self.pending_project = staged_project
            self.status.setText("Restoring in rebuild mode; the intended document is reused and modified shapes "
                                "are never overwritten.")
            self.start_import(state.source_step)
        self.guard(operation)

    def check_project(self):
        def operation():
            self.sync()
            errors, warnings = validate(self.project, self.index)
            self.log.appendPlainText("\n".join(["Error: "+e for e in errors]+["Notice: "+w for w in warnings]))
            self.status.setText(f"Errors: {len(errors)} | Notices: {len(warnings)}")
            # The static-validation evidence is the actual result of this validate() run.
            self.result_state.validate_result = not errors
            self._refresh_result_evidence()
            if errors:
                self.log.show()
                self.log_toggle.setText("Hide Log")
            return not errors
        return self.guard(operation)

    def save_project(self):
        def operation():
            if self.process:
                raise ValueError("A background task is already running; wait for it before saving.")
            self.sync()
            path, _ = W.QFileDialog.getSaveFileName(self, "Save Analysis Project", str(self.work_dir/"project.ibra-project.json"), "IBRA Project (*.ibra-project.json);;Project JSON (*.json)")
            if path:
                write_project(path, self.project)
                self.status.setText("Project saved: "+path)
        self.guard(operation)

    def open_project(self):
        def operation():
            if self.process:
                raise ValueError("A background task is already running; wait for it before opening a project.")
            if self.snapshot:
                raise ValueError("A controlled snapshot is pending; apply or cancel it before opening another "
                                 "project.")
            path, _ = W.QFileDialog.getOpenFileName(self, "Open Analysis Project", str(self.work_dir), "IBRA Project (*.ibra-project.json);;Project JSON (*.json)")
            if not path:
                return
            self.load_project(path)
        self.guard(operation)

    def load_project(self, path):
        if self.process:
            raise ValueError("A background task is already running; wait for it before opening a project.")
        if self.snapshot:
            raise ValueError("A controlled snapshot is pending; apply or cancel it before opening a project.")
        project = read_project(path)  # read_project migrates legacy schema 1 in memory.
        if project.get("schema_version") != SCHEMA:
            raise ValueError("Unsupported project schema version.")
        if hashlib.sha256(Path(project["source_step"]).read_bytes()).hexdigest() != project["source_sha256"]:
            raise ValueError("The STEP checksum has changed; previous boundary conditions cannot be reused.")
        self.pending_project = project
        self.profile.setText(project.get("profile", ""))
        self.start_import(project["source_step"])

    def save_backend_configuration(self):
        if self.process:
            raise ValueError("Wait for the current background task to finish.")
        self.settings = save_settings(self.user_data, {
            "backend_python": self.backend_python.text().strip(),
            "pipeline_root": self.pipeline_root.text().strip(),
            "work_dir": self.work_directory.text().strip()})
        self.work_dir = Path(self.settings["work_dir"])
        self.status.setText("Backend configuration saved.")

    def check_backend_environment(self):
        self.save_backend_configuration()
        self.log.show()
        self.log_toggle.setText("Hide Log")
        self.start_process([str(BASE/"backend/check_environment.py")],
                           lambda: self.status.setText("Backend environment verified."))

    def export_case(self):
        if not self.check_project():
            return
        folder = W.QFileDialog.getExistingDirectory(self, "Select Parent Directory for a New Analysis Model", str(Path.home()))
        if not folder:
            return
        destination = Path(folder)/("ibra_case_"+uuid.uuid4().hex[:8])
        project_file = self.cache/("project_"+uuid.uuid4().hex+".json")
        write_json(project_file, self.project)
        def done():
            self.last_export = destination
            # A different case: no run directory belongs to it yet.
            self.last_run = None
            self.clear_result_display(full=True)
            self.refresh_result_run_dirs()
            self.status.setText("Analysis model exported: "+str(destination))
        self.guard(lambda: self.start_worker({"action": "export", "cache_dir": str(self.cache), "project_file": str(project_file), "destination": str(destination)}, done))

    def export_geometry(self):
        def operation():
            if not self.cache:
                raise ValueError("Import STEP geometry first.")
            path, _ = W.QFileDialog.getSaveFileName(self, "Export Source CAD Geometry (mm)", str(Path.home()/"geometry.cad.json"), "CAD JSON (*.cad.json)")
            if path:
                write_json(path, read_json(self.cache/"geometry.cad.json"))
                self.status.setText("CAD geometry exported (mm; analysis settings excluded): "+path)
        self.guard(operation)

    def run_case(self, check):
        def operation():
            if not self.last_export:
                raise ValueError("Export a complete analysis model first.")
            # Every launch gets its own isolated run directory beside the read-only
            # export: the ten exported inputs are copied, no earlier run directory and no
            # previous results.vtkhdf/run_report.json is ever consulted, and nothing in
            # the export or in an existing run directory is deleted or overwritten.
            self.clear_run_report()
            # A new launch invalidates the previous result display; its run directory is not
            # this run's and must never stay on screen as if it were.
            self.clear_result_display(full=True)
            self.last_run = None
            created = run_dirs.create_run_dir(Path(self.last_export))
            self.last_run = created["run_dir"]
            self.refresh_result_run_dirs()
            arguments = [str(created["run_dir"]/"MainKratos.py")]+(["--check"] if check else [])
            def done():
                self.status.setText(("Model initialization completed: " if check else
                                     "Analysis completed: ")+f"{created['run_id']} · "
                                     f"{created['run_dir']}")
            self.start_process(arguments, done,
                               completion=self._refresh_run_report_and_results)
        self.guard(operation)


def show_panel():
    global _panel
    if _panel is None:
        _panel = Panel(Gui.getMainWindow())
        Gui.getMainWindow().addDockWidget(QtCore.Qt.RightDockWidgetArea, _panel)
    _panel.show()
    _panel.raise_()
    return _panel


class OpenCommand:
    def GetResources(self):
        return {"MenuText": "IBRA Preprocessor", "ToolTip": "Open the IBRA preprocessor for Kratos", "Pixmap": str(BASE/"Resources"/"iga.svg")}

    def IsActive(self):
        return True

    def Activated(self):
        show_panel()
