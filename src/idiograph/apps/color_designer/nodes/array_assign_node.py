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
from PySide6.QtCore import Qt, QPointF, QTimer

from idiograph.apps.color_designer.nodes.base_node import BaseNode, NODE_WIDTH, HEADER_HEIGHT
from idiograph.apps.color_designer.nodes.array_node import ArrayNode
from idiograph.apps.color_designer.token_store import TokenStore
from idiograph.core.models import Node

# ── layout ────────────────────────────────────────────────────────────────────
ARRAY_ASSIGN_BODY_H = 220
_PORT_MARGIN = 14  # vertical margin from body edges for the input ports

# ── colours / styles ──────────────────────────────────────────────────────────
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

# Status colours: aligned (green), warning (yellow), unconnected (muted)
_STATUS_OK = "#4ab88a"
_STATUS_WARN = "#f7c948"
_STATUS_NONE = "#666677"


# ── body widget ───────────────────────────────────────────────────────────────

class _ArrayAssignBody(QWidget):
    """Status banner + scrollable role list. Refreshed via on_connections_changed."""

    def __init__(self, node: "ArrayAssignNode"):
        super().__init__()
        self._node = node
        self.setFixedSize(NODE_WIDTH, ARRAY_ASSIGN_BODY_H)
        self.setStyleSheet(f"background-color: {_BG_BODY};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # Status banner (cardinality alignment)
        self.status_lbl = QLabel("")
        self.status_lbl.setFixedHeight(22)
        self.status_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_lbl)

        # Scrollable role → colour list
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(_SCROLL_STYLE)

        self._list_widget = QWidget()
        self._list_widget.setStyleSheet(f"background-color: {_BG_ROWS};")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(2, 2, 2, 2)
        self._list_layout.setSpacing(1)
        self._list_layout.setAlignment(Qt.AlignTop)
        self._scroll.setWidget(self._list_widget)
        layout.addWidget(self._scroll)

        # Defer initial refresh until the node is in a scene so wire walks succeed
        QTimer.singleShot(0, self.refresh)

    def refresh(self) -> None:
        # Status banner
        n_roles = len(self._node.roles)
        arr = self._node.get_connected_array()
        if arr is None:
            text = f"no array connected · {n_roles} roles"
            color = _STATUS_NONE
        else:
            n_rows = len(arr.rows)
            if n_rows == n_roles:
                text = f"✓ {n_rows} rows ↔ {n_roles} roles"
                color = _STATUS_OK
            else:
                text = f"⚠ {n_rows} rows ↔ {n_roles} roles"
                color = _STATUS_WARN

        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(
            f"color: {color}; font-family: monospace; font-size: 8pt;"
            f" background-color: {_BG_ROWS}; border-radius: 3px;"
            f" border: 1px solid {color};"
        )

        # Rebuild list
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()

        for i, role in enumerate(self._node.roles):
            self._list_layout.addWidget(self._make_row(i, role, arr))

    def _make_row(self, idx: int, role: str, arr) -> QWidget:
        row = QWidget()
        row.setFixedHeight(18)
        row.setStyleSheet(f"background-color: {_BG_ROWS};")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        chip = QLabel()
        chip.setFixedSize(10, 10)
        if arr is not None and idx < len(arr.rows):
            hex_val = arr.rows[idx][0]
            chip.setStyleSheet(
                f"background-color: {hex_val}; border: 1px solid #555568;"
                " border-radius: 2px;"
            )
        else:
            chip.setStyleSheet(
                "background-color: #1a1a1f; border: 1px solid #3a3a4a;"
                " border-radius: 2px;"
            )
        layout.addWidget(chip)

        name = QLabel(role)
        name.setStyleSheet(
            "color: #aaaacc; font-family: monospace; font-size: 8pt;"
        )
        layout.addWidget(name, 1)

        return row


# ── node ──────────────────────────────────────────────────────────────────────

class ArrayAssignNode(BaseNode):
    """
    Bulk assignment node — maps a Color Array to the full Schema positionally.

    Inputs:  one `color_array` (top), one `token_dict` (bottom)
    Output:  a single ganged `assignment` port. Downstream Write nodes
             receive one wire and pull the entire role→colour mapping
             through it via get_assignments().

    Cardinality is checked against the connected Color Array's row count;
    if it doesn't match the loaded role list, the body status banner shows
    a warning. The Color Array's "Match Schema" button aligns the two.
    """

    def __init__(
        self,
        token_path: Path,
        pos: QPointF = QPointF(0.0, 0.0),
    ):
        super().__init__(
            "Array Assign",
            pos,
            view_labels=[],
        )
        self.token_path = token_path
        tokens = TokenStore(token_path).tokens()
        self.roles: list[str] = list(tokens.keys())

        self.setBodyWidget(_ArrayAssignBody(self), ARRAY_ASSIGN_BODY_H)

        # Two inputs distributed top/bottom of the body
        self.add_input_port("color_array", HEADER_HEIGHT + _PORT_MARGIN)
        self.add_input_port(
            "token_dict",
            HEADER_HEIGHT + ARRAY_ASSIGN_BODY_H - _PORT_MARGIN,
        )

        # Single ganged output — one wire downstream pulls everything
        self.add_output_port("assignment")

    # ── data flow ────────────────────────────────────────────────────────────

    def get_connected_array(self) -> ArrayNode | None:
        """Walk the color_array input wire to find the upstream ArrayNode."""
        for in_port in self.input_ports():
            if in_port.port_type != "color_array":
                continue
            for wire in in_port.wires:
                src = wire.source_port.parentItem()
                if isinstance(src, ArrayNode):
                    return src
        return None

    def is_aligned(self) -> bool | None:
        """True if connected array's row count == role count.
        None if no array is connected."""
        arr = self.get_connected_array()
        if arr is None:
            return None
        return len(arr.rows) == len(self.roles)

    def get_assignments(self) -> dict[str, str]:
        """Return all (role → hex) mappings based on positional alignment.
        Roles past the array's row count are dropped."""
        arr = self.get_connected_array()
        if arr is None:
            return {}
        result: dict[str, str] = {}
        for i, role in enumerate(self.roles):
            if i >= len(arr.rows):
                break
            hex_val, _ = arr.rows[i]
            result[role] = hex_val
        return result

    # ── refresh hook ─────────────────────────────────────────────────────────

    def on_connections_changed(self) -> None:
        if self._proxy is None:
            return
        widget = self._proxy.widget()
        if isinstance(widget, _ArrayAssignBody):
            widget.refresh()

    def to_idiograph_node(self) -> Node:
        return Node(
            id=self.node_id,
            type="array_assign",
            params={},
        )
