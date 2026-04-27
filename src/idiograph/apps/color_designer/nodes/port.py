# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

from PySide6.QtWidgets import QGraphicsItem, QGraphicsSceneMouseEvent
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QBrush, QColor

PORT_RADIUS = 6
PORT_HIT_RADIUS = 10  # generous click area, larger than the visible dot

INPUT = "input"
OUTPUT = "output"

# Per-type colour. The token-file colours edge.default / edge.selected drive
# wire colour at rest and on selection (see wire.py); these per-type colours
# distinguish ports of different shapes (color vs token vs assignment).
TYPE_COLORS: dict[str, str] = {
    "color": "#7eb8f7",
    "color_array": "#7eb8f7",
    "token_role": "#f7c948",
    "token_dict": "#f7c948",
    "assignment": "#4ab88a",
}


def is_compatible(out_port: "Port", in_port: "Port") -> bool:
    """Validity rule for connecting an output port to an input port."""
    if out_port.direction != OUTPUT or in_port.direction != INPUT:
        return False
    if out_port.port_type == in_port.port_type:
        return True
    # SPEC: token_dict → token_role is allowed (Assign extracts the role)
    if out_port.port_type == "token_dict" and in_port.port_type == "token_role":
        return True
    return False


class Port(QGraphicsItem):
    """A typed connection point on a node.

    Ports are child QGraphicsItems of their parent node. They paint a single
    dot at their local origin (0, 0); the parent positions them via setPos.

    Mouse press starts a wire drag — for an OUTPUT port a new wire is
    created; for a connected INPUT port the existing wire is detached and
    the drag re-routes from the upstream output. Subsequent mouse move and
    release events are routed to the scene because Qt's mouse grabber
    keeps event flow on the originally-pressed item.
    """

    def __init__(
        self,
        parent_node: QGraphicsItem,
        port_type: str,
        direction: str,
        accept_multiple: bool = False,
        label: str = "",
    ):
        super().__init__(parent_node)
        self.port_type = port_type
        self.direction = direction
        self.accept_multiple = accept_multiple
        self.label = label
        self.wires: list = []  # List[Wire] — populated at connection time
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setZValue(2)

    @property
    def color(self) -> QColor:
        return QColor(TYPE_COLORS.get(self.port_type, "#888899"))

    # ── geometry ─────────────────────────────────────────────────────────────

    def boundingRect(self) -> QRectF:
        r = PORT_HIT_RADIUS
        return QRectF(-r, -r, 2 * r, 2 * r)

    # ── paint ────────────────────────────────────────────────────────────────

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing)
        c = self.color
        painter.setPen(QPen(c, 1.5))
        if self.wires:
            painter.setBrush(QBrush(c))                 # filled when connected
        else:
            painter.setBrush(QBrush(QColor("#1a1a1f"))) # hollow when empty
        painter.drawEllipse(QPointF(0, 0), PORT_RADIUS, PORT_RADIUS)

    # ── connection bookkeeping ───────────────────────────────────────────────

    def add_wire(self, wire) -> None:
        if wire not in self.wires:
            self.wires.append(wire)
        self.update()

    def remove_wire(self, wire) -> None:
        if wire in self.wires:
            self.wires.remove(wire)
        self.update()

    # ── mouse handling — drag wire creation / disconnection ──────────────────

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        scene = self.scene()
        if scene is None or not hasattr(scene, "start_wire_drag"):
            super().mousePressEvent(event)
            return

        if self.direction == INPUT and self.wires:
            # Disconnect + re-route from the upstream output
            wire = self.wires[0]
            source = wire.source_port
            wire.detach()
            scene.removeItem(wire)
            scene.start_wire_drag(source, event.scenePos())
            event.accept()
            return

        if self.direction == OUTPUT:
            scene.start_wire_drag(self, event.scenePos())
            event.accept()
            return

        # Empty input port — nothing to drag
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        scene = self.scene()
        if scene is not None and getattr(scene, "_dragging_wire", None):
            scene.update_wire_drag(event.scenePos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        scene = self.scene()
        if scene is not None and getattr(scene, "_dragging_wire", None):
            scene.finish_wire_drag(event.scenePos())
            event.accept()
            return
        super().mouseReleaseEvent(event)
