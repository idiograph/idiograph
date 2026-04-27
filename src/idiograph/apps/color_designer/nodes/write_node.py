# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel, QScrollArea, QFrame,
)
from PySide6.QtCore import Qt, QPointF, QTimer

import asyncio

from idiograph.apps.color_designer.nodes.base_node import BaseNode, NODE_WIDTH
from idiograph.apps.color_designer.nodes.assign_node import AssignNode
from idiograph.apps.color_designer.nodes.array_assign_node import ArrayAssignNode
from idiograph.apps.color_designer.token_store import TokenStore
from idiograph.core.executor import execute_graph
from idiograph.core.logging_config import get_logger
from idiograph.core.models import Node

_log = get_logger("apps.color_designer.write_node")

# ── port modes ────────────────────────────────────────────────────────────────
_PORT_MODES = ["All", "Conn", "Gang"]
_DEFAULT_PORT_MODE = 2  # Gang — Write accepts many inputs

# ── layout ────────────────────────────────────────────────────────────────────
WRITE_BODY_H = 196   # margins + path(24) + label(16) + list(96) + btn(28) + spacings

# ── colours / styles ──────────────────────────────────────────────────────────
_BG_BODY = "#2e2e3a"
_BG_ROWS = "#252530"

_FIELD_STYLE = (
    "background-color: #24242c; color: #ccccdd; border: 1px solid #3a3a4a;"
    " border-radius: 3px; padding: 2px 6px; font-family: monospace; font-size: 8pt;"
)
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
_SAVE_BTN_STYLE = """
    QPushButton {
        background-color: #3a3a4a; color: #ccccdd;
        border: 1px solid #4a4a5a; border-radius: 3px;
        padding: 4px; font-size: 9pt;
    }
    QPushButton:hover { background-color: #4a4a5a; color: #ffffff; }
    QPushButton:pressed { background-color: #7eb8f7; color: #1a1a1f; }
"""


# ── body widget ───────────────────────────────────────────────────────────────

class _WriteBody(QWidget):
    def __init__(self, node: "WriteNode"):
        super().__init__()
        self._node = node
        self.setFixedSize(NODE_WIDTH, WRITE_BODY_H)
        self.setStyleSheet(f"background-color: {_BG_BODY};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # Output path field
        self.path_edit = QLineEdit(str(node.token_path))
        self.path_edit.setFixedHeight(24)
        self.path_edit.setStyleSheet(_FIELD_STYLE)
        self.path_edit.editingFinished.connect(self._on_path_change)
        layout.addWidget(self.path_edit)

        # Assignments label
        lbl = QLabel("assignments")
        lbl.setFixedHeight(14)
        lbl.setStyleSheet(
            "color: #888899; font-family: monospace; font-size: 8pt;"
        )
        layout.addWidget(lbl)

        # Scrollable assignments list
        self._scroll = QScrollArea()
        self._scroll.setFixedHeight(96)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(_SCROLL_STYLE)

        self._list_widget = QWidget()
        self._list_widget.setStyleSheet(f"background-color: {_BG_ROWS};")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(4, 4, 4, 4)
        self._list_layout.setSpacing(2)
        self._list_layout.setAlignment(Qt.AlignTop)
        self._scroll.setWidget(self._list_widget)
        layout.addWidget(self._scroll)

        # Save button
        save_btn = QPushButton("Save")
        save_btn.setFixedHeight(26)
        save_btn.setStyleSheet(_SAVE_BTN_STYLE)
        save_btn.clicked.connect(self._on_save)
        layout.addWidget(save_btn)

        # Defer initial refresh until the node is in a scene
        QTimer.singleShot(0, self.refresh_list)

    def refresh_list(self) -> None:
        # Clear
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()

        assignments = self._node.collect_assignments()
        if not assignments:
            placeholder = QLabel("(no assignments)")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet(
                "color: #555568; font-family: monospace; font-size: 8pt;"
                " padding: 12px;"
            )
            self._list_layout.addWidget(placeholder)
            return

        for role, color in assignments.items():
            self._list_layout.addWidget(self._make_row(role, color))

    def _make_row(self, role: str, color: str) -> QWidget:
        row = QWidget()
        row.setFixedHeight(20)
        row.setStyleSheet(f"background-color: {_BG_ROWS};")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        chip = QLabel()
        chip.setFixedSize(12, 12)
        chip.setStyleSheet(
            f"background-color: {color}; border: 1px solid #555568;"
            " border-radius: 2px;"
        )
        layout.addWidget(chip)

        name = QLabel(role)
        name.setStyleSheet(
            "color: #aaaacc; font-family: monospace; font-size: 8pt;"
        )
        layout.addWidget(name, 1)

        return row

    def _on_path_change(self) -> None:
        text = self.path_edit.text().strip()
        if text:
            self._node.token_path = Path(text)

    def _on_save(self) -> None:
        self.refresh_list()
        scene = self._node.scene()
        if scene is None:
            _log.warning("Save clicked but WriteNode has no scene; skipping.")
            return
        graph = scene.build_graph()
        results = asyncio.run(execute_graph(graph))
        _log.info("Color designer pipeline complete: %s", results)


# ── node ──────────────────────────────────────────────────────────────────────

class WriteNode(BaseNode):
    """
    Write node — collects (role, value) assignments and writes them to a
    token JSON file via TokenStore.

    Body:   editable output path, scrollable list of currently-known
            assignments, explicit Save button.
    Ports:  one ganged input on the left (Gang is the default port-display
            mode); no output.

    Without an edge layer, the assignment set is collected by scanning the
    scene for AssignNodes. Save is the only thing that writes to disk —
    nothing happens automatically.
    """

    def __init__(
        self,
        token_path: Path,
        pos: QPointF = QPointF(0.0, 0.0),
    ):
        super().__init__(
            "Write",
            pos,
            view_labels=[],
            port_mode_labels=_PORT_MODES,
        )
        self.token_path = token_path
        self._port_mode = _DEFAULT_PORT_MODE  # default Gang
        self.setBodyWidget(_WriteBody(self), WRITE_BODY_H)
        # Single ganged input — accepts multiple incoming wires of type
        # `assignment`. Per-assignment input ports (All mode) wait for the
        # same future phase as Schema's per-role outputs.
        self.add_input_port("assignment", accept_multiple=True)

    # ── data collection ──────────────────────────────────────────────────────

    def collect_assignments(self) -> dict[str, str]:
        """Walk wires from this node's input port and collect assignments
        from every upstream Assign / ArrayAssign output actually wired to
        it. The graph is the source of truth — no scene scanning."""
        result: dict[str, str] = {}
        seen_array_assigns: set = set()
        for in_port in self.input_ports():
            for wire in in_port.wires:
                src_node = wire.source_port.parentItem()
                if isinstance(src_node, AssignNode):
                    role, color = src_node.assignment()
                    if role:
                        result[role] = color
                elif isinstance(src_node, ArrayAssignNode):
                    if src_node in seen_array_assigns:
                        continue
                    seen_array_assigns.add(src_node)
                    for role, color in src_node.get_assignments().items():
                        if role:
                            result[role] = color
        return result

    # ── save ─────────────────────────────────────────────────────────────────

    def save(self) -> None:
        """Write all current assignments to the configured token file.
        Existing tokens in the file are preserved (TokenStore loads them
        first, then we overwrite the keys we care about)."""
        if not self.token_path.exists():
            # Ensure the file exists so TokenStore can load it.
            self.token_path.write_text("{}", encoding="utf-8")

        store = TokenStore(self.token_path)
        for role, value in self.collect_assignments().items():
            store.set(role, value)
        store.save()

    def to_idiograph_node(self) -> Node:
        return Node(
            id=self.node_id,
            type="write_tokens",
            params={"token_file": str(self.token_path)},
        )
