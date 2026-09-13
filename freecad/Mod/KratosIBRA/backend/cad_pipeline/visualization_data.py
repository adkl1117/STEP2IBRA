"""Validate and select coupling topology without a Kratos dependency."""
from __future__ import annotations


def collect_couplings(cad_json: dict, requested_ids: set[int] | None = None) -> list[dict]:
    requested_ids = requested_ids or set()
    faces = {}
    trims = {}
    for brep in cad_json.get('breps', []):
        for face in brep.get('faces', []):
            identity = face.get('brep_id')
            if type(identity) is not int or identity in faces:
                raise ValueError(f'Invalid or duplicate face ID: {identity}')
            faces[identity] = face
            records = list(face.get('embedded_edges', []))
            for loop in face.get('boundary_loops', []) + face.get('embedded_loops', []):
                records.extend(loop.get('trimming_curves', []))
            indexed = {}
            for record in records:
                index = record.get('trim_index')
                if type(index) is not int or index in indexed or 'parameter_curve' not in record:
                    raise ValueError(f'Invalid or duplicate trim {identity}/{index}')
                indexed[index] = record
            trims[identity] = indexed

    found, couplings = set(), []
    for brep in cad_json.get('breps', []):
        for edge in brep.get('edges', []):
            topology = edge.get('topology', [])
            if len(topology) < 2:
                continue  # Ordinary external boundary; never promote it to coupling.
            identity = edge.get('brep_id')
            if type(identity) is not int or identity in found:
                raise ValueError(f'Invalid or duplicate coupling ID: {identity}')
            if requested_ids and identity not in requested_ids:
                continue
            if len(topology) != 2:
                raise ValueError(f'Coupling {identity} must have exactly two sides.')
            if topology[0].get('brep_id') == topology[1].get('brep_id'):
                raise ValueError(f'Coupling {identity} references the same face twice.')
            sides = []
            for side, entry in enumerate(topology):
                face_id, trim_index = entry.get('brep_id'), entry.get('trim_index')
                if type(face_id) is not int or type(trim_index) is not int:
                    raise ValueError(f'Coupling {identity} has invalid face/trim IDs.')
                if face_id not in trims or trim_index not in trims[face_id]:
                    raise ValueError(f'Coupling {identity} references missing trim {face_id}/{trim_index}.')
                trim = trims[face_id][trim_index]
                sides.append({
                    'side': side, 'face_id': face_id, 'trim_index': trim_index,
                    'relative_direction': bool(entry.get('relative_direction', True)),
                    'curve_direction': bool(trim.get('curve_direction', True)),
                    'parameter_curve': trim['parameter_curve'],
                })
            couplings.append({'coupling_id': identity, 'sides': sides})
            found.add(identity)
    if requested_ids - found:
        raise ValueError(f'Requested coupling IDs are missing: {sorted(requested_ids - found)}')
    if not couplings:
        raise ValueError('No two-sided coupling geometries were found.')
    return couplings
