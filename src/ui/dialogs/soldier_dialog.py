"""
SoldierDialog — create or edit a soldier record.

Fields:
  · Identity : real name, phone, Matrix ID, active toggle
  · Stats    : day pts, night pts, present days, reserve days (read-only)
  · Roles    : checkbox list seeded from the Role registry
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox,
    QGroupBox, QWidget, QScrollArea, QMessageBox,
)
from PyQt6.QtCore import Qt

from src.core.models import Soldier
from src.services.config_service import ConfigService
from src.services.soldier_service import SoldierService


def _display_name(s: Soldier) -> str:
    return s.name or f"#{s.id}"


class SoldierDialog(QDialog):
    """Modal dialog for creating or editing a soldier profile."""

    def __init__(self, db, soldier: Soldier | None = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.soldier = soldier
        self._is_new = soldier is None

        self.setModal(True)
        self.setMinimumSize(500, 520)
        title = "NEW SOLDIER" if self._is_new else f"EDIT — {_display_name(soldier)}"
        self.setWindowTitle(title)

        self._setup_ui()
        if not self._is_new:
            self._load_data()

    # ─────────────────────────────────────────────── UI construction ──── #

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        title_lbl = QLabel(
            "NEW SOLDIER" if self._is_new else f"EDIT — {_display_name(self.soldier)}"
        )
        title_lbl.setObjectName("sectionHeader")
        root.addWidget(title_lbl)

        # ── Identity ──────────────────────────────────────────────────────────
        id_group = QGroupBox("IDENTITY")
        id_form  = QFormLayout(id_group)
        id_form.setSpacing(7)

        self._name     = QLineEdit(); self._name.setPlaceholderText("Full real name")
        self._phone    = QLineEdit(); self._phone.setPlaceholderText("+1 555 0000")
        self._matrix_id = QLineEdit(); self._matrix_id.setPlaceholderText("Matrix ID (e.g. @wolf:server)")
        self._active   = QCheckBox("Active in current rotation")
        self._active.setChecked(True)

        id_form.addRow("Real name *", self._name)
        id_form.addRow("Phone",       self._phone)
        id_form.addRow("Matrix ID",   self._matrix_id)
        id_form.addRow("",            self._active)
        root.addWidget(id_group)

        # ── Stats (read-only, hidden for new soldiers) ────────────────────────
        self._stats_group = QGroupBox("STATS  (read-only)")
        stats_form = QFormLayout(self._stats_group)
        stats_form.setSpacing(5)
        self._lbl_day_pts      = QLabel("—")
        self._lbl_night_pts    = QLabel("—")
        self._lbl_present_days = QLabel("—")
        self._lbl_reserve_days = QLabel("—")
        stats_form.addRow("Day +/−:",   self._lbl_day_pts)
        stats_form.addRow("Night +/−:", self._lbl_night_pts)
        stats_form.addRow("Present days:", self._lbl_present_days)
        stats_form.addRow("Reserve days:", self._lbl_reserve_days)
        self._stats_group.setVisible(not self._is_new)
        root.addWidget(self._stats_group)

        # ── Roles (searchable scrollable checkboxes) ──────────────────────────
        roles_group = QGroupBox("ROLES")
        roles_outer = QVBoxLayout(roles_group)
        roles_outer.setContentsMargins(4, 4, 4, 4)
        roles_outer.setSpacing(4)

        self._role_search = QLineEdit()
        self._role_search.setPlaceholderText("Search roles…")
        self._role_search.setClearButtonEnabled(True)
        self._role_search.textChanged.connect(self._filter_roles)
        roles_outer.addWidget(self._role_search)

        roles_scroll = QScrollArea()
        roles_scroll.setWidgetResizable(True)
        roles_scroll.setMaximumHeight(180)
        roles_inner = QWidget()
        roles_vbox  = QVBoxLayout(roles_inner)
        roles_vbox.setSpacing(3)
        roles_vbox.setContentsMargins(6, 4, 6, 4)

        self._role_checks: dict[str, QCheckBox] = {}
        for role in ConfigService(self.db).list_roles_for_picker():
            cb = QCheckBox(role.name)
            if role.description:
                cb.setToolTip(role.description)
            self._role_checks[role.name] = cb
            roles_vbox.addWidget(cb)
        roles_vbox.addStretch()

        roles_scroll.setWidget(roles_inner)
        roles_outer.addWidget(roles_scroll)
        root.addWidget(roles_group)

        root.addStretch()

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn     = QPushButton("[ CANCEL ]")
        self._save_btn = QPushButton("[ SAVE SOLDIER ]")
        cancel_btn.clicked.connect(self.reject)
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._save_btn)
        root.addLayout(btn_row)

    # ──────────────────────────────────── role search filter ──── #

    def _filter_roles(self, text: str):
        text = text.lower().strip()
        for name, cb in self._role_checks.items():
            visible = text == "" or text in name.lower()
            if cb.isChecked():
                visible = True
            cb.setVisible(visible)

    # ─────────────────────────────────────────────── data loading ──── #

    def _load_data(self):
        s = self.soldier

        self._name.setText(s.name or "")
        self._phone.setText(s.phone_number or "")
        self._matrix_id.setText(s.matrix_id or "")
        self._active.setChecked(bool(s.is_active_in_kav))

        self._lbl_day_pts.setText(f"{s.total_day_points:.2f}")
        self._lbl_night_pts.setText(f"{s.total_night_points:.2f}")
        self._lbl_present_days.setText(f"{s.present_days_count:.1f}")
        self._lbl_reserve_days.setText(str(s.active_reserve_days))

        for role_name in (s.role or []):
            if role_name in self._role_checks:
                self._role_checks[role_name].setChecked(True)

    # ─────────────────────────────────────────────── save ──────────── #

    def _on_save(self):
        name = self._name.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation Error", "Real name is required.")
            self._name.setFocus()
            return

        selected_roles = [rn for rn, cb in self._role_checks.items() if cb.isChecked()]

        svc = SoldierService(self.db)
        if self._is_new:
            s = svc.create_soldier(
                name=name,
                phone_number=self._phone.text().strip() or None,
                roles=selected_roles,
                matrix_id=self._matrix_id.text().strip() or None,
                is_active_in_kav=self._active.isChecked(),
            )
            self.soldier = s
        else:
            svc.update_soldier(
                self.soldier.id,
                name=name,
                phone_number=self._phone.text().strip() or None,
                matrix_id=self._matrix_id.text().strip() or None,
                is_active_in_kav=self._active.isChecked(),
                role=selected_roles,
            )

        self.accept()
