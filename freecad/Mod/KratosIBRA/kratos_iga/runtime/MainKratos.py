"""Run from any working directory. --check initializes and verifies without solving.

P0-4 extends the existing ``run_report.json`` (old fields are preserved) and adds the
initialized statistics that can be read back from the actual Kratos model:

* root nodes plus the nodes that actually contribute to elements/conditions;
* element/condition counts and the per-entity ``IntegrationPointsNumber()`` counts;
* displacement DOF total/fixed/free (definition written into the report);
* per active patch: actual degree, control points, positive knot spans, integrated
  physical area = sum of ``element.GetGeometry().DomainSize()``;
* the independent OCCT source area exported by ``bundle``, compared patch by patch.

The comparison is fail-closed: a mismatch raises before ``RunSolutionLoop`` in both
``--check`` and solve mode, so the known Kratos 10.4.3 contained-hole over-integration
cannot silently produce a solvable model. Condition (load) geometries are never added
to a patch area. A case whose ``export_report.json`` predates the source-area metadata
is reported as ``unknown``/reimport-required, never as verified.

P0-6 shard B2 extends the same report, additively, with the mechanical fields read from
the real Kratos in-memory model after every ``FinalizeSolutionStep``:

* strong supports: ``REACTION_X/Y/Z`` on the fixed translational DOFs of every
  ``constraints_process_list`` ``Support_*`` sub-model part, deduplicated across
  sub-model parts by (node id, DOF). Fixity is read *before* the base implementation
  runs, because the ``assign_*_variable_process`` processes release the DOFs in their
  own ``ExecuteFinalizeSolutionStep``; measuring after it would silently see no support.
* applied loads: the resultant of ``condition.CalculateRightHandSide`` on the real
  ``Load_*`` conditions. A right-hand side whose length is not ``3 * geometry nodes`` or
  that is not finite makes that item unavailable; the load is never replaced by a
  ``pressure x area`` product.
* penalty supports (``Support_*``) and penalty couplings (``Coupling_*``) conditions:
  the raw condition right-hand side is reported separately and is *not* a signed
  physical reaction, because the sign convention and DOF layout are not confirmed. It is
  never mixed into the fixed-node ``REACTION`` total.
* equilibrium: the raw signed vector (strong + loads) and its residual, plus coverage
  flags. ``pass`` needs complete signed coverage *and* verified convergence evidence;
  a static linear single-iteration solve can therefore never be ``pass`` (the P0-5
  ~0.252% load-axis error and spurious transverse reaction stay visible).
"""
import argparse
import json
import math
from pathlib import Path
import os

import KratosMultiphysics as KM
import KratosMultiphysics.IgaApplication
import KratosMultiphysics.LinearSolversApplication
from KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_analysis import StructuralMechanicsAnalysis

DISPLACEMENT_COMPONENTS = ("DISPLACEMENT_X", "DISPLACEMENT_Y", "DISPLACEMENT_Z")
AREA_TOLERANCE_RELATIVE = 1.0e-6
DOF_DEFINITION = "DISPLACEMENT_X/Y/Z DOFs owned by the root model part nodes"

# ---------------------------------------------------------------- P0-6 B2 force evidence
FORCE_AXES = ("X", "Y", "Z")
SUPPORT_PREFIX = "Support_"
COUPLING_PREFIX = "Coupling_"
LOAD_PREFIX = "Load_"
FORCES_SCHEMA = "ibra.run_forces/v1"
FORCES_MEASURED_AT = ("FinalizeSolutionStep (real Kratos in-memory model: measured after "
                      "SolveSolutionStep and before the base class releases the fixed DOFs)")
EQUILIBRIUM_RELATIVE_TOLERANCE = 1.0e-6
PENALTY_REACTION_REASON = (
    "the sign convention and DOF layout of this condition right-hand side are not confirmed against "
    "the target model, so it is reported RAW and is NOT a signed physical reaction; it is never "
    "mixed into the fixed-node REACTION total")
LINEAR_CONVERGENCE_REASON = (
    "analysis_type 'linear' performs one direct solve and never exercises an iterative convergence "
    "criterion; P0-5 recorded a ~0.252% load-axis reaction error and a spurious transverse reaction "
    "for the Shell3p single residual iteration, so this is not a complete equilibrium - a converged "
    "'non_linear' run is required for an equilibrium pass")
STRONG_REACTION_DEFINITION = (
    "REACTION_X/Y/Z read on the fixed translational DOFs of the constraints_process_list Support_* "
    "nodes, deduplicated across sub-model parts by (node id, DOF); non-fixed components are excluded")


class AreaCheckMismatch(RuntimeError):
    """The initialized integration area disagrees with the independent OCCT source."""


def _read_export_report(folder):
    path = Path(folder) / "export_report.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001 - a broken report is reported as missing metadata
        return None


def _positive_spans(knots):
    values = [float(knot) for knot in knots]
    return sum(1 for a, b in zip(values, values[1:]) if b > a)


def _dof_statistics(part):
    total = fixed = 0
    for node in part.Nodes:
        for name in DISPLACEMENT_COMPONENTS:
            variable = KM.KratosGlobals.GetVariable(name)
            if node.HasDofFor(variable):
                total += 1
                if node.IsFixed(variable):
                    fixed += 1
    return {"total": total, "fixed": fixed, "free": total - fixed, "definition": DOF_DEFINITION}


def _nodes_of(entities):
    nodes = set()
    for entity in entities:
        geometry = entity.GetGeometry()
        for i in range(geometry.PointsNumber()):
            nodes.add(geometry[i].Id)
    return nodes


def _integration_counts(entities):
    per_entity, unavailable_ids, counts = [], [], []
    for entity in entities:
        try:
            count = int(entity.GetGeometry().IntegrationPointsNumber())
        except Exception:  # noqa: BLE001 - report unavailable, never infer a count
            unavailable_ids.append(entity.Id)
            continue
        per_entity.append({"id": entity.Id, "integration_points": count})
        counts.append(count)
    return {"entities": len(entities), "total": sum(counts), "distinct": sorted(set(counts)),
            "histogram": {str(count): counts.count(count) for count in sorted(set(counts))},
            "per_entity": per_entity, "unavailable_ids": unavailable_ids}


def _finite_or_none(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _patch_report(part, patch, tolerance):
    face_id = patch.get("face_id")
    name = f"Patch_{face_id}"
    elements = list(part.GetSubModelPart(name).Elements) if part.HasSubModelPart(name) else []
    actual_area = 0.0
    area_finite = True
    for element in elements:
        try:
            value = float(element.GetGeometry().DomainSize())
        except Exception:  # noqa: BLE001
            area_finite = False
            continue
        if not math.isfinite(value):
            area_finite = False
            continue
        actual_area += value

    geometry = next((g for g in part.Geometries
                     if g.Id == face_id and hasattr(g, "KnotsU")), None)
    degree = control_points = spans = None
    knots_u = knots_v = None
    if geometry is not None:
        try:
            degree = [int(geometry.PolynomialDegree(0)), int(geometry.PolynomialDegree(1))]
        except Exception:  # noqa: BLE001
            degree = None
        try:
            control_points = [int(geometry.PointsNumberInDirection(0)),
                              int(geometry.PointsNumberInDirection(1))]
        except Exception:  # noqa: BLE001
            control_points = None
        try:
            raw_u = [_finite_or_none(k) for k in geometry.KnotsU()]
            raw_v = [_finite_or_none(k) for k in geometry.KnotsV()]
            if all(k is not None for k in raw_u) and all(k is not None for k in raw_v):
                knots_u, knots_v = raw_u, raw_v
                spans = [_positive_spans(knots_u), _positive_spans(knots_v)]
        except Exception:  # noqa: BLE001
            spans = None

    # Per-patch authority: a mixed cache must not disable the comparison for the
    # patches it did measure. A declared non-finite/negative area is normalized to
    # null with an explicit reason and fails, so no NaN/inf can reach the JSON.
    patch_state = patch.get("source_area_state")
    measured = patch_state == "measured"
    declared = patch.get("source_area")
    expected = None
    invalid_expected = None
    if declared is None:
        if measured:
            invalid_expected = "the patch is marked measured but declares no source_area"
    else:
        try:
            value = float(declared)
        except (TypeError, ValueError):
            invalid_expected = f"declared source_area is not a number: {declared!r}"
        else:
            if not math.isfinite(value):
                invalid_expected = f"declared source_area is not finite: {value!r}"
            elif value <= 0:
                invalid_expected = f"declared source_area is not positive: {value!r}"
            else:
                expected = value
    relative_error = match = None
    if invalid_expected is not None:
        match = False
    elif measured:
        if not area_finite:
            match = False
        else:
            relative_error = (0.0 if actual_area == expected
                              else abs(actual_area - expected) / expected)
            relative_error = _finite_or_none(relative_error)
            match = relative_error is not None and relative_error <= tolerance

    estimate = patch.get("final_grid_control_points")
    grid_match = None
    if estimate is not None and control_points is not None:
        try:
            grid_match = [int(v) for v in estimate] == list(control_points)
        except (TypeError, ValueError):
            grid_match = None

    return {
        "face_id": face_id,
        "source_area_state": patch_state,
        "elements": len(elements),
        "integration_points": _integration_counts(elements),
        "degree": degree,
        "control_points": control_points,
        "positive_knot_spans": spans,
        "knots_u": knots_u,
        "knots_v": knots_v,
        "actual_area": actual_area if area_finite else None,
        "actual_area_finite": area_finite,
        "expected_area": expected,
        "expected_area_invalid": invalid_expected,
        "relative_error": relative_error,
        "area_match": match,
        "control_points_estimate": list(estimate) if isinstance(estimate, (list, tuple)) else estimate,
        "grid_match": grid_match,
        "requested_quadrature_per_span": patch.get("requested_quadrature_per_span"),
        "target_degree": patch.get("target_degree"),
    }


def _member(node, key):
    """Read a child of a ``KM.Parameters`` node or of a plain mapping; ``None`` if absent.

    The runtime passes real ``KM.Parameters``; the targeted unit tests pass nested
    mappings. Both are supported so the pure force logic is testable without Kratos.
    """
    if node is None:
        return None
    has = getattr(node, "Has", None)
    if callable(has):
        try:
            if not has(key):
                return None
        except Exception:  # noqa: BLE001 - an unreadable container is reported as absent
            return None
        try:
            return node[key]
        except Exception:  # noqa: BLE001
            return None
    if isinstance(node, dict):
        return node.get(key)
    return None


def _count(node):
    if node is None:
        return None
    size = getattr(node, "size", None)
    if callable(size):
        try:
            return int(size())
        except Exception:  # noqa: BLE001
            return None
    if isinstance(node, (list, tuple)):
        return len(node)
    return None


def _as_string(node):
    if node is None:
        return None
    getter = getattr(node, "GetString", None)
    if callable(getter):
        try:
            return str(getter())
        except Exception:  # noqa: BLE001
            return None
    return node if isinstance(node, str) else None


def _as_bool(node):
    if node is None:
        return None
    getter = getattr(node, "GetBool", None)
    if callable(getter):
        try:
            return bool(getter())
        except Exception:  # noqa: BLE001
            return None
    return node if isinstance(node, bool) else None


def _as_int(node):
    if node is None:
        return None
    getter = getattr(node, "GetInt", None)
    if callable(getter):
        try:
            return int(getter())
        except Exception:  # noqa: BLE001
            return None
    if isinstance(node, int) and not isinstance(node, bool):
        return node
    return None


def _finite_vector(values):
    result = []
    for value in values:
        finite = _finite_or_none(value)
        if finite is None:
            return None
        result.append(finite)
    return result


def _entity_id(entity):
    try:
        return int(entity.Id)
    except Exception:  # noqa: BLE001 - an entity without a usable id is reported as null
        return None


def _sub_model_part_tail(full_name):
    return full_name.rsplit(".", 1)[-1] if full_name else ""


def _process_rows(parameters, list_name):
    rows = _member(_member(parameters, "processes"), list_name)
    count = _count(rows)
    if not count:
        return []
    result = []
    for index in range(count):
        try:
            result.append(rows[index])
        except Exception:  # noqa: BLE001 - a malformed row is skipped, never guessed
            continue
    return result


def _referenced_sub_parts(parameters, list_name, prefix):
    names = []
    for row in _process_rows(parameters, list_name):
        full = _as_string(_member(_member(row, "Parameters"), "model_part_name"))
        tail = _sub_model_part_tail(full)
        if tail.startswith(prefix) and tail not in names:
            names.append(tail)
    return names


def _compute_reactions_setting(parameters):
    node = _member(_member(parameters, "solver_settings"), "compute_reactions")
    if node is None:
        return {"enabled": True, "declared": False, "reason": None,
                "source": "solver_settings.compute_reactions is absent; the Kratos "
                          "StructuralMechanicsSolver default (true) applies"}
    value = _as_bool(node)
    if value is None:
        return {"enabled": False, "declared": True,
                "reason": "solver_settings.compute_reactions is not a readable boolean; nodal support "
                          "reactions are unavailable",
                "source": "solver_settings.compute_reactions"}
    if not value:
        return {"enabled": False, "declared": True,
                "reason": "solver_settings.compute_reactions is false; nodal support reactions were "
                          "never computed",
                "source": "solver_settings.compute_reactions"}
    return {"enabled": True, "declared": True, "reason": None,
            "source": "solver_settings.compute_reactions"}


def _process_info_value(process_info, name):
    if process_info is None:
        return None
    try:
        variable = KM.KratosGlobals.GetVariable(name)
    except Exception:  # noqa: BLE001
        return None
    has = getattr(process_info, "Has", None)
    if callable(has):
        try:
            if not has(variable):
                return None
            return process_info[variable]
        except Exception:  # noqa: BLE001
            return None
    if isinstance(process_info, dict):
        return process_info.get(name)
    return None


def _convergence_evidence(parameters, part, solve_converged):
    settings = _member(parameters, "solver_settings")
    analysis_type = _as_string(_member(settings, "analysis_type"))
    solver_type = _as_string(_member(settings, "solver_type"))
    max_iteration = _as_int(_member(settings, "max_iteration"))
    process_info = getattr(part, "ProcessInfo", None)
    iterations = _finite_or_none(_process_info_value(process_info, "NL_ITERATION_NUMBER"))
    residual = _finite_or_none(_process_info_value(process_info, "RESIDUAL_NORM"))
    linear = (analysis_type or "").strip().lower() == "linear"
    verified = (not linear) and solve_converged is True and iterations is not None
    if linear:
        reason = LINEAR_CONVERGENCE_REASON
    elif solve_converged is not True:
        reason = ("the solver did not report a converged step (SolveSolutionStep returned "
                  f"{solve_converged!r})")
        verified = False
    elif iterations is None:
        reason = ("no finite NL_ITERATION_NUMBER was recorded, so the iteration history cannot "
                  "confirm convergence")
        verified = False
    else:
        reason = ("a non-linear solve reported convergence after a recorded iteration count; "
                  "residual evidence is reported raw")
    evidence = {
        "analysis_type": analysis_type,
        "solver_type": solver_type,
        "declared_max_iteration": max_iteration,
        "solve_reported_converged": None if solve_converged is None else bool(solve_converged),
        "nl_iteration_number": iterations,
        "residual_norm": residual,
        "verified": bool(verified),
        "reason": reason,
    }
    if linear:
        evidence["known_linear_single_iteration_residual"] = {
            "load_axis_relative_error": 0.00252,
            "spurious_transverse_reaction_n": -0.003434,
            "source": "P0-5 numerics handoff record (docs/P0_5_NUMERICS_HANDOFF_zh.md)",
            "meaning": ("recorded prior evidence that a single static-linear Shell3p iteration is "
                        "not a complete reaction equilibrium; it is quoted, not re-measured here"),
        }
    return evidence


def _condition_rhs(condition, process_info):
    """``(values, expected_size, actual_size, reason)``; ``values`` is ``None`` if unavailable."""
    try:
        geometry = condition.GetGeometry()
        points = int(geometry.PointsNumber())
    except Exception as error:  # noqa: BLE001 - reported, never guessed
        return None, None, None, f"condition geometry is unreadable ({type(error).__name__}: {error})"
    expected = 3 * points
    try:
        vector = KM.Vector()
        condition.CalculateRightHandSide(vector, process_info)
    except Exception as error:  # noqa: BLE001
        return None, expected, None, (f"CalculateRightHandSide raised {type(error).__name__}: {error}")
    try:
        actual = len(vector)
    except Exception as error:  # noqa: BLE001
        return None, expected, None, (f"the right-hand side has no length "
                                      f"({type(error).__name__}: {error})")
    if actual != expected:
        return None, expected, actual, (f"right-hand side length {actual} != 3 * {points} geometry "
                                        f"nodes = {expected}")
    values = []
    for index in range(actual):
        value = _finite_or_none(vector[index])
        if value is None:
            return None, expected, actual, f"right-hand side entry {index} is not finite"
        values.append(value)
    return values, expected, actual, None


def _rhs_resultant(values, points):
    total = [0.0, 0.0, 0.0]
    for index in range(points):
        for component in range(3):
            total[component] += values[3 * index + component]
    return _finite_vector(total)


def _condition_force_rows(conditions, process_info):
    rows = []
    total = [0.0, 0.0, 0.0]
    counts = {"measured": 0, "unavailable": 0}
    for condition in conditions:
        row = {"condition_id": _entity_id(condition), "status": "unavailable", "points": None,
               "rhs_size": None, "expected_size": None, "rhs": None, "reason": None}
        values, expected, actual, reason = _condition_rhs(condition, process_info)
        row["expected_size"] = expected
        row["rhs_size"] = actual
        if values is None:
            row["reason"] = reason
            counts["unavailable"] += 1
        else:
            points = expected // 3
            row.update({"status": "measured", "points": points,
                        "rhs": _rhs_resultant(values, points)})
            if row["rhs"] is None:
                row.update({"status": "unavailable", "rhs": None,
                            "reason": "the condition resultant is not finite"})
                counts["unavailable"] += 1
            else:
                counts["measured"] += 1
                for component in range(3):
                    total[component] += row["rhs"][component]
        rows.append(row)
    complete = counts["unavailable"] == 0 and counts["measured"] > 0
    return rows, (_finite_vector(total) if complete else None), counts


def _strong_support_reactions(part, parameters):
    enabled = _compute_reactions_setting(parameters)
    names = _referenced_sub_parts(parameters, "constraints_process_list", SUPPORT_PREFIX)
    result = {
        "definition": STRONG_REACTION_DEFINITION,
        "compute_reactions": enabled,
        "sub_model_parts": {},
        "reactions": [],
        "total": None,
        "duplicates_removed": 0,
        "non_fixed_components_excluded": 0,
        "status": "none",
        "reason": None,
    }
    if not enabled["enabled"]:
        result["status"] = "unavailable"
        result["reason"] = enabled["reason"]
        return result
    if not names:
        result["reason"] = "no constraints_process_list entry targets a Support_* sub-model part"
        return result
    seen = {}
    per_part = {}
    pairs = []
    reason = None
    duplicates = 0
    non_fixed = 0
    for name in names:
        entry = {"present": True, "nodes": 0, "conditions": 0, "fixed_dofs": 0, "unique_dofs": 0,
                 "duplicates": 0, "total": [0.0, 0.0, 0.0], "reason": None}
        if not part.HasSubModelPart(name):
            entry.update({"present": False,
                          "reason": "the sub-model part is absent from the model"})
            reason = reason or f"Support_* sub-model part {name} is absent from the model"
            per_part[name] = entry
            continue
        sub_part = part.GetSubModelPart(name)
        entry["nodes"] = int(sub_part.NumberOfNodes())
        entry["conditions"] = int(sub_part.NumberOfConditions())
        for node in sub_part.Nodes:
            for index, axis in enumerate(FORCE_AXES):
                try:
                    fixed = bool(node.IsFixed(KM.KratosGlobals.GetVariable("DISPLACEMENT_" + axis)))
                except Exception as error:  # noqa: BLE001
                    reason = reason or (f"the fixity of node {getattr(node, 'Id', '?')} along {axis} "
                                        f"is unreadable ({type(error).__name__}: {error})")
                    continue
                if not fixed:
                    non_fixed += 1
                    continue
                entry["fixed_dofs"] += 1
                try:
                    value = _finite_or_none(node.GetSolutionStepValue(
                        KM.KratosGlobals.GetVariable("REACTION_" + axis)))
                except Exception as error:  # noqa: BLE001
                    reason = reason or (f"REACTION_{axis} on node {getattr(node, 'Id', '?')} is "
                                        f"unreadable ({type(error).__name__}: {error})")
                    continue
                if value is None:
                    reason = reason or (f"REACTION_{axis} on node {getattr(node, 'Id', '?')} is not "
                                        "finite")
                    continue
                node_id = getattr(node, "Id", None)
                try:
                    key = (int(node_id), axis)
                except Exception:  # noqa: BLE001
                    reason = reason or f"node id {node_id!r} is not usable for (node, DOF) deduplication"
                    continue
                if key in seen:
                    duplicates += 1
                    entry["duplicates"] += 1
                    continue
                seen[key] = value
                entry["unique_dofs"] += 1
                entry["total"][index] += value
                pairs.append({"node_id": key[0], "component": axis, "reaction": value,
                              "sub_model_part": name})
        entry["total"] = _finite_vector(entry["total"])
        per_part[name] = entry
    totals = [0.0, 0.0, 0.0]
    for entry in per_part.values():
        if entry["total"] is None:
            reason = reason or f"a Support_* subtotal is not finite ({entry['reason']})"
            continue
        for component in range(3):
            totals[component] += entry["total"][component]
    finite_totals = _finite_vector(totals)
    result["sub_model_parts"] = per_part
    result["reactions"] = pairs
    result["duplicates_removed"] = duplicates
    result["non_fixed_components_excluded"] = non_fixed
    if reason is not None or finite_totals is None:
        result["status"] = "unavailable"
        result["reason"] = reason or "the deduplicated support reaction total is not finite"
        result["total"] = None
    elif not pairs:
        result["status"] = "unavailable"
        result["reason"] = ("no fixed translational DOF was found on the constraints_process_list "
                            "Support_* nodes; a condition-based (penalty) support has no nodal REACTION")
        result["total"] = None
    else:
        result["status"] = "measured"
        result["total"] = finite_totals
    return result


def _applied_loads(part, parameters):
    names = _referenced_sub_parts(parameters, "loads_process_list", LOAD_PREFIX)
    result = {
        "source": ("the resultant of condition.CalculateRightHandSide on the real Load_* conditions "
                   "(never a pressure x area product)"),
        "sub_model_parts": {},
        "total": None,
        "conditions_measured": 0,
        "conditions_unavailable": 0,
        "status": "none",
        "reason": None,
    }
    if not names:
        result["reason"] = "no loads_process_list entry targets a Load_* sub-model part"
        return result
    reason = None
    present = 0
    for name in names:
        entry = {"present": True, "conditions": 0, "nodes": 0, "rows": [], "total": None,
                 "conditions_measured": 0, "conditions_unavailable": 0, "reason": None}
        if not part.HasSubModelPart(name):
            entry.update({"present": False, "reason": "the sub-model part is absent from the model"})
            reason = reason or f"Load_* sub-model part {name} is absent from the model"
            result["sub_model_parts"][name] = entry
            continue
        present += 1
        sub_part = part.GetSubModelPart(name)
        entry["nodes"] = int(sub_part.NumberOfNodes())
        conditions = list(sub_part.Conditions)
        entry["conditions"] = len(conditions)
        if not conditions:
            entry["reason"] = "the load sub-model part carries no conditions"
            reason = reason or f"Load_* sub-model part {name} carries no conditions"
            result["sub_model_parts"][name] = entry
            continue
        rows, total, counts = _condition_force_rows(conditions, part.ProcessInfo)
        entry.update({"rows": rows, "total": total,
                      "conditions_measured": counts["measured"],
                      "conditions_unavailable": counts["unavailable"]})
        result["conditions_measured"] += counts["measured"]
        result["conditions_unavailable"] += counts["unavailable"]
        if total is None:
            bad = next((row for row in rows if row["status"] == "unavailable"), None)
            entry["reason"] = (bad or {}).get("reason") or "a load condition resultant is unavailable"
            reason = reason or f"{name} condition {((bad or {}).get('condition_id'))}: " \
                               f"{entry['reason']}"
        result["sub_model_parts"][name] = entry
    if present == 0 or not result["sub_model_parts"]:
        result["status"] = "unavailable"
        result["reason"] = reason or "no Load_* sub-model part is present in the model"
        return result
    totals = [0.0, 0.0, 0.0]
    complete = True
    for entry in result["sub_model_parts"].values():
        if entry["total"] is None:
            complete = False
            continue
        for component in range(3):
            totals[component] += entry["total"][component]
    if complete:
        finite = _finite_vector(totals)
        if finite is None:
            result["status"] = "unavailable"
            result["reason"] = "the applied load resultant is not finite"
        else:
            result["status"] = "measured"
            result["total"] = finite
    else:
        result["status"] = "unavailable"
        result["reason"] = reason or "at least one Load_* condition resultant is unavailable"
    return result


def _penalty_condition_forces(part, parameters, prefix, kind):
    names = _referenced_sub_parts(parameters, "constraints_process_list", prefix)
    result = {
        "kind": kind,
        "source": ("condition.CalculateRightHandSide on the real constraints_process_list "
                   f"{prefix}* conditions"),
        "sub_model_parts": {},
        "raw_total": None,
        "raw_conditions_measured": 0,
        "raw_conditions_unavailable": 0,
        "reaction_status": "unavailable",
        "physical_reaction": None,
        "status": "none",
        "reason": PENALTY_REACTION_REASON,
    }
    reason = None
    totals = [0.0, 0.0, 0.0]
    carried = 0
    for name in names:
        if not part.HasSubModelPart(name):
            continue
        sub_part = part.GetSubModelPart(name)
        conditions = list(sub_part.Conditions)
        if not conditions:
            continue
        carried += 1
        rows, raw_total, counts = _condition_force_rows(conditions, part.ProcessInfo)
        result["raw_conditions_measured"] += counts["measured"]
        result["raw_conditions_unavailable"] += counts["unavailable"]
        entry = {"nodes": int(sub_part.NumberOfNodes()), "conditions": len(conditions),
                 "rows": rows, "raw_total": raw_total, "status": "measured" if raw_total else "unavailable",
                 "reason": None}
        if raw_total is None:
            bad = next((row for row in rows if row["status"] == "unavailable"), None)
            entry["status"] = "unavailable"
            entry["reason"] = (bad or {}).get("reason") or "the raw condition resultant is unavailable"
            reason = reason or f"{name} condition {((bad or {}).get('condition_id'))}: " \
                               f"{entry['reason']}"
        else:
            for component in range(3):
                totals[component] += raw_total[component]
        result["sub_model_parts"][name] = entry
    if carried == 0:
        result["status"] = "none"
        result["reason"] = f"no constraints_process_list {prefix}* sub-model part carries conditions"
        return result
    result["raw_total"] = _finite_vector(totals) if reason is None else None
    result["status"] = "measured" if result["raw_total"] is not None else "unavailable"
    result["reaction_status"] = "unavailable"
    result["physical_reaction"] = None
    result["reason"] = PENALTY_REACTION_REASON
    if reason is not None:
        result["raw_total"] = None
        result["status"] = "unavailable"
        result["raw_unavailable_reason"] = reason
    return result


def _equilibrium(strong, loads, penalty_supports, penalty_couplings, convergence):
    missing = []
    if strong["status"] not in ("measured", "none"):
        missing.append("strong support reactions unavailable: " + str(strong.get("reason")))
    if loads["status"] not in ("measured", "none"):
        missing.append("applied loads unavailable: " + str(loads.get("reason")))
    penalty_present = (penalty_supports["status"] != "none"
                       or penalty_couplings["status"] != "none")
    penalty_raw_available = (penalty_supports["status"] in ("measured", "none")
                             and penalty_couplings["status"] in ("measured", "none"))
    strong_total = strong.get("total") or [0.0, 0.0, 0.0]
    load_total = loads.get("total") or [0.0, 0.0, 0.0]
    signed_total = None
    if strong["status"] in ("measured", "none") and loads["status"] in ("measured", "none"):
        signed_total = _finite_vector([strong_total[component] + load_total[component]
                                       for component in range(3)])
    raw_with_penalty = None
    if signed_total is not None and penalty_raw_available:
        penalty = [0.0, 0.0, 0.0]
        for section in (penalty_supports, penalty_couplings):
            raw = section.get("raw_total")
            if raw:
                for component in range(3):
                    penalty[component] += raw[component]
        raw_with_penalty = _finite_vector([signed_total[component] + penalty[component]
                                           for component in range(3)])
    scale = max([abs(value) for value in load_total + strong_total] + [1.0e-30])
    relative = None
    if signed_total is not None:
        relative = _finite_or_none(max(abs(value) for value in signed_total) / scale)
    result = {
        "definition": ("strong support reaction total + applied load resultant (a signed nodal "
                       "vector); penalty raw right-hand sides are reported separately and never "
                       "counted as signed"),
        "signed_vector": signed_total,
        "signed_residual_relative": relative,
        "raw_vector_including_penalty": raw_with_penalty,
        "scale": _finite_or_none(scale),
        "coverage": {
            "strong_supports": strong["status"] in ("measured", "none"),
            "applied_loads": loads["status"] in ("measured", "none"),
            "penalty_raw_available": penalty_raw_available,
            "penalty_present": penalty_present,
            "signed_complete": (not missing) and not penalty_present,
            "missing": missing,
        },
        "convergence": convergence,
        "status": "unavailable",
        "reason": None,
    }
    if signed_total is None:
        result["status"] = "unavailable"
        result["reason"] = "; ".join(missing) or "the signed strong+loads residual is not computable"
    elif missing or (penalty_present and not penalty_raw_available):
        result["status"] = "unavailable"
        result["reason"] = "; ".join(missing) or ("a penalty condition right-hand side is unavailable, "
                                                  "so the contribution coverage is incomplete")
    elif penalty_present:
        result["status"] = "unverified"
        result["reason"] = ("penalty condition contributions are raw and unsigned (" + penalty_supports
                            .get("reason", PENALTY_REACTION_REASON) + "); the signed coverage is "
                            "incomplete, so equilibrium cannot be verified")
    elif not convergence["verified"]:
        result["status"] = "unverified"
        result["reason"] = convergence["reason"]
    elif relative is not None and relative <= EQUILIBRIUM_RELATIVE_TOLERANCE:
        result["status"] = "pass"
        result["reason"] = ("complete signed coverage and verified convergence; the residual is within "
                            f"the relative tolerance {EQUILIBRIUM_RELATIVE_TOLERANCE:g}")
    else:
        result["status"] = "fail"
        result["reason"] = ("complete signed coverage and verified convergence, but the residual "
                            f"exceeds the relative tolerance {EQUILIBRIUM_RELATIVE_TOLERANCE:g}")
    return result


def _unavailable_strong(reason):
    return {"definition": STRONG_REACTION_DEFINITION, "compute_reactions": None,
            "sub_model_parts": {}, "reactions": [], "total": None, "duplicates_removed": 0,
            "non_fixed_components_excluded": 0, "status": "unavailable", "reason": reason}


def _unavailable_loads(reason):
    return {"source": "not measured", "sub_model_parts": {}, "total": None,
            "conditions_measured": 0, "conditions_unavailable": 0, "status": "unavailable",
            "reason": reason}


def _unavailable_penalty(kind, reason):
    return {"kind": kind, "source": "not measured", "sub_model_parts": {}, "raw_total": None,
            "raw_conditions_measured": 0, "raw_conditions_unavailable": 0,
            "reaction_status": "unavailable", "physical_reaction": None, "status": "unavailable",
            "reason": reason}


def measure_step_forces(model, parameters, mode, solve_converged=None):
    """Read the mechanical fields of the finalized step from the Kratos in-memory model.

    Must be called from ``FinalizeSolutionStep`` *before* the base implementation: the
    ``assign_scalar_variable_process`` processes release the fixed DOFs in their own
    ``ExecuteFinalizeSolutionStep``, so afterwards the support fixity is gone. Any failure
    is recorded as ``unavailable`` and never aborts the solve or leaks a non-finite value.
    """
    measurement = {
        "mode": mode,
        "time": None,
        "step": None,
        "compute_reactions": None,
        "strong_supports": _unavailable_strong("not measured"),
        "penalty_supports": _unavailable_penalty("support", "not measured"),
        "penalty_couplings": _unavailable_penalty("coupling", "not measured"),
        "applied_loads": _unavailable_loads("not measured"),
        "equilibrium": None,
        "status": "unavailable",
        "reason": None,
    }
    try:
        part_name = _as_string(_member(_member(parameters, "solver_settings"), "model_part_name"))
        part = model[part_name]
        process_info = getattr(part, "ProcessInfo", None)
        measurement["time"] = _finite_or_none(_process_info_value(process_info, "TIME"))
        measurement["step"] = _finite_or_none(_process_info_value(process_info, "STEP"))
        enabled = _compute_reactions_setting(parameters)
        measurement["compute_reactions"] = enabled
        strong = _strong_support_reactions(part, parameters)
        loads = _applied_loads(part, parameters)
        penalty_supports = _penalty_condition_forces(part, parameters, SUPPORT_PREFIX, "support")
        penalty_couplings = _penalty_condition_forces(part, parameters, COUPLING_PREFIX, "coupling")
        convergence = _convergence_evidence(parameters, part, solve_converged)
        measurement["strong_supports"] = strong
        measurement["applied_loads"] = loads
        measurement["penalty_supports"] = penalty_supports
        measurement["penalty_couplings"] = penalty_couplings
        measurement["equilibrium"] = _equilibrium(strong, loads, penalty_supports, penalty_couplings,
                                                  convergence)
        strong_available = strong["status"] in ("measured", "none")
        loads_available = loads["status"] in ("measured", "none")
        if strong_available and loads_available:
            measurement["status"] = "measured"
        elif strong["status"] == "measured" or loads["status"] == "measured":
            measurement["status"] = "partial"
            measurement["reason"] = ("only part of the mechanical fields could be measured "
                                     "(strong support or applied load is unavailable)")
        else:
            measurement["status"] = "unavailable"
            measurement["reason"] = ("no strong support reaction and no applied load resultant could "
                                     "be measured")
    except Exception as error:  # noqa: BLE001 - reporting must never abort the solve
        measurement["status"] = "unavailable"
        measurement["reason"] = f"force measurement failed: {type(error).__name__}: {error}"
    return measurement


def _step_force_summary(index, measurement):
    equilibrium = measurement.get("equilibrium") or {}
    convergence = equilibrium.get("convergence") or {}
    return {
        "index": index,
        "time": measurement.get("time"),
        "step": measurement.get("step"),
        "status": measurement.get("status"),
        "strong_support_total": (measurement.get("strong_supports") or {}).get("total"),
        "applied_load_total": (measurement.get("applied_loads") or {}).get("total"),
        "penalty_support_raw_total": (measurement.get("penalty_supports") or {}).get("raw_total"),
        "penalty_coupling_raw_total": (measurement.get("penalty_couplings") or {}).get("raw_total"),
        "equilibrium_status": equilibrium.get("status"),
        "equilibrium_signed_residual_relative": equilibrium.get("signed_residual_relative"),
        "convergence_verified": convergence.get("verified"),
    }


def summarize_forces(step_forces):
    steps = list(step_forces or [])
    section = {
        "schema": FORCES_SCHEMA,
        "measured_at": FORCES_MEASURED_AT,
        "step_count": len(steps),
        "steps": [_step_force_summary(index, measurement) for index, measurement in enumerate(steps)],
        "last": steps[-1] if steps else None,
        "status": "unavailable",
        "reason": None,
        "note": ("strong support reactions are nodal REACTION_X/Y/Z on fixed translational DOFs, "
                 "deduplicated by node+DOF; applied loads and penalty condition right-hand sides are "
                 "condition.CalculateRightHandSide resultants; penalty contributions are raw and are "
                 "NOT signed physical reactions; equilibrium is never 'pass' without complete signed "
                 "coverage and verified convergence evidence"),
    }
    if not steps:
        section["reason"] = "no FinalizeSolutionStep force measurement was recorded"
    elif all(step.get("status") == "measured" for step in steps):
        section["status"] = "measured"
    elif all(step.get("status") != "measured" for step in steps):
        section["status"] = "unavailable"
        first = next((step.get("reason") for step in steps if step.get("reason")), None)
        section["reason"] = first or "every recorded step failed to measure a support or load resultant"
    else:
        section["status"] = "partial"
        section["reason"] = "at least one recorded step could not measure every mechanical field"
    return section


def collect_report(model, parameters, mode, source_manifest=None):
    part = model[parameters["solver_settings"]["model_part_name"].GetString()]
    displacements = [list(node.GetSolutionStepValue(KM.DISPLACEMENT)) for node in part.Nodes]
    elements = list(part.Elements)
    conditions = list(part.Conditions)

    actual_total = 0.0
    total_finite = True
    for element in elements:
        try:
            value = float(element.GetGeometry().DomainSize())
        except Exception:  # noqa: BLE001
            total_finite = False
            continue
        if not math.isfinite(value):
            total_finite = False
            continue
        actual_total += value

    preprocessing = (source_manifest or {}).get("preprocessing")
    source_state = preprocessing.get("source_area", {}).get("state") if preprocessing else "missing"
    patch_reports = []
    if preprocessing:
        for patch in preprocessing.get("patches", []) or []:
            patch_reports.append(_patch_report(part, patch, AREA_TOLERANCE_RELATIVE))

    # Active-patch coverage: the metadata must describe exactly the patch element
    # parts the model actually built. An incomplete metadata set can never pass.
    model_patch_ids = sorted({int(name[len("Patch_"):]) for name in part.GetSubModelPartNames()
                              if name.startswith("Patch_") and name[len("Patch_"):].isdigit()})
    metadata_ids = []
    for patch in (preprocessing or {}).get("patches", []) or []:
        try:
            metadata_ids.append(int(patch.get("face_id")))
        except (TypeError, ValueError):
            continue
    metadata_ids = sorted(set(metadata_ids))
    missing_in_model = sorted(set(metadata_ids) - set(model_patch_ids))
    missing_in_metadata = sorted(set(model_patch_ids) - set(metadata_ids))
    coverage_complete = bool(metadata_ids) and not missing_in_model and not missing_in_metadata

    # Per-patch authority. A known mismatch always fails, even when another patch is
    # unknown; only a fully measured, fully covering, fully matching set can pass.
    known_mismatch = [p for p in patch_reports if p["area_match"] is False]
    unknown_patches = [p for p in patch_reports if p["area_match"] is None
                       or p["source_area_state"] != "measured"]
    if source_state == "measured" and all(p["source_area_state"] == "measured"
                                          for p in patch_reports) and patch_reports:
        patch_state_summary = "measured"
    elif any(p["source_area_state"] == "measured" for p in patch_reports):
        patch_state_summary = "mixed"
    elif patch_reports:
        patch_state_summary = "unknown"
    else:
        patch_state_summary = source_state

    if known_mismatch or not total_finite:
        area_status = "fail"
    elif not patch_reports or unknown_patches or not coverage_complete:
        area_status = "unknown"
    else:
        area_status = "pass"
    if area_status == "unknown":
        if patch_state_summary == "mixed":
            reason = ("The source-area metadata is mixed (some patches measured, some not); "
                      "the unverified patches block a pass. Re-export with the current bundle.")
        elif source_state != "measured":
            reason = ("The case carries no measured source-area metadata "
                      f"(state={source_state!r}); re-export with the current bundle. "
                      "The analysis area is NOT verified.")
        elif not coverage_complete:
            reason = ("The source-area metadata does not cover exactly the model's patch element "
                      f"parts (missing_in_model={missing_in_model}, "
                      f"missing_in_metadata={missing_in_metadata}); the analysis area is NOT verified.")
        elif any(p.get("expected_area_invalid") for p in patch_reports):
            reason = "At least one patch declares an invalid source area."
        else:
            reason = "At least one active patch has no comparable expected area."
    elif area_status == "fail":
        invalid = [p for p in patch_reports if p.get("expected_area_invalid")]
        if invalid:
            reason = ("At least one active patch declares an invalid source area: "
                      + "; ".join(str(p["expected_area_invalid"]) for p in invalid) + ".")
        else:
            reason = "At least one active patch area disagrees with the independent OCCT source area."
    else:
        reason = (f"Compared every active patch against the independent OCCT source area "
                  f"(relative tolerance {AREA_TOLERANCE_RELATIVE:g}).")

    report = {
        # Preserved fields (the existing consumers read exactly these).
        "mode": mode,
        "nodes": part.NumberOfNodes(),
        "elements": part.NumberOfElements(),
        "conditions": part.NumberOfConditions(),
        "max_abs_displacement": _finite_or_none(
            max((abs(v) for row in displacements for v in row), default=0.0)),
        "time": _finite_or_none(part.ProcessInfo[KM.TIME]),
        # P0-4 additive statistics.
        "active_element_nodes": len(_nodes_of(elements)),
        "active_condition_nodes": len(_nodes_of(conditions)),
        "displacement_dofs": _dof_statistics(part),
        "element_integration_points": _integration_counts(elements),
        "condition_integration_points": _integration_counts(conditions),
        "sub_model_parts": {name: {"nodes": part.GetSubModelPart(name).NumberOfNodes(),
                                   "elements": part.GetSubModelPart(name).NumberOfElements(),
                                   "conditions": part.GetSubModelPart(name).NumberOfConditions()}
                            for name in sorted(part.GetSubModelPartNames())},
        "actual_physical_area": actual_total if total_finite else None,
        "actual_physical_area_finite": total_finite,
        "preprocessing_source_area_state": source_state,
        "patch_source_area_states": {str(p["face_id"]): p["source_area_state"]
                                     for p in patch_reports},
        "patch_coverage": {
            "model_patch_ids": model_patch_ids,
            "metadata_patch_ids": metadata_ids,
            "missing_in_model": missing_in_model,
            "missing_in_metadata": missing_in_metadata,
            "complete": coverage_complete,
        },
        "patches": patch_reports,
        "area_check": {
            "status": area_status,
            "tolerance_relative": AREA_TOLERANCE_RELATIVE,
            "source_area_state": source_state,
            "patch_state_summary": patch_state_summary,
            "coverage_complete": coverage_complete,
            "measured_at": "Initialize (reference configuration, before RunSolutionLoop)",
            "reason": reason,
            "actual_physical_area": actual_total if total_finite else None,
            "mismatches": [{"face_id": p["face_id"], "actual_area": p["actual_area"],
                            "expected_area": p["expected_area"],
                            "relative_error": p["relative_error"],
                            "expected_area_invalid": p.get("expected_area_invalid")}
                           for p in known_mismatch],
        },
        "checks": {"area": area_status},
        # P0-6 B2 additive: filled with the measured fields after the solve loop. The
        # Initialize/--check report can only state that no step has been finalized yet.
        "forces": {
            "schema": FORCES_SCHEMA,
            "measured_at": "not measured (Initialize report)",
            "step_count": 0,
            "steps": [],
            "last": None,
            "status": "unavailable",
            "reason": ("run_report.json written during Initialize: no FinalizeSolutionStep has run "
                       "yet, so no support reaction or load resultant was read from the model"),
        },
    }
    return report


def finalize_report(initial_report, model, parameters, step_forces=None):
    """Solve-mode report: keep the pre-solve area result, add post-solve displacement/forces.

    ``DomainSize()`` follows the current configuration, so recomputing it after the
    solve would mix a deformed measure into the acceptance result. The area check is
    therefore frozen from ``Initialize``; the displacement/time and the per-step force
    evidence gathered in ``FinalizeSolutionStep`` are added instead.
    """
    part = model[parameters["solver_settings"]["model_part_name"].GetString()]
    displacements = [list(node.GetSolutionStepValue(KM.DISPLACEMENT)) for node in part.Nodes]
    report = dict(initial_report)
    report["mode"] = "solve"
    report["max_abs_displacement"] = _finite_or_none(
        max((abs(v) for row in displacements for v in row), default=0.0))
    report["time"] = _finite_or_none(part.ProcessInfo[KM.TIME])
    report["displacement_measured_at"] = "solve end"
    area_check = dict(initial_report["area_check"])
    area_check["measured_at"] = "Initialize (reference configuration, before RunSolutionLoop)"
    report["area_check"] = area_check
    report["forces"] = summarize_forces(step_forces)
    return report


def enforce_report(report):
    if report.get("area_check", {}).get("status") != "fail":
        return
    details = "; ".join(
        f"patch {m['face_id']}: initialized area {m['actual_area']!r} vs independent OCCT source "
        f"{m['expected_area']!r} (relative error {m['relative_error']!r})"
        for m in report["area_check"]["mismatches"])
    raise AreaCheckMismatch(
        "the initialized integration area disagrees with the independent OCCT source area: " + details)


def write_report(report):
    Path("run_report.json").write_text(json.dumps(report, indent=2, allow_nan=False),
                                       encoding="utf-8")


class CheckedAnalysis(StructuralMechanicsAnalysis):
    """Run the ordinary public analysis, but verify the area after Initialize.

    ``AnalysisStage.Run`` calls ``self.Initialize()`` (this override) and only then
    ``RunSolutionLoop()``, so a mismatch raises before the solver loop and the model
    is never solved. ``--check`` calls the same single ``Initialize()``.

    Static + linear only: each pseudo-time is an independent reference load case.
    The native IgaApplication ``Shell3pElement`` is residual-based with the Jacobian at
    the *current* coordinates and subtracts the internal stresses, so a multi-step
    static linear run would otherwise carry the previous step's deformation/config into
    the next. ``InitializeSolutionStep`` therefore resets every node to its initial
    position and zeroes the buffered DISPLACEMENT before the base class applies the
    prescribed BCs, loads and Predict. The gate is exactly ``solver_type == "static"``
    AND ``analysis_type == "linear"``; dynamic and nonlinear history are untouched.
    """

    def __init__(self, model, parameters, mode):
        super().__init__(model, parameters)
        self._ibra_model = model
        self._ibra_parameters = parameters
        self._ibra_mode = mode
        self.ibra_report = None
        self.ibra_step_forces = []
        self._independent_static_linear = False
        self._ibra_last_solve_converged = None

    def _is_independent_static_linear(self):
        try:
            settings = self._ibra_parameters["solver_settings"]
            return (settings["solver_type"].GetString() == "static"
                    and settings["analysis_type"].GetString() == "linear")
        except Exception:  # noqa: BLE001 - never guess from an unreadable setting
            return False

    def _reset_reference_and_displacement(self):
        name = self._ibra_parameters["solver_settings"]["model_part_name"].GetString()
        part = self._ibra_model[name]
        zero = KM.Vector([0.0, 0.0, 0.0])
        buffer_size = part.GetBufferSize()
        for node in part.Nodes:
            initial = node.GetInitialPosition()
            node.X, node.Y, node.Z = initial.X, initial.Y, initial.Z
            for step in range(buffer_size):
                node.SetSolutionStepValue(KM.DISPLACEMENT, step, zero)

    def Initialize(self):
        super().Initialize()
        self._independent_static_linear = self._is_independent_static_linear()
        self.ibra_report = collect_report(self._ibra_model, self._ibra_parameters,
                                          self._ibra_mode, _read_export_report(Path.cwd()))
        write_report(self.ibra_report)
        enforce_report(self.ibra_report)

    def InitializeSolutionStep(self):
        if self._independent_static_linear:
            self._reset_reference_and_displacement()
        super().InitializeSolutionStep()

    def SolveSolutionStep(self):
        """Record whether the real solver reported convergence for this step."""
        converged = super().SolveSolutionStep()
        self._ibra_last_solve_converged = None if converged is None else bool(converged)
        return converged

    def FinalizeSolutionStep(self):
        """Measure the step forces BEFORE the base class releases the support fixity.

        ``assign_scalar_variable_process`` frees the fixed DOFs in its own
        ``ExecuteFinalizeSolutionStep`` (called by the base implementation), so a
        measurement taken afterwards would see no fixed support at all.
        """
        measurement = measure_step_forces(self._ibra_model, self._ibra_parameters,
                                          self._ibra_mode, self._ibra_last_solve_converged)
        self.ibra_step_forces.append(measurement)
        super().FinalizeSolutionStep()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    os.chdir(Path(__file__).resolve().parent)
    parameters = KM.Parameters(Path("ProjectParameters.json").read_text(encoding="utf-8"))
    model = KM.Model()
    mode = "initialize" if args.check else "solve"
    analysis = CheckedAnalysis(model, parameters, mode)
    try:
        if args.check:
            analysis.Initialize()
        else:
            analysis.Run()
    except AreaCheckMismatch as error:
        print(f"FAIL: {error}")
        return 2
    if args.check:
        report = analysis.ibra_report
    else:
        report = finalize_report(analysis.ibra_report, model, parameters, analysis.ibra_step_forces)
        write_report(report)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
