"""P0-3 regression tests for :mod:`kratos_iga.brep_compare`.

The pure tests exercise the *actual* ASCII layout written by OCCT V7.8
(``TopTools_ShapeSet`` / ``BRepTools_ShapeSet`` / ``TopTools_LocationSet``): one embedded
real ``BRepTools::Write`` export plus hand-built fixtures that reproduce the writer's exact
token order for every record type.  They run with the backend venv and need no FreeCAD.

``NativeBrepTests`` builds real plane / wire / rational B-spline geometry with OCP,
serialises it through ``BRepTools.Write_s`` (``TopTools_FormatVersion_VERSION_1``) and
checks self-comparison, save/load round-trips and that pole/weight/trim/placement changes
are detected.  It is skipped when OCP is unavailable.
"""
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "freecad/Mod/KratosIBRA"))

# Load the file under test by path so an installed workbench copy can never shadow it.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "brep_compare_under_test", ROOT / "freecad/Mod/KratosIBRA/kratos_iga/brep_compare.py")
brep_compare = importlib.util.module_from_spec(_spec)  # noqa: E402
_spec.loader.exec_module(brep_compare)
BrepCompareError = brep_compare.BrepCompareError
BrepUnsupportedVersion = brep_compare.BrepUnsupportedVersion
compare = brep_compare.compare
describe_difference = brep_compare.describe_difference
normalize = brep_compare.normalize

HEADER = "CASCADE Topology V1, (c) Matra-Datavision"
EMPTY_TABLES = "\n".join([
    "Curve2ds 0",
    "Curves 0",
    "Polygon3D 0",
    "PolygonOnTriangulations 0",
    "Surfaces 0",
    "Triangulations 0",
])

# A real export of a 2x1 planar face produced by BRepTools::Write_s(..., VERSION_1).
# Kept verbatim because it is the ground truth for the multi-line record layout.
PLANE_FIXTURE = (
    "CASCADE Topology V1, (c) Matra-Datavision\n"
    "Locations 0\n"
    "Curve2ds 4\n"
    "1 0 0 0 1 \n"
    "1 0 0 1 0 \n"
    "1 2 0 0 1 \n"
    "1 0 1 1 0 \n"
    "Curves 4\n"
    "1 0 0 0 -0 1 0 \n"
    "1 0 0 0 1 0 -0 \n"
    "1 2 0 0 -0 1 0 \n"
    "1 0 1 0 1 0 -0 \n"
    "Polygon3D 0\n"
    "PolygonOnTriangulations 0\n"
    "Surfaces 1\n"
    "1 0 0 0 0 0 1 1 0 -0 -0 1 0 \n"
    "Triangulations 0\n"
    "\n"
    "TShapes 10\n"
    "Ve\n"
    "1e-07\n"
    "0 0 0\n"
    "0 0\n"
    "\n"
    "0101101\n"
    "*\n"
    "Ve\n"
    "1e-07\n"
    "0 1 0\n"
    "0 0\n"
    "\n"
    "0101101\n"
    "*\n"
    "Ed\n"
    " 1e-07 1 1 0\n"
    "1  1 0 0 1\n"
    "2  1 1 0 0 1\n"
    "0\n"
    "\n"
    "0101000\n"
    "+10 0 -9 0 *\n"
    "Ve\n"
    "1e-07\n"
    "2 0 0\n"
    "0 0\n"
    "\n"
    "0101101\n"
    "*\n"
    "Ed\n"
    " 1e-07 1 1 0\n"
    "1  2 0 0 2\n"
    "2  2 1 0 0 2\n"
    "0\n"
    "\n"
    "0101000\n"
    "+10 0 -7 0 *\n"
    "Ve\n"
    "1e-07\n"
    "2 1 0\n"
    "0 0\n"
    "\n"
    "0101101\n"
    "*\n"
    "Ed\n"
    " 1e-07 1 1 0\n"
    "1  3 0 0 1\n"
    "2  3 1 0 0 1\n"
    "0\n"
    "\n"
    "0101000\n"
    "+7 0 -5 0 *\n"
    "Ed\n"
    " 1e-07 1 1 0\n"
    "1  4 0 0 2\n"
    "2  4 1 0 0 2\n"
    "0\n"
    "\n"
    "0101000\n"
    "+9 0 -5 0 *\n"
    "Wi\n"
    "\n"
    "0101100\n"
    "-8 0 +6 0 +4 0 -3 0 *\n"
    "Fa\n"
    "0  1e-07 1 0\n"
    "\n"
    "1101000\n"
    "+2 0 *\n"
    "\n"
    "+1 0 \n"
)


# --------------------------------------------------------------------------- #
# fixture helpers that mirror the OCCT writer
# --------------------------------------------------------------------------- #

def fmt(value):
    return f"{float(value):.17g}"


def location_type1(matrix):
    """One ``Locations`` type-1 record: leading ``1`` then the 12 matrix doubles."""
    rows = [" ".join(fmt(v) for v in matrix[row * 4:row * 4 + 4]) for row in range(3)]
    return ["1"] + rows


def location_type2(pairs):
    """One ``Locations`` type-2 record: ``2 id power ... 0``."""
    tokens = ["2"]
    for ident, power in pairs:
        tokens.extend([str(ident), str(power)])
    tokens.append("0")
    return [" ".join(tokens)]


def vertex(x=0, y=0, z=0, tol="1e-07", reps=(), flags="0101101", children="*"):
    lines = ["Ve", f"{tol} {fmt(x)} {fmt(y)} {fmt(z)}"]
    lines.extend(" ".join(rep) for rep in reps)
    lines.extend(["0 0", "", flags, children])
    return lines


def edge(reps=(), tol="1e-07", same="1", rng="1", degenerated="0",
         flags="0101000", children="*"):
    lines = ["Ed", f"{tol} {same} {rng} {degenerated}"]
    lines.extend(" ".join(rep) for rep in reps)
    lines.extend(["0", "", flags, children])
    return lines


def face(restriction="0", tol="1e-07", surface="1", location="0",
         flags="1101000", children="*", tag=None):
    lines = ["Fa", f"{restriction} {tol} {surface} {location}"]
    if tag is not None:
        lines.append(tag)
    lines.extend(["", flags, children])
    return lines


def aggregate(kind, flags="0101100", children="*", extra=None):
    lines = [kind]
    if extra is not None:
        lines.append(extra)
    lines.extend(["", flags, children])
    return lines


def make_brep(locations, records, root, tables=EMPTY_TABLES):
    lines = [HEADER, f"Locations {len(locations)}"]
    for location in locations:
        lines.extend(location)
    lines.append(tables)
    lines.append(f"TShapes {len(records)}")
    for record in records:
        lines.extend(record)
    lines.append(root)
    return "\n".join(lines) + "\n"


ROT90 = (0.0, -1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)
TRANS5 = (1.0, 0.0, 0.0, 5.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)
TRANS_ROT = (0.0, -1.0, 0.0, 5.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)
ROT_TRANS = (0.0, -1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 5.0, 0.0, 0.0, 1.0, 0.0)
ROT90_INV = (0.0, 1.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)


class RealLayoutTests(unittest.TestCase):
    """The embedded real export must parse and behave like the writer intended."""

    def test_real_plane_parses_and_self_compares(self):
        canonical = normalize(PLANE_FIXTURE)
        self.assertEqual(canonical[0], "BREP")
        self.assertIn("TShapes", canonical)
        # Location id 0 resolves to the canonical identity matrix tuple.
        self.assertTrue(any(isinstance(item, tuple) for item in canonical))
        self.assertEqual(compare(PLANE_FIXTURE, PLANE_FIXTURE), {"equal": True})
        self.assertEqual(describe_difference(PLANE_FIXTURE, PLANE_FIXTURE), "")

    def test_real_plane_header_is_exactly_v1(self):
        self.assertEqual(PLANE_FIXTURE.splitlines()[0], HEADER)

    def test_draw_preamble_before_banner_is_accepted(self):
        prefixed = "DBRep_DrawableShape\n\n" + PLANE_FIXTURE
        self.assertEqual(compare(prefixed, prefixed), {"equal": True})
        canonical = normalize(prefixed)
        self.assertEqual(canonical[1], "DBRep_DrawableShape")
        without = normalize(PLANE_FIXTURE)
        self.assertNotEqual(canonical, without)

    def test_draw_preamble_with_v10_is_still_rejected(self):
        prefixed = "DBRep_DrawableShape\n\n" + PLANE_FIXTURE.replace("V1,", "V10,", 1)
        with self.assertRaises(BrepUnsupportedVersion):
            normalize(prefixed)

    def test_real_plane_changed_vertex_coordinate_detected(self):
        changed = PLANE_FIXTURE.replace("1e-07\n2 1 0\n", "1e-07\n2 1.5 0\n", 1)
        self.assertNotEqual(changed, PLANE_FIXTURE)
        self.assertFalse(compare(PLANE_FIXTURE, changed)["equal"])

    def test_real_plane_changed_weight_table_token_detected(self):
        changed = PLANE_FIXTURE.replace(
            "1 0 0 0 0 0 1 1 0 -0 -0 1 0", "1 0 0 0 0 0 1 1 0 -0 -0 1 0.001", 1)
        self.assertFalse(compare(PLANE_FIXTURE, changed)["equal"])

    def test_real_plane_nonfinite_rejected_even_identical(self):
        for token in ("nan", "inf", "-inf", "1e999"):
            poisoned = PLANE_FIXTURE.replace("1e-07", token, 1)
            with self.assertRaises(BrepCompareError):
                compare(poisoned, poisoned)
            with self.assertRaises(BrepCompareError):
                normalize(poisoned)

    def test_real_plane_truncations_raise(self):
        tokens = PLANE_FIXTURE.split()
        for cut in (2, 6, 8, 20, 40, len(tokens) - 1):
            with self.assertRaises(BrepCompareError):
                normalize(" ".join(tokens[:cut]))


class LocationTableTests(unittest.TestCase):
    def test_identity_location_and_zero_are_equivalent(self):
        with_zero = make_brep([], [vertex(children="*")], "+1 0")
        with_identity = make_brep([location_type1((1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0))],
                                  [vertex(children="*")], "+1 1")
        self.assertEqual(compare(with_zero, with_identity), {"equal": True})

    def test_unknown_location_id_raises_instead_of_passing_through(self):
        text = make_brep([], [vertex(children="*")], "+1 9")
        with self.assertRaises(BrepCompareError):
            normalize(text)

    def test_unknown_location_in_vertex_representation_raises(self):
        text = make_brep([], [vertex(reps=[["0.5", "1", "1", "7"]])], "+1 0")
        with self.assertRaises(BrepCompareError):
            normalize(text)

    def test_negative_power_composition(self):
        inverse = make_brep([location_type1(ROT90), location_type2([(1, -1)])],
                            [vertex(children="*")], "+1 2")
        explicit = make_brep([location_type1(ROT90_INV)], [vertex(children="*")], "+1 1")
        self.assertEqual(compare(inverse, explicit), {"equal": True})

    def test_rotation_translation_do_not_commute(self):
        composed = make_brep([location_type1(ROT90), location_type1(TRANS5),
                              location_type2([(1, 1), (2, 1)])],
                             [vertex(children="*")], "+1 3")
        expected = make_brep([location_type1(TRANS_ROT)], [vertex(children="*")], "+1 1")
        reversed_order = make_brep([location_type1(ROT_TRANS)],
                                   [vertex(children="*")], "+1 1")
        self.assertEqual(compare(composed, expected), {"equal": True})
        self.assertFalse(compare(composed, reversed_order)["equal"])

    def test_repeated_type1_records_resolve_by_ordinal(self):
        text = make_brep([location_type1(ROT90), location_type1(TRANS5),
                          location_type1(TRANS_ROT)],
                         [vertex(children="*")], "+1 3")
        explicit = make_brep([location_type1(TRANS_ROT)], [vertex(children="*")], "+1 1")
        self.assertEqual(compare(text, explicit), {"equal": True})

    def test_unused_locations_are_omitted(self):
        bare = make_brep([], [vertex(children="*")], "+1 0")
        extra = make_brep([location_type1(ROT90), location_type1(TRANS5)],
                          [vertex(children="*")], "+1 0")
        self.assertEqual(compare(bare, extra), {"equal": True})

    def test_location_matrix_compared_with_tolerance(self):
        base = make_brep([location_type1(TRANS5)], [vertex(children="*")], "+1 1")
        nudged = make_brep([location_type1((1.0, 0.0, 0.0, 5.0 + 1e-15,
                                            0.0, 1.0, 0.0, 0.0,
                                            0.0, 0.0, 1.0, 0.0))],
                           [vertex(children="*")], "+1 1")
        self.assertEqual(compare(base, nudged), {"equal": True})
        moved = make_brep([location_type1((1.0, 0.0, 0.0, 5.5,
                                           0.0, 1.0, 0.0, 0.0,
                                           0.0, 0.0, 1.0, 0.0))],
                          [vertex(children="*")], "+1 1")
        self.assertFalse(compare(base, moved)["equal"])

    def test_forward_location_reference_raises(self):
        text = make_brep([location_type2([(2, 1)]), location_type1(ROT90)],
                         [vertex(children="*")], "+1 0")
        with self.assertRaises(BrepCompareError):
            normalize(text)


class VertexGrammarTests(unittest.TestCase):
    def test_all_vertex_representations_round_trip(self):
        reps = [["0.5", "1", "1", "0"],
                ["0.25", "2", "1", "1", "0"],
                ["0.75", "3", "2", "1", "0"]]
        text = make_brep([], [vertex(reps=reps, children="*")], "+1 0")
        self.assertEqual(compare(text, text), {"equal": True})

    def test_vertex_terminator_must_be_zero_zero(self):
        record = ["Ve", "1e-07 0 0 0", "0.5 1 1 0", "0.5 0", "", "0101101", "*"]
        text = make_brep([], [record], "+1 0")
        with self.assertRaises(BrepCompareError):
            normalize(text)

    def test_unknown_vertex_representation_raises(self):
        text = make_brep([], [vertex(reps=[["0.5", "9", "1"]])], "+1 0")
        with self.assertRaises(BrepCompareError):
            normalize(text)

    def test_negative_coordinates_are_not_location_references(self):
        text = make_brep([], [vertex(x=-3, y=-4, z=-5, children="*")], "+1 0")
        self.assertEqual(compare(text, text), {"equal": True})
        normalize(text)  # would raise if -3/-4/-5 were read as location ids


class EdgeGrammarTests(unittest.TestCase):
    def test_all_seven_edge_representations_round_trip(self):
        reps = [["1", "1", "0", "0", "1"],
                ["2", "1", "1", "0", "0", "1"],
                ["3", "1", "2", "C0", "1", "0", "0", "1"],
                ["4", "G1", "1", "0", "2", "0"],
                ["5", "1", "0"],
                ["6", "1", "1", "0"],
                ["7", "1", "2", "1", "0"]]
        text = make_brep([], [vertex(children="*"), edge(reps=reps, children="+1 0 *")],
                         "+2 0")
        self.assertEqual(compare(text, text), {"equal": True})

    def test_continuity_is_a_string_not_an_integer(self):
        base = make_brep([], [edge(reps=[["3", "1", "2", "C0", "1", "0", "0", "1"]],
                                   children="*")], "+1 0")
        other = make_brep([], [edge(reps=[["3", "1", "2", "C1", "1", "0", "0", "1"]],
                                    children="*")], "+1 0")
        integer = make_brep([], [edge(reps=[["3", "1", "2", "0", "1", "0", "0", "1"]],
                                      children="*")], "+1 0")
        self.assertFalse(compare(base, other)["equal"])
        with self.assertRaises(BrepCompareError):
            normalize(integer)

    def test_unknown_edge_representation_fails_closed(self):
        text = make_brep([], [edge(reps=[["8", "1"]], children="*")], "+1 0")
        with self.assertRaises(BrepCompareError):
            normalize(text)


class AggregateAndFaceTests(unittest.TestCase):
    def test_aggregates_have_zero_geometry_fields(self):
        records = [vertex(children="*"),
                   aggregate("Wi", children="+1 0 *")]
        text = make_brep([], records, "+2 0")
        self.assertEqual(compare(text, text), {"equal": True})

    def test_aggregate_geometry_token_fails_closed(self):
        text = make_brep([], [aggregate("Wi", children="*", extra="5 0")], "+1 0")
        with self.assertRaises(BrepCompareError):
            normalize(text)

    def test_face_without_triangulation_is_supported(self):
        text = make_brep([], [face(children="*")], "+1 0")
        self.assertEqual(compare(text, text), {"equal": True})

    def test_face_triangulation_tag_fails_closed(self):
        with_tag = make_brep([], [face(children="*", tag="2 1")], "+1 0")
        with self.assertRaises(BrepUnsupportedVersion):
            normalize(with_tag)


class PairAndHeaderTests(unittest.TestCase):
    def test_child_pairs_use_combined_orientation_and_location(self):
        text = make_brep([], [vertex(children="*"), aggregate("Co", children="+1 0 *")],
                         "+2 0")
        self.assertEqual(compare(text, text), {"equal": True})

    def test_child_triple_layout_is_rejected(self):
        text = make_brep([], [vertex(children="*"),
                              aggregate("Co", children="+1 0 0 *")], "+2 0")
        with self.assertRaises(BrepCompareError):
            normalize(text)

    def test_child_and_root_ids_validated(self):
        for children, root in (("+9 0 *", "+1 0"), ("+1 0 *", "+9 0"), ("+0 0 *", "+1 0")):
            text = make_brep([], [vertex(children=children)], root)
            with self.assertRaises(BrepCompareError):
                normalize(text)

    def test_malformed_reference_token_raises(self):
        for bad in ("2", "+x", "++1", "1"):
            text = make_brep([], [vertex(children="*"),
                                  aggregate("Co", children=f"{bad} 0 *")], "+2 0")
            with self.assertRaises(BrepCompareError):
                normalize(text)

    def test_flags_must_be_exactly_seven_bits(self):
        for flags in ("000000", "00000000", "0000002", "0101101x"):
            text = make_brep([], [vertex(flags=flags, children="*")], "+1 0")
            with self.assertRaises(BrepCompareError):
                normalize(text)

    def test_root_pair_is_required_and_trailing_tokens_rejected(self):
        no_root = "\n".join([HEADER, "Locations 0", EMPTY_TABLES, "TShapes 1",
                             "Ve", "1 0 0 0", "0 0", "0101101", "*"]) + "\n"
        with self.assertRaises(BrepCompareError):
            normalize(no_root)
        extra = make_brep([], [vertex(children="*")], "+1 0") + "+1 0\n"
        with self.assertRaises(BrepCompareError):
            normalize(extra)

    def test_header_is_exact_v1_not_a_prefix_match(self):
        for version in ("V10,", "V2,", "V3,", "V1"):
            text = PLANE_FIXTURE.replace("V1,", version, 1)
            with self.assertRaises(BrepUnsupportedVersion):
                normalize(text)

    def test_missing_locations_or_tshapes_table_raises(self):
        with self.assertRaises(BrepCompareError):
            normalize(HEADER + "\nTShapes 1\n+1 0\n")
        with self.assertRaises(BrepCompareError):
            normalize(HEADER + "\nLocations 0\nCurve2ds 0\n")


class PreservationTests(unittest.TestCase):
    def tables_with(self, weight):
        return "\n".join([
            "Curve2ds 0",
            "Curves 1",
            "1 0 0 0 1 0 0",
            "1 5 0 0 1",
            f"0 1 1 2 3 0.5 {weight}",
            "Polygon3D 0",
            "PolygonOnTriangulations 0",
            "Surfaces 0",
            "Triangulations 0",
        ])

    def test_poles_weights_and_trims_are_preserved_exactly(self):
        base = make_brep([], [vertex(children="*")], "+1 0", tables=self.tables_with("0.25"))
        same = make_brep([], [vertex(children="*")], "+1 0", tables=self.tables_with("0.25"))
        changed = make_brep([], [vertex(children="*")], "+1 0", tables=self.tables_with("0.5"))
        self.assertEqual(compare(base, same), {"equal": True})
        self.assertFalse(compare(base, changed)["equal"])
        self.assertIn("differs", describe_difference(base, changed))

    def test_changed_geometry_token_count_is_reported(self):
        base = make_brep([], [vertex(children="*")], "+1 0", tables=self.tables_with("0.25"))
        shorter = base.replace(" 0.25", "", 1)
        outcome = compare(base, shorter)
        self.assertFalse(outcome["equal"])
        self.assertEqual(outcome["reason"], "token count")

    def test_non_numeric_structure_change_is_reported(self):
        base = make_brep([], [vertex(children="*")], "+1 0")
        changed = base.replace("0101101", "0101100", 1)
        outcome = compare(base, changed)
        self.assertFalse(outcome["equal"])

    def test_truncated_input_maps_to_compare_error(self):
        text = make_brep([], [vertex(reps=[["0.5", "1", "1", "0"]])], "+1 0")
        for cut in range(1, 8):
            with self.assertRaises(BrepCompareError):
                normalize(" ".join(text.split()[:-cut]))


class TypedStructuralTests(unittest.TestCase):
    """Structural fields must be exact; only real coordinates are toleranced."""

    def test_large_integer_ids_are_compared_exactly(self):
        base = make_brep([], [vertex(reps=[["0.5", "1", "1000000000000000", "0"]])], "+1 0")
        other = make_brep([], [vertex(reps=[["0.5", "1", "1000000000000001", "0"]])], "+1 0")
        outcome = compare(base, other)
        self.assertFalse(outcome["equal"])
        self.assertEqual(outcome["reason"], "integer token")

    def test_seven_bit_flags_are_compared_exactly(self):
        base = make_brep([], [vertex(flags="0101101")], "+1 0")
        other = make_brep([], [vertex(flags="0101100")], "+1 0")
        outcome = compare(base, other)
        self.assertFalse(outcome["equal"])
        self.assertEqual(outcome["reason"], "structural token")

    def test_reader_recomputed_checked_bit_is_masked(self):
        # Bits: Free, Modified, Checked, Orientable, Closed, Infinite, Convex. OCCT's V1
        # reader recomputes Checked, so only that bit is masked.
        without = make_brep([], [vertex(flags="1101000")], "+1 0")
        with_checked = make_brep([], [vertex(flags="1111000")], "+1 0")
        self.assertEqual(compare(without, with_checked), {"equal": True})
        other_bit = make_brep([], [vertex(flags="1100000")], "+1 0")
        self.assertFalse(compare(without, other_bit)["equal"])

    def test_edge_bool_flags_must_be_zero_or_one(self):
        text = make_brep([], [edge(same="2")], "+1 0")
        with self.assertRaises(BrepCompareError):
            normalize(text)

    def test_malformed_counts_block(self):
        for old, new in (("Locations 0", "Locations 3"), ("TShapes 1", "TShapes 2"),
                         ("TShapes 1", "TShapes -1")):
            text = make_brep([], [vertex()], "+1 0").replace(old, new, 1)
            with self.assertRaises(BrepCompareError):
                normalize(text)

    def test_real_coordinates_remain_toleranced(self):
        base = make_brep([], [vertex(x=1.0, y=0.0, z=0.0)], "+1 0")
        nudged = make_brep([], [vertex(x=1.0 + 1e-15, y=0.0, z=0.0)], "+1 0")
        self.assertEqual(compare(base, nudged), {"equal": True})
        moved = make_brep([], [vertex(x=1.5, y=0.0, z=0.0)], "+1 0")
        self.assertFalse(compare(base, moved)["equal"])


try:
    from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge,      # noqa: E402
                                    BRepBuilderAPI_MakeFace,
                                    BRepBuilderAPI_MakeWire,
                                    BRepBuilderAPI_Transform)
    from OCP.BRepTools import BRepTools
    from OCP.BRep import BRep_Builder
    from OCP.Geom import Geom_BSplineCurve
    from OCP.TColStd import TColStd_Array1OfInteger, TColStd_Array1OfReal
    from OCP.TColgp import TColgp_Array1OfPnt
    from OCP.TopTools import TopTools_FormatVersion_VERSION_1
    from OCP.TopLoc import TopLoc_Location
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt, gp_Trsf, gp_Vec
    _OCP_ERROR = None
except Exception as _error:  # pragma: no cover - environment dependent
    _OCP_ERROR = _error


def _export_v1(shape):
    stream = io.BytesIO()
    BRepTools.Write_s(shape, stream, False, False, TopTools_FormatVersion_VERSION_1)
    return stream.getvalue().decode("utf-8")


def _read_back(text, shape_type):
    loaded = shape_type()
    BRepTools.Read_s(loaded, io.BytesIO(text.encode("utf-8")), BRep_Builder())
    return loaded


def _rational_edge(weight=1.0, interior_z=1.0, first=None, last=None):
    poles = TColgp_Array1OfPnt(1, 3)
    poles.SetValue(1, gp_Pnt(0.0, 0.0, 0.0))
    poles.SetValue(2, gp_Pnt(1.0, interior_z, 0.0))
    poles.SetValue(3, gp_Pnt(2.0, 0.0, 0.0))
    weights = TColStd_Array1OfReal(1, 3)
    weights.SetValue(1, 1.0)
    weights.SetValue(2, weight)
    weights.SetValue(3, 1.0)
    knots = TColStd_Array1OfReal(1, 2)
    knots.SetValue(1, 0.0)
    knots.SetValue(2, 1.0)
    mults = TColStd_Array1OfInteger(1, 2)
    mults.SetValue(1, 3)
    mults.SetValue(2, 3)
    curve = Geom_BSplineCurve(poles, weights, knots, mults, 2, False)
    if first is None:
        return BRepBuilderAPI_MakeEdge(curve).Edge()
    return BRepBuilderAPI_MakeEdge(curve, first, last).Edge()


@unittest.skipIf(_OCP_ERROR is not None, f"OCP unavailable: {_OCP_ERROR}")
class NativeBrepTests(unittest.TestCase):
    def setUp(self):
        self.plane = BRepBuilderAPI_MakeFace(
            gp_Pln(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 0.0, 2.0, 0.0, 1.0).Face()
        self.wire = BRepBuilderAPI_MakeWire(
            BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 0), gp_Pnt(1, 0, 0)).Edge(),
            BRepBuilderAPI_MakeEdge(gp_Pnt(1, 0, 0), gp_Pnt(1, 1, 0)).Edge()).Wire()
        self.spline = _rational_edge(weight=0.5)

    def test_native_exports_are_v1_and_self_compare(self):
        for shape in (self.plane, self.wire, self.spline):
            text = _export_v1(shape)
            self.assertEqual(text.splitlines()[1], HEADER)
            self.assertEqual(compare(text, text), {"equal": True})

    def test_native_save_load_round_trip_is_equal(self):
        # A single read/write round trip is stable from the second write on: OCCT's V1
        # reader marks a re-read face as Checked, and flags are compared exactly.
        for shape in (self.plane, self.wire, self.spline):
            first = _export_v1(_read_back(_export_v1(shape), type(shape)))
            second = _export_v1(_read_back(first, type(shape)))
            outcome = compare(first, second)
            self.assertTrue(outcome["equal"], describe_difference(first, second))

    def test_native_copy_of_a_shape_compares_equal(self):
        for shape in (self.plane, self.wire, self.spline):
            text = _export_v1(shape)
            self.assertEqual(compare(text, _export_v1(shape)), {"equal": True})

    def test_native_interior_pole_only_detected(self):
        baseline = _rational_edge(weight=0.5, interior_z=1.0)
        changed = _rational_edge(weight=0.5, interior_z=1.4)
        left, right = _export_v1(baseline), _export_v1(changed)
        self.assertNotEqual(left, right)
        self.assertFalse(compare(left, right)["equal"])
        # Same baseline re-serialized is the only reference: the interior pole is the
        # single changed input.
        self.assertEqual(compare(left, _export_v1(
            _rational_edge(weight=0.5, interior_z=1.0))), {"equal": True})

    def test_native_modified_weight_detected(self):
        left = _export_v1(_rational_edge(weight=0.5))
        right = _export_v1(_rational_edge(weight=0.75))
        self.assertFalse(compare(left, right)["equal"])

    def test_native_translation_of_a_copy_detected(self):
        trsf = gp_Trsf()
        trsf.SetTranslation(gp_Vec(0.0, 3.0, 0.0))
        moved = BRepBuilderAPI_Transform(self.plane, trsf, True).Shape()
        self.assertFalse(compare(_export_v1(self.plane), _export_v1(moved))["equal"])

    def test_native_located_shape_round_trips_locations_table(self):
        trsf = gp_Trsf()
        trsf.SetTranslation(gp_Vec(2.0, 3.0, 0.0))
        located = self.plane.Moved(TopLoc_Location(trsf))
        text = _export_v1(located)
        self.assertNotIn("Locations 0", text)
        first = _export_v1(_read_back(text, type(located)))
        second = _export_v1(_read_back(first, type(located)))
        self.assertTrue(compare(first, second)["equal"],
                        describe_difference(first, second))

    def test_native_trim_change_detected(self):
        left = _export_v1(_rational_edge(weight=1.0))
        right = _export_v1(_rational_edge(weight=1.0, first=0.25, last=0.75))
        self.assertFalse(compare(left, right)["equal"])


try:
    import FreeCAD as _FreeCAD  # noqa: F401
    import Part as _Part  # noqa: F401
    _FREECAD_ERROR = None
except Exception as _freecad_error:  # pragma: no cover - environment dependent
    _FREECAD_ERROR = _freecad_error


@unittest.skipIf(_FREECAD_ERROR is not None, f"FreeCAD unavailable: {_FREECAD_ERROR}")
class FreeCadBrepTests(unittest.TestCase):
    """Real FreeCAD B-spline surface changes measured against one shared baseline."""

    def _bicubic(self, delta_pole=None, delta_weight=None):
        from FreeCAD import Base
        grid = [[Base.Vector(x / 3.0, y / 3.0, 0.0) for y in range(4)] for x in range(4)]
        weights = [[1.0] * 4 for _ in range(4)]
        if delta_pole:
            row, column, dz = delta_pole
            grid[row][column].z += dz
        if delta_weight:
            row, column, dw = delta_weight
            weights[row][column] += dw
        surface = _Part.BSplineSurface()
        if delta_weight:
            surface.buildFromPolesMultsKnots(grid, [4, 4], [4, 4], [0.0, 1.0], [0.0, 1.0],
                                             False, False, 3, 3, weights)
        else:
            surface.buildFromPolesMultsKnots(grid, [4, 4], [4, 4], [0.0, 1.0], [0.0, 1.0],
                                             False, False, 3, 3)
        return surface.toShape()

    def _trimmed(self, dx):
        """A B-spline surface face with a 2x2 trim window shifted by ``dx`` (same area)."""
        from FreeCAD import Base
        outer = _Part.makePolygon([Base.Vector(0, 0, 0), Base.Vector(10, 0, 0),
                                   Base.Vector(10, 10, 0), Base.Vector(0, 10, 0),
                                   Base.Vector(0, 0, 0)])
        x, y = 3.0 + dx, 4.0
        inner = _Part.makePolygon([Base.Vector(x, y, 0), Base.Vector(x + 2, y, 0),
                                   Base.Vector(x + 2, y + 2, 0), Base.Vector(x, y + 2, 0),
                                   Base.Vector(x, y, 0)])
        return _Part.makeFace([outer, inner], "Part::FaceMakerBullseye").toNurbs()

    def test_freecad_interior_pole_only_differs(self):
        baseline = self._bicubic()
        changed = self._bicubic(delta_pole=(1, 1, 0.05))
        self.assertFalse(compare(baseline.exportBrepToString(),
                                 changed.exportBrepToString())["equal"])

    def test_freecad_weight_only_differs(self):
        baseline = self._bicubic()
        changed = self._bicubic(delta_weight=(1, 1, 0.05))
        self.assertFalse(compare(baseline.exportBrepToString(),
                                 changed.exportBrepToString())["equal"])

    def test_freecad_same_area_shifted_trim_differs(self):
        baseline = self._trimmed(0.0)
        shifted = self._trimmed(3.0)
        self.assertEqual(baseline.Faces[0].Surface.__class__.__name__, "BSplineSurface")
        self.assertAlmostEqual(baseline.Area, shifted.Area, places=9)
        self.assertFalse(compare(baseline.exportBrepToString(),
                                 shifted.exportBrepToString())["equal"])


if __name__ == "__main__":
    unittest.main()
