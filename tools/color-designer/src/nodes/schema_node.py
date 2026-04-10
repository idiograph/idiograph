from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QScrollArea, QFrame,
)
from PySide6.QtCore import Qt, QPointF

from nodes.base_node import BaseNode, NODE_WIDTH
from token_store import TokenStore

# ── modes ─────────────────────────────────────────────────────────────────────
_PORT_MODES = ["All", "Conn", "Gang"]
_MODE_ALL = 0
_MODE_CONN = 1
_MODE_GANG = 2

# ── heights ───────────────────────────────────────────────────────────────────
LIST_BODY_H = 220   # All / Conn — fixed height with scroll
GANG_BODY_H = 48    # Ganged — single port summary

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

    def __init__(self, role: str, hex_value: str, port_active: bool):
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
        dot_color = hex_value if port_active else "#3a3a4a"
        dot_border = "#7eb8f7" if port_active else "#3a3a4a"
        dot.setStyleSheet(
            f"background-color: {dot_color}; border-radius: 4px;"
            f" border: 1px solid {dot_border};"
        )
        layout.addWidget(dot)


# ── role list body (All / Connected) ──────────────────────────────────────────

class _RoleListBody(QWidget):
    def __init__(self, node: "SchemaNode", connected_only: bool):
        super().__init__()
        self.setFixedSize(NODE_WIDTH, LIST_BODY_H)
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

        # In Phase D nothing is wired, so Connected mode shows a placeholder.
        # All mode shows every role with a live port dot.
        if connected_only:
            placeholder = QLabel("no connected ports")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet(
                "color: #555568; font-family: monospace; font-size: 8pt;"
                " padding: 20px;"
            )
            vbox.addWidget(placeholder)
        else:
            for role in node.roles:
                hex_val = node.values.get(role, "#888899")
                vbox.addWidget(_RoleRow(role, hex_val, port_active=True))

        scroll.setWidget(container)
        outer.addWidget(scroll)


# ── ganged body (single output) ───────────────────────────────────────────────

class _GangedBody(QWidget):
    def __init__(self, node: "SchemaNode"):
        super().__init__()
        self.setFixedSize(NODE_WIDTH, GANG_BODY_H)
        self.setStyleSheet(f"background-color: {_BG_BODY};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setAlignment(Qt.AlignCenter)

        n = len(node.roles)
        lbl = QLabel(f"{n} role{'s' if n != 1 else ''}  →  full token dict")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(
            "color: #888899; font-family: monospace; font-size: 8pt;"
        )
        layout.addWidget(lbl)


# ── node ──────────────────────────────────────────────────────────────────────

class SchemaNode(BaseNode):
    """
    Schema node — token role registry.
    Loads roles from a token JSON file (flat dot-notation).
    No input ports. Outputs depend on the active port-display mode:
      All  — one port per role (rendered inside the scrollable body)
      Conn — only wired roles visible (empty in Phase D)
      Gang — single output port emitting the full token dict
    """

    def __init__(self, token_path: Path, pos: QPointF = QPointF(0.0, 0.0)):
        super().__init__(
            "Schema",
            pos,
            view_labels=[],                # no body view switching
            port_mode_labels=_PORT_MODES,  # strip drives port display mode
            output_port=False,             # toggled by Ganged mode
        )
        self.token_path = token_path
        tokens = TokenStore(token_path).tokens()
        self.roles: list[str] = list(tokens.keys())
        self.values: dict[str, str] = tokens

        self._rebuild_body()

    # ── port-mode handler ─────────────────────────────────────────────────────

    def _on_port_mode_switch(self, idx: int) -> None:
        self._rebuild_body()

    def _rebuild_body(self) -> None:
        if self._port_mode == _MODE_GANG:
            self._output_port = True
            self.setBodyWidget(_GangedBody(self), GANG_BODY_H)
        else:
            self._output_port = False
            connected_only = self._port_mode == _MODE_CONN
            self.setBodyWidget(
                _RoleListBody(self, connected_only=connected_only),
                LIST_BODY_H,
            )
