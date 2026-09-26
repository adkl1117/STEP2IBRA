"""Throw-away FreeCAD display layer for one already-validated native result frame.

This module is imported by the **FreeCAD GUI process** and therefore never touches
h5py, the VTKHDF file, the project, the bindings or the CAD shape. The backend bridge
:mod:`kratos_iga.results_cli` has already read, validated and bounded one
``results.vtkhdf`` frame; this module only turns that bounded JSON frame into temporary
display geometry.

What it draws, and what it refuses to claim:

* Cells are built from the frame's **native** ``Connectivity``/``Offsets``/``Types``:
  VTK triangles (5) become triangular faces and VTK quads (9) become quadrilateral
  faces, or two triangles each when ``split_quads`` is requested. The frame's own
  per-point displacement values colour the faces through a fixed blue-cyan-green-
  yellow-red gradient; the minimum and maximum points of the selected component are
  added as marker spheres and their coordinates are returned to the caller.
* The plain VTKHDF has **no face/UV mapping**. Every plan and summary repeats
  ``mapping_state: "unavailable"``: these display elements are the native sampling
  cells, never the trimmed integration domain of the CAD face.
* Ownership is a reserved group name plus a persisted marker property. ``clear``
  removes only marker-owned objects, and a foreign object that merely shares a
  reserved name is refused rather than adopted or deleted.
* Making the result visible changes **only** ``ViewObject.Visible`` of the CAD objects
  the caller names, and ``clear`` restores exactly the visibility it recorded. Shape,
  project, bindings and geometry fingerprints are never touched, and no ``IbraBrepId``
  property is ever added, so a result object can never enter the analysis entity map.

The pure helpers (:func:`gradient_color`, :func:`colors_for_values`,
:func:`cell_faces`, :func:`extrema_markers`, :func:`build_render_plan`) import nothing
from FreeCAD and are unit-tested directly; :class:`ResultLayer` imports ``FreeCAD``
and ``Part`` lazily inside its methods and is accepted by a real FreeCAD smoke test,
not by a mock.
"""
from copy import deepcopy
import json
import math

GROUP_NAME = "IBRA_Result"
PREFIX = "IBRA_Result_"
SURFACE_NAME = PREFIX + "Surface"
EXTREMA_NAME = PREFIX + "Extrema"
# Ownership never relies on the name alone: every object carries this persisted marker.
MARKER_PROPERTY = "IbraResultTag"
MARKER_VALUE = "P0-6-native-result"
SAVED_VISIBILITY_PROPERTY = "IbraResultSavedVisibility"

VTK_TRIANGLE = 5
VTK_QUAD = 9
CELL_WIDTHS = {VTK_TRIANGLE: 3, VTK_QUAD: 4}
COMPONENTS = ("X", "Y", "Z", "magnitude")
# Drawing caps mirror results_cli.MAX_DISPLAY_*; a frame above them is refused here too
# so the GUI can never be handed a mesh that freezes it.
MAX_DRAW_CELLS = 10_000
MAX_DRAW_POINTS = 50_000
# Fixed five-stop colour ramp (blue -> cyan -> green -> yellow -> red).
COLOR_STOPS = (
    (0.00, (0.0, 0.0, 1.0)),
    (0.25, (0.0, 1.0, 1.0)),
    (0.50, (0.0, 1.0, 0.0)),
    (0.75, (1.0, 1.0, 0.0)),
    (1.00, (1.0, 0.0, 0.0)),
)
MARKER_COLORS = {"max": (1.0, 0.15, 0.15), "min": (0.15, 0.15, 1.0)}
MAPPING_UNAVAILABLE = "unavailable"
MAPPING_NOTE = ("a plain VTKHDF carries no face/UV mapping; the display cells are not "
                "the trimmed integration domain")


class ResultLayerError(Exception):
    """The frame cannot be drawn, or the document would be altered beyond our own objects."""


# --------------------------------------------------------------------------- #
# pure helpers (no FreeCAD, no h5py)
# --------------------------------------------------------------------------- #

def _as_points(points, name="points"):
    if not isinstance(points, (list, tuple)) or not points:
        raise ResultLayerError(f"{name} must be a non-empty array of 3D points")
    result = []
    for index, point in enumerate(points):
        if not isinstance(point, (list, tuple)) or len(point) < 3:
            raise ResultLayerError(f"{name}[{index}] is not a 3D point")
        row = []
        for value in point[:3]:
            try:
                number = float(value)
            except (TypeError, ValueError) as error:
                raise ResultLayerError(
                    f"{name}[{index}] is not a number: {value!r}") from error
            if not math.isfinite(number):
                raise ResultLayerError(f"{name}[{index}] contains a non-finite coordinate")
            row.append(number)
        result.append(row)
    return result


def _as_finite_values(values, name="values"):
    if not isinstance(values, (list, tuple)):
        raise ResultLayerError(f"{name} must be an array")
    result = []
    for index, value in enumerate(values):
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise ResultLayerError(f"{name}[{index}] is not a number: {value!r}") from error
        if not math.isfinite(number):
            raise ResultLayerError(f"{name}[{index}] is not finite")
        result.append(number)
    return result


def _as_integers(values, name):
    if not isinstance(values, (list, tuple)):
        raise ResultLayerError(f"{name} must be an array")
    result = []
    for index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ResultLayerError(f"{name}[{index}] must be an integer, got {value!r}")
        if isinstance(value, float) and not value.is_integer():
            raise ResultLayerError(f"{name}[{index}] must be an integer, got {value!r}")
        result.append(int(value))
    return result


def gradient_color(fraction):
    """The fixed ramp colour for ``fraction`` in ``[0, 1]`` (clamped)."""
    position = float(fraction)
    if position != position:  # NaN
        position = 0.0
    position = min(max(position, 0.0), 1.0)
    for (low, low_color), (high, high_color) in zip(COLOR_STOPS, COLOR_STOPS[1:]):
        if position <= high:
            span = high - low
            weight = 0.0 if span <= 0.0 else (position - low) / span
            return tuple(round(low_color[channel]
                               + (high_color[channel] - low_color[channel]) * weight, 6)
                         for channel in range(3))
    return tuple(COLOR_STOPS[-1][1])


def colors_for_values(values, vmin=None, vmax=None):
    """Per-value ramp colours plus the range actually used.

    Returns ``(colors, vmin, vmax, constant)``. A constant range (including a single
    value) has no gradient to show, so every face gets the middle colour instead of a
    division by zero.
    """
    values = _as_finite_values(values)
    if not values:
        raise ResultLayerError("values must not be empty")
    low = min(values) if vmin is None else float(vmin)
    high = max(values) if vmax is None else float(vmax)
    if not math.isfinite(low) or not math.isfinite(high) or not high > low:
        return [gradient_color(0.5) for _ in values], low, high, True
    return [gradient_color((value - low) / (high - low)) for value in values], low, high, False


def cell_faces(connectivity, offsets, types, values, split_quads=False):
    """Native VTK cells as drawable faces over the per-point ``values``.

    Exactly the offsets/types the VTKHDF holds are honoured - no cell is invented,
    reordered or dropped. A triangle yields one 3-index face; a quad yields one
    4-index face, or two triangles ``(0,1,2)`` and ``(0,2,3)`` when ``split_quads``.
    Every face value is the mean of its own vertices' values. Malformed offsets, an
    unsupported type, a wrong width or an out-of-range index refuses.
    """
    connectivity = _as_integers(connectivity, "connectivity")
    offsets = _as_integers(offsets, "offsets")
    types = _as_integers(types, "types")
    values = _as_finite_values(values)
    if len(offsets) < 1 or offsets[0] != 0:
        raise ResultLayerError("offsets must start at 0")
    if offsets[-1] != len(connectivity):
        raise ResultLayerError(
            f"offsets end at {offsets[-1]}, connectivity holds {len(connectivity)} ids")
    if len(offsets) != len(types) + 1:
        raise ResultLayerError(
            f"offsets has {len(offsets)} entries for {len(types)} cell types")
    if any(high < low for low, high in zip(offsets, offsets[1:])):
        raise ResultLayerError("offsets must be non-decreasing")
    faces, face_values = [], []
    for index, kind in enumerate(types):
        width = CELL_WIDTHS.get(kind)
        if width is None:
            raise ResultLayerError(
                f"cell {index} has unsupported VTK type {kind}; only {VTK_TRIANGLE} "
                f"(triangle) and {VTK_QUAD} (quad) are supported")
        start, stop = offsets[index], offsets[index + 1]
        if stop - start != width:
            raise ResultLayerError(
                f"cell {index} (type {kind}) spans {stop - start} ids, expected {width}")
        corners = connectivity[start:stop]
        if any(corner < 0 or corner >= len(values) for corner in corners):
            raise ResultLayerError(
                f"cell {index} references a point outside [0, {len(values)})")
        mean = sum(values[corner] for corner in corners) / float(width)
        if kind == VTK_TRIANGLE or not split_quads:
            faces.append(list(corners))
            face_values.append(mean)
        else:
            faces.append([corners[0], corners[1], corners[2]])
            faces.append([corners[0], corners[2], corners[3]])
            face_values.extend([mean, mean])
    return {"faces": faces, "face_values": face_values, "cells": len(types),
            "faces_count": len(faces), "split_quads": bool(split_quads),
            "triangles_count": sum(1 for face in faces if len(face) == 3)}


def extrema_markers(extrema, component):
    """Max/min markers of one component from a validated frame's ``extrema`` block."""
    if not isinstance(extrema, dict):
        return []
    entry = extrema.get(component)
    if not isinstance(entry, dict):
        return []
    markers = []
    for kind, position_key, value_key, index_key in (
            ("max", "max_position", "max", "max_index"),
            ("min", "min_position", "min", "min_index")):
        position = entry.get(position_key)
        if not isinstance(position, (list, tuple)) or len(position) < 3:
            continue
        coordinates = []
        for value in position[:3]:
            number = float(value)
            if not math.isfinite(number):
                coordinates = None
                break
            coordinates.append(number)
        if coordinates is None:
            continue
        raw_value = entry.get(value_key)
        value = float(raw_value) if isinstance(raw_value, (int, float)) \
            and not isinstance(raw_value, bool) and math.isfinite(float(raw_value)) else None
        raw_index = entry.get(index_key)
        index = int(raw_index) if isinstance(raw_index, int) \
            and not isinstance(raw_index, bool) else None
        markers.append({"kind": kind, "component": component, "value": value,
                        "position": coordinates, "index": index,
                        "unit": entry.get("unit")})
    return markers


def marker_radius(points):
    """A small marker radius derived from the displayed geometry, never zero."""
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    zs = [point[2] for point in points]
    diagonal = math.sqrt((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2
                         + (max(zs) - min(zs)) ** 2)
    return max(diagonal * 0.012, 1.0e-6)


def build_render_plan(frame, *, component=None, split_quads=False,
                      max_cells=MAX_DRAW_CELLS, max_points=MAX_DRAW_POINTS):
    """Everything :class:`ResultLayer` needs to draw one frame, FreeCAD-free.

    Raises :class:`ResultLayerError` on a failed/oversized/malformed frame instead of
    drawing a partial or stale picture.
    """
    if not isinstance(frame, dict):
        raise ResultLayerError("the frame must be a JSON object")
    if frame.get("ok") is False:
        raise ResultLayerError("the frame reports failure; nothing is drawn")
    points = _as_points(frame.get("points"))
    values = _as_finite_values(frame.get("values"))
    if len(points) != len(values):
        raise ResultLayerError(
            f"the frame has {len(points)} points but {len(values)} values")
    if len(points) > int(max_points):
        raise ResultLayerError(
            f"the frame has {len(points)} points, above the {int(max_points)} draw cap")
    mesh = frame.get("mesh")
    if not isinstance(mesh, dict):
        raise ResultLayerError("the frame has no mesh block")
    for key in ("connectivity", "offsets", "types"):
        if not isinstance(mesh.get(key), (list, tuple)):
            raise ResultLayerError(f"the frame mesh has no {key} array")
    if len(mesh["types"]) > int(max_cells):
        raise ResultLayerError(
            f"the frame has {len(mesh['types'])} cells, above the {int(max_cells)} draw cap")
    step = frame.get("step") if isinstance(frame.get("step"), dict) else {}
    chosen = component or step.get("component") or "magnitude"
    if chosen not in COMPONENTS:
        raise ResultLayerError(f"component must be one of {COMPONENTS}, got {chosen!r}")
    drawn = cell_faces(mesh["connectivity"], mesh["offsets"], mesh["types"], values,
                       split_quads=split_quads)
    colors, low, high, constant = colors_for_values(drawn["face_values"])
    markers = extrema_markers(frame.get("extrema"), chosen)
    return {
        "component": chosen,
        "points": points,
        "faces": drawn["faces"],
        "face_values": drawn["face_values"],
        "face_colors": [list(color) for color in colors],
        "value_range": [low, high],
        "value_range_constant": constant,
        "point_value_range": [min(values), max(values)],
        "points_count": len(points),
        "cells_count": drawn["cells"],
        "faces_count": drawn["faces_count"],
        "triangles_count": drawn["triangles_count"],
        "split_quads": drawn["split_quads"],
        "marker_radius": marker_radius(points),
        "markers": markers,
        "mapping_state": MAPPING_UNAVAILABLE,
        "mapping_note": MAPPING_NOTE,
        "step": step,
        "notes": [],
    }


def summary_from_plan(plan, faces_drawn, faces_skipped):
    """The bounded, JSON-safe description a GUI panel can display after drawing."""
    return {
        "component": plan["component"],
        "step_index": plan["step"].get("index"),
        "time": plan["step"].get("time"),
        "scale": plan["step"].get("scale"),
        "unit": plan["step"].get("requested_unit"),
        "points": plan["points_count"],
        "cells": plan["cells_count"],
        "faces_drawn": faces_drawn,
        "faces_skipped": faces_skipped,
        "value_range": plan["value_range"],
        "point_value_range": plan["point_value_range"],
        "value_range_constant": plan["value_range_constant"],
        "markers": deepcopy(plan["markers"]),
        "mapping_state": plan["mapping_state"],
        "mapping_note": plan["mapping_note"],
        "notes": list(plan["notes"]),
    }


# --------------------------------------------------------------------------- #
# document-side layer (FreeCAD/Part imported lazily)
# --------------------------------------------------------------------------- #

class ResultLayer:
    """Owns only its own marked display objects in one FreeCAD document.

    The layer never registers anything in the analysis entity map, never adds
    ``IbraBrepId``, never writes the project and never touches any Shape except the one
    it puts on its own ``Part::Feature`` objects. ``clear`` removes exactly the
    marker-owned objects it created and restores every ``ViewObject.Visible`` it
    changed.
    """

    GROUP_NAME = GROUP_NAME
    PREFIX = PREFIX
    SURFACE_NAME = SURFACE_NAME
    EXTREMA_NAME = EXTREMA_NAME
    MARKER_PROPERTY = MARKER_PROPERTY
    MARKER_VALUE = MARKER_VALUE
    SAVED_VISIBILITY_PROPERTY = SAVED_VISIBILITY_PROPERTY

    def __init__(self, document=None):
        self.doc = document
        self.objects = {}            # role -> object name
        self.saved_visibility = {}   # CAD object name -> the visibility to restore
        self.notes = []
        self.summary = None

    # -- document plumbing ------------------------------------------------- #
    def attach(self, document):
        """Bind to ``document``, clearing our objects from the previous one first."""
        if self.doc is not None and self.doc is not document:
            self.clear(self.doc)
        self.doc = document
        self._reset_records()

    def _live_document(self):
        import FreeCAD as App
        doc = self.doc
        if doc is None:
            return None
        try:
            if doc.Name in App.listDocuments():
                return doc
        except Exception:  # noqa: BLE001 - a deleted document object
            return None
        return None

    def _reset_records(self):
        self.objects = {}
        self.notes = []
        self.summary = None

    @classmethod
    def owns(cls, obj):
        """True only for an object carrying this layer's own persisted marker."""
        return getattr(obj, cls.MARKER_PROPERTY, None) == cls.MARKER_VALUE

    @classmethod
    def _mark(cls, obj):
        if not hasattr(obj, cls.MARKER_PROPERTY):
            obj.addProperty("App::PropertyString", cls.MARKER_PROPERTY, "IBRA")
            try:
                obj.setEditorMode(cls.MARKER_PROPERTY, 1)
            except Exception:  # noqa: BLE001 - not fatal to ownership
                pass
        setattr(obj, cls.MARKER_PROPERTY, cls.MARKER_VALUE)

    def _ensure_group(self):
        """Return our marked group; never adopt, mark or delete a foreign one."""
        doc = self._live_document()
        if doc is None:
            raise ResultLayerError("no live FreeCAD document is attached to the result layer")
        group = doc.getObject(self.GROUP_NAME)
        if group is not None and not self.owns(group):
            raise ResultLayerError(
                f"an unrelated object already uses the reserved name {self.GROUP_NAME!r}; "
                "the result layer will not adopt, mark or delete it. Rename or remove it "
                "first")
        if group is None:
            group = doc.addObject("App::DocumentObjectGroup", self.GROUP_NAME)
            group.Label = "IBRA Result (display only)"
            self._mark(group)
        return group

    def _feature(self, name, shapes):
        """Create/replace one marker-owned ``Part::Feature`` inside our group."""
        import Part
        doc = self._live_document()
        if doc is None:
            raise ResultLayerError("no live FreeCAD document is attached to the result layer")
        obj = doc.getObject(name)
        if obj is not None and not self.owns(obj):
            raise ResultLayerError(
                f"an unrelated object already uses the reserved name {name!r}; the result "
                "layer will not adopt, mark or delete it. Rename or remove it first")
        if obj is None:
            obj = doc.addObject("Part::Feature", name)
        obj.Label = "IBRA Result surface" if name == self.SURFACE_NAME \
            else ("IBRA Result extrema" if name == self.EXTREMA_NAME else name)
        obj.Shape = shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)
        self._mark(obj)
        group = self._ensure_group()
        try:
            if obj not in group.Group:
                group.addObject(obj)
        except Exception as error:  # noqa: BLE001 - grouping is cosmetic, ownership is not
            self.notes.append(f"{name}: could not be added to the result group ({error})")
        return obj

    def _remove_owned(self, name):
        doc = self._live_document()
        obj = doc.getObject(name) if doc is not None else None
        if obj is None or not self.owns(obj):
            return False
        try:
            doc.removeObject(name)
        except Exception as error:  # noqa: BLE001
            self.notes.append(f"{name}: could not be removed ({error})")
            return False
        return True

    # -- CAD visibility ---------------------------------------------------- #
    @staticmethod
    def _visibility_of(obj):
        view = getattr(obj, "ViewObject", None)
        if view is None or not hasattr(view, "Visibility"):
            return None
        try:
            return bool(view.Visibility)
        except Exception:  # noqa: BLE001
            return None

    def _persist_saved_visibility(self):
        doc = self._live_document()
        if doc is None:
            return
        group = doc.getObject(self.GROUP_NAME)
        if group is None or not self.owns(group):
            return
        if not hasattr(group, self.SAVED_VISIBILITY_PROPERTY):
            group.addProperty("App::PropertyString", self.SAVED_VISIBILITY_PROPERTY, "IBRA")
            try:
                group.setEditorMode(self.SAVED_VISIBILITY_PROPERTY, 1)
            except Exception:  # noqa: BLE001
                pass
        try:
            setattr(group, self.SAVED_VISIBILITY_PROPERTY,
                    json.dumps(self.saved_visibility))
        except Exception as error:  # noqa: BLE001
            self.notes.append(f"saved visibility was not persisted ({error})")

    @staticmethod
    def _persisted_visibility(doc):
        group = doc.getObject(GROUP_NAME)
        if group is None or not ResultLayer.owns(group):
            return {}
        raw = getattr(group, SAVED_VISIBILITY_PROPERTY, "")
        if not raw:
            return {}
        try:
            saved = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        if not isinstance(saved, dict):
            return {}
        return {str(name): bool(state) for name, state in saved.items()}

    def _restore_visibility(self, doc, saved):
        for name, state in saved.items():
            obj = doc.getObject(name)
            view = getattr(obj, "ViewObject", None) if obj is not None else None
            if view is None or not hasattr(view, "Visibility"):
                continue
            try:
                view.Visibility = bool(state)
            except Exception as error:  # noqa: BLE001
                self.notes.append(f"{name}: visibility was not restored ({error})")

    def _hide_cad(self, doc, cad_objects):
        """Remember and hide only the CAD objects the caller explicitly names."""
        changed = 0
        for entry in cad_objects or ():
            name = entry if isinstance(entry, str) else getattr(entry, "Name", None)
            if not name:
                continue
            obj = doc.getObject(name)
            if obj is None or obj.Name in self.objects.values():
                continue
            state = self._visibility_of(obj)
            if state is None:
                continue
            if name not in self.saved_visibility:
                self.saved_visibility[name] = state
            try:
                obj.ViewObject.Visibility = False
                changed += 1
            except Exception as error:  # noqa: BLE001
                self.notes.append(f"{name}: visibility was not changed ({error})")
        if self.saved_visibility:
            self._persist_saved_visibility()
        return changed

    @staticmethod
    def _cell_shapes(vertices, indices, part):
        """One native cell as a face, split into two triangles if it is not buildable.

        A deformed quad can be non-planar, which OCC refuses as a planar face; splitting
        the same four vertices along their shared diagonal keeps the cell visible instead
        of dropping it. A cell that still cannot be built is reported, never faked.
        """
        polygon = [vertices[index] for index in indices]
        polygon.append(polygon[0])
        try:
            return [part.Face(part.makePolygon(polygon))]
        except Exception:  # noqa: BLE001 - try the triangular fallback below
            pass
        if len(indices) != 4:
            return []
        built = []
        for triple in ((0, 1, 2), (0, 2, 3)):
            triangle = [vertices[indices[index]] for index in triple]
            triangle.append(triangle[0])
            try:
                built.append(part.Face(part.makePolygon(triangle)))
            except Exception:  # noqa: BLE001 - one bad triangle drops the whole cell
                return []
        return built

    # -- public operations ------------------------------------------------- #
    def apply_frame(self, frame, *, component=None, split_quads=False, cad_objects=()):
        """Draw one validated frame; returns the bounded summary for the GUI panel.

        ``cad_objects`` names the original CAD objects whose ``ViewObject.Visible`` may
        be switched off so the result is visible; nothing else is ever hidden, and
        :meth:`clear` restores every recorded state.
        """
        import FreeCAD as App
        import Part
        doc = self._live_document()
        if doc is None:
            raise ResultLayerError("no live FreeCAD document is attached to the result layer")
        self.notes = []
        plan = build_render_plan(frame, component=component, split_quads=split_quads)
        self._ensure_group()

        vertices = [App.Vector(point[0], point[1], point[2]) for point in plan["points"]]
        shapes, colors = [], []
        skipped = 0
        split_fallbacks = 0
        for indices, color in zip(plan["faces"], plan["face_colors"]):
            built = self._cell_shapes(vertices, indices, Part)
            if not built:
                skipped += 1
                continue
            if len(built) > 1:
                split_fallbacks += 1
            shapes.extend(built)
            colors.extend([tuple(color)] * len(built))
        if not shapes:
            raise ResultLayerError(
                f"none of the {plan['cells_count']} native cells could be built as faces; "
                "the frame is not drawable")
        if split_fallbacks:
            self.notes.append(
                f"{split_fallbacks} non-planar quad(s) were drawn as two triangles each; "
                "the cell count and domain are unchanged")
        surface = self._feature(self.SURFACE_NAME, shapes)
        view = getattr(surface, "ViewObject", None)
        if view is not None:
            try:
                view.DisplayMode = "Flat Lines"
                view.ShapeColor = (0.8, 0.8, 0.8)
                view.LineColor = (0.12, 0.12, 0.12)
                view.LineWidth = 1.0
                view.DiffuseColor = colors
                view.Selectable = False
            except Exception as error:  # noqa: BLE001 - view styling is optional
                self.notes.append(f"surface colouring limited ({error})")
        self.objects["surface"] = surface.Name

        marker_shapes = []
        for marker in plan["markers"]:
            try:
                marker_shapes.append(Part.makeSphere(
                    plan["marker_radius"],
                    App.Vector(marker["position"][0], marker["position"][1],
                               marker["position"][2])))
            except Exception as error:  # noqa: BLE001
                self.notes.append(f"{marker['kind']} marker was not created ({error})")
        if marker_shapes:
            extrema = self._feature(self.EXTREMA_NAME, marker_shapes)
            extrema_view = getattr(extrema, "ViewObject", None)
            if extrema_view is not None:
                try:
                    extrema_view.ShapeColor = MARKER_COLORS["max"]
                    extrema_view.Selectable = False
                except Exception as error:  # noqa: BLE001
                    self.notes.append(f"extrema styling limited ({error})")
            self.objects["extrema"] = extrema.Name
        elif self._remove_owned(self.EXTREMA_NAME):
            self.notes.append("a previous extrema marker was removed for a frame without "
                              "reported extrema")

        self._hide_cad(doc, cad_objects)
        plan["notes"] = list(self.notes)
        self.summary = summary_from_plan(plan, len(shapes), skipped)
        return self.summary

    def set_results_visible(self, visible):
        """Toggle only our own result objects; CAD visibility is left alone."""
        doc = self._live_document()
        if doc is None:
            return 0
        changed = 0
        for role, name in self.objects.items():
            obj = doc.getObject(name)
            if obj is None or not self.owns(obj):
                continue
            view = getattr(obj, "ViewObject", None)
            if view is None:
                continue
            try:
                view.Visibility = bool(visible)
                changed += 1
            except Exception as error:  # noqa: BLE001
                self.notes.append(f"{role}: visibility was not changed ({error})")
        return changed

    def show(self):
        return self.set_results_visible(True)

    def hide(self):
        return self.set_results_visible(False)

    def clear(self, doc=None):
        """Remove only marker-owned objects and restore every visibility we changed."""
        doc = doc if doc is not None else self._live_document()
        if doc is None:
            self.saved_visibility = {}
            self._reset_records()
            self.doc = None
            return
        saved = self._persisted_visibility(doc)
        if self.doc is None or doc is self.doc:
            saved.update(self.saved_visibility)
        self._restore_visibility(doc, saved)

        names = set(self.objects.values())
        try:
            for obj in list(doc.Objects):
                name = getattr(obj, "Name", "")
                if name.startswith(self.PREFIX) and self.owns(obj):
                    names.add(name)
        except Exception:  # noqa: BLE001 - best effort while closing a document
            pass
        for name in names:
            obj = doc.getObject(name)
            if obj is not None and self.owns(obj):
                try:
                    doc.removeObject(name)
                except Exception:  # noqa: BLE001
                    pass
        group = doc.getObject(self.GROUP_NAME)
        if group is not None and self.owns(group):
            try:
                if not group.Group:
                    doc.removeObject(self.GROUP_NAME)
            except Exception:  # noqa: BLE001
                pass
        self.saved_visibility = {}
        self._reset_records()
        self.doc = None
