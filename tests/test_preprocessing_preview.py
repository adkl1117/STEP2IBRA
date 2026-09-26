"""P0-4 inspectable-preprocessing tests.

Pure tests (NURBS frames, diagnostics, refinement report, glyph specifications) run
under any interpreter. The native tests need FreeCAD + Part and are skipped in the
backend venv; they exercise the real :class:`kratos_iga.preview.PreviewLayer`
document objects, the entity/fingerprint isolation and the snapshot mapping.

The full Qt panel invariants (ViewObject ``Selectable``/colour, ``panel.sync()``
rejection after a shape edit, document restore/cleanup) are asserted by the isolated
GUI probe ``runtime/p0-4-review/run_gui_p04.py`` because they require a live
FreeCAD GUI; this module deliberately never starts one.
"""
from __future__ import annotations

import math
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "freecad/Mod/KratosIBRA"))

from kratos_iga import preview  # noqa: E402
from kratos_iga import snapshots  # noqa: E402
from kratos_iga.bundle import make_bundle  # noqa: E402
from kratos_iga.groups import expand  # noqa: E402
from kratos_iga.project import new_project, validate  # noqa: E402

try:
    import FreeCAD as App  # noqa: F401
    import Part  # noqa: F401
    HAVE_FREECAD = True
except Exception as exc:  # noqa: BLE001 - the backend venv has no FreeCAD
    App = Part = None
    HAVE_FREECAD = False
    FREECAD_ERROR = f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- #
# synthetic geometry / project fixtures (small, per-stage only)
# --------------------------------------------------------------------------- #

def plane_surface(z=0.0, reverse_u=False):
    knots = [0.0, 0.0, 1.0, 1.0]
    corners = [[0.0, 0.0, z], [2.0, 0.0, z], [0.0, 3.0, z], [2.0, 3.0, z]]
    if reverse_u:
        corners = [corners[1], corners[0], corners[3], corners[2]]
    records = [[index + 1, [point[0], point[1], point[2], 1.0]]
               for index, point in enumerate(corners)]
    return {"is_rational": True, "degrees": [1, 1],
            "knot_vectors": [list(knots), list(knots)], "control_points": records}


def rational_surface():
    """A genuinely rational 2x1 surface with non-uniform weights."""
    return {"is_rational": True, "degrees": [2, 1],
            "knot_vectors": [[0.0, 0.0, 0.0, 1.0, 1.0, 1.0], [0.0, 0.0, 1.0, 1.0]],
            "control_points": [
                [1, [0.0, 0.0, 0.0, 1.0]], [2, [0.0, 4.0, 0.0, 1.0]],
                [3, [2.0, 0.0, 0.0, 0.7]], [4, [2.0, 4.0, 0.0, 1.3]],
                [5, [5.0, 0.0, 0.0, 1.0]], [6, [5.0, 4.0, 0.0, 1.0]]]}


def synthetic_index():
    faces = [{"id": 2, "degrees": [2, 2], "min_continuity": [2, 2], "control_points": 12,
              "control_point_counts": {"u": 4, "v": 3, "total": 12},
              "knot_spans": {"u": 2, "v": 1},
              "corners": {"00": [0.0, 0.0, 0.0], "10": [2.0, 0.0, 0.0],
                          "01": [0.0, 3.0, 0.0], "11": [2.0, 3.0, 0.0]}}]
    edges = [{"id": 3, "kind": "edge", "face_ids": [2], "strong_local": [0, -1],
              "points": [[0.0, 0.0, 0.0], [0.0, 1.5, 0.0], [0.0, 3.0, 0.0]]},
             {"id": 5, "kind": "edge", "face_ids": [2], "strong_local": [1, -1],
              "points": [[2.0, 0.0, 0.0], [2.0, 1.5, 0.0], [2.0, 3.0, 0.0]]},
             {"id": 7, "kind": "coupling", "face_ids": [2, 9], "strong_local": None,
              "points": []}]
    return {"source_sha256": "src", "geometry_sha256": "geo", "faces": faces, "edges": edges}


def synthetic_cad(surface=None, swapped=False):
    return {"tolerances": {"model_tolerance": 1.0e-3}, "version_number": 1, "breps": [
        {"brep_id": 1, "faces": [{"brep_id": 2, "swapped_surface_normal": swapped,
                                  "surface": surface or plane_surface(),
                                  "boundary_loops": [], "embedded_loops": [],
                                  "embedded_edges": [], "embedded_points": []}],
         "edges": [], "vertices": []}]}


def synthetic_project():
    project = new_project()
    project.update({
        "source_step": "", "source_sha256": "src", "geometry_sha256": "geo", "units": "mm-N-s",
        "refinement_enabled": True,
        "patches": [{"face_id": 2, "material_id": 1, "section_id": 1, "active": True,
                     "element": "Shell3pElement", "degree_u": 3, "degree_v": 3,
                     "insert_u": 1, "insert_v": 0, "quadrature": 2}],
        "groups": [{"id": 1, "name": "Tip", "kind": "edge", "members": [5, 5]},
                   {"id": 2, "name": "Root", "kind": "edge", "members": [3]}],
        "supports": [{"name": "Clamp", "group_id": 2, "method": "strong",
                      "value": [0.0, None, 0.0], "side": "u0", "clamp": True,
                      "interval": [0.0, "End"]}],
        "loads": [{"name": "Tip pull", "group_id": 1, "variable": "LINE_LOAD",
                   "value": [0.0, 0.0, -2.0], "interval": [0.0, "End"]},
                  {"name": "Zero pressure", "target_kind": "face", "target_id": 2,
                   "variable": "SURFACE_LOAD", "value": [0.0, 0.0, 0.0],
                   "interval": [0.0, "End"]}],
        "output": {"vtk": False, "refinement": 4},
    })
    return project


# --------------------------------------------------------------------------- #
# pure: NURBS frames
# --------------------------------------------------------------------------- #

class SurfaceFrameTests(unittest.TestCase):
    def test_plane_frame_has_unit_normal_and_analytic_derivatives(self):
        frame = preview.surface_frame(plane_surface(), [0.5, 0.5])
        self.assertEqual(frame["point"], [1.0, 1.5, 0.0])
        self.assertAlmostEqual(frame["du"][0], 2.0, places=9)
        self.assertAlmostEqual(frame["du"][1], 0.0, places=9)
        self.assertAlmostEqual(frame["dv"][1], 3.0, places=9)
        self.assertEqual([round(value, 9) for value in frame["normal"]], [0.0, 0.0, 1.0])
        self.assertAlmostEqual(frame["normal_length"], 6.0, places=9)
        self.assertFalse(frame["degenerate"])

    def test_rational_curved_derivatives_match_central_difference(self):
        surface = rational_surface()
        uv = [0.37, 0.61]
        frame = preview.surface_frame(surface, uv)
        step = 1.0e-6
        for axis, key in ((0, "du"), (1, "dv")):
            low, high = list(uv), list(uv)
            low[axis] -= step
            high[axis] += step
            a = preview.surface_point(surface, low)
            b = preview.surface_point(surface, high)
            finite = [(b[i] - a[i]) / (2.0 * step) for i in range(3)]
            for component in range(3):
                self.assertAlmostEqual(frame[key][component], finite[component], places=5)
        self.assertAlmostEqual(math.sqrt(sum(v * v for v in frame["normal"])), 1.0, places=12)

    def test_reversed_parameterization_flips_normal_and_ignores_cad_orientation(self):
        forward = preview.surface_frame(plane_surface(), [0.25, 0.75])
        reversed_frame = preview.surface_frame(plane_surface(reverse_u=True), [0.25, 0.75])
        self.assertGreater(forward["normal"][2], 0.0)
        self.assertLess(reversed_frame["normal"][2], 0.0)
        # The CAD-local orientation flag is not consulted: the frame follows u x v.
        flagged = preview.surface_frame(plane_surface(), [0.25, 0.75])
        self.assertEqual(forward["normal"], flagged["normal"])

    def test_degenerate_frame_reports_none_instead_of_a_fabricated_normal(self):
        surface = plane_surface()
        for record in surface["control_points"]:
            record[1] = [1.0, 1.0, 1.0, 1.0]
        frame = preview.surface_frame(surface, [0.4, 0.4])
        self.assertTrue(frame["degenerate"])
        self.assertIsNone(frame["normal"])
        self.assertLess(frame["normal_length"], 1.0e-12)

    def test_non_finite_knots_are_refused(self):
        surface = plane_surface()
        surface["knot_vectors"][0][2] = float("nan")
        with self.assertRaises(ValueError):
            preview.surface_frame(surface, [0.5, 0.5])

    def test_natural_boundary_follows_sides_semantics(self):
        surface = plane_surface()
        row = preview.natural_boundary(surface, [0, -1], samples=5)
        self.assertEqual(len(row), 1)
        self.assertTrue(all(abs(point[0]) < 1.0e-12 for point in row[0]))
        self.assertEqual([round(point[1], 6) for point in row[0]], [0.0, 0.75, 1.5, 2.25, 3.0])
        corner = preview.natural_boundary(surface, [1, 1], samples=5)
        self.assertEqual(len(corner[0]), 1)
        self.assertEqual([round(value, 9) for value in corner[0][0]], [2.0, 3.0, 0.0])
        whole = preview.natural_boundary(surface, [-1, -1], samples=5)
        self.assertEqual(len(whole), 4)

    def test_surface_midpoint_is_the_background_knot_midpoint(self):
        surface = rational_surface()
        self.assertEqual(preview.surface_midpoint(surface), [0.5, 0.5])
        frame = preview.surface_frame(surface, preview.surface_midpoint(surface))
        self.assertEqual(frame["uv"], [0.5, 0.5])


# --------------------------------------------------------------------------- #
# pure: diagnostics
# --------------------------------------------------------------------------- #

class DiagnosticTests(unittest.TestCase):
    def test_repeated_interior_knots_are_normal_multiplicity(self):
        report = preview.span_diagnostics([0.0, 0.0, 1.0, 1.0, 1.0, 2.0, 2.0])
        self.assertEqual(report["positive"], 2)
        self.assertEqual(report["errors"], [])

    def test_decreasing_and_non_finite_knots_are_errors(self):
        self.assertTrue(preview.span_diagnostics([0.0, 2.0, 1.0])["errors"])
        self.assertTrue(preview.span_diagnostics([0.0, float("inf")])["errors"])

    def test_no_positive_span_is_an_error(self):
        report = preview.span_diagnostics([1.0, 1.0, 1.0])
        self.assertEqual(report["positive"], 0)
        self.assertTrue(any("no positive-length span" in error for error in report["errors"]))

    def test_non_positive_and_non_finite_weights_are_errors(self):
        surface = plane_surface()
        surface["control_points"][0][1][3] = 0.0
        surface["control_points"][1][1][3] = -2.0
        surface["control_points"][2][1][3] = float("nan")
        report = preview.weight_diagnostics(surface)
        self.assertEqual(report["nonpositive"], [0, 1])
        self.assertEqual(report["nonfinite"], [2])
        self.assertTrue(report["errors"])

    def test_geometry_diagnostics_reads_the_cache_without_mutating_it(self):
        cad = synthetic_cad()
        before = deepcopy(cad)
        report = preview.geometry_diagnostics(cad)
        self.assertEqual(report["errors"], [])
        self.assertEqual(len(report["faces"]), 1)
        self.assertEqual(cad, before)

    def test_project_blockers_reports_pending_rebind(self):
        project = synthetic_project()
        self.assertEqual(preview.project_blockers(project), "")
        project["geometry_needs_rebind"] = "the converted geometry changed"
        self.assertIn("converted geometry changed", preview.project_blockers(project))


# --------------------------------------------------------------------------- #
# pure: refinement / quadrature / display separation
# --------------------------------------------------------------------------- #

class RefinementReportTests(unittest.TestCase):
    def test_estimate_uses_final_grid_and_keeps_scales_separate(self):
        report = preview.refinement_report(synthetic_project(), synthetic_index())
        row = report["analysis"]["faces"][0]
        self.assertEqual(row["original_degree"], [2, 2])
        self.assertEqual(row["target_degree"], [3, 3])
        self.assertEqual(row["spans"], [2, 1])
        self.assertEqual(row["original_control_points"], {"u": 4, "v": 3, "total": 12})
        # final_grid: u = 4 + (3-2)*2 + 1*2 = 8; v = 3 + (3-2)*1 + 0 = 4
        self.assertEqual(row["estimate_control_points"], {"u": 8, "v": 4, "total": 32})
        self.assertFalse(row["estimate_is_authoritative"])
        self.assertEqual(report["quadrature"]["faces"][0]["requested_points_per_span"], 2)
        self.assertEqual(report["display"]["subdivision"], 4)
        self.assertIn("display sampling only", report["display"]["note"])

    def test_disabled_refinement_uses_the_original_degree(self):
        project = synthetic_project()
        project["refinement_enabled"] = False
        row = preview.refinement_report(project, synthetic_index())["analysis"]["faces"][0]
        self.assertEqual(row["target_degree"], [2, 2])
        self.assertEqual(row["inserts"], [0, 0])
        self.assertEqual(row["estimate_control_points"], {"u": 4, "v": 3, "total": 12})

    def test_missing_grid_metadata_is_reported_not_guessed(self):
        index = synthetic_index()
        del index["faces"][0]["control_point_counts"]
        row = preview.refinement_report(synthetic_project(), index)["analysis"]["faces"][0]
        self.assertIsNone(row["estimate_control_points"])
        self.assertTrue(row["reason"])


# --------------------------------------------------------------------------- #
# pure: legend, groups, dedup, arrow scales
# --------------------------------------------------------------------------- #

class LegendAndGlyphTests(unittest.TestCase):
    def setUp(self):
        self.project = synthetic_project()
        self.index = synthetic_index()
        self.cad = synthetic_cad()
        self.groups = expand(self.project["groups"], self.index)

    def test_material_legend_lists_exact_references_and_units(self):
        legend = preview.material_legend(self.project, self.index)
        row = legend["rows"][0]
        self.assertEqual(row["material_id"], 1)
        self.assertEqual(row["material_name"], "Steel")
        self.assertEqual(row["young_unit"], "N/mm^2")
        self.assertEqual(row["section_id"], 1)
        self.assertEqual(row["thickness_unit"], "mm")
        self.assertEqual(legend["colors"][2], row["color"])

    def test_group_expansion_is_deduplicated_and_deterministic(self):
        glyphs = preview.support_glyphs(self.project, self.index, self.cad, self.groups)
        targets = glyphs[0]["targets"]
        self.assertEqual(len(targets), 1)  # the duplicated group member acts once
        self.assertEqual(targets[0]["kind"], "face")
        self.assertEqual(targets[0]["id"], 2)
        self.assertEqual(targets[0]["local"], [0, -1])
        self.assertEqual(targets[0]["components"], ["X", "Z"])
        self.assertTrue(targets[0]["clamp"])
        self.assertEqual(glyphs[0]["method"], "strong")
        self.assertTrue(targets[0]["curves"] and targets[0]["curves"][0])

    def test_load_arrows_use_actual_direction_and_family_scales(self):
        specs = preview.load_glyphs(self.project, self.index, self.cad, self.groups)
        line = next(glyph for glyph in specs["glyphs"] if glyph["variable"] == "LINE_LOAD")
        self.assertEqual(line["magnitude"], 2.0)
        self.assertEqual([round(value, 9) for value in line["direction"]], [0.0, 0.0, -1.0])
        self.assertEqual(len(line["targets"]), 1)  # [5, 5] expands once
        self.assertEqual(line["targets"][0]["id"], 5)
        self.assertTrue(line["targets"][0]["active"])
        diagonal = preview.model_diagonal(self.index)
        self.assertAlmostEqual(specs["scales"]["LINE_LOAD"]["scale"],
                               preview.MAX_ARROW_FRACTION * diagonal / 2.0, places=9)
        self.assertAlmostEqual(line["display_length"],
                               preview.MAX_ARROW_FRACTION * diagonal, places=9)

    def test_zero_load_is_a_zero_marker_and_zero_family_has_no_scale(self):
        specs = preview.load_glyphs(self.project, self.index, self.cad, self.groups)
        zero = next(glyph for glyph in specs["glyphs"] if glyph["variable"] == "SURFACE_LOAD")
        self.assertEqual(zero["magnitude"], 0.0)
        self.assertIsNone(zero["direction"])
        self.assertIsNone(specs["scales"]["SURFACE_LOAD"]["scale"])

    def test_dead_load_edge_and_face_use_the_converted_dimension(self):
        self.project["loads"] = [
            {"name": "Dead edge", "target_kind": "edge", "target_id": 5, "variable": "DEAD_LOAD",
             "value": [0.0, 0.0, -1.0], "interval": [0.0, "End"]},
            {"name": "Dead face", "target_kind": "face", "target_id": 2, "variable": "DEAD_LOAD",
             "value": [0.0, 0.0, -3.0], "interval": [0.0, "End"]}]
        specs = preview.load_glyphs(self.project, self.index, self.cad, self.groups)
        edge = next(glyph for glyph in specs["glyphs"] if glyph["name"] == "Dead edge")
        face = next(glyph for glyph in specs["glyphs"] if glyph["name"] == "Dead face")
        self.assertEqual((edge["family"], edge["dimension"], edge["unit"]),
                         ("DEAD_LOAD_EDGE", "line_load", "N/mm"))
        self.assertEqual((face["family"], face["dimension"], face["unit"]),
                         ("DEAD_LOAD_FACE", "stress", "N/mm^2"))
        self.assertIn("DEAD_LOAD_EDGE", specs["scales"])
        self.assertIn("DEAD_LOAD_FACE", specs["scales"])
        self.assertNotAlmostEqual(specs["scales"]["DEAD_LOAD_EDGE"]["scale"],
                                  specs["scales"]["DEAD_LOAD_FACE"]["scale"])

    def test_tiny_load_keeps_its_true_proportional_length(self):
        self.project["loads"] = [
            {"name": "Big", "target_kind": "edge", "target_id": 5, "variable": "LINE_LOAD",
             "value": [0.0, 0.0, -1.0], "interval": [0.0, "End"]},
            {"name": "Tiny", "target_kind": "edge", "target_id": 3, "variable": "LINE_LOAD",
             "value": [0.0, 0.0, -1.0e-9], "interval": [0.0, "End"]}]
        specs = preview.load_glyphs(self.project, self.index, self.cad, self.groups)
        big = next(glyph for glyph in specs["glyphs"] if glyph["name"] == "Big")
        tiny = next(glyph for glyph in specs["glyphs"] if glyph["name"] == "Tiny")
        scale = specs["scales"]["LINE_LOAD"]["scale"]
        self.assertAlmostEqual(big["display_length"],
                               preview.MAX_ARROW_FRACTION * preview.model_diagonal(self.index))
        self.assertAlmostEqual(tiny["display_length"], scale * 1.0e-9, places=12)
        self.assertLess(tiny["display_length"], big["display_length"])
        self.assertEqual(specs["length_unit"], "mm")

    def test_load_lengths_are_mm_even_in_the_metre_unit_system(self):
        self.project["units"] = "m-N-s"
        specs = preview.load_glyphs(self.project, self.index, self.cad, self.groups)
        self.assertEqual(specs["length_unit"], "mm")
        self.assertEqual(specs["scales"]["SURFACE_LOAD"]["unit"], "N/m^2")

    def test_frame_legend_has_three_distinct_colours(self):
        legend = preview.frame_legend()
        self.assertEqual([entry["axis"] for entry in legend], ["u", "v", "n"])
        self.assertEqual(len({tuple(entry["color"]) for entry in legend}), 3)

    def test_inactive_targets_are_marked_inactive(self):
        self.project["patches"][0]["active"] = False
        glyphs = preview.support_glyphs(self.project, self.index, self.cad, self.groups)
        self.assertFalse(glyphs[0]["targets"][0]["active"])

    def test_background_frame_is_the_knot_midpoint_and_notes_cad_orientation(self):
        frames = preview.frame_glyphs(self.project, self.index, synthetic_cad(swapped=True))
        self.assertEqual(frames[0]["uv"], [0.5, 0.5])
        self.assertTrue(frames[0]["swapped_surface_normal"])
        self.assertEqual([round(value, 9) for value in frames[0]["frame"]["normal"]],
                         [0.0, 0.0, 1.0])
        self.assertIn("background-surface sample", frames[0]["note"])
        self.assertIn("not the CAD face orientation", frames[0]["note"])

    def test_synthetic_project_still_validates_for_the_bundle_invariant(self):
        errors, _warnings = validate(self.project, self.index)
        self.assertEqual(errors, [])


# --------------------------------------------------------------------------- #
# native: document-side isolation
# --------------------------------------------------------------------------- #

@unittest.skipUnless(HAVE_FREECAD, "native FreeCAD runtime required")
class PreviewLayerNativeTests(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("IbraPreviewTest")
        self.addCleanup(self._close)
        self.face = self.doc.addObject("Part::Feature", "IBRA_Face_2")
        self.face.Shape = Part.makePlane(2.0, 3.0)
        self.doc.recompute()
        self.entities = {("face", 2): self.face.Name}
        self.fingerprints = {self.face.Name: (self.face.Shape.hashCode(), str(self.face.Placement))}
        self.project = synthetic_project()
        self.index = synthetic_index()
        self.cad = synthetic_cad()
        self.layer = preview.PreviewLayer()

    def _close(self):
        if self.doc is not None and self.doc.Name in App.listDocuments():
            App.closeDocument(self.doc.Name)

    def _all_toggles(self):
        return {"supports": True, "loads": True, "frame": True, "materials": True}

    def _preview_names(self):
        return {obj.Name for obj in self.doc.Objects if obj.Name.startswith(preview.PreviewLayer.PREFIX)}

    def test_preview_objects_are_independent_and_never_analysis_entities(self):
        report = self.layer.rebuild(self.doc, self.project, self.index, self.cad,
                                    self._all_toggles(), entities=self.entities)
        self.assertFalse(report["blocked"])
        self.assertTrue(self._preview_names())
        group = self.doc.getObject(preview.PreviewLayer.GROUP_NAME)
        self.assertIsNotNone(group)
        for name in self._preview_names():
            obj = self.doc.getObject(name)
            self.assertEqual(obj.TypeId, "Part::Feature")
            self.assertFalse(hasattr(obj, "IbraBrepId"))
            self.assertFalse(hasattr(obj, "IbraKind"))
            self.assertIn(obj, group.Group)
        # The analysis entity registry and the fingerprints are untouched.
        self.assertEqual(self.entities, {("face", 2): self.face.Name})
        self.assertEqual(self.fingerprints[(self.face.Name)],
                         (self.face.Shape.hashCode(), str(self.face.Placement)))
        # A preview object can never be captured by the snapshot mapping.
        faces, display = snapshots.normalize_face_entries(self.entities)
        self.assertEqual([entry["id"] for entry in faces], [2])
        self.assertEqual(display, [])
        self.assertTrue(self._preview_names().isdisjoint({entry["object"] for entry in faces}))

    def test_rebuild_and_toggle_are_idempotent_and_only_owned_objects_close(self):
        self.layer.rebuild(self.doc, self.project, self.index, self.cad,
                           self._all_toggles(), entities=self.entities)
        first = self._preview_names()
        self.layer.rebuild(self.doc, self.project, self.index, self.cad,
                           self._all_toggles(), entities=self.entities)
        self.assertEqual(self._preview_names(), first)
        foreign = self.doc.addObject("Part::Feature", "User_Face")
        self.layer.clear()
        self.assertEqual(self._preview_names(), set())
        self.assertIsNotNone(self.doc.getObject("User_Face"))
        self.assertIsNotNone(self.doc.getObject("IBRA_Face_2"))
        self.assertIsNone(self.doc.getObject(preview.PreviewLayer.GROUP_NAME))

    def test_same_bundle_and_analysis_data_before_and_after_preview(self):
        project_before, index_before, cad_before = (deepcopy(self.project), deepcopy(self.index),
                                                     deepcopy(self.cad))
        bundle_before = make_bundle(self.project, self.cad, self.index)
        self.layer.rebuild(self.doc, self.project, self.index, self.cad,
                           self._all_toggles(), entities=self.entities)
        self.layer.rebuild(self.doc, self.project, self.index, self.cad,
                           self._all_toggles(), entities=self.entities)
        bundle_after = make_bundle(self.project, self.cad, self.index)
        self.assertEqual(bundle_after, bundle_before)
        self.assertEqual(self.project, project_before)
        self.assertEqual(self.index, index_before)
        self.assertEqual(self.cad, cad_before)

    def test_actual_shape_edit_is_still_detected_while_preview_is_on(self):
        baseline = (self.face.Shape.hashCode(), str(self.face.Placement))
        self.layer.rebuild(self.doc, self.project, self.index, self.cad,
                           self._all_toggles(), entities=self.entities)
        self.assertEqual((self.face.Shape.hashCode(), str(self.face.Placement)), baseline)
        edited = self.face.Shape.copy()
        edited.translate(App.Vector(0.0, 0.0, 2.0))
        self.face.Shape = edited
        self.doc.recompute()
        self.assertNotEqual((self.face.Shape.hashCode(), str(self.face.Placement)), baseline)

    def test_ownership_marker_controls_cleanup_in_an_incoming_document(self):
        owned = self.doc.addObject("Part::Feature", preview.PreviewLayer.PREFIX + "Support_1")
        owned.Shape = Part.makePlane(1.0, 1.0)
        preview.PreviewLayer._mark(owned)
        foreign = self.doc.addObject("Part::Feature", preview.PreviewLayer.PREFIX + "User_1")
        foreign.Shape = Part.makePlane(1.0, 1.0)
        owned_name, foreign_name = owned.Name, foreign.Name
        self.doc.recompute()
        # A fresh layer has no memory of the document it is asked to clean: the marker,
        # not the reserved name, decides ownership.
        preview.PreviewLayer().clear(self.doc)
        self.assertIsNone(self.doc.getObject(owned_name))
        self.assertIsNotNone(self.doc.getObject(foreign_name))
        self.assertIsNotNone(self.doc.getObject("IBRA_Face_2"))

    def test_unrelated_same_prefix_object_is_never_reused_or_overwritten(self):
        foreign = self.doc.addObject("Part::Feature", preview.PreviewLayer.PREFIX + "Support_1")
        foreign.Shape = Part.makePlane(1.0, 1.0)
        foreign_name = foreign.Name
        self.doc.recompute()
        baseline = foreign.Shape.hashCode()
        report = self.layer.rebuild(self.doc, self.project, self.index, self.cad,
                                    {"supports": True}, entities=self.entities)
        self.assertEqual(self.doc.getObject(foreign_name).Shape.hashCode(), baseline)
        self.assertTrue(any("reserved name" in note for note in report["notes"]))
        self.assertNotIn(("support", 1), self.layer.objects)

    def test_foreign_preview_group_is_never_adopted_or_deleted(self):
        foreign_group = self.doc.addObject("App::DocumentObjectGroup",
                                           preview.PreviewLayer.GROUP_NAME)
        child = self.doc.addObject("Part::Feature", "User_Group_Child")
        child.Shape = Part.makePlane(1.0, 1.0)
        foreign_group.addObject(child)
        group_name, child_name = foreign_group.Name, child.Name
        self.doc.recompute()
        report = self.layer.rebuild(self.doc, self.project, self.index, self.cad,
                                    {"supports": True}, entities=self.entities)
        self.assertTrue(report["blocked"])
        self.assertTrue(any("reserved name" in note for note in report["notes"]), report["notes"])
        surviving = self.doc.getObject(group_name)
        self.assertIsNotNone(surviving)
        self.assertFalse(hasattr(surviving, preview.PreviewLayer.MARKER_PROPERTY))
        self.assertIsNotNone(self.doc.getObject(child_name))
        self.assertEqual(self._preview_names(), set())
        # A later clear must leave the foreign group and its child untouched.
        self.layer.clear(self.doc)
        self.assertIsNotNone(self.doc.getObject(group_name))
        self.assertIsNotNone(self.doc.getObject(child_name))

    def test_markers_are_persisted_on_every_preview_object_and_group(self):
        self.layer.rebuild(self.doc, self.project, self.index, self.cad,
                           self._all_toggles(), entities=self.entities)
        group = self.doc.getObject(preview.PreviewLayer.GROUP_NAME)
        self.assertEqual(getattr(group, preview.PreviewLayer.MARKER_PROPERTY, None),
                         preview.PreviewLayer.MARKER_VALUE)
        for name in self._preview_names():
            self.assertTrue(preview.PreviewLayer._owns(self.doc.getObject(name)), name)

    def test_inactive_physics_creates_no_glyphs_and_an_explicit_count(self):
        self.project["patches"][0]["active"] = False
        report = self.layer.rebuild(self.doc, self.project, self.index, self.cad,
                                    {"supports": True, "loads": True}, entities=self.entities)
        self.assertEqual(report["supports"], 0)
        self.assertEqual(report["loads"], 0)
        self.assertTrue(report["inactive_support_targets"])
        self.assertTrue(report["inactive_load_targets"])
        self.assertFalse([name for name in self._preview_names()
                          if "Support" in name or "Load" in name])
        self.assertTrue(any("INACTIVE" in note for note in report["notes"]))

    def test_frame_axes_are_separate_coloured_objects_with_a_legend(self):
        report = self.layer.rebuild(self.doc, self.project, self.index, self.cad,
                                    {"frame": True}, entities=self.entities)
        axes = sorted(key[0] for key in self.layer.objects)
        self.assertEqual(axes, ["frame_n", "frame_u", "frame_v"])
        self.assertEqual(report["frames"], 3)
        self.assertEqual(len({tuple(entry["color"]) for entry in report["frame_legend"]}), 3)
        self.assertTrue(any("background sample" in note for note in report["notes"]))

    def test_report_notes_preserve_components_clamp_and_scale_meaning(self):
        report = self.layer.rebuild(self.doc, self.project, self.index, self.cad,
                                    self._all_toggles(), entities=self.entities)
        notes = " | ".join(report["notes"])
        self.assertIn("components", notes)
        self.assertIn("clamp adjacent control row", notes)
        self.assertIn("true arrow", notes)
        self.assertIn("background sample", notes)

    def test_geometry_blocker_detects_a_shape_edit_read_only(self):
        self.assertEqual(preview.geometry_blocker(self.doc, self.fingerprints), "")
        edited = self.face.Shape.copy()
        edited.translate(App.Vector(0.0, 0.0, 2.0))
        self.face.Shape = edited
        self.doc.recompute()
        self.assertIn("changed", preview.geometry_blocker(self.doc, self.fingerprints))

    def test_blocked_project_creates_no_preview_objects(self):
        self.project["geometry_needs_rebind"] = "the converted geometry changed"
        report = self.layer.rebuild(self.doc, self.project, self.index, self.cad,
                                    self._all_toggles(), entities=self.entities)
        self.assertTrue(report["blocked"])
        self.assertEqual(self._preview_names(), set())


@unittest.skipUnless(HAVE_FREECAD, "native FreeCAD runtime required")
class RunReportFormatTests(unittest.TestCase):
    def test_known_fields_render_and_missing_fields_stay_unknown(self):
        # Import inside the test: kratos_iga.gui imports FreeCADGui/PySide and must not
        # be pulled in by the backend venv discovery.
        import importlib
        gui_module = importlib.import_module("kratos_iga.gui")
        data = {
            "mode": "initialize",
            "nodes": 44, "elements": 192, "conditions": 12,
            "active_element_nodes": 44, "active_condition_nodes": 16,
            "displacement_dofs": {"total": 132, "fixed": 24, "free": 108,
                                  "definition": "DISPLACEMENT_X/Y/Z DOFs"},
            "element_integration_points": {"entities": 192, "total": 192, "distinct": [1]},
            "condition_integration_points": {"entities": 12, "total": 12, "distinct": [1]},
            "actual_physical_area": 10.0, "actual_physical_area_finite": True,
            "area_check": {"status": "pass", "tolerance_relative": 1.0e-6,
                           "measured_at": "Initialize", "mismatches": []},
            "patches": [{"face_id": 2, "elements": 192, "degree": [3, 2], "control_points": [11, 4],
                         "positive_knot_spans": [8, 2], "actual_area": 10.0, "expected_area": 10.0,
                         "area_match": True, "requested_quadrature_per_span": 0,
                         "target_degree": [3, 2], "grid_match": True}],
        }
        text = gui_module.Panel._format_run_report(data)
        self.assertIn("total/fixed/free: 132/24/108", text)
        self.assertIn("area_check: pass", text)
        self.assertIn("patch 2:", text)
        self.assertIn("grid_match=True", text)
        self.assertNotIn("nodes: unknown", text)
        self.assertNotIn("actual_area=unknown", text)
        sparse = gui_module.Panel._format_run_report({"mode": "initialize"})
        self.assertIn("nodes: unknown", sparse)
        self.assertIn("area_check: unknown", sparse)


if __name__ == "__main__":
    unittest.main()
