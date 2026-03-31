"""
SearchableSelectWidget — compact searchable dropdown for selecting items.

Two modes:
  · show_quantity=False  — simple selection (e.g. soldier exclusion lists)
  · show_quantity=True   — each selected item has an integer quantity spinner
                           (e.g. role requirements)

The dropdown is a plain QListWidget parented to the top-level window and
positioned absolutely below the search field.  It uses raise_() to render
on top of sibling widgets.  Dismissal is handled by an event filter on the
parent window that watches for mouse clicks outside the dropdown/search.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QListWidget, QListWidgetItem, QLabel, QPushButton,
    QScrollArea, QFrame, QSpinBox, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal, QEvent, QPoint, QTimer


class _NoScrollSpinBox(QSpinBox):
    """QSpinBox that ignores scroll-wheel events."""
    def wheelEvent(self, event):
        event.ignore()


class SearchableSelectWidget(QWidget):
    selection_changed = pyqtSignal()

    def __init__(self, show_quantity: bool = False, parent=None):
        super().__init__(parent)
        self._show_quantity = show_quantity
        self._all_items: dict[int | str, str] = {}
        self._selected: dict[int | str, int] = {}
        self._dropdown: QListWidget | None = None   # created lazily
        self._filter_installed = False
        self._setup_ui()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_changed)
        self._search.installEventFilter(self)
        outer.addWidget(self._search)

        self._selected_scroll = QScrollArea()
        self._selected_scroll.setWidgetResizable(True)
        self._selected_scroll.setMaximumHeight(200)
        self._selected_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._selected_container = QWidget()
        self._selected_layout = QVBoxLayout(self._selected_container)
        self._selected_layout.setContentsMargins(0, 0, 0, 0)
        self._selected_layout.setSpacing(2)
        self._selected_layout.addStretch()
        self._selected_scroll.setWidget(self._selected_container)
        outer.addWidget(self._selected_scroll)

    # ── Dropdown (lazily created as child of top-level window) ───────────

    def _ensure_dropdown(self):
        if self._dropdown is not None:
            return
        # Parent to the nearest top-level window (dialog) so it overlays correctly
        dd = QListWidget(self.window())
        dd.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        dd.setVisible(False)
        dd.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        dd.itemClicked.connect(self._on_item_clicked)
        # Style it to look like a dropdown
        dd.setStyleSheet(
            "QListWidget { border: 1px solid #4caf50; }"
        )
        self._dropdown = dd

    def _position_dropdown(self):
        """Move the dropdown to sit just below the search field."""
        dd = self._dropdown
        if dd is None:
            return
        # Map the bottom-left of the search field to the top-level parent coords
        pos = self._search.mapTo(dd.parentWidget(), QPoint(0, self._search.height()))
        dd.move(pos)
        dd.setFixedWidth(self._search.width())

    # ── Event handling ───────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        # Filter on the search field: open dropdown on focus
        if obj is self._search:
            if event.type() == QEvent.Type.FocusIn:
                self._open_dropdown()
            elif event.type() == QEvent.Type.FocusOut:
                # Delay so itemClicked on the dropdown fires first
                QTimer.singleShot(150, self._hide_if_not_focused)
            return super().eventFilter(obj, event)

        # Filter on the parent window: close dropdown on outside click
        if event.type() == QEvent.Type.MouseButtonPress:
            if self._dropdown is not None and self._dropdown.isVisible():
                click_pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
                # Check if click is inside the dropdown or search field
                dd_rect = self._dropdown.geometry()
                search_global = self._search.mapTo(self._dropdown.parentWidget(),
                                                    QPoint(0, 0))
                search_rect = self._search.rect().translated(search_global)
                if not dd_rect.contains(click_pos) and not search_rect.contains(click_pos):
                    self._close_dropdown()
                    self._search.clearFocus()

        return super().eventFilter(obj, event)

    def _hide_if_not_focused(self):
        try:
            if not self._search.hasFocus():
                self._close_dropdown()
        except RuntimeError:
            pass

    # ── Public API ──────────────────────────────────────────────────────

    def set_items(self, items: list[tuple[int | str, str]]):
        self._all_items = {item_id: name for item_id, name in items}
        if self._dropdown is not None:
            self._refresh_dropdown()

    def get_selected(self):
        if self._show_quantity:
            return [(item_id, qty) for item_id, qty in self._selected.items()]
        return list(self._selected.keys())

    def set_selected(self, selected):
        self._selected.clear()
        if isinstance(selected, dict):
            for key, qty in selected.items():
                item_id = self._resolve_key(key)
                if item_id is not None:
                    self._selected[item_id] = int(qty)
        elif isinstance(selected, list):
            for item in selected:
                if isinstance(item, (tuple, list)) and len(item) == 2:
                    item_id = self._resolve_key(item[0])
                    if item_id is not None:
                        self._selected[item_id] = int(item[1])
                else:
                    item_id = self._resolve_key(item)
                    if item_id is not None:
                        self._selected[item_id] = 1
        self._rebuild_selected_display()
        if self._dropdown is not None:
            self._refresh_dropdown()

    def _resolve_key(self, key):
        if key in self._all_items:
            return key
        for item_id, name in self._all_items.items():
            if name == key:
                return item_id
        return None

    # ── Dropdown logic ──────────────────────────────────────────────────

    def _install_window_filter(self):
        if not self._filter_installed:
            win = self.window()
            if win is not None:
                win.installEventFilter(self)
                self._filter_installed = True

    def _remove_window_filter(self):
        if self._filter_installed:
            try:
                win = self.window()
                if win is not None:
                    win.removeEventFilter(self)
            except RuntimeError:
                pass
            self._filter_installed = False

    def _open_dropdown(self):
        self._ensure_dropdown()
        self._refresh_dropdown()
        count = self._dropdown.count()
        if count == 0:
            self._dropdown.setVisible(False)
            return
        row_h = self._dropdown.sizeHintForRow(0) if count > 0 else 24
        h = row_h * min(count, 10) + 6
        self._dropdown.setFixedHeight(min(h, 320))
        self._position_dropdown()
        self._dropdown.raise_()
        self._dropdown.setVisible(True)
        self._install_window_filter()

    def _close_dropdown(self):
        if self._dropdown is not None:
            self._dropdown.setVisible(False)
        self._remove_window_filter()

    def _refresh_dropdown(self):
        dd = self._dropdown
        if dd is None:
            return
        text = self._search.text().strip().lower()
        dd.clear()
        for item_id, name in self._all_items.items():
            if item_id in self._selected:
                continue
            if text and text not in name.lower():
                continue
            li = QListWidgetItem(name)
            li.setData(Qt.ItemDataRole.UserRole, item_id)
            dd.addItem(li)

    def _on_search_changed(self, text: str):
        self._ensure_dropdown()
        self._refresh_dropdown()
        count = self._dropdown.count()
        if count == 0:
            self._dropdown.setVisible(False)
            return
        row_h = self._dropdown.sizeHintForRow(0) if count > 0 else 24
        h = row_h * min(count, 10) + 6
        self._dropdown.setFixedHeight(min(h, 320))
        self._position_dropdown()
        self._dropdown.raise_()
        self._dropdown.setVisible(True)

    def _on_item_clicked(self, item: QListWidgetItem):
        item_id = item.data(Qt.ItemDataRole.UserRole)
        if item_id is None:
            return
        self._selected[item_id] = 1
        self._search.clear()
        self._close_dropdown()
        self._rebuild_selected_display()
        if self._dropdown is not None:
            self._refresh_dropdown()
        self.selection_changed.emit()

    # ── Selected items display ──────────────────────────────────────────

    def _rebuild_selected_display(self):
        while self._selected_layout.count() > 1:
            item = self._selected_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for item_id, qty in list(self._selected.items()):
            name = self._all_items.get(item_id, f"#{item_id}")
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(2, 1, 2, 1)
            row_layout.setSpacing(4)

            lbl = QLabel(name)
            lbl.setMinimumWidth(120)
            row_layout.addWidget(lbl, 1)

            if self._show_quantity:
                spin = _NoScrollSpinBox()
                spin.setRange(1, 20)
                spin.setValue(qty)
                spin.setFixedWidth(64)
                spin.valueChanged.connect(
                    lambda val, iid=item_id: self._on_qty_changed(iid, val)
                )
                row_layout.addWidget(spin)

            remove_btn = QPushButton("✕")
            remove_btn.setFixedSize(24, 24)
            remove_btn.setStyleSheet("padding: 0px; font-size: 12px;")
            remove_btn.clicked.connect(
                lambda _, iid=item_id: self._on_remove(iid)
            )
            row_layout.addWidget(remove_btn)

            self._selected_layout.insertWidget(self._selected_layout.count() - 1, row)

    def _on_qty_changed(self, item_id, value: int):
        self._selected[item_id] = value
        self.selection_changed.emit()

    def _on_remove(self, item_id):
        self._selected.pop(item_id, None)
        self._rebuild_selected_display()
        if self._dropdown is not None:
            self._refresh_dropdown()
        self.selection_changed.emit()
