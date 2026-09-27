"""Native FreeCAD GUI probe for the V0.5 mouse selection / coupling flow.

Internal child of ``scripts/check_mouse_interaction.py``.  It runs under FreeCAD's
bundled Python with a normal Qt platform and an isolated copied workbench + user
directory.

The probe drives **real native surfaces only**:

* the real ``Gui.Selection`` observer (``addSelection`` / ``setPreselection``) for
  left selection and hover, and
* real ``QtTest`` mouse/key events dispatched into the native 3D view, which flow
  through FreeCAD/Coin into the callback installed by ``CoinEventBridge``
  (``addEventCallbackPivy`` when available, else ``addEventCallback``).  The probe
  never calls the installed callback itself and never substitutes the picker.

Events are synthesized with QtTest, so the payload records
``input: "synthesized-qt-test"``; no physical mouse input is claimed.

A startup trace is appended (``<result>.trace``) before the FreeCAD/Qt imports and
around every stage, and the partial result JSON is written immediately, so a host
timeout still shows where the child stopped.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from copy import deepcopy
from pathlib import Path

PHASES = ("interaction", "persistence")


def _trace(trace_path: Path, stage: str):
    try:
        with trace_path.open("a", encoding="utf-8") as stream:
            stream.write(stage + "\n")
            stream.flush()
    except OSError:
        pass


def _read_trace(trace_path: Path):
    try:
        return trace_path.read_text(encoding="utf-8").splitlines()[-40:]
    except OSError:
        return []


def run_child(args) -> int:
    result_path = Path(args.result).resolve()
    trace_path = result_path.with_suffix(result_path.suffix + ".trace")
    try:
        trace_path.unlink()
    except OSError:
        pass

    def trace(stage):
        _trace(trace_path, stage)

    payload = {"phase": args.phase, "state": "starting", "passed": False,
               "checks": [], "error": None, "input": "synthesized-qt-test",
               "physical_input": False}
    try:
        result_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                               encoding="utf-8")
    except OSError:
        pass
    trace("enter")

    def check(label, condition, detail=""):
        payload["checks"].append({"check": label, "ok": bool(condition),
                                  "detail": str(detail)[:600]})
        print(f"  [{'PASS' if condition else 'FAIL'}] {args.phase}:{label} -> {detail}",
              flush=True)
        try:  # persist every check so a timeout never hides evidence
            result_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                   encoding="utf-8")
        except OSError:
            pass
        if not condition:
            raise AssertionError(f"{label}: {detail}")

    try:
        plugin = Path(args.plugin).resolve()
        user = Path(args.user_dir).resolve()
        document = Path(args.document).resolve()
        fixture = json.loads(Path(args.project).read_text(encoding="utf-8-sig"))
        sys.path.insert(0, str(plugin))
        trace("before-kratos_iga-import")
        import kratos_iga
        trace("before-FreeCAD-import")
        import FreeCAD as App
        import FreeCADGui as Gui
        trace("before-showMainWindow")
        check("plugin loaded from the isolated copy",
              Path(kratos_iga.__file__).resolve().is_relative_to(plugin), kratos_iga.__file__)
        App.getUserAppDataDir = lambda: str(user)
        Gui.showMainWindow()
        trace("after-showMainWindow")
        from PySide import QtCore, QtWidgets
        try:
            from PySide import QtTest
        except ImportError:
            # FreeCAD's PySide shim omits QtTest; it re-exports the same PySide6 binding.
            from PySide6 import QtTest
        trace("qt-test-imported")

        def class_name(widget):
            """The native C++ class name (``type.__name__`` is unreliable on shims)."""
            try:
                return str(widget.metaObject().className())
            except Exception:  # noqa: BLE001
                return type(widget).__name__

        def _is_renderer(name):
            return "View3DInventorViewer" in name or "QOpenGLWidget" in name

        def view_widget():
            """The proven native accessor for the active 3D renderer.

            ``mdi = main.findChild(QMdiArea); sub = mdi.activeSubWindow();
            root = sub.widget()`` (``Gui::View3DInventor``); the renderer is the
            ``Gui::View3DInventorViewer`` among ``root.findChildren(QWidget)`` (with
            ``QOpenGLWidget`` as fallback).  Returns ``(None, names)`` when inaccessible
            so the caller fails instead of silently falling back to the main window.
            """
            QtWidgets.QApplication.processEvents()
            names = []
            main = Gui.getMainWindow()
            try:
                mdi = main.findChild(QtWidgets.QMdiArea)
            except Exception:  # noqa: BLE001
                mdi = None
            if mdi is None:
                return None, ["QMdiArea-not-found"]
            sub = mdi.activeSubWindow()
            if sub is None:
                return None, ["activeSubWindow-None"]
            root = None
            widget_method = getattr(sub, "widget", None)
            if callable(widget_method):
                try:
                    root = widget_method()
                except Exception:  # noqa: BLE001
                    root = None
            if root is None:
                return None, ["sub.widget()-None"]
            names.append(class_name(root))
            viewer = gl = None
            for child in root.findChildren(QtWidgets.QWidget):
                child_name = class_name(child)
                names.append(child_name)
                if "QOpenGLWidget" in child_name and gl is None:
                    gl = child
                elif "View3DInventorViewer" in child_name and viewer is None:
                    viewer = child
            # The drawable the Coin ray pick / view.getSize() refer to is the inner
            # QOpenGLWidget; injecting into the Viewer container adds its border offset.
            return (gl or viewer), names[:60]

        def view_pixel_size(view):
            try:
                size = view.getSize()
                return (int(size[0]), int(size[1]))
            except Exception:  # noqa: BLE001 - reported by the caller
                return None

        def coin_to_qt(widget, view, position):
            """Coin/OpenGL device pixels -> the exact drawable-local Qt point.

            Uses the SAME inverse contract the native middle filter inverts
            (``interaction.map_coin_to_local``).  A coin pixel whose rounded local point
            falls outside the drawable is rejected rather than silently clamped; the edge
            pixel itself is additionally required to round-trip exactly.
            """
            size = view_pixel_size(view)
            if size is None:
                raise AssertionError("view.getSize() is unavailable for the click mapping")
            local = interaction_module.map_coin_to_local(
                position, (widget.width(), widget.height()), size)
            if local is None:
                raise AssertionError(
                    f"coin {position} has no drawable-local image in "
                    f"{widget.width()}x{widget.height()} (view {size})")
            return QtCore.QPoint(local[0], local[1])

        def widget_under(widget, point):
            top = QtWidgets.QApplication.widgetAt(widget.mapToGlobal(point))
            chain = []
            node = top
            while node is not None:
                chain.append(class_name(node))
                node = node.parentWidget()
            return top, chain

        def qt_click(widget, view, button, position):
            point = coin_to_qt(widget, view, position)
            top, chain = widget_under(widget, point)
            reached = top is not None and (top is widget or widget.isAncestorOf(top))
            record = {"button": repr(button),
                      "requested_coin": [int(position[0]), int(position[1])],
                      "qt_point": [point.x(), point.y()],
                      "widget_at": class_name(top) if top is not None else None,
                      "parent_chain": chain[:6], "reached_drawable": reached}
            if not reached:
                # A floating panel, transient FreeCAD notification label or stale stacking
                # can cover the drawable and steal the synthesized event; re-raise the
                # native main window and give a transient overlay time to auto-hide before
                # deciding the point is genuinely unreachable.
                main = Gui.getMainWindow()
                main.raise_()
                main.activateWindow()
                QtWidgets.QApplication.processEvents()
                for _attempt in range(50):
                    top, chain = widget_under(widget, point)
                    reached = top is not None and (top is widget or widget.isAncestorOf(top))
                    if reached:
                        break
                    name = class_name(top) if top is not None else ""
                    if "Notification" in name:
                        # FreeCAD's transient notification overlay is not part of the
                        # interaction under test; dismiss it instead of letting it absorb
                        # the synthesized click.
                        record["dismissed_overlay"] = name
                        try:
                            top.hide()
                        except Exception:  # noqa: BLE001
                            pass
                    QtTest.QTest.qWait(100)
                    QtWidgets.QApplication.processEvents()
                record["retry_widget_at"] = class_name(top) if top is not None else None
                record["retry_parent_chain"] = chain[:6]
                record["reached_drawable"] = reached
                record["retry_attempts"] = _attempt + 1
            payload.setdefault("click_guards", []).append(record)
            if not reached:
                raise AssertionError(
                    "QtTest click is not over the drawable: "
                    f"coin={list(position)} local={point.x()},{point.y()} "
                    f"widgetAt={record.get('retry_widget_at', record['widget_at'])} "
                    f"chain={record.get('retry_parent_chain', record['parent_chain'])}")
            # A physical user moves the pointer (creating preselection) before clicking;
            # QTest.mouseClick alone may never reach FreeCAD's CAD selection.
            QtTest.QTest.mouseMove(widget, point)
            QtWidgets.QApplication.processEvents()
            QtTest.QTest.qWait(30)
            QtTest.QTest.mouseClick(widget, button, QtCore.Qt.NoModifier, point)
            QtWidgets.QApplication.processEvents()
            QtTest.QTest.qWait(30)

        def qt_escape(widget):
            QtTest.QTest.keyClick(widget, QtCore.Qt.Key_Escape)
            QtWidgets.QApplication.processEvents()

        from kratos_iga.workbench import KratosIBRAWorkbench
        from kratos_iga import gui as panel_module
        from kratos_iga import interaction as interaction_module

        if "KratosIBRAWorkbench" not in Gui.listWorkbenches():
            Gui.addWorkbench(KratosIBRAWorkbench())
        Gui.activateWorkbench("KratosIBRAWorkbench")
        panel = panel_module.show_panel()
        # Real QtTest input needs a mapped, on-screen top-level window; moving it fully
        # off-screen makes the window system drop the synthesized mouse events.  FreeCAD
        # 1.x docks live inside a Gui::OverlaySplitter that can overlay the central 3D
        # drawable (QApplication.widgetAt then resolves to a TaskPanel/NotificationLabel
        # instead of the renderer and the synthesized click is stolen).  So the panel is
        # FLOATED and placed visibly OUTSIDE the drawable: the main window leaves room on
        # the right and the narrowed floating panel sits in it.  The per-click widgetAt
        # guard below still fails closed if anything ever overlaps the drawable.
        screen = QtWidgets.QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        main_window = Gui.getMainWindow()
        panel.setMinimumWidth(0)
        panel.setMaximumWidth(380)
        if available is not None:
            room = 400
            main_window.showNormal()
            main_window.setGeometry(available.x(), available.y(),
                                    max(700, available.width() - room), available.height())
            panel.setFloating(True)
            panel.show()
            panel.setGeometry(available.x() + available.width() - room + 10,
                              available.y() + 10, room - 20, available.height() - 20)
            panel.raise_()
        else:
            main_window.move(20, 20)
        main_window.raise_()
        main_window.activateWindow()
        QtWidgets.QApplication.processEvents()
        payload["screen_geometry"] = (
            [available.x(), available.y(), available.width(), available.height()]
            if available is not None else None)
        payload["main_geometry"] = [main_window.x(), main_window.y(),
                                    main_window.width(), main_window.height()]
        payload["panel_geometry"] = [panel.x(), panel.y(),
                                     panel.width(), panel.height()]
        payload["panel_floating"] = bool(panel.isFloating())
        trace("panel-created")

        if args.phase == "persistence":
            _run_persistence(payload, check, App, document, panel, trace)
            payload["passed"] = all(item["ok"] for item in payload["checks"])
            payload["state"] = "done"
            payload["trace"] = _read_trace(trace_path)
            result_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                   encoding="utf-8")
            return 0 if payload["passed"] else 1

        _run_interaction(payload, check, App, Gui, QtCore, QtWidgets, qt_click, qt_escape,
                         view_widget, panel_module, interaction_module, KratosIBRAWorkbench,
                         panel, user, document, fixture, args, trace)
        payload["passed"] = all(item["ok"] for item in payload["checks"])
        payload["state"] = "done"
    except Exception as exc:  # noqa: BLE001 - persisted for the host
        payload["error"] = f"{type(exc).__name__}: {exc}"
        payload["traceback"] = traceback.format_exc()[-1800:]
        payload["passed"] = False
        payload["state"] = "error"
    payload["trace"] = _read_trace(trace_path)
    result_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if payload["passed"] else 1


def _run_persistence(payload, check, App, document, panel, trace):
    trace("persistence-open")
    App.openDocument(str(document))
    opened = next((d for d in App.listDocuments().values()
                   if getattr(d, "FileName", "") and
                   Path(d.FileName).resolve() == document), None)
    check("document opened in a fresh process", opened is not None, list(App.listDocuments()))
    App.setActiveDocument(opened.Name)
    panel.restore_document()
    check("panel restored the project", panel.project is not None, panel.project is not None)
    records = panel.project.get("couplings", [])
    check("exactly one interface record restored", len(records) == 1, records)
    record = records[0]
    check("mouse-confirmed decision persisted through the real save/reopen",
          record.get("state") == "confirmed" and record.get("origin") == "user", record)
    row = panel.interaction._coupling_row(record["edge_id"])
    check("restored table shows the confirmed state",
          row >= 0 and panel.coupling_table.item(row, 2).text() == "confirmed", row)
    initials = panel.project.get("initial_conditions", [])
    check("the zero-displacement initial record survived the reopen",
          any(entry.get("variable") == "DISPLACEMENT"
              and (entry.get("value") or [None])[0] == 0.0
              and entry.get("target_id") == 8 for entry in initials), initials)
    trace("persistence-done")


def _run_interaction(payload, check, App, Gui, QtCore, QtWidgets, qt_click, qt_escape,
                     view_widget, panel_module, interaction_module, KratosIBRAWorkbench,
                     panel, user, document, fixture, args, trace):
    def pump(timeout_ms=30000):
        clock = QtCore.QElapsedTimer()
        clock.start()
        while panel.process and clock.elapsed() < timeout_ms:
            QtWidgets.QApplication.processEvents()
        return panel.process is None

    def fit_view():
        """Frame the live model before a scan (real view op, never a picker substitute).

        The docked panel can leave a smaller 3D view than the floating layout did, so the
        projected patch/edge must be re-framed to keep a verified interior pixel without
        loosening the picker or the refusals.
        """
        try:
            view.fitAll()
        except Exception:  # noqa: BLE001 - keep the current camera
            return
        QtWidgets.QApplication.processEvents()

    panel.backend_python.setText(os.environ["IBRA_BACKEND_PYTHON"])
    panel.pipeline_root.setText(os.environ["IBRA_PIPELINE_ROOT"])
    panel.work_directory.setText(str(user / "work"))
    panel.save_backend_configuration()
    panel.pending_project = None
    panel.load_project(str(Path(args.project).resolve()))
    trace("project-loading")
    check("conversion finished", pump() and panel.cache is not None,
          panel.log.toPlainText()[-600:])
    panel.project.setdefault("geometry_sha256", panel.index.get("geometry_sha256"))
    panel.sync()
    App.setActiveDocument(panel.doc.Name)
    trace("project-loaded")
    check("two patches and one candidate interface are indexed",
          len(panel.index.get("faces", [])) == 2
          and len(panel.project.get("couplings", [])) == 1,
          f"faces={[f['id'] for f in panel.index.get('faces', [])]} "
          f"couplings={panel.project.get('couplings')}")

    interaction = panel.interaction
    machine = interaction.machine
    bridge = interaction.bridge
    face_names = {ident: panel.entities[("face", ident)]
                  for (kind, ident) in panel.entities if kind == "face"}
    boundary_edge = next((edge for edge in panel.index["edges"]
                          if edge.get("kind") == "edge"), None)
    check("a one-sided boundary edge exists for the rejection checks",
          boundary_edge is not None,
          [edge.get("kind") for edge in panel.index.get("edges", [])])
    boundary_name = panel.entities[("edge", boundary_edge["id"])]
    coupling = panel.project["couplings"][0]
    coupling_name = panel.entities[("coupling", coupling["edge_id"])]
    App.setActiveDocument(panel.doc.Name)
    QtWidgets.QApplication.processEvents()
    view = Gui.activeDocument().activeView()
    try:
        view.setNavigationType("Gui::CADNavigationStyle")
    except Exception:  # noqa: BLE001 - the isolated user keeps its own navigation style
        pass
    widget, widget_names = view_widget()
    try:
        widget_class = str(widget.metaObject().className()) if widget is not None else None
    except Exception:  # noqa: BLE001
        widget_class = type(widget).__name__ if widget is not None else None
    payload["qt_widget"] = widget_class
    payload["widget_candidates"] = widget_names
    if widget is not None:
        payload["widget_size"] = [widget.width(), widget.height()]
        try:
            payload["device_pixel_ratio"] = widget.devicePixelRatio()
        except Exception:  # noqa: BLE001
            payload["device_pixel_ratio"] = None
    try:
        payload["view_size"] = list(view.getSize())
    except Exception:  # noqa: BLE001
        payload["view_size"] = None
    check("the native renderer drawable (QOpenGLWidget) is accessible",
          widget is not None and widget is not Gui.getMainWindow()
          and "QOpenGLWidget" in (payload["qt_widget"] or ""),
          f"widget={payload['qt_widget']} names={widget_names[:12]}")

    def select(*pairs):
        Gui.Selection.clearSelection()
        for doc_name, object_name, sub in pairs:
            Gui.Selection.addSelection(doc_name, object_name, sub)

    def selection_dump():
        entries = []
        try:
            for entry in Gui.Selection.getSelectionEx():
                entries.append({"object": entry.Object.Name,
                                "subs": list(entry.SubElementNames)})
        except Exception as error:  # noqa: BLE001
            entries.append({"error": str(error)})
        return entries

    def target_detail():
        return (f"selection={selection_dump()} notice={machine.notice!r} "
                f"guidance={panel.interaction_hint.text()!r} "
                f"coin={bridge.last_event} pick={interaction.last_pick} "
                f"selection_targets={interaction.last_selection_targets}")

    def left_select(object_name, component_prefix=None):
        """Select a target with a real QtTest LEFT click into the native renderer."""
        pixel = face_pixel(object_name, component_prefix=component_prefix)
        qt_click(widget, view, QtCore.Qt.LeftButton, pixel)
        return pixel

    def left_capture_face(object_name, face_id, label, coupling=False):
        """Left-click a patch interior and require the machine target before middle."""
        pixel = left_select(object_name, component_prefix="Face")
        subs = [sub for entry in selection_dump() for sub in entry.get("subs", [])]
        face_subs_ok = not [sub for sub in subs
                            if not sub.startswith("Face") and sub != ""]
        if coupling:
            consumed = machine.face1 is not None or machine.face2 is not None
        else:
            consumed = [target.key() for target in machine.targets] == [("face", face_id, None)]
        check(f"{label}: left click selected the whole patch {face_id}",
              face_subs_ok and consumed, target_detail())
        return pixel

    def face_pixel(object_name, component_prefix=None):
        trace("scan:" + object_name)
        fit_view()
        position, resolution = find_object_position(view, object_name,
                                                    component_prefix=component_prefix)
        trace("scan-done:" + object_name)
        check(f"a real pixel resolves to {object_name}", position is not None, resolution)
        return position

    # ---- installed callback contract -----------------------------------------
    # FreeCAD 1.x docks live in a Gui::OverlaySplitter and the built-in TaskView / dock
    # overlays render over the central 3D view, so QApplication.widgetAt (and therefore
    # native click routing) resolves to Gui::TaskView::TaskPanel instead of the renderer
    # even though no plugin source is involved.  For this isolated test UI only, hide every
    # built-in main-window dock except the IBRA plugin panel, after loading and before any
    # interaction, and record what was hidden.  No source, global preference or user
    # configuration is changed; every real hit-pixel click below is still widgetAt-guarded.
    hidden_docks = []
    for dock in Gui.getMainWindow().findChildren(QtWidgets.QDockWidget):
        if dock is panel:
            continue
        try:
            hidden_docks.append({"title": str(dock.windowTitle()),
                                 "class": str(dock.metaObject().className()),
                                 "was_visible": bool(dock.isVisible())})
            dock.hide()
        except Exception as error:  # noqa: BLE001 - recorded, never fatal
            hidden_docks.append({"error": f"{type(error).__name__}: {error}"})
    payload["hidden_builtin_docks"] = hidden_docks
    QtWidgets.QApplication.processEvents()
    trace("interaction-checks-start")
    trace("attach-start")
    panel.start_boundary_pick("supports")
    trace("attach-done")
    check("physical pick starts and installs the Selection observer",
          machine.mode == "boundary" and interaction._observers_attached, machine.mode)
    check("the real view event callback is installed (pivy or dictionary contract)",
          bridge.attached and bridge.callbacks and bridge.mode in ("pivy", "dict"),
          f"attached={bridge.attached} mode={bridge.mode} callbacks={len(bridge.callbacks)}")
    payload["callback_mode"] = bridge.mode

    # ---- support: real QtTest LEFT Face1 then real middle commit -------------
    pixel = left_capture_face(face_names[2], 2, "support")
    payload["native_coin_last_event"] = bridge.last_event
    payload["native_selection_subelements"] = selection_dump()
    qt_click(widget, view, QtCore.Qt.MiddleButton, pixel)
    payload["native_coin_middle_event"] = bridge.last_event
    check("support middle commit wrote the real target",
          panel.supports_targets == [{"target_kind": "face", "target_id": 2}]
          and not machine.active(),
          f"targets={panel.supports_targets} {target_detail()}")
    check("capture success shows the confirmed Add / Update guidance",
          "Add / Update" in panel.interaction_hint.text(), panel.interaction_hint.text())

    # ---- initial field: real QtTest LEFT Face1 then real middle commit -------
    # Ordinary capture runs BEFORE any geometry edit, so no restore/undo is involved.
    panel.start_boundary_pick("initial_conditions")
    pixel = left_capture_face(face_names[8], 8, "initial")
    qt_click(widget, view, QtCore.Qt.MiddleButton, pixel)
    payload["middle_events_after_initial"] = list(interaction.middle_events)
    check("initial-field middle commit wrote the real target",
          panel.initial_conditions_targets == [{"target_kind": "face", "target_id": 8}],
          f"targets={panel.initial_conditions_targets} {target_detail()}")

    # ---- pressure: edge refused, real QtTest LEFT Face1 then middle commit ---
    panel.load_kind.setCurrentIndex(panel.load_kind.findData("reference_pressure"))
    panel.start_boundary_pick("loads")
    select((panel.doc.Name, boundary_name, ""))
    check("reference pressure refuses an edge without mutation",
          "压力仅允许面" in (machine.notice or "") and not machine.targets,
          f"notice={machine.notice!r} targets={machine.targets}")
    pixel = left_capture_face(face_names[2], 2, "pressure")
    qt_click(widget, view, QtCore.Qt.MiddleButton, pixel)
    check("pressure middle commit wrote the real target face",
          panel.loads_targets == [{"target_kind": "face", "target_id": 2}],
          f"targets={panel.loads_targets} {target_detail()}")

    # ---- capture failure must not show a false confirmation ------------------
    original_capture = panel.capture
    panel.capture = lambda category: None
    try:
        panel.start_boundary_pick("supports")
        pixel = left_capture_face(face_names[2], 2, "capture-failure")
        qt_click(widget, view, QtCore.Qt.MiddleButton, pixel)
    finally:
        panel.capture = original_capture
    check("a guarded capture failure never shows the confirmed guidance",
          interaction_module.GUIDANCE_CAPTURE_FAILED in panel.interaction_hint.text()
          and "Add / Update" not in panel.interaction_hint.text(),
          panel.interaction_hint.text())

    # ---- hover through the actual Selection API signature --------------------
    panel.hover_mode.setCurrentIndex(panel.hover_mode.findData("coupling"))
    panel.start_boundary_pick("supports")
    Gui.Selection.setPreselection(panel.doc.getObject(face_names[2]), "Face1")
    QtWidgets.QApplication.processEvents()
    check("setPreselection(obj, 'Face1') audits the whole patch",
          machine.hover_text is not None and "Patch 2" in machine.hover_text,
          machine.hover_text)
    Gui.Selection.setPreselection(panel.doc.getObject(coupling_name), "")
    QtWidgets.QApplication.processEvents()
    check("setPreselection(obj, '') audits the interface with both sides",
          machine.hover_text is not None
          and f"Coupling {coupling['edge_id']}" in machine.hover_text
          and "Patch" in machine.hover_text,
          machine.hover_text)
    Gui.Selection.clearPreselection()
    QtWidgets.QApplication.processEvents()
    panel.hover_mode.setCurrentIndex(panel.hover_mode.findData("all"))

    # ---- each hover view mode audits its own kind -----------------------------
    panel.start_boundary_pick("supports")
    for mode, object_name, sub, expected in (
            ("edge", boundary_name, "", "Edge"),
            ("patch", face_names[2], "Face1", "Patch"),
            ("coupling", coupling_name, "", "Coupling")):
        panel.hover_mode.setCurrentIndex(panel.hover_mode.findData(mode))
        Gui.Selection.setPreselection(panel.doc.getObject(object_name), sub)
        QtWidgets.QApplication.processEvents()
        check(f"hover view mode {mode} audits {expected}",
              machine.hover_text is not None and expected in machine.hover_text,
              f"{mode}: {machine.hover_text}")
    Gui.Selection.clearPreselection()
    QtWidgets.QApplication.processEvents()
    panel.hover_mode.setCurrentIndex(panel.hover_mode.findData("all"))

    # ---- coupling-only hides unrelated drawables too --------------------------
    unrelated = panel.doc.addObject("Part::Feature", "IBRA_UnrelatedPart")
    panel.doc.recompute()
    preview_glyph = panel.doc.addObject("Part::Feature", "IBRA_PreviewGlyph")
    panel.doc.recompute()
    panel.preview_objects[("preview", "glyph")] = preview_glyph.Name
    panel.doc.getObject(unrelated.Name).ViewObject.Visibility = True
    panel.doc.getObject(preview_glyph.Name).ViewObject.Visibility = True
    # Isolation on its own, with no picking session active.
    panel.cancel_interaction()
    QtWidgets.QApplication.processEvents()
    panel.toggle_coupling_visibility()
    check("coupling-only hides unrelated visible Part and preview glyphs",
          not panel.doc.getObject(unrelated.Name).ViewObject.Visibility
          and not panel.doc.getObject(preview_glyph.Name).ViewObject.Visibility,
          "unrelated/preview not hidden")
    check("isolation alone installs the document-close observer (no picking hooks)",
          interaction._doc_observer_attached and not interaction._observers_attached,
          f"doc_obs={interaction._doc_observer_attached} "
          f"sel_obs={interaction._observers_attached}")
    panel.toggle_coupling_visibility()
    check("unrelated and preview glyph visibility restored exactly",
          bool(panel.doc.getObject(unrelated.Name).ViewObject.Visibility)
          and bool(panel.doc.getObject(preview_glyph.Name).ViewObject.Visibility),
          "restore failed")
    panel.preview_objects.pop(("preview", "glyph"), None)
    panel.doc.removeObject(preview_glyph.Name)
    panel.doc.removeObject(unrelated.Name)
    panel.doc.recompute()

    # ---- rejection paths -----------------------------------------------------
    foreign = App.newDocument("IBRA_MouseForeign")
    foreign.addObject("Part::Feature", "ForeignShape")
    foreign.recompute()
    panel.start_boundary_pick("supports")
    select((panel.doc.Name, face_names[2], ""))
    before = [target.key() for target in machine.targets]
    # Add the foreign object WITHOUT clearing: a clear would itself empty the pending
    # targets and make the "unchanged" assertion meaningless.
    Gui.Selection.addSelection(foreign.Name, "ForeignShape", "")
    QtWidgets.QApplication.processEvents()
    check("a foreign-document object is refused without mutation",
          machine.last_refusal == "foreign_document"
          and [target.key() for target in machine.targets] == before,
          f"{machine.last_refusal} {machine.targets}")
    Gui.Selection.clearSelection()
    App.closeDocument(foreign.Name)
    App.setActiveDocument(panel.doc.Name)

    spare = panel.doc.addObject("Part::Feature", "IBRA_MouseUnregistered")
    panel.doc.recompute()
    panel.start_boundary_pick("supports")
    select((panel.doc.Name, spare.Name, ""))
    check("an unregistered/preview display object is refused",
          machine.last_refusal == "unregistered" and not machine.targets,
          f"{machine.last_refusal} {machine.targets}")
    panel.doc.removeObject(spare.Name)
    panel.doc.recompute()
    App.setActiveDocument(panel.doc.Name)

    preview_spare = panel.doc.addObject("Part::Feature", "IBRA_MousePreview")
    panel.doc.recompute()
    panel.preview_objects[("preview", "probe")] = preview_spare.Name
    panel.start_boundary_pick("supports")
    select((panel.doc.Name, preview_spare.Name, ""))
    check("a registered display-only preview object is refused as a preview target",
          machine.last_refusal == "preview_or_result" and not machine.targets,
          f"{machine.last_refusal} {machine.targets}")
    panel.preview_objects.pop(("preview", "probe"), None)
    panel.doc.removeObject(preview_spare.Name)
    panel.doc.recompute()
    App.setActiveDocument(panel.doc.Name)

    panel.start_boundary_pick("supports")
    select((panel.doc.Name, coupling_name, ""))
    check("an interface edge is refused for an ordinary boundary",
          "接口边" in (machine.notice or "") and not machine.targets,
          f"notice={machine.notice!r} targets={machine.targets}")

    # ---- full coupling flow with real QtTest LEFT clicks + middle ------------
    panel.start_coupling_pick()
    left_capture_face(face_names[2], 2, "coupling-face1", coupling=True)
    check("real QtTest LEFT click accepted the first candidate face",
          machine.step == "face2" and machine.face1 is not None,
          f"step={machine.step} face1={machine.face1} {target_detail()}")
    left_capture_face(face_names[8], 8, "coupling-face2", coupling=True)
    check("real QtTest LEFT click accepted the second face and resolved the candidate",
          machine.candidate_edge == coupling["edge_id"],
          f"candidate={machine.candidate_edge} {target_detail()}")
    # A middle click that does not hit the candidate edge must be refused (the fresh
    # pick/resolver decides; no cached target and no edge pick yet).
    qt_click(widget, view, QtCore.Qt.MiddleButton, face_pixel(face_names[8],
                                                              component_prefix="Face"))
    check("middle on a face at the coupling edge step is refused",
          machine.active() and machine.step == "edge" and not machine.edge_picked
          and machine.candidate_edge == coupling["edge_id"],
          f"notice={machine.notice!r} step={machine.step} {target_detail()}")

    record_before = deepcopy(panel.project["couplings"][0])
    coupling_visibility_before = bool(panel.doc.getObject(coupling_name).ViewObject.Visibility)
    # Clear the (possibly hidden) face selection so its ghost does not obscure the ray
    # pick; the coupling machine state (face1/face2) is retained.
    Gui.Selection.clearSelection()
    QtWidgets.QApplication.processEvents()
    panel.toggle_coupling_visibility()
    QtWidgets.QApplication.processEvents()
    clock = QtCore.QElapsedTimer()
    clock.start()
    while clock.elapsed() < 80:
        QtWidgets.QApplication.processEvents()
    coupling_object = panel.doc.getObject(coupling_name)
    check("coupling-only isolation is active with the interface edge visible",
          interaction.visibility.active
          and bool(coupling_object.ViewObject.Visibility),
          f"active={interaction.visibility.active} "
          f"visibility={getattr(coupling_object.ViewObject, 'Visibility', None)}")
    payload["doc_objects"] = [
        {"name": obj.Name, "type": obj.TypeId,
         "visibility": bool(getattr(getattr(obj, "ViewObject", None), "Visibility", False)),
         "selectable": bool(getattr(getattr(obj, "ViewObject", None), "Selectable", True))}
        for obj in panel.doc.Objects if getattr(obj, "ViewObject", None) is not None]
    # Re-acquire the CURRENT document/view/renderer after every transition and before
    # clicking; a stale renderer is exactly what made the old click miss.
    App.setActiveDocument(panel.doc.Name)
    QtWidgets.QApplication.processEvents()
    view = Gui.activeDocument().activeView()
    widget, widget_names = view_widget()
    payload["coupling_renderer_info"] = dict(interaction.renderer_info)
    payload["coupling_event_counts_before"] = dict(interaction.event_counts)
    payload["coupling_view_id"] = id(view)
    payload["coupling_widget"] = (str(widget.metaObject().className())
                                  if widget is not None else None)
    fit_view()
    edge_hits, projected_resolution = find_projected_hits(view, coupling_name,
                                                          coupling_object)
    payload["projected_hits"] = edge_hits[-60:]
    payload["projected_hits_count"] = len(edge_hits)
    payload["projected_resolution"] = projected_resolution
    seeds = [tuple(hit["pick"]) for hit in edge_hits]
    if not seeds:
        fallback_pixel, fallback_resolution = find_object_position(
            view, coupling_name, max_calls=8000, coarse=False)
        payload["projected_fallback"] = fallback_resolution
        if fallback_pixel is not None:
            seeds = [tuple(fallback_pixel)]

    def current_view_pixel():
        size = view.getSize()
        return (int(size[0]), int(size[1]))

    # Only a REAL hit pixel whose QtTest round trip is exact AND that still resolves to
    # the edge with a verified interior margin may be injected.  The round trip uses the
    # same inverse contract the native filter applies, so the injected point cannot drift.
    edge_pixel, edge_local, edge_choice = choose_edge_pixel(
        seeds, view, coupling_name, widget, current_view_pixel(),
        interaction_module.map_coin_to_local, interaction_module.map_local_to_coin)
    payload["edge_pixel_choice"] = edge_choice
    payload["edge_requested_local"] = list(edge_local) if edge_local is not None else None
    check("a real projected shape pixel resolves to the interface edge in coupling-only view",
          edge_pixel is not None,
          payload.get("projected_resolution") or payload.get("projected_fallback"))
    qt_click(widget, view, QtCore.Qt.MiddleButton, edge_pixel)
    check("middle without an explicit LEFT edge pick is refused",
          machine.active() and machine.step == "edge" and not machine.edge_picked,
          f"notice={machine.notice!r} step={machine.step} picked={machine.edge_picked}")
    before_click_event = bridge.last_event
    qt_click(widget, view, QtCore.Qt.LeftButton, edge_pixel)
    payload["coupling_event_counts_after_left"] = dict(interaction.event_counts)
    payload["coupling_last_event_before"] = before_click_event
    payload["coupling_last_event_after"] = bridge.last_event
    payload["edge_requested_coin"] = list(edge_pixel) if edge_pixel else None
    payload["edge_actual_coin"] = (bridge.last_event or {}).get("position")
    payload["edge_receiver_offset"] = interaction.last_offset
    payload["edge_drawable"] = {
        "widget": payload.get("qt_widget"),
        "widget_size": payload.get("widget_size"),
        "view_size": payload.get("view_size"),
        "device_pixel_ratio": payload.get("device_pixel_ratio")}
    payload["edge_left_fallback_attempts"] = interaction.edge_left_fallback_attempts
    payload["edge_left_fallback_hits"] = interaction.edge_left_fallback_hits
    payload["last_edge_left_fallback"] = interaction.last_edge_left_fallback
    payload["middle_events_before_confirm"] = list(interaction.middle_events)[-80:]
    payload["middle_events_before_confirm_count"] = len(interaction.middle_events)
    payload["filter_generation"] = interaction.filter_generation
    payload["filter_history"] = list(interaction.filter_history)[-20:]
    check("real QtTest LEFT click accepted the interface edge at the edge step",
          machine.step == "edge" and machine.edge_picked
          and "中键" in panel.interaction_hint.text(),
          f"step={machine.step} picked={machine.edge_picked} "
          f"fallback={interaction.edge_left_fallback_hits}/"
          f"{interaction.edge_left_fallback_attempts} {target_detail()}")
    payload["pick_resolution"] = "real-QtTest-left-and-middle-projected"
    qt_click(widget, view, QtCore.Qt.MiddleButton, edge_pixel)
    # Capture the middle-confirm evidence IMMEDIATELY after the final middle click,
    # before any other assertion, so ``middle_events`` describes the confirmed event.
    payload["coupling_event_counts_after"] = dict(interaction.event_counts)
    payload["middle_events"] = list(interaction.middle_events)[-80:]
    payload["middle_events_count"] = len(interaction.middle_events)
    payload["last_pick_after_confirm"] = interaction.last_pick
    payload["filter_generation"] = interaction.filter_generation
    payload["filter_history"] = list(interaction.filter_history)[-20:]
    # Image AFTER the successful edge clicks, at the current device dimensions, and only
    # then re-acquire the view (saveImage may rebuild the renderer).
    try:
        image_path = Path(document).resolve().parent / "coupling_only.png"
        view.saveImage(str(image_path), 1014, 553, "PNG")
        payload["coupling_only_image"] = str(image_path)
    except Exception as error:  # noqa: BLE001 - reported, never fatal
        payload["coupling_only_image_error"] = f"{type(error).__name__}: {error}"
    App.setActiveDocument(panel.doc.Name)
    QtWidgets.QApplication.processEvents()
    view = Gui.activeDocument().activeView()
    widget, widget_names = view_widget()
    payload["coupling_renderer_info_after_image"] = dict(interaction.renderer_info)
    check("middle commit through the real Qt event confirmed the target edge",
          machine.confirmed_edge == coupling["edge_id"] and not machine.active(),
          f"confirmed={machine.confirmed_edge} mode={machine.mode}")
    check("the mouse flow alone did not change the stored decision",
          panel.project["couplings"][0] == record_before,
          f"{panel.project['couplings'][0]} vs {record_before}")
    row = interaction._coupling_row(coupling["edge_id"])
    check("the coupling table is highlighted on the confirmed edge",
          row >= 0 and panel.coupling_table.currentRow() == row, row)
    combo = panel.coupling_table.cellWidget(row, 4) if row >= 0 else None
    check("the right side exposes the existing type and penalty",
          combo is not None
          and bool(combo.currentData()) == bool(record_before.get("rotation"))
          and panel.coupling_table.item(row, 5).text() == str(record_before.get("penalty")),
          f"rotation={None if combo is None else combo.currentData()} "
          f"penalty={None if row < 0 else panel.coupling_table.item(row, 5).text()}")
    check("guidance points at the explicit decision buttons",
          "Confirm Selected Interface" in panel.interaction_hint.text(),
          panel.interaction_hint.text())
    panel.toggle_coupling_visibility()
    check("coupling-only visibility was restored after the pick",
          bool(panel.doc.getObject(coupling_name).ViewObject.Visibility)
          == coupling_visibility_before)

    panel.coupling_table.setCurrentCell(row, 0)
    panel.coupling_decision("pending", "geometry")
    check("Reset to Candidate changes the decision explicitly",
          panel.project["couplings"][0]["state"] == "pending"
          and panel.project["couplings"][0]["origin"] == "geometry",
          panel.project["couplings"][0])
    # refresh() rebuilds the table, so select the row again for the explicit confirm.
    row = interaction._coupling_row(coupling["edge_id"])
    panel.coupling_table.setCurrentCell(row, 0)
    panel.coupling_decision("confirmed", "user")
    check("Confirm Selected Interface writes the confirmed intent",
          panel.project["couplings"][0]["state"] == "confirmed"
          and panel.project["couplings"][0]["origin"] == "user",
          panel.project["couplings"][0])

    # ---- reversible reveal of a hidden table-selected interface --------------
    panel.doc.getObject(coupling_name).ViewObject.Visibility = False
    panel.coupling_table.clearSelection()
    panel.coupling_table.selectRow(row)
    QtWidgets.QApplication.processEvents()
    revealed = bool(panel.doc.getObject(coupling_name).ViewObject.Visibility)
    panel.coupling_table.clearSelection()
    QtWidgets.QApplication.processEvents()
    hidden_again = not bool(panel.doc.getObject(coupling_name).ViewObject.Visibility)
    check("a hidden table-selected interface is revealed and then restored",
          revealed and hidden_again and not interaction.revealed,
          f"revealed={revealed} hidden_again={hidden_again}")

    # ---- visibility isolate/restore -----------------------------------------
    names = [name for name in panel.entities.values() if name]
    before_visibility = {name: bool(panel.doc.getObject(name).ViewObject.Visibility)
                         for name in names}
    panel.toggle_coupling_visibility()
    after_isolate = {name: bool(panel.doc.getObject(name).ViewObject.Visibility)
                     for name in names}
    check("coupling-only shows exactly the interface edges",
          all(after_isolate[panel.entities[key]] for key in panel.entities
              if key[0] == "coupling")
          and all(not after_isolate[panel.entities[key]] for key in panel.entities
                  if key[0] != "coupling"),
          {key: after_isolate[value] for key, value in panel.entities.items()})
    check("the coupling-only button reflects the active state",
          "恢复" in panel.coupling_isolate_button.text(),
          panel.coupling_isolate_button.text())
    panel.toggle_coupling_visibility()
    check("the previous visibility is restored exactly",
          {name: bool(panel.doc.getObject(name).ViewObject.Visibility)
           for name in names} == before_visibility)

    # ---- Escape through real Qt key dispatch ---------------------------------
    panel.start_coupling_pick()
    select((panel.doc.Name, face_names[8], ""))
    check("coupling flow advanced before Escape", machine.step == "face2", machine.step)
    qt_escape(widget)
    check("Escape through the real view cancels and detaches",
          not machine.active() and not bridge.attached
          and interaction_module.GUIDANCE_CANCELLED in panel.interaction_hint.text(),
          f"mode={machine.mode} attached={bridge.attached}")

    # ---- page switch, inner Boundary tab switch, hide and workbench exit -----
    panel.start_boundary_pick("supports")
    check("a picking session is attached before the page switch", bridge.attached)
    current_tab = panel.tabs.currentIndex()
    other_tab = 0 if current_tab != 0 else min(1, panel.tabs.count() - 1)
    panel.tabs.setCurrentIndex(other_tab)
    QtWidgets.QApplication.processEvents()
    check("page switch removed the callbacks and restored visibility",
          not bridge.attached and not interaction.visibility.active
          and "切换" in panel.interaction_hint.text(),
          f"tab {current_tab}->{other_tab} attached={bridge.attached} "
          f"{panel.interaction_hint.text()}")

    boundary_index = next(i for i in range(panel.tabs.count())
                          if panel.tabs.tabText(i) == "Boundary")
    panel.tabs.setCurrentIndex(boundary_index)
    QtWidgets.QApplication.processEvents()
    panel.start_boundary_pick("supports")
    check("a picking session is attached before the inner tab switch", bridge.attached)
    panel.boundary_tabs.setCurrentIndex(1)
    QtWidgets.QApplication.processEvents()
    check("Support -> Loads inner tab switch removed the callbacks",
          not bridge.attached and not interaction.visibility.active,
          f"attached={bridge.attached}")

    panel.start_boundary_pick("supports")
    panel.toggle_coupling_visibility()
    panel.hide()
    QtWidgets.QApplication.processEvents()
    check("hiding the dock removed the callbacks and restored visibility",
          not bridge.attached and not interaction.visibility.active
          and not panel.hover_audit.isChecked(),
          f"attached={bridge.attached} active={interaction.visibility.active}")
    panel.show()
    QtWidgets.QApplication.processEvents()

    panel.start_boundary_pick("supports")
    check("a picking session is attached before workbench exit", bridge.attached)
    other = next((name for name in Gui.listWorkbenches()
                  if name not in ("KratosIBRAWorkbench", "NoneWorkbench")), None)
    check("another installed workbench exists for a real switch", other is not None,
          list(Gui.listWorkbenches()))
    Gui.activateWorkbench(other)
    QtWidgets.QApplication.processEvents()
    check("a real workbench switch cleared callbacks, middle filter and visibility",
          not bridge.attached and interaction._middle_filter is None
          and not interaction.visibility.active,
          f"attached={bridge.attached} filter={interaction._middle_filter} "
          f"active={interaction.visibility.active}")
    Gui.activateWorkbench("KratosIBRAWorkbench")
    QtWidgets.QApplication.processEvents()

    # ---- a real zero-displacement initial record through Add / Update --------
    if not panel.initial_conditions_targets:
        panel.start_boundary_pick("initial_conditions")
        pixel = left_capture_face(face_names[8], 8, "initial-record")
        qt_click(widget, view, QtCore.Qt.MiddleButton, pixel)
    panel.initial_variable.setCurrentText("DISPLACEMENT")
    panel.initial_vector.set([0.0, None, None])
    panel.initial_conditions_name.setText("Probe_Zero_Initial")
    panel.add_condition("initial_conditions")
    initials = panel.project.get("initial_conditions", [])
    check("a zero-displacement initial record was added through Add / Update",
          any(record.get("variable") == "DISPLACEMENT"
              and (record.get("value") or [None])[0] == 0.0
              and record.get("target_id") == 8 for record in initials),
          initials)

    # ---- real save for the fresh-process persistence phase -------------------
    # The interaction tests leave test-only editor drafts (captured target lists);
    # discard them through the existing panel entry point BEFORE the clean save so the
    # sync/save guard stays intact. Snapshot every committed record (including the
    # coupling decisions) BEFORE the discard and assert nothing changed by the cleanup.
    App.setActiveDocument(panel.doc.Name)
    QtWidgets.QApplication.processEvents()
    records_before_cleanup = {
        category: deepcopy(panel.project.get(category, []))
        for category in ("supports", "loads", "initial_conditions", "couplings")}
    panel.new_condition("supports")
    panel.new_condition("loads")
    panel.new_condition("initial_conditions")
    records_after_cleanup = {
        category: panel.project.get(category, [])
        for category in ("supports", "loads", "initial_conditions", "couplings")}
    payload["pending_editor_before_save"] = bool(panel._pending_condition_edit())
    check("discarding the test drafts left every committed record unchanged",
          records_after_cleanup == records_before_cleanup,
          f"before={records_before_cleanup} after={records_after_cleanup}")
    check("no pending editor draft before the clean save",
          not panel._pending_condition_edit(),
          f"pending={panel._pending_condition_edit()} "
          f"targets={panel.supports_targets}/{panel.loads_targets}/"
          f"{panel.initial_conditions_targets} editing={panel.editing}")
    trace("saving-document")
    original_dialog = QtWidgets.QFileDialog.getSaveFileName
    QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(document), ""))
    try:
        panel.save_to_document()
    finally:
        QtWidgets.QFileDialog.getSaveFileName = original_dialog
    records_after_save = {category: panel.project.get(category, [])
                          for category in ("supports", "loads", "initial_conditions",
                                           "couplings")}
    check("committed records are unchanged through the clean save",
          records_after_save == records_before_cleanup,
          f"before={records_before_cleanup} after={records_after_save}")
    if not document.is_file():
        payload["save_status"] = panel.status.text()
        payload["save_log"] = panel.log.toPlainText()[-1000:]
    check("FCStd written with the confirmed decision and the initial record",
          document.is_file() and document.stat().st_size > 0,
          f"{document} status={panel.status.text()!r} "
          f"log={panel.log.toPlainText()[-400:]!r}")

    # ---- destructive LAST: modified geometry must be refused by the fresh middle,
    # AFTER the clean save and every valid workflow; nothing is restored or re-saved.
    destructive_name = face_names[2]
    panel.start_boundary_pick("supports")
    destructive_pixel = left_capture_face(destructive_name, 2, "destructive-left")
    panel.supports_targets = []
    destructive = panel.doc.getObject(destructive_name)
    destructive.Placement = App.Placement(destructive.Placement.Base + App.Vector(0.5, 0, 0),
                                          destructive.Placement.Rotation)
    panel.doc.recompute()
    before_targets = [target.key() for target in machine.targets]
    qt_click(widget, view, QtCore.Qt.MiddleButton, destructive_pixel)
    payload["destructive_middle_events"] = list(interaction.middle_events)
    payload["destructive_last_pick"] = interaction.last_pick
    check("modified geometry between left and middle is refused as stale (no restore)",
          machine.last_refusal == "stale_geometry"
          and [target.key() for target in machine.targets] == before_targets
          and not panel.supports_targets,
          f"refusal={machine.last_refusal} {target_detail()}")

    # Isolation alone (no picking), then close the document: the standalone close
    # observer must reset the saved visibility/buttons.
    panel.toggle_coupling_visibility()
    check("coupling-only is active before the document close",
          interaction.visibility.active and interaction._doc_observer_attached,
          f"active={interaction.visibility.active}")
    App.closeDocument(panel.doc.Name)
    QtWidgets.QApplication.processEvents()
    check("document close cleared the standalone isolation state",
          not interaction.visibility.active and not interaction.visibility.saved
          and not interaction._doc_observer_attached,
          f"active={interaction.visibility.active} saved={interaction.visibility.saved} "
          f"doc_obs={interaction._doc_observer_attached}")
    trace("interaction-done")


def view_size(view):
    """Best-effort 3D view size in device pixels."""
    for attribute in ("getSize", "size"):
        getter = getattr(view, attribute, None)
        if getter is None:
            continue
        try:
            value = getter()
        except Exception:  # noqa: BLE001 - the next strategy is tried
            continue
        try:
            return int(value[0]), int(value[1])
        except Exception:  # noqa: BLE001
            continue
    try:
        import FreeCADGui as Gui
        window = Gui.getMainWindow()
        return int(window.width()), int(window.height())
    except Exception:  # noqa: BLE001
        return 800, 600


def _neighborhood(centre, view_height, margin, step, view_width):
    """Device pixels around a projected point, trying both vertical orientations."""
    cx, cy = centre
    for base_y in (cy, view_height - 1 - cy):
        for dx in range(-margin, margin + 1, step):
            for dy in range(-margin, margin + 1, step):
                yield (max(0, min(view_width - 1, cx + dx)),
                       max(0, min(view_height - 1, base_y + dy)))


def find_projected_hits(view, object_name, shape_object, margin=12, step=1, limit=24):
    """Project the object's real shape samples and collect every validated pixel.

    Uses ``obj.Shape.Edges[i].valueAt(t)`` -> ``view.getPointOnScreen`` (no fake picker,
    no ``addSelection``) and validates each candidate with ``getObjectInfo``.  Returns
    ``(hits, resolution)`` where each hit is
    ``{edge, frac, projected, pick, component}``; the caller chooses the injectable pixel
    from the real hits and revalidates it.  Collection stops at ``limit`` hits so a probe
    timeout can never be blamed on an unbounded scan.
    """
    import FreeCAD as App
    hits = []
    try:
        edges = list(shape_object.Shape.Edges)
    except Exception as error:  # noqa: BLE001 - reported
        return [], [f"shape unavailable: {error}"]
    width, height = view_size(view)
    for index, edge in enumerate(edges):
        try:
            first, last = edge.FirstParameter, edge.LastParameter
        except Exception:  # noqa: BLE001
            continue
        for fraction in (0.15, 0.35, 0.5, 0.65, 0.85):
            try:
                point = edge.valueAt(first + (last - first) * fraction)
                screen = view.getPointOnScreen(App.Vector(point.x, point.y, point.z))
            except Exception:  # noqa: BLE001
                continue
            if not screen:
                continue
            projected = (int(round(screen[0])), int(round(screen[1])))
            found = 0
            for candidate in _neighborhood(projected, height, margin, step, width):
                try:
                    info = view.getObjectInfo(candidate)
                except Exception:  # noqa: BLE001
                    continue
                if not isinstance(info, dict) or info.get("Object") != object_name:
                    continue
                hits.append({"edge": index, "frac": fraction,
                             "projected": list(projected), "pick": list(candidate),
                             "component": str(info.get("Component", ""))})
                found += 1
                if found >= 4 or len(hits) >= limit:
                    break
            if len(hits) >= limit:
                break
        if len(hits) >= limit:
            break
    if hits:
        return hits, f"{len(hits)} validated projected pixels"
    return [], ["no projected sample validated"]


def choose_edge_pixel(seeds, view, object_name, widget, view_pixel, to_local, to_coin,
                      radius=3):
    """Choose a REAL hit pixel whose QtTest round-trip is exact and still resolves.

    Every candidate is revalidated with ``getObjectInfo``; the round trip uses the same
    inverse contract as the native filter (``to_local``/``to_coin``), so an injected Qt
    point maps back to the intended Coin pixel instead of drifting.  Candidates are
    ranked by how many of their eight immediate neighbours also resolve to the object (a
    verified interior margin, so an isolated ray-pick boundary vertex is avoided), then by
    an ``Edge*`` component, then by closeness to the projected sample.  The component may
    be a vertex when the thin stroke never reports ``Edge*`` - the resolver maps any
    component of the registered object to that whole entity.  No native tolerance is
    changed, no picker is substituted and no pixel is invented.
    """
    tile = (widget.width(), widget.height())

    def component_of(coin):
        try:
            info = view.getObjectInfo((int(coin[0]), int(coin[1])))
        except Exception:  # noqa: BLE001 - not a guaranteed hit
            return None
        if isinstance(info, dict) and info.get("Object") == object_name:
            return str(info.get("Component", ""))
        return None

    def neighbor_count(coin):
        return sum(1 for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                   if (dx or dy) and component_of((coin[0] + dx, coin[1] + dy)))

    def exact_local(coin):
        if to_local is None or to_coin is None:
            return None
        local = to_local(coin, tile, view_pixel)
        if local is None:
            return None
        try:
            back = tuple(to_coin(local, tile, view_pixel))
        except Exception:  # noqa: BLE001
            return None
        return local if back == (int(coin[0]), int(coin[1])) else None

    ordered = {}
    for seed in seeds:
        seed = (int(seed[0]), int(seed[1]))
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                coin = (seed[0] + dx, seed[1] + dy)
                local = exact_local(coin)
                if local is None:
                    continue
                component = component_of(coin)
                if component is None:
                    continue
                ordered.setdefault(coin, {"coin": list(coin), "local": list(local),
                                          "component": component, "seed": list(seed),
                                          "neighbors": neighbor_count(coin)})
    if not ordered:
        return None, None, {"reason": "no round-trip exact hit pixel",
                            "seeds": [[int(s[0]), int(s[1])] for s in seeds[:16]]}
    candidates = list(ordered.values())
    on_a_stroke = [item for item in candidates if item["neighbors"] >= 1]
    ranked = sorted(on_a_stroke or candidates,
                    key=lambda item: (-item["neighbors"],
                                      0 if item["component"].startswith("Edge") else 1,
                                      (item["coin"][0] - item["seed"][0]) ** 2
                                      + (item["coin"][1] - item["seed"][1]) ** 2))
    best = ranked[0]
    return (tuple(best["coin"]), tuple(best["local"]),
            {"chosen": best, "candidates": len(ranked),
             "stroke_candidates": len(on_a_stroke)})




def find_object_position(view, object_name, max_calls=4000, component_prefix=None,
                         margin=2, coarse=True):
    """Scan the real 3D view for an interior pixel whose pick resolves to ``object_name``.

    The scan is bounded (``max_calls`` ray picks, centre-outward) so a probe timeout can
    never be blamed on an unbounded grid; a miss is reported instead of substituted.
    ``component_prefix`` requires the picked Component to start with it (e.g. ``Face``),
    and every pixel within ``margin`` physical pixels must resolve to the same object and
    Component, so the returned point is strictly interior to the patch.  ``margin`` stays
    small because the registered boundary/interface display objects are drawn over the
    patches, so a large window would land on those instead of the face.  ``coarse=False``
    uses a finer grid for thin polylines (coupling edges).
    """
    width, height = view_size(view)
    step = max(6, min(width, height) // 56) if coarse else max(3, min(width, height) // 180)
    centre = (width // 2, height // 2)
    points = [(x, y) for y in range(step, height, step) for x in range(step, width, step)]
    points.sort(key=lambda point: (point[0] - centre[0]) ** 2 + (point[1] - centre[1]) ** 2)

    def interior(x, y):
        for dx in (-margin, 0, margin):
            for dy in (-margin, 0, margin):
                try:
                    info = view.getObjectInfo((x + dx, y + dy))
                except Exception:  # noqa: BLE001 - not a guaranteed interior pixel
                    return False
                if not isinstance(info, dict) or info.get("Object") != object_name:
                    return False
                if component_prefix is not None and not str(
                        info.get("Component", "")).startswith(component_prefix):
                    return False
        return True

    object_hits = 0
    component_hits = 0
    components = {}
    interior_failures = 0
    for x, y in points[:max_calls]:
        try:
            info = view.getObjectInfo((x, y))
        except Exception as error:  # noqa: BLE001 - reported as an unresolved pick
            return None, f"getObjectInfo failed: {error}"
        if not isinstance(info, dict) or info.get("Object") != object_name:
            continue
        object_hits += 1
        component = str(info.get("Component", ""))
        components[component] = components.get(component, 0) + 1
        if component_prefix is not None and not component.startswith(component_prefix):
            continue
        component_hits += 1
        if not interior(x, y):
            interior_failures += 1
            continue
        return (x, y), (f"({x},{y}) component={component} interior(+-{margin}) "
                        f"of {len(points[:max_calls])} picks")
    centre_info = None
    try:
        centre_info = view.getObjectInfo(centre)
    except Exception as error:  # noqa: BLE001 - diagnostic only
        centre_info = f"{type(error).__name__}: {error}"
    return None, (f"no interior pixel resolved to {object_name}"
                  + (f" with Component {component_prefix}*" if component_prefix else "")
                  + f" in {min(len(points), max_calls)} picks"
                  + f"; object_hits={object_hits} component_hits={component_hits}"
                  + f" interior_failures={interior_failures} components={components}"
                  + f" centre={centre_info}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=PHASES, required=True)
    parser.add_argument("--plugin", required=True)
    parser.add_argument("--user-dir", required=True)
    parser.add_argument("--document", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    return run_child(args)


if __name__ == "__main__":
    raise SystemExit(main())
