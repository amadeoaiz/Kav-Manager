"""
Reusable UI widgets shared across dialogs.
"""
from PyQt6.QtWidgets import (
    QScrollArea, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QSpinBox, QLineEdit,
)
from PyQt6.QtCore import Qt, QEvent

class NoScrollSpinBox(QSpinBox):
    """QSpinBox that ignores scroll-wheel events to prevent accidental changes."""

    def wheelEvent(self, event):
        event.ignore()


class RoleQuantityWidget(QWidget):
    """
    Filterable list showing every role (except 'Soldier') with a QSpinBox.
    Includes a search bar at the top to filter roles by name.
    Spinbox value 0 = not required; ≥1 = that many of this role required.

    get_value()  → dict[str, int]   (only roles with count > 0)
    set_value()  → accepts dict[str,int] or list[str] (legacy)
    """

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._spinboxes: dict[str, QSpinBox] = {}
        self._role_widgets: dict[str, QWidget] = {}
        self._build(db)

    def _build(self, db):
        from src.services.config_service import ConfigService

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search roles…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter)
        outer.addWidget(self._search)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # Allow a taller roles list; parent dialogs will constrain overall size.
        scroll.setMaximumHeight(300)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(4)
        layout.setContentsMargins(6, 4, 6, 4)

        roles = ConfigService(db).list_roles_for_picker()

        for role in roles:
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            lbl = QLabel(role.name)
            lbl.setMinimumWidth(190)
            spin = NoScrollSpinBox()
            spin.setRange(0, 20)
            spin.setValue(0)
            spin.setFixedWidth(64)
            if role.description:
                spin.setToolTip(role.description)
                lbl.setToolTip(role.description)
            self._spinboxes[role.name] = spin
            self._role_widgets[role.name] = row_widget
            row_layout.addWidget(lbl)
            row_layout.addWidget(spin)
            row_layout.addStretch()
            layout.addWidget(row_widget)

        layout.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

    def _filter(self, text: str):
        text = text.lower().strip()
        for name, widget in self._role_widgets.items():
            visible = text == "" or text in name.lower()
            if self._spinboxes[name].value() > 0:
                visible = True
            widget.setVisible(visible)

    def get_value(self) -> dict[str, int]:
        """Returns {role_name: count} for roles where count > 0."""
        return {
            name: spin.value()
            for name, spin in self._spinboxes.items()
            if spin.value() > 0
        }

    def set_value(self, value):
        """
        Loads existing values.
        Accepts dict[str, int] (new format) or list[str] (legacy, treats each as count=1).
        """
        if isinstance(value, list):
            value = {v: 1 for v in value}
        for name, count in (value or {}).items():
            if name in self._spinboxes:
                self._spinboxes[name].setValue(int(count))
