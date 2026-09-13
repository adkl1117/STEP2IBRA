"""Build a CAD model from an OCCT shape."""
from __future__ import annotations

from OCP.BRepTools import BRepTools
from OCP.TopAbs import TopAbs_FACE, TopAbs_FORWARD, TopAbs_REVERSED, TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from typing import Any

from .config import ConversionOptions
from .selection import prepare_analysis_faces
from .coupling import add_curve_couplings
from .geometry import (
    edge_3d_curve_to_json,
    edge_pcurve_to_json,
    face_surface_record,
    ordered_edges,
    orient_parameter_curve_to_wire,
    surface_to_json,
)
from .pcurves import PcurveMatcher

def build_cad_json(
    shape: Any, matcher: PcurveMatcher | None = None, *,
    options: ConversionOptions = ConversionOptions(), report: dict | None = None,
) -> dict[str, Any]:
    report = report if report is not None else {}
    matcher = matcher if matcher is not None else PcurveMatcher()
    cad_json = {
        "tolerances": {"model_tolerance": 0.001},
        "version_number": 1,
        "breps": [],
    }

    next_brep_id = 1
    next_point_id = 1
    face_records = []

    face_explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while face_explorer.More():
        face = TopoDS.Face_s(face_explorer.Current())

        face_container_brep_id = next_brep_id
        next_brep_id += 1

        face_brep_id = next_brep_id
        next_brep_id += 1
        face_records.append(face_surface_record(face, face_brep_id, options.tolerance))

        face_brep = {
            "brep_id": face_container_brep_id,
            "faces": [],
            "edges": [],
            "vertices": [],
        }

        surface_data, next_point_id = surface_to_json(face, next_point_id)
        face_json = {
            "brep_id": face_brep_id,
            "swapped_surface_normal": face.Orientation() == TopAbs_REVERSED,
            "surface": surface_data,
            "boundary_loops": [],
            "embedded_loops": [],
            "embedded_edges": [],
            "embedded_points": [],
        }

        outer_wire = BRepTools.OuterWire_s(face)
        trim_index = 0
        pending_edges = []

        wire_explorer = TopExp_Explorer(face, TopAbs_WIRE)
        while wire_explorer.More():
            wire = TopoDS.Wire_s(wire_explorer.Current())
            loop_type = "outer" if wire.IsSame(outer_wire) else "inner"
            loop_json = {"loop_type": loop_type, "trimming_curves": []}

            for edge in ordered_edges(wire, face):
                pcurve_data, next_point_id = edge_pcurve_to_json(edge, face, next_point_id, matcher)
                pcurve_data = orient_parameter_curve_to_wire(pcurve_data, edge)

                loop_json["trimming_curves"].append(
                    {
                        "trim_index": trim_index,
                        # The parameter curve is physically oriented along the
                        # wire because some CAD readers do not apply this flag.
                        "curve_direction": True,
                        "parameter_curve": pcurve_data,
                    }
                )

                pending_edges.append(
                    (
                        edge,
                        next_brep_id,
                        face_brep_id,
                        trim_index,
                        edge.Orientation() == TopAbs_FORWARD,
                    )
                )
                next_brep_id += 1
                trim_index += 1

            face_json["boundary_loops"].append(loop_json)
            wire_explorer.Next()

        for edge, edge_brep_id, topology_face_brep_id, topology_trim_index, relative_direction in pending_edges:
            edge_curve_data, next_point_id = edge_3d_curve_to_json(edge, next_point_id)
            face_brep["edges"].append(
                {
                    "brep_id": edge_brep_id,
                    "3d_curve": edge_curve_data,
                    "topology": [
                        {
                            "brep_id": topology_face_brep_id,
                            "trim_index": topology_trim_index,
                            "relative_direction": relative_direction,
                        }
                    ],
                }
            )

        face_brep["faces"].append(face_json)
        cad_json["breps"].append(face_brep)
        face_explorer.Next()

    report['input_face_count'] = len(face_records)
    report['faces'] = [{'face_id': r['face_brep_id'], 'bounds': r['tight_aabb']} for r in face_records]
    selected = prepare_analysis_faces(face_records, options, report)
    selected_ids = {r['face_brep_id'] for r in selected}
    # Serialize the full traversal first: retain stable IDs and raw-pcurve matching
    # order even when a profile excludes faces. Only selected faces are exported.
    cad_json['breps'] = [b for b in cad_json['breps'] if b['faces'][0]['brep_id'] in selected_ids]
    report['output_face_count'] = len(selected)
    report['pcurves'] = {**matcher.stats, 'unused': len(matcher.records)-len(matcher.used)}
    next_brep_id, next_point_id = add_curve_couplings(
        cad_json, selected, next_brep_id, next_point_id, options=options, report=report,
    )
    return cad_json
