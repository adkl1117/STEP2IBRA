from pathlib import Path
import FreeCADGui as Gui


class KratosIBRAWorkbench(Gui.Workbench):
    MenuText = "IBRA Preprocessor for Kratos"
    ToolTip = "STEP → NURBS / IBRA → Kratos Multiphysics"
    Icon = str(Path(__file__).resolve().parents[1]/"Resources"/"iga.svg")

    def Initialize(self):
        from . import gui
        Gui.addCommand("IBRA_Preprocessor", gui.OpenCommand())
        self.appendToolbar(self.MenuText, ["IBRA_Preprocessor"])
        self.appendMenu(self.MenuText, ["IBRA_Preprocessor"])

    def Activated(self):
        from .gui import show_panel
        show_panel()

    def GetClassName(self):
        return "Gui::PythonWorkbench"
