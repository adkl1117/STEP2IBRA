"""Execute using the external backend Python, never FreeCAD's interpreter."""
import importlib
import importlib.metadata
import json
from pathlib import Path
import platform
import struct
import sys


def check():
    if sys.version_info[:2] != (3, 11) or struct.calcsize("P") != 8:
        raise RuntimeError("Use a 64-bit Python 3.11 backend for this verified configuration.")
    for name in ("OCP", "numpy", "h5py", "KratosMultiphysics",
                 "KratosMultiphysics.IgaApplication", "KratosMultiphysics.StructuralMechanicsApplication",
                 "KratosMultiphysics.LinearSolversApplication", "cad_pipeline.api"):
        importlib.import_module(name)
    required = {"cadquery-ocp": "7.8.1.1", "KratosMultiphysics": "10.4.3",
                "KratosIgaApplication": "10.4.3", "KratosStructuralMechanicsApplication": "10.4.3",
                "KratosLinearSolversApplication": "10.4.3"}
    versions = {name: importlib.metadata.version(name) for name in required}
    if any(versions[name] != version for name, version in required.items()):
        raise RuntimeError("Backend versions differ from requirements-backend.txt: " + str(versions))
    return {"passed": True, "python": platform.python_version(), "platform": platform.system(), "packages": versions}


if __name__ == "__main__":
    result = check()
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
