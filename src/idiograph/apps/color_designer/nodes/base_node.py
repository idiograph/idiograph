# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

import uuid

from PySide6.QtWidgets import QGraphicsItem, QGraphicsProxyWidget, QGraphicsSceneMouseEvent
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QBrush, QColor, QFont, QPainterPath

from idiograph.apps.color_designer.nodes.port import Port, INPUT, OUTPUT, PORT_RADIUS

# ── layout constants ──────────────────────────────────────────────────────────
NODE_WIDTH = 200
HEADER_HEIGHT = 32
STRIP_HEIGHT = 24
CORNER_RADIUS = 6
# PORT_RADIUS imported from port.py (single source of truth)

# ── palette ───────────────────────────────────────────────────────────────────
COLOR_BODY = QColor("#2e2e3a")
COLOR_HEADER = QColor("#3a3a4a")
COLOR_BORDER = QColor("#4a4a5a")
COLOR_BORDER_SEL = QColor("#7eb8f7")
COLOR_TITLE = QColor("#ccccdd")
COLOR_TYPE_LABEL = QColor("#aaaacc")
COLOR_STRIP = QColor("#252530")
COLOR_STRIP_TEXT = QColor("#666677")
COLOR_STRIP_TEXT_ACTIVE = QColor("#ccccdd")
COLOR_PORT = QColor("#7eb8f7")


class BaseNode(QGraphicsItem):
    """
    Node chrome: header, body area, optional view + port-mode strips, output port.

    Drag is restricted to the header. Body mouse events go directly to the
    embedded QGraphicsProxyWidget child; this class never needs to forward them.

    Two independent strips can be configured:
      - view_labels        → drives body view switching (e.g. Full/Cmp/Data)
      - port_mode_labels   → drives port display mode (e.g. All/Conn/Gang)
    Either, both, or neither may be set. When both are present the view strip
    sits directly below the body and the port-mode strip sits below it.

    Subclasses override _on_view_switch(idx) and _on_port_mode_switch(idx).
    """

    def __init__(
        self,
        node_type: str = "Node",
        pos: QPointF = QPointF(0.0, 0.0),
        title: str = "",
        view_labels: list[str] | None = None,
        port_mode_labels: list[str] | None = None,
    ):
        super().__init__()
        self.node_id: str = str(uuid.uuid4())
        self.node_type = node_type
        self.title = title
        self._view_labels: list[str] = view_labels or []
        self._port_mode_labels: list[str] = port_mode_labels or []
        self._active_view: int = 0
        self._port_mode: int = 0
        self._ports: list[Port] = []  # populated by add_input_port / add_output_port

        self._body_h: int = 80
        self._proxy: QGraphicsProxyWidget | None = None

        # Manual drag state
        self._dragging = False
        self._drag_start = QPointF()
        self._drag_origin = QPointF()
        self.on_drag_end: callable | None = None  # set by MainWindow for cascade reset

        self.setPos(pos)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        # ItemIsMovable intentionally omitted — drag implemented manually (header only)

    # ── geometry ──────────────────────────────────────────────────────────────

    @property
    def _view_strip_h(self) -> int:
        return STRIP_HEIGHT if self._view_labels else 0

    @property
    def _port_strip_h(self) -> int:
        return STRIP_HEIGHT if self._port_mode_labels else 0

    @property
    def _strips_total_h(self) -> int:
        return self._view_strip_h + self._port_strip_h

    @property
    def _total_h(self) -> int:
        return HEADER_HEIGHT + self._body_h + self._strips_total_h

    def boundingRect(self) -> QRectF:
        # Always extend by port radius on both sides so port child items
        # whose bounding rects extend outside NODE_WIDTH are still indexed
        # correctly by the scene.
        extra = PORT_RADIUS + 2
        return QRectF(
            -extra,
            0,
            NODE_WIDTH + 2 * extra,
            self._total_h,
        )

    def shape(self) -> QPainterPath:
        """Hit-test shape is the node body only (not the port extension)."""
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(0, 0, NODE_WIDTH, self._total_h), CORNER_RADIUS, CORNER_RADIUS
        )
        return path

    # ── body widget ───────────────────────────────────────────────────────────

    def setBodyWidget(self, widget, height: int) -> None:
        """Embed (or replace) the body widget and resize the node."""
        # Snapshot the current scene area so we can invalidate stale pixels
        # outside the new bounding rect when the body shrinks.
        old_scene_rect = self.sceneBoundingRect() if self.scene() is not None else None

        self.prepareGeometryChange()
        self._body_h = height

        if self._proxy is None:
            self._proxy = QGraphicsProxyWidget(self)
            self._proxy.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)

        old = self._proxy.widget()
        self._proxy.setWidget(widget)
        if old is not None:
            old.deleteLater()

        # Atomic position+size — calling setPos and resize separately leaves
        # a window between setWidget and resize during which the freshly
        # re-parented embedded QWidget can mis-register at proxy-local (0,0)
        # and overdraw the header band on first paint.
        self._proxy.setGeometry(QRectF(0, HEADER_HEIGHT, NODE_WIDTH, height))

        self._reposition_centered_ports()
        self.update()
        if old_scene_rect is not None:
            self.scene().update(old_scene_rect)

    def resizeBody(self, height: int) -> None:
        """Called by body widgets to dynamically expand the node (e.g. on row add)."""
        if height == self._body_h:
            return
        old_scene_rect = self.sceneBoundingRect() if self.scene() is not None else None
        self.prepareGeometryChange()
        self._body_h = height
        if self._proxy is not None:
            self._proxy.setGeometry(QRectF(0, HEADER_HEIGHT, NODE_WIDTH, height))
        self._reposition_centered_ports()
        self.update()
        if old_scene_rect is not None:
            self.scene().update(old_scene_rect)

    def _reposition_centered_ports(self) -> None:
        """Move every port created with the default y_offset to the new
        body centre. Ports placed at explicit y_offsets stay where they
        were (their position was a deliberate layout choice)."""
        cy = HEADER_HEIGHT + self._body_h / 2
        for port in self._ports:
            if not getattr(port, "_follows_center", False):
                continue
            x = NODE_WIDTH if port.direction == OUTPUT else 0
            port.setPos(x, cy)
            for wire in port.wires:
                wire.update_geometry()

    # ── paint paths ───────────────────────────────────────────────────────────

    def _node_path(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(0, 0, NODE_WIDTH, self._total_h), CORNER_RADIUS, CORNER_RADIUS
        )
        return path

    def _header_path(self) -> QPainterPath:
        """Top-rounded, bottom-square."""
        r, w, h = CORNER_RADIUS, NODE_WIDTH, HEADER_HEIGHT
        path = QPainterPath()
        path.moveTo(0, h)
        path.lineTo(0, r)
        path.arcTo(QRectF(0, 0, 2 * r, 2 * r), 180, -90)
        path.lineTo(w - r, 0)
        path.arcTo(QRectF(w - 2 * r, 0, 2 * r, 2 * r), 90, -90)
        path.lineTo(w, h)
        path.closeSubpath()
        return path

    def _strip_path_at(self, y0: float, rounded_bottom: bool) -> QPainterPath:
        """Strip shape at the given y. Bottom-most strip gets rounded corners."""
        r, w, h = CORNER_RADIUS, NODE_WIDTH, STRIP_HEIGHT
        path = QPainterPath()
        if rounded_bottom:
            path.moveTo(0, y0)
            path.lineTo(0, y0 + h - r)
            path.arcTo(QRectF(0, y0 + h - 2 * r, 2 * r, 2 * r), 180, 90)
            path.lineTo(w - r, y0 + h)
            path.arcTo(QRectF(w - 2 * r, y0 + h - 2 * r, 2 * r, 2 * r), 270, 90)
            path.lineTo(w, y0)
            path.closeSubpath()
        else:
            path.addRect(QRectF(0, y0, w, h))
        return path

    # ── paint ─────────────────────────────────────────────────────────────────

    def paint(self, painter: QPainter, option, widget=None) -> None:
        selected = self.isSelected()
        painter.setRenderHint(QPainter.Antialiasing)

        # Body fill
        painter.fillPath(self._node_path(), QBrush(COLOR_BODY))

        # Header fill
        painter.fillPath(self._header_path(), QBrush(COLOR_HEADER))

        # Strips (view first if present, port-mode below it)
        self._paint_strips(painter)

        # Border (drawn last so it sits on top of fills)
        painter.setPen(QPen(COLOR_BORDER_SEL if selected else COLOR_BORDER, 1.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(self._node_path())

        # Type label — uppercased explicitly (don't rely on QFont capitalization
        # combined with horizontalAdvance — they disagree and clip the rect).
        # Wide rect + AlignLeft so width is never the limiter; right-aligned
        # title in the same rect avoids any positioning math.
        header_rect = QRectF(10, 0, NODE_WIDTH - 20, HEADER_HEIGHT)
        type_font = QFont("monospace", 8)
        type_font.setBold(True)
        painter.setFont(type_font)
        painter.setPen(QPen(COLOR_TYPE_LABEL))
        painter.drawText(
            header_rect,
            Qt.AlignVCenter | Qt.AlignLeft,
            self.node_type.upper(),
        )

        if self.title:
            painter.setFont(QFont("monospace", 9))
            painter.setPen(QPen(COLOR_TITLE))
            painter.drawText(
                header_rect,
                Qt.AlignVCenter | Qt.AlignRight,
                self.title,
            )

        # Ports paint themselves as child items — nothing to do here.

    def _paint_strips(self, painter: QPainter) -> None:
        body_end = HEADER_HEIGHT + self._body_h
        has_view = bool(self._view_labels)
        has_port = bool(self._port_mode_labels)

        if has_view:
            y = body_end
            painter.fillPath(
                self._strip_path_at(y, rounded_bottom=not has_port),
                QBrush(COLOR_STRIP),
            )
            self._paint_strip_labels(
                painter, y, self._view_labels, self._active_view
            )
        if has_port:
            y = body_end + self._view_strip_h
            painter.fillPath(
                self._strip_path_at(y, rounded_bottom=True),
                QBrush(COLOR_STRIP),
            )
            self._paint_strip_labels(
                painter, y, self._port_mode_labels, self._port_mode
            )

    def _paint_strip_labels(
        self,
        painter: QPainter,
        y0: float,
        labels: list[str],
        active_idx: int,
    ) -> None:
        n = len(labels)
        btn_w = NODE_WIDTH / n
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        for i, label in enumerate(labels):
            active = i == active_idx
            x = i * btn_w
            if active:
                painter.fillRect(
                    QRectF(x + 2, y0, btn_w - 4, 2), QBrush(COLOR_BORDER_SEL)
                )
            painter.setPen(
                QPen(COLOR_STRIP_TEXT_ACTIVE if active else COLOR_STRIP_TEXT)
            )
            painter.drawText(
                QRectF(x, y0, btn_w, STRIP_HEIGHT), Qt.AlignCenter, label
            )

    # ── port management ──────────────────────────────────────────────────────

    def add_output_port(
        self,
        port_type: str,
        y_offset: float | None = None,
    ) -> Port:
        """Add an output port on the right edge. Default y is body centre.
        Ports without an explicit y_offset are tagged so they follow the
        body centre across view switches."""
        follows_center = y_offset is None
        if y_offset is None:
            y_offset = HEADER_HEIGHT + self._body_h / 2
        port = Port(self, port_type, OUTPUT)
        port._follows_center = follows_center
        port.setPos(NODE_WIDTH, y_offset)
        self._ports.append(port)
        return port

    def add_input_port(
        self,
        port_type: str,
        y_offset: float | None = None,
        accept_multiple: bool = False,
    ) -> Port:
        """Add an input port on the left edge. Default y is body centre.
        Ports without an explicit y_offset are tagged so they follow the
        body centre across view switches."""
        follows_center = y_offset is None
        if y_offset is None:
            y_offset = HEADER_HEIGHT + self._body_h / 2
        port = Port(self, port_type, INPUT, accept_multiple=accept_multiple)
        port._follows_center = follows_center
        port.setPos(0, y_offset)
        self._ports.append(port)
        return port

    def output_ports(self) -> list[Port]:
        return [p for p in self._ports if p.direction == OUTPUT]

    def input_ports(self) -> list[Port]:
        return [p for p in self._ports if p.direction == INPUT]

    def remove_all_ports(self) -> None:
        """Detach + delete all ports (used when bodies/modes rebuild)."""
        for port in list(self._ports):
            for wire in list(port.wires):
                wire.detach()
                if wire.scene() is not None:
                    wire.scene().removeItem(wire)
            port.setParentItem(None)
            if port.scene() is not None:
                port.scene().removeItem(port)
        self._ports.clear()

    # ── header-only drag ──────────────────────────────────────────────────────

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        y = event.pos().y()
        body_end = HEADER_HEIGHT + self._body_h

        if y <= HEADER_HEIGHT:
            self._dragging = True
            self._drag_start = event.scenePos()
            self._drag_origin = self.pos()
            self.setZValue(1.0)
            event.accept()
            return

        if y >= body_end:
            strip_y = y - body_end
            x = event.pos().x()

            if self._view_labels and strip_y < self._view_strip_h:
                btn_w = NODE_WIDTH / len(self._view_labels)
                idx = max(0, min(int(x / btn_w), len(self._view_labels) - 1))
                if idx != self._active_view:
                    self._active_view = idx
                    self.update()
                    self._on_view_switch(idx)
                event.accept()
                return

            if self._port_mode_labels and strip_y >= self._view_strip_h:
                btn_w = NODE_WIDTH / len(self._port_mode_labels)
                idx = max(0, min(int(x / btn_w), len(self._port_mode_labels) - 1))
                if idx != self._port_mode:
                    self._port_mode = idx
                    self.update()
                    self._on_port_mode_switch(idx)
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._dragging:
            self.setPos(self._drag_origin + (event.scenePos() - self._drag_start))
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._dragging:
            self._dragging = False
            self.setZValue(0.0)
            if self.on_drag_end is not None:
                self.on_drag_end()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    # ── override hook ─────────────────────────────────────────────────────────

    def _on_view_switch(self, idx: int) -> None:
        """Called when the user selects a different view. Override in subclasses."""

    def _on_port_mode_switch(self, idx: int) -> None:
        """Called when the user selects a different port display mode."""

    def on_connections_changed(self) -> None:
        """Called when a wire is attached to or detached from any of this
        node's ports. Subclasses override to refresh derived state (e.g. an
        ArrayAssign body that displays the connected array's row count)."""

    # ── wire sync on move ────────────────────────────────────────────────────

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for port in self._ports:
                for wire in port.wires:
                    wire.update_geometry()
        return super().itemChange(change, value)
