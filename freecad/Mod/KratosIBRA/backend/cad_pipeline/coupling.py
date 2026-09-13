"""Coordinate candidate detection, deduplication and topology writing."""
from __future__ import annotations

from typing import Any

from .config import ConversionOptions
from .detection import boundary_curve_surface_candidates, curve_coupling_signature, surface_surface_candidates
from .geometry import aabb_overlap
from .topology import find_or_create_shared_brep, write_curve_coupling_as_shared_edge


def add_curve_couplings(
    cad_json: dict[str, Any], face_records: list[dict[str, Any]],
    next_brep_id: int, next_point_id: int, *,
    options: ConversionOptions, report: dict,
) -> tuple[int, int]:
    """Detect only among selected, non-overlapping analysis faces.

    The sidecar report contains provenance; the CAD JSON schema remains the
    format consumed by Kratos. IDs are assigned only after a candidate passes.
    """
    used_signatures = set()
    shared_brep = None
    counts = {'total_pairs': 0, 'skipped_by_aabb': 0, 'exact_check_pairs': 0}
    report['couplings'] = []
    for index, left in enumerate(face_records):
        for right in face_records[index + 1:]:
            counts['total_pairs'] += 1
            if not aabb_overlap(left['aabb'], right['aabb']):
                counts['skipped_by_aabb'] += 1
                continue
            counts['exact_check_pairs'] += 1
            candidates = boundary_curve_surface_candidates(
                left, right, options.tolerance, options.sample_count,
            )
            if not candidates:
                candidates = surface_surface_candidates(
                    left, right, options.tolerance, options.sample_count,
                )
            for candidate in candidates:
                signature = curve_coupling_signature(candidate, options.tolerance)
                if signature in used_signatures:
                    continue
                if shared_brep is None:
                    shared_brep, next_brep_id = find_or_create_shared_brep(cad_json, next_brep_id)
                edge_id = next_brep_id
                next_brep_id, next_point_id, written = write_curve_coupling_as_shared_edge(
                    cad_json, candidate, shared_brep, next_brep_id, next_point_id,
                )
                if not written:
                    continue
                used_signatures.add(signature)
                report['couplings'].append({
                    'edge_id': edge_id,
                    'face_ids': [candidate['face_id_a'], candidate['face_id_b']],
                    'detection_type': candidate['detection_type'],
                    'sample_count': len(candidate['physical_points']),
                    'max_projection_error': candidate['projection_error'],
                    'max_distance_error': candidate['max_distance_error'],
                    'endpoints': [candidate['physical_points'][0], candidate['physical_points'][-1]],
                })
    if shared_brep is not None and not shared_brep['edges']:
        cad_json['breps'].remove(shared_brep)
    report['detection'] = counts
    report['coupling_count'] = len(report['couplings'])
    return next_brep_id, next_point_id
