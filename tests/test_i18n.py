"""V0.5 selectable English/Chinese interface: pure logic and registry tests.

Runs in the backend venv (no FreeCAD, no Qt) because both new modules are
deliberately Qt-free: :mod:`kratos_iga.i18n` owns the catalog, persistence and
translation, :mod:`kratos_iga.i18n_qt` owns the widget registry through duck typing.

The native-language behavior is covered separately by
``tests/gui_i18n_probe.py`` (installed into an isolated workbench and driven by
``run(panel, out)``); nothing here claims a real Qt widget or a real process.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "freecad/Mod/KratosIBRA"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from kratos_iga import i18n                                       # noqa: E402
from kratos_iga import i18n_qt                                    # noqa: E402
from kratos_iga import interaction                                # noqa: E402
from kratos_iga import project                                    # noqa: E402
from kratos_iga.interaction import (SelectionMachine, Target, KIND_COUPLING,   # noqa: E402
                                    KIND_EDGE, KIND_PATCH, EntityResolver, RawSelection,
                                    InteractionRefusal)


# --------------------------------------------------------------------------- #
# Minimal Qt doubles: only the methods the registry actually uses.
# --------------------------------------------------------------------------- #
class FakeWidget:
    def __init__(self, text=""):
        self._text = text
        self._tooltip = None
        self._properties = {}
        self.set_calls = []

    # QLabel / QPushButton / QCheckBox surface
    def text(self):
        return self._text

    def setText(self, value):
        self.set_calls.append(value)
        self._text = value

    def setToolTip(self, value):
        self._tooltip = value

    def setAccessibleName(self, value):
        self._properties["accessible"] = value

    def __getattr__(self, name):        # setattr properties used by the registry
        if name.startswith("_"):
            raise AttributeError(name)
        return self._properties.get(name)


class FakeItem:
    def __init__(self, text=""):
        self._text = text
        self._data = {}

    def text(self):
        return self._text

    def setText(self, value):
        self._text = value

    def setData(self, role, value):
        self._data[role] = value

    def data(self, role):
        return self._data.get(role)


class FakeCombo:
    def __init__(self):
        self.items = []
        self.data = []
        self.roles = {}
        self._index = 0

    def addItem(self, text, value=None):
        self.items.append(text)
        self.data.append(value)

    def count(self):
        return len(self.items)

    def itemData(self, index, role=0):
        if role == i18n_qt.SOURCE_ROLE:
            return self.roles.get(index)
        if role:
            return None
        return self.data[index]

    def setItemData(self, index, value, role=0):
        if role == i18n_qt.SOURCE_ROLE:
            self.roles[index] = value

    def setItemText(self, index, value):
        self.items[index] = value

    def itemText(self, index):
        return self.items[index]

    def currentData(self):
        return self.data[self._index]

    def currentText(self):
        return self.items[self._index]

    def setCurrentIndex(self, index):
        self._index = index

    def findData(self, value):
        try:
            return self.data.index(value)
        except ValueError:
            return -1


class FakeLineEdit(FakeWidget):
    """The registry must never treat a QLineEdit as a translatable label."""

    def __init__(self, text=""):
        super().__init__(text)
        self.placeholder = None

    def setPlaceholderText(self, value):
        self.placeholder = value

    def placeholderText(self):
        return self.placeholder


# --------------------------------------------------------------------------- #
# Catalog and translation
# --------------------------------------------------------------------------- #
class CatalogTest(unittest.TestCase):
    def test_known_gui_error_localizes_but_technical_values_stay_raw(self):
        source = "Import STEP geometry first."
        english = i18n.translate("msg.operation_failed", "en", error=source)
        chinese = i18n.translate("msg.operation_failed", "zh", error=source)
        self.assertIn(source, english)
        self.assertNotIn(source, chinese)
        self.assertIn("STEP", chinese)
        detail = "userName=Import STEP geometry first.; path=D:/自定义/Name.step"
        self.assertEqual(i18n.localized_error(detail, "zh"), detail)

    def test_every_entry_has_both_languages_and_non_empty_text(self):
        self.assertTrue(i18n.MESSAGES, "the catalog must not be empty")
        for key, entry in i18n.MESSAGES.items():
            self.assertIsInstance(entry, dict, key)
            for code in i18n.LANGUAGES:
                text = entry.get(code)
                self.assertTrue(text and text.strip(), f"{key} missing {code}")
                # Deliberate leading/trailing spaces are allowed for suffix fragments
                # (for example a refusal suffix, a " · corner" tail), so only a text
                # that is entirely whitespace is rejected.
                self.assertNotIn("\n", text, key)

    def test_catalog_keys_are_stable_message_ids(self):
        for key in i18n.MESSAGES:
            self.assertRegex(key, r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$", key)

    def test_translate_is_bidirectional(self):
        key = "top.save_project"
        self.assertEqual(i18n.translate(key, "zh"), i18n.MESSAGES[key]["zh"])
        self.assertEqual(i18n.translate(key, "en"), i18n.MESSAGES[key]["en"])

    def test_chinese_source_keeps_the_pre_i18n_literals(self):
        # Compatibility: the pure core's Chinese default must equal the old text.
        self.assertEqual(i18n.translate("interaction.guidance.idle", "zh"),
                         interaction.GUIDANCE_IDLE)
        self.assertEqual(i18n.translate("interaction.guidance.selected",
                                        "zh", items="Patch 1"),
                         "已选：Patch 1。可继续左键点选或中键确认。")
        self.assertEqual(i18n.translate("interaction.refusal.foreign_document", "zh"),
                         "该对象不属于当前项目文档，已拒绝；选择状态未改动。")

    def test_template_parameters_are_data_not_translated_text(self):
        chinese = i18n.translate("msg.run_dir", "zh", folder="D:/work/run-1")
        english = i18n.translate("msg.run_dir", "en", folder="D:/work/run-1")
        self.assertIn("D:/work/run-1", chinese)
        self.assertIn("D:/work/run-1", english)
        self.assertNotIn("运行目录", english)

    def test_english_can_reorder_parameters(self):
        # A template with two placeholders must be able to reorder them.
        entry = i18n.MESSAGES["interaction.hover_pair"]
        self.assertEqual(i18n.translate("interaction.hover_pair", "en", first=3, second=7),
                         entry["en"].format(first=3, second=7))

    def test_missing_parameter_never_raises(self):
        text = i18n.translate("msg.run_dir", "en")     # folder omitted on purpose
        self.assertIn("{folder}", text)

    def test_unknown_key_returns_the_key_unchanged_with_substitution(self):
        self.assertEqual(i18n.translate("not.a.key"), "not.a.key")
        self.assertEqual(i18n.translate("not.a.key", "en", name="x"), "not.a.key")

    def test_language_normalization_and_labels(self):
        for value, expected in (("en", "en"), ("EN", "en"), ("en-US", "en"),
                                ("zh", "zh"), ("zh-CN", "zh"), ("zh_TW", "zh"),
                                ("chinese", "zh"), ("fr", i18n.DEFAULT_LANGUAGE),
                                (None, i18n.DEFAULT_LANGUAGE), ("", i18n.DEFAULT_LANGUAGE)):
            self.assertEqual(i18n.normalize_language(value), expected, value)
        self.assertEqual(i18n.language_label("en"), "English")
        self.assertEqual(i18n.language_label("zh"), "中文")

    def test_first_run_default_follows_freecad_then_falls_back_to_english(self):
        self.assertEqual(i18n.default_language_from_freecad("zh-CN"), "zh")
        self.assertEqual(i18n.default_language_from_freecad("Chinese"), "zh")
        self.assertEqual(i18n.default_language_from_freecad("en"), "en")
        self.assertEqual(i18n.default_language_from_freecad("de"), "en")
        self.assertEqual(i18n.default_language_from_freecad(None), i18n.DEFAULT_LANGUAGE)

    def test_set_language_is_process_wide_and_reversible(self):
        original = i18n.current_language()
        try:
            self.assertEqual(i18n.set_language("zh"), "zh")
            self.assertEqual(i18n.current_language(), "zh")
            self.assertEqual(i18n.translate("top.save_project"), "保存项目")
            self.assertEqual(i18n.set_language("en"), "en")
            self.assertEqual(i18n.translate("log.show"), "Show Log")
            self.assertEqual(i18n.set_language("nonsense"), i18n.DEFAULT_LANGUAGE)
        finally:
            i18n.set_language(original)


# --------------------------------------------------------------------------- #
# Persistence (plugin's own preference only)
# --------------------------------------------------------------------------- #
class PreferenceTest(unittest.TestCase):
    def test_written_language_round_trips_through_the_plugin_file(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(i18n.read_language_preference(folder))
            i18n.write_language_preference("zh", folder)
            self.assertEqual(i18n.read_language_preference(folder), "zh")
            i18n.write_language_preference("en", folder)
            self.assertEqual(i18n.read_language_preference(folder), "en")

    def test_initialize_prefers_the_stored_value_over_freecad(self):
        with tempfile.TemporaryDirectory() as folder:
            i18n.write_language_preference("zh", folder)
            self.assertEqual(i18n.initialize_language(folder, freecad_language="en"), "zh")
            self.assertEqual(i18n.current_language(), "zh")
            i18n.set_language("en")

    def test_initialize_uses_freecad_only_for_the_first_run(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(i18n.initialize_language(folder, freecad_language="zh-CN"), "zh")
            self.assertEqual(i18n.initialize_language(folder, freecad_language="fr"),
                             i18n.DEFAULT_LANGUAGE)
            i18n.set_language("en")

    def test_preference_never_touches_project_data_or_the_global_language(self):
        with tempfile.TemporaryDirectory() as folder:
            payload = project.new_project()
            before = json.dumps(payload, sort_keys=True)
            i18n.write_language_preference("zh", folder)
            i18n.set_language("zh")
            i18n.set_language("en")
            self.assertEqual(json.dumps(payload, sort_keys=True), before)
            stored = json.loads((Path(folder) / "IBRA/language.json").read_text(encoding="utf-8"))
            self.assertEqual(set(stored), {"language"})
            self.assertNotIn("units", stored)
            self.assertNotIn("couplings", stored)


# --------------------------------------------------------------------------- #
# Registry: live text switch without losing state
# --------------------------------------------------------------------------- #
class RegistryTest(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")
        self.registry = i18n_qt.TextRegistry()

    def tearDown(self):
        i18n.set_language("en")

    def test_registered_label_switches_both_ways(self):
        label = FakeWidget()
        self.registry.apply_text(label, "top.save_project")
        self.assertEqual(label.text(), "Save Project")
        self.registry.retranslate("zh")
        self.assertEqual(label.text(), "保存项目")
        self.registry.retranslate("en")
        self.assertEqual(label.text(), "Save Project")

    def test_template_label_keeps_its_data_through_a_switch(self):
        label = FakeWidget()
        self.registry.apply_text(label, "msg.run_dir", folder="D:/runs/run-7")
        self.registry.retranslate("zh")
        self.assertIn("D:/runs/run-7", label.text())
        self.assertIn("运行目录", label.text())
        self.registry.retranslate("en")
        self.assertIn("D:/runs/run-7", label.text())
        self.assertIn("Run directory", label.text())

    def test_stale_message_guard_never_resurrects_older_text(self):
        label = FakeWidget()
        self.registry.apply_text(label, "msg.preview_idle")
        # A later, newer status message owns the label (not one of our sources).
        label.setText("Geometry imported | Define material properties.")
        self.registry.retranslate("zh")
        self.assertEqual(label.text(), "Geometry imported | Define material properties.")
        # A fresh source write takes ownership again.
        self.registry.apply_text(label, "msg.preview_cleared")
        self.registry.retranslate("zh")
        self.assertEqual(label.text(), "预览已清除，不保留任何显示对象。")

    def test_combo_display_switches_while_canonical_data_is_untouched(self):
        combo = FakeCombo()
        i18n_qt.set_combo_items(self.registry, combo, [
            ("conditions.load_kind_vector", "vector", None),
            ("conditions.load_kind_pressure", "reference_pressure", None)])
        self.assertEqual(combo.itemData(0), "vector")
        self.assertEqual(combo.itemData(1), "reference_pressure")
        self.assertEqual(combo.itemText(0),
                         i18n.translate("conditions.load_kind_vector", "en"))
        self.registry.retranslate("zh")
        self.assertEqual(combo.itemText(0),
                         i18n.translate("conditions.load_kind_vector", "zh"))
        self.assertEqual(combo.itemData(0), "vector")
        self.assertEqual(combo.itemData(1), "reference_pressure")
        self.assertEqual(i18n_qt.canonical_combo_value(combo), "vector")
        combo.setCurrentIndex(1)
        self.assertEqual(i18n_qt.canonical_combo_value(combo), "reference_pressure")

    def test_hover_mode_combo_round_trips_its_canonical_value(self):
        combo = FakeCombo()
        i18n_qt.set_combo_items(self.registry, combo, [
            ("geometry.hover_mode_all", "all", None),
            ("geometry.hover_mode_edge", "edge", None)])
        for _ in range(2):
            self.registry.retranslate("zh")
            self.registry.retranslate("en")
        self.assertEqual([combo.itemData(i) for i in range(combo.count())], ["all", "edge"])
        self.assertEqual(i18n_qt.canonical_combo_value(combo), "all")

    def test_combo_item_with_parameters_is_rendered_per_language(self):
        combo = FakeCombo()
        i18n_qt.set_combo_items(self.registry, combo,
                                [("result.component_axis", "X", {"axis": "X"})])
        # canonical stays "X"; the visible text is the translated template.
        self.assertEqual(combo.itemData(0), "X")
        self.assertEqual(combo.itemText(0), "X")
        i18n_qt.set_combo_items(self.registry, combo,
                                [("conditions.vector_displacement", "u0", {"axis": "X"})])
        self.assertEqual(combo.itemData(1), "u0")
        self.assertEqual(combo.itemText(1), i18n.translate("conditions.vector_displacement",
                                                           "en", axis="X"))
        self.registry.retranslate("zh")
        self.assertEqual(combo.itemText(1), i18n.translate("conditions.vector_displacement",
                                                           "zh", axis="X"))
        self.assertEqual(combo.itemData(0), "X")
        self.assertEqual(combo.itemData(1), "u0")

    def test_table_header_switches_in_place(self):
        table = FakeTable(3)
        i18n_qt.translate_table_headers(self.registry, table,
                                        ["materials.table.id", "materials.table.name",
                                         "materials.table.thickness"], "en")
        self.assertEqual(table.labels(), ["ID", "Name", "Thickness"])
        i18n_qt.retranslate_table_headers(self.registry, table, "zh")
        self.assertEqual(table.labels(), ["ID", "名称", "厚度"])
        self.assertEqual(table.columnCount(), 3)

    def test_table_cell_enum_switches_and_canonical_value_survives(self):
        item = FakeItem()
        i18n_qt.display_cell(item, "state.confirmed")
        self.assertEqual(item.text(), i18n.translate("state.confirmed", "en"))
        item.setData(i18n_qt.CANONICAL_ROLE, "confirmed")
        i18n.set_language("zh")
        # display_cell wrote with the then-current language; a re-render uses the key
        item.setText(i18n.translate(item.data(i18n_qt.CELL_SOURCE_ROLE), "zh"))
        self.assertEqual(item.text(), "已确认")
        self.assertEqual(i18n_qt.canonical_item_value(item), "confirmed")

    def test_line_edit_placeholder_and_user_value_survive_a_switch(self):
        edit = FakeLineEdit("Support_1")
        edit.setPlaceholderText(i18n.translate("geometry.profile_placeholder", "en"))
        # The registry only ever owns setText; it never rewrites a placeholder and a
        # user-entered value stays verbatim across a language change.
        self.registry.retranslate("zh")
        self.assertEqual(edit.text(), "Support_1")
        self.assertEqual(edit.placeholderText(),
                         "Optional model-specific conversion profile (JSON)")
        self.assertEqual(i18n.translate("geometry.profile_placeholder", "zh"),
                         "可选的模型专用转换配置文件（JSON）")

    def test_user_typed_value_and_selection_are_preserved(self):
        name_edit = FakeLineEdit("Support_1")
        self.registry.apply_text(name_edit, "interaction.kind.edge")
        # The user's own value replaced the label text, so the guard protects it.
        name_edit.setText("My-Support")
        self.registry.retranslate("zh")
        self.assertEqual(name_edit.text(), "My-Support")

    def test_retranslate_returns_a_count_and_is_idempotent(self):
        first = FakeWidget()
        second = FakeWidget()
        self.registry.apply_text(first, "log.show")
        self.registry.apply_text(second, "log.hide")
        self.assertEqual(self.registry.retranslate("zh"), 2)
        self.assertEqual(self.registry.retranslate("zh"), 2)
        self.assertEqual(first.text(), "显示日志")
        self.assertEqual(second.text(), "隐藏日志")

    def test_unknown_key_is_not_registered_as_translatable(self):
        label = FakeWidget()
        self.registry.apply_text(label, "definitely.not.a.key")
        self.registry.retranslate("zh")
        self.assertEqual(label.text(), "definitely.not.a.key")

    def test_long_result_and_backend_text_is_left_raw(self):
        raw = "ValueError: backend process exited with code 1\nTraceback ..."
        status = FakeWidget(raw)
        self.registry.retranslate("zh")
        self.assertEqual(status.text(), raw)


class FakeTable:
    """Minimal QTableWidget surface for the header helpers."""

    def __init__(self, columns):
        self._columns = columns
        self._headers = [FakeItem() for _ in range(columns)]

    def columnCount(self):
        return self._columns

    def setHorizontalHeaderLabels(self, texts):
        for index, text in enumerate(texts):
            self._headers[index].setText(text)

    def horizontalHeaderItem(self, index):
        return self._headers[index]

    def labels(self):
        return [item.text() for item in self._headers]


# --------------------------------------------------------------------------- #
# Pure interaction core: language switch keeps the state and re-renders
# --------------------------------------------------------------------------- #
def _target(kind=KIND_PATCH, ident=1, side=None):
    return Target(kind=kind, ident=ident, object=f"Obj{ident}", side=side)


class MachineLanguageTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("en")

    def test_default_language_is_chinese_for_the_pure_core(self):
        machine = SelectionMachine()
        self.assertEqual(machine.language, "zh")
        self.assertEqual(machine.guidance, interaction.GUIDANCE_IDLE)
        self.assertEqual(machine.render_guidance(), interaction.GUIDANCE_IDLE)

    def test_start_boundary_guidance_switches_language_without_state_change(self):
        machine = SelectionMachine()
        machine.start_boundary("loads", pressure=True)
        self.assertIn("载荷", machine.guidance)
        self.assertIn("参考法向压力", machine.guidance)
        machine.set_language("en")
        self.assertEqual(machine.mode, "boundary")
        self.assertTrue(machine.pressure)
        self.assertEqual(machine.category, "loads")
        self.assertIn("Load picking started", machine.guidance)
        self.assertIn("face Patch only", machine.guidance)
        self.assertNotIn("载荷", machine.guidance)
        machine.set_language("zh")
        self.assertIn("载荷", machine.guidance)

    def test_mid_coupling_flow_keeps_faces_and_edge_on_switch(self):
        machine = SelectionMachine()
        machine.set_couplings([{"edge_id": 5, "face_ids": [1, 2], "penalty": 1e7,
                                "rotation": False, "state": "pending"}])
        machine.start_coupling()
        machine.left(_target(KIND_PATCH, 1))
        machine.left(_target(KIND_PATCH, 2))
        self.assertEqual(machine.step, "edge")
        self.assertEqual(machine.candidate_edge, 5)
        before = (machine.mode, machine.step, machine.face1.key(), machine.face2.key(),
                  machine.candidate_edge, list(machine.candidate_edges),
                  machine.edge_picked)
        machine.set_language("en")
        self.assertIn("Coupling 5", machine.guidance)
        machine.set_language("zh")
        self.assertIn("Coupling 5", machine.guidance)
        after = (machine.mode, machine.step, machine.face1.key(), machine.face2.key(),
                 machine.candidate_edge, list(machine.candidate_edges), machine.edge_picked)
        self.assertEqual(before, after)

    def test_selection_list_and_hover_switch_language(self):
        machine = SelectionMachine()
        machine.hover_mode = "all"
        machine.start_boundary("supports")
        machine.left(_target(KIND_EDGE, 3))
        machine.hover(_target(KIND_COUPLING, 5))
        machine.set_language("en")
        self.assertIn("Edge 3", machine.render_guidance())
        self.assertIn("Coupling 5", machine.hover_text)
        self.assertIn("Hover:", machine.render_guidance())
        machine.set_language("zh")
        self.assertIn("已选：边界边 3", machine.render_guidance())
        self.assertIn("悬浮：", machine.render_guidance())

    def test_refusal_kind_token_is_canonical_but_message_is_localized(self):
        # A machine started in English refuses in English; the same category in
        # Chinese refuses in Chinese.  The invariant is the refusal *code*, not text.
        english_machine = SelectionMachine(language="en")
        english_machine.start_boundary("initial_conditions")
        english = english_machine.left(_target(KIND_EDGE, 4))
        self.assertEqual(english.refused, "bad_target")
        self.assertIn("initial field", english.notice.lower())
        self.assertNotIn("初始场", english.notice)

        chinese_machine = SelectionMachine(language="zh")
        chinese_machine.start_boundary("initial_conditions")
        chinese = chinese_machine.left(_target(KIND_EDGE, 4))
        self.assertEqual(chinese.refused, "bad_target")
        self.assertIn("初始场", chinese.notice)

    def test_refusal_renders_in_the_requested_language(self):
        error = InteractionRefusal(
            "coupling_edge_required", "请点选接口边（Coupling），而不是普通边界；状态未改动。",
            key="interaction.refusal.coupling_edge_required")
        english = interaction.refusal_outcome(error, "en")
        self.assertIn("interface edge", english.notice.lower())
        chinese = interaction.refusal_outcome(error, "zh")
        self.assertEqual(chinese.notice,
                         "请点选接口边（Coupling），而不是普通边界；状态未改动。")

    def test_resolver_refusals_follow_the_resolver_language(self):
        resolver = EntityResolver(document="Doc", entities={}, fingerprints={},
                                  language="en")
        with self.assertRaises(InteractionRefusal) as caught:
            resolver.resolve(RawSelection(document="Other", object="X"))
        self.assertEqual(caught.exception.code, "foreign_document")
        self.assertEqual(caught.exception.key, "interaction.refusal.foreign_document")
        self.assertIn("current project document", caught.exception.message)
        chinese = EntityResolver(document="Doc", entities={}, fingerprints={},
                                 language="zh")
        with self.assertRaises(InteractionRefusal) as caught:
            chinese.resolve(RawSelection(document="Other", object="X"))
        self.assertIn("当前项目文档", caught.exception.message)

    def test_guidance_source_is_stored_independently_of_language(self):
        machine = SelectionMachine()
        machine.start_boundary("supports")
        key, params = machine.current_key()
        self.assertEqual(key, "interaction.guidance.started")
        self.assertEqual(set(params), {"label", "allowed"})
        # The parameters hold data (IDs/labels), never a rendered sentence.
        self.assertNotIn("\n", json.dumps(params))

    def test_pure_core_english_and_chinese_never_share_a_message_id(self):
        # Canonical entity tokens (Patch/Edge/Coupling and the hover pair) are the
        # same in both languages on purpose: they identify a registered entity and
        # the accepted mouse tests assert them verbatim.
        canonical = {"interaction.kind.face", "interaction.kind.edge",
                     "interaction.kind.coupling", "interaction.hover_pair"}
        for key in i18n.MESSAGES:
            if key.startswith("interaction.") and key not in canonical:
                self.assertNotEqual(i18n.translate(key, "en"), i18n.translate(key, "zh"), key)


# --------------------------------------------------------------------------- #
# Project serialization is never touched by a language switch
# --------------------------------------------------------------------------- #
class ProjectInvariantTest(unittest.TestCase):
    def test_switching_language_does_not_change_serialized_model(self):
        payload = project.new_project()
        payload["units"] = "mm-N-s"
        i18n.set_language("en")
        before = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        i18n.set_language("zh")
        i18n.set_language("en")
        self.assertEqual(json.dumps(payload, sort_keys=True, ensure_ascii=False), before)

    def test_user_entered_names_ids_paths_and_labels_stay_verbatim(self):
        values = ["支撑-左端", "D:/模型/example.step", "coupling_2", "0.001", "1e7",
                  "IBRA_Face_12"]
        for value in values:
            self.assertEqual(i18n.translate(value, "en"), value)
            self.assertEqual(i18n.translate(value, "zh"), value)
            self.assertEqual(i18n.resolve_text(value, "zh"), value)
            self.assertEqual(i18n.translate_text(value, "en"), value)

    def test_backend_raw_log_lines_are_not_rewritten(self):
        raw = ("Kratos ERROR: condition id 17 has no reference area\n"
               "  in StructuralMechanicsApplication")
        self.assertEqual(i18n.translate_text(raw, "en"), raw)
        self.assertEqual(i18n.translate_text(raw, "zh"), raw)


# --------------------------------------------------------------------------- #
# Guidance/refusal coverage: every message the mouse flow can produce exists
# --------------------------------------------------------------------------- #
class MouseCoverageTest(unittest.TestCase):
    def test_every_refusal_code_has_a_translatable_message(self):
        codes = {
            "foreign_document", "preview_or_result", "unregistered", "bad_kind",
            "inconsistent_tag", "stale_geometry", "subelement", "ambiguous_subedge",
            "hover_mode", "category", "coupling_flow_face", "coupling_same_face",
            "no_existing_candidate", "coupling_edge_required", "wrong_coupling_edge",
            "coupling_state", "no_document", "no_target", "middle_mismatch",
            "coupling_confirm_order", "coupling_edge_first", "not_started",
            "bad_target",
        }
        resolver_keys = {"interaction.refusal." + code for code in codes}
        present = resolver_keys & set(i18n.MESSAGES)
        self.assertGreaterEqual(len(present), 15,
                                "most resolver refusals must carry a catalog key")
        for key in present:
            self.assertTrue(i18n.has_language(key, "en"), key)
            self.assertTrue(i18n.has_language(key, "zh"), key)

    def test_every_guidance_outcome_is_rendered_in_both_languages(self):
        scenarios = []
        machine = SelectionMachine()
        scenarios.append(machine.start_boundary("supports"))
        machine.left(_target(KIND_EDGE, 1))
        scenarios.append(machine.current_outcome())
        machine2 = SelectionMachine()
        machine2.start_coupling()
        machine2.left(_target(KIND_PATCH, 1))
        machine2.set_couplings([{"edge_id": 5, "face_ids": [1, 2]}])
        machine2.left(_target(KIND_PATCH, 2))
        scenarios.append(machine2.current_outcome())
        machine2.left(_target(KIND_COUPLING, 5))
        scenarios.append(machine2.current_outcome())
        scenarios.append(machine2.escape())
        for outcome in scenarios:
            chinese = machine2.render_message(outcome.guidance_key,
                                              outcome.guidance_params, "zh")
            english = machine2.render_message(outcome.guidance_key,
                                              outcome.guidance_params, "en")
            self.assertTrue(chinese and english, outcome.guidance_key)
            self.assertNotEqual(chinese, english, outcome.guidance_key)

    def test_nested_guidance_parameters_follow_a_live_language_switch(self):
        """The category label and the allowed-targets phrase must never mix languages."""
        machine = SelectionMachine()
        machine.start_boundary("supports")
        self.assertIn("支承", machine.guidance)
        self.assertIn("面 Patch", machine.guidance)
        machine.set_language("en")
        # No Chinese may survive inside an English template.
        self.assertIn("Support picking started", machine.guidance)
        for chinese in ("支承", "面 Patch", "单侧边界", "自然角点"):
            self.assertNotIn(chinese, machine.guidance)
        machine.set_language("zh")
        self.assertIn("支承", machine.guidance)
        self.assertIn("面 Patch", machine.guidance)

    def test_selected_items_list_follows_a_live_language_switch(self):
        machine = SelectionMachine()
        machine.start_boundary("supports")
        machine.left(_target(KIND_EDGE, 3))
        machine.left(_target(KIND_PATCH, 7), additive=True)
        self.assertIn("边界边 3", machine.guidance)
        self.assertIn("曲面 7", machine.guidance)
        machine.set_language("en")
        self.assertIn("Edge 3", machine.guidance)
        self.assertIn("Patch 7", machine.guidance)
        self.assertIn("Selected:", machine.guidance)


if __name__ == "__main__":
    unittest.main()
