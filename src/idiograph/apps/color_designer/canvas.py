# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

from PySide6.QtWidgets import QGraphicsScene, QGraphicsView
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QColor, QBrush

from idiograph.apps.color_designer.nodes.base_node import BaseNode
from idiograph.core.models import Edge, Graph


class NodeGraphScene(QGraphicsScene):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSceneRect(-5000, -5000, 10000, 10000)
        self.setBackgroundBrush(QBrush(QColor("#1a1a1f")))
        self._dragging_wire = None  # active in-progress Wire during a drag

    def drawBackground(self, painter: QPainter, rect) -> None:
        super().drawBackground(painter, rect)
        # Subtle dot grid
        painter.setPen(QColor("#2a2a35"))
        grid_size = 24
        left = int(rect.left()) - (int(rect.left()) % grid_size)
        top = int(rect.top()) - (int(rect.top()) % grid_size)
        x = left
        while x < rect.right():
            y = top
            while y < rect.bottom():
                painter.drawPoint(x, y)
                y += grid_size
            x += grid_size

    # ── wire drag protocol (called from Port mouse handlers) ──────────────────

    def start_wire_drag(self, source_port, scene_pos) -> None:
        from idiograph.apps.color_designer.nodes.wire import Wire
        from idiograph.apps.color_designer.nodes.port import OUTPUT
        if source_port.direction != OUTPUT:
            return
        wire = Wire(source_port)
        wire.set_cursor_target(scene_pos)
        self.addItem(wire)
        self._dragging_wire = wire

    def update_wire_drag(self, scene_pos) -> None:
        if self._dragging_wire is None:
            return
        target = self._port_at(scene_pos)
        invalid = False
        if target is not None and target is not self._dragging_wire.source_port:
            from idiograph.apps.color_designer.nodes.port import is_compatible
            if not is_compatible(self._dragging_wire.source_port, target):
                invalid = True
        self._dragging_wire.set_cursor_target(scene_pos, invalid=invalid)

    def finish_wire_drag(self, scene_pos) -> None:
        if self._dragging_wire is None:
            return
        wire = self._dragging_wire
        self._dragging_wire = None

        target = self._port_at(scene_pos)
        from idiograph.apps.color_designer.nodes.port import is_compatible
        if (
            target is not None
            and target is not wire.source_port
            and is_compatible(wire.source_port, target)
        ):
            # If the input doesn't accept multiple, drop any existing wires.
            if not target.accept_multiple:
                for existing in list(target.wires):
                    existing.detach()
                    self.removeItem(existing)
            wire.target_port = target
            wire.attach()
            wire.update_geometry()
        else:
            self.removeItem(wire)

    def _port_at(self, scene_pos):
        from idiograph.apps.color_designer.nodes.port import Port
        for item in self.items(scene_pos):
            if isinstance(item, Port):
                return item
        return None

    # ── graph export ─────────────────────────────────────────────────────────

    def build_graph(self) -> Graph:
        """Snapshot current scene state as an Idiograph Graph.

        Nodes come from each BaseNode's to_idiograph_node(); edges come from
        live Wire items whose source and target ports are both attached.
        """
        from idiograph.apps.color_designer.nodes.wire import Wire

        nodes = [
            item.to_idiograph_node()
            for item in self.items()
            if isinstance(item, BaseNode)
        ]
        edges = []
        for item in self.items():
            if not isinstance(item, Wire):
                continue
            if item.source_port is None or item.target_port is None:
                continue
            src_node = item.source_port.parentItem()
            tgt_node = item.target_port.parentItem()
            if src_node is None or tgt_node is None:
                continue
            edges.append(
                Edge(source=src_node.node_id, target=tgt_node.node_id, type="DATA")
            )
        return Graph(name="color_design", version="1.0", nodes=nodes, edges=edges)


class NodeGraphView(QGraphicsView):
    def __init__(self, scene: NodeGraphScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(self.Shape.NoFrame)

        self._panning = False
        self._pan_start = QPointF()
        self._space_held = False

        self.setFocusPolicy(Qt.StrongFocus)

    # ── zoom ──────────────────────────────────────────────────────────────────

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        factor = 1.12 if delta > 0 else 1.0 / 1.12
        current = self.transform().m11()  # current x scale
        if (factor > 1 and current < 8.0) or (factor < 1 and current > 0.1):
            self.scale(factor, factor)

    # ── pan ───────────────────────────────────────────────────────────────────

    # ── frame hotkeys ─────────────────────────────────────────────────────────

    _FRAME_MARGIN = 40  # px in scene coords added on each side

    def _rect_of_items(self, items) -> QRectF:
        rect = QRectF()
        for item in items:
            rect = rect.united(item.mapToScene(item.boundingRect()).boundingRect())
        return rect

    def _frame(self, rect: QRectF) -> None:
        if rect.isNull():
            return
        margin = self._FRAME_MARGIN
        rect = rect.adjusted(-margin, -margin, margin, margin)
        self.fitInView(rect, Qt.KeepAspectRatio)

    def frame_all(self) -> None:
        self._frame(self._rect_of_items(self.scene().items()))

    def frame_selected(self) -> None:
        selected = self.scene().selectedItems()
        items = selected if selected else self.scene().items()
        self._frame(self._rect_of_items(items))

    # ── keyboard ──────────────────────────────────────────────────────────────

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if not event.isAutoRepeat():
            if key == Qt.Key_F:
                self.frame_all()
                return
            if key == Qt.Key_S:
                self.frame_selected()
                return
            if key == Qt.Key_Delete or key == Qt.Key_Backspace:
                self._delete_selected_wires()
                return
        if key == Qt.Key_Space and not event.isAutoRepeat():
            self._space_held = True
            self.setCursor(Qt.OpenHandCursor)
        super().keyPressEvent(event)

    def _delete_selected_wires(self) -> None:
        from idiograph.apps.color_designer.nodes.wire import Wire
        scene = self.scene()
        if scene is None:
            return
        for item in list(scene.selectedItems()):
            if isinstance(item, Wire):
                item.detach()
                scene.removeItem(item)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_held = False
            if not self._panning:
                self.setCursor(Qt.ArrowCursor)
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event) -> None:
        is_middle = event.button() == Qt.MiddleButton
        is_space_left = event.button() == Qt.LeftButton and self._space_held

        if is_middle or is_space_left:
            self._panning = True
            self._pan_start = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return

        if event.button() == Qt.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if item is None:
                self.setDragMode(QGraphicsView.RubberBandDrag)

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._panning:
            delta = event.position() - self._pan_start
            self._pan_start = event.position()
            self.horizontalScrollBar().setValue(
                int(self.horizontalScrollBar().value() - delta.x())
            )
            self.verticalScrollBar().setValue(
                int(self.verticalScrollBar().value() - delta.y())
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._panning and event.button() in (Qt.MiddleButton, Qt.LeftButton):
            self._panning = False
            self.setCursor(Qt.OpenHandCursor if self._space_held else Qt.ArrowCursor)
            event.accept()
            return
        self.setDragMode(QGraphicsView.NoDrag)
        super().mouseReleaseEvent(event)
