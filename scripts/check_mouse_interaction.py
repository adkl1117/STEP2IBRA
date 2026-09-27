"""V0.5 mouse-interaction host runner (isolated native FreeCAD GUI).

Isolation follows the accepted P0-5/P0-7 runners (``scripts/check_p07_gui_cases.py``):
the repository plugin tree is copied into ``--out``, FreeCAD's bundled Python is
launched with a normal Qt platform, the window is kept on-screen (real QtTest input is
dropped for an off-screen window), and no installed workbench copy or user configuration
is touched.

Host mode drives two bounded (45 s) child processes of ``tests/gui_mouse_interaction_probe.py``:

  interaction : real STEP conversion -> real ``Gui.Selection`` observer (left select,
                refusals, hover) -> the installed pivy ``SoEvent`` callback plus a
                native Qt event filter on the live ``Gui::View3DInventorViewer`` that
                owns only the active ``MiddleButton`` press+release (Qt middle == Coin
                BUTTON3; right == BUTTON2 is never consumed), with a deferred fresh
                Coin ray fallback for a thin coupling edge whose left click the
                Selection observer did not accept -> coupling table highlight, right
                type/penalty exposure, the existing decision entry point, reversible
                coupling-only visibility (visibility + selectability), page-switch and
                workbench-switch cleanup -> a real ``save_to_document`` ``.FCStd`` after
                discarding test-only editor drafts.
  persistence : fresh process -> ``App.openDocument`` -> ``panel.restore_document`` ->
                the mouse-confirmed decision and the initial record must survive.

Synthesized events are labelled in the child payload (``input: "synthesized-qt-test"``);
no physical mouse input is claimed, and the runner never claims visual 3D criteria beyond
the state it asserts.

    STEP2IBRA/.venv/Scripts/python.exe scripts/check_mouse_interaction.py \
        --out runtime/v0_5-mouse-review/gui-mouse
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                                        # STEP2IBRA-publish
WORKSPACE = ROOT.parent
FREECAD_PY = WORKSPACE / "FreeCAD/FreeCAD_1.1.3-Windows-x86_64-py311/bin/python.exe"
BACKEND_PY = WORKSPACE / "STEP2IBRA/.venv/Scripts/python.exe"
PROJECT = "examples/two_patch/two_patch_shell.ibra-project.json"
PROBE = ROOT / "tests/gui_mouse_interaction_probe.py"
PHASES = ("interaction", "persistence")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", help="New or empty output directory (host mode).")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--phase", choices=PHASES, help="Internal child mode.")
    parser.add_argument("--plugin", default="")
    parser.add_argument("--user-dir", default="")
    parser.add_argument("--document", default="")
    parser.add_argument("--project", default="")
    parser.add_argument("--result", default="")
    args = parser.parse_args()

    if not args.out:
        parser.error("--out is required in host mode")
    if not FREECAD_PY.is_file() or not BACKEND_PY.is_file():
        print(json.dumps({"host_ok": False,
                          "error": f"missing interpreter: {FREECAD_PY} / {BACKEND_PY}"}))
        return 2
    if not PROBE.is_file():
        print(json.dumps({"host_ok": False, "error": f"missing probe: {PROBE}"}))
        return 2

    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    plugin = out / "Mod" / "KratosIBRA"
    plugin.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "freecad/Mod/KratosIBRA", plugin)
    user = out / "user"
    user.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["IBRA_BACKEND_PYTHON"] = str(BACKEND_PY)
    env["IBRA_PIPELINE_ROOT"] = str(plugin / "backend")
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("QT_QPA_PLATFORM", None)

    document = out / "two_patch_mouse.FCStd"
    phases, ok = {}, True
    for phase in PHASES:
        result_file = out / f"phase_{phase}.json"
        if result_file.exists():
            result_file.unlink()
        command = [str(FREECAD_PY), str(PROBE), "--phase", phase, "--plugin", str(plugin),
                   "--user-dir", str(user), "--document", str(document),
                   "--project", str(plugin / PROJECT), "--result", str(result_file)]
        try:
            child = subprocess.run(command, env=env, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=args.timeout)
            exit_code, tail = child.returncode, ((child.stdout or "")
                                                 + (child.stderr or ""))[-1200:]
        except subprocess.TimeoutExpired as expired:
            exit_code = None
            stdout = expired.stdout or expired.output or ""
            stderr = expired.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", "replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", "replace")
            tail = (f"timeout after {args.timeout:g}s; stdout/stderr tail:\n"
                    + (stdout + stderr)[-1200:])
        payload = (json.loads(result_file.read_text(encoding="utf-8"))
                   if result_file.is_file() else {})
        trace_file = result_file.with_suffix(result_file.suffix + ".trace")
        trace = (trace_file.read_text(encoding="utf-8").splitlines()[-40:]
                 if trace_file.is_file() else [])
        started = result_file.is_file()
        phase_ok = exit_code == 0 and payload.get("passed") is True
        ok = ok and phase_ok
        phases[phase] = {
            "exit": exit_code, "passed": payload.get("passed"), "state": payload.get("state"),
            "child_started": started, "startup_trace": trace,
            "failed_checks": [c for c in payload.get("checks", []) if not c["ok"]],
            "error": payload.get("error"), "traceback": payload.get("traceback"),
            "tail": tail, "callback_mode": payload.get("callback_mode"),
            "qt_widget": payload.get("qt_widget"),
            "widget_candidates": payload.get("widget_candidates"),
            "widget_size": payload.get("widget_size"),
            "device_pixel_ratio": payload.get("device_pixel_ratio"),
            "view_size": payload.get("view_size"),
            "native_coin_last_event": payload.get("native_coin_last_event"),
            "native_coin_middle_event": payload.get("native_coin_middle_event"),
            "native_selection_subelements": payload.get("native_selection_subelements"),
            "middle_events": payload.get("middle_events"),
            "middle_events_count": payload.get("middle_events_count"),
            "middle_events_before_confirm": payload.get("middle_events_before_confirm"),
            "middle_events_before_confirm_count": payload.get("middle_events_before_confirm_count"),
            "middle_events_after_initial": payload.get("middle_events_after_initial"),
            "last_pick_after_confirm": payload.get("last_pick_after_confirm"),
            "destructive_middle_events": payload.get("destructive_middle_events"),
            "destructive_last_pick": payload.get("destructive_last_pick"),
            "click_guards": payload.get("click_guards"),
            "hidden_builtin_docks": payload.get("hidden_builtin_docks"),
            "screen_geometry": payload.get("screen_geometry"),
            "main_geometry": payload.get("main_geometry"),
            "panel_geometry": payload.get("panel_geometry"),
            "panel_floating": payload.get("panel_floating"),
            "filter_generation": payload.get("filter_generation"),
            "filter_history": payload.get("filter_history"),
            "doc_objects": payload.get("doc_objects"),
            "projected_hits": payload.get("projected_hits"),
            "projected_resolution": payload.get("projected_resolution"),
            "projected_fallback": payload.get("projected_fallback"),
            "edge_pixel_choice": payload.get("edge_pixel_choice"),
            "edge_requested_local": payload.get("edge_requested_local"),
            "coupling_renderer_info": payload.get("coupling_renderer_info"),
            "coupling_renderer_info_after_image": payload.get("coupling_renderer_info_after_image"),
            "coupling_event_counts_before": payload.get("coupling_event_counts_before"),
            "coupling_event_counts_after_left": payload.get("coupling_event_counts_after_left"),
            "coupling_event_counts_after": payload.get("coupling_event_counts_after"),
            "coupling_last_event_before": payload.get("coupling_last_event_before"),
            "coupling_last_event_after": payload.get("coupling_last_event_after"),
            "coupling_only_image": payload.get("coupling_only_image"),
            "pending_editor_before_save": payload.get("pending_editor_before_save"),
            "save_status": payload.get("save_status"),
            "save_log": payload.get("save_log"),
            "edge_left_fallback_attempts": payload.get("edge_left_fallback_attempts"),
            "edge_left_fallback_hits": payload.get("edge_left_fallback_hits"),
            "last_edge_left_fallback": payload.get("last_edge_left_fallback"),
            "edge_requested_coin": payload.get("edge_requested_coin"),
            "edge_actual_coin": payload.get("edge_actual_coin"),
            "edge_receiver_offset": payload.get("edge_receiver_offset"),
            "edge_drawable": payload.get("edge_drawable"),
            "input": payload.get("input"), "physical_input": payload.get("physical_input"),
            "pick_resolution": payload.get("pick_resolution")}
        if not phase_ok:
            break
    summary = {"host_ok": ok, "out": str(out), "plugin": str(plugin),
               "interpreter": str(FREECAD_PY), "timeout_s": args.timeout,
               "project": PROJECT, "phases": phases,
               "input": "synthesized-qt-test", "physical_input": False,
               "not_checked": ["physical mouse input", "visual 3D colour criteria",
                               "installed workbench copy", "Kratos solve"]}
    (out / "mouse_interaction.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                                encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
