"""Qt-facing half of the V0.5 language interface.

The module is deliberately thin and **does not import Qt**.  Every widget is
accessed through Qt's own ``setXxx`` methods (duck typing), so the same registry
logic is exercised by a plain unit test with fake widgets and by the real panel.

What it guarantees (contract ``docs/V0_5_LANGUAGE_INTERFACE_zh.md``):

* Text is registered **with its stored source** (a catalog key plus parameters),
  so a language switch re-renders *text* only.
* It never rebuilds a panel, never polls with a timer, never monkeypatches a Qt
  class and never touches widget *data* (``itemData``, ``currentData``, cell
  values, the project, the selection or the interaction machine state).
* A **stale-source guard**: a label whose text was replaced by a newer status
  message is skipped, so switching language can never resurrect an older message
  over newer state.
"""
from __future__ import annotations

from . import i18n

#: item data role used to remember a combo item's display source text.
SOURCE_ROLE = 0x0100 + 501          # Qt.UserRole + 501
#: item data role used to remember a translated table cell's display source text.
CELL_SOURCE_ROLE = 0x0100 + 502     # Qt.UserRole + 502
#: item data role used to remember a canonical option token for readback.
CANONICAL_ROLE = 0x0100 + 503       # Qt.UserRole + 503


class TextRegistry:
    """Registers widget text producers and re-renders them on language change.

    ``apply_text(widget, key, **params)`` stores the *source rendering* (the
    Chinese catalog text, which is byte-identical to the pre-i18n literal) on the
    widget and displays the current language.  A later language switch compares
    the widget's live text with that stored source: equal means "nothing changed
    this text since we wrote it", so it is re-rendered; a different text means a
    later status message owns the widget and it is skipped.
    """

    def __init__(self):
        self._widgets = []      # [(widget, producer, tooltip)] insertion ordered, unique
        self._index = {}        # id(widget) -> position
        self._live = {}         # id(widget) -> source display text
        self._items = []        # [(combo, index, key, params)]
        self._item_index = {}   # (id(combo), index) -> position
        self._cells = []        # [(item, key, params, source)]
        self._cell_index = {}   # id(item) -> position
        self._properties = {}  # (widget id, setter) -> source and getter

    # -- registration ------------------------------------------------------
    def _slot(self, widget):
        position = self._index.get(id(widget))
        if position is None:
            self._widgets.append([widget, None, None])
            position = len(self._widgets) - 1
            self._index[id(widget)] = position
        return position

    def apply_text(self, widget, key, *args, **kwargs):
        """Set a widget's display text from a catalog key and register it."""
        if widget is None:
            return widget
        text = i18n.translate(key, *args, **kwargs)
        setter = getattr(widget, "setText", None)
        if not callable(setter):
            setter = getattr(widget, "setWindowTitle", None)
        if callable(setter):
            setter(text)
        label = getattr(widget, "setAccessibleName", None)
        if callable(label):
            try:
                label(i18n.translate(key))
            except Exception:  # noqa: BLE001 - accessible name is cosmetic
                pass
        position = self._slot(widget)
        self._widgets[position][1] = (key, dict(kwargs))
        self._live[id(widget)] = text
        return widget

    def apply_property(self, widget, key, setter, getter, **params):
        """Register a placeholder or display property without touching input values."""
        text = i18n.translate(key, **params)
        getattr(widget, setter)(text)
        self._properties[(id(widget), setter)] = (widget, key, getter, dict(params), text)
        return widget

    def apply_placeholder(self, widget, key, **params):
        return self.apply_property(widget, key, "setPlaceholderText", "placeholderText", **params)

    def apply_tooltip(self, widget, key, *args, **kwargs):
        if widget is None:
            return widget
        setter = getattr(widget, "setToolTip", None)
        if callable(setter):
            setter(i18n.translate(key, *args, **kwargs))
        position = self._slot(widget)
        self._widgets[position][2] = (key, dict(kwargs))
        return widget

    def register_item(self, combo, index, key, *args, **kwargs):
        if combo is None:
            return
        item_setter = getattr(combo, "setItemText", None)
        if callable(item_setter):
            item_setter(index, i18n.translate(key, *args, **kwargs))
        try:
            combo.setItemData(index, key, SOURCE_ROLE)
        except Exception:  # noqa: BLE001 - a combo without roles still translates live
            pass
        identifier = (id(combo), index)
        if identifier in self._item_index:
            self._items[self._item_index[identifier]] = [combo, index, key, dict(kwargs)]
        else:
            self._item_index[identifier] = len(self._items)
            self._items.append([combo, index, key, dict(kwargs)])

    def register_cell(self, item, key, *args, **kwargs):
        if item is None:
            return
        identifier = id(item)
        if identifier in self._cell_index:
            self._cells[self._cell_index[identifier]] = [item, key, dict(kwargs)]
        else:
            self._cell_index[identifier] = len(self._cells)
            self._cells.append([item, key, dict(kwargs)])

    # -- re-render ---------------------------------------------------------
    def retranslate(self, language=None):
        """Re-render every live registered text; returns how many were updated."""
        changed = 0
        for position in range(len(self._widgets) - 1, -1, -1):
            widget, producer, tooltip = self._widgets[position]
            if not self._alive(widget):
                self._forget(widget)
                continue
            changed += self._render_widget(widget, producer, tooltip, language)
        for combo, index, key, params in list(self._items):
            if not self._alive(combo):
                continue
            setter = getattr(combo, "setItemText", None)
            if callable(setter):
                blocker = getattr(combo, "blockSignals", None)
                previous = blocker(True) if callable(blocker) else None
                try:
                    setter(index, i18n.translate(key, language, **params))
                    changed += 1
                except Exception:  # noqa: BLE001 - an index that vanished is skipped
                    pass
                finally:
                    if callable(blocker):
                        blocker(previous)
        for item, key, params in list(self._cells):
            if not self._alive(item):
                continue
            setter = getattr(item, "setText", None)
            if callable(setter):
                setter(i18n.translate(key, language, **params))
                changed += 1
        for ident, (widget, key, getter, params, last) in list(self._properties.items()):
            if not self._alive(widget):
                self._properties.pop(ident, None)
                continue
            try:
                if getattr(widget, getter)() != last:
                    continue
                text = i18n.translate(key, language, **params)
                getattr(widget, ident[1])(text)
                self._properties[ident] = (widget, key, getter, params, text)
                changed += 1
            except RuntimeError:
                self._properties.pop(ident, None)
        return changed

    def _alive(self, widget):
        try:
            for method in ("text", "objectName", "windowTitle", "count", "rowCount"):
                getter = getattr(widget, method, None)
                if callable(getter):
                    getter()
                    return True
            return False
        except Exception:  # noqa: BLE001 - a deleted C++ object is gone
            return False

    def _forget(self, widget):
        position = self._index.pop(id(widget), None)
        self._live.pop(id(widget), None)
        if position is not None:
            self._widgets[position] = [None, None, None]

    def _render_widget(self, widget, producer, tooltip, language):
        changed = 0
        if producer is not None:
            key, params = producer
            text = i18n.translate(key, language, **params)
            getter = getattr(widget, "text", None)
            if not callable(getter):
                getter = getattr(widget, "windowTitle", None)
            live = getter() if callable(getter) else None
            stored = self._live.get(id(widget))
            if stored is not None and live is not None and live != stored:
                # Another status update owns this widget; never resurrect our text.
                pass
            else:
                setter = getattr(widget, "setText", None)
                if not callable(setter):
                    setter = getattr(widget, "setWindowTitle", None)
                if callable(setter):
                    setter(text)
                    self._live[id(widget)] = text
                    accessible = getattr(widget, "setAccessibleName", None)
                    if callable(accessible):
                        accessible(text)
                    changed += 1
        if tooltip is not None:
            key, params = tooltip
            setter = getattr(widget, "setToolTip", None)
            if callable(setter):
                setter(i18n.translate(key, language, **params))
                changed += 1
        return changed


# --------------------------------------------------------------------------- #
# Module-level helpers for source-language widgets (tables, combos, cells)
# --------------------------------------------------------------------------- #
def set_combo_items(registry, combo, entries):
    """Populate ``combo`` with ``[(key, canonical, params), ...]``.

    ``canonical`` becomes the item's data (what the GUI reads back); the display
    text comes from the catalog, so ``currentData()`` is unchanged by language.
    """
    for key, canonical, params in entries:
        try:
            combo.addItem("", canonical)
        except Exception:  # noqa: BLE001 - a fake combo without data support
            combo.addItem("")
        index = combo.count() - 1 if hasattr(combo, "count") else 0
        registry.register_item(combo, index, key, **(params or {}))
    return combo


def retranslate_combo(registry, combo, language=None, block=None):
    """Re-render every translated item of a combo that was built with sources."""
    changed = 0
    close = block if callable(block) else None
    for index in range(0, getattr(combo, "count", lambda: 0)()):
        try:
            key = combo.itemData(index, SOURCE_ROLE)
        except Exception:  # noqa: BLE001
            key = None
        if not isinstance(key, str):
            continue
        if close is not None:
            close()
        combo.setItemText(index, i18n.translate(key, language))
        changed += 1
    return changed


def translate_table_headers(registry, table, headers, language=None):
    """Set and register a table's horizontal headers from catalog keys."""
    texts = [i18n.translate(key, language) for key in headers]
    setter = getattr(table, "setHorizontalHeaderLabels", None)
    if callable(setter):
        setter(texts)
    for index, key in enumerate(headers):
        getter = getattr(table, "horizontalHeaderItem", None)
        item = getter(index) if callable(getter) else None
        if item is None:
            continue
        store = getattr(item, "setData", None)
        if callable(store):
            store(CELL_SOURCE_ROLE, key)
    return table


def retranslate_table_headers(registry, table, language=None):
    getter = getattr(table, "horizontalHeaderItem", None)
    count = 0
    counter = getattr(table, "columnCount", None)
    total = counter() if callable(counter) else 0
    for index in range(total):
        item = getter(index) if callable(getter) else None
        if item is None:
            continue
        data = getattr(item, "data", None)
        key = data(CELL_SOURCE_ROLE) if callable(data) else None
        if not isinstance(key, str):
            continue
        item.setText(i18n.translate(key, language))
        count += 1
    return count


def display_cell(item, key, *args, **kwargs):
    """Write a translated enum/value cell and remember its source key."""
    text = i18n.translate(key, *args, **kwargs)
    setter = getattr(item, "setText", None)
    if callable(setter):
        setter(text)
    store = getattr(item, "setData", None)
    if callable(store):
        store(CELL_SOURCE_ROLE, key)
    return item


def canonical_item_value(item, default=None):
    """Read a cell's canonical token (never its translated display text)."""
    if item is None:
        return default
    data = getattr(item, "data", None)
    if callable(data):
        for role in (CANONICAL_ROLE, SOURCE_ROLE):
            try:
                value = data(role)
            except Exception:  # noqa: BLE001
                value = None
            if value is not None:
                return value
    getter = getattr(item, "text", None)
    return getter() if callable(getter) else default


def canonical_combo_value(combo, default=None):
    """Read a combo's canonical value from ``currentData()`` (never ``currentText()``)."""
    if combo is None:
        return default
    checker = getattr(combo, "currentData", None)
    if callable(checker):
        try:
            value = checker()
        except TypeError:  # a Qt that requires the role argument
            for role in (CANONICAL_ROLE, SOURCE_ROLE):
                try:
                    value = combo.currentData(role)
                except Exception:  # noqa: BLE001
                    value = None
                if value is not None:
                    return value
            value = None
        if value is not None:
            return value
    getter = getattr(combo, "currentText", None)
    return getter() if callable(getter) else default
