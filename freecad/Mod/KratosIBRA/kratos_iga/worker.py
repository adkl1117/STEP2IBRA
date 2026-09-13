"""Run with the OCP virtualenv; never import OCP in FreeCAD itself."""
import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kratos_iga.project import read_json, write_json


def surface_point(surface, uv):
    import numpy as np
    from cad_pipeline.nurbs import find_span, basis_functions
    p, q = surface["degrees"]
    ku, kv = surface["knot_vectors"]
    nu, nv = len(ku)-p-1, len(kv)-q-1
    su, sv = find_span(nu-1, p, uv[0], ku), find_span(nv-1, q, uv[1], kv)
    bu, bv = basis_functions(su, uv[0], p, ku), basis_functions(sv, uv[1], q, kv)
    total, weight = np.zeros(3), 0.
    for j in range(q+1):
        for i in range(p+1):
            cp = surface["control_points"][(sv-q+j)*nu + su-p+i][1]
            w = float(bu[i]*bv[j]*cp[3])
            total += np.asarray(cp[:3])*w
            weight += w
    return (total/weight).tolist()


def build_index(cad, report, folder):
    import numpy as np
    from cad_pipeline.nurbs import evaluate_curve
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from cad_pipeline.step_io import read_step_file
    # Re-read using the SAME reader and traversal as the converter, tying IDs to
    # original OCCT faces. FreeCAD import order / FaceN is never used as an ID.
    shape = read_step_file(Path(report["input"]))
    traversal = TopExp_Explorer(shape, TopAbs_FACE)
    face_objects = {}
    for record in report["faces"]:
        if not traversal.More():
            raise ValueError("STEP traversal no longer agrees with conversion report")
        face_objects[record["face_id"]] = traversal.Current()
        traversal.Next()
    if traversal.More():
        raise ValueError("STEP face count changed")
    faces = {f["brep_id"]: f for b in cad["breps"] for f in b["faces"]}
    index = {"source_sha256": report["input_sha256"],
             "geometry_sha256": hashlib.sha256(json.dumps(cad, sort_keys=True).encode()).hexdigest(), "faces": [], "edges": []}
    for fid, face in faces.items():
        filename = f"face_{fid}.brep"
        if not BRepTools.Write_s(face_objects[fid], str(folder/filename)):
            raise RuntimeError("Cannot write OCCT BREP")
        surface = face["surface"]
        ku, kv = surface["knot_vectors"]
        index["faces"].append({"id": fid, "brep_file": filename, "degrees": surface["degrees"],
            "min_continuity": [min((degree-count for knot, count in Counter(knots).items() if knots[0] < knot < knots[-1]), default=99)
                               for degree, knots in zip(surface["degrees"], surface["knot_vectors"])],
            "control_points": len(surface["control_points"]),
            "corners": {key: surface_point(surface, uv) for key, uv in {
                "00": [ku[0], kv[0]], "10": [ku[-1], kv[0]], "01": [ku[0], kv[-1]], "11": [ku[-1], kv[-1]]}.items()}})
    for brep in cad["breps"]:
        for edge in brep["edges"]:
            topology = edge["topology"]
            side = topology[0]
            face = faces[side["brep_id"]]
            curves = [c for loop in face["boundary_loops"] for c in loop["trimming_curves"]] + face.get("embedded_edges", [])
            curve_record = next((c for c in curves if c["trim_index"] == side["trim_index"]), None)
            if curve_record is None:
                # Converter stores generated coupling trims in embedded loops.
                curves += [c for loop in face.get("embedded_loops", []) for c in loop.get("trimming_curves", [])]
                curve_record = next(c for c in curves if c["trim_index"] == side["trim_index"])
            curve = curve_record["parameter_curve"]
            a, b = curve["active_range"]
            uv = [evaluate_curve(curve, float(t)) for t in np.linspace(a, b, 41)]
            points = [surface_point(face["surface"], pair) for pair in uv]
            ku, kv = face["surface"]["knot_vectors"]
            strong = None
            # Convex-hull test on ALL rational curve poles: not a sampled guess.
            poles = [c[1] for c in curve["control_points"]]
            for axis, knot, other in ((0, ku, kv), (1, kv, ku)):
                eps = max(1., abs(knot[-1]-knot[0]))*1e-9
                for end in (0, 1):
                    constant = knot[0] if end == 0 else knot[-1]
                    if all(abs(cp[axis]-constant) < eps for cp in poles):
                        extent = sorted([uv[0][1-axis], uv[-1][1-axis]])
                        if abs(extent[0]-other[0]) < eps and abs(extent[1]-other[-1]) < eps:
                            strong = [end, -1] if axis == 0 else [-1, end]
            index["edges"].append({"id": edge["brep_id"], "kind": "coupling" if len(topology) == 2 else "edge",
                "face_ids": [s["brep_id"] for s in topology], "points": points,
                "strong_local": strong if len(topology) == 1 else None})
    return index


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("request")
    args = parser.parse_args()
    request = read_json(args.request)
    pipeline = Path(request["pipeline_root"]).resolve()
    if not (pipeline/"cad_pipeline"/"api.py").is_file():
        raise ValueError("Select the conversion pipeline directory containing cad_pipeline.")
    sys.path.insert(0, str(pipeline))
    action = request["action"]
    if action == "convert":
        from cad_pipeline.api import convert_step
        from cad_pipeline.config import ConversionOptions
        source = Path(request["source_step"]).resolve(strict=True)
        if source.suffix.lower() not in (".step", ".stp"):
            raise ValueError("Only STEP/STP input is supported")
        options = ConversionOptions.from_file(Path(request["profile"])) if request.get("profile") else ConversionOptions()
        folder = Path(request["cache_dir"])
        folder.mkdir(parents=True, exist_ok=True)
        cad, report = convert_step(source, options)
        index = build_index(cad, report, folder)
        if hashlib.sha256(source.read_bytes()).hexdigest() != report["input_sha256"]:
            raise ValueError("The STEP file changed during conversion. Reimport the source geometry.")
        write_json(folder/"geometry.cad.json", cad)
        write_json(folder/"geometry.cad.report.json", report)
        write_json(folder/"index.json", index)
        print(f"Converted: {len(index['faces'])} faces, {len(index['edges'])} boundaries/interfaces", flush=True)
    elif action == "export":
        from kratos_iga.bundle import export_bundle
        project = read_json(request["project_file"])
        source = Path(project["source_step"])
        if hashlib.sha256(source.read_bytes()).hexdigest() != project["source_sha256"]:
            raise ValueError("The STEP file has changed. Reimport it before exporting the model.")
        folder = Path(request["cache_dir"])
        report = export_bundle(project, read_json(folder/"geometry.cad.json"), read_json(folder/"index.json"), request["destination"])
        print(json.dumps(report, ensure_ascii=False), flush=True)
    else:
        raise ValueError("Unknown worker action")


if __name__ == "__main__":
    main()
