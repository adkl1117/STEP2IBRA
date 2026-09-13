"""Portable backend configuration, independent of the source checkout location."""
import json
import os
from pathlib import Path


def load_settings(user_data, plugin):
    user_data, plugin = Path(user_data), Path(plugin)
    result = {"backend_python": "", "pipeline_root": str(plugin / "backend"),
              "work_dir": str(user_data / "IBRA-work")}
    for path in (plugin / "backend_config.json", user_data / "IBRA/backend.json"):
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in result:
                if key in data:
                    result[key] = data[key]
    return result


def save_settings(user_data, data):
    python = Path(data["backend_python"]).expanduser().resolve(strict=True)
    pipeline = Path(data["pipeline_root"]).expanduser().resolve(strict=True)
    if not python.is_file() or not (pipeline / "cad_pipeline/api.py").is_file():
        raise ValueError("Select a Python executable and a directory containing cad_pipeline.")
    work = Path(data["work_dir"]).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    result = dict(backend_python=str(python), pipeline_root=str(pipeline), work_dir=str(work))
    path = Path(user_data) / "IBRA/backend.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return result
