# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import math

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLineEdit, QLabel, QColorDialog,
)
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor

from idiograph.apps.color_designer.nodes.base_node import BaseNode, NODE_WIDTH
from idiograph.core.models import Node

# ── view labels ───────────────────────────────────────────────────────────────
_VIEWS = ["Cmp", "List", "Grid"]
_DEFAULT_VIEW = 1  # List

# ── colours ───────────────────────────────────────────────────────────────────
_BG_BODY = "#2e2e3a"
_BG_ROWS = "#252530"

_FIELD_STYLE = (
    "background-color: #24242c; color: #ccccdd; border: 1px solid #3a3a4a;"
    " border-radius: 3px; padding: 1px 4px; font-family: monospace;"
)
_ADD_BTN_STYLE = """
    QPushButton {
        background-color: #252530; color: #666677;
        border: 1px dashed #3a3a4a; border-radius: 3px;
        padding: 2px; font-size: 9pt;
    }
    QPushButton:hover { background-color: #2e2e3a; color: #ccccdd; }
"""

# ── grid constants ────────────────────────────────────────────────────────────
_GRID_COLS = 4
_GRID_CELL = 44   # px square
_GRID_GAP = 2


def _swatch_css(hex_value: str) -> str:
    return (
        f"background-color: {hex_value}; border: 1px solid #555568;"
        " border-radius: 2px;"
    )


# ── row widget (List view only) ───────────────────────────────────────────────

class ArrayRow(QWidget):
    """Single colour entry: swatch chip | hex field | label field."""

    def __init__(self, hex_value: str = "#ccccdd", label: str = "", on_change=None):
        super().__init__()
        self.hex_value = hex_value
        self.label_text = label
        self._on_change = on_change
        self.setFixedHeight(26)
        self.setStyleSheet(f"background-color: {_BG_ROWS};")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        self.swatch = QPushButton()
        self.swatch.setFixedSize(20, 20)
        self.swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.swatch.clicked.connect(self._pick)
        layout.addWidget(self.swatch)

        self.hex_edit = QLineEdit(hex_value)
        self.hex_edit.setFixedWidth(66)
        self.hex_edit.setMaxLength(7)
        self.hex_edit.setStyleSheet(_FIELD_STYLE)
        self.hex_edit.editingFinished.connect(self._on_hex)
        layout.addWidget(self.hex_edit)

        self.label_edit = QLineEdit(label)
        self.label_edit.setPlaceholderText("label")
        self.label_edit.setStyleSheet(_FIELD_STYLE)
        self.label_edit.editingFinished.connect(self._on_label)
        layout.addWidget(self.label_edit)

        self._refresh_swatch()

    def _refresh_swatch(self) -> None:
        self.swatch.setStyleSheet(_swatch_css(self.hex_value))

    def _pick(self) -> None:
        # parent=None + DontUseNativeDialog: native dialog renders blank when
        # parented to a QGraphicsProxyWidget on Windows.
        color = QColorDialog.getColor(
            QColor(self.hex_value),
            None,
            "Pick colour",
            QColorDialog.ColorDialogOption.DontUseNativeDialog,
        )
        if color.isValid():
            self.hex_value = color.name()
            self.hex_edit.setText(self.hex_value)
            self._refresh_swatch()
            self._notify()

    def _on_hex(self) -> None:
        raw = self.hex_edit.text().strip()
        value = raw if raw.startswith("#") else f"#{raw}"
        if QColor(value).isValid():
            self.hex_value = value
            self.hex_edit.setText(value)
            self._refresh_swatch()
            self._notify()
        else:
            self.hex_edit.setText(self.hex_value)

    def _on_label(self) -> None:
        self.label_text = self.label_edit.text()
        self._notify()

    def _notify(self) -> None:
        if self._on_change:
            self._on_change()

    def data(self) -> tuple[str, str]:
        return (self.hex_value, self.label_text)


# ── compact view ──────────────────────────────────────────────────────────────

COMPACT_HEIGHT = 50  # top(6)+name(18)+sp(4)+chips(16)+bottom(6)


class _CompactBody(QWidget):
    """Summary: array name + colour chips + count. Fixed height, no editing."""

    def __init__(self, node: "ArrayNode"):
        super().__init__()
        self.setFixedSize(NODE_WIDTH, COMPACT_HEIGHT)
        self.setStyleSheet(f"background-color: {_BG_BODY};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(4)

        name = QLabel(node.array_label or "—")
        name.setStyleSheet("color: #888899; font-family: monospace; font-size: 9pt;")
        layout.addWidget(name)

        row_w = QWidget()
        row_w.setStyleSheet(f"background-color: {_BG_BODY};")
        row_layout = QHBoxLayout(row_w)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(3)

        for hex_val, _ in node.rows[:8]:
            chip = QLabel()
            chip.setFixedSize(14, 14)
            chip.setStyleSheet(
                f"background-color: {hex_val}; border-radius: 2px;"
            )
            row_layout.addWidget(chip)

        n = len(node.rows)
        count = QLabel(f"{n} colour{'s' if n != 1 else ''}")
        count.setStyleSheet(
            "color: #555568; font-family: monospace; font-size: 8pt;"
        )
        row_layout.addSpacing(4)
        row_layout.addWidget(count)
        row_layout.addStretch()

        layout.addWidget(row_w)


# ── list view ─────────────────────────────────────────────────────────────────

class _ListBody(QWidget):
    """
    Stacked editable rows with array name field and + New Item button.
    No scroll — node body expands to show all rows.
    """

    _STATIC_H = 70   # height when n_rows == 0  (margins + name + btn + spacings)
    _ROW_H = 28      # height added per row (26px row + 2px spacing)

    @staticmethod
    def calc_height(n_rows: int) -> int:
        return _ListBody._STATIC_H + n_rows * _ListBody._ROW_H

    def __init__(self, node: "ArrayNode"):
        super().__init__()
        self._node = node
        self.setFixedWidth(NODE_WIDTH)
        self.setStyleSheet(f"background-color: {_BG_BODY};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        self.name_edit = QLineEdit(node.array_label)
        self.name_edit.setPlaceholderText("array name")
        self.name_edit.setFixedHeight(24)
        self.name_edit.setStyleSheet(_FIELD_STYLE)
        self.name_edit.editingFinished.connect(self._on_name)
        outer.addWidget(self.name_edit)

        # Rows container — no scroll, grows with content
        self._rows_widget = QWidget()
        self._rows_widget.setStyleSheet(f"background-color: {_BG_ROWS};")
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setContentsMargins(2, 2, 2, 2)
        self._rows_layout.setSpacing(2)
        self._rows_layout.setAlignment(Qt.AlignTop)
        outer.addWidget(self._rows_widget)

        # Bottom button row: Match Schema | + Item
        btn_row = QWidget()
        btn_row.setFixedHeight(24)
        btn_row.setStyleSheet(f"background-color: {_BG_BODY};")
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(4)

        match_btn = QPushButton("Match Schema")
        match_btn.setFixedHeight(24)
        match_btn.setStyleSheet(_ADD_BTN_STYLE)
        match_btn.clicked.connect(self._on_match_schema)
        btn_layout.addWidget(match_btn)

        add_btn = QPushButton("+ Item")
        add_btn.setFixedHeight(24)
        add_btn.setStyleSheet(_ADD_BTN_STYLE)
        add_btn.clicked.connect(self._add_row)
        btn_layout.addWidget(add_btn)

        outer.addWidget(btn_row)

        for hex_val, lbl in node.rows:
            self._append_row(hex_val, lbl, notify=False)

        self._apply_height()

    def _on_match_schema(self) -> None:
        """Resize node.rows to match the first SchemaNode in the scene."""
        scene = self._node.scene()
        if scene is None:
            return
        # Local import to avoid a circular module dependency at file load time.
        from idiograph.apps.color_designer.nodes.schema_node import SchemaNode
        schema = next(
            (item for item in scene.items() if isinstance(item, SchemaNode)),
            None,
        )
        if schema is None:
            return
        target = schema.field_count
        current = len(self._node.rows)
        if current == target:
            return
        if current < target:
            for _ in range(target - current):
                self._node.rows.append(("#ccccdd", ""))
        else:
            self._node.rows = self._node.rows[:target]
        # Rebuild the body so the new row count is reflected in the UI…
        self._node._switch_view(self._node._active_view)
        # …and tell wired downstream nodes (e.g. ArrayAssign) to refresh.
        self._node.notify_rows_changed()

    def _append_row(self, hex_value: str, label: str, notify: bool = True) -> None:
        row = ArrayRow(hex_value, label, self._sync_rows)
        self._rows_layout.addWidget(row)
        if notify:
            self._apply_height()

    def _add_row(self) -> None:
        self._node.rows.append(("#ccccdd", ""))
        self._append_row("#ccccdd", "", notify=True)
        self._node.notify_rows_changed()

    def _apply_height(self) -> None:
        n = self._rows_layout.count()
        h = self.calc_height(n)
        self.setFixedHeight(h)
        self._node.resizeBody(h)

    def _on_name(self) -> None:
        self._node.array_label = self.name_edit.text()

    def _sync_rows(self) -> None:
        rows = []
        for i in range(self._rows_layout.count()):
            item = self._rows_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, ArrayRow):
                rows.append(w.data())
        self._node.rows = rows
        # Cell-level edits (hex / label) propagate to ArrayAssign body chips.
        self._node.notify_rows_changed()


# ── grid view ─────────────────────────────────────────────────────────────────

class _GridBody(QWidget):
    """
    Read-only swatch matrix. No edit controls, no scroll — node expands.
    COLS = 4, CELL = 44px square.
    """

    _STATIC_H = 38   # top(6) + name(20) + sp(6) + bottom(6)

    @staticmethod
    def calc_height(n: int) -> int:
        grid_rows = max(1, math.ceil(n / _GRID_COLS)) if n > 0 else 1
        return _GridBody._STATIC_H + grid_rows * _GRID_CELL + (grid_rows - 1) * _GRID_GAP

    def __init__(self, node: "ArrayNode"):
        super().__init__()
        n = len(node.rows)
        h = self.calc_height(n)
        self.setFixedSize(NODE_WIDTH, h)
        self.setStyleSheet(f"background-color: {_BG_BODY};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(6)

        name = QLabel(node.array_label or "—")
        name.setFixedHeight(20)
        name.setStyleSheet("color: #888899; font-family: monospace; font-size: 9pt;")
        outer.addWidget(name)

        grid_w = QWidget()
        grid_w.setStyleSheet(f"background-color: {_BG_BODY};")
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(_GRID_GAP)

        for i, (hex_val, lbl) in enumerate(node.rows):
            cell = QLabel()
            cell.setFixedSize(_GRID_CELL, _GRID_CELL)
            cell.setToolTip(f"{lbl}\n{hex_val}" if lbl else hex_val)
            cell.setStyleSheet(
                f"background-color: {hex_val}; border: 1px solid #3a3a4a;"
                " border-radius: 3px;"
            )
            grid.addWidget(cell, i // _GRID_COLS, i % _GRID_COLS)

        outer.addWidget(grid_w)
        outer.addStretch()


# ── node ──────────────────────────────────────────────────────────────────────

class ArrayNode(BaseNode):
    """
    Color Array node — ordered collection of internal colour entries.
    Rows are self-contained; no external port connections or input ports.
    Data: array_label (str), rows (list of (hex, label) pairs).
    Ports: one output (color array).
    Views: Compact | List | Grid.
    """

    def __init__(
        self,
        array_label: str = "my palette",
        rows: list[tuple[str, str]] | None = None,
        pos: QPointF = QPointF(0.0, 0.0),
    ):
        super().__init__(
            "Color Array",
            pos,
            view_labels=_VIEWS,
        )
        self.array_label = array_label
        self.rows: list[tuple[str, str]] = rows if rows is not None else [
            ("#7eb8f7", "primary"),
            ("#2e2e3a", "surface"),
        ]

        self._active_view = _DEFAULT_VIEW
        self._switch_view(_DEFAULT_VIEW)
        self.add_output_port("color_array")

    # ── view switching ────────────────────────────────────────────────────────

    def _on_view_switch(self, idx: int) -> None:
        self._switch_view(idx)

    def _switch_view(self, idx: int) -> None:
        if idx == 0:
            widget = _CompactBody(self)
            height = COMPACT_HEIGHT
        elif idx == 1:
            widget = _ListBody(self)
            height = _ListBody.calc_height(len(self.rows))
        else:
            widget = _GridBody(self)
            height = _GridBody.calc_height(len(self.rows))
        self.setBodyWidget(widget, height)

    # ── notification ─────────────────────────────────────────────────────────

    def notify_rows_changed(self) -> None:
        """Tell every node wired to this array's output that its row set
        has changed (added, trimmed, or just edited). Reuses the existing
        on_connections_changed hook because the consumer's response is the
        same: re-read this node's state and refresh its derived view."""
        for port in self.output_ports():
            for wire in port.wires:
                target = wire.target_port
                if target is None:
                    continue
                tnode = target.parentItem()
                if tnode is not None and hasattr(tnode, "on_connections_changed"):
                    tnode.on_connections_changed()

    # ── data ──────────────────────────────────────────────────────────────────

    def colors(self) -> list[tuple[str, str]]:
        """Return current (hex, label) pairs for downstream nodes."""
        return list(self.rows)

    def to_idiograph_node(self) -> Node:
        return Node(
            id=self.node_id,
            type="color_array",
            params={"colors": [{"label": lbl, "hex": h} for h, lbl in self.rows]},
        )
