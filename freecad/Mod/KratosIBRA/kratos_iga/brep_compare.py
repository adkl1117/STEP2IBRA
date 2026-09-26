"""Strict semantic comparison of two OCCT ASCII BREP strings (stdlib only).

Grammar verified token-by-token against the OCCT ``V7_8_0`` primary sources, not
against guesses:

``TopTools_LocationSet::Write`` / ``Read``
    ``Locations <n>`` then ``n`` records with **implicit ordinal ids 1..n**; the leading
    token of a record is its **type**, never an id:

    * type ``1``: exactly 12 doubles (one 3x4 affine matrix, ``WriteTrsf``);
    * type ``2``: repeated ``(earlierId, power)`` pairs terminated by a single ``0``,
      composed as ``L = L_earlier.Powered(power) * L`` (accumulator on the right, so the
      left-most pair is the right-most factor; negative powers are legal); a composite may
      only reference *earlier* ids.

``TopTools_ShapeSet::Write`` / ``Write(shape)`` / ``Read``
    ``TShapes <n>``, then ``n`` records: ``shapeType``, per-type geometry, one 7-bit flags
    token, then zero-or-more **child pairs** until ``*``.  A pair is a combined
    ``orientation+id`` token (``+2``/``-3``/``i4``/``e5``) followed by a location id.  There
    are no triples, no child counts and no location prefix.  After all records comes the
    single **root pair** (no trailing ``*``).

``BRepTools_ShapeSet::WriteGeometry`` / ``ReadGeometry``
    * ``Ve``: tolerance, x, y, z, then ``parameter, type`` entries until a ``type`` of
      ``0`` (whose parameter must be ``0``): ``1 curveId loc``, ``2 pcurveId surfId loc``,
      ``3 parameter2 surfId loc``.
    * ``Ed``: tolerance, three 0/1 bool flags, then representations until ``0``:
      ``1 curveId loc first last``; ``2 pcurveId surfId loc first last``;
      ``3 pcurve1 pcurve2 continuity surfId loc first last``;
      ``4 continuity surf1 loc1 surf2 loc2``; ``5 polyId loc``;
      ``6 polyId triId loc``; ``7 poly1 poly2 triId loc``.  Continuity is a *string*.
    * ``Fa``: restriction, tolerance, surfId, loc, then an optional triangulation tag.
      Only the verified V1 no-triangulation form is compared; the tag fails closed.
    * ``Wi`` / ``Sh`` / ``So`` / ``CS`` / ``Co``: **no** geometry fields.

What the comparison does:

* canonical items are **typed**: locations are 12-float matrix tuples compared with the
  caller's tolerance, real geometry values are floats compared with that tolerance, integer
  fields (shape/curve/surface ids, representation types, counts, bool flags) are Python
  ints compared exactly, and shape types / continuity / ``orientation+id`` references are
  exact tokens.  The 7-bit flags word is compared exactly too, except the ``Checked`` bit,
  which OCCT's V1 reader recomputes and therefore cannot round-trip (every other bit is
  exact).  A generic float tolerance can never mask a changed large integer ID or a changed
  flag bit;
* the unused ``Locations`` table is omitted and every *referenced* location id -- including
  the root reference -- is replaced by its resolved matrix, so identity/duplicate/reordered
  bookkeeping cannot cause a false difference; unknown or out-of-range ids raise;
* geometry tables (``Curve2ds`` .. ``Triangulations``) and every other token (poles,
  weights, knots, p-curves, ranges, tolerances) are preserved verbatim;
* non-finite tokens (``nan``/``inf`` spellings and overflowing spellings such as ``1e999``)
  are rejected up front, even when both sides are identical;
* only the verified ``CASCADE Topology V1`` grammar is compared; anything else raises so the
  caller blocks instead of guessing.  The optional Draw preamble line that precedes the
  banner in files produced through ``DBRep_DrawableShape`` (``DBRep_DrawableShape`` before
  ``CASCADE ...``) is preserved verbatim and is not treated as geometry.
"""
from __future__ import annotations

import math
import re

NUMBER = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
NON_FINITE = re.compile(r"^[+-]?(?:nan|inf(?:inity)?)$", re.IGNORECASE)
INTEGER = re.compile(r"^(?:0|[1-9]\d*)$")
SIGNED_INTEGER = re.compile(r"^-?\d+$")
FLAGS = re.compile(r"^[01]{7}$")
PAIR = re.compile(r"^([+\-ie])(\d+)$")
CONTINUITY = frozenset({"C0", "C1", "C2", "C3", "CN", "G1", "G2"})

BANNER = ("CASCADE", "Topology", "V1,", "(c)", "Matra-Datavision")
SHAPE_TYPES = ("Ve", "Ed", "Wi", "Fa", "Sh", "So", "CS", "Co")
AGGREGATES = ("Wi", "Sh", "So", "CS", "Co")

# Row-major 3x4 affine matrix: [r00 r01 r02 tx  r10 r11 r12 ty  r20 r21 r22 tz].
IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)
DEFAULT_TOLERANCE = 1e-12


class BrepCompareError(ValueError):
    """Malformed, truncated, unsupported or non-comparable BREP content."""


class BrepUnsupportedVersion(BrepCompareError):
    """The ASCII writer version/grammar is not the verified V1 one."""


class _Exact:
    """An exact structural token (flag word, shape type, continuity, reference)."""

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = str(value)

    def __eq__(self, other):
        return isinstance(other, _Exact) and other.value == self.value

    def __hash__(self):
        return hash((_Exact, self.value))

    def __repr__(self):
        return f"Exact({self.value!r})"


# --------------------------------------------------------------------------- #
# token helpers
# --------------------------------------------------------------------------- #

def _at(tokens, index, context):
    if index < 0 or index >= len(tokens):
        raise BrepCompareError(f"truncated BREP: missing {context}")
    return tokens[index]


def _number(token, context):
    """Return ``float(token)`` for a finite number, ``None`` for a non-number."""
    if NON_FINITE.match(token):
        raise BrepCompareError(f"non-finite value {token!r} in {context}")
    if not NUMBER.match(token):
        return None
    value = float(token)
    if not math.isfinite(value):
        raise BrepCompareError(f"non-finite value {token!r} in {context}")
    return value


def _real(token, context):
    value = _number(token, context)
    if value is None:
        raise BrepCompareError(f"expected a number, got {token!r} in {context}")
    return value


def _integer(token, context):
    if not INTEGER.match(token):
        raise BrepCompareError(f"expected an integer, got {token!r} in {context}")
    return int(token)


def _signed_integer(token, context):
    """Location powers are the only signed integers the V1 writer emits."""
    if not SIGNED_INTEGER.match(token):
        raise BrepCompareError(f"expected a signed integer, got {token!r} in {context}")
    return int(token)


def _reject_non_finite(tokens):
    for token in tokens:
        if NON_FINITE.match(token):
            raise BrepCompareError(f"non-finite token {token!r} in BREP text")
        if NUMBER.match(token) and not math.isfinite(float(token)):
            raise BrepCompareError(f"non-finite token {token!r} in BREP text")


def _finite_matrix(matrix, context):
    for value in matrix:
        if not math.isfinite(value):
            raise BrepCompareError(f"{context} resolves to a non-finite matrix")
    return matrix


# --------------------------------------------------------------------------- #
# affine algebra
# --------------------------------------------------------------------------- #

def _multiply(left, right):
    a, b = left, right
    out = []
    for row in range(3):
        for column in range(4):
            if column == 3:
                out.append(a[row * 4] * b[3] + a[row * 4 + 1] * b[7]
                           + a[row * 4 + 2] * b[11] + a[row * 4 + 3])
            else:
                out.append(a[row * 4] * b[column] + a[row * 4 + 1] * b[4 + column]
                           + a[row * 4 + 2] * b[8 + column])
    return tuple(out)


def _inverse(matrix):
    r00, r01, r02, t0, r10, r11, r12, t1, r20, r21, r22, t2 = matrix
    det = (r00 * (r11 * r22 - r12 * r21) - r01 * (r10 * r22 - r12 * r20)
           + r02 * (r10 * r21 - r11 * r20))
    if abs(det) < 1e-300:
        raise BrepCompareError("location matrix is singular and cannot be inverted")
    inv = [(r11 * r22 - r12 * r21) / det, (r02 * r21 - r01 * r22) / det,
           (r01 * r12 - r02 * r11) / det, (r12 * r20 - r10 * r22) / det,
           (r00 * r22 - r02 * r20) / det, (r02 * r10 - r00 * r12) / det,
           (r10 * r21 - r11 * r20) / det, (r01 * r20 - r00 * r21) / det,
           (r00 * r11 - r01 * r10) / det]
    return (inv[0], inv[1], inv[2], -(inv[0] * t0 + inv[1] * t1 + inv[2] * t2),
            inv[3], inv[4], inv[5], -(inv[3] * t0 + inv[4] * t1 + inv[5] * t2),
            inv[6], inv[7], inv[8], -(inv[6] * t0 + inv[7] * t1 + inv[8] * t2))


def _powered(matrix, power):
    if power == 0:
        return IDENTITY
    if power < 0:
        return _powered(_inverse(matrix), -power)
    result, base, exponent = IDENTITY, matrix, power
    while exponent:
        if exponent & 1:
            result = _multiply(result, base)
        base = _multiply(base, base)
        exponent >>= 1
    return result


# --------------------------------------------------------------------------- #
# Locations table
# --------------------------------------------------------------------------- #

def _parse_locations(tokens, index):
    """Return ``(resolved_by_id, end_index)``; ids are the implicit ordinals 1..n."""
    count = _integer(_at(tokens, index + 1, "Locations count"), "Locations count")
    cursor = index + 2
    resolved = {}
    for ordinal in range(1, count + 1):
        kind_token = _at(tokens, cursor, f"location {ordinal} type")
        kind = _integer(kind_token, f"location {ordinal} type")
        cursor += 1
        if kind == 1:
            values = []
            for offset in range(12):
                token = _at(tokens, cursor + offset, f"location {ordinal} matrix")
                values.append(_real(token, f"location {ordinal} matrix"))
            cursor += 12
            matrix = tuple(values)
        elif kind == 2:
            matrix = IDENTITY
            while True:
                reference = _integer(
                    _at(tokens, cursor, f"location {ordinal} reference"),
                    f"location {ordinal} reference")
                cursor += 1
                if reference == 0:
                    break
                if reference >= ordinal or reference not in resolved:
                    raise BrepCompareError(
                        f"location {ordinal} references {reference}, which is not an earlier "
                        "location; the table is not in the supported form")
                power = _signed_integer(_at(tokens, cursor, f"location {ordinal} power"),
                                        f"location {ordinal} power")
                cursor += 1
                matrix = _multiply(_powered(resolved[reference], power), matrix)
        else:
            raise BrepCompareError(f"location {ordinal}: unknown record type {kind!r}")
        resolved[ordinal] = _finite_matrix(matrix, f"location {ordinal}")
    return resolved, cursor


def _location_ref(token, resolved, context):
    """Resolve a location field; unknown ids raise, they are never passed through."""
    ident = _integer(token, context)
    if ident == 0:
        return IDENTITY
    if ident not in resolved:
        raise BrepCompareError(f"unknown location id {ident} in {context}")
    return resolved[ident]


# --------------------------------------------------------------------------- #
# per-shape geometry (typed items)
# --------------------------------------------------------------------------- #

def _real_value(tokens, cursor, context):
    return _real(_at(tokens, cursor, context), context)


def _int_value(tokens, cursor, context):
    return _integer(_at(tokens, cursor, context), context)


def _bool_value(tokens, cursor, context):
    token = _at(tokens, cursor, context)
    if token not in ("0", "1"):
        raise BrepCompareError(f"expected a 0/1 flag, got {token!r} in {context}")
    return int(token)


def _continuity_value(tokens, cursor, context):
    token = _at(tokens, cursor, context)
    if token not in CONTINUITY:
        raise BrepCompareError(f"unknown continuity {token!r} in {context}")
    return _Exact(token)


def _vertex_geometry(tokens, cursor, resolved):
    out = [_real_value(tokens, cursor, "vertex tolerance")]
    cursor += 1
    for axis in "xyz":
        out.append(_real_value(tokens, cursor, f"vertex {axis}"))
        cursor += 1
    while True:
        parameter = _real_value(tokens, cursor, "vertex point parameter")
        cursor += 1
        kind = _int_value(tokens, cursor, "vertex representation type")
        cursor += 1
        if kind == 0:
            if parameter != 0.0:
                raise BrepCompareError(
                    "vertex point representations must be terminated by '0 0'")
            out.append(0)
            return out, cursor
        out.append(kind)
        if kind == 1:
            out.append(_int_value(tokens, cursor, "vertex curve id"))
            cursor += 1
            out.append(_location_ref(_at(tokens, cursor, "vertex location"),
                                     resolved, "vertex curve location"))
            cursor += 1
        elif kind == 2:
            out.append(_int_value(tokens, cursor, "vertex pcurve id"))
            cursor += 1
            out.append(_int_value(tokens, cursor, "vertex surface id"))
            cursor += 1
            out.append(_location_ref(_at(tokens, cursor, "vertex location"),
                                     resolved, "vertex surface location"))
            cursor += 1
        elif kind == 3:
            out.append(_real_value(tokens, cursor, "vertex parameter 2"))
            cursor += 1
            out.append(_int_value(tokens, cursor, "vertex surface id"))
            cursor += 1
            out.append(_location_ref(_at(tokens, cursor, "vertex location"),
                                     resolved, "vertex surface location"))
            cursor += 1
        else:
            raise BrepCompareError(f"unknown vertex representation type {kind}")


def _edge_geometry(tokens, cursor, resolved):
    out = [_real_value(tokens, cursor, "edge tolerance")]
    cursor += 1
    for flag in ("same-parameter", "same-range", "degenerated"):
        out.append(_bool_value(tokens, cursor, f"edge {flag} flag"))
        cursor += 1
    while True:
        representation = _int_value(tokens, cursor, "edge representation type")
        cursor += 1
        if representation == 0:
            out.append(0)
            return out, cursor
        out.append(representation)
        if representation == 1:
            out.append(_int_value(tokens, cursor, "edge curve id"))
            cursor += 1
            out.append(_location_ref(_at(tokens, cursor, "edge location"),
                                     resolved, "edge curve location"))
            cursor += 1
            out.append(_real_value(tokens, cursor, "edge first parameter"))
            cursor += 1
            out.append(_real_value(tokens, cursor, "edge last parameter"))
            cursor += 1
        elif representation == 2:
            out.append(_int_value(tokens, cursor, "edge pcurve id"))
            cursor += 1
            out.append(_int_value(tokens, cursor, "edge surface id"))
            cursor += 1
            out.append(_location_ref(_at(tokens, cursor, "edge location"),
                                     resolved, "edge surface location"))
            cursor += 1
            out.append(_real_value(tokens, cursor, "edge first parameter"))
            cursor += 1
            out.append(_real_value(tokens, cursor, "edge last parameter"))
            cursor += 1
        elif representation == 3:
            out.append(_int_value(tokens, cursor, "edge pcurve 1 id"))
            cursor += 1
            out.append(_int_value(tokens, cursor, "edge pcurve 2 id"))
            cursor += 1
            out.append(_continuity_value(tokens, cursor, "edge closed-surface continuity"))
            cursor += 1
            out.append(_int_value(tokens, cursor, "edge surface id"))
            cursor += 1
            out.append(_location_ref(_at(tokens, cursor, "edge location"),
                                     resolved, "edge surface location"))
            cursor += 1
            out.append(_real_value(tokens, cursor, "edge first parameter"))
            cursor += 1
            out.append(_real_value(tokens, cursor, "edge last parameter"))
            cursor += 1
        elif representation == 4:
            out.append(_continuity_value(tokens, cursor, "edge regularity continuity"))
            cursor += 1
            out.append(_int_value(tokens, cursor, "edge regular surface 1"))
            cursor += 1
            out.append(_location_ref(_at(tokens, cursor, "edge location 1"),
                                     resolved, "edge regular location 1"))
            cursor += 1
            out.append(_int_value(tokens, cursor, "edge regular surface 2"))
            cursor += 1
            out.append(_location_ref(_at(tokens, cursor, "edge location 2"),
                                     resolved, "edge regular location 2"))
            cursor += 1
        elif representation == 5:
            out.append(_int_value(tokens, cursor, "edge polygon id"))
            cursor += 1
            out.append(_location_ref(_at(tokens, cursor, "edge location"),
                                     resolved, "edge polygon location"))
            cursor += 1
        elif representation == 6:
            out.append(_int_value(tokens, cursor, "edge polygon id"))
            cursor += 1
            out.append(_int_value(tokens, cursor, "edge triangulation id"))
            cursor += 1
            out.append(_location_ref(_at(tokens, cursor, "edge location"),
                                     resolved, "edge polygon location"))
            cursor += 1
        elif representation == 7:
            out.append(_int_value(tokens, cursor, "edge polygon 1 id"))
            cursor += 1
            out.append(_int_value(tokens, cursor, "edge polygon 2 id"))
            cursor += 1
            out.append(_int_value(tokens, cursor, "edge triangulation id"))
            cursor += 1
            out.append(_location_ref(_at(tokens, cursor, "edge location"),
                                     resolved, "edge polygon location"))
            cursor += 1
        else:
            raise BrepCompareError(f"unknown edge representation {representation}")


def _face_geometry(tokens, cursor, resolved):
    restriction = _int_value(tokens, cursor, "face natural restriction")
    if restriction == 2:
        raise BrepUnsupportedVersion(
            "triangulation-only face records are not in the verified V1 comparison grammar")
    if restriction not in (0, 1):
        raise BrepCompareError(f"invalid face natural restriction {restriction!r}")
    cursor += 1
    out = [restriction, _real_value(tokens, cursor, "face tolerance")]
    cursor += 1
    out.append(_int_value(tokens, cursor, "face surface id"))
    cursor += 1
    out.append(_location_ref(_at(tokens, cursor, "face location"),
                             resolved, "face surface location"))
    cursor += 1
    return out, cursor


def _shape_geometry(shape_type, tokens, cursor, resolved):
    if shape_type == "Ve":
        return _vertex_geometry(tokens, cursor, resolved)
    if shape_type == "Ed":
        return _edge_geometry(tokens, cursor, resolved)
    if shape_type == "Fa":
        return _face_geometry(tokens, cursor, resolved)
    if shape_type in AGGREGATES:
        return [], cursor
    raise BrepCompareError(f"unsupported shape record type {shape_type!r}")


# --------------------------------------------------------------------------- #
# TShapes records
# --------------------------------------------------------------------------- #

def _check_pair(token, count, context):
    match = PAIR.match(token)
    if not match:
        raise BrepCompareError(f"malformed shape reference {token!r} in {context}")
    ident = int(match.group(2))
    if not 1 <= ident <= count:
        raise BrepCompareError(
            f"shape reference {token!r} in {context} is outside 1..{count}")
    return token


def _comparable_flags(token):
    """The exact 7-bit flags word, with OCCT's V1 reader artifact masked.

    Bits are ``Free, Modified, Checked, Orientable, Closed, Infinite, Convex``.  The V1
    reader deliberately ignores the stored ``Checked`` bit (``isChecked`` is only honoured
    for V2+) and recomputes it through ``Check``/``BRepTools::Update``, so the same shape
    read back legitimately differs there.  That one bit is masked; the other six are
    compared exactly.
    """
    return token[:2] + "0" + token[3:]


def _parse_tshapes(tokens, start, resolved):
    count_token = _at(tokens, start + 1, "TShapes count")
    count = _integer(count_token, "TShapes count")
    out = ["TShapes", count]
    cursor = start + 2
    for ordinal in range(1, count + 1):
        shape_type = _at(tokens, cursor, f"shape {ordinal} type")
        if shape_type not in SHAPE_TYPES:
            raise BrepCompareError(
                f"shape {ordinal}: unknown shape type {shape_type!r}")
        out.append(_Exact(shape_type))
        cursor += 1
        geometry, cursor = _shape_geometry(shape_type, tokens, cursor, resolved)
        out.extend(geometry)

        flags = _at(tokens, cursor, f"shape {ordinal} flags")
        if not FLAGS.match(flags):
            if flags == "2":
                raise BrepUnsupportedVersion(
                    "face triangulation tags are not in the verified V1 comparison grammar")
            raise BrepCompareError(
                f"shape {ordinal}: expected a 7-bit flags token, got {flags!r}")
        out.append(_Exact(_comparable_flags(flags)))
        cursor += 1

        while True:
            token = _at(tokens, cursor, f"shape {ordinal} child list")
            if token == "*":
                out.append(_Exact("*"))
                cursor += 1
                break
            out.append(_Exact(_check_pair(token, count, f"shape {ordinal} child list")))
            cursor += 1
            out.append(_location_ref(_at(tokens, cursor, f"shape {ordinal} child location"),
                                     resolved, f"shape {ordinal} child location"))
            cursor += 1

    root = _check_pair(_at(tokens, cursor, "root reference"), count, "root reference")
    out.append(_Exact(root))
    cursor += 1
    out.append(_location_ref(_at(tokens, cursor, "root location"), resolved, "root location"))
    cursor += 1
    if cursor != len(tokens):
        raise BrepCompareError(
            f"{len(tokens) - cursor} unexpected token(s) after the root reference")
    return out


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #

def normalize(text, *, tolerance=DEFAULT_TOLERANCE):
    """Canonical item tuple for an ASCII BREP string.

    Items are typed: real geometry values are floats, integer fields are ints, exact
    structural tokens are :class:`_Exact`, locations are 12-float matrix tuples and the
    untouched geometry tables stay verbatim strings.  The unused ``Locations`` table is
    omitted; only referenced locations appear, so identity/duplicate/reordered bookkeeping
    cannot cause a false difference.
    """
    if not isinstance(text, str) or not text.strip():
        raise BrepCompareError("empty BREP text")
    tokens = text.split()
    try:
        return _normalize_tokens(tokens)
    except IndexError as error:  # defensive: direct indexing must never leak
        raise BrepCompareError(f"truncated BREP content: {error}") from error


def _normalize_tokens(tokens):
    _reject_non_finite(tokens)
    if len(tokens) < 6:
        raise BrepCompareError("truncated BREP: header is incomplete")
    banner_index = None
    for index in range(len(tokens) - 4):
        if tuple(tokens[index:index + 5]) == BANNER:
            banner_index = index
            break
    if banner_index is None:
        window = " ".join(tokens[:8])
        raise BrepUnsupportedVersion(
            f"unsupported ASCII BREP grammar {window!r}; only the verified V1 writer is "
            "compared")
    locations_index = banner_index + 5
    if _at(tokens, locations_index, "Locations table") != "Locations":
        raise BrepCompareError("BREP text has no Locations table")

    resolved, locations_end = _parse_locations(tokens, locations_index)
    try:
        tshapes_index = tokens.index("TShapes", locations_end)
    except ValueError as error:
        raise BrepCompareError("BREP text has no TShapes table") from error

    # tokens[:banner_index] is the optional Draw preamble (e.g. "DBRep_DrawableShape");
    # it is not geometry and is kept verbatim so format differences still surface.
    canonical = ["BREP"]
    canonical.extend(tokens[:banner_index])
    canonical.extend(tokens[locations_end:tshapes_index])   # geometry tables verbatim
    canonical.extend(_parse_tshapes(tokens, tshapes_index, resolved))
    return tuple(canonical)


def _close(a, b, tolerance):
    deviation = abs(a - b)
    return deviation <= tolerance or deviation / max(abs(a), abs(b), 1.0) <= tolerance


def _items_equal(a, b, tolerance):
    if isinstance(a, tuple) and isinstance(b, tuple):
        return len(a) == len(b) and all(_close(x, y, tolerance) for x, y in zip(a, b))
    if isinstance(a, _Exact) and isinstance(b, _Exact):
        return a.value == b.value
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    if isinstance(a, float) and isinstance(b, float):
        return _close(a, b, tolerance)
    if isinstance(a, str) and isinstance(b, str):
        if a == b:
            return True
        left, right = _number(a, "left"), _number(b, "right")
        return left is not None and right is not None and _close(left, right, tolerance)
    return False


def _difference(index, a, b, tolerance):
    if isinstance(a, tuple) and isinstance(b, tuple) and len(a) == len(b):
        return {"equal": False, "reason": "location matrix", "index": index,
                "left": a, "right": b}
    if isinstance(a, _Exact) and isinstance(b, _Exact):
        return {"equal": False, "reason": "structural token", "index": index,
                "left": a.value, "right": b.value}
    if isinstance(a, int) and isinstance(b, int):
        return {"equal": False, "reason": "integer token", "index": index,
                "left": a, "right": b}
    if isinstance(a, float) and isinstance(b, float):
        return {"equal": False, "reason": "numeric tolerance", "index": index,
                "left": a, "right": b, "deviation": abs(a - b)}
    if isinstance(a, str) and isinstance(b, str):
        left, right = _number(a, "left"), _number(b, "right")
        if left is None or right is None:
            return {"equal": False, "reason": "non-numeric token", "index": index,
                    "left": a, "right": b}
        return {"equal": False, "reason": "numeric tolerance", "index": index,
                "left": a, "right": b, "deviation": abs(left - right)}
    return {"equal": False, "reason": "structural token", "index": index,
            "left": a, "right": b}


def compare(left_text, right_text, *, tolerance=DEFAULT_TOLERANCE):
    """Semantic comparison; raises only for malformed/unsupported input."""
    left = normalize(left_text, tolerance=tolerance)
    right = normalize(right_text, tolerance=tolerance)
    if len(left) != len(right):
        return {"equal": False, "reason": "token count", "left": len(left),
                "right": len(right)}
    for index in range(len(left)):
        a, b = left[index], right[index]
        if _items_equal(a, b, tolerance):
            continue
        return _difference(index, a, b, tolerance)
    return {"equal": True}


def describe_difference(left_text, right_text, *, tolerance=DEFAULT_TOLERANCE):
    """One-line diagnostic, or ``""`` when equal."""
    outcome = compare(left_text, right_text, tolerance=tolerance)
    if outcome["equal"]:
        return ""
    if outcome["reason"] == "token count":
        return (f"brep token count {outcome['left']} != {outcome['right']} after semantic "
                "location normalization")
    if outcome["reason"] == "location matrix":
        return f"brep location matrix {outcome['index']} differs beyond {tolerance:g}"
    if outcome["reason"] == "integer token":
        return (f"brep integer token {outcome['index']} differs "
                f"({outcome['left']} vs {outcome['right']})")
    if outcome["reason"] == "structural token":
        return (f"brep structural token {outcome['index']} differs "
                f"({outcome['left']!r} vs {outcome['right']!r})")
    if outcome["reason"] == "non-numeric token":
        return (f"brep structural token {outcome['index']} differs "
                f"({outcome['left']!r} vs {outcome['right']!r})")
    return (f"brep numeric token {outcome['index']} differs beyond {tolerance:g} "
            f"({outcome['left']!r} vs {outcome['right']!r})")
