"""Coupling decision and geometry-evidence contracts (stdlib only).

This module is imported by ``project.py`` (validation, GUI-safe) and by the
``worker`` index builder. It must stay free of OCP, numpy and Kratos imports.

Ownership split for P0-2:

* ``project.ibra-project.json`` owns the **decision**: ``state``, ``origin``,
  ``penalty``, ``rotation`` plus the interface id.
* ``index.json`` owns the **geometry evidence**: both face/trim ids, the actual
  pcurve origins and directions, the sample count and the sampled paired gap.

The evidence below is a *sample* statement. Nothing here proves whole-edge
coincidence, tangency, or mechanical intent.
"""
from __future__ import annotations

import math

STATES = ("pending", "confirmed", "excluded")
ORIGINS = ("geometry", "user", "migrated_unreviewed",
           "legacy_enabled_unreviewed", "legacy_disabled")
# Origin values that mean "kept for compatibility, never reviewed by a person".
UNREVIEWED_ORIGINS = ("legacy_enabled_unreviewed", "migrated_unreviewed")
EVIDENCE_NOTE = "sampled on both source surfaces; not whole-edge or mechanical proof"


def new_candidate(edge_id, penalty=1e7, rotation=False):
    """A freshly detected interface: a candidate, never an active condition."""
    return {"edge_id": int(edge_id), "state": "pending", "origin": "geometry",
            "penalty": float(penalty), "rotation": bool(rotation)}


def normalize_coupling(record, default_enabled=None):
    """Return a canonical decision record, or ``None`` when it cannot be read.

    Canonical records have exactly one source of truth: ``state``. Only an explicit
    legacy *boolean* ``enabled`` is translated. A record carrying both ``state`` and
    ``enabled``, carrying neither, or whose ``enabled`` is not a boolean is returned
    unchanged so ``project.validate`` rejects it instead of migration hiding it.
    """
    if not isinstance(record, dict) or "edge_id" not in record:
        return None
    result = dict(record)
    has_state, has_enabled = "state" in result, "enabled" in result
    if has_state and has_enabled:
        return result  # contradictory; validation rejects, migration must not hide it
    if not has_state:
        if not has_enabled:
            return result  # incomplete; validation rejects the missing state
        enabled = result.get("enabled")
        if not isinstance(enabled, bool):
            return result  # e.g. "false" is not a legacy decision; validation rejects
        result.pop("enabled")
        result["state"] = "confirmed" if enabled else "excluded"
        result["origin"] = "legacy_enabled_unreviewed" if enabled else "legacy_disabled"
    result.setdefault("origin", "migrated_unreviewed")
    result.setdefault("rotation", False)
    return result


def is_confirmed(record):
    return record.get("state") == "confirmed"


def gap_is_acceptable(gap, tolerance):
    return (isinstance(gap, (int, float)) and not isinstance(gap, bool)
            and math.isfinite(gap) and math.isfinite(tolerance) and gap <= tolerance)


def missing_evidence_message(edge_id):
    return (f"Interface {edge_id} is confirmed but the geometry cache has no usable "
            "pair diagnostic for it. Reimport the source STEP to rebuild the cache, then "
            "review the interface before exporting.")


def summarize(edge_id, diagnostics):
    """One-line, GUI-ready summary. Never claims whole-edge or mechanical proof."""
    if not isinstance(diagnostics, dict):
        return "no cached diagnostic - reimport the source geometry"
    sides = diagnostics.get("sides") or []
    places = ", ".join(f"F{s.get('face_id')}/T{s.get('trim_index')}" for s in sides) or "?"
    if diagnostics.get("status") != "ok":
        return f"{places}: {diagnostics.get('status')} - {diagnostics.get('detail', 'rebuild cache')}"
    gap = diagnostics.get("max_paired_gap")
    tol = diagnostics.get("tolerance")
    nearest = diagnostics.get("max_nearest_segment_gap")
    direction = "/".join(str(s.get("direction", "?")) for s in sides)
    return (f"{places} | {diagnostics.get('sample_count')} samples | nearest-segment "
            f"{nearest:.3g} mm (tol {tol:.3g}) | parameter-pair gap {gap} | "
            f"pcurve dir {direction} | sampled approximation only")


def evidence_errors(edge_id, diagnostics, geometry_sha256, edge=None):
    """Errors that must block exporting a *confirmed* interface.

    The cached ``status`` string is not trusted alone: the tolerance must be a
    finite positive number, every sampled number must be finite and within it, and
    both sides must be present, non-degenerate and match the current edge faces.
    """
    if not isinstance(diagnostics, dict):
        return [missing_evidence_message(edge_id)]
    if diagnostics.get("geometry_sha256") != geometry_sha256:
        return [missing_evidence_message(edge_id) + " (cached evidence belongs to a different geometry)"]
    tolerance = diagnostics.get("tolerance")
    if not (isinstance(tolerance, (int, float)) and not isinstance(tolerance, bool)
            and math.isfinite(tolerance) and tolerance > 0.0):
        return [f"Interface {edge_id}: the cached pair diagnostic has no finite positive tolerance. "
                "Reimport the source geometry to rebuild the cache."]
    if diagnostics.get("status") != "ok":
        return [f"Interface {edge_id}: {diagnostics.get('detail', 'invalid geometry correspondence')}. "
                "Reimport the source geometry or exclude the interface."]
    errors = []
    sides = diagnostics.get("sides") or []
    if len(sides) != 2 or any(not isinstance(s, dict) or s.get("degenerate") for s in sides):
        errors.append(f"Interface {edge_id}: both sides of the interface need a usable trimming curve.")
    else:
        cached = {s.get("face_id") for s in sides}
        expected = set(edge.get("face_ids", [])) if isinstance(edge, dict) else None
        if expected is not None and cached != expected:
            errors.append(f"Interface {edge_id}: the cached pair does not match the current interface faces.")
        if not all(isinstance(s.get("trim_index"), int) for s in sides):
            errors.append(f"Interface {edge_id}: the cached pair is missing a face/trim id.")
    if diagnostics.get("param_correspondence") in (None, "unknown"):
        errors.append(f"Interface {edge_id}: the parameter correspondence between the two trims is unknown.")
    if not isinstance(diagnostics.get("sample_count"), int) or diagnostics["sample_count"] < 2:
        errors.append(f"Interface {edge_id}: the cached pair diagnostic has too few samples.")
    for key in ("max_paired_gap", "max_curve_error", "max_nearest_segment_gap"):
        value = diagnostics.get(key)
        if not (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and 0.0 <= value <= tolerance):
            errors.append(f"Interface {edge_id}: the cached pair diagnostic has an invalid {key} "
                          f"(must be finite and within tolerance {tolerance:g}).")
    mismatch = diagnostics.get("parameter_mismatch")
    if not (isinstance(mismatch, (int, float)) and not isinstance(mismatch, bool)
            and math.isfinite(mismatch)):
        errors.append(f"Interface {edge_id}: the cached pair diagnostic has no finite "
                      "parameter-correspondence number.")
    return errors
