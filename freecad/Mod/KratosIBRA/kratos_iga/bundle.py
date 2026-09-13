"""Translate GUI intent into the native Kratos modeler/process contracts."""
from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
from .project import validate, strong_location, write_json, write_project

ROOT = "IgaModelPart"


def process(module, name, params):
    result = {"python_module": name, "Parameters": params}
    if module:
        result["kratos_module"] = module
    return result


def make_bundle(project, cad, index):
    errors, warnings = validate(project, index)
    if errors:
        raise ValueError("\n".join(errors))
    physics, refinements, materials = [], [], []
    processes = {"initial_conditions_process_list": [], "constraints_process_list": [], "loads_process_list": []}
    face_map = {f["id"]: f for f in index["faces"]}
    mat_map = {m["id"]: m for m in project["materials"]}
    def unit(ids, kind, name, params):
        physics.append({"brep_ids": ids, "geometry_type": kind, "iga_model_part": name, "parameters": params})
    def prop(name, variables, law=None):
        mat = {"Variables": variables, "Tables": {}}
        if law:
            mat.update({"name": law["name"], "constitutive_law": {"name": "LinearElasticPlaneStress2DLaw"}})
        materials.append({"model_part_name": ROOT+"."+name, "properties_id": len(materials)+1, "Material": mat})
    for patch in project["patches"]:
        fid = patch["face_id"]
        params = {"type": "element", "name": patch["element"], "shape_function_derivatives_order": 3}
        if patch["quadrature"]:
            params["number_of_integration_points_per_span"] = patch["quadrature"]
        unit([fid], "GeometrySurface", f"Patch_{fid}", params)
        material = mat_map[patch["material_id"]]
        prop(f"Patch_{fid}", {"YOUNG_MODULUS": material["young"], "POISSON_RATIO": material["poisson"],
              "DENSITY": material["density"], "THICKNESS": material["thickness"], "PRESTRESS": [0., 0., 0.]}, material)
        refinements.append({"brep_ids": [fid], "geometry_type": "NurbsSurface", "model_part_name": ROOT,
            "parameters": {"increase_degree_u": patch["degree_u"]-face_map[fid]["degrees"][0],
                "increase_degree_v": patch["degree_v"]-face_map[fid]["degrees"][1],
                "insert_nb_per_span_u": patch["insert_u"], "insert_nb_per_span_v": patch["insert_v"]}})
    for i, support in enumerate(project["supports"], 1):
        name = f"Support_{i}"
        values = support["value"]
        if support["method"] == "strong":
            fid, uv = strong_location(support, index)
            unit([fid], "GeometrySurfaceNodes", name, {"local_parameters": uv})
            if support.get("clamp"):
                unit([fid], "GeometrySurfaceVariationNodes", name, {"local_parameters": uv})
            processes["constraints_process_list"].append(process("KratosMultiphysics", "assign_vector_variable_process",
                {"model_part_name": ROOT+"."+name, "variable_name": "DISPLACEMENT", "value": values,
                 "constrained": [v is not None for v in values], "interval": support["interval"]}))
        else:
            unit([support["target_id"]], "SurfaceEdge", name, {"type": "condition", "name": "SupportPenaltyCondition", "shape_function_derivatives_order": 2})
            prop(name, {"PENALTY_FACTOR": support["penalty"]})
            processes["constraints_process_list"].append(process("KratosMultiphysics", "assign_vector_variable_to_conditions_process",
                {"model_part_name": ROOT+"."+name, "variable_name": "DISPLACEMENT", "value": values, "interval": support["interval"]}))
    for i, load in enumerate(project["loads"], 1):
        name = f"Load_{i}"
        unit([load["target_id"]], "GeometrySurface" if load["target_kind"] == "face" else "SurfaceEdge", name,
             {"type": "condition", "name": "LoadCondition", "shape_function_derivatives_order": 2})
        prop(name, {})
        # This process explicitly clears the load outside its interval.
        processes["loads_process_list"].append(process("", "ibra_case_process",
            {"mode": "load", "model_part_name": ROOT+"."+name, "variable_name": load["variable"], "value": load["value"], "interval": load["interval"]}))
    for i, initial in enumerate(project["initial_conditions"], 1):
        name = f"Initial_{i}"
        unit([initial["target_id"]], "GeometrySurfaceNodes", name, {"local_parameters": [-1, -1]})
        processes["initial_conditions_process_list"].append(process("", "ibra_case_process",
            {"mode": "initial", "model_part_name": ROOT+"."+name, "variable_name": initial["variable"], "value": initial["value"]}))
    for coupling in project["couplings"]:
        if not coupling["enabled"]:
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
    if project["output"]["vtk"]:
        params["output_processes"]["output_process_list"].append(process("KratosMultiphysics.IgaApplication", "iga_vtk_output_process",
            {"model_part_name": ROOT, "output_file_name": "results", "brep_surface_ids": list(face_map),
             "nodal_solution_step_data_variables": ["DISPLACEMENT"], "output_refinement": [project["output"]["refinement"]]*2,
             "output_control_type": "step", "output_frequency": 1}))
        warnings.append("Kratos 10.4.3 VTK output samples the background surface. Verify the visualization of trimming holes independently; the display mesh does not establish the integration domain.")
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
            "export_report.json": {"errors": [], "warnings": warnings, "runtime_target": "Kratos 10.4.3",
                "source_sha256": project["source_sha256"], "geometry_units": "m" if project["units"] == "m-N-s" else "mm",
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
        for name in ("MainKratos.py", "ibra_case_process.py"):
            shutil.copy2(templates/name, stage/name)
        if dest.exists():
            dest.rmdir()  # Only the verified empty destination.
        stage.rename(dest)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return payload["export_report.json"]
