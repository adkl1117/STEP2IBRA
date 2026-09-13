"""Project validation; stdlib only, usable in FreeCAD and the backend."""
import json
import math
import os
from copy import deepcopy
from pathlib import Path

SCHEMA = 1
ELEMENTS = {"Shell3pElement": 2, "IgaMembraneElement": 1}
SIDES = {"u0": [0, -1], "u1": [1, -1], "v0": [-1, 0], "v1": [-1, 1],
         "00": [0, 0], "10": [1, 0], "01": [0, 1], "11": [1, 1], "all": [-1, -1]}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def read_project(path):
    path = Path(path).resolve()
    project = read_json(path)
    for key in ("source_step", "profile"):
        if project.get(key):
            project[key] = str((path.parent / project[key]).resolve())
    return project


def write_project(path, project):
    path = Path(path).resolve()
    data = deepcopy(project)
    for key in ("source_step", "profile"):
        if data.get(key):
            try:
                data[key] = Path(os.path.relpath(data[key], path.parent)).as_posix()
            except ValueError:  # Different Windows drives cannot use relative paths.
                data[key] = str(Path(data[key]).resolve())
    write_json(path, data)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(tmp, path)


def new_project():
    return {"schema_version": SCHEMA, "source_step": "", "source_sha256": "", "profile": "",
            "units": "mm-N-s", "materials": [{"id": 1, "name": "Steel", "young": 210000.0,
                "poisson": 0.3, "density": 7.85e-9, "thickness": 1.0}],
            "patches": [], "supports": [], "loads": [], "initial_conditions": [],
            "couplings": [], "solver": {"type": "static", "analysis_type": "linear",
                "start_time": 0.0, "end_time": 1.0, "time_step": 1.0},
            "output": {"vtk": True, "refinement": 4}}


def populate(project, index):
    project["patches"] = [{"face_id": f["id"], "material_id": 1, "element": "Shell3pElement",
        "degree_u": max(2, f["degrees"][0]), "degree_v": max(2, f["degrees"][1]),
        "insert_u": 1, "insert_v": 1, "quadrature": 0} for f in index["faces"]]
    project["couplings"] = [{"edge_id": e["id"], "enabled": True, "penalty": 1e7,
        "rotation": False} for e in index["edges"] if e["kind"] == "coupling"]


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate(project, index):
    errors, warnings = [], []
    def require(test, message):
        if not test:
            errors.append(message)
    require(project.get("schema_version") == SCHEMA, "Unsupported project schema version.")
    require(project.get("source_sha256") == index.get("source_sha256"), "The STEP checksum does not match the geometry cache. Reimport the source geometry.")
    require(project.get("geometry_sha256", index.get("geometry_sha256")) == index.get("geometry_sha256"), "Converted geometry has changed, possibly due to a modified profile. Reassign analysis conditions.")
    require(project.get("units") in ("mm-N-s", "m-N-s"), "Select a consistent unit system.")
    faces = {f["id"]: f for f in index["faces"]}
    edges = {e["id"]: e for e in index["edges"]}
    materials = {m["id"]: m for m in project["materials"]}
    require(len(materials) == len(project["materials"]), "Material IDs must be unique.")
    for m in materials.values():
        require(all(finite(m.get(k)) and m[k] > 0 for k in ("young", "density", "thickness")), f"Material {m['id']}: Young's modulus, density and thickness must be finite and positive.")
        require(finite(m.get("poisson")) and -1 < m["poisson"] < 0.5, f"Material {m['id']}: Poisson's ratio must lie in (-1, 0.5).")
    patch_ids = [p["face_id"] for p in project["patches"]]
    require(len(set(patch_ids)) == len(patch_ids) and set(patch_ids) == set(faces), "Assign exactly one element formulation and material to each imported analysis patch.")
    for p in project["patches"]:
        if p["face_id"] not in faces:
            continue
        require(p["material_id"] in materials, f"Patch {p['face_id']} references an undefined material ID.")
        require(p["element"] in ELEMENTS, "Supported formulations: Kirchhoff–Love shell and membrane.")
        require(p["element"] != "Shell3pElement" or min(faces[p["face_id"]].get("min_continuity", [99,99])) >= 1,
                f"Patch {p['face_id']} has internal continuity C⁰ or lower; the shell requires C¹. Degree elevation does not improve existing continuity. Reparameterize, remove redundant knots where valid, or select a membrane formulation if physically appropriate.")
        for d, original in zip(("u", "v"), faces[p["face_id"]]["degrees"]):
            require(type(p["degree_"+d]) is int and max(original, ELEMENTS.get(p["element"], 1)) <= p["degree_"+d] <= 8,
                    f"Patch {p['face_id']}: target degrees must meet the original and formulation-specific minimum degrees and must not exceed 8.")
            require(type(p["insert_"+d]) is int and 0 <= p["insert_"+d] <= 100, "Knot insertions per span must be integers from 0 to 100.")
        require(type(p["quadrature"]) is int and 0 <= p["quadrature"] <= 20, "Quadrature points per span must be 0 (default) or an integer from 1 to 20.")
    s = project["solver"]
    require(s["type"] in ("static", "dynamic"), "Select static or dynamic analysis.")
    require(s["analysis_type"] in ("linear", "non_linear"), "Invalid analysis formulation.")
    require(all(finite(s.get(k)) for k in ("start_time", "end_time", "time_step")) and s["end_time"] > s["start_time"] and 0 < s["time_step"] <= s["end_time"]-s["start_time"], "The time increment must be positive and must not exceed the analysis duration.")
    occupied = set()
    for category in ("supports", "loads", "initial_conditions"):
        names = set()
        for item in project[category]:
            require(item["name"] not in names, f"Condition names must be unique within {category}.")
            names.add(item["name"])
            values = item["value"]
            require(len(values) == 3 and all(v is None or finite(v) for v in values) and any(v is not None for v in values), f"{item['name']}: specify at least one finite component.")
            kind, ident = item["target_kind"], item["target_id"]
            require((kind == "face" and ident in faces) or (kind == "edge" and ident in edges and edges[ident]["kind"] == "edge"), f"{item['name']}: the target is undefined or is not a one-sided boundary.")
            if category != "initial_conditions":
                a, b = item["interval"]
                require(finite(a) and (b == "End" or finite(b) and b >= a), f"{item['name']}: invalid active time interval.")
                require(finite(a) and a >= s["start_time"] and a <= s["end_time"] and (b == "End" or finite(b) and b <= s["end_time"]), f"{item['name']}: the active interval must lie within the analysis interval.")
            if category == "supports":
                require(item["method"] in ("strong", "penalty"), "Invalid essential-boundary-condition enforcement method.")
                if item["method"] == "penalty":
                    require(kind == "edge", "Penalty enforcement requires a one-sided boundary curve.")
                    require(all(v is not None for v in values), "Penalty supports constrain all XYZ components simultaneously. Use strong enforcement for individual components.")
                    require(finite(item["penalty"]) and item["penalty"] > 0, "The penalty factor must be positive.")
                    require(item["interval"] == [s["start_time"], "End"], "Penalty supports currently require the entire analysis interval.")
                    require(not item.get("clamp"), "Penalty supports do not provide adjacent-row clamping.")
                else:
                    local = strong_location(item, index)
                    require(local is not None, f"{item['name']}: strong enforcement requires a complete natural parametric boundary or corner. Use penalty enforcement on general trimming curves.")
                    require(not item.get("clamp") or local is not None and -1 in local[1] and local[1] != [-1, -1], "Adjacent-row clamping requires a complete natural parametric boundary.")
                    if local:
                        for i, v in enumerate(values):
                            key = (local[0], tuple(local[1]), i)
                            require(v is None or key not in occupied, "Duplicate strongly constrained components. Edit the existing essential boundary condition.")
                            if v is not None:
                                occupied.add(key)
            elif category == "loads":
                require(item["variable"] in ("LINE_LOAD", "SURFACE_LOAD", "DEAD_LOAD"), "Invalid load variable.")
                require(item["variable"] != "LINE_LOAD" or kind == "edge", "LINE_LOAD requires a boundary curve.")
                require(item["variable"] != "SURFACE_LOAD" or kind == "face", "SURFACE_LOAD requires a surface patch.")
                require(all(v is not None for v in values), "Specify all XYZ load components; zero values are permitted.")
            else:
                require(kind == "face", "Initial fields are currently assigned to all control points of a patch.")
                require(item["variable"] in ("DISPLACEMENT", "VELOCITY", "ACCELERATION"), "Invalid initial-field variable.")
                require(s["type"] == "dynamic" or item["variable"] == "DISPLACEMENT", "Static analysis does not support initial velocity or acceleration.")
                require(s["analysis_type"] == "non_linear" or item["variable"] != "DISPLACEMENT" or all(v in (None, 0) for v in values), "Nonzero initial displacement requires nonlinear analysis.")
    for c in project["couplings"]:
        require(c["edge_id"] in edges and edges[c["edge_id"]]["kind"] == "coupling", "Invalid interface ID.")
        require(finite(c["penalty"]) and c["penalty"] > 0, "The interface penalty factor must be positive.")
        if c["enabled"] and not c.get("rotation"):
            warnings.append(f"Interface {c['edge_id']} enforces displacement continuity only. Enable rotational coupling where shell bending continuity is required.")
        if c.get("rotation") and c["edge_id"] in edges:
            patch_map = {p["face_id"]: p for p in project["patches"]}
            require(all(patch_map.get(fid, {}).get("element") == "Shell3pElement" for fid in edges[c["edge_id"]]["face_ids"]), "Rotational coupling requires the shell formulation on both sides of an interface.")
    require(type(project["output"]["refinement"]) is int and 1 <= project["output"]["refinement"] <= 100, "Visualization subdivision must be an integer from 1 to 100.")
    if not project["supports"]:
        warnings.append("No essential boundary conditions are defined; the static system will generally contain rigid-body modes.")
    if not project["loads"] and not project["initial_conditions"]:
        warnings.append("No loads or initial fields are defined.")
    warnings.append("mm-N-s preserves STEP coordinates in millimetres; m-N-s scales geometry by 0.001 at export. Material, thickness and load values must be dimensionally consistent.")
    return errors, list(dict.fromkeys(warnings))


def strong_location(item, index):
    if item["target_kind"] == "face":
        if item.get("side") in SIDES:
            return item["target_id"], SIDES[item["side"]]
    else:
        for edge in index["edges"]:
            if edge["id"] == item["target_id"] and edge.get("strong_local") is not None:
                return edge["face_ids"][0], edge["strong_local"]
    return None
