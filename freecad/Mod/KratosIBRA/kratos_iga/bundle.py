"""Translate GUI intent into the native Kratos modeler/process contracts."""
from copy import deepcopy
import math
from pathlib import Path
import shutil
import tempfile
from . import inspection
from .groups import (active_face_ids, coupling_is_active, edge_is_active, expand, resolve,
                     strong_spec)
from .project import (convert_project_units, migrate_project, strong_location, validate,
                      write_json, write_project)

ROOT = "IgaModelPart"


def process(module, name, params):
    result = {"python_module": name, "Parameters": params}
    if module:
        result["kratos_module"] = module
    return result


def _source_area(index, face_id, units):
    """Independent OCP source area for one face, in the export unit (None if unknown)."""
    face = next((f for f in index.get("faces", []) if f.get("id") == face_id), None)
    raw = face.get("source_area_mm2") if isinstance(face, dict) else None
    if not isinstance(raw, (int, float)) or isinstance(raw, bool) \
            or not math.isfinite(raw) or raw <= 0:
        return None
    return float(raw) * inspection.UNITS_AREA.get(units, ("mm", "mm^2", 1.0))[2]


def make_bundle(project, cad, index):
    project = migrate_project(project)
    errors, warnings = validate(project, index)
    if errors:
        raise ValueError("\n".join(errors))
    # P0-4 pre-export diagnostics: malformed NURBS data (non-finite weights/knots,
    # no positive span, bad degree) rejects the export. Repeated knots are a valid
    # multiplicity and are only reported. Nothing is repaired and the CAD is untouched.
    diagnostics = inspection.cad_diagnostics(cad)
    if diagnostics["errors"]:
        raise ValueError("The converted geometry is malformed:\n" + "\n".join(diagnostics["errors"]))
    active_faces = active_face_ids(project)
    groups = expand(project.get("groups", []), index)
    edge_map = {e["id"]: e for e in index["edges"]}
    excluded, load_overlaps = [], []

    def targets(record):
        """Deterministic group/direct expansion, shared with validation and the GUI."""
        resolved = resolve(record, groups, index)
        return resolved["kind"], list(resolved["ids"]), resolved["source"]

    physics, refinements, materials = [], [], []
    processes = {"initial_conditions_process_list": [], "constraints_process_list": [], "loads_process_list": []}
    face_map = {f["id"]: f for f in index["faces"]}
    mat_map = {m["id"]: m for m in project["materials"]}
    sec_map = {sec["id"]: sec for sec in project["sections"]}
    def unit(ids, kind, name, params):
        physics.append({"brep_ids": ids, "geometry_type": kind, "iga_model_part": name, "parameters": params})
    def prop(name, variables, law=None):
        mat = {"Variables": variables, "Tables": {}}
        if law:
            mat.update({"name": law["name"], "constitutive_law": {"name": "LinearElasticPlaneStress2DLaw"}})
        materials.append({"model_part_name": ROOT+"."+name, "properties_id": len(materials)+1, "Material": mat})
    for patch in project["patches"]:
        fid = patch["face_id"]
        if not patch.get("active", True):
            excluded.append(f"patch {fid} is outside the active analysis domain")
            continue
        params = {"type": "element", "name": patch["element"], "shape_function_derivatives_order": 3}
        if patch["quadrature"]:
            params["number_of_integration_points_per_span"] = patch["quadrature"]
        unit([fid], "GeometrySurface", f"Patch_{fid}", params)
        material = mat_map[patch["material_id"]]
        section = sec_map[patch["section_id"]]
        prop(f"Patch_{fid}", {"YOUNG_MODULUS": material["young"], "POISSON_RATIO": material["poisson"],
              "DENSITY": material["density"], "THICKNESS": section["thickness"], "PRESTRESS": [0., 0., 0.]}, material)
        if project.get("refinement_enabled", True):
            refinements.append({"brep_ids": [fid], "geometry_type": "NurbsSurface", "model_part_name": ROOT,
                "parameters": {"increase_degree_u": patch["degree_u"]-face_map[fid]["degrees"][0],
                    "increase_degree_v": patch["degree_v"]-face_map[fid]["degrees"][1],
                    "insert_nb_per_span_u": patch["insert_u"], "insert_nb_per_span_v": patch["insert_v"]}})
    # Strong supports: one submodel part per *record*, holding every member of that
    # record. `assign_vector_variable_process` builds one scalar process per non-null
    # value component (installed Kratos `assign_vector_variable_process.py:72-82`), so
    # a record that leaves a component null does NOT free that component; records are
    # therefore never merged and never have their interval widened. Every part name is
    # unique because `support_number` runs over strong and penalty records together.
    support_number = 0
    for support in project["supports"]:
        support_number += 1
        name = f"Support_{support_number}"
        if support["method"] != "strong":
            kind, ids, _source = targets(support)
            unit(ids, "SurfaceEdge", name, {"type": "condition", "name": "SupportPenaltyCondition", "shape_function_derivatives_order": 2})
            prop(name, {"PENALTY_FACTOR": support["penalty"]})
            processes["constraints_process_list"].append(process("KratosMultiphysics", "assign_vector_variable_to_conditions_process",
                {"model_part_name": ROOT+"."+name, "variable_name": "DISPLACEMENT", "value": support["value"], "interval": support["interval"]}))
            continue
        spec = strong_spec(support, index, groups, project)
        seen = set()
        for face_id, local, _region, _mask in spec["locations"]:
            key = (face_id, tuple(local))
            if key in seen:  # a member selected twice acts once
                continue
            seen.add(key)
            unit([face_id], "GeometrySurfaceNodes", name, {"local_parameters": list(local)})
            if support.get("clamp"):
                unit([face_id], "GeometrySurfaceVariationNodes", name, {"local_parameters": list(local)})
        processes["constraints_process_list"].append(process("KratosMultiphysics", "assign_vector_variable_process",
            {"model_part_name": ROOT+"."+name, "variable_name": "DISPLACEMENT", "value": support["value"],
             "constrained": [v is not None for v in support["value"]], "interval": support["interval"]}))
    load_targets = {}
    patch_quadrature = {p["face_id"]: p["quadrature"] for p in project["patches"]}
    area_unit = inspection.UNITS_AREA.get(project["units"], ("mm", "mm^2", 1.0))[1]
    for i, load in enumerate(project["loads"], 1):
        name = f"Load_{i}"
        kind, ids, source = targets(load)
        if load.get("kind") == "reference_pressure":
            # One condition unit per distinct requested patch quadrature (so the
            # reference/load integration matches the element patch face by face), but
            # exactly ONE process for the record: the process sees the whole Load_i
            # submodel part, so its expected_area is the sum over every target face.
            by_quadrature = {}
            for ident in ids:
                by_quadrature.setdefault(patch_quadrature.get(ident, 0), []).append(ident)
            prop(name, {})
            expected_total = 0.0
            expected_by_face = {}
            missing = []
            for quadrature, group_ids in sorted(by_quadrature.items()):
                params = {"type": "condition", "name": "LoadCondition",
                          "shape_function_derivatives_order": 2}
                if quadrature:
                    params["number_of_integration_points_per_span"] = quadrature
                unit(group_ids, "GeometrySurface", name, params)
                for ident in group_ids:
                    area = _source_area(index, ident, project["units"])
                    if area is None:
                        missing.append(ident)
                    else:
                        expected_total += area
                        expected_by_face[str(ident)] = area
                    key = ("face", ident, "reference_pressure")
                    if key in load_targets:
                        load_overlaps.append(f"{load_targets[key]} and {name} both act on face "
                                             f"{ident} (reference_pressure); the solver adds them.")
                    load_targets[key] = name
            if missing:
                raise ValueError(
                    f"{name}: reference pressure target face(s) {missing} have no independent "
                    "OCP source area in the geometry cache. Reimport the source STEP and "
                    "rebuild the cache before exporting pressure.")
            # expected_area is the independent OCP trimmed area (export units) summed
            # over every face the single process covers; the runtime checks the
            # integrated condition area against it.
            processes["loads_process_list"].append(process("", "ibra_case_process", {
                "mode": "reference_pressure", "model_part_name": ROOT+"."+name,
                "pressure": load["pressure"],
                "normal_flip": bool(load.get("normal_flip", False)),
                "interval": load["interval"], "target_source": source,
                "amplitude": load.get("amplitude"),
                "expected_area": expected_total, "expected_area_units": area_unit,
                "expected_areas": expected_by_face}))
            continue
        for ident in ids:
            key = (kind, ident, load["variable"])
            if key in load_targets:
                load_overlaps.append(f"{load_targets[key]} and {name} both act on {kind} {ident} "
                                     f"({load['variable']}); the solver adds them.")
            load_targets[key] = name
        unit(ids, "GeometrySurface" if kind == "face" else "SurfaceEdge", name,
             {"type": "condition", "name": "LoadCondition", "shape_function_derivatives_order": 2})
        prop(name, {})
        # This process explicitly clears the load outside its interval; an optional
        # amplitude table scales it inside. Legacy records are unchanged.
        processes["loads_process_list"].append(process("", "ibra_case_process",
            {"mode": "load", "model_part_name": ROOT+"."+name, "variable_name": load["variable"], "value": load["value"], "interval": load["interval"],
             "target_source": source, "amplitude": load.get("amplitude")}))
    for i, initial in enumerate(project["initial_conditions"], 1):
        name = f"Initial_{i}"
        _kind, ids, source = targets(initial)
        unit(ids, "GeometrySurfaceNodes", name, {"local_parameters": [-1, -1]})
        processes["initial_conditions_process_list"].append(process("", "ibra_case_process",
            {"mode": "initial", "model_part_name": ROOT+"."+name, "variable_name": initial["variable"], "value": initial["value"],
             "target_source": source}))
    for coupling in project["couplings"]:
        if coupling["state"] != "confirmed":
            continue
        edge = edge_map.get(coupling["edge_id"], {})
        if not coupling_is_active(edge, active_faces):
            excluded.append(f"interface {coupling['edge_id']} (an adjacent patch is inactive)")
            continue
        name = "Coupling_"+str(coupling["edge_id"])
        unit([coupling["edge_id"]], "SurfaceEdgeSurfaceEdge", name,
             {"type": "condition", "name": "CouplingPenaltyCondition", "shape_function_derivatives_order": 3})
        prop(name, {"PENALTY_FACTOR": coupling["penalty"]})
        for variable in (["DISPLACEMENT", "ROTATION"] if coupling.get("rotation") else ["DISPLACEMENT"]):
            processes["constraints_process_list"].append(process("KratosMultiphysics.IgaApplication", "assign_vector_variable_and_constraints_to_conditions_process",
                {"model_part_name": ROOT+"."+name, "variable_name": variable, "value": [0., 0., 0.], "interval": [project["solver"]["start_time"], "End"]}))
    solver = project["solver"]
    settings = {"solver_type": solver["type"], "model_part_name": ROOT, "domain_size": 3,
        "analysis_type": solver["analysis_type"], "model_import_settings": {"input_type": "use_input_model_part"},
        "material_import_settings": {"materials_filename": "Materials.json"}, "time_stepping": {"time_step": solver["time_step"]},
        "rotation_dofs": False, "echo_level": 1, "compute_reactions": True,
        "max_iteration": 40, "move_mesh_flag": True,
        "linear_solver_settings": {"solver_type": "LinearSolversApplication.sparse_lu"}}
    params = {"problem_data": {"problem_name": "FreeCAD_IBRA", "parallel_type": "OpenMP", "echo_level": 1,
            "start_time": solver["start_time"], "end_time": solver["end_time"]},
        "solver_settings": settings,
        "modelers": [
            {"modeler_name": "CadIoModeler", "Parameters": {"cad_model_part_name": ROOT, "geometry_file_name": "geometry.cad.json"}},
            {"modeler_name": "RefinementModeler", "Parameters": {"refinements_file_name": "refinements.iga.json"}},
            {"modeler_name": "IgaModeler", "Parameters": {"cad_model_part_name": ROOT, "analysis_model_part_name": ROOT, "physics_file_name": "physics.iga.json"}}],
        "processes": processes, "output_processes": {"output_process_list": []}}
    if not project.get("refinement_enabled", True):
        params["modelers"] = [m for m in params["modelers"] if m["modeler_name"] != "RefinementModeler"]
    if project["output"]["vtk"]:
        params["output_processes"]["output_process_list"].append(process("KratosMultiphysics.IgaApplication", "iga_vtk_output_process",
            {"model_part_name": ROOT, "output_file_name": "results", "brep_surface_ids": sorted(active_faces),
             "nodal_solution_step_data_variables": ["DISPLACEMENT"], "output_refinement": [project["output"]["refinement"]]*2,
             "output_control_type": "step", "output_frequency": 1}))
        warnings.append("Kratos 10.4.3 VTK output samples the background surface. Verify the visualization of trimming holes independently; the display mesh does not establish the integration domain.")
    for entry in load_overlaps:
        warnings.append("Overlapping loads: " + entry)
    if excluded:
        warnings.append("Not exported: " + "; ".join(excluded) + ". The CAD file still contains the "
                        "reference geometry.")
    geometry = deepcopy(cad)
    if project["units"] == "m-N-s":
        for brep in geometry["breps"]:
            entities = [f["surface"] for f in brep["faces"]] + [e["3d_curve"] for e in brep["edges"] if "3d_curve" in e]
            for entity in entities:
                for cp in entity["control_points"]:
                    cp[1][:3] = [x * 0.001 for x in cp[1][:3]]
        geometry["tolerances"]["model_tolerance"] *= 0.001
    return {"geometry.cad.json": geometry, "physics.iga.json": {"element_condition_list": physics},
            "refinements.iga.json": {"refinements": refinements}, "Materials.json": {"properties": materials},
            "ProjectParameters.json": params, "project.ibra-project.json": deepcopy(project),
            "export_report.json": {"errors": [], "warnings": list(dict.fromkeys(warnings)), "runtime_target": "Kratos 10.4.3",
                "source_sha256": project["source_sha256"], "geometry_units": "m" if project["units"] == "m-N-s" else "mm",
                "active_patches": sorted(active_faces), "total_patches": len(project["patches"]),
                "groups": {str(gid): {"name": g["name"], "kind": g["kind"], "members": g["members"]}
                           for gid, g in sorted(groups.items())},
                "excluded_from_export": list(excluded),
                # P0-4 additive block. Existing fields above are unchanged; the
                # analysis refinement, requested quadrature and display sampling stay
                # separate, and the independent OCCT source area (unit^2 scaled) is
                # only claimed when the cache actually carries it.
                "preprocessing": inspection.preprocessing_report(project, cad, index, diagnostics),
                "note": "Export validation is not a mechanical correctness or convergence proof."}}


def export_bundle(project, cad, index, destination):
    payload = make_bundle(project, cad, index)
    dest = Path(destination).resolve()
    if dest.exists() and any(dest.iterdir()):
        raise ValueError("Select a new or empty output directory to preserve existing analysis models.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".ibra-export-", dir=dest.parent))
    try:
        for name, content in payload.items():
            if name == "project.ibra-project.json":
                write_project(stage/name, content)
            else:
                write_json(stage/name, content)
        templates = Path(__file__).parent/"runtime"
        for name in ("MainKratos.py", "ibra_case_process.py", "ibra_amplitude.py"):
            shutil.copy2(templates/name, stage/name)
        if dest.exists():
            dest.rmdir()  # Only the verified empty destination.
        stage.rename(dest)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return payload["export_report.json"]
