"""Run with FreeCAD's bundled Python against an isolated installed workbench.

Usage: <FreeCAD>/bin/python.exe tests/gui_installed_smoke.py <test-user-dir>
This test creates and closes its own model; use a disposable test user directory.
"""
import json
import os
from pathlib import Path
import sys
import traceback

user = Path(sys.argv[1]).resolve()
plugin = user / "Mod/KratosIBRA"
result_path = user / "gui_result.json"
result_path.write_text(json.dumps({"passed":False,"state":"starting"}),encoding="utf-8")
sys.path.insert(0,str(plugin))
import kratos_iga  # Pin the installed package before FreeCAD scans other Mod paths.
import FreeCAD as App
import FreeCADGui as Gui
# Isolate this test's configuration without changing the user's FreeCAD settings.
App.getUserAppDataDir = lambda: str(user)
Gui.showMainWindow()
from PySide import QtCore, QtWidgets
from kratos_iga.workbench import KratosIBRAWorkbench
from kratos_iga import gui
assert Path(gui.__file__).resolve().is_relative_to(plugin)
if "KratosIBRAWorkbench" not in Gui.listWorkbenches():
    Gui.addWorkbench(KratosIBRAWorkbench())
Gui.activateWorkbench("KratosIBRAWorkbench")
panel = gui.show_panel()


def finish(error=None):
    payload = {"passed":error is None,"workbench":"KratosIBRAWorkbench",
               "installed_copy":True,"relative_example":True,"async_import":True}
    if error:
        payload["error"] = error
    result_path.write_text(json.dumps(payload,indent=2),encoding="utf-8")
    if panel.doc:
        App.closeDocument(panel.doc.Name)
    os._exit(1 if error else 0)


def inspect():
    if panel.process:
        QtCore.QTimer.singleShot(200,inspect)
        return
    try:
        assert panel.cache and len(panel.index["faces"])==1,panel.log.toPlainText()
        assert panel.check_project(),panel.log.toPlainText()
        panel.save_backend_configuration()
        assert (user/"IBRA/backend.json").exists()
        Gui.Selection.addSelection(panel.doc.Name,panel.entities[("face",2)],"Vertex1")
        assert panel.selections()[0]["side"] in ("00","10","01","11")
        panel.setFloating(True)
        panel.resize(820,920)
        panel.tabs.setCurrentIndex(7)
        QtWidgets.QApplication.processEvents()
        panel.grab().save(str(user/"installed_output.png"))
        panel.tabs.setCurrentIndex(3)
        panel.edit_condition("supports",0)
        QtWidgets.QApplication.processEvents()
        panel.grab().save(str(user/"installed_supports.png"))
        finish()
    except Exception:
        finish(traceback.format_exc())


QtCore.QTimer.singleShot(90000,lambda:finish("Timed out"))
try:
    panel.load_project(plugin/"examples/cantilever/cantilever.ibra-project.json")
    QtCore.QTimer.singleShot(200,inspect)
except Exception:
    finish(traceback.format_exc())
QtWidgets.QApplication.instance().exec()
