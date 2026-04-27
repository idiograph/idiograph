# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QScrollArea, QFrame,
)
from PySide6.QtCore import Qt, QPointF

from idiograph.apps.color_designer.nodes.base_node import BaseNode, NODE_WIDTH
from idiograph.apps.color_designer.token_store import TokenStore
from idiograph.core.models import Node

# ── layout ────────────────────────────────────────────────────────────────────
BODY_H = 220   # fixed scrollable role list

# ── colours ───────────────────────────────────────────────────────────────────
_BG_BODY = "#2e2e3a"
_BG_ROWS = "#252530"

_SCROLL_STYLE = """
    QScrollArea        { background-color: #252530; border: none; }
    QScrollArea > QWidget > QWidget { background-color: #252530; }
    QScrollBar:vertical {
        background: #1a1a1f; width: 6px; border: none; margin: 0;
    }
    QScrollBar::handle:vertical {
        background: #3a3a4a; border-radius: 3px; min-height: 16px;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


# ── role row ──────────────────────────────────────────────────────────────────

class _RoleRow(QWidget):
    """One role: colour chip + role name + port indicator dot."""

    H = 22

    def __init__(self, role: str, hex_value: str):
        super().__init__()
        self.setFixedHeight(self.H)
        self.setStyleSheet(f"background-color: {_BG_ROWS};")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 8, 2)
        layout.setSpacing(6)

        chip = QLabel()
        chip.setFixedSize(12, 12)
        chip.setStyleSheet(
            f"background-color: {hex_value}; border: 1px solid #555568;"
            " border-radius: 2px;"
        )
        layout.addWidget(chip)

        name = QLabel(role)
        name.setStyleSheet(
            "color: #aaaacc; font-family: monospace; font-size: 8pt;"
        )
        layout.addWidget(name, 1)

        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(
            f"background-color: {hex_value}; border-radius: 4px;"
            " border: 1px solid #7eb8f7;"
        )
        layout.addWidget(dot)


# ── role list body ────────────────────────────────────────────────────────────

class _RoleListBody(QWidget):
    """Scrollable list of every role in the loaded token file."""

    def __init__(self, node: "SchemaNode"):
        super().__init__()
        self.setFixedSize(NODE_WIDTH, BODY_H)
        self.setStyleSheet(f"background-color: {_BG_BODY};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(_SCROLL_STYLE)

        container = QWidget()
        container.setStyleSheet(f"background-color: {_BG_ROWS};")
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 2, 0, 2)
        vbox.setSpacing(1)
        vbox.setAlignment(Qt.AlignTop)

        for role in node.roles:
            hex_val = node.values.get(role, "#888899")
            vbox.addWidget(_RoleRow(role, hex_val))

        scroll.setWidget(container)
        outer.addWidget(scroll)


# ── node ──────────────────────────────────────────────────────────────────────

class SchemaNode(BaseNode):
    """
    Schema node — token role registry.
    Loads roles from a token JSON file (flat dot-notation).

    Currently exposes a single ganged output port of type token_dict.
    Per-role individual output ports (and the All / Conn / Gang strip
    that switches between them) are deferred until the scrollable role
    list and the port system can be reconciled.

    No input ports.
    """

    def __init__(self, token_path: Path, pos: QPointF = QPointF(0.0, 0.0)):
        super().__init__(
            "Schema",
            pos,
            view_labels=[],          # no body view switching
            port_mode_labels=[],     # no port-mode strip — single output for now
        )
        self.token_path = token_path
        tokens = TokenStore(token_path).tokens()
        self.roles: list[str] = list(tokens.keys())
        self.values: dict[str, str] = tokens

        self.setBodyWidget(_RoleListBody(self), BODY_H)
        self.add_output_port("token_dict")

    @property
    def field_count(self) -> int:
        """Number of token roles currently loaded — consumed by Color Array
        and Array Assign for cardinality alignment."""
        return len(self.roles)

    def to_idiograph_node(self) -> Node:
        return Node(
            id=self.node_id,
            type="schema",
            params={"token_file": str(self.token_path)},
        )
