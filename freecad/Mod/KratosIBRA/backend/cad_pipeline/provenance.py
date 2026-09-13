"""Best-effort STEP source mapping, verified by surface poles rather than order."""
from __future__ import annotations

from OCP.BRep import BRep_Tool
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

from .step_io import entity_call_args, parse_step_points, step_entities, step_refs


def annotate_source_faces(step_path, shape, report: dict) -> None:
    entities = step_entities(step_path)
    points = parse_step_points(entities)
    surfaces, faces = {}, {}
    for identity, entity in entities.items():
        if entity.startswith('B_SPLINE_SURFACE_WITH_KNOTS'):
            args = entity_call_args(entity, 'B_SPLINE_SURFACE_WITH_KNOTS')
            references = step_refs(args[3])
            if all(p in points for p in references):
                key = tuple(tuple(round(x, 9) for x in points[p]) for p in references)
                surfaces.setdefault(key, []).append(identity)
        elif entity.startswith('ADVANCED_FACE'):
            args = entity_call_args(entity, 'ADVANCED_FACE')
            for surface in step_refs(args[2]):
                faces.setdefault(surface, []).append(identity)
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    for record in report['faces']:
        surface = BRep_Tool.Surface_s(TopoDS.Face_s(explorer.Current()))
        explorer.Next()
        source_ids = []
        if hasattr(surface, 'NbUPoles'):
            poles = [surface.Pole(u, v) for u in range(1, surface.NbUPoles()+1)
                     for v in range(1, surface.NbVPoles()+1)]
            key = tuple(tuple(round(x, 9) for x in (p.X(), p.Y(), p.Z())) for p in poles)
            source_ids = surfaces.get(key, [])
        record['step_surface_candidates'] = source_ids
        record['step_face_candidates'] = [f for s in source_ids for f in faces.get(s, [])]
    # Empty/multiple candidate lists are preserved: do not invent source IDs for
    # analytic surfaces, transformed instances, or identical support surfaces.
