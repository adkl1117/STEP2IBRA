"""Project relocation and configuration contracts for a standalone installation."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT/"freecad/Mod/KratosIBRA"
sys.path.insert(0,str(PLUGIN))
from kratos_iga.project import read_project, write_project
from kratos_iga.settings import load_settings, save_settings


class PortabilityTests(unittest.TestCase):
    def test_example_relocation(self):
        with tempfile.TemporaryDirectory() as folder:
            moved=Path(folder)/"relocated example"
            shutil.copytree(PLUGIN/"examples/cantilever",moved)
            project=read_project(moved/"cantilever.ibra-project.json")
            self.assertEqual(Path(project["source_step"]),moved/"cantilever.step")
            project["profile"]=""
            write_project(moved/"saved.ibra-project.json",project)
            data=json.loads((moved/"saved.ibra-project.json").read_text())
            self.assertEqual(data["source_step"],"cantilever.step")
            self.assertEqual(read_project(moved/"saved.ibra-project.json")["source_step"],project["source_step"])

    def test_user_settings_survive_new_session(self):
        with tempfile.TemporaryDirectory() as folder:
            user=Path(folder)/"FreeCAD User"
            data=save_settings(user,{"backend_python":sys.executable,"pipeline_root":str(PLUGIN/"backend"),"work_dir":str(user/"Analysis")})
            self.assertEqual(load_settings(user,PLUGIN),data)
            self.assertTrue(Path(data["work_dir"]).is_dir())

    def test_invalid_backend_does_not_save(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):
                save_settings(folder,{"backend_python":str(Path(folder)/"missing.exe"),"pipeline_root":str(PLUGIN/"backend"),"work_dir":folder})
            self.assertFalse((Path(folder)/"IBRA/backend.json").exists())


if __name__ == "__main__":
    unittest.main()
