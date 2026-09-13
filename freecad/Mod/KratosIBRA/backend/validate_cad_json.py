"""Structural CAD JSON checks, independent of the converter and Kratos runtime.

Checks finite values, NURBS dimensions, unique IDs and face/trim references.
These checks do not establish physical coupling correctness or Kratos compatibility.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))

    def finite(value):
        if isinstance(value, float):
            require(math.isfinite(value), f"Non-finite number in {path}")
        elif isinstance(value, list):
            for item in value:
                finite(item)
        elif isinstance(value, dict):
            for item in value.values():
                finite(item)

    finite(data)
    require(data["version_number"] == 1, "Unexpected CAD JSON version")
    require(data["tolerances"]["model_tolerance"] > 0, "Invalid model tolerance")
    brep_ids, point_ids = set(), set()

    def register(record):
        identity = record["brep_id"]
        require(identity not in brep_ids, f"Duplicate brep_id {identity}")
        brep_ids.add(identity)

    def points(record):
        values = record["control_points"]
        require(bool(values), "Empty control points")
        for identity, coordinates in values:
            require(identity not in point_ids, f"Duplicate control-point ID {identity}")
            point_ids.add(identity)
            require(len(coordinates) == 4, "Expected x,y,z,weight")
            require(coordinates[3] > 0, "Non-positive NURBS weight")
        return len(values)

    def knots(values, degree):
        require(isinstance(degree, int) and degree >= 1, "Invalid degree")
        require(len(values) >= 2 * (degree + 1), "Knot vector too short")
        require(all(a <= b for a, b in zip(values, values[1:])), "Unsorted knots")
        return len(values) - degree - 1

    def curve(record):
        degree = record["degree"]
        values = record["knot_vector"]
        require(points(record) == knots(values, degree), "Curve pole/knot count mismatch")
        require(len(record["active_range"]) == 2, "Invalid active range")
        low, high = sorted(record["active_range"])
        epsilon = 1e-7 * max(1, abs(low), abs(high))
        require(low >= values[degree] - epsilon and high <= values[-degree - 1] + epsilon,
                "Active range outside NURBS domain")

    faces, trims = {}, {}
    edges = []
    for brep in data["breps"]:
        register(brep)
        for face in brep["faces"]:
            register(face)
            identity = face["brep_id"]
            faces[identity] = face
            surface = face["surface"]
            require(len(surface["degrees"]) == len(surface["knot_vectors"]) == 2, "Invalid surface dimensions")
            dimensions = [knots(k, d) for k, d in zip(surface["knot_vectors"], surface["degrees"])]
            require(points(surface) == math.prod(dimensions), "Surface pole/knot count mismatch")
            trim_records = list(face.get("embedded_edges", []))
            for loop in face["boundary_loops"] + face.get("embedded_loops", []):
                trim_records.extend(loop["trimming_curves"])
            trims[identity] = set()
            for trim in trim_records:
                index = trim["trim_index"]
                require(index not in trims[identity], f"Duplicate trim {identity}/{index}")
                trims[identity].add(index)
                curve(trim["parameter_curve"])
        for edge in brep["edges"]:
            register(edge)
            curve(edge["3d_curve"])
            edges.append(edge)
        for vertex in brep.get("vertices", []):
            register(vertex)
    require(bool(faces), "No faces exported")
    for edge in edges:
        topology = edge["topology"]
        require(len(topology) in (1, 2), "Expected one-sided or two-sided edge")
        for side in topology:
            identity = side["brep_id"]
            require(identity in faces, f"Missing face {identity}")
            require(side["trim_index"] in trims[identity], f"Missing trim on face {identity}")
        if len(topology) == 2:
            require(topology[0]["brep_id"] != topology[1]["brep_id"], "Coupling references the same face twice")
    return {"file": path.name, "breps": len(data["breps"]), "faces": len(faces),
            "edges": len(edges), "couplings": sum(len(e["topology"]) == 2 for e in edges),
            "control_points": len(point_ids), "structural_validation": "passed"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    for filename in parser.parse_args().files:
        print(json.dumps(validate(filename), ensure_ascii=False))
