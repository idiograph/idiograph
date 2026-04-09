# src/main.py
import sys
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QScrollArea,
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QColorDialog, QToolBar, QFileDialog
)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt
from token_store import TokenStore

HERE = Path(__file__).parent.parent


class TokenRow(QWidget):
    def __init__(self, key: str, value: str, on_change):
        super().__init__()
        self.key = key
        self.on_change = on_change

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)

        self.label = QLabel(key)
        self.label.setMinimumWidth(240)
        self.label.setStyleSheet("color: #8888aa; font-family: monospace;")

        self.swatch = QPushButton()
        self.swatch.setFixedSize(28, 28)
        self.swatch.clicked.connect(self._pick_color)

        self.hex_field = QLineEdit(value)
        self.hex_field.setFixedWidth(90)
        self.hex_field.setStyleSheet("font-family: monospace;")
        self.hex_field.editingFinished.connect(self._on_hex_edited)

        layout.addWidget(self.label)
        layout.addWidget(self.swatch)
        layout.addWidget(self.hex_field)
        layout.addStretch()

        self._apply_color(value)

    def _apply_color(self, hex_value: str) -> None:
        self.swatch.setStyleSheet(
            f"background-color: {hex_value}; border: 1px solid #555568;"
        )
        self.hex_field.setText(hex_value)

    def _pick_color(self) -> None:
        current = QColor(self.hex_field.text())
        color = QColorDialog.getColor(current, self, f"Pick: {self.key}")
        if color.isValid():
            hex_value = color.name()
            self._apply_color(hex_value)
            self.on_change(self.key, hex_value)

    def _on_hex_edited(self) -> None:
        value = self.hex_field.text().strip()
        if QColor(value).isValid():
            self._apply_color(value)
            self.on_change(self.key, value)


class MainWindow(QMainWindow):
    def __init__(self, store: TokenStore):
        super().__init__()
        self.store = store
        self.setWindowTitle("Idiograph — Color Designer")
        self.setMinimumSize(520, 600)
        self._build_toolbar()
        self._build_scroll_area()
        self._populate_rows()

    def _build_toolbar(self) -> None:
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        load_btn = QPushButton("Load")
        load_btn.clicked.connect(self._load)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)

        toolbar.addWidget(load_btn)
        toolbar.addWidget(save_btn)

    def _build_scroll_area(self) -> None:
        self.container = QWidget()
        self.rows_layout = QVBoxLayout(self.container)
        self.rows_layout.setAlignment(Qt.AlignTop)

        scroll = QScrollArea()
        scroll.setWidget(self.container)
        scroll.setWidgetResizable(True)
        self.setCentralWidget(scroll)

    def _populate_rows(self) -> None:
        # Clear existing rows
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for key, value in self.store.tokens().items():
            row = TokenRow(key, value, self._on_token_change)
            self.rows_layout.addWidget(row)

    def _on_token_change(self, key: str, value: str) -> None:
        self.store.set(key, value)

    def _save(self) -> None:
        self.store.save()
        self.setWindowTitle("Idiograph — Color Designer")

    def _load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load token file", str(HERE), "JSON files (*.json)"
        )
        if path:
            self.store.path = Path(path)
            self.store.load()
            self._populate_rows()


def main():
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
        QLineEdit {
            background-color: #24242c;
            color: #ccccdd;
            border: 1px solid #3a3a4a;
            border-radius: 3px;
            padding: 2px 6px;
            font-family: monospace;
        }
        QScrollArea {
            border: none;
        }
        QScrollBar:vertical {
            background: #1a1a1f;
            width: 8px;
        }
        QScrollBar::handle:vertical {
            background: #3a3a4a;
            border-radius: 4px;
        }
    """)

    store = TokenStore(HERE / "tokens.seed.json")
    window = MainWindow(store)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()