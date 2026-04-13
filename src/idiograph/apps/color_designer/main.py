import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication, QMainWindow, QToolBar, QPushButton
from PySide6.QtCore import QPointF

from idiograph.apps.color_designer.canvas import NodeGraphScene, NodeGraphView
from idiograph.apps.color_designer.nodes.base_node import BaseNode
from idiograph.apps.color_designer.nodes.swatch_node import SwatchNode
from idiograph.apps.color_designer.nodes.array_node import ArrayNode
from idiograph.apps.color_designer.nodes.schema_node import SchemaNode
from idiograph.apps.color_designer.nodes.assign_node import AssignNode
from idiograph.apps.color_designer.nodes.write_node import WriteNode
from idiograph.apps.color_designer.nodes.array_assign_node import ArrayAssignNode

HERE = Path(__file__).parent
TOKEN_FILE = HERE / "tokens.seed.json"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Idiograph — Color Designer")
        self.setMinimumSize(900, 600)
        self.resize(1280, 800)

        self._scene = NodeGraphScene()
        self._view = NodeGraphView(self._scene)
        self.setCentralWidget(self._view)

        self._next_spawn: QPointF | None = None  # None → use viewport centre
        self._node_counter = 0
        self._seeded = False

        self._build_toolbar()
        # NOTE: do NOT seed here. Seeding runs in showEvent so the seeded
        # nodes are constructed under exactly the same realised-window
        # context as nodes spawned by the +Node button. Building proxy-
        # widget bodies in __init__ (before show()) leaves the embedded
        # QWidget in a half-initialised state and clips the parent's
        # header paint at first render.

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._seeded:
            self._seeded = True
            self._seed_nodes()

    def _build_toolbar(self) -> None:
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        add_btn = QPushButton("+ Node")
        add_btn.clicked.connect(self._add_node)
        toolbar.addWidget(add_btn)

    # ── spawn helpers ──────────────────────────────────────────────────────────

    def _spawn_pos(self) -> QPointF:
        """Return next spawn position and advance the cascade by +20/−20."""
        if self._next_spawn is None:
            pos = self._view.mapToScene(self._view.viewport().rect().center())
        else:
            pos = self._next_spawn
        self._next_spawn = pos + QPointF(20, -20)
        return pos

    def _reset_spawn_cascade(self) -> None:
        """Called when any node is manually dragged — breaks the current cascade."""
        self._next_spawn = None

    def _wire_node(self, node) -> None:
        """Attach the cascade-reset callback to a node."""
        node.on_drag_end = self._reset_spawn_cascade

    # ── scene population ───────────────────────────────────────────────────────

    def _install_node(self, node) -> None:
        """Single entry point — every node added to the scene goes through here.
        Used identically by _seed_nodes (post-show) and _add_node (button click)."""
        self._scene.addItem(node)
        self._wire_node(node)

    def _seed_nodes(self) -> None:
        self._install_node(SwatchNode("#7eb8f7", "node.selected", QPointF(0, 0)))
        self._install_node(SwatchNode("#2e2e3a", "node.default", QPointF(240, 0)))
        self._install_node(SwatchNode("#f7c948", "semantic.alert", QPointF(480, 0)))
        self._install_node(ArrayNode(
            "status colours",
            [
                ("#555568", "pending"),
                ("#f7c948", "running"),
                ("#4ab88a", "complete"),
                ("#c0392b", "failed"),
            ],
            QPointF(720, 0),
        ))
        self._install_node(SchemaNode(TOKEN_FILE, QPointF(960, 0)))
        self._install_node(AssignNode(
            TOKEN_FILE, "node.selected", "#7eb8f7", QPointF(0, 320),
        ))
        self._install_node(AssignNode(
            TOKEN_FILE, "semantic.alert", "#f7c948", QPointF(260, 320),
        ))
        self._install_node(WriteNode(TOKEN_FILE, QPointF(960, 320)))
        self._install_node(ArrayAssignNode(TOKEN_FILE, QPointF(520, 600)))

    def _add_node(self) -> None:
        self._node_counter += 1
        self._install_node(BaseNode(
            node_type="Node",
            pos=self._spawn_pos(),
            title=f"#{self._node_counter}",
        ))


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QMainWindow, QWidget {
            background-color: #1a1a1f;
            color: #ccccdd;
        }
        QToolBar {
            background-color: #24242c;
            border-bottom: 1px solid #3a3a4a;
            padding: 4px;
            spacing: 4px;
        }
        QPushButton {
            background-color: #2e2e3a;
            color: #ccccdd;
            border: 1px solid #3a3a4a;
            border-radius: 3px;
            padding: 4px 12px;
        }
        QPushButton:hover {
            background-color: #3a3a4a;
        }
        QGraphicsView {
            border: none;
            background-color: #1a1a1f;
        }
    """)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
