"""Choose analysis faces and distinguish shared curves from area overlap."""
from __future__ import annotations

from typing import Any

from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopTools import TopTools_ListOfShape

from .config import ConversionOptions
from .geometry import aabb_overlap


class OverlappingFacesError(ValueError):
    """A positive-area intersection needs an explicit model decision."""


def surface_area(shape: Any) -> float:
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, props)
    return abs(float(props.Mass()))


def boundary_length(shape: Any) -> float:
    props = GProp_GProps()
    BRepGProp.LinearProperties_s(shape, props)
    return abs(float(props.Mass()))


def common_surface_area(left: Any, right: Any) -> float:
    """Compute the area of actual trimmed faces without modifying either input."""
    arguments, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    arguments.Append(left)
    tools.Append(right)
    operation = BRepAlgoAPI_Common()
    operation.SetArguments(arguments)
    operation.SetTools(tools)
    operation.SetNonDestructive(True)
    operation.Build()
    if not operation.IsDone():
        raise RuntimeError('OCCT could not determine face overlap; no coupling was written.')
    return surface_area(operation.Shape())


def prepare_analysis_faces(
    records: list[dict[str, Any]], options: ConversionOptions, report: dict,
) -> list[dict[str, Any]]:
    """Keep deterministic representatives; reject ambiguous partial overlaps.

    Face IDs refer to the full STEP traversal, so removing a face does not shift
    the IDs used in profiles or diagnostic reports. Merely touching along a line
    has zero common area and is retained, including coplanar patch connections.
    """
    missing = set(options.exclude_face_ids) - {r['face_brep_id'] for r in records}
    if missing:
        raise ValueError(f'Excluded face IDs do not exist: {sorted(missing)}')
    selected = []
    report['excluded_faces'] = []
    report['duplicate_faces'] = []
    report['overlap_checks'] = 0
    report['tolerated_overlap_slivers'] = []
    for record in records:
        identity = record['face_brep_id']
        reason = 'explicit_face_id' if identity in options.exclude_face_ids else None
        for plane in options.exclude_planes:
            axis = 'xyz'.index(plane.axis)
            bounds = record['tight_aabb']
            if max(abs(bounds[axis] - plane.coordinate), abs(bounds[axis+3] - plane.coordinate)) <= plane.tolerance:
                reason = f'plane {plane.axis}={plane.coordinate:g}'
                break
        if reason:
            report['excluded_faces'].append({'face_id': identity, 'reason': reason})
        else:
            record['area'] = surface_area(record['face'])
            record['perimeter'] = boundary_length(record['face'])
            if record['area'] <= 1.0e-12:
                raise ValueError(f'Face {identity} has zero or negligible area.')
            selected.append(record)
    if not selected:
        raise ValueError('Face selection removed every face.')
    if options.overlap_policy == 'allow':
        return selected

    representatives = []
    for record in selected:
        duplicate = False
        for kept in representatives:
            if not aabb_overlap(record['aabb'], kept['aabb']):
                continue
            report['overlap_checks'] += 1
            common = common_surface_area(kept['face'], record['face'])
            minimum_area = min(record['area'], kept['area'])
            # Adjacent STEP patches can have thin overlap strips at rounded trim
            # boundaries. Convert the linear tolerance to area using perimeter;
            # a fixed relative-area cutoff alone rejects these valid seams.
            area_tolerance = max(
                1.0e-12, options.overlap_relative_area_tolerance * minimum_area,
                options.tolerance * min(record['perimeter'], kept['perimeter']),
            )
            if common <= area_tolerance:
                if common > 1.0e-12:
                    report['tolerated_overlap_slivers'].append({
                        'face_ids': [kept['face_brep_id'], record['face_brep_id']],
                        'common_area': common, 'area_tolerance': area_tolerance,
                    })
                continue
            detail = {'kept_face_id': kept['face_brep_id'], 'removed_face_id': record['face_brep_id'],
                      'common_area': common, 'kept_area': kept['area'], 'removed_area': record['area']}
            equal_domains = all(abs(area-common) <= options.duplicate_relative_area_tolerance * area
                                for area in (record['area'], kept['area']))
            if equal_domains and options.overlap_policy == 'merge_duplicates':
                report['duplicate_faces'].append(detail)
                duplicate = True
                break
            raise OverlappingFacesError(
                f"Faces {kept['face_brep_id']} and {record['face_brep_id']} overlap over area {common:.9g}. "
                'Choose the intended analysis faces; an area overlap is not a curve coupling.'
            )
        if not duplicate:
            representatives.append(record)
    return representatives
