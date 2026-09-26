"""P0-6 B1 tests: isolated run directories and the GUI call/report wiring.

The helper tests use a throwaway export directory and a throwaway sidecar directory;
they never write into ``runtime/`` and never start FreeCAD or Kratos. The GUI checks
are static: they parse ``gui.py`` and assert the call/report structure without
importing FreeCAD.
"""
import ast
import importlib
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "freecad/Mod/KratosIBRA"
sys.path.insert(0, str(PACKAGE_ROOT))

run_dirs = importlib.import_module("kratos_iga.run_dirs")

TEN_INPUTS = (
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
RUN_OUTPUTS = ("run_report.json", "results.vtkhdf")


def make_export(folder, *, extra=()):
    """A throwaway export directory containing the ten inputs plus stale outputs."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for name in TEN_INPUTS:
        (folder / name).write_text(f"content of {name}", encoding="utf-8")
    # Stale computed outputs, to prove they are never copied into a new run.
    (folder / "run_report.json").write_text('{"mode": "solve", "stale": true}',
                                            encoding="utf-8")
    (folder / "results.vtkhdf").write_bytes(b"stale-vtkhdf")
    (folder / "cache").mkdir(exist_ok=True)
    (folder / "cache" / "index.json").write_text("{}", encoding="utf-8")
    for name in extra:
        (folder / name).write_text("extra", encoding="utf-8")
    return folder


def export_snapshot(folder, *, ignore_run_dirs=True):
    """Every exported path and its bytes; own run directories are not export content."""
    folder = Path(folder)
    snapshot = {}
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(folder)
        if ignore_run_dirs and relative.parts[0].startswith(run_dirs.RUN_PREFIX):
            continue
        snapshot[str(relative)] = path.read_bytes()
    return snapshot


class RunDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="ibra-b1-"))
        self.addCleanup(self._cleanup)
        self.export = make_export(self.temp / "ibra_case_deadbeef")

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_run_inputs_are_exactly_the_ten_exported_inputs(self):
        self.assertEqual(tuple(run_dirs.RUN_INPUTS), TEN_INPUTS)
        self.assertEqual(set(run_dirs.RUN_INPUTS) & set(RUN_OUTPUTS), set())

    def test_two_launches_get_different_directories(self):
        first = run_dirs.create_run_dir(self.export)
        second = run_dirs.create_run_dir(self.export)
        self.assertNotEqual(first["run_dir"], second["run_dir"])
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertTrue(first["run_dir"].is_dir())
        self.assertTrue(second["run_dir"].is_dir())
        # Both live beside the export, which stays the parent of every run.
        for created in (first, second):
            self.assertEqual(created["run_dir"].parent, self.export)
            self.assertTrue(created["run_id"].startswith(run_dirs.RUN_PREFIX))

    def test_only_the_ten_inputs_are_copied(self):
        created = run_dirs.create_run_dir(self.export)
        self.assertEqual(sorted(created["copied"]), sorted(TEN_INPUTS))
        present = {path.name for path in created["run_dir"].iterdir()}
        self.assertEqual(present, set(TEN_INPUTS))
        for name in TEN_INPUTS:
            self.assertEqual((created["run_dir"] / name).read_bytes(),
                             (self.export / name).read_bytes(), name)

    def test_stale_outputs_and_cache_are_not_copied(self):
        created = run_dirs.create_run_dir(self.export)
        for name in RUN_OUTPUTS:
            self.assertFalse((created["run_dir"] / name).exists(), name)
        self.assertFalse((created["run_dir"] / "cache").exists())
        # And the helper has no notion of reading from an earlier run directory.
        second = run_dirs.create_run_dir(self.export)
        self.assertFalse((second["run_dir"] / "results.vtkhdf").exists())
        self.assertFalse((second["run_dir"] / "run_report.json").exists())

    def test_original_export_is_not_modified(self):
        before = export_snapshot(self.export)
        stale_report = (self.export / "run_report.json").read_bytes()
        stale_results = (self.export / "results.vtkhdf").read_bytes()
        run_dirs.create_run_dir(self.export)
        run_dirs.create_run_dir(self.export)
        # The only new entries in the export directory are the run directories it owns,
        # and every exported file is byte-identical afterwards.
        self.assertEqual(export_snapshot(self.export), before)
        self.assertEqual((self.export / "run_report.json").read_bytes(), stale_report)
        self.assertEqual((self.export / "results.vtkhdf").read_bytes(), stale_results)
        new_entries = {path.name for path in self.export.iterdir()
                       if path.name.startswith(run_dirs.RUN_PREFIX)}
        self.assertEqual(len(new_entries), 2)

    def test_existing_destination_is_never_reused(self):
        target = self.export / "ibra_run_existing"
        target.mkdir()
        marker = target / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        created = run_dirs.create_run_dir(self.export)
        self.assertNotEqual(created["run_dir"], target)
        self.assertTrue(marker.is_file())
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_missing_input_is_refused_and_no_directory_is_left(self):
        for name in TEN_INPUTS:
            export = make_export(self.temp / ("case_missing_" + name.replace(".", "_")))
            (export / name).unlink()
            before = {p.name for p in export.iterdir()}
            with self.assertRaises(run_dirs.RunDirectoryError) as caught:
                run_dirs.create_run_dir(export)
            self.assertIn(name, str(caught.exception))
            # No new directory was added and nothing was removed.
            self.assertEqual({p.name for p in export.iterdir()}, before)

    def test_missing_export_directory_is_refused(self):
        absent = self.temp / "not_there"
        with self.assertRaises(run_dirs.RunDirectoryError):
            run_dirs.create_run_dir(absent)

    def test_copy_failure_cleans_its_own_partial_directory(self):
        before = export_snapshot(self.export)
        real_copy = run_dirs.shutil.copy2
        state = {"count": 0}

        def failing_copy(source, target, *args, **kwargs):
            state["count"] += 1
            if state["count"] == 3:  # fail after two inputs were already copied
                raise OSError("simulated copy failure")
            return real_copy(source, target, *args, **kwargs)

        run_dirs.shutil.copy2 = failing_copy
        try:
            with self.assertRaises(run_dirs.RunDirectoryError) as caught:
                run_dirs.create_run_dir(self.export)
        finally:
            run_dirs.shutil.copy2 = real_copy
        self.assertIn("simulated copy failure", str(caught.exception))
        # The incomplete run directory this call created is gone, and the failure is
        # reported as cleaned rather than silently left behind.
        self.assertIn("removed the incomplete run directory", str(caught.exception))
        self.assertFalse([path for path in self.export.iterdir()
                          if path.name.startswith(run_dirs.RUN_PREFIX)])
        self.assertEqual(export_snapshot(self.export), before)

    def test_cleanup_never_removes_anything_but_our_own_created_directory(self):
        foreign = self.temp / "someone_elses_directory"
        foreign.mkdir()
        marker = foreign / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        self.assertIn("not a run directory this call created",
                      run_dirs._discard_own_dir(self.export, foreign))
        self.assertTrue(marker.is_file())
        # A run-prefixed directory outside the export is not ours.
        outside = self.temp / f"{run_dirs.RUN_PREFIX}outside"
        outside.mkdir()
        self.assertIn("not a run directory this call created",
                      run_dirs._discard_own_dir(self.export, outside))
        self.assertTrue(outside.is_dir())
        # The export directory itself is never removed, even if a caller passes it.
        self.assertIn("not a run directory this call created",
                      run_dirs._discard_own_dir(self.export, self.export))
        self.assertTrue(self.export.is_dir())
        # Only a fresh, owned, prefixed child is removed, and only once.
        fresh = self.export / f"{run_dirs.RUN_PREFIX}fresh_created"
        fresh.mkdir()
        self.assertIn("removed the incomplete run directory",
                      run_dirs._discard_own_dir(self.export, fresh))
        self.assertFalse(fresh.exists())
        self.assertIn("not a run directory this call created",
                      run_dirs._discard_own_dir(self.export, fresh))

    def test_an_existing_run_prefixed_directory_with_files_is_never_removed(self):
        existing = self.export / f"{run_dirs.RUN_PREFIX}existing"
        existing.mkdir()
        marker = existing / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        # Ownership and removal are separate: a prefixed child is "owned" by shape, but
        # removal is only ever asked for a directory this call actually created. The
        # caller passes its own created path, so an existing run is left in place.
        self.assertTrue(run_dirs._owned_run_dir(self.export, existing))
        self.assertTrue(marker.is_file())
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        # A real launch never targets it: the new directory is a different name.
        created = run_dirs.create_run_dir(self.export)
        self.assertNotEqual(created["run_dir"], existing)
        self.assertTrue(marker.is_file())

    def test_no_leftover_helper_name_remains(self):
        self.assertFalse(hasattr(run_dirs, "_remove_own_empty_dir"))

    def test_missing_inputs_reports_every_absent_name_in_order(self):
        export = make_export(self.temp / "case_two_missing")
        (export / "physics.iga.json").unlink()
        (export / "Materials.json").unlink()
        absent = run_dirs.missing_inputs(export)
        self.assertEqual(absent, ["Materials.json", "physics.iga.json"])

    def test_name_collision_retries_without_touching_the_collision(self):
        stamp = run_dirs._utc_stamp()
        first = self.export / f"{run_dirs.RUN_PREFIX}{stamp}"
        first.mkdir()
        keep = first / "keep.txt"
        keep.write_text("keep", encoding="utf-8")
        # Force the generated stamp to collide with the directory just created.
        original = run_dirs._utc_stamp
        run_dirs._utc_stamp = lambda: stamp
        try:
            created = run_dirs.create_run_dir(self.export)
        finally:
            run_dirs._utc_stamp = original
        self.assertNotEqual(created["run_dir"], first)
        self.assertEqual(created["run_id"], f"{run_dirs.RUN_PREFIX}{stamp}_01")
        self.assertEqual(keep.read_text(encoding="utf-8"), "keep")
        self.assertEqual(sorted(created["copied"]), sorted(TEN_INPUTS))

    def test_helpers_point_at_the_run_directory_only(self):
        created = run_dirs.create_run_dir(self.export)
        self.assertEqual(run_dirs.report_path(created["run_dir"]),
                         created["run_dir"] / "run_report.json")
        self.assertEqual(run_dirs.results_path(created["run_dir"]),
                         created["run_dir"] / "results.vtkhdf")

    def test_run_id_and_paths_carry_no_hash(self):
        created = run_dirs.create_run_dir(self.export)
        text = f"{created['run_id']} {created['run_dir']}"
        for token in ("sha", "hash", "digest"):
            self.assertNotIn(token, text.lower())


class GuiWiringTests(unittest.TestCase):
    """Static checks on gui.py: the run call and report path, without FreeCAD."""

    @classmethod
    def setUpClass(cls):
        cls.source = (PACKAGE_ROOT / "kratos_iga/gui.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        panel = [node for node in cls.tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "Panel"][0]
        cls.methods = {node.name: node for node in panel.body
                       if isinstance(node, ast.FunctionDef)}

    def _segment(self, name):
        node = self.methods[name]
        lines = self.source.splitlines()
        return "\n".join(lines[node.lineno - 1:node.end_lineno])

    def test_run_case_creates_an_isolated_run_directory(self):
        segment = self._segment("run_case")
        self.assertIn("run_dirs.create_run_dir", segment)
        self.assertIn("self.last_run", segment)
        # The launched script comes from the new run directory, never the export.
        self.assertIn('created["run_dir"]', segment)
        self.assertNotIn('arguments = [str(self.last_export/"MainKratos.py")]', segment)
        self.assertIn('"MainKratos.py"', segment)

    def test_run_case_never_deletes_a_previous_output(self):
        segment = self._segment("run_case")
        self.assertNotIn("unlink", segment)
        self.assertNotIn("rmdir", segment)
        self.assertNotIn("shutil.rmtree", segment)

    def test_run_case_sets_the_run_before_completion_can_read_it(self):
        segment = self._segment("run_case")
        self.assertLess(segment.index("self.last_run = created"),
                        segment.index("self.start_process"))
        # A fresh attempt must not leave an earlier run attached.
        self.assertIn("self.last_run = None", segment)

    def test_report_reading_uses_last_run_only(self):
        segment = self._segment("refresh_run_report")
        self.assertIn("self.last_run", segment)
        self.assertNotIn("self.last_export", segment)
        self.assertIn("run_report.json", segment)
        self.assertIn("export_report.json", segment)

    def test_clear_reports_the_run_directory_label(self):
        segment = self._segment("clear_run_report")
        self.assertIn("run_report_path", segment)

    def test_export_and_project_change_drop_the_run_association(self):
        for name in ("export_case", "import_finished"):
            segment = self._segment(name)
            self.assertIn("self.last_run = None", segment, name)
        # The export completion assigns the export and clears the run in one place.
        export_segment = self._segment("export_case")
        self.assertIn("self.last_export = destination", export_segment)
        self.assertIn("self.last_run = None", export_segment)

    def test_every_last_export_reset_also_resets_last_run(self):
        resets = [node for node in ast.walk(self.tree)
                  if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Attribute)
                          and target.attr == "last_export"
                          and isinstance(target.value, ast.Name)
                          and target.value.id == "self"
                          for target in node.targets)]
        self.assertTrue(resets)
        run_resets = [node for node in ast.walk(self.tree)
                      if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Attribute)
                              and target.attr == "last_run"
                              for target in node.targets)]
        self.assertGreaterEqual(len(run_resets), len(resets))

    def test_the_panel_starts_without_a_run(self):
        segment = self._segment("__init__")
        self.assertIn("self.last_run = None", segment)
        self.assertIn("self.last_export = None", segment)

    def test_the_run_path_label_is_a_real_widget(self):
        segment = self._segment("build_output")
        self.assertIn("self.run_report_path", segment)
        self.assertIn("Run directory", segment)


if __name__ == "__main__":
    unittest.main(verbosity=2)
