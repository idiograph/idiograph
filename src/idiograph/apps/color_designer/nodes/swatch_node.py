# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel,
    QColorDialog, QSizePolicy,
)
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor

from idiograph.apps.color_designer.nodes.base_node import BaseNode, NODE_WIDTH
from idiograph.core.models import Node

# ── view constants ────────────────────────────────────────────────────────────
_VIEWS = ["Full", "Cmp", "Data"]
_HEIGHTS = [128, 36, 32]  # body height for each view

_BG = "background-color: #2e2e3a;"
_FIELD_STYLE = (
    "background-color: #24242c; color: #ccccdd; border: 1px solid #3a3a4a;"
    " border-radius: 3px; padding: 2px 6px; font-family: monospace;"
)


def _swatch_css(hex_value: str) -> str:
    return (
        f"background-color: {hex_value}; border: 1px solid #555568;"
        " border-radius: 3px;"
    )


# ── body widgets ──────────────────────────────────────────────────────────────

class _FullBody(QWidget):
    """Large swatch + editable label + hex field."""

    def __init__(self, node: "SwatchNode"):
        super().__init__()
        self._node = node
        self.setFixedSize(NODE_WIDTH, _HEIGHTS[0])
        self.setStyleSheet(_BG)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        # Swatch button — click to open picker
        self.swatch = QPushButton()
        self.swatch.setFixedHeight(52)
        self.swatch.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.swatch.clicked.connect(self._pick)
        layout.addWidget(self.swatch)

        # Label field
        self.label_edit = QLineEdit(node.label)
        self.label_edit.setPlaceholderText("label")
        self.label_edit.setStyleSheet(_FIELD_STYLE)
        self.label_edit.editingFinished.connect(self._on_label)
        layout.addWidget(self.label_edit)

        # Hex field
        self.hex_edit = QLineEdit(node.hex_value)
        self.hex_edit.setPlaceholderText("#rrggbb")
        self.hex_edit.setMaxLength(7)
        self.hex_edit.setStyleSheet(_FIELD_STYLE)
        self.hex_edit.editingFinished.connect(self._on_hex)
        layout.addWidget(self.hex_edit)

        self._refresh_swatch()

    def _refresh_swatch(self) -> None:
        self.swatch.setStyleSheet(_swatch_css(self._node.hex_value))

    def _pick(self) -> None:
        # parent=None + DontUseNativeDialog: native dialog renders blank when
        # parented to a QGraphicsProxyWidget on Windows.
        color = QColorDialog.getColor(
            QColor(self._node.hex_value),
            None,
            f"Pick — {self._node.node_type}",
            QColorDialog.ColorDialogOption.DontUseNativeDialog,
        )
        if color.isValid():
            self._node._set_color(color.name())
            self.hex_edit.setText(self._node.hex_value)
            self._refresh_swatch()

    def _on_hex(self) -> None:
        raw = self.hex_edit.text().strip()
        value = raw if raw.startswith("#") else f"#{raw}"
        if QColor(value).isValid():
            self._node._set_color(value)
            self.hex_edit.setText(self._node.hex_value)
            self._refresh_swatch()
        else:
            self.hex_edit.setText(self._node.hex_value)

    def _on_label(self) -> None:
        self._node.label = self.label_edit.text()


class _CompactBody(QWidget):
    """Small colour chip + label on a single row."""

    def __init__(self, node: "SwatchNode"):
        super().__init__()
        self._node = node
        self.setFixedSize(NODE_WIDTH, _HEIGHTS[1])
        self.setStyleSheet(_BG)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        self.chip = QLabel()
        self.chip.setFixedSize(18, 18)
        self.chip.setStyleSheet(_swatch_css(node.hex_value))
        layout.addWidget(self.chip)

        self.name_lbl = QLabel(node.label or node.hex_value)
        self.name_lbl.setStyleSheet("color: #ccccdd; font-family: monospace;")
        layout.addWidget(self.name_lbl)
        layout.addStretch()


class _DataBody(QWidget):
    """Hex value only, monospace."""

    def __init__(self, node: "SwatchNode"):
        super().__init__()
        self._node = node
        self.setFixedSize(NODE_WIDTH, _HEIGHTS[2])
        self.setStyleSheet(_BG)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)

        self.lbl = QLabel(node.hex_value)
        self.lbl.setStyleSheet(
            "color: #ccccdd; font-family: monospace; font-size: 11pt;"
        )
        layout.addWidget(self.lbl)


# ── node ──────────────────────────────────────────────────────────────────────

class SwatchNode(BaseNode):
    """
    Color Swatch node — atomic color input.
    Data: one hex value, one label.
    Ports: one output (color).
    Views: Full / Compact / Data.
    """

    def __init__(
        self,
        hex_value: str = "#7eb8f7",
        label: str = "untitled",
        pos: QPointF = QPointF(0.0, 0.0),
    ):
        super().__init__(
            "Color Swatch",
            pos,
            view_labels=_VIEWS,
        )
        self.hex_value = hex_value
        self.label = label
        self._switch_view(0)
        self.add_output_port("color")

    # ── data ──────────────────────────────────────────────────────────────────

    def _set_color(self, hex_value: str) -> None:
        self.hex_value = hex_value

    def to_idiograph_node(self) -> Node:
        return Node(
            id=self.node_id,
            type="color_swatch",
            params={"hex": self.hex_value, "label": self.label},
        )

    # ── view switching ────────────────────────────────────────────────────────

    def _on_view_switch(self, idx: int) -> None:
        self._switch_view(idx)

    def _switch_view(self, idx: int) -> None:
        if idx == 0:
            widget = _FullBody(self)
        elif idx == 1:
            widget = _CompactBody(self)
        else:
            widget = _DataBody(self)
        self.setBodyWidget(widget, _HEIGHTS[idx])
