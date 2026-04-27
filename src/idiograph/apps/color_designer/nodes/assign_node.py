# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QLabel, QColorDialog,
)
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor

from idiograph.apps.color_designer.nodes.base_node import BaseNode, NODE_WIDTH, HEADER_HEIGHT
from idiograph.apps.color_designer.token_store import TokenStore
from idiograph.core.models import Node

# ── layout ────────────────────────────────────────────────────────────────────
ASSIGN_BODY_H = 76  # margins + dropdown(26) + spacing(4) + color row(28) + bottom

# ── colours / styles ──────────────────────────────────────────────────────────
_BG_BODY = "#2e2e3a"
_FIELD_STYLE = (
    "background-color: #24242c; color: #ccccdd; border: 1px solid #3a3a4a;"
    " border-radius: 3px; padding: 2px 6px; font-family: monospace; font-size: 8pt;"
)
_COMBO_STYLE = """
    QComboBox {
        background-color: #24242c; color: #ccccdd;
        border: 1px solid #3a3a4a; border-radius: 3px;
        padding: 2px 6px; font-family: monospace; font-size: 8pt;
    }
    QComboBox::drop-down { border: none; width: 16px; }
    QComboBox::down-arrow { image: none; }
    QComboBox QAbstractItemView {
        background-color: #24242c; color: #ccccdd;
        selection-background-color: #3a3a4a;
        font-family: monospace; font-size: 8pt;
        border: 1px solid #3a3a4a;
    }
"""


def _swatch_css(hex_value: str) -> str:
    return (
        f"background-color: {hex_value}; border: 1px solid #555568;"
        " border-radius: 3px;"
    )


# ── body widget ───────────────────────────────────────────────────────────────

class _AssignBody(QWidget):
    def __init__(self, node: "AssignNode"):
        super().__init__()
        self._node = node
        self.setFixedSize(NODE_WIDTH, ASSIGN_BODY_H)
        self.setStyleSheet(f"background-color: {_BG_BODY};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # Role dropdown — populated from the token file
        self.role_combo = QComboBox()
        self.role_combo.setFixedHeight(24)
        self.role_combo.setStyleSheet(_COMBO_STYLE)
        for role in node.roles:
            self.role_combo.addItem(role)
        if node.role and node.role in node.roles:
            self.role_combo.setCurrentText(node.role)
        elif node.roles:
            node.role = node.roles[0]
            self.role_combo.setCurrentIndex(0)
        self.role_combo.currentTextChanged.connect(self._on_role)
        layout.addWidget(self.role_combo)

        # Color row: clickable swatch + hex label
        row = QWidget()
        row.setStyleSheet(f"background-color: {_BG_BODY};")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        self.swatch = QPushButton()
        self.swatch.setFixedSize(28, 24)
        self.swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.swatch.clicked.connect(self._pick)
        row_layout.addWidget(self.swatch)

        self.color_lbl = QLabel(node.color)
        self.color_lbl.setStyleSheet(
            "color: #aaaacc; font-family: monospace; font-size: 8pt;"
        )
        row_layout.addWidget(self.color_lbl)
        row_layout.addStretch()

        layout.addWidget(row)
        self._refresh_swatch()

    def _refresh_swatch(self) -> None:
        self.swatch.setStyleSheet(_swatch_css(self._node.color))

    def _pick(self) -> None:
        color = QColorDialog.getColor(
            QColor(self._node.color),
            None,
            f"Pick — {self._node.role}",
            QColorDialog.ColorDialogOption.DontUseNativeDialog,
        )
        if color.isValid():
            self._node.color = color.name()
            self.color_lbl.setText(self._node.color)
            self._refresh_swatch()

    def _on_role(self, text: str) -> None:
        self._node.role = text


# ── node ──────────────────────────────────────────────────────────────────────

class AssignNode(BaseNode):
    """
    Assign node — maps a colour to a token role.
    Inputs:  one colour input (from Swatch/Array), one role input (from Schema).
    Output:  one (role, value) pair.
    Body:    role dropdown (loaded from token file) + colour picker.

    The two inputs are visual placeholders in Phase E — actual edge wiring
    arrives in a later phase. The role dropdown is the immediate UI for
    picking the target role, mirroring what an upstream Schema node would
    eventually drive via a connection.
    """

    def __init__(
        self,
        token_path: Path,
        role: str = "",
        color: str = "#7eb8f7",
        pos: QPointF = QPointF(0.0, 0.0),
    ):
        super().__init__(
            "Assign",
            pos,
            view_labels=[],
        )
        self.token_path = token_path
        tokens = TokenStore(token_path).tokens()
        self.roles: list[str] = list(tokens.keys())
        self.role = role
        self.color = color

        self.setBodyWidget(_AssignBody(self), ASSIGN_BODY_H)
        # Two inputs distributed vertically, one output centred.
        upper = HEADER_HEIGHT + 14
        lower = HEADER_HEIGHT + ASSIGN_BODY_H - 14
        self.add_input_port("color", upper)
        self.add_input_port("token_dict", lower)
        self.add_output_port("assignment")

    def assignment(self) -> tuple[str, str]:
        """Return the (role, color) pair this node represents."""
        return (self.role, self.color)

    def to_idiograph_node(self) -> Node:
        return Node(
            id=self.node_id,
            type="assign",
            params={"role": self.role},
        )
