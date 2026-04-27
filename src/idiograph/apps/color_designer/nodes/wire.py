# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

from PySide6.QtWidgets import QGraphicsItem
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QPainter, QPen, QPainterPath, QPainterPathStroker, QColor,
)

from idiograph.apps.color_designer.nodes.port import Port

# Wire colours per SPEC: edge.default at rest, edge.selected when selected.
# These match the corresponding token values for visual consistency.
COLOR_REST = QColor("#3a3a4a")       # edge.default
COLOR_SELECTED = QColor("#7eb8f7")   # edge.selected
COLOR_INVALID = QColor("#c0392b")    # muted red — incompatible drag target


class Wire(QGraphicsItem):
    """A bezier wire between two typed ports.

    A Wire always has a source_port (an OUTPUT). target_port is None while
    the wire is being dragged from the source toward the cursor; it is set
    when the user drops on a compatible INPUT port.

    Wires are top-level scene items (not child items of nodes), so when a
    node moves the parent BaseNode is responsible for calling
    update_geometry() on every wire attached to its ports — see
    BaseNode.itemChange.
    """

    def __init__(self, source_port: Port):
        super().__init__()
        self.source_port = source_port
        self.target_port: Port | None = None
        self._cursor_pos: QPointF | None = None
        self._invalid = False

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(-1)  # render behind nodes

    # ── attach / detach ──────────────────────────────────────────────────────

    def attach(self) -> None:
        self.source_port.add_wire(self)
        if self.target_port is not None:
            self.target_port.add_wire(self)
        self._notify_endpoints()

    def detach(self) -> None:
        self.source_port.remove_wire(self)
        if self.target_port is not None:
            self.target_port.remove_wire(self)
        self._notify_endpoints()

    def _notify_endpoints(self) -> None:
        """Tell both endpoint nodes that their connection set changed."""
        for port in (self.source_port, self.target_port):
            if port is None:
                continue
            node = port.parentItem()
            if node is not None and hasattr(node, "on_connections_changed"):
                node.on_connections_changed()

    # ── drag updates ─────────────────────────────────────────────────────────

    def set_cursor_target(self, scene_pos: QPointF, invalid: bool = False) -> None:
        self.prepareGeometryChange()
        self._cursor_pos = scene_pos
        self._invalid = invalid
        self.update()

    def update_geometry(self) -> None:
        """Recompute path after either endpoint moves (e.g. node drag)."""
        self.prepareGeometryChange()
        self.update()

    # ── geometry ─────────────────────────────────────────────────────────────

    def _start_pos(self) -> QPointF:
        return self.source_port.scenePos()

    def _end_pos(self) -> QPointF:
        if self.target_port is not None:
            return self.target_port.scenePos()
        return self._cursor_pos or self._start_pos()

    def boundingRect(self) -> QRectF:
        s = self._start_pos()
        e = self._end_pos()
        margin = 30
        return QRectF(
            min(s.x(), e.x()) - margin,
            min(s.y(), e.y()) - margin,
            abs(e.x() - s.x()) + 2 * margin,
            abs(e.y() - s.y()) + 2 * margin,
        )

    def shape(self) -> QPainterPath:
        # Stroked path so clicks within ~8px of the curve hit the wire.
        stroker = QPainterPathStroker()
        stroker.setWidth(8)
        return stroker.createStroke(self._build_path())

    def _build_path(self) -> QPainterPath:
        s = self._start_pos()
        e = self._end_pos()
        path = QPainterPath()
        path.moveTo(s)
        # Cubic bezier with horizontal tangents — matches typical node-graph editors.
        dx = abs(e.x() - s.x())
        offset = max(dx * 0.5, 40)
        c1 = QPointF(s.x() + offset, s.y())
        c2 = QPointF(e.x() - offset, e.y())
        path.cubicTo(c1, c2, e)
        return path

    # ── paint ────────────────────────────────────────────────────────────────

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing)
        if self._invalid:
            color = COLOR_INVALID
        elif self.isSelected():
            color = COLOR_SELECTED
        else:
            color = COLOR_REST
        pen = QPen(color, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPath(self._build_path())
