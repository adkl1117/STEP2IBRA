"""V0.5 mouse-interaction tests (FreeCAD-free).

The pure half of :mod:`kratos_iga.interaction` - refusals, target resolution, the
boundary/coupling selection machine and the reversible visibility controller - is
exercised here under any interpreter.  The native half (the real ``Gui.Selection``
observer and the installed Coin ``SoEvent`` callback) needs a live FreeCAD GUI and is
asserted by ``tests/gui_mouse_interaction_probe.py`` through
``scripts/check_mouse_interaction.py``; this module deliberately never starts one.

Nothing here claims physical mouse input: the Coin callback classification is tested
with synthesized event objects and is labelled as such.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "freecad/Mod/KratosIBRA"))

from kratos_iga import interaction as it  # noqa: E402

FACE_KEY = ("face", 1)
EDGE_KEY = ("edge", 3)
COUPLING_KEY = ("coupling", 5)

ENTITIES = {
    FACE_KEY: "IBRA_Face_1",
    ("face", 2): "IBRA_Face_2",
    EDGE_KEY: "IBRA_Edge_3",
    COUPLING_KEY: "IBRA_Edge_5",
}
FINGERPRINTS = {
    "IBRA_Face_1": (11, "Placement [P]"),
    "IBRA_Face_2": (22, "Placement [P]"),
    "IBRA_Edge_3": (33, "Placement [P]"),
    "IBRA_Edge_5": (55, "Placement [P]"),
}
FACE1 = it.Target("face", 1, "IBRA_Face_1")
FACE2 = it.Target("face", 2, "IBRA_Face_2")
EDGE3 = it.Target("edge", 3, "IBRA_Edge_3")
COUPLING5 = it.Target("coupling", 5, "IBRA_Edge_5")
COUPLINGS = [
    {"edge_id": 5, "face_ids": [1, 2], "state": "pending", "origin": "geometry",
     "penalty": 1e7, "rotation": False},
    {"edge_id": 6, "face_ids": [2, 3], "state": "confirmed", "origin": "user",
     "penalty": 3.5, "rotation": True},
]


def raw(object_name, *, document="Project", kind=None, ident=None, subs=(), fingerprint=None):
    return it.RawSelection(document=document, object=object_name, kind=kind, ident=ident,
                           sub_names=tuple(subs),
                           fingerprint=FINGERPRINTS.get(object_name) if fingerprint is None
                           else fingerprint)


def resolver(**overrides):
    options = dict(document="Project", entities=ENTITIES, fingerprints=FINGERPRINTS)
    options.update(overrides)
    return it.EntityResolver(**options)


def snapshot(machine):
    return (machine.mode, machine.step, machine.category, machine.pressure,
            [target.key() for target in machine.targets],
            None if machine.face1 is None else machine.face1.key(),
            None if machine.face2 is None else machine.face2.key(),
            machine.candidate_edge, machine.confirmed_edge)


class FakeType:
    def __init__(self, name):
        self._name = name

    def getName(self):
        return self._name


class FakeMouseEvent:
    DOWN = 1
    UP = 0
    BUTTON1 = 1
    BUTTON2 = 2
    BUTTON3 = 3

    def __init__(self, button, state=1, position=(10, 20), kind="SoMouseButtonEvent"):
        self._kind = kind
        self._button = button
        self._state = state
        self._position = position

    def getTypeId(self):
        return FakeType(self._kind)

    def getButton(self):
        return self._button

    def getState(self):
        return self._state

    def getPosition(self):
        return self._position


class FakeKeyEvent:
    DOWN = 1
    UP = 0
    ESCAPE = 200
    A = 100

    def __init__(self, key, state=1):
        self._key = key
        self._state = state

    def getTypeId(self):
        return FakeType("SoKeyboardEvent")

    def getKey(self):
        return self._key

    def getState(self):
        return self._state


class EntityResolverTest(unittest.TestCase):
    def test_registered_face_edge_coupling_resolve(self):
        for name, key in (("IBRA_Face_1", FACE_KEY), ("IBRA_Edge_3", EDGE_KEY),
                          ("IBRA_Edge_5", COUPLING_KEY)):
            target = resolver().resolve(raw(name))
            self.assertEqual(target.key(), (key[0], key[1], None), name)

    def test_foreign_document_is_refused_without_resolution(self):
        with self.assertRaises(it.InteractionRefusal) as caught:
            resolver().resolve(raw("IBRA_Face_1", document="Other"))
        self.assertEqual(caught.exception.code, "foreign_document")
        self.assertIn("当前项目文档", caught.exception.message)

    def test_preview_and_result_objects_are_refused(self):
        for extra in ({"preview_objects": ["IBRA_Face_1"]},
                      {"result_objects": ["IBRA_Face_1"]}):
            with self.assertRaises(it.InteractionRefusal) as caught:
                resolver(**extra).resolve(raw("IBRA_Face_1"))
            self.assertEqual(caught.exception.code, "preview_or_result")

    def test_unregistered_object_is_refused(self):
        with self.assertRaises(it.InteractionRefusal) as caught:
            resolver().resolve(raw("IBRA_Unknown"))
        self.assertEqual(caught.exception.code, "unregistered")

    def test_stale_geometry_and_missing_baseline_are_refused(self):
        with self.assertRaises(it.InteractionRefusal) as caught:
            resolver().resolve(raw("IBRA_Face_1", fingerprint=(999, "moved")))
        self.assertEqual(caught.exception.code, "stale_geometry")
        with self.assertRaises(it.InteractionRefusal) as caught:
            resolver(fingerprints={}).resolve(raw("IBRA_Face_1"))
        self.assertEqual(caught.exception.code, "stale_geometry")

    def test_inconsistent_ibra_tag_is_refused(self):
        with self.assertRaises(it.InteractionRefusal) as caught:
            resolver().resolve(raw("IBRA_Face_1", kind="edge", ident=3))
        self.assertEqual(caught.exception.code, "inconsistent_tag")

    def test_own_component_of_a_boundary_or_coupling_is_the_whole_entity(self):
        # ``getObjectInfo`` reports the object's own Component (Edge1/Face1); for a
        # registered single entity that is the entity itself, never a foreign sub-edge.
        for name, key in (("IBRA_Edge_3", EDGE_KEY), ("IBRA_Edge_5", COUPLING_KEY)):
            target = resolver().resolve(raw(name, subs=["Edge1"]))
            self.assertEqual(target.key(), (key[0], key[1], None), name)

    def test_multiple_sub_names_are_refused(self):
        with self.assertRaises(it.InteractionRefusal) as caught:
            resolver().resolve(raw("IBRA_Face_1", subs=["Edge1", "Edge2"]))
        self.assertEqual(caught.exception.code, "subelement")

    def test_patch_subelement_uses_the_injected_matcher(self):
        def matcher(selection, face_target):
            if selection.sub_names == ("Edge1",):
                return it.Target("edge", 3, selection.object, sub_names=selection.sub_names)
            raise it.InteractionRefusal("ambiguous_subedge", "no unique sub-edge")

        target = resolver(subedge_matcher=matcher).resolve(raw("IBRA_Face_1", subs=["Edge1"]))
        self.assertEqual(target.key(), ("edge", 3, None))
        # The capture contract must be able to re-select the same face + sub-edge.
        self.assertEqual(target.object, "IBRA_Face_1")
        self.assertEqual(target.sub_names, ("Edge1",))
        with self.assertRaises(it.InteractionRefusal) as caught:
            resolver(subedge_matcher=matcher).resolve(raw("IBRA_Face_1", subs=["Edge2"]))
        self.assertEqual(caught.exception.code, "ambiguous_subedge")

    def test_patch_subelement_without_a_matcher_refuses_instead_of_guessing(self):
        with self.assertRaises(it.InteractionRefusal) as caught:
            resolver().resolve(raw("IBRA_Face_1", subs=["Edge1"]))
        self.assertEqual(caught.exception.code, "ambiguous_subedge")


class BoundaryMachineTest(unittest.TestCase):
    def machine(self, category="supports", pressure=False):
        machine = it.SelectionMachine()
        machine.set_couplings(COUPLINGS)
        machine.start_boundary(category, pressure=pressure)
        return machine

    def test_start_requires_a_known_category_and_renders_guidance(self):
        machine = it.SelectionMachine()
        self.assertFalse(machine.start_boundary("nope").ok)
        outcome = machine.start_boundary("supports")
        self.assertTrue(outcome.ok)
        self.assertIn("左键", machine.render_guidance())
        self.assertIn("中键", machine.render_guidance())

    def test_left_then_middle_confirm_emits_capture_and_returns_idle(self):
        machine = self.machine()
        outcome = machine.left(FACE1)
        self.assertTrue(outcome.ok)
        self.assertEqual(machine.targets, [FACE1])
        outcome = machine.middle(FACE1)
        self.assertTrue(outcome.ok)
        self.assertEqual(machine.mode, "idle")
        self.assertEqual(machine.targets, [])
        capture = [event for event in outcome.events if event["kind"] == "capture"]
        self.assertEqual(len(capture), 1)
        self.assertEqual(capture[0]["category"], "supports")
        self.assertEqual(capture[0]["targets"], [{"target_kind": "face", "target_id": 1}])
        # The confirmed text is only shown after the coordinator verifies the capture;
        # the machine itself must not claim success.
        self.assertEqual(machine.guidance, it.GUIDANCE_COMMITTING)
        self.assertIn("添加／更新", capture[0]["guidance_after"])
        self.assertNotIn("Add / Update", machine.render_guidance())

    def test_middle_mismatch_is_refused_without_mutation(self):
        machine = self.machine()
        machine.left(FACE1)
        before = snapshot(machine)
        outcome = machine.middle(FACE2)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.refused, "middle_mismatch")
        self.assertEqual(snapshot(machine), before)

    def test_coupling_edge_is_rejected_for_ordinary_boundaries(self):
        for category in ("supports", "loads", "initial_conditions"):
            machine = self.machine(category)
            before = snapshot(machine)
            outcome = machine.left(COUPLING5)
            self.assertFalse(outcome.ok)
            self.assertEqual(outcome.refused, "bad_target")
            self.assertIn("接口边", outcome.notice)
            self.assertEqual(snapshot(machine), before)

    def test_initial_and_pressure_accept_faces_only(self):
        initial = self.machine("initial_conditions")
        self.assertFalse(initial.left(EDGE3).ok)
        self.assertTrue(initial.left(FACE1).ok)
        pressure = self.machine("loads", pressure=True)
        before = snapshot(pressure)
        self.assertFalse(pressure.left(EDGE3).ok)
        self.assertEqual(snapshot(pressure), before)
        self.assertTrue(pressure.left(FACE1).ok)
        vector = self.machine("loads", pressure=False)
        self.assertTrue(vector.left(EDGE3).ok)

    def test_additive_selection_toggles_and_deduplicates(self):
        machine = self.machine()
        machine.left(FACE1)
        machine.left(FACE2, additive=True)
        self.assertEqual([target.key() for target in machine.targets],
                         [FACE1.key(), FACE2.key()])
        machine.left(FACE2, additive=True)
        self.assertEqual([target.key() for target in machine.targets], [FACE1.key()])

    def test_escape_resets_every_step(self):
        machine = self.machine("loads")
        machine.left(FACE1)
        outcome = machine.escape()
        self.assertTrue(outcome.ok)
        self.assertFalse(machine.active())
        self.assertEqual(machine.targets, [])
        self.assertIn("已取消", outcome.guidance)

    def test_middle_without_a_target_is_refused(self):
        machine = self.machine()
        self.assertFalse(machine.middle(FACE1).ok)


class CouplingMachineTest(unittest.TestCase):
    def machine(self):
        machine = it.SelectionMachine()
        machine.set_couplings(COUPLINGS)
        machine.start_coupling()
        return machine

    def test_full_left_face1_face2_edge_then_middle(self):
        machine = self.machine()
        first = machine.left(FACE1)
        self.assertEqual(first.events[0]["kind"], "highlight_coupling_candidates")
        self.assertEqual(first.events[0]["edge_ids"], [5])
        self.assertEqual(machine.step, "face2")

        second = machine.left(FACE2)
        self.assertTrue(second.ok)
        self.assertEqual(second.events[0], {"kind": "highlight_coupling", "edge_id": 5})
        self.assertEqual(machine.step, "edge")
        self.assertIn("Coupling 5", machine.render_guidance())

        edge = machine.left(COUPLING5)
        self.assertTrue(edge.ok)
        self.assertIn("中键", machine.render_guidance())

        confirmed = machine.middle(COUPLING5)
        self.assertTrue(confirmed.ok)
        self.assertEqual(machine.confirmed_edge, 5)
        self.assertFalse(machine.active())
        expose = [event for event in confirmed.events
                  if event["kind"] == "expose_coupling_decision"][0]
        self.assertEqual(expose["penalty"], 1e7)
        self.assertFalse(expose["rotation"])
        self.assertIn("确认所选接口", machine.render_guidance())

    def test_no_existing_candidate_between_unrelated_faces_is_refused(self):
        machine = self.machine()
        machine.left(FACE1)
        before = snapshot(machine)
        outcome = machine.left(it.Target("face", 9, "IBRA_Face_9"))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.refused, "no_existing_candidate")
        self.assertEqual(snapshot(machine), before)

    def test_same_face_twice_is_refused(self):
        machine = self.machine()
        machine.left(FACE1)
        before = snapshot(machine)
        self.assertFalse(machine.left(FACE1).ok)
        self.assertEqual(snapshot(machine), before)

    def test_wrong_or_non_coupling_edge_is_refused_without_mutation(self):
        machine = self.machine()
        machine.left(FACE1)
        machine.left(FACE2)
        before = snapshot(machine)
        self.assertFalse(machine.left(EDGE3).ok)
        self.assertEqual(snapshot(machine), before)
        self.assertFalse(machine.left(it.Target("coupling", 6, "IBRA_Edge_6")).ok)
        self.assertEqual(snapshot(machine), before)
        self.assertTrue(machine.left(COUPLING5).ok)

    def test_middle_before_the_edge_step_is_refused(self):
        machine = self.machine()
        machine.left(FACE1)
        self.assertFalse(machine.middle(FACE1).ok)
        self.assertFalse(machine.middle(COUPLING5).ok)

    def test_coupling_flow_never_mutates_the_decision_records(self):
        before = [dict(record) for record in COUPLINGS]
        machine = self.machine()
        machine.left(FACE1)
        machine.left(FACE2)
        machine.left(COUPLING5)
        machine.middle(COUPLING5)
        self.assertEqual(COUPLINGS, before)


class HoverTest(unittest.TestCase):
    def test_modes_filter_the_audited_kind(self):
        machine = it.SelectionMachine()
        self.assertTrue(machine.set_hover_mode("coupling").ok)
        self.assertFalse(machine.set_hover_mode("bogus").ok)
        machine.hover(it.Target("edge", 3, "IBRA_Edge_3"))
        self.assertIn("不显示该类型", machine.hover_text)
        self.assertTrue(it.hover_mode_allows("all", "coupling"))
        self.assertFalse(it.hover_mode_allows("patch", "coupling"))

    def test_hover_text_is_auditable_and_carries_interface_sides(self):
        machine = it.SelectionMachine()
        machine.set_couplings(COUPLINGS)
        machine.hover(COUPLING5)
        self.assertIn("耦合边 5", machine.hover_text)
        self.assertIn("曲面 1 ↔ 曲面 2", machine.hover_text)
        self.assertIn("悬浮：", machine.render_guidance())

    def test_hover_refusal_does_not_clear_the_next_step(self):
        machine = it.SelectionMachine()
        machine.start_boundary("supports")
        machine.hover(None)
        self.assertIn("中键", machine.render_guidance())


class VisibilityTest(unittest.TestCase):
    def build(self):
        state = {"a": True, "b": False, "c": True}
        record = []

        def setter(name, value):
            if name not in state:
                return False
            state[name] = bool(value)
            record.append((name, bool(value)))
            return True

        return state, record, it.VisibilityController(lambda name: state.get(name), setter)

    def test_isolate_then_restore_restores_the_exact_previous_state(self):
        state, record, controller = self.build()
        saved = controller.isolate(["a"], ["b", "c"])
        self.assertEqual(saved, {"a": True, "b": False, "c": True})
        self.assertEqual(state, {"a": True, "b": False, "c": False})
        controller.restore()
        self.assertEqual(state, {"a": True, "b": False, "c": True})
        self.assertFalse(controller.active)
        self.assertEqual(controller.saved, {})

    def test_isolate_is_idempotent_and_restores_the_first_captured_state(self):
        state, record, controller = self.build()
        first = controller.isolate(["a"], ["b"])
        self.assertTrue(state["a"])
        self.assertFalse(state["b"])
        state["a"] = False         # an external change while isolated
        controller.isolate(["a"], ["b"])
        self.assertTrue(state["a"])   # the second isolate re-applies the isolation
        self.assertFalse(state["b"])
        controller.restore()
        self.assertEqual(state, {"a": True, "b": False, "c": True})
        self.assertEqual(first, {"a": True, "b": False})

    def test_missing_objects_are_skipped_and_reported(self):
        state, record, controller = self.build()
        controller.isolate(["a", "gone"], ["b"])
        self.assertIn("gone", controller.failed)
        self.assertNotIn("gone", controller.saved)
        controller.restore()
        self.assertEqual(state, {"a": True, "b": False, "c": True})

    def test_restore_after_the_object_vanished_does_not_invent_it(self):
        state, record, controller = self.build()
        controller.isolate(["a"], ["b"])
        del state["b"]
        controller.restore()
        self.assertIn("b", controller.failed)
        self.assertEqual(state, {"a": True, "c": True})


class CoinClassificationTest(unittest.TestCase):
    def test_left_and_middle_press_are_classified_and_state_is_ignored(self):
        left = it.classify_coin_event(FakeMouseEvent(FakeMouseEvent.BUTTON1))
        self.assertEqual(left["action"], "left")
        self.assertEqual(left["position"], (10, 20))
        self.assertFalse(left["consume"])
        # FreeCAD maps the Qt middle button to Coin BUTTON3.
        middle = it.classify_coin_event(FakeMouseEvent(FakeMouseEvent.BUTTON3))
        self.assertEqual(middle["action"], "middle")
        self.assertTrue(middle["consume"])
        released = it.classify_coin_event(FakeMouseEvent(FakeMouseEvent.BUTTON3,
                                                         state=FakeMouseEvent.UP))
        self.assertIsNone(released)

    def test_right_click_is_never_middle_and_never_consumed(self):
        # Qt right == Coin BUTTON2; it must never confirm and never be consumed.
        right = it.classify_coin_event(FakeMouseEvent(FakeMouseEvent.BUTTON2))
        self.assertEqual(right["action"], "right")
        self.assertFalse(right["consume"])
        self.assertNotEqual(right["action"], "middle")

    def test_escape_is_classified_and_other_keys_are_ignored(self):
        escape = it.classify_coin_event(FakeKeyEvent(FakeKeyEvent.ESCAPE))
        self.assertEqual(escape["action"], "escape")
        self.assertTrue(escape["consume"])
        other = it.classify_coin_event(FakeKeyEvent(FakeKeyEvent.A))
        self.assertIsNone(other["action"])

    def test_non_input_events_are_ignored(self):
        class Other:
            def getTypeId(self):
                return FakeType("SoLocation2Event")

        self.assertIsNone(it.classify_coin_event(Other())["action"])
        self.assertIsNone(it.classify_coin_event(None))

    def test_bridge_source_labels_synthesized_input(self):
        bridge = it.CoinEventBridge(handler=lambda info, event: None)
        info = bridge.dispatch(FakeMouseEvent(FakeMouseEvent.BUTTON1), source="synthesized")
        self.assertEqual(info["source"], "synthesized")
        self.assertTrue(info["synthetic"])
        info = bridge.dispatch(FakeMouseEvent(FakeMouseEvent.BUTTON1), source="coin")
        self.assertEqual(bridge.last_event_source, "coin")
        self.assertFalse(info["synthetic"])

    def test_event_from_argument_handles_pivy_node_and_dictionary(self):
        event = FakeMouseEvent(FakeMouseEvent.BUTTON1)

        class Node:
            def getEvent(self):
                return event

        self.assertIs(it.event_from_argument(Node()), event)
        self.assertIs(it.event_from_argument({"Event": event}), event)
        self.assertIs(it.event_from_argument(event), event)
        self.assertIsNone(it.event_from_argument({"unrelated": 1}))
        self.assertIsNone(it.event_from_argument(None))

    def test_pivy_and_dict_handlers_consume_only_owned_actions(self):
        seen = []

        def handler(info, event):
            seen.append(info["action"])
            return True if info["action"] == "middle" else None

        bridge = it.CoinEventBridge(handler=handler)
        node = type("Node", (), {"getEvent": lambda self: FakeMouseEvent(3),
                                 "handled": False,
                                 "setHandled": lambda self: setattr(self, "handled", True)})()
        self.assertTrue(bridge._pivy_dispatch(node))
        self.assertTrue(node.handled)
        node2 = type("Node", (), {"getEvent": lambda self: FakeMouseEvent(1),
                                  "handled": False,
                                  "setHandled": lambda self: setattr(self, "handled", True)})()
        self.assertIsNone(bridge._pivy_dispatch(node2))
        self.assertFalse(node2.handled)
        node3 = type("Node", (), {"getEvent": lambda self: FakeMouseEvent(2),
                                  "handled": False,
                                  "setHandled": lambda self: setattr(self, "handled", True)})()
        self.assertIsNone(bridge._pivy_dispatch(node3))   # right is never consumed
        self.assertFalse(node3.handled)
        self.assertEqual(seen, ["middle", "left", "right"])

        data = {"Event": FakeMouseEvent(3)}
        self.assertTrue(bridge._dict_dispatch(data))
        self.assertTrue(data["Handled"])


class GuidanceTest(unittest.TestCase):
    def test_idle_guidance_is_always_renderable(self):
        machine = it.SelectionMachine()
        text = machine.render_guidance()
        self.assertTrue(text.strip())
        self.assertIn("左键", text)

    def test_refusal_outcome_keeps_the_guidance_and_sets_the_notice(self):
        outcome = it.refusal_outcome(it.InteractionRefusal("x", "拒绝原因"))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.notice, "拒绝原因")


class ExplicitEdgePickTest(unittest.TestCase):
    def machine(self, couplings=None):
        machine = it.SelectionMachine()
        machine.set_couplings(couplings or COUPLINGS)
        machine.start_coupling()
        machine.left(FACE1)
        machine.left(FACE2)
        return machine

    def test_middle_after_face2_without_a_left_edge_pick_is_refused(self):
        machine = self.machine()
        self.assertEqual(machine.step, "edge")
        self.assertFalse(machine.edge_picked)
        outcome = machine.middle(COUPLING5)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.refused, "coupling_edge_first")
        self.assertEqual(machine.step, "edge")

    def test_left_edge_pick_arms_the_middle_confirm(self):
        machine = self.machine()
        picked = machine.left(COUPLING5)
        self.assertTrue(picked.ok)
        self.assertTrue(machine.edge_picked)
        confirmed = machine.middle(COUPLING5)
        self.assertTrue(confirmed.ok)
        self.assertEqual(machine.confirmed_edge, 5)

    def test_multiple_candidates_allow_picking_the_actual_edge(self):
        couplings = [{"edge_id": 5, "face_ids": [1, 2], "state": "pending",
                      "origin": "geometry", "penalty": 1e7, "rotation": False},
                     {"edge_id": 7, "face_ids": [1, 2], "state": "pending",
                      "origin": "geometry", "penalty": 2e7, "rotation": True}]
        machine = self.machine(couplings)
        self.assertEqual(machine.candidate_edges, [5, 7])
        self.assertIsNone(machine.candidate_edge)
        self.assertFalse(machine.edge_picked)
        self.assertFalse(machine.middle(COUPLING5).ok)
        self.assertTrue(machine.left(it.Target("coupling", 7, "IBRA_Edge_7")).ok)
        self.assertEqual(machine.candidate_edge, 7)
        self.assertTrue(machine.edge_picked)
        self.assertTrue(machine.middle(it.Target("coupling", 7, "IBRA_Edge_7")).ok)
        self.assertEqual(machine.confirmed_edge, 7)


class WholeFaceOnlyTest(unittest.TestCase):
    CORNER = it.Target("face", 1, "IBRA_Face_1", side="u0")

    def test_initial_and_pressure_reject_a_face_corner_or_side(self):
        for category, pressure in (("initial_conditions", False), ("loads", True)):
            machine = it.SelectionMachine()
            machine.start_boundary(category, pressure=pressure)
            outcome = machine.left(self.CORNER)
            self.assertFalse(outcome.ok, category)
            self.assertEqual(outcome.refused, "bad_target")
            self.assertFalse(machine.targets)
        # A strong support at a natural corner is still legitimate.
        supports = it.SelectionMachine()
        supports.start_boundary("supports")
        self.assertTrue(supports.left(self.CORNER).ok)
        self.assertEqual(supports.targets[0].side, "u0")


class FakeSelection:
    def __init__(self):
        self.calls = []

    def clearSelection(self, *args):
        self.calls.append("clear")

    def addSelection(self, *args):
        self.calls.append(("add",) + tuple(str(a) for a in args))

    def removeObserver(self, observer):
        self.calls.append("removeObserver")

    def addObserver(self, observer):
        self.calls.append("addObserver")

    def getSelectionEx(self):
        return ()

    def writes(self):
        return [call for call in self.calls if call == "clear" or isinstance(call, tuple)]


class FakeGui:
    def __init__(self):
        self.Selection = FakeSelection()


class FakeShape:
    def __init__(self, digest):
        self._digest = digest

    def hashCode(self):
        return self._digest


class FakeObject:
    def __init__(self, name):
        self.Name = name
        self.IbraKind = self.IbraBrepId = None
        for (kind, ident), mapped in ENTITIES.items():
            if mapped == name:
                self.IbraKind, self.IbraBrepId = kind, ident
        self.Shape = FakeShape(FINGERPRINTS.get(name, (0, ""))[0])
        self.Placement = "Placement [P]"


class FakeView:
    def __init__(self, visible=True):
        self.Visibility = visible


class FakeDocObject:
    def __init__(self, name, type_id="Part::Feature", group=False, visible=True):
        self.Name = name
        self.TypeId = type_id
        self.ViewObject = FakeView(visible)
        self.IbraKind = self.IbraBrepId = None
        for (kind, ident), mapped in ENTITIES.items():
            if mapped == name:
                self.IbraKind, self.IbraBrepId = kind, ident
        self.Shape = FakeShape(FINGERPRINTS.get(name, (0, ""))[0])
        self.Placement = "Placement [P]"
        if group:
            self.Group = []


class FakeDocument:
    Name = "Project"

    def __init__(self):
        self.Objects = [
            FakeDocObject("IBRA_Face_1"),
            FakeDocObject("IBRA_Edge_3"),
            FakeDocObject("IBRA_Edge_5"),
            FakeDocObject("OtherVisiblePart"),
            FakeDocObject("IBRA_PreviewGlyph"),
            FakeDocObject("IBRA_Geometry", "App::DocumentObjectGroup", group=True),
        ]

    def getObject(self, name):
        found = next((obj for obj in self.Objects if obj.Name == name), None)
        if found is not None:
            return found
        return FakeObject(name) if name in FINGERPRINTS else None


class FakeApp:
    def __init__(self):
        self.document = FakeDocument()
        self.observers = []

    def listDocuments(self):
        return {FakeDocument.Name: self.document}

    def getDocument(self, name):
        return self.document if name == FakeDocument.Name else None

    def addDocumentObserver(self, observer):
        self.observers.append(observer)

    def removeDocumentObserver(self, observer):
        if observer in self.observers:
            self.observers.remove(observer)


class FakePanel:
    def __init__(self):
        self.doc = FakeDocument()
        self.entities = dict(ENTITIES)
        self.display_fingerprints = dict(FINGERPRINTS)
        self.index = {"faces": [], "edges": []}
        self.project = {"couplings": [dict(record) for record in COUPLINGS]}
        self.preview_objects = {}
        self.result_layer = None
        self.guidance = ""
        self.captured = None

    def _interaction_set_guidance(self, text):
        self.guidance = text

    def _interaction_log(self, message):
        self.guidance = str(message)

    def _interaction_sync_toggles(self):
        pass

    def _pressure_mode(self):
        return False

    def capture(self, category):
        self.captured = category


class _ProbeCoordinator(it.InteractionCoordinator):
    def __init__(self, panel):
        super().__init__(panel)
        self.fake_gui = FakeGui()
        self.fake_app = FakeApp()

    def _gui(self):
        return self.fake_gui

    def _app(self):
        return self.fake_app


class ObserverFeedbackTest(unittest.TestCase):
    """The native Selection observer must never re-enter selection writes."""

    def build(self):
        panel = FakePanel()
        coordinator = _ProbeCoordinator(panel)
        coordinator.machine.set_couplings(COUPLINGS)
        coordinator.machine.start_boundary("supports")
        coordinator.attach()
        return panel, coordinator

    def test_observer_selection_never_writes_the_native_selection(self):
        panel, coordinator = self.build()
        coordinator.fake_gui.Selection.calls.clear()
        coordinator.on_add_selection("Project", "IBRA_Face_1", ())
        self.assertEqual([target.key() for target in coordinator.machine.targets],
                         [("face", 1, None)])
        self.assertEqual(coordinator.fake_gui.Selection.writes(), [],
                         coordinator.fake_gui.Selection.calls)

    def test_delayed_repeated_same_target_does_not_loop_or_duplicate(self):
        panel, coordinator = self.build()
        for _ in range(3):
            coordinator.on_add_selection("Project", "IBRA_Face_1", ())
        self.assertEqual([target.key() for target in coordinator.machine.targets],
                         [("face", 1, None)])
        self.assertEqual(coordinator.fake_gui.Selection.writes(), [])

    def test_repeated_same_target_is_idempotent_and_additive_still_works(self):
        machine = it.SelectionMachine()
        machine.start_boundary("supports")
        machine.left(FACE1)
        machine.left(FACE1)
        self.assertEqual([target.key() for target in machine.targets], [FACE1.key()])
        machine.left(FACE2, additive=True)
        machine.left(FACE1, additive=True)
        self.assertEqual([target.key() for target in machine.targets], [FACE2.key()])

    def test_table_to_view_write_removes_then_restores_the_observer(self):
        panel, coordinator = self.build()
        coordinator.fake_gui.Selection.calls.clear()
        coordinator._write_selection(objects=[{"object": "IBRA_Edge_5", "sub_names": []}])
        self.assertEqual(coordinator.fake_gui.Selection.calls,
                         ["removeObserver", "clear",
                          ("add", "Project", "IBRA_Edge_5", ""), "addObserver"])

    def test_coupling_highlight_from_the_observer_updates_the_table_only(self):
        panel, coordinator = self.build()
        coordinator.machine.start_coupling()
        coordinator.fake_gui.Selection.calls.clear()
        coordinator.on_add_selection("Project", "IBRA_Face_2", ())
        self.assertEqual(coordinator.machine.face1.key(), ("face", 2, None))
        self.assertEqual(coordinator.fake_gui.Selection.writes(), [])

    def test_capture_suspends_the_observer_around_its_selection_write(self):
        panel, coordinator = self.build()
        coordinator.machine.targets = [FACE1]
        coordinator.fake_gui.Selection.calls.clear()
        coordinator._capture("supports", [{"object": "IBRA_Face_1", "sub_names": []}],
                             [{"target_kind": "face", "target_id": 1}])
        self.assertIn("removeObserver", coordinator.fake_gui.Selection.calls)
        self.assertIn("addObserver", coordinator.fake_gui.Selection.calls)
        self.assertEqual(coordinator.fake_gui.Selection.calls[0], "removeObserver")
        self.assertEqual(coordinator.fake_gui.Selection.calls[-1], "addObserver")


class CouplingIsolationTest(unittest.TestCase):
    def build(self):
        panel = FakePanel()
        coordinator = _ProbeCoordinator(panel)
        return panel, coordinator

    def _visibility(self, panel):
        return {obj.Name: obj.ViewObject.Visibility for obj in panel.doc.Objects}

    def test_isolation_hides_every_drawable_except_couplings_and_containers(self):
        panel, coordinator = self.build()
        coordinator.toggle_coupling_visibility()
        visibility = self._visibility(panel)
        self.assertTrue(visibility["IBRA_Edge_5"])            # coupling stays visible
        self.assertFalse(visibility["IBRA_Face_1"])           # registered patch hidden
        self.assertFalse(visibility["IBRA_Edge_3"])           # registered edge hidden
        self.assertFalse(visibility["OtherVisiblePart"])      # unrelated visible Part hidden
        self.assertFalse(visibility["IBRA_PreviewGlyph"])     # preview glyph hidden
        self.assertTrue(visibility["IBRA_Geometry"])          # group container untouched

    def test_isolation_restores_exact_prior_visibility(self):
        panel, coordinator = self.build()
        panel.doc.Objects[3].ViewObject.Visibility = False     # unrelated already hidden
        before = self._visibility(panel)
        coordinator.toggle_coupling_visibility()
        coordinator.toggle_coupling_visibility()
        self.assertEqual(self._visibility(panel), before)

    def test_isolation_alone_installs_and_close_clears_the_document_observer(self):
        panel, coordinator = self.build()
        coordinator.toggle_coupling_visibility()
        self.assertTrue(coordinator._doc_observer_attached)
        self.assertFalse(coordinator._observers_attached)      # no navigation hooks
        coordinator.on_document_closed("Project")
        self.assertFalse(coordinator.visibility.active)
        self.assertEqual(coordinator.visibility.saved, {})
        self.assertFalse(coordinator._doc_observer_attached)

    def test_hover_modes_audit_edge_patch_and_coupling(self):
        machine = it.SelectionMachine()
        machine.set_couplings(COUPLINGS)
        for mode, target, expected in (
                ("edge", EDGE3, "边界边 3"),
                ("patch", FACE1, "曲面 1"),
                ("coupling", COUPLING5, "耦合边 5")):
            machine.set_hover_mode(mode)
            machine.hover(target)
            self.assertIn(expected, machine.hover_text, mode)


class MiddleFreshPickTest(unittest.TestCase):
    """Middle must always re-pick and re-resolve; a cached target is not authority."""

    def test_middle_uses_a_fresh_pick_and_refuses_a_mismatched_target(self):
        panel = FakePanel()
        coordinator = _ProbeCoordinator(panel)
        coordinator.machine.set_couplings(COUPLINGS)
        coordinator.machine.start_boundary("supports")
        coordinator.machine.left(FACE1)
        picks = []

        def fresh(position):
            picks.append(position)
            return FACE2

        coordinator._pick = fresh
        self.assertTrue(coordinator.on_middle_event((10, 20)))
        self.assertEqual(picks, [(10, 20)])          # the fresh pick was used
        self.assertEqual([target.key() for target in coordinator.machine.targets],
                         [FACE1.key()])               # not confirmed from the cache
        self.assertIn("一致", coordinator.machine.notice or "")

    def test_middle_confirms_when_the_fresh_pick_matches(self):
        panel = FakePanel()
        coordinator = _ProbeCoordinator(panel)
        coordinator.machine.start_boundary("supports")
        coordinator.machine.left(FACE1)
        coordinator._pick = lambda position: FACE1
        panel.capture = lambda category: setattr(
            panel, category + "_targets", [{"target_kind": "face", "target_id": 1}])
        self.assertTrue(coordinator.on_middle_event((10, 20)))
        self.assertEqual(panel.supports_targets, [{"target_kind": "face", "target_id": 1}])


class ThinCouplingLeftFallbackTest(unittest.TestCase):
    """A native left click on a thin coupling edge that the observer missed."""

    def build(self, picked):
        panel = FakePanel()
        coordinator = _ProbeCoordinator(panel)
        coordinator.machine.set_couplings(COUPLINGS)
        coordinator.machine.start_coupling()
        coordinator.machine.left(FACE1)
        coordinator.machine.left(FACE2)
        calls = []

        def fresh(position):
            calls.append(position)
            return picked

        coordinator._pick = fresh
        return panel, coordinator, calls

    def test_fresh_candidate_coupling_edge_is_accepted(self):
        panel, coordinator, calls = self.build(COUPLING5)
        self.assertTrue(coordinator._edge_left_fallback((892, 544)))
        self.assertEqual(calls, [(892, 544)])
        self.assertTrue(coordinator.machine.edge_picked)
        self.assertEqual(coordinator.machine.candidate_edge, 5)

    def test_face_duplicate_cannot_satisfy_the_edge_step(self):
        panel, coordinator, calls = self.build(FACE2)
        self.assertFalse(coordinator._edge_left_fallback((892, 544)))
        self.assertEqual(calls, [(892, 544)])
        self.assertFalse(coordinator.machine.edge_picked)
        self.assertEqual(coordinator.machine.step, "edge")

    def test_unrelated_coupling_edge_is_ignored(self):
        panel, coordinator, _calls = self.build(
            it.Target("coupling", 6, "IBRA_Edge_6"))
        self.assertFalse(coordinator._edge_left_fallback((892, 544)))
        self.assertFalse(coordinator.machine.edge_picked)

    def test_invalid_or_stale_fresh_pick_changes_nothing(self):
        panel, coordinator, calls = self.build(None)
        self.assertFalse(coordinator._edge_left_fallback((892, 544)))
        self.assertFalse(coordinator.machine.edge_picked)
        self.assertEqual(coordinator.machine.step, "edge")
        self.assertEqual(len(calls), 1)

    def test_no_double_process_when_the_observer_already_accepted(self):
        panel, coordinator, calls = self.build(COUPLING5)
        coordinator.machine.left(COUPLING5)          # observer accepted first
        self.assertTrue(coordinator.machine.edge_picked)
        self.assertFalse(coordinator._edge_left_fallback((892, 544)))
        self.assertEqual(calls, [])
        coordinator.on_coin_event({"action": "left", "position": (892, 544)}, None)
        self.assertEqual(calls, [])

    def test_right_or_non_coupling_step_never_schedules(self):
        panel, coordinator, calls = self.build(COUPLING5)
        coordinator.on_coin_event({"action": "right", "position": (892, 544)}, None)
        self.assertEqual(calls, [])
        # Outside the coupling edge step the left fallback is not scheduled either.
        coordinator.machine.start_boundary("supports")
        coordinator.on_coin_event({"action": "left", "position": (892, 544)}, None)
        self.assertEqual(calls, [])


class DrawableCoordinateTest(unittest.TestCase):
    """The pure device/drawable coordinate contract used by the middle filter."""

    def test_dpr_two_scaling_and_bottom_up_flip(self):
        self.assertEqual(it.map_local_to_coin((100, 50), (1014, 553), (2028, 1106)),
                         (200, 1005))

    def test_identity_when_drawable_and_view_agree(self):
        self.assertEqual(it.map_local_to_coin((10, 20), (100, 100), (100, 100)),
                         (10, 79))

    def test_invalid_sizes_return_none(self):
        self.assertIsNone(it.map_local_to_coin((1, 2), (0, 100), (100, 100)))
        self.assertIsNone(it.map_local_to_coin((1, 2), (100, 100), (0, 0)))

    def test_out_of_range_is_clamped(self):
        self.assertEqual(it.map_local_to_coin((10_000, -5), (100, 100), (200, 200)),
                         (199, 199))


class MiddleOwnershipTest(unittest.TestCase):
    """Ownership is strictly middle + active + view receiver + inside drawable."""

    def call(self, **overrides):
        options = dict(local_xy=(10, 10), drawable_size=(100, 100),
                       machine_active=True, is_middle=True, receiver_in_view=True)
        options.update(overrides)
        return it.middle_ownership(**options)

    def test_owned_only_when_every_condition_holds(self):
        self.assertEqual(self.call(), (True, "owned"))

    def test_point_outside_drawable_is_not_owned_and_not_clamped(self):
        self.assertEqual(self.call(local_xy=(100, 10)), (False, "point_outside_drawable"))
        self.assertEqual(self.call(local_xy=(10, 100)), (False, "point_outside_drawable"))
        self.assertEqual(self.call(local_xy=(-1, 10)), (False, "point_outside_drawable"))

    def test_missing_local_point_is_not_owned(self):
        self.assertEqual(self.call(local_xy=None), (False, "no_local_point"))

    def test_right_button_inactive_and_outside_receiver_are_not_owned(self):
        self.assertEqual(self.call(is_middle=False)[0], False)
        self.assertEqual(self.call(machine_active=False)[0], False)
        self.assertEqual(self.call(receiver_in_view=False), (False, "receiver_outside_view"))

    def test_point_inside_rejects_edges(self):
        self.assertTrue(it.point_inside((0, 0), (100, 100)))
        self.assertTrue(it.point_inside((99, 99), (100, 100)))
        self.assertFalse(it.point_inside((100, 0), (100, 100)))
        self.assertFalse(it.point_inside((0, 100), (100, 100)))
        self.assertFalse(it.point_inside(None, (100, 100)))


class MiddlePairingTest(unittest.TestCase):
    """A release only confirms while an owned, mapped press is paired to it."""

    def test_unowned_press_is_never_remembered(self):
        self.assertEqual(it.middle_action(phase="press", owned=False, has_press=False,
                                          mapped=None),
                         (it.MIDDLE_PASS, "press_not_owned"))

    def test_owned_press_without_a_mapped_position_passes_through(self):
        self.assertEqual(it.middle_action(phase="press", owned=True, has_press=False,
                                          mapped=None),
                         (it.MIDDLE_PASS, "press_unmapped"))

    def test_owned_and_mapped_press_is_remembered(self):
        self.assertEqual(it.middle_action(phase="press", owned=True, has_press=False,
                                          mapped=(10, 20)),
                         (it.MIDDLE_OWN_PRESS, "press_paired"))

    def test_release_without_an_owned_press_passes_through(self):
        self.assertEqual(it.middle_action(phase="release", owned=True, has_press=False,
                                          mapped=None),
                         (it.MIDDLE_PASS, "unpaired_release"))

    def test_unowned_release_passes_through_even_with_a_press(self):
        self.assertEqual(it.middle_action(phase="release", owned=False, has_press=True,
                                          mapped=(10, 20)),
                         (it.MIDDLE_PASS, "release_not_owned"))

    def test_paired_release_with_the_press_mapping_confirms(self):
        self.assertEqual(it.middle_action(phase="release", owned=True, has_press=True,
                                          mapped=(10, 20)),
                         (it.MIDDLE_CONFIRM, "release_paired"))

    def test_paired_release_without_a_mapping_never_confirms(self):
        self.assertEqual(it.middle_action(phase="release", owned=True, has_press=True,
                                          mapped=None),
                         (it.MIDDLE_PASS, "release_unmapped"))


class CoinInverseTest(unittest.TestCase):
    """The documented inverse contract used to prove an exact QtTest round trip."""

    def test_inverse_of_the_forward_mapping(self):
        self.assertEqual(it.map_coin_to_local((10, 79), (100, 100), (100, 100)), (10, 20))
        self.assertEqual(it.map_coin_to_local((200, 1005), (1014, 553), (2028, 1106)),
                         (100, 50))

    def test_round_trip_is_identity_when_the_device_pixel_has_an_image(self):
        size, view = (1014, 553), (2028, 1106)
        for coin in ((100, 1005), (200, 1005), (200, 1003), (0, 1105), (2026, 1)):
            local = it.map_coin_to_local(coin, size, view)
            self.assertIsNotNone(local, coin)
            self.assertEqual(it.map_local_to_coin(local, size, view), coin)

    def test_a_device_pixel_without_a_drawable_image_is_rejected(self):
        size, view = (1014, 553), (2028, 1106)
        for coin in ((-1, 1005), (2028, 1005), (100, 1106), (2027, 1005)):
            self.assertIsNone(it.map_coin_to_local(coin, size, view), coin)

    def test_out_of_range_or_invalid_sizes_return_none(self):
        self.assertIsNone(it.map_coin_to_local((-1, 10), (100, 100), (100, 100)))
        self.assertIsNone(it.map_coin_to_local((10, 100), (100, 100), (100, 100)))
        self.assertIsNone(it.map_coin_to_local((10, 10), (0, 100), (100, 100)))
        self.assertIsNone(it.map_coin_to_local((10, 10), (100, 100), (0, 0)))


class WidgetMappingTest(unittest.TestCase):
    """An ancestor receiver must map through global coordinates, never ``mapTo``.

    ``QWidget.mapTo`` requires the target to be in the receiver's parent hierarchy; for
    the real ``Gui::View3DInventorViewer`` receiver and its ``QOpenGLWidget`` drawable the
    target is a DESCENDANT, so ``mapTo`` warns and returns a wrong offset.  The pure
    global round trip is valid both ways and fails closed.
    """

    def test_ancestor_receiver_round_trips_through_global_coordinates(self):
        # Receiver global origin (100, 50), drawable global origin (110, 70): the receiver
        # local (30, 40) is global (130, 90) and therefore drawable-local (20, 20).
        self.assertEqual(
            it.map_between_widgets((30, 40),
                                   lambda x, y: (100 + x, 50 + y),
                                   lambda x, y: (x - 110, y - 70)),
            ((20, 20), (-10, -20)))

    def test_descendant_and_same_widget_hierarchies_are_consistent(self):
        # Drawable is an ancestor of the receiver: receiver global (200, 100).
        self.assertEqual(
            it.map_between_widgets((5, 5),
                                   lambda x, y: (200 + x, 100 + y),
                                   lambda x, y: (x - 200, y - 100)),
            ((5, 5), (0, 0)))
        self.assertEqual(
            it.map_between_widgets((7, 9), lambda x, y: (x, y), lambda x, y: (x, y)),
            ((7, 9), (0, 0)))

    def test_a_failing_step_is_failed_closed(self):
        def boom(x, y):
            raise RuntimeError("parent must be in parent hierarchy")

        self.assertIsNone(it.map_between_widgets((1, 2), boom, lambda x, y: (x, y)))
        self.assertIsNone(it.map_between_widgets((1, 2), lambda x, y: (x, y), boom))
        self.assertIsNone(it.map_between_widgets((1, 2), lambda x, y: (3, 4), None))


class FreshPickDiagnosticsTest(unittest.TestCase):
    """A fresh pick that does not hit an object records it instead of stale state."""

    def test_no_hit_pixel_records_no_info(self):
        coordinator = _ProbeCoordinator(FakePanel())
        coordinator.view = _FakePickView(None)
        self.assertIsNone(coordinator._pick((5, 6)))
        self.assertEqual(coordinator.last_pick, {"position": [5, 6], "reason": "no_info",
                                                 "info_type": "NoneType"})

    def test_dict_without_an_object_records_no_object(self):
        coordinator = _ProbeCoordinator(FakePanel())
        coordinator.view = _FakePickView({"Component": "Face1"})
        self.assertIsNone(coordinator._pick((7, 8)))
        self.assertEqual(coordinator.last_pick["reason"], "no_object")
        self.assertEqual(coordinator.last_pick["position"], [7, 8])

    def test_a_previous_hit_is_replaced_by_the_new_miss(self):
        coordinator = _ProbeCoordinator(FakePanel())
        coordinator.last_pick = {"position": [1, 1], "object": "IBRA_Face_1"}
        coordinator.view = _FakePickView(None)
        self.assertIsNone(coordinator._pick((9, 9)))
        self.assertNotIn("object", coordinator.last_pick)

    def test_missing_view_is_recorded(self):
        coordinator = _ProbeCoordinator(FakePanel())
        coordinator.view = None
        self.assertIsNone(coordinator._pick((1, 2)))
        self.assertEqual(coordinator.last_pick, {"position": [1, 2], "skipped": "no_view"})


class _FakePickView:
    def __init__(self, info):
        self.info = info

    def getObjectInfo(self, coin):
        return self.info


if __name__ == "__main__":
    unittest.main()
