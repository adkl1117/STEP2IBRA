"""Native FreeCAD probe for the selectable English/Chinese plugin interface.

The coordinator calls ``gui_i18n_probe.run(panel, out)`` from a real FreeCAD GUI
macro with an isolated user directory, after loading a two-patch / one-coupling
project; ``out`` is the result directory and ``native_language_probe.json`` is
written there.

Assertions use the panel's real public API and native widgets only: registered
Qt text and tooltips, ``QComboBox`` canonical ``itemData``, the ``Gui.Selection``
observer that drives the coupling machine, and a second real ``Panel`` instance
for the stored preference.  No path import, no monkeypatch, no mouse or pixel
claim; the first failed assertion is recorded and never reported as a pass.
"""
from __future__ import annotations

import json
import traceback
from copy import deepcopy
from pathlib import Path

RESULT_NAME = "native_language_probe.json"
STAGED_MARKER = ".ibra_probe_profile"   # coordinator's isolated test-profile marker
NAV_KEYS = ("nav.geometry", "nav.materials", "nav.groups", "nav.boundary",
            "nav.initial", "nav.interfaces", "nav.preview", "nav.analysis")


def _cjk(value):
    """True when the string carries at least one Chinese character."""
    return any("\u4e00" <= char <= "\u9fff" for char in (value or ""))


def _brief(value):
    text = repr(value)
    return text if len(text) <= 169 else text[:166] + "..."


def run(panel, out):
    """Drive the real panel through the language flow and return the verdict."""
    path = Path(out)
    path.mkdir(parents=True, exist_ok=True)
    path = path / RESULT_NAME
    started = panel.language
    payload = {"passed": False, "state": "running", "error": None, "checks": [],
               "output": str(path), "initial_language": panel.language,
               "final_language": None, "staged": None}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def check(label, condition, detail=""):
        payload["checks"].append({"check": label, "ok": bool(condition),
                                  "detail": str(detail)[:400]})
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        if not condition:
            raise AssertionError(f"{label}: {detail}")

    def text(widget):
        return widget.text() if widget is not None else None

    def items(combo):
        return [combo.itemData(i) for i in range(combo.count())]

    def texts(combo):
        return [combo.itemText(i) for i in range(combo.count())]

    def nav():
        return [panel.navigation.item(row).text() for row in range(panel.navigation.count())]

    def switches(sequence):
        return [panel.set_language(code) for code in sequence]

    def lookup(key, language):
        from kratos_iga import i18n
        return i18n.translate(key, language)

    def shows(widget, key, language):
        return text(widget) == lookup(key, language)

    def summary_in(language):
        from kratos_iga import i18n
        return i18n.translate("msg.summary_line", language,
                              name=Path(panel.project["source_step"]).name,
                              patches=len(panel.index["faces"]),
                              interfaces=len(panel.project["couplings"]))

    try:
        from PySide import QtWidgets

        # ---- A. controls, navigation, static labels, tooltips --------------
        panel.set_language("en")
        buttons = [button.text() for button in panel.findChildren(QtWidgets.QPushButton)]
        english = nav()
        selector = panel.language_selector
        check("top bar, summary, navigation and selector show the English catalog text",
              lookup("top.import_step", "en") in buttons
              and lookup("top.save_project", "en") in buttons
              and text(panel.summary) == summary_in("en")
              and panel.windowTitle() == lookup("app.title", "en")
              and len(english) == 8
              and all(lookup(key, "en") in english[i] for i, key in enumerate(NAV_KEYS))
              and selector.itemText(selector.findData("en")) == "English"
              and selector.itemText(selector.findData("zh")) == "中文",
              f"{buttons[:6]} nav={english} picker={texts(selector)}")

        switches(("zh",))
        check("a live switch localizes navigation, title and static labels",
              nav() != english and _cjk(panel.windowTitle()) and _cjk(text(panel.summary))
              and text(panel.summary) == summary_in("zh")
              and shows(panel.coupling_isolate_button, "interfaces.isolate", "zh")
              and text(panel.interaction_hint) == panel.interaction.machine.render_guidance(),
              f"nav={nav()[:3]} summary={_brief(text(panel.summary))}")
        switches(("en",))
        check("labels and tooltips return to English and differ per language",
              shows(panel.coupling_isolate_button, "interfaces.isolate", "en")
              and lookup("geometry.hover_audit_tooltip", "en")
              != lookup("geometry.hover_audit_tooltip", "zh")
              and text(panel.interaction_hint) == panel.interaction.machine.render_guidance()
              and lookup("top.import_step", "zh") == "+ 导入 STEP"
              and lookup("geometry.table.brep_id", "en") == "BRep ID"
              and "VTKHDF" in lookup("analysis.vtk_output", "en"),
              text(panel.coupling_isolate_button))

        # ---- B. combo canonical data, and the name / numeric draft ---------
        combos = [combo for combo in panel.findChildren(QtWidgets.QComboBox)
                  if combo is not panel.language_selector
                  and combo.count() and all(item is not None for item in items(combo))]
        baseline = [(combo.currentIndex(), items(combo)) for combo in combos]
        switches(("zh", "en"))
        load_kind = panel.load_kind
        english_items = texts(load_kind)
        switches(("zh",))
        check("combo canonical itemData, index and one option survive the switches",
              bool(combos)
              and baseline == [(combo.currentIndex(), items(combo)) for combo in combos]
              and texts(load_kind) != english_items
              and load_kind.itemData(load_kind.currentIndex()) is not None,
              f"combos={len(combos)} load_kind={items(load_kind)}")

        name_field = getattr(panel, "supports_name")
        vector = getattr(panel, "support_vector")
        name_field.setText("Probe_Support_语言")
        vector.set([1.25, None, -3.5])
        draft_name, draft_vector = text(name_field), vector.get()
        project = deepcopy(panel.project)
        for step, applied in enumerate(switches(("en", "zh", "en", "zh")), 1):
            check(f"switch {step}/4 keeps the name and numeric drafts and the project",
                  applied is not None and text(name_field) == "Probe_Support_语言"
                  and vector.get() == [1.25, None, -3.5] and panel.project == project,
                  f"{applied} {draft_name!r} {draft_vector}")

        # ---- D. the real Selection drives the coupling machine -------------
        import FreeCAD as App
        import FreeCADGui as Gui
        panel.start_coupling_pick()
        machine = panel.interaction.machine
        check("Start interface picking attaches the observer at the face1 stage",
              machine.mode == "coupling" and machine.step == "face1"
              and panel.interaction._observers_attached, f"{machine.mode}/{machine.step}")
        App.setActiveDocument(panel.doc.Name)
        faces = sorted(ident for (kind, ident) in panel.entities if kind == "face")
        first, second = (panel.entities[("face", ident)] for ident in faces[:2])
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(panel.doc.Name, first, "Face1")
        QtWidgets.QApplication.processEvents()
        phase = (machine.mode, machine.step, machine.face1.key() if machine.face1 else None)
        check("a real Selection.addSelection selects the patch and enters the face2 stage",
              machine.step == "face2" and phase[2] == ("face", faces[0], None), phase)

        name_field.setText("阶段草稿保留")
        stage_name = text(name_field)
        switches(("zh",))
        check("switching mid-flow keeps the phase, the face, the draft and the guidance",
              (machine.mode, machine.step, machine.face1.key()) == phase
              and text(name_field) == stage_name
              and shows(panel.interaction_hint, "interaction.guidance.coupling_face2", "zh"),
              f"{phase} {text(panel.interaction_hint)!r}")

        Gui.Selection.addSelection(panel.doc.Name, second, "Face1")
        QtWidgets.QApplication.processEvents()
        check("the second real patch resolves the existing candidate interface",
              machine.step == "edge" and machine.candidate_edge is not None,
              f"step={machine.step} candidate={machine.candidate_edge}")
        Gui.Selection.setPreselection(panel.doc.getObject(second), "Face1")
        QtWidgets.QApplication.processEvents()
        hover_zh = machine.hover_text
        switches(("en",))
        check("hover IDs and pair text are localized through the machine API",
              bool(hover_zh) and hover_zh != machine.hover_text
              and str(faces[1]) in hover_zh and "Patch" in (machine.hover_text or ""),
              f"zh={_brief(hover_zh)} en={_brief(machine.hover_text)}")
        switches(("zh",))
        check("switching after hover keeps the edge step, candidate and hover text",
              (machine.mode, machine.step, machine.candidate_edge, machine.hover_text)
              == ("coupling", "edge", machine.candidate_edge, hover_zh), machine.step)
        Gui.Selection.clearPreselection()
        panel.cancel_interaction()
        check("cancelling ends the flow through the real coordinator",
              not machine.active() and machine.mode == "idle", machine.mode)

        # The translated interval sentinel must still serialize as the schema's End.
        panel.set_language("zh")
        panel.new_condition("loads")
        panel.load_kind.setCurrentIndex(panel.load_kind.findData("vector"))
        panel.load_variable.setCurrentIndex(panel.load_variable.findText("DEAD_LOAD"))
        panel.load_vector.set([0.0, 0.0, 1.0])
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(panel.doc.Name, first, "Face1")
        panel.capture("loads")
        count_before = len(panel.project["loads"])
        panel.add_condition("loads")
        check("Chinese End interval records a real load with canonical End",
              len(panel.project["loads"]) == count_before + 1
              and panel.project["loads"][-1]["interval"][-1] == "End",
              panel.status.text())

        # ---- E. stored preference and a fresh Panel in the same process ----
        from kratos_iga import i18n
        from kratos_iga.gui import Panel
        panel.set_language("zh", persist=True)
        stored = i18n.read_language_preference(panel.user_data)
        fresh = Panel()
        try:
            fresh_nav = text(fresh.navigation.item(0))
            check("the stored Chinese preference is read by a new Panel instance",
                  stored == "zh" and fresh.language == "zh" and _cjk(fresh_nav),
                  f"stored={stored} fresh={fresh.language} {_brief(fresh_nav)}")
        finally:
            fresh.deleteLater()
        staged = (Path(panel.user_data) / STAGED_MARKER).exists()
        payload["staged"] = staged
        if staged and panel.language != started:
            panel.set_language(started, persist=True)
        payload["final_language"] = started if staged else "zh"
        check("the profile ends in the intended language state",
              panel.language == payload["final_language"]
              and i18n.current_language() == payload["final_language"],
              f"staged={staged} language={panel.language} start={started}")
        payload["state"] = "done"
        payload["passed"] = all(item["ok"] for item in payload["checks"])
    except Exception as exc:  # noqa: BLE001 - persisted for the coordinator
        payload["error"] = f"{type(exc).__name__}: {exc}"
        payload["traceback"] = traceback.format_exc()[-1500:]
        payload["state"] = "error"
        payload["passed"] = False
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload
