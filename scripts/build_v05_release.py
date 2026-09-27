"""Build the V0.5 preview asset from an explicit portable file selection."""
import argparse
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "STEP2IBRA-V0.5preview"
ARCHIVE = "STEP2IBRA-V0.5preview-20260927.zip"
DOCUMENTS = (
    "V0_5_INSTALLATION_en.md", "V0_5_INSTALLATION_zh.md",
    "V0_5_RELEASE_NOTES_en.md", "V0_5_RELEASE_NOTES_zh.md",
)


def selected_files():
    explicit = ["LICENSE", "THIRD_PARTY_NOTICES.md", "README.md",
                "requirements-backend.txt", "requirements-windows-lock.txt",
                "scripts/install_workbench.py", "scripts/run_example.py",
                "docs/Validation.md", "docs/Architecture.md"]
    explicit.extend("docs/" + name for name in DOCUMENTS)
    selected = [ROOT / name for name in explicit]
    for path in sorted((ROOT / "freecad/Mod/KratosIBRA").rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path.suffix in (".pyc", ".pyo") or path.name == "backend_config.json":
            continue
        selected.append(path)
    for path in selected:
        if not path.is_file():
            raise FileNotFoundError(path)
    return selected


def build(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / ARCHIVE
    files = selected_files()
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as zipped:
        for path in files:
            zipped.write(path, PREFIX + "/" + path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(archive) as zipped:
        if zipped.testzip() is not None:
            raise RuntimeError("Archive integrity check failed")
        for path in files:
            name = PREFIX + "/" + path.relative_to(ROOT).as_posix()
            if zipped.read(name) != path.read_bytes():
                raise RuntimeError("Archive content differs: " + name)
        # Every shipped example must resolve its source within the package.
        for path in files:
            if path.name.endswith(".ibra-project.json"):
                project = json.loads(path.read_text(encoding="utf-8"))
                source = path.parent / project["source_step"]
                if not source.is_file() or source not in files:
                    raise RuntimeError("Unresolved packaged STEP reference: " + str(path))
    print(json.dumps({"archive": str(archive), "file_count": len(files),
                      "bytes": archive.stat().st_size, "byte_comparison": "passed",
                      "example_paths": "passed"}))
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    build(parser.parse_args().output)
