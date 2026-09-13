"""STEP entity parsing and OCCT import."""
from __future__ import annotations

from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from pathlib import Path
from typing import Any
import re

from .numeric import clean

def read_step_file(step_path: Path) -> Any:
    reader = STEPControl_Reader()
    status = reader.ReadFile(str(step_path))
    if status != IFSelect_RetDone:
        raise RuntimeError(f"Cannot read STEP file: {step_path}")
    reader.TransferRoots()
    return reader.OneShape()


def split_step_args(text: str) -> list[str]:
    args = []
    current = []
    depth = 0
    in_string = False
    i = 0
    while i < len(text):
        char = text[i]
        if char == "'":
            in_string = not in_string
        elif not in_string:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "," and depth == 0:
                args.append("".join(current).strip())
                current = []
                i += 1
                continue
        current.append(char)
        i += 1
    if current:
        args.append("".join(current).strip())
    return args


def parse_step_number_list(text: str) -> list[float]:
    return [float(value) for value in re.findall(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[Ee][-+]?\d+)?", text)]


def parenthesized_content(text: str, open_index: int) -> str:
    depth = 0
    in_string = False
    content = []
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "'":
            in_string = not in_string
        if not in_string:
            if char == "(":
                depth += 1
                if depth == 1:
                    continue
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return "".join(content)
        if depth >= 1:
            content.append(char)
    return "".join(content)


def entity_call_args(entity: str, call_name: str) -> list[str]:
    start = entity.find(call_name)
    if start < 0:
        return []
    open_index = entity.find("(", start + len(call_name))
    if open_index < 0:
        return []
    return split_step_args(parenthesized_content(entity, open_index))


def step_refs(text: str) -> list[int]:
    return [int(value) for value in re.findall(r"#(\d+)", text)]


def step_entities(step_path: Path) -> dict[int, str]:
    text = step_path.read_text(encoding="utf-8", errors="ignore")
    return {
        int(match.group(1)): match.group(2).strip()
        for match in re.finditer(r"#(\d+)\s*=\s*(.*?);", text, re.S)
    }


def parse_step_points(entities: dict[int, str]) -> dict[int, list[float]]:
    points: dict[int, list[float]] = {}
    for entity_id, entity in entities.items():
        if not entity.startswith("CARTESIAN_POINT"):
            continue
        match = re.search(r"CARTESIAN_POINT\s*\(\s*''\s*,\s*\((.*?)\)\s*\)", entity, re.S)
        if match:
            points[entity_id] = parse_step_number_list(match.group(1))
    return points


def parse_step_directions(entities: dict[int, str]) -> dict[int, list[float]]:
    directions: dict[int, list[float]] = {}
    for entity_id, entity in entities.items():
        if not entity.startswith("DIRECTION"):
            continue
        args = entity_call_args(entity, "DIRECTION")
        if len(args) >= 2:
            directions[entity_id] = parse_step_number_list(args[1])
    return directions


def parse_step_vectors(
        entities: dict[int, str],
        directions: dict[int, list[float]],
) -> dict[int, list[float]]:
    vectors: dict[int, list[float]] = {}
    for entity_id, entity in entities.items():
        if not entity.startswith("VECTOR"):
            continue
        args = entity_call_args(entity, "VECTOR")
        if len(args) < 3:
            continue
        refs = step_refs(args[1])
        if not refs:
            continue
        direction = directions.get(refs[0])
        numbers = parse_step_number_list(args[2])
        if direction is None or not numbers:
            continue
        magnitude = numbers[0]
        vectors[entity_id] = [clean(value * magnitude) for value in direction]
    return vectors


def parse_raw_bspline_curve(entity_id: int, entity: str, points: dict[int, list[float]]) -> dict[str, Any] | None:
    weights: list[float] | None = None
    if entity.startswith("B_SPLINE_CURVE_WITH_KNOTS"):
        args = entity_call_args(entity, "B_SPLINE_CURVE_WITH_KNOTS")
        if len(args) < 8:
            return None
        degree = int(parse_step_number_list(args[1])[0])
        point_ids = step_refs(args[2])
        multiplicities = [int(value) for value in parse_step_number_list(args[6])]
        knots = [clean(value) for value in parse_step_number_list(args[7])]
    elif "B_SPLINE_CURVE(" in entity and "B_SPLINE_CURVE_WITH_KNOTS" in entity:
        curve_args = entity_call_args(entity, "B_SPLINE_CURVE")
        knot_args = entity_call_args(entity, "B_SPLINE_CURVE_WITH_KNOTS")
        if len(curve_args) < 2 or len(knot_args) < 2:
            return None
        degree = int(parse_step_number_list(curve_args[0])[0])
        point_ids = step_refs(curve_args[1])
        multiplicities = [int(value) for value in parse_step_number_list(knot_args[0])]
        knots = [clean(value) for value in parse_step_number_list(knot_args[1])]
        rational_args = entity_call_args(entity, "RATIONAL_B_SPLINE_CURVE")
        if rational_args:
            weights = [clean(value) for value in parse_step_number_list(rational_args[0])]
    else:
        return None

    if not point_ids or not multiplicities or not knots:
        return None

    expanded_knots = []
    for knot, multiplicity in zip(knots, multiplicities):
        expanded_knots += [knot] * multiplicity
    if not expanded_knots:
        return None

    control_points = []
    for index, point_id in enumerate(point_ids):
        point = points.get(point_id)
        if point is None or len(point) < 2:
            return None
        weight = weights[index] if weights is not None and index < len(weights) else 1.0
        control_points.append([clean(point[0]), clean(point[1]), 0.0, clean(weight)])

    return {
        "source": "raw_step",
        "kind": "bspline",
        "entity_id": entity_id,
        "is_rational": bool(weights),
        "degree": degree,
        "knot_vector": expanded_knots,
        "active_range": [expanded_knots[0], expanded_knots[-1]],
        "control_points": control_points,
    }


def parse_raw_line_curve(
        entity_id: int,
        entity: str,
        points: dict[int, list[float]],
        vectors: dict[int, list[float]],
) -> dict[str, Any] | None:
    if not entity.startswith("LINE"):
        return None
    args = entity_call_args(entity, "LINE")
    if len(args) < 3:
        return None
    point_refs = step_refs(args[1])
    vector_refs = step_refs(args[2])
    if not point_refs or not vector_refs:
        return None
    point = points.get(point_refs[0])
    vector = vectors.get(vector_refs[0])
    if point is None or vector is None or len(point) < 2 or len(vector) < 2:
        return None
    return {
        "source": "raw_step",
        "kind": "line",
        "entity_id": entity_id,
        "is_rational": False,
        "degree": 1,
        "line_point": [clean(point[0]), clean(point[1])],
        "line_direction": [clean(vector[0]), clean(vector[1])],
    }


def parse_step_curve_records(entities: dict[int, str]) -> dict[int, dict[str, Any]]:
    points = parse_step_points(entities)
    directions = parse_step_directions(entities)
    vectors = parse_step_vectors(entities, directions)
    curves: dict[int, dict[str, Any]] = {}
    for entity_id, entity in entities.items():
        curve = parse_raw_bspline_curve(entity_id, entity, points)
        if curve is None:
            curve = parse_raw_line_curve(entity_id, entity, points, vectors)
        if curve is not None:
            curves[entity_id] = curve
    return curves


def pcurve_surface_and_curve_ids(
        pcurve_id: int,
        entities: dict[int, str],
) -> tuple[int | None, int | None]:
    pcurve_entity = entities.get(pcurve_id, "")
    if not pcurve_entity.startswith("PCURVE"):
        return None, None
    args = entity_call_args(pcurve_entity, "PCURVE")
    if len(args) < 3:
        return None, None
    surface_refs = step_refs(args[1])
    representation_refs = step_refs(args[2])
    if not surface_refs or not representation_refs:
        return None, None
    representation = entities.get(representation_refs[0], "")
    representation_args = entity_call_args(representation, "DEFINITIONAL_REPRESENTATION")
    if len(representation_args) < 2:
        return surface_refs[0], None
    curve_refs = step_refs(representation_args[1])
    return surface_refs[0], curve_refs[0] if curve_refs else None


def parse_step_raw_pcurves(step_path: Path) -> list[dict[str, Any]]:
    entities = step_entities(step_path)
    curves = parse_step_curve_records(entities)
    pcurve_to_surface: dict[int, int] = {}
    pcurve_to_raw: dict[int, dict[str, Any]] = {}
    for entity_id, entity in entities.items():
        if not entity.startswith("PCURVE"):
            continue
        surface_id, curve_id = pcurve_surface_and_curve_ids(entity_id, entities)
        if surface_id is None or curve_id is None:
            continue
        curve = curves.get(curve_id)
        if curve is None:
            continue
        record = dict(curve)
        record["pcurve_id"] = entity_id
        record["surface_id"] = surface_id
        record["curve_id"] = curve_id
        pcurve_to_surface[entity_id] = surface_id
        pcurve_to_raw[entity_id] = record

    topology_ordered: list[dict[str, Any]] = []
    for face_id, face_entity in sorted(entities.items()):
        if not face_entity.startswith("ADVANCED_FACE"):
            continue
        face_args = entity_call_args(face_entity, "ADVANCED_FACE")
        if len(face_args) < 3:
            continue
        bound_ids = step_refs(face_args[1])
        surface_refs = step_refs(face_args[2])
        if not surface_refs:
            continue
        surface_id = surface_refs[0]
        for loop_index, bound_id in enumerate(bound_ids):
            bound_entity = entities.get(bound_id, "")
            bound_args = (
                entity_call_args(bound_entity, "FACE_OUTER_BOUND")
                if bound_entity.startswith("FACE_OUTER_BOUND")
                else entity_call_args(bound_entity, "FACE_BOUND")
            )
            if len(bound_args) < 2:
                continue
            loop_refs = step_refs(bound_args[1])
            if not loop_refs:
                continue
            loop_entity = entities.get(loop_refs[0], "")
            loop_args = entity_call_args(loop_entity, "EDGE_LOOP")
            if len(loop_args) < 2:
                continue
            oriented_edge_ids = step_refs(loop_args[1])
            for edge_index, oriented_edge_id in enumerate(oriented_edge_ids):
                oriented_entity = entities.get(oriented_edge_id, "")
                oriented_args = entity_call_args(oriented_entity, "ORIENTED_EDGE")
                if len(oriented_args) < 4:
                    continue
                edge_curve_refs = step_refs(oriented_args[3])
                if not edge_curve_refs:
                    continue
                edge_curve_entity = entities.get(edge_curve_refs[0], "")
                edge_curve_args = entity_call_args(edge_curve_entity, "EDGE_CURVE")
                if len(edge_curve_args) < 4:
                    continue
                curve_refs = step_refs(edge_curve_args[3])
                if not curve_refs:
                    continue
                surface_curve_entity = entities.get(curve_refs[0], "")
                surface_curve_args = entity_call_args(surface_curve_entity, "SURFACE_CURVE")
                if len(surface_curve_args) < 3:
                    continue
                candidate_pcurve_ids = step_refs(surface_curve_args[2])
                raw_record = None
                for pcurve_id in candidate_pcurve_ids:
                    if pcurve_to_surface.get(pcurve_id) == surface_id:
                        raw_record = pcurve_to_raw.get(pcurve_id)
                        break
                if raw_record is None:
                    for pcurve_id in candidate_pcurve_ids:
                        raw_record = pcurve_to_raw.get(pcurve_id)
                        if raw_record is not None:
                            break
                if raw_record is None:
                    continue
                record = dict(raw_record)
                record["advanced_face_id"] = face_id
                record["face_surface_id"] = surface_id
                record["bound_id"] = bound_id
                record["loop_index"] = loop_index
                record["oriented_edge_id"] = oriented_edge_id
                record["edge_index"] = edge_index
                topology_ordered.append(record)

    if topology_ordered:
        return topology_ordered
    return [record for _, record in sorted(pcurve_to_raw.items())]
