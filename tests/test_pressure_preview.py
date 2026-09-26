"""P0-5 reference-pressure preview tests (pure; no FreeCAD, no Kratos).

Covers the approved contract's preview side: pointwise X0 reference normals from
the cached CAD NURBS, the ``normal_flip`` sign, zero/intensity behaviour, the
face-only target rule, the additive amplitude metadata and that the legacy vector
DEAD_LOAD dimension families are untouched.
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "freecad/Mod/KratosIBRA"))

from kratos_iga import preview  # noqa: E402
from kratos_iga.groups import expand  # noqa: E402
from kratos_iga.project import new_project  # noqa: E402


def plane_surface(reverse_u=False):
    knots = [0.0, 0.0, 1.0, 1.0]
    corners = [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 3.0, 0.0], [2.0, 3.0, 0.0]]
    if reverse_u:
        corners = [corners[1], corners[0], corners[3], corners[2]]
    return {"is_rational": True, "degrees": [1, 1],
            "knot_vectors": [list(knots), list(knots)],
            "control_points": [[i + 1, [p[0], p[1], p[2], 1.0]] for i, p in enumerate(corners)]}


def curved_surface():
    """A paraboloid-like rational NURBS: normals differ pointwise along u."""
    points = []
    for x, z in ((0.0, 0.0), (1.0, 0.5), (2.0, 2.0)):
        points.append([x, 0.0, z, 1.0])
    for x, z in ((0.0, 0.0), (1.0, 0.5), (2.0, 2.0)):
        points.append([x, 3.0, z, 1.0])
    return {"is_rational": True, "degrees": [2, 1],
            "knot_vectors": [[0.0, 0.0, 0.0, 1.0, 1.0, 1.0], [0.0, 0.0, 1.0, 1.0]],
            "control_points": [[i + 1, p] for i, p in enumerate(points)]}


def cad_for(surface):
    return {"tolerances": {"model_tolerance": 1.0e-3}, "version_number": 1, "breps": [
        {"brep_id": 1, "faces": [{"brep_id": 2, "swapped_surface_normal": False,
                                  "surface": surface, "boundary_loops": [],
                                  "embedded_loops": [], "embedded_edges": [],
                                  "embedded_points": []}], "edges": [], "vertices": []}]}


def index_for():
    return {"source_sha256": "src", "geometry_sha256": "geo",
            "faces": [{"id": 2, "degrees": [1, 1], "min_continuity": [2, 2],
                       "control_points": 4,
                       "control_point_counts": {"u": 2, "v": 2, "total": 4},
                       "knot_spans": {"u": 1, "v": 1},
                       "corners": {"00": [0.0, 0.0, 0.0], "10": [2.0, 0.0, 0.0],
                                   "01": [0.0, 3.0, 0.0], "11": [2.0, 3.0, 0.0]}}],
            "edges": [{"id": 3, "kind": "edge", "face_ids": [2], "strong_local": [0, -1],
                       "points": [[0.0, 0.0, 0.0], [0.0, 3.0, 0.0]]}]}


def project_with(load, units="mm-N-s"):
    project = new_project()
    project.update({"units": units, "source_sha256": "src", "geometry_sha256": "geo",
                    "refinement_enabled": False,
                    "patches": [{"face_id": 2, "material_id": 1, "section_id": 1, "active": True,
                                 "element": "Shell3pElement", "degree_u": 2, "degree_v": 2,
                                 "insert_u": 0, "insert_v": 0, "quadrature": 0}],
                    "groups": [], "supports": [], "loads": [load], "initial_conditions": [],
                    "couplings": [], "output": {"vtk": False, "refinement": 4}})
    return project


def pressure_load(pressure=0.5, flip=False, target_kind="face", target_id=2, amplitude=None, **extra):
    record = {"name": "Top_pressure", "kind": "reference_pressure", "target_kind": target_kind,
              "target_id": target_id, "pressure": pressure, "normal_flip": flip,
              "interval": [0.0, "End"]}
    if amplitude is not None:
        record["amplitude"] = amplitude
    record.update(extra)
    return record


class PressureArrowTests(unittest.TestCase):
    def _glyph(self, load, surface=None, units="mm-N-s"):
        project = project_with(load, units)
        groups = expand(project["groups"], index_for())
        specs = preview.load_glyphs(project, index_for(), cad_for(surface or plane_surface()), groups)
        glyph = next(item for item in specs["glyphs"] if item.get("pressure"))
        return glyph, specs

    def test_plane_pressure_arrows_follow_the_unit_normal_and_flip(self):
        glyph, specs = self._glyph(pressure_load(0.5, False))
        self.assertEqual(glyph["family"], "SURFACE_LOAD")
        self.assertEqual(glyph["unit"], "N/mm^2")
        self.assertAlmostEqual(glyph["magnitude"], 0.5)
        arrows = glyph["targets"][0]["pressure_arrows"]
        self.assertTrue(arrows)
        for arrow in arrows:
            self.assertAlmostEqual(arrow["direction"][2], 1.0, places=9)
        flipped, _ = self._glyph(pressure_load(0.5, True))
        for arrow in flipped["targets"][0]["pressure_arrows"]:
            self.assertAlmostEqual(arrow["direction"][2], -1.0, places=9)

    def test_reverse_parameterization_flips_the_reference_normal(self):
        forward, _ = self._glyph(pressure_load(0.5), surface=plane_surface())
        reversed_glyph, _ = self._glyph(pressure_load(0.5), surface=plane_surface(reverse_u=True))
        self.assertGreater(forward["targets"][0]["pressure_arrows"][0]["direction"][2], 0.0)
        self.assertLess(reversed_glyph["targets"][0]["pressure_arrows"][0]["direction"][2], 0.0)

    def test_curved_face_normals_vary_pointwise_and_are_unit(self):
        glyph, _ = self._glyph(pressure_load(0.25), surface=curved_surface())
        arrows = glyph["targets"][0]["pressure_arrows"]
        rounded = {tuple(round(component, 6) for component in arrow["direction"]) for arrow in arrows}
        self.assertGreater(len(rounded), 1)  # never a single centre normal
        for arrow in arrows:
            self.assertAlmostEqual(math.sqrt(sum(c * c for c in arrow["direction"])), 1.0, places=9)

    def test_zero_pressure_and_intensity_scale(self):
        zero, _ = self._glyph(pressure_load(0.0))
        self.assertEqual(zero["magnitude"], 0.0)
        self.assertEqual(zero["display_length"], 0.0)
        project = project_with(pressure_load(1.0))
        project["loads"].append(dict(pressure_load(0.001), name="Weak"))
        specs = preview.load_glyphs(project, index_for(), cad_for(plane_surface()),
                                    expand(project["groups"], index_for()))
        strong = next(g for g in specs["glyphs"] if g["name"] == "Top_pressure")
        weak = next(g for g in specs["glyphs"] if g["name"] == "Weak")
        scale = specs["scales"]["SURFACE_LOAD"]["scale"]
        self.assertAlmostEqual(strong["display_length"], scale * 1.0, places=9)
        self.assertAlmostEqual(weak["display_length"], scale * 0.001, places=12)
        flipped, _ = self._glyph(pressure_load(1.0, True))
        self.assertAlmostEqual(flipped["display_length"], strong["display_length"], places=12)

    def test_edge_pressure_target_is_blocked_not_drawn(self):
        glyph, _ = self._glyph(pressure_load(0.5, target_kind="edge", target_id=3))
        self.assertTrue(glyph["blocked"])
        self.assertIn("face", glyph["blocked"])
        self.assertEqual(glyph["targets"], [])

    def test_pressure_units_follow_the_project_and_lengths_stay_mm(self):
        glyph, specs = self._glyph(pressure_load(0.5), units="m-N-s")
        self.assertEqual(glyph["unit"], "N/m^2")
        self.assertEqual(specs["length_unit"], "mm")

    def test_amplitude_metadata_reports_knots_and_domain_without_a_current_time(self):
        amplitude = {"points": [[0.0, 0.0], [0.25, 1.0], [1.0, 1.0]]}
        glyph, _ = self._glyph(pressure_load(0.5, amplitude=amplitude))
        meta = glyph["amplitude"]
        self.assertTrue(meta["present"])
        self.assertEqual(meta["knots"], [0.0, 0.25, 1.0])
        self.assertEqual(meta["domain"], [0.0, 1.0])
        self.assertIn("outside the table", meta["note"])
        constant, _ = self._glyph(pressure_load(0.5))
        self.assertFalse(constant["amplitude"]["present"])
        self.assertIn("constant factor 1", constant["amplitude"]["note"])

    def test_vector_glyph_carries_amplitude_metadata_too(self):
        project = project_with(pressure_load(0.5), "mm-N-s")
        project["loads"] = [{"name": "Vector table", "target_kind": "face", "target_id": 2,
                             "variable": "SURFACE_LOAD", "value": [0.0, 0.0, -1.0],
                             "interval": [0.0, "End"],
                             "amplitude": {"points": [[0.0, 0.0], [1.0, 2.0]]}}]
        specs = preview.load_glyphs(project, index_for(), cad_for(plane_surface()),
                                    expand(project["groups"], index_for()))
        vector = specs["glyphs"][0]
        self.assertIsNone(vector.get("pressure"))
        self.assertTrue(vector["amplitude"]["present"])
        self.assertEqual(vector["amplitude"]["knots"], [0.0, 1.0])
        self.assertIn("piecewise-linear", vector["amplitude"]["note"])

    def test_malformed_amplitude_is_invalid_never_constant_one(self):
        for bad in ({}, {"points": []}, {"points": [[0.0, 1.0]]}, {"other": 1}):
            with self.subTest(bad=bad):
                meta = preview.amplitude_metadata({"name": "L", "amplitude": bad})
                self.assertTrue(meta["present"])
                self.assertFalse(meta["valid"])
                self.assertIn("INVALID", meta["note"])
        none = preview.amplitude_metadata({"name": "L"})
        self.assertFalse(none["present"])
        self.assertTrue(none["valid"])
        self.assertIn("constant factor 1", none["note"])

    def test_all_four_pressure_flip_combinations_follow_the_sign(self):
        expected = {
            (2.0, False): 1.0, (2.0, True): -1.0,
            (-2.0, False): -1.0, (-2.0, True): 1.0,
        }
        for (pressure, flip), sign in expected.items():
            with self.subTest(pressure=pressure, flip=flip):
                glyph, _ = self._glyph(pressure_load(pressure, flip))
                self.assertAlmostEqual(glyph["sign"], sign, places=12)
                for arrow in glyph["targets"][0]["pressure_arrows"]:
                    self.assertAlmostEqual(arrow["direction"][2], sign, places=9)

    def test_negative_pressure_on_reversed_parameters_flips_again(self):
        # Reversed u flips the reference normal (-z); negative pressure flips again (+z).
        glyph, _ = self._glyph(pressure_load(-2.0, False), surface=plane_surface(reverse_u=True))
        for arrow in glyph["targets"][0]["pressure_arrows"]:
            self.assertAlmostEqual(arrow["direction"][2], 1.0, places=9)

    def test_zero_pressure_has_no_direction_magnitude(self):
        glyph, _ = self._glyph(pressure_load(0.0, True))
        self.assertEqual(glyph["magnitude"], 0.0)
        self.assertEqual(glyph["display_length"], 0.0)

    def test_bool_pressure_and_non_bool_flip_are_blocked(self):
        glyph, _ = self._glyph(pressure_load(True))
        self.assertTrue(glyph["blocked"])
        self.assertIn("finite signed scalar", glyph["blocked"])
        glyph, _ = self._glyph(pressure_load(1.0, flip=1))
        self.assertTrue(glyph["blocked"])
        self.assertIn("boolean", glyph["blocked"])

    def test_malformed_amplitude_uses_full_parse_and_never_crashes(self):
        for bad in ({"points": [[0.0, 1.0], [0.0, 2.0]]},
                    {"points": [[0.5, 1.0], [0.25, 1.0]]},
                    {"points": [[0.0, 1.0], [1.0, float("nan")]]},
                    {"points": [[0.0, 1.0], [True, 1.0]]},
                    {"points": [[0.0, "x"]]}):
            with self.subTest(bad=bad):
                meta = preview.amplitude_metadata({"name": "L", "amplitude": bad})
                self.assertFalse(meta["valid"])
                self.assertIn("INVALID", meta["note"])
        valid = preview.amplitude_metadata({"name": "L", "amplitude": {"points": [[0.0, 1.0], [1.0, 2.0]]}})
        self.assertTrue(valid["valid"])
        self.assertEqual(valid["knots"], [0.0, 1.0])

    def test_legacy_vector_records_keep_their_dead_dimension_families(self):
        project = project_with(pressure_load(0.5), "mm-N-s")
        project["loads"].append({"name": "Dead edge", "target_kind": "edge", "target_id": 3,
                                 "variable": "DEAD_LOAD", "value": [0.0, 0.0, -1.0],
                                 "interval": [0.0, "End"]})
        project["loads"].append({"name": "Dead face", "target_kind": "face", "target_id": 2,
                                 "variable": "DEAD_LOAD", "value": [0.0, 0.0, -2.0],
                                 "interval": [0.0, "End"]})
        specs = preview.load_glyphs(project, index_for(), cad_for(plane_surface()),
                                    expand(project["groups"], index_for()))
        edge = next(g for g in specs["glyphs"] if g["name"] == "Dead edge")
        face = next(g for g in specs["glyphs"] if g["name"] == "Dead face")
        self.assertEqual(edge["family"], "DEAD_LOAD_EDGE")
        self.assertEqual(face["family"], "DEAD_LOAD_FACE")
        self.assertIsNone(edge.get("pressure"))
        pressure = next(g for g in specs["glyphs"] if g.get("pressure"))
        self.assertIsNotNone(pressure["targets"][0]["pressure_arrows"])


if __name__ == "__main__":
    unittest.main()
