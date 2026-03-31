"""
RequirementDialog — create or edit a MissionRequirement block.

A requirement defines, for a date range:
  · A human label (e.g. "Exercise ALPHA")
  · Minimum number of soldiers needed
  · Required roles (at least one of each must be present)
  · Optional notes for the commander
"""
import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QSpinBox,
    QDateEdit, QTextEdit, QMessageBox, QDialogButtonBox,
)
from PyQt6.QtCore import Qt, QDate

from src.core.models import MissionRequirement
from src.services.config_service import ConfigService
from src.services.task_service import TaskService
from src.ui.widgets.searchable_select import SearchableSelectWidget


class RequirementDialog(QDialog):
    """Modal to add or edit a MissionRequirement block."""

    def __init__(self, db, requirement=None, prefill_date=None, prefill_to=None, parent=None):
        super().__init__(parent)
        self.db = db
        self._task_svc = TaskService(db)
        self.req = requirement
        self._is_new = requirement is None

        self.setModal(True)
        self.setMinimumSize(520, 480)
        self.setWindowTitle("EDIT REQUIREMENT" if not self._is_new else "NEW REQUIREMENT BLOCK")

        self._setup_ui()
        if self._is_new:
            if prefill_date:
                qd_from = QDate(prefill_date.year, prefill_date.month, prefill_date.day)
                self._from_date.setDate(qd_from)
            if prefill_to:
                qd_to = QDate(prefill_to.year, prefill_to.month, prefill_to.day)
                self._to_date.setDate(qd_to)
            elif prefill_date:
                self._to_date.setDate(self._from_date.date())
        if not self._is_new:
            self._load()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        lbl = QLabel("MISSION REQUIREMENT BLOCK")
        lbl.setObjectName("sectionHeader")
        root.addWidget(lbl)

        hint = QLabel(
            "Define minimum readiness for a period. Multiple blocks can overlap — "
            "the engine will use the strictest requirements on each day."
        )
        hint.setObjectName("dimLabel")
        hint.setWordWrap(True)
        root.addWidget(hint)

        # Body split: left = period/label + notes, right = roles
        body = QHBoxLayout()
        body.setSpacing(12)

        # ── Basic info ──────────────────────────────────────────────────── #
        info_group = QGroupBox("PERIOD & LABEL")
        info_form = QFormLayout(info_group)
        info_form.setSpacing(8)

        self._label = QLineEdit()
        self._label.setPlaceholderText("e.g.  Exercise OMEGA  /  Weekend Duty")
        self._from_date = QDateEdit()
        self._from_date.setCalendarPopup(True)
        self._from_date.setDate(QDate.currentDate())
        self._to_date = QDateEdit()
        self._to_date.setCalendarPopup(True)
        self._to_date.setDate(QDate.currentDate())
        self._min_soldiers = QSpinBox()
        self._min_soldiers.setRange(0, 999)
        self._min_soldiers.setValue(1)
        self._min_soldiers.setSuffix("  soldiers")

        info_form.addRow("Label:",            self._label)
        info_form.addRow("From (inclusive):", self._from_date)
        info_form.addRow("To (inclusive):",   self._to_date)
        info_form.addRow("Min. soldiers:",    self._min_soldiers)

        left_col = QVBoxLayout()
        left_col.addWidget(info_group)

        # ── Notes ────────────────────────────────────────────────────────── #
        self._note = QTextEdit()
        self._note.setPlaceholderText("Optional note for the commander…")
        self._note.setMaximumHeight(90)
        left_col.addWidget(self._note)
        left_col.addStretch()

        body.addLayout(left_col, 1)

        # ── Required roles ───────────────────────────────────────────────── #
        roles_group = QGroupBox("REQUIRED ROLES")
        roles_outer = QVBoxLayout(roles_group)
        roles_outer.setContentsMargins(4, 4, 4, 4)
        self._roles_widget = SearchableSelectWidget(show_quantity=True)
        roles = ConfigService(self.db).list_roles_for_picker()
        self._roles_widget.set_items([(r.name, r.name) for r in roles])
        roles_outer.addWidget(self._roles_widget)
        body.addWidget(roles_group, 1)

        root.addLayout(body)

        # ── Buttons ──────────────────────────────────────────────────────── #
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _load(self):
        r = self.req
        self._label.setText(r.label or "")
        if r.date_from:
            d = r.date_from
            self._from_date.setDate(QDate(d.year, d.month, d.day))
        if r.date_to:
            d = r.date_to
            self._to_date.setDate(QDate(d.year, d.month, d.day))
        self._min_soldiers.setValue(r.min_soldiers or 1)
        self._roles_widget.set_selected(r.required_roles or {})
        self._note.setPlainText(r.note or "")

    def _on_save(self):
        fd = self._from_date.date().toPyDate()
        td = self._to_date.date().toPyDate()
        if td < fd:
            QMessageBox.warning(self, "Date error", "'To' date must be ≥ 'From' date.")
            return

        roles_selected = {name: qty for name, qty in self._roles_widget.get_selected()}

        df = datetime.datetime.combine(fd, datetime.time(0, 0, 0))
        dt = datetime.datetime.combine(td, datetime.time(23, 59, 59))

        if self._is_new:
            r = MissionRequirement(
                date_from=df,
                date_to=dt,
                label=self._label.text().strip() or None,
                min_soldiers=self._min_soldiers.value(),
                required_roles=roles_selected,
                note=self._note.toPlainText().strip() or None,
            )
            self._task_svc.save_requirement(r)
        else:
            r = self.req
            r.date_from = df
            r.date_to = dt
            r.label = self._label.text().strip() or None
            r.min_soldiers = self._min_soldiers.value()
            r.required_roles = roles_selected
            r.note = self._note.toPlainText().strip() or None
            self._task_svc.commit_requirement(r)
        self.accept()
