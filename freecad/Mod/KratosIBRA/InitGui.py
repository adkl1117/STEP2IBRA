"""Register the workbench when FreeCAD starts its graphical interface."""
import FreeCADGui as Gui
from kratos_iga.workbench import KratosIBRAWorkbench

Gui.addWorkbench(KratosIBRAWorkbench())
