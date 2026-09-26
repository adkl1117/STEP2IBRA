"""Reusable P0-2 fixtures: real adjacent STEP surfaces through the real converter.

No committed generated cases: every STEP file is written into the test's own
temporary directory. The fixtures build two coplanar, edge-adjacent faces (and a
reversed-parameterisation specimen) so the acceptance path goes through
``cad_pipeline.convert_step`` and ``kratos_iga.worker.build_index``.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "freecad/Mod/KratosIBRA"))
sys.path.insert(0, str(ROOT / "freecad/Mod/KratosIBRA/backend"))

E = 200000.0
NU = 0.0
RHO = 7.85e-9
THICKNESS = 0.1
WIDTH = 1.0
LENGTH = 20.0           # total strip length (mm)
P_BEND = 0.001          # N/mm tip line load, bending
P_TENSION = 1.0         # N/mm tip line load, in-plane


def _rect_face(x0, x1, y0=0.0, y1=WIDTH, clockwise=False):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
    from OCP.gp import gp_Pnt
    points = [gp_Pnt(x0, y0, 0.0), gp_Pnt(x1, y0, 0.0), gp_Pnt(x1, y1, 0.0), gp_Pnt(x0, y1, 0.0)]
    if clockwise:
        points = list(reversed(points))
    edges = [BRepBuilderAPI_MakeEdge(points[i], points[(i + 1) % 4]).Edge() for i in range(4)]
    wire = BRepBuilderAPI_MakeWire()
    for edge in edges:
        wire.Add(edge)
    return BRepBuilderAPI_MakeFace(wire.Wire()).Face()


def write_strip_step(path: Path, split: bool, reverse_second: bool = False) -> Path:
    """Write a 20 mm strip as one face, or as two edge-adjacent faces."""
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    writer = STEPControl_Writer()
    if split:
        writer.Transfer(_rect_face(0.0, LENGTH / 2), STEPControl_AsIs)
        writer.Transfer(_rect_face(LENGTH / 2, LENGTH, clockwise=reverse_second), STEPControl_AsIs)
    else:
        writer.Transfer(_rect_face(0.0, LENGTH), STEPControl_AsIs)
    writer.Write(str(path))
    return Path(path)


def converted(step_path: Path, folder: Path):
    """Convert a STEP file and build the index exactly as the worker does."""
    from cad_pipeline.api import convert_step
    from kratos_iga.worker import build_index
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    cad, report = convert_step(Path(step_path))
    index = build_index(cad, report, folder)
    return cad, index


def coupling_edges(index):
    return [e for e in index["edges"] if e["kind"] == "coupling"]


def tip_edge(index):
    """Boundary edge of the last face furthest in +x (the free end)."""
    face_ids = [f["id"] for f in index["faces"]]
    candidates = [e for e in index["edges"]
                  if e["kind"] == "edge" and e["face_ids"] == [face_ids[-1]]]
    return max(candidates, key=lambda e: min(e["points"][0][0], e["points"][-1][0]))["id"]


def make_project(step_path: Path, index, *, state="confirmed", rotation=False,
                 penalty=1e7, load=(0.0, 0.0, -P_BEND), refinement_u=8):
    from kratos_iga.project import new_project, populate
    project = new_project()
    project.update({"source_step": str(step_path), "source_sha256": index["source_sha256"],
                    "geometry_sha256": index["geometry_sha256"], "units": "mm-N-s",
                    "refinement_enabled": True})
    project["materials"] = [{"id": 1, "name": "Steel", "young": E, "poisson": NU, "density": RHO}]
    project["sections"] = [{"id": 1, "name": "Steel t=0.1", "thickness": THICKNESS}]
    populate(project, index)
    for patch in project["patches"]:
        patch["degree_u"], patch["degree_v"] = 3, 3
        patch["insert_u"], patch["insert_v"] = refinement_u, 0
    if state != "pending":
        for record in project["couplings"]:
            record["state"] = state
            record["origin"] = "user"
            record["rotation"] = rotation
            record["penalty"] = penalty
    project["supports"] = [{"name": "root", "target_kind": "face", "target_id": index["faces"][0]["id"],
                            "side": "u0", "method": "strong", "value": [0.0, 0.0, 0.0],
                            "clamp": True, "interval": [0.0, "End"]}]
    project["loads"] = [{"name": "tip", "target_kind": "edge", "target_id": tip_edge(index),
                         "variable": "LINE_LOAD", "value": list(load), "interval": [0.0, "End"]}]
    project["output"] = {"vtk": False, "refinement": 4}
    return project


def bending_reference(length=LENGTH):
    inertia = WIDTH * THICKNESS ** 3 / 12.0
    return P_BEND * length ** 3 / (3.0 * E * inertia)


def tension_reference(length=LENGTH):
    return P_TENSION * length / (E * THICKNESS)


def source_identity(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
