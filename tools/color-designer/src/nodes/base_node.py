from PySide6.QtWidgets import QGraphicsItem, QGraphicsProxyWidget, QGraphicsSceneMouseEvent
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QBrush, QColor, QFont, QPainterPath

# ── layout constants ──────────────────────────────────────────────────────────
NODE_WIDTH = 200
HEADER_HEIGHT = 32
STRIP_HEIGHT = 24
CORNER_RADIUS = 6
PORT_RADIUS = 6

# ── palette ───────────────────────────────────────────────────────────────────
COLOR_BODY = QColor("#2e2e3a")
COLOR_HEADER = QColor("#3a3a4a")
COLOR_BORDER = QColor("#4a4a5a")
COLOR_BORDER_SEL = QColor("#7eb8f7")
COLOR_TITLE = QColor("#ccccdd")
COLOR_STRIP = QColor("#252530")
COLOR_STRIP_TEXT = QColor("#666677")
COLOR_STRIP_TEXT_ACTIVE = QColor("#ccccdd")
COLOR_PORT = QColor("#7eb8f7")


class BaseNode(QGraphicsItem):
    """
    Node chrome: header, body area, view-strip, output port dot.

    Drag is restricted to the header. Body mouse events go directly to the
    embedded QGraphicsProxyWidget child; this class never needs to forward them.

    Subclasses call setBodyWidget(widget, height) to embed a body and override
    _on_view_switch(idx) to respond when the user clicks a strip button.
    """

    def __init__(
        self,
        title: str = "Node",
        pos: QPointF = QPointF(0.0, 0.0),
        view_labels: list[str] | None = None,
        output_port: bool = False,
    ):
        super().__init__()
        self.title = title
        self._view_labels: list[str] = view_labels or []
        self._active_view: int = 0
        self._output_port = output_port

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
    def _strip_h(self) -> int:
        return STRIP_HEIGHT if self._view_labels else 0

    @property
    def _total_h(self) -> int:
        return HEADER_HEIGHT + self._body_h + self._strip_h

    def boundingRect(self) -> QRectF:
        # Extend right by port radius so the port dot is inside the bounding rect.
        extra = (PORT_RADIUS + 2) if self._output_port else 0
        return QRectF(0, 0, NODE_WIDTH + extra, self._total_h)

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
        self.prepareGeometryChange()
        self._body_h = height

        if self._proxy is None:
            self._proxy = QGraphicsProxyWidget(self)
            self._proxy.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
            self._proxy.setPos(0, HEADER_HEIGHT)

        old = self._proxy.widget()
        self._proxy.setWidget(widget)
        if old is not None:
            old.deleteLater()

        if widget is not None:
            self._proxy.resize(NODE_WIDTH, height)

        self.update()

    def resizeBody(self, height: int) -> None:
        """Called by body widgets to dynamically expand the node (e.g. on row add)."""
        if height == self._body_h:
            return
        self.prepareGeometryChange()
        self._body_h = height
        if self._proxy is not None:
            self._proxy.resize(NODE_WIDTH, height)
        self.update()

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

    def _strip_path(self) -> QPainterPath:
        """Bottom-rounded, top-square."""
        r, w = CORNER_RADIUS, NODE_WIDTH
        y0 = HEADER_HEIGHT + self._body_h
        h = self._strip_h
        path = QPainterPath()
        path.moveTo(0, y0)
        path.lineTo(0, y0 + h - r)
        path.arcTo(QRectF(0, y0 + h - 2 * r, 2 * r, 2 * r), 180, 90)
        path.lineTo(w - r, y0 + h)
        path.arcTo(QRectF(w - 2 * r, y0 + h - 2 * r, 2 * r, 2 * r), 270, 90)
        path.lineTo(w, y0)
        path.closeSubpath()
        return path

    # ── paint ─────────────────────────────────────────────────────────────────

    def paint(self, painter: QPainter, option, widget=None) -> None:
        selected = self.isSelected()
        painter.setRenderHint(QPainter.Antialiasing)

        # Body fill
        painter.fillPath(self._node_path(), QBrush(COLOR_BODY))

        # Header fill
        painter.fillPath(self._header_path(), QBrush(COLOR_HEADER))

        # View strip
        if self._view_labels:
            painter.fillPath(self._strip_path(), QBrush(COLOR_STRIP))
            self._paint_strip(painter)

        # Border (drawn last so it sits on top of fills)
        painter.setPen(QPen(COLOR_BORDER_SEL if selected else COLOR_BORDER, 1.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(self._node_path())

        # Title
        font = QFont("monospace", 9)
        painter.setFont(font)
        painter.setPen(QPen(COLOR_TITLE))
        painter.drawText(
            QRectF(10, 0, NODE_WIDTH - 20, HEADER_HEIGHT),
            Qt.AlignVCenter | Qt.AlignLeft,
            self.title,
        )

        # Output port dot
        if self._output_port:
            self._paint_output_port(painter)

    def _paint_strip(self, painter: QPainter) -> None:
        n = len(self._view_labels)
        btn_w = NODE_WIDTH / n
        y0 = HEADER_HEIGHT + self._body_h
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        for i, label in enumerate(self._view_labels):
            active = i == self._active_view
            x = i * btn_w
            # Active tab indicator — thin bar at top of button
            if active:
                painter.fillRect(
                    QRectF(x + 2, y0, btn_w - 4, 2), QBrush(COLOR_BORDER_SEL)
                )
            painter.setPen(
                QPen(COLOR_STRIP_TEXT_ACTIVE if active else COLOR_STRIP_TEXT)
            )
            painter.drawText(
                QRectF(x, y0, btn_w, self._strip_h), Qt.AlignCenter, label
            )

    def _paint_output_port(self, painter: QPainter) -> None:
        cx = NODE_WIDTH
        cy = HEADER_HEIGHT + self._body_h / 2
        painter.setPen(QPen(COLOR_BODY, 1.5))
        painter.setBrush(QBrush(COLOR_PORT))
        painter.drawEllipse(QPointF(cx, cy), PORT_RADIUS, PORT_RADIUS)

    # ── header-only drag ──────────────────────────────────────────────────────

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        y = event.pos().y()
        if y <= HEADER_HEIGHT:
            self._dragging = True
            self._drag_start = event.scenePos()
            self._drag_origin = self.pos()
            self.setZValue(1.0)
            event.accept()
        elif self._view_labels and y >= HEADER_HEIGHT + self._body_h:
            x = event.pos().x()
            btn_w = NODE_WIDTH / len(self._view_labels)
            idx = max(0, min(int(x / btn_w), len(self._view_labels) - 1))
            if idx != self._active_view:
                self._active_view = idx
                self.update()
                self._on_view_switch(idx)
            event.accept()
        else:
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
