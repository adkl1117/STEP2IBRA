"""Isolated run directories for exported IBRA cases (P0-6 B1).

An exported case directory holds the model inputs and is **read-only** for this
module: nothing here deletes, overwrites or modifies anything inside it. Every
``Initialize``/``Run`` therefore gets its own directory beside the export, created
fresh and populated by copying exactly the ten exported inputs, so a repeated solve
cannot mix a new time series or report into an earlier run's results.

The copy list is deliberately closed: computed outputs (``results.vtkhdf``,
``run_report.json``) and any cached artefact are never copied from the export or from
an earlier run, so a failed solve cannot leave a stale success in the new directory.
If a required input is missing, the destination already exists or a copy fails, the
call raises before any result can be produced. On a copy failure it removes the
incomplete directory **only** when that directory is a direct child of the export and
carries the run prefix created by this call (a partially copied directory of our own
is our trace); the raised message states whether that removal happened, so a leftover
is never reported as cleaned. Nothing else in the export or in any existing run
directory is ever deleted or overwritten.
"""
from datetime import datetime, timezone
from pathlib import Path
import shutil

# Exactly the exported calculation inputs. MainKratos.py, ibra_case_process.py and
# ibra_amplitude.py are copied because the runner resolves its inputs beside itself.
RUN_INPUTS = (
    "MainKratos.py",
    "Materials.json",
    "physics.iga.json",
    "project.ibra-project.json",
    "ProjectParameters.json",
    "refinements.iga.json",
    "geometry.cad.json",
    "export_report.json",
    "ibra_case_process.py",
    "ibra_amplitude.py",
)
# Never copied: computed outputs that must be produced by this run only.
RUN_OUTPUTS = ("run_report.json", "results.vtkhdf")
RUN_PREFIX = "ibra_run_"
MAX_NAME_ATTEMPTS = 100


class RunDirectoryError(Exception):
    """The isolated run directory could not be created; no result may be shown."""


def _utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def _candidate_names():
    """Unique, sortable run names (no content hash, just a time stamp and a counter)."""
    stamp = _utc_stamp()
    yield f"{RUN_PREFIX}{stamp}"
    for counter in range(1, MAX_NAME_ATTEMPTS):
        yield f"{RUN_PREFIX}{stamp}_{counter:02d}"


def missing_inputs(export_dir):
    """The required exported inputs that are absent from ``export_dir``, in order."""
    export_dir = Path(export_dir)
    return [name for name in RUN_INPUTS if not (export_dir / name).is_file()]


def create_run_dir(export_dir):
    """Create one fresh run directory beside ``export_dir`` and copy the inputs.

    Returns ``{"run_dir", "run_id", "export_dir", "copied"}``. Raises
    :class:`RunDirectoryError` when the export directory or any required input is
    missing, when the destination already exists, or when the copy itself fails; in
    those cases only the newly created empty directory is removed and the export is
    left untouched.
    """
    export_dir = Path(export_dir)
    if not export_dir.is_dir():
        raise RunDirectoryError(f"the exported case directory does not exist: {export_dir}")
    absent = missing_inputs(export_dir)
    if absent:
        raise RunDirectoryError(
            "the exported case is incomplete; missing " + ", ".join(absent)
            + f" in {export_dir}. Re-export before running.")

    destination = None
    for name in _candidate_names():
        candidate = export_dir / name
        if candidate.exists():
            continue
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        except OSError as error:
            raise RunDirectoryError(
                f"cannot create the run directory {candidate}: {error}") from error
        destination = candidate
        break
    if destination is None:
        raise RunDirectoryError(
            f"cannot allocate a unique run directory beside {export_dir} "
            f"after {MAX_NAME_ATTEMPTS} attempts")

    copied = []
    created_here = destination  # exists only because this call ran mkdir()
    try:
        for name in RUN_INPUTS:
            source = export_dir / name
            target = destination / name
            if target.exists():  # cannot happen in a fresh directory; fail closed anyway
                raise RunDirectoryError(
                    f"refusing to overwrite {target}; the run directory is not empty")
            shutil.copy2(source, target)
            copied.append(name)
        extra = [name for name in RUN_OUTPUTS if (destination / name).exists()]
        if extra:
            raise RunDirectoryError(
                "a fresh run directory must not contain computed outputs: "
                + ", ".join(extra))
    except RunDirectoryError as error:
        cleanup = _discard_own_dir(export_dir, created_here)
        raise RunDirectoryError(f"{error} ({cleanup})") from error
    except OSError as error:
        cleanup = _discard_own_dir(export_dir, created_here)
        raise RunDirectoryError(
            f"cannot populate the run directory {destination}: {error} ({cleanup})") from error
    return {"run_dir": destination, "run_id": destination.name,
            "export_dir": export_dir, "copied": copied}


def _owned_run_dir(export_dir, directory):
    """True only for a direct, non-symlink run-prefixed child of ``export_dir``."""
    if directory is None:
        return False
    directory = Path(directory)
    try:
        return (directory.parent == Path(export_dir)
                and directory.name.startswith(RUN_PREFIX)
                and directory.is_dir()
                and not directory.is_symlink())
    except OSError:  # pragma: no cover - an unreadable path is not ours to remove
        return False


def _discard_own_dir(export_dir, directory):
    """Remove one directory that **this call created**, and report truthfully.

    The caller passes only a path it created in this call; this function additionally
    requires it to be a direct, non-symlink run-prefixed child of ``export_dir``. A
    partially populated directory of our own is our trace and is removed with it. The
    returned sentence states whether the removal happened, so a leftover is never
    reported as cleaned.
    """
    if directory is None:
        return "no run directory was created"
    directory = Path(directory)
    if not _owned_run_dir(export_dir, directory):
        return f"left {directory} in place (it is not a run directory this call created)"
    try:
        shutil.rmtree(directory)
    except OSError as error:
        return f"could not remove the incomplete run directory {directory}: {error}"
    return f"removed the incomplete run directory {directory}"


def report_path(run_dir):
    """The run report of one run directory (the run's own, never the export's)."""
    return Path(run_dir) / "run_report.json"


def results_path(run_dir):
    """The VTKHDF result of one run directory (the run's own, never the export's)."""
    return Path(run_dir) / "results.vtkhdf"
