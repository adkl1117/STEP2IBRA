"""Geometry regressions for open roots, duplicate faces and valid connections."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys
import unittest

from OCP.BRep import BRep_Builder
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.TopoDS import TopoDS_Compound
from OCP.gp import gp_Ax3, gp_Dir, gp_Pln, gp_Pnt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"freecad/Mod/KratosIBRA/backend"))

from cad_pipeline.api import convert_step
from cad_pipeline.config import ConversionOptions, ExcludedPlane
from cad_pipeline.model import build_cad_json
from cad_pipeline.nurbs import evaluate_curve, sample_parameters
from cad_pipeline.selection import OverlappingFacesError
from cad_pipeline.visualization_data import collect_couplings
from validate_cad_json import validate


def rectangle(x0=0, x1=1, y0=0, y1=1):
    return BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), x0, x1, y0, y1).Face()


def compound(*faces):
    builder = BRep_Builder()
    shape = TopoDS_Compound()
    builder.MakeCompound(shape)
    for face in faces:
        builder.Add(shape, face)
    return shape


def coupling_edges(data):
    return [e for b in data['breps'] for e in b['edges'] if len(e['topology']) == 2]


class SelectionTests(unittest.TestCase):
    def test_adjacent_coplanar_patches_keep_shared_line(self):
        result = build_cad_json(compound(rectangle(), rectangle(1, 2)))
        self.assertEqual(len(coupling_edges(result)), 1)

    def test_duplicate_faces_do_not_couple_their_perimeter(self):
        report = {}
        result = build_cad_json(compound(rectangle(), rectangle()), report=report)
        self.assertEqual(report['output_face_count'], 1)
        self.assertEqual(len(report['duplicate_faces']), 1)
        self.assertEqual(coupling_edges(result), [])

    def test_partially_overlapping_faces_require_model_decision(self):
        with self.assertRaises(OverlappingFacesError):
            build_cad_json(compound(rectangle(), rectangle(.5, 1.5)))

    def test_strict_policy_rejects_even_duplicates(self):
        with self.assertRaises(OverlappingFacesError):
            build_cad_json(compound(rectangle(), rectangle()), options=ConversionOptions(overlap_policy='error'))

    def test_t_junction_boundary_to_interior_is_preserved(self):
        skin = rectangle(0, 2, 0, 2)
        plane = gp_Pln(gp_Ax3(gp_Pnt(1, 0, 0), gp_Dir(1, 0, 0), gp_Dir(0, 1, 0)))
        rib = BRepBuilderAPI_MakeFace(plane, 0, 2, 0, 1).Face()
        result = build_cad_json(compound(skin, rib))
        self.assertEqual(len(coupling_edges(result)), 1)
        for _, point in coupling_edges(result)[0]['3d_curve']['control_points']:
            self.assertAlmostEqual(point[0], 1)
            self.assertAlmostEqual(point[2], 0)

    def test_plane_rule_does_not_remove_crossing_faces(self):
        plane = gp_Pln(gp_Ax3(gp_Pnt(1, 0, 0), gp_Dir(1, 0, 0), gp_Dir(0, 1, 0)))
        rib = BRepBuilderAPI_MakeFace(plane, 0, 2, 0, 1).Face()
        report = {}
        build_cad_json(compound(rectangle(), rib), report=report,
                       options=ConversionOptions(exclude_planes=(ExcludedPlane('z', 0, 1e-6),)))
        self.assertEqual(report['output_face_count'], 1)
        self.assertEqual(len(report['excluded_faces']), 1)


class NurbsTests(unittest.TestCase):
    def test_rational_quarter_circle(self):
        curve = {'degree': 2, 'knot_vector': [0, 0, 0, 1, 1, 1], 'active_range': [0, 1],
                 'is_rational': True, 'control_points': [[1, [1, 0, 0, 1]],
                 [2, [1, 1, 0, math.sqrt(.5)]], [3, [0, 1, 0, 1]]]}
        x, y = evaluate_curve(curve, .5)
        self.assertAlmostEqual(x, math.sqrt(.5))
        self.assertAlmostEqual(y, math.sqrt(.5))
        curve['active_range'] = [1, 0]
        self.assertEqual(sample_parameters(curve, 3), [1, .75, .5, .25, 0])

    def test_negative_refinement_is_rejected(self):
        with self.assertRaises(ValueError):
            sample_parameters({}, -1)

    def test_invalid_options_are_rejected(self):
        for kwargs in ({'tolerance': 0}, {'tolerance': float('nan')}, {'sample_count': 2}, {'overlap_policy': 'guess'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ConversionOptions(**kwargs)


if __name__ == '__main__':
    unittest.main()
