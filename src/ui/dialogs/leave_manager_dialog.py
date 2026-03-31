"""
LeaveManagerDialog — find and apply coverage solutions for a soldier's leave request.

Access via [ FIND LEAVE COVERAGE ] in SoldierDetailPanel.
Calls UnitManager.find_leave_solutions() for the backend logic.
"""
import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QDateEdit, QScrollArea,
    QWidget, QButtonGroup, QRadioButton, QGroupBox,
    QMessageBox, QFrame,
)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QColor

from src.core.models import Soldier
from src.core.unit_manager import (
    UnitManager, LeaveSolverResult, LeaveSolution, ReplacementSlot,
)


def _name(s: Soldier | None) -> str:
    if s is None:
        return "?"
    return s.name or f"#{s.id}"


class LeaveManagerDialog(QDialog):
    """Full leave coverage solver dialog."""

    def __init__(self, db, soldier: Soldier, main_window=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.soldier = soldier
        self.mw = main_window
        self._result: LeaveSolverResult | None = None
        self._solution_radios: list[tuple[QRadioButton, LeaveSolution]] = []
        self._btn_group = QButtonGroup(self)

        self.setModal(True)
        self.setMinimumSize(720, 580)
        self.setWindowTitle(f"LEAVE COVERAGE — {_name(soldier)}")

        self._setup_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        # Header
        hdr = QLabel(f"LEAVE COVERAGE SOLVER — {_name(self.soldier)}")
        hdr.setObjectName("sectionHeader")
        root.addWidget(hdr)

        # Date pickers
        date_grp = QGroupBox("LEAVE PERIOD")
        date_form = QFormLayout(date_grp)
        date_form.setSpacing(8)

        self._from_date = QDateEdit()
        self._from_date.setCalendarPopup(True)
        self._from_date.setDate(QDate.currentDate())
        self._from_date.setDisplayFormat("dd/MM/yyyy")
        self._to_date = QDateEdit()
        self._to_date.setCalendarPopup(True)
        self._to_date.setDate(QDate.currentDate().addDays(6))
        self._to_date.setDisplayFormat("dd/MM/yyyy")

        date_form.addRow("From (first absent day):", self._from_date)
        date_form.addRow("To   (last  absent day):", self._to_date)
        root.addWidget(date_grp)

        find_btn = QPushButton("[ FIND SOLUTIONS ]")
        find_btn.clicked.connect(self._on_find)
        root.addWidget(find_btn)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        # Results area (scrollable)
        self._results_scroll = QScrollArea()
        self._results_scroll.setWidgetResizable(True)
        self._results_inner = QWidget()
        self._results_layout = QVBoxLayout(self._results_inner)
        self._results_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._results_layout.setSpacing(6)
        self._results_scroll.setWidget(self._results_inner)
        root.addWidget(self._results_scroll, 1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("[ CANCEL ]")
        self._apply_btn = QPushButton("[ APPLY SELECTED PLAN ]")
        self._apply_btn.setEnabled(False)
        cancel_btn.clicked.connect(self.reject)
        self._apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._apply_btn)
        root.addLayout(btn_row)

        # Seed results with a hint
        self._set_results_hint("Choose a leave period and click FIND SOLUTIONS.")

    # ── actions ───────────────────────────────────────────────────────────────

    def _on_find(self):
        fd = self._from_date.date().toPyDate()
        td = self._to_date.date().toPyDate()
        if td < fd:
            QMessageBox.warning(self, "Date error", "'To' must be ≥ 'From'.")
            return

        um = UnitManager(self.db)
        self._result = um.find_leave_solutions(self.soldier.id, fd, td)
        self._populate_results(self._result)

    def _on_apply(self):
        selected = self._selected_solution()
        if selected is None:
            QMessageBox.information(self, "No selection", "Please select a plan first.")
            return

        fd = self._from_date.date().toPyDate()
        td = self._to_date.date().toPyDate()

        # Confirmation summary
        lines = [f"Apply leave for <b>{_name(self.soldier)}</b>  "
                 f"{fd.strftime('%d %b')} – {td.strftime('%d %b')}?", ""]
        for slot in selected.replacements:
            day_str = ", ".join(d.strftime("%d %b") for d in slot.days)
            lines.append(f"• <b>{_name(slot.soldier)}</b> covers: {day_str}")
            if slot.has_conflict:
                for note in slot.conflict_notes:
                    lines.append(f"  &nbsp;&nbsp;⚠ {note}")
        lines += ["", "This will update the presence grid and mark the schedule for recalculation."]

        ans = QMessageBox.question(
            self, "Confirm plan",
            "<br>".join(lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        um = UnitManager(self.db)
        msg = um.apply_leave_solution(selected, self.soldier.id, fd, td)
        if self.mw:
            # Mark schedule dirty and immediately refresh home tab so the
            # mission calendar reflects the new presence / readiness.
            self.mw.set_dirty(True)
            if hasattr(self.mw, "home_tab") and hasattr(self.mw.home_tab, "refresh"):
                self.mw.home_tab.refresh()
        QMessageBox.information(self, "Done", msg)
        self.accept()

    # ── results rendering ─────────────────────────────────────────────────────

    def _clear_results(self):
        while self._results_layout.count():
            item = self._results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._solution_radios.clear()
        # Reset button group
        for btn in self._btn_group.buttons():
            self._btn_group.removeButton(btn)
        self._apply_btn.setEnabled(False)

    def _set_results_hint(self, text: str):
        self._clear_results()
        lbl = QLabel(text)
        lbl.setObjectName("dimLabel")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._results_layout.addWidget(lbl)

    def _populate_results(self, result: LeaveSolverResult):
        self._clear_results()

        # Warnings
        for w in result.warnings:
            warn_lbl = QLabel(f"⚠  {w}")
            warn_lbl.setWordWrap(True)
            warn_lbl.setObjectName("dimLabel")
            self._results_layout.addWidget(warn_lbl)

        # Critical days summary
        if result.critical_days:
            crit_str = ", ".join(d.strftime("%d %b") for d in result.critical_days)
            self._results_layout.addWidget(
                QLabel(f"<b>Critical days:</b>  {crit_str}  ({len(result.critical_days)} of "
                       f"{((result.leave_to - result.leave_from).days + 1)} days)")
            )
        else:
            ok_lbl = QLabel("✓  No critical days — leave can be approved freely.")
            ok_lbl.setStyleSheet("color: #4caf50;")
            self._results_layout.addWidget(ok_lbl)
            return

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        self._results_layout.addWidget(sep)

        # Explain scoring once at the top: lower load = fairer distribution.
        hint = QLabel(
            "Load = total extra PRESENT-days assigned to replacements in this plan.\n"
            "Lower load usually means a fairer distribution of duty."
        )
        hint.setObjectName("dimLabel")
        hint.setWordWrap(True)
        self._results_layout.addWidget(hint)

        # SINGLE solutions
        if result.singles:
            hdr = QLabel("── SINGLE COVERAGE ──")
            hdr.setObjectName("dimLabel")
            self._results_layout.addWidget(hdr)
            for sol in result.singles:
                self._add_solution_card(sol)
        else:
            lbl = QLabel("No single-soldier solution found.")
            lbl.setObjectName("dimLabel")
            self._results_layout.addWidget(lbl)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        self._results_layout.addWidget(sep2)

        # SPLIT solutions
        if result.splits:
            hdr2 = QLabel("── SPLIT COVERAGE ──")
            hdr2.setObjectName("dimLabel")
            self._results_layout.addWidget(hdr2)
            for sol in result.splits:
                self._add_solution_card(sol)
        else:
            lbl2 = QLabel("No two-soldier split solution found.")
            lbl2.setObjectName("dimLabel")
            self._results_layout.addWidget(lbl2)

        self._results_layout.addStretch()

        if self._solution_radios:
            self._solution_radios[0][0].setChecked(True)
            self._apply_btn.setEnabled(True)

    def _add_solution_card(self, sol: LeaveSolution):
        card = QFrame()
        card.setStyleSheet(
            "QFrame { border: 1px solid #2e7d32; padding: 6px; margin: 2px; }"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(3)

        # Radio + title row
        title_row = QHBoxLayout()
        radio = QRadioButton()
        # Associate this radio with its card so we can style the selected plan,
        # but keep the actual circle hidden — selection is indicated by card
        # highlighting instead.
        radio.setProperty("solution_card", card)
        radio.setVisible(False)
        self._btn_group.addButton(radio)
        radio.toggled.connect(self._on_solution_toggled)
        self._solution_radios.append((radio, sol))

        title_parts = []
        for slot in sol.replacements:
            day_str = ", ".join(d.strftime("%d %b") for d in slot.days)
            title_parts.append(f"<b>{_name(slot.soldier)}</b> → {day_str}")
        title_lbl = QLabel("  |  ".join(title_parts))
        title_lbl.setTextFormat(Qt.TextFormat.RichText)
        title_row.addWidget(title_lbl)
        title_row.addStretch()

        score_lbl = QLabel(f"{sol.score:.1f} days load")
        score_lbl.setObjectName("dimLabel")
        title_row.addWidget(score_lbl)
        card_layout.addLayout(title_row)

        # Conflict flags
        for slot in sol.replacements:
            if slot.has_conflict:
                for note in slot.conflict_notes:
                    note_lbl = QLabel(f"  {note}")
                    note_lbl.setStyleSheet("color: #ffd600; font-size: 12px;")
                    card_layout.addWidget(note_lbl)

        # Make the whole card clickable to select this plan.
        old_mouse_press = card.mousePressEvent

        def _on_card_clicked(event, _radio=radio, _orig=old_mouse_press):
            _radio.setChecked(True)
            if _orig is not None:
                _orig(event)

        card.mousePressEvent = _on_card_clicked

        self._results_layout.addWidget(card)

    def _selected_solution(self) -> LeaveSolution | None:
        for radio, sol in self._solution_radios:
            if radio.isChecked():
                return sol
        return None

    def _on_solution_toggled(self, _checked: bool):
        """Update card highlighting and apply-button state when a plan is selected."""
        # Enable APPLY when any solution is selected
        self._apply_btn.setEnabled(self._selected_solution() is not None)

        # Strongly highlight the selected plan's card so it's obvious which one
        # is active, regardless of how the radio-circle indicator is rendered.
        for btn in self._btn_group.buttons():
            card = btn.property("solution_card")
            if not isinstance(card, QFrame):
                continue
            if btn.isChecked():
                card.setStyleSheet(
                    "QFrame {"
                    " border: 2px solid #00e676;"
                    " padding: 6px; margin: 2px;"
                    " background-color: rgba(0, 230, 118, 0.10);"
                    " }"
                )
            else:
                card.setStyleSheet(
                    "QFrame { border: 1px solid #2e7d32; padding: 6px; margin: 2px; }"
                )
