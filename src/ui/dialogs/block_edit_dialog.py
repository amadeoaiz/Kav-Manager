"""
Block Edit Dialog — lets the commander manually edit soldier assignments
for a specific task time block.  Includes a Soldier Picker sub-dialog.
"""
from datetime import datetime
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QWidget,
    QMessageBox, QScrollArea, QFrame, QAbstractItemView,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from sqlalchemy.orm import Session

from src.core.models import Task, TaskAssignment, Soldier
from src.services.schedule_service import ScheduleService
from src.services.soldier_service import SoldierService
from src.domain.task_rules import task_roles_list, format_task_roles_display


# ── helpers ──────────────────────────────────────────────────────────────────

def _soldier_name(s: Soldier | None) -> str:
    if s is None:
        return "—"
    return s.name or f"#{s.id}"


def _role_annotation(soldier: Soldier, task: Task) -> str:
    """Return role annotation if soldier has a role matching any task requirement."""
    required = set(task_roles_list(task)) - {"Soldier"}
    if not required:
        return ""
    soldier_roles = set(soldier.role or [])
    matching = soldier_roles & required
    if matching:
        return f" ({', '.join(sorted(matching))})"
    return ""


def _is_night_block(start_time: datetime, night_start: int = 23, night_end: int = 7) -> bool:
    h = start_time.hour
    if night_end <= night_start:
        return h >= night_start or h < night_end
    return night_start <= h < night_end


def _soldier_present_during(soldier: Soldier, block_start: datetime, block_end: datetime) -> bool:
    """Check if soldier has any PRESENT interval overlapping the block."""
    for pi in (soldier.presence or []):
        if pi.status != "PRESENT":
            continue
        if pi.start_time < block_end and pi.end_time > block_start:
            return True
    return False


def _overlapping_assignments(db: Session, soldier_id: int,
                              block_start: datetime, block_end: datetime,
                              exclude_task_id: int | None = None) -> list[TaskAssignment]:
    """Find assignments for soldier that overlap the block time window."""
    return ScheduleService(db).get_overlapping_assignments(
        soldier_id, block_start, block_end, exclude_task_id,
    )


# ── Soldier Picker Sub-Dialog ────────────────────────────────────────────────

class SoldierPickerDialog(QDialog):
    """Shows available and swap-candidate soldiers for a block assignment."""

    def __init__(self, db: Session, task: Task,
                 block_start: datetime, block_end: datetime,
                 current_soldier_ids: list[int],
                 prioritize_role: str | None = None,
                 parent=None):
        super().__init__(parent)
        self.db = db
        self.task = task
        self.block_start = block_start
        self.block_end = block_end
        self.current_soldier_ids = set(current_soldier_ids)
        self.prioritize_role = prioritize_role
        self._selected_soldier_id: int | None = None
        self._selected_swap_assignment: TaskAssignment | None = None

        self.setWindowTitle("Select Soldier")
        self.setModal(True)
        self.setMinimumSize(480, 400)

        self._setup_ui()
        self._populate()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(8)

        hint = QLabel("Select a soldier to assign to this block.")
        hint.setObjectName("dimLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["", "SOLDIER", "ROLES", "HOURS ±"])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(0, 0)  # hidden section marker column
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setColumnHidden(0, True)
        self._table.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self._table, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("[ CANCEL ]")
        cancel_btn.clicked.connect(self.reject)
        select_btn = QPushButton("[ SELECT ]")
        select_btn.clicked.connect(self._on_select)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(select_btn)
        layout.addLayout(btn_row)

    def _populate(self):
        is_night = _is_night_block(self.block_start)

        # Compute hours stats for all active soldiers.
        all_soldiers = SoldierService(self.db).list_active_soldiers()

        # Compute per-soldier hours in the relevant domain.
        hours_map: dict[int, float] = {}
        for s in all_soldiers:
            total = 0.0
            for a in (s.assignments or []):
                a_is_night = _is_night_block(a.start_time)
                if a_is_night == is_night:
                    total += (a.end_time - a.start_time).total_seconds() / 3600.0
            hours_map[s.id] = total

        avg_hours = sum(hours_map.values()) / max(len(hours_map), 1)

        # Split soldiers into available vs assigned-elsewhere.
        available: list[tuple[Soldier, float]] = []
        swap_candidates: list[tuple[Soldier, float, TaskAssignment]] = []

        for s in all_soldiers:
            if s.id in self.current_soldier_ids:
                continue
            if not _soldier_present_during(s, self.block_start, self.block_end):
                continue

            excess = hours_map.get(s.id, 0.0) - avg_hours
            overlaps = _overlapping_assignments(
                self.db, s.id, self.block_start, self.block_end,
                exclude_task_id=self.task.id,
            )
            if overlaps:
                # Pick the first overlapping assignment as the swap target.
                swap_candidates.append((s, excess, overlaps[0]))
            else:
                available.append((s, excess))

        # Sort available by lowest excess first (fairest pick at top).
        available.sort(key=lambda x: x[1])
        swap_candidates.sort(key=lambda x: x[1])

        # If prioritizing a role, sort that role's holders to the top within each section.
        if self.prioritize_role and self.prioritize_role != "Soldier":
            def _has_role(soldier: Soldier) -> bool:
                return self.prioritize_role in (soldier.role or [])
            available.sort(key=lambda x: (0 if _has_role(x[0]) else 1, x[1]))
            swap_candidates.sort(key=lambda x: (0 if _has_role(x[0]) else 1, x[1]))

        # Build table rows.
        rows = []

        # Section header: Available
        if available:
            rows.append(("__header__", "Available", "", "", None))
            if self.prioritize_role and self.prioritize_role != "Soldier":
                has_role = [x for x in available if self.prioritize_role in (x[0].role or [])]
                no_role = [x for x in available if self.prioritize_role not in (x[0].role or [])]
                if has_role:
                    rows.append(("__subheader__", f"Has required role: {self.prioritize_role}", "", "", None))
                    for s, excess in has_role:
                        rows.append(("available", s, excess, None, None))
                    if no_role:
                        rows.append(("__subheader__", "Other soldiers", "", "", None))
                for s, excess in no_role:
                    rows.append(("available", s, excess, None, None))
                if not has_role:
                    for s, excess in available:
                        rows.append(("available", s, excess, None, None))
            else:
                for s, excess in available:
                    rows.append(("available", s, excess, None, None))

        # Section header: Assigned elsewhere
        if swap_candidates:
            rows.append(("__header__", "Assigned elsewhere (swap)", "", "", None))
            for s, excess, swap_asgn in swap_candidates:
                rows.append(("swap", s, excess, swap_asgn, None))

        self._table.setRowCount(len(rows))
        self._row_data: list[tuple] = []

        for row_idx, row_data in enumerate(rows):
            kind = row_data[0]

            if kind in ("__header__", "__subheader__"):
                label = row_data[1]
                item = QTableWidgetItem(label)
                font = item.font()
                font.setBold(True)
                if kind == "__subheader__":
                    font.setPointSize(font.pointSize() - 1)
                item.setFont(font)
                item.setForeground(QColor("#4caf50"))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self._table.setItem(row_idx, 1, item)
                for c in (0, 2, 3):
                    empty = QTableWidgetItem("")
                    empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                    self._table.setItem(row_idx, c, empty)
                self._table.setSpan(row_idx, 1, 1, 3)
                self._row_data.append(None)
                continue

            soldier = row_data[1]
            excess = row_data[2]
            swap_asgn = row_data[3]

            # Hidden marker column stores soldier id.
            id_item = QTableWidgetItem(str(soldier.id))
            self._table.setItem(row_idx, 0, id_item)

            name_text = _soldier_name(soldier)
            name_item = QTableWidgetItem(name_text)
            self._table.setItem(row_idx, 1, name_item)

            roles_text = ", ".join(r for r in (soldier.role or []) if r != "Soldier") or "—"
            roles_item = QTableWidgetItem(roles_text)
            self._table.setItem(row_idx, 2, roles_item)

            sign = "+" if excess >= 0 else ""
            hours_text = f"{sign}{excess:.1f}h"
            hours_item = QTableWidgetItem(hours_text)
            hours_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if excess > 0:
                hours_item.setForeground(QColor("#ff9800"))
            elif excess < 0:
                hours_item.setForeground(QColor("#4caf50"))
            self._table.setItem(row_idx, 3, hours_item)

            if kind == "swap" and swap_asgn:
                # Show current assignment info in the name cell.
                task_obj = swap_asgn.task
                task_name = task_obj.real_title if task_obj else f"Task#{swap_asgn.task_id}"
                time_str = (f"{swap_asgn.start_time.strftime('%H:%M')}"
                            f"–{swap_asgn.end_time.strftime('%H:%M')}")
                name_item.setText(f"{name_text}  [{task_name} {time_str}]")

            self._row_data.append((kind, soldier, swap_asgn))

    def _on_double_click(self, index):
        self._on_select()

    def _on_select(self):
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        row_idx = rows[0].row()
        if row_idx >= len(self._row_data) or self._row_data[row_idx] is None:
            return

        kind, soldier, swap_asgn = self._row_data[row_idx]

        if kind == "swap" and swap_asgn:
            task_obj = swap_asgn.task
            task_name = task_obj.real_title if task_obj else f"Task#{swap_asgn.task_id}"
            time_str = (f"{swap_asgn.start_time.strftime('%H:%M')}"
                        f"–{swap_asgn.end_time.strftime('%H:%M')}")
            ans = QMessageBox.question(
                self, "Confirm Swap",
                f"This will assign {_soldier_name(soldier)} here and move "
                f"the current soldier to {task_name} {time_str}.\n\nConfirm?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
            self._selected_swap_assignment = swap_asgn

        self._selected_soldier_id = soldier.id
        self.accept()

    @property
    def selected_soldier_id(self) -> int | None:
        return self._selected_soldier_id

    @property
    def swap_assignment(self) -> TaskAssignment | None:
        return self._selected_swap_assignment


# ── Block Edit Dialog ────────────────────────────────────────────────────────

class BlockEditDialog(QDialog):
    """Dialog for editing soldier assignments on a specific task time block."""

    def __init__(self, db: Session, task: Task,
                 block_start: datetime, block_end: datetime,
                 parent=None):
        super().__init__(parent)
        self.db = db
        self.task = task
        self.block_start = block_start
        self.block_end = block_end
        self._changed = False

        self.setWindowTitle("Edit Assignment Block")
        self.setModal(True)
        self.setMinimumSize(500, 380)

        self._setup_ui()
        self._refresh()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        # Header: task name + time window
        task_name = self.task.real_title or f"Task#{self.task.id}"
        time_str = (f"{self.block_start.strftime('%H:%M')}"
                    f" – {self.block_end.strftime('%H:%M')}")
        if self.block_start.date() != self.block_end.date():
            time_str = (f"{self.block_start.strftime('%d %b %H:%M')}"
                        f" – {self.block_end.strftime('%d %b %H:%M')}")

        hdr = QLabel(f"{task_name}  {time_str}")
        hdr.setObjectName("sectionHeader")
        layout.addWidget(hdr)

        # Subheader: required info
        roles = task_roles_list(self.task)
        concurrent = len(roles) or 1
        roles_display = format_task_roles_display(
            self.task.required_roles_list, self.task.required_count or 1
        )
        sub = QLabel(f"Required: {concurrent} soldier(s)  |  Roles: {roles_display}")
        sub.setObjectName("dimLabel")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("border: 1px solid #1a3a1a;")
        layout.addWidget(sep)

        # Assigned soldiers table
        self._soldiers_table = QTableWidget()
        self._soldiers_table.setColumnCount(3)
        self._soldiers_table.setHorizontalHeaderLabels(["SOLDIER", "", ""])
        th = self._soldiers_table.horizontalHeader()
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        th.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        th.resizeSection(1, 100)
        th.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        th.resizeSection(2, 40)
        self._soldiers_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._soldiers_table.verticalHeader().setVisible(False)
        layout.addWidget(self._soldiers_table, 1)

        # Add soldier button
        add_btn = QPushButton("[ + ADD SOLDIER ]")
        add_btn.clicked.connect(self._on_add)
        layout.addWidget(add_btn)

        # Warning strip
        self._warning_lbl = QLabel()
        self._warning_lbl.setWordWrap(True)
        self._warning_lbl.setStyleSheet(
            "color: #ffd600; background: #3a2a00; padding: 6px; border-radius: 4px;"
        )
        self._warning_lbl.setVisible(False)
        layout.addWidget(self._warning_lbl)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("[ CANCEL ]")
        cancel_btn.clicked.connect(self.reject)
        confirm_btn = QPushButton("[ CONFIRM ]")
        confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(confirm_btn)
        layout.addLayout(btn_row)

    def _get_block_assignments(self) -> list[TaskAssignment]:
        """Get all assignments for this task that overlap the block window."""
        return ScheduleService(self.db).get_block_assignments(
            self.task.id, self.block_start, self.block_end,
        )

    def _refresh(self):
        """Rebuild the soldiers table and update warnings."""
        assignments = self._get_block_assignments()
        self._soldiers_table.setRowCount(len(assignments))

        for row, asgn in enumerate(assignments):
            soldier = asgn.soldier
            name = _soldier_name(soldier)
            annotation = _role_annotation(soldier, self.task) if soldier else ""

            name_item = QTableWidgetItem(f"{name}{annotation}")
            if asgn.is_pinned:
                name_item.setForeground(QColor("#64b5f6"))  # blue tint for pinned
            self._soldiers_table.setItem(row, 0, name_item)

            # Change button
            change_btn = QPushButton("CHANGE")
            change_btn.setStyleSheet("padding: 2px 8px; font-size: 11px;")
            change_btn.clicked.connect(
                lambda _, a=asgn: self._on_change(a)
            )
            self._soldiers_table.setCellWidget(row, 1, change_btn)

            # Remove button
            remove_btn = QPushButton("✕")
            remove_btn.setStyleSheet("padding: 2px 6px; font-size: 11px; color: #ff1744;")
            remove_btn.clicked.connect(
                lambda _, a=asgn: self._on_remove(a)
            )
            self._soldiers_table.setCellWidget(row, 2, remove_btn)

        self._update_warnings(assignments)

    def _update_warnings(self, assignments: list[TaskAssignment]):
        """Update the warning strip based on current state."""
        roles_required = task_roles_list(self.task)
        required_count = len(roles_required) or 1
        assigned_count = len(assignments)

        warnings = []

        if assigned_count < required_count:
            warnings.append(
                f"Undercovered: {assigned_count} of {required_count} required soldiers assigned"
            )
        elif assigned_count > required_count:
            warnings.append(
                f"Overcovered: {assigned_count} of {required_count} required soldiers assigned"
            )

        # Check for missing required roles (non-Soldier roles).
        specific_roles = [r for r in roles_required if r != "Soldier"]
        if specific_roles:
            assigned_roles: set[str] = set()
            for asgn in assignments:
                if asgn.soldier:
                    assigned_roles.update(asgn.soldier.role or [])

            for role in set(specific_roles):
                if role not in assigned_roles:
                    warnings.append(f"Missing role: {role}")

        if warnings:
            self._warning_lbl.setText("\n".join(warnings))
            self._warning_lbl.setVisible(True)
        else:
            self._warning_lbl.setVisible(False)

    def _current_soldier_ids(self) -> list[int]:
        return [a.soldier_id for a in self._get_block_assignments()]

    def _on_change(self, assignment: TaskAssignment):
        """Open soldier picker to replace the soldier on this assignment."""
        soldier = assignment.soldier
        # Determine if this soldier holds a required role for priority sorting.
        prioritize_role = None
        if soldier:
            required = set(task_roles_list(self.task)) - {"Soldier"}
            soldier_roles = set(soldier.role or [])
            matching = soldier_roles & required
            if matching:
                prioritize_role = next(iter(matching))

        dlg = SoldierPickerDialog(
            self.db, self.task,
            self.block_start, self.block_end,
            self._current_soldier_ids(),
            prioritize_role=prioritize_role,
            parent=self,
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        if dlg.selected_soldier_id is None:
            return

        new_soldier_id = dlg.selected_soldier_id

        sched_svc = ScheduleService(self.db)
        if dlg.swap_assignment is not None:
            # Swap: exchange soldier_ids on both assignments.
            other_asgn = dlg.swap_assignment
            sched_svc.swap_block_assignments(assignment, other_asgn)
        else:
            sched_svc.change_assignment_soldier(assignment, new_soldier_id)

        self._changed = True
        self._refresh()

    def _on_remove(self, assignment: TaskAssignment):
        """Remove a soldier from this block."""
        soldier = assignment.soldier
        name = _soldier_name(soldier)
        ans = QMessageBox.question(
            self, "Remove Soldier",
            f"Remove {name} from this block?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        ScheduleService(self.db).remove_assignment(assignment)
        self._changed = True
        self._refresh()

    def _on_add(self):
        """Add a new soldier to this block."""
        dlg = SoldierPickerDialog(
            self.db, self.task,
            self.block_start, self.block_end,
            self._current_soldier_ids(),
            parent=self,
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        if dlg.selected_soldier_id is None:
            return

        new_soldier_id = dlg.selected_soldier_id

        sched_svc = ScheduleService(self.db)
        if dlg.swap_assignment is not None:
            # Swap: the picked soldier is currently assigned elsewhere.
            # Pull them here; remove their old assignment (no replacement when adding).
            other_asgn = dlg.swap_assignment

            # Find any existing assignment in this block to use as template for weight.
            existing = self._get_block_assignments()
            if existing:
                weight = existing[0].final_weight_applied
            else:
                duration_hours = (self.block_end - self.block_start).total_seconds() / 3600.0
                weight = (self.task.base_weight or 1.0) * duration_hours

            sched_svc.add_assignment(
                self.task.id, new_soldier_id,
                self.block_start, self.block_end, weight,
            )
            sched_svc.remove_assignment(other_asgn)
        else:
            # Simple add: create a new assignment row.
            duration_hours = (self.block_end - self.block_start).total_seconds() / 3600.0
            weight = (self.task.base_weight or 1.0) * duration_hours

            sched_svc.add_assignment(
                self.task.id, new_soldier_id,
                self.block_start, self.block_end, weight,
            )

        self._changed = True
        self._refresh()

    def _on_confirm(self):
        if self._changed:
            ScheduleService(self.db).commit_block_edits()
        self.accept()

    @property
    def changed(self) -> bool:
        return self._changed
