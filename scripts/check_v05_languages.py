"""Native language checks in an isolated staged profile; no module pinning."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

import check_v05_preview_startup as startup

PROBE = r'''
def language_probe():
    from copy import deepcopy
    import importlib.util
    from kratos_iga import gui, i18n
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide import QtCore
    try:
        from PySide import QtWidgets as W
    except ImportError:
        from PySide import QtGui as W
    panel = gui.show_panel()
    phase = os.environ["IBRA_LANGUAGE_PHASE"]
    if phase == "restore":
        check("language_fresh_process_restore", panel.language == "zh", panel.language)
        check("language_fresh_process_button", any(
            button.text() == i18n.translate("top.open_project", "zh")
            for button in panel.findChildren(W.QPushButton)))
        return
    probe_path = Path(os.environ["IBRA_LANGUAGE_PROBE"])
    spec = importlib.util.spec_from_file_location("language_native_probe", str(probe_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.run(panel, OUT)
    notes["language_probe"] = result
    check("language_native_probe", isinstance(result, dict) and
          result.get("passed", result.get("ok", False)), result)
    model = deepcopy(panel.project)
    panel.supports_name.setText("custom 名字 EN")
    draft = panel.supports_name.text()
    for language in ("en", "zh", "en", "zh"):
        panel.language_selector.setCurrentIndex(panel.language_selector.findData(language))
        Gui.updateGui()
        check("language_selector_" + language, panel.language == language, panel.language)
        check("language_draft_" + language, panel.supports_name.text() == draft)
        check("language_model_" + language, panel.project == model)
        check("language_geometry_cell_" + language,
              panel.geometry_table.item(0, 0).text() == i18n.translate("interaction.kind.face", language))
        check("language_button_" + language, any(
            button.text() == i18n.translate("top.open_project", language)
            for button in panel.findChildren(W.QPushButton)))
        if language == "en":
            check("language_english_buttons_no_chinese", all(
                not any("\u4e00" <= char <= "\u9fff" for char in button.text())
                for button in panel.findChildren(W.QPushButton)))
        Gui.getMainWindow().grab().save(str(OUT / ("interface_" + language + ".png")))
    group = App.ParamGet("User parameter:" + i18n.PREFERENCE_GROUP)
    check("language_preference_written", group.GetString(i18n.PREFERENCE_KEY) == "zh")
    App.saveParameter()
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview-root", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    preview = Path(args.preview_root).resolve()
    out = Path(args.out).resolve()
    if out.exists():
        raise FileExistsError("Use a fresh output directory")
    if out == preview or preview in out.parents:
        raise ValueError("Output must be outside the user preview")
    stage = out / "stage"
    profile = stage / "profile"
    profile.mkdir(parents=True)
    shutil.copyfile(preview / startup.LAUNCHER_NAME, stage / startup.LAUNCHER_NAME)
    for name in ("user.cfg", "system.cfg"):
        shutil.copyfile(preview / "profile" / name, profile / name)
    target = profile / "Mod" / "KratosIBRA"
    shutil.copytree(startup.ROOT / "freecad/Mod/KratosIBRA", target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "backend_config.json"))
    existing = json.loads((preview / "profile/Mod/KratosIBRA/backend_config.json").read_text())
    existing.update(pipeline_root=str(target / "backend"), work_dir=str(profile / "IBRA-work"))
    (target / "backend_config.json").write_text(json.dumps(existing, indent=2), encoding="utf-8")
    startup.MACRO = startup.MACRO.replace("\ndef main():", PROBE + "\ndef main():")
    startup.MACRO = startup.MACRO.replace("        run()\n", "        run()\n        language_probe()\n")
    os.environ["IBRA_LANGUAGE_PROBE"] = str(startup.ROOT / "tests/gui_i18n_probe.py")
    original_argv = sys.argv
    results = []
    try:
        for phase in ("switch", "restore"):
            os.environ["IBRA_LANGUAGE_PHASE"] = phase
            phase_out = out / phase
            sys.argv = [original_argv[0], "--preview-root", str(stage), "--out", str(phase_out),
                        "--timeout", "45", "--two-patch"]
            code = startup.main()
            result = json.loads((phase_out / "v05_preview_startup.json").read_text(encoding="utf-8"))
            results.append({"phase": phase, "ok": code == 0, "checks": len(result["checks"]),
                            "failed": [c for c in result["checks"] if not c["ok"]],
                            "error": result["error"]})
            if code:
                break
            if phase == "switch":
                # Only the staged test profile is changed; the user profile is untouched.
                shutil.copyfile(phase_out / "config/user.cfg", profile / "user.cfg")
    finally:
        sys.argv = original_argv
    verdict = {"passed": len(results) == 2 and all(r["ok"] for r in results),
               "stage": str(stage), "phases": results}
    (out / "language_verdict.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
