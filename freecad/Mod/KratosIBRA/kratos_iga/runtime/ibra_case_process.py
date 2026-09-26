"""Portable processes: initial fields once; vector and reference-pressure loads.

Reference pressure writes the reference traction ``p * sign * amp(t) * n_ref`` into
``DEAD_LOAD`` at each condition's single quadrature point. ``n_ref`` and the
integrated condition area are measured in ONE frozen transaction: every unique node
of the geometry is temporarily placed at ``GetInitialPosition()``, then the X0
tangents/Jacobian and ``DomainSize()`` are read, then every coordinate is restored in
a ``finally`` block even if a getter/setter or the Jacobian raises. Nodes are
deduplicated by ``Id`` so a shared node is frozen/restored exactly once. The sign is
relative to the parametric ``du x dv`` direction, never the OCCT face orientation.
Outside the record interval, or outside the amplitude table domain, the traction is
exactly zero. The follower/current-normal pressure variables are never set.

The process rejects a condition whose geometry does not have exactly one quadrature
point, and requires the independent OCP source area exported with the case: a
missing/non-finite/non-positive expected area, or an integrated condition area that
disagrees with it (relative 1e-6), raises. Geometry is never repaired automatically.
"""
import math

import KratosMultiphysics as KM

try:
    import ibra_amplitude
except ImportError:  # loaded from source via importlib in tests
    import importlib.util
    from pathlib import Path
    _amplitude_spec = importlib.util.spec_from_file_location(
        "ibra_amplitude", Path(__file__).resolve().parent / "ibra_amplitude.py")
    ibra_amplitude = importlib.util.module_from_spec(_amplitude_spec)
    _amplitude_spec.loader.exec_module(ibra_amplitude)


def Factory(settings, model):
    return CaseProcess(model, settings["Parameters"])


def _parse_amplitude(settings):
    """Return parsed table rows, or None for an absent/null amplitude."""
    if not settings.Has("amplitude") or settings["amplitude"].IsNull():
        return None
    points = settings["amplitude"]["points"]
    count = points.size() if hasattr(points, "size") else len(points)
    return ibra_amplitude.parse([[points[i][0].GetDouble(), points[i][1].GetDouble()]
                                 for i in range(count)])


def _unique_nodes(geometry):
    """Geometry nodes deduplicated by ``Id`` (a shared node is touched once)."""
    nodes = {}
    for i in range(geometry.PointsNumber()):
        node = geometry[i]
        nodes.setdefault(node.Id, node)
    return list(nodes.values())


def reference_state(geometry):
    """Return ``(unit_normal, integrated_area)`` measured in one X0 transaction.

    Freezes every unique node to its initial position, reads ``Jacobian(0)`` (normal
    ``= (a1 x a2)/|a1 x a2|``) and ``DomainSize()``, then restores every coordinate in
    a ``finally`` block. The area therefore belongs to the reference configuration,
    not to any current/initial displacement.
    """
    count = geometry.IntegrationPointsNumber()
    if count != 1:
        raise RuntimeError(
            f"reference pressure: the condition geometry has {count} quadrature points; "
            "exactly one is required (fail closed, no silent per-point scaling).")
    nodes = _unique_nodes(geometry)
    saved = []
    try:
        for node in nodes:
            initial = node.GetInitialPosition()
            saved.append((node, node.X, node.Y, node.Z))
            node.X, node.Y, node.Z = initial.X, initial.Y, initial.Z
        jacobian = geometry.Jacobian(0)
        a1 = [jacobian[0, 0], jacobian[1, 0], jacobian[2, 0]]
        a2 = [jacobian[0, 1], jacobian[1, 1], jacobian[2, 1]]
        normal = [a1[1] * a2[2] - a1[2] * a2[1],
                  a1[2] * a2[0] - a1[0] * a2[2],
                  a1[0] * a2[1] - a1[1] * a2[0]]
        norm = math.sqrt(sum(value * value for value in normal))
        if not math.isfinite(norm) or norm <= 1.0e-30:
            raise RuntimeError("reference pressure: the reference normal is degenerate.")
        area = float(geometry.DomainSize())
        if not math.isfinite(area):
            raise RuntimeError("reference pressure: the reference integrated area is not finite.")
        return [value / norm for value in normal], area
    finally:
        for node, x, y, z in saved:
            node.X, node.Y, node.Z = x, y, z


class CaseProcess(KM.Process):
    def __init__(self, model, settings):
        super().__init__()
        self.part = model[settings["model_part_name"].GetString()]
        self.mode = settings["mode"].GetString()
        self.interval = None
        self.rows = None
        self.normals = {}
        if self.mode == "initial":
            self.name = settings["variable_name"].GetString()
            self.variable = KM.KratosGlobals.GetVariable(self.name)
            self.values = [None if settings["value"][i].IsNull()
                           else settings["value"][i].GetDouble() for i in range(3)]
            return
        if self.mode == "reference_pressure":
            self.pressure = settings["pressure"].GetDouble()
            self.sign = -1.0 if (settings.Has("normal_flip")
                                 and settings["normal_flip"].GetBool()) else 1.0
            self.interval = KM.IntervalUtility(settings)
            self.rows = _parse_amplitude(settings)
            self.expected_area = settings["expected_area"].GetDouble() \
                if settings.Has("expected_area") and not settings["expected_area"].IsNull() else None
            return
        self.name = settings["variable_name"].GetString()
        self.variable = KM.KratosGlobals.GetVariable(self.name)
        self.values = [None if settings["value"][i].IsNull()
                       else settings["value"][i].GetDouble() for i in range(3)]
        self.interval = KM.IntervalUtility(settings)
        self.rows = _parse_amplitude(settings)

    # ----------------------------------------------------------------- reference
    def _initialize_reference(self, conditions):
        if self.expected_area is None or not math.isfinite(self.expected_area) \
                or self.expected_area <= 0.0:
            raise RuntimeError(
                "reference pressure: the exported independent source area is missing or not "
                "finite/positive; reimport the STEP and re-export. The pressure load is not "
                "verified and is not applied silently.")
        total = 0.0
        self.normals = {}
        for condition in conditions:
            normal, area = reference_state(condition.GetGeometry())
            self.normals[condition.Id] = normal
            total += area
        if not math.isfinite(total) or total <= 0.0:
            raise RuntimeError("reference pressure: the integrated reference area is not finite "
                               "and positive.")
        relative = abs(total - self.expected_area) / self.expected_area
        if relative > 1.0e-6:
            raise RuntimeError(
                f"reference pressure: the integrated condition area {total!r} disagrees with the "
                f"independent OCP source area {self.expected_area!r} (relative error {relative:.3e}); "
                "the trimmed load domain is not resolved. Increase knot insertions/quadrature or "
                "reimport; geometry is never changed automatically.")

    def _amplitude(self, time):
        if self.rows is None:
            return 1.0
        return ibra_amplitude.evaluate(self.rows, time)

    # ----------------------------------------------------------------- lifecycle
    def ExecuteInitialize(self):
        if self.mode == "initial":
            for node in self.part.Nodes:
                for i, value in enumerate(self.values):
                    if value is not None:
                        component = KM.KratosGlobals.GetVariable(self.name + "_" + "XYZ"[i])
                        # Seed the history as well as current state before time integration.
                        for step in range(self.part.GetBufferSize()):
                            node.SetSolutionStepValue(component, step, value)
            return
        if self.mode == "reference_pressure":
            conditions = list(self.part.Conditions)
            if not conditions:
                raise RuntimeError("reference pressure: the target model part has no conditions.")
            self._initialize_reference(conditions)

    def ExecuteInitializeSolutionStep(self):
        if self.mode == "load":
            time = self.part.ProcessInfo[KM.TIME]
            active = self.interval.IsInInterval(time)
            factor = self._amplitude(time)
            value = KM.Vector([float(v) * factor if active else 0.0 for v in self.values])
            for condition in self.part.Conditions:
                condition.SetValue(self.variable, value)
            return
        if self.mode == "reference_pressure":
            time = self.part.ProcessInfo[KM.TIME]
            factor = self._amplitude(time) if self.interval.IsInInterval(time) else 0.0
            dead_load = KM.KratosGlobals.GetVariable("DEAD_LOAD")
            for condition in self.part.Conditions:
                normal = self.normals.get(condition.Id)
                if normal is None:
                    raise RuntimeError(f"reference pressure: condition {condition.Id} has no "
                                       "cached reference normal.")
                value = KM.Vector([self.pressure * self.sign * factor * component
                                   for component in normal])
                condition.SetValue(dead_load, value)

    def ExecuteBeforeSolutionLoop(self):
        self.ExecuteInitializeSolutionStep()
