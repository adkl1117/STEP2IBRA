"""Install the self-contained workbench into an explicitly selected user directory."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def install(user_directory, backend_python):
    source = ROOT / "freecad/Mod/KratosIBRA"
    user_directory = Path(user_directory).expanduser().resolve()
    target = user_directory / "Mod/KratosIBRA"
    if target.exists():
        raise FileExistsError(f"Existing installation preserved: {target}. Move it outside Mod before reinstalling.")
    backend_python = Path(backend_python).expanduser().absolute()
    if not backend_python.is_file():
        raise FileNotFoundError(backend_python)
    subprocess.run([str(backend_python), str(source / "backend/check_environment.py")], check=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "backend_config.json"))
    config = {"backend_python": str(backend_python), "pipeline_root": str(target / "backend"),
              "work_dir": str(user_directory / "IBRA-work")}
    (target / "backend_config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"Installed: {target}")
    print("Restart FreeCAD, then select 'IBRA Preprocessor for Kratos'.")
    if (user_directory / "IBRA/backend.json").exists():
        print("Existing user backend settings take precedence; review them in Analysis and Output.")
    print("The backend virtual environment must remain at its configured location.")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freecad-user-dir", required=True,
                        help="Exact directory printed by FreeCAD.getUserAppDataDir() in FreeCAD's Python console")
    parser.add_argument("--backend-python", default=sys.executable)
    args = parser.parse_args()
    install(args.freecad_user_dir, args.backend_python)
