"""Convert, export and optionally solve the bundled analytical cantilever."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "freecad/Mod/KratosIBRA"
sys.path.insert(0, str(PLUGIN))
from kratos_iga.project import read_json, read_project, write_json
from kratos_iga.bundle import export_bundle


def run(output, solve=False):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a new or empty output directory.")
    output.parent.mkdir(parents=True, exist_ok=True)
    source = PLUGIN / "examples/cantilever/cantilever.ibra-project.json"
    project = read_project(source)
    with tempfile.TemporaryDirectory(prefix="ibra_convert_", dir=output.parent) as folder:
        cache = Path(folder)
        request = {"action":"convert", "pipeline_root":str(PLUGIN/"backend"),
                   "source_step":project["source_step"], "profile":"", "cache_dir":str(cache)}
        write_json(cache/"request.json", request)
        subprocess.run([sys.executable, str(PLUGIN/"kratos_iga/worker.py"), str(cache/"request.json")], check=True)
        export_bundle(project, read_json(cache/"geometry.cad.json"), read_json(cache/"index.json"), output)
    command = [sys.executable, str(output/"MainKratos.py")]
    subprocess.run(command + ([] if solve else ["--check"]), check=True)
    report = read_json(output/"run_report.json")
    if solve:
        expected = .02
        error = abs(report["max_abs_displacement"]-expected)/expected
        if error > 1e-6:
            raise AssertionError(f"Cantilever relative displacement error: {error}")
        report.update(expected_abs_tip_displacement_mm=expected, relative_error=error)
    write_json(output/"example_verification.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--solve", action="store_true")
    args = parser.parse_args()
    run(args.output, args.solve)
