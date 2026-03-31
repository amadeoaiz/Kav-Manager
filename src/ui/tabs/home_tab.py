"""
Home Tab — Mission Calendar (left) + Schedule Viewer (right) + Active Now (bottom).
"""
import csv
import os
from datetime import datetime, date, time, timedelta

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QGridLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QSizePolicy, QScrollArea, QFileDialog, QMessageBox, QFrame,
    QSplitter, QDialog,
)
from PyQt6.QtCore import Qt, QDate, pyqtSignal, QTimer, QEvent
from PyQt6.QtGui import QColor
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog

from src.core.models import Task, TaskAssignment, Soldier, MissionRequirement
from src.services.config_service import ConfigService
from src.services.soldier_service import SoldierService
from src.services.task_service import TaskService
from src.domain.presence_rules import is_full_day_present
from src.services.readiness_service import (
    get_active_now,
    get_day_readiness,
    get_day_schedule,
)
from src.ui.stylesheet import CELL_COLORS


# ── helpers ───────────────────────────────────────────────────────────────────

def _display_name(soldier: Soldier | None) -> str:
    if soldier is None:
        return "—"
    return soldier.name or f"#{soldier.id}"


def _commander_display_name(db, config) -> str:
    """Return commander display name from config (by soldier id or legacy fallback)."""
    return SoldierService(db).get_commander_display_name(config)


def _task_label(task: Task | None) -> str:
    if task is None:
        return "—"
    return task.real_title or f"Task#{task.id}"


# ── Mission Calendar widget ────────────────────────────────────────────────────

class MissionCalendar(QWidget):
    day_selected        = pyqtSignal(object)          # emits a datetime.date (single click)
    range_selected      = pyqtSignal(object, object)  # emits (date_from, date_to) on Shift+click
    requirement_changed = pyqtSignal()                # emitted after requirement create/edit via context menu

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._config_svc = ConfigService(db)
        self._task_svc = TaskService(db)
        self._month = date.today().replace(day=1)
        self._selected = date.today()
        self._range_start: date | None = None
        self._range_end:   date | None = None
        self._theme = 'dark'
        self._setup_ui()
        # Detect clicks outside this widget so we can clear the range selection.
        # Qt removes the filter automatically when this QObject is destroyed.
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

    def set_theme(self, theme: str):
        self._theme = theme
        self.refresh()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Header: section label
        hdr = QLabel("MISSION CALENDAR")
        hdr.setObjectName("sectionHeader")
        layout.addWidget(hdr)

        # Month navigation
        nav = QHBoxLayout()
        self._prev_btn = QPushButton("◄")
        self._prev_btn.setFixedSize(44, 32)
        self._prev_btn.setStyleSheet("padding: 2px; font-size: 18px;")
        self._next_btn = QPushButton("►")
        self._next_btn.setFixedSize(44, 32)
        self._next_btn.setStyleSheet("padding: 2px; font-size: 18px;")
        self._month_lbl = QLabel()
        self._month_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self._prev_btn)
        nav.addWidget(self._month_lbl, 1)
        nav.addWidget(self._next_btn)
        layout.addLayout(nav)

        # Day-of-week headers
        dow_row = QHBoxLayout()
        dow_row.setSpacing(2)
        for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
            lbl = QLabel(d)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setObjectName("dimLabel")
            lbl.setMinimumWidth(36)
            lbl.setMaximumWidth(56)
            dow_row.addWidget(lbl)
        layout.addLayout(dow_row)

        # Calendar grid
        self._grid = QGridLayout()
        self._grid.setSpacing(2)
        layout.addLayout(self._grid)

        # Legend (rebuilt on each refresh — see refresh())
        self._legend_layout = QHBoxLayout()
        self._legend_layout.setSpacing(12)
        layout.addLayout(self._legend_layout)

        layout.addStretch()

        self._prev_btn.clicked.connect(self._go_prev)
        self._next_btn.clicked.connect(self._go_next)
        self.refresh()

    def refresh(self):
        # Resolve theme first — everything else depends on it
        config = self._config_svc.get_config()
        theme = config.theme or 'dark'
        colors = CELL_COLORS[theme]
        today = date.today()

        # Clear existing day buttons
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Rebuild legend
        while self._legend_layout.count():
            item = self._legend_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        legend_entries = [
            ("Surplus (well above minimum)", "surplus"),
            ("OK (ready at minimum)", "ok"),
            ("Partial (not ready, almost there)", "partial"),
            ("Critical (not ready)", "critical"),
        ]
        for text, style in legend_entries:
            c = colors[style]
            bg_color, fg_color = c[0], c[1]
            lbl = QLabel(text)
            # Draw a coloured pill behind the text so the legend swatch is visible
            # in both themes.
            if theme == "light":
                text_color = "#1b2b1b"
            else:
                text_color = "#f0f0f0"
            lbl.setStyleSheet(
                f"background-color: {bg_color};"
                f"color: {text_color};"
                f"padding: 2px 8px;"
                f"border-radius: 4px;"
                f"font-size: 12px;"
            )
            self._legend_layout.addWidget(lbl)
        # Spacer to keep layout tidy
        self._legend_layout.addWidget(QLabel(""))

        self._month_lbl.setText(self._month.strftime("%B %Y").upper())

        first_dow = self._month.weekday()   # 0=Monday
        if self._month.month == 12:
            next_mo = self._month.replace(year=self._month.year + 1, month=1, day=1)
        else:
            next_mo = self._month.replace(month=self._month.month + 1, day=1)
        days_in_month = (next_mo - self._month).days

        row, col = 0, first_dow
        for day_num in range(1, days_in_month + 1):
            d = self._month.replace(day=day_num)
            info = get_day_readiness(self.db, d)
            status = info['status']
            bg, fg = colors.get(status, colors['empty'])
            # In dark theme, prefer high-contrast light text for day numbers and
            # keep the readiness semantics in the background/border colours.
            if theme == "dark":
                fg = "#f8f8f8"

            in_range = (self._range_start is not None and self._range_end is not None
                        and self._range_start <= d <= self._range_end)

            btn = QPushButton(str(day_num))
            btn.setMinimumSize(36, 32)
            btn.setMaximumSize(56, 44)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if d == today:
                border_color, border_width = colors['today_border'], 2
            elif d == self._selected:
                border_color, border_width = colors['selected_border'], 2
            elif in_range:
                border_color, border_width = '#ffd600', 2   # amber = range highlight
            else:
                border_color, border_width = bg, 1
            btn.setStyleSheet(
                f"background-color: {bg}; color: {fg}; font-weight: bold;"
                f"border: {border_width}px solid {border_color};"
                f"font-size: 13px; padding: 0px;"
            )
            tooltip = (
                f"{d.strftime('%a %d %b')}\n"
                f"Tasks: {info['task_count']}  "
                f"Required: {info['required']}  "
                f"Present: {info['present']}"
            )
            if in_range:
                tooltip += "\nShift-selected range"
            btn.setToolTip(tooltip)
            btn.clicked.connect(lambda _, _d=d: self._on_day_clicked(_d))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(lambda pos, _d=d, _b=btn: self._on_day_context_menu(_d, _b, pos))
            btn.setProperty('day_date', d.isoformat())
            btn.installEventFilter(self)
            self._grid.addWidget(btn, row, col)

            col += 1
            if col > 6:
                col = 0
                row += 1

    def _on_day_clicked(self, d: date):
        from PyQt6.QtWidgets import QApplication
        modifiers = QApplication.keyboardModifiers()
        if (modifiers & Qt.KeyboardModifier.ShiftModifier) and self._selected:
            self._range_start = min(self._selected, d)
            self._range_end   = max(self._selected, d)
            self.refresh()
            self.range_selected.emit(self._range_start, self._range_end)
        else:
            self._selected    = d
            self._range_start = None
            self._range_end   = None
            self.refresh()
            self.day_selected.emit(d)

    def get_range(self) -> tuple[date, date]:
        """Return the active selection as (from, to). Falls back to (selected, selected)."""
        if self._range_start and self._range_end:
            return self._range_start, self._range_end
        return self._selected, self._selected

    def _clear_range(self):
        """Clear the active range selection and re-emit day_selected to hide the hint label."""
        if self._range_start is None and self._range_end is None:
            return
        self._range_start = None
        self._range_end = None
        self.refresh()
        if self._selected:
            self.day_selected.emit(self._selected)

    def _on_day_context_menu(self, d: date, btn, pos):
        from PyQt6.QtWidgets import QMenu

        has_range = self._range_start is not None and self._range_end is not None

        menu = QMenu(self)

        if has_range:
            rs, re = self._range_start, self._range_end
            menu.addAction(
                f"Set requirements  ({rs.strftime('%d %b')} → {re.strftime('%d %b')})",
                lambda _s=rs, _e=re: self._open_req_dialog_new(_s, _e),
            )
        else:
            menu.addAction(
                f"Set requirements  ({d.strftime('%d %b')})",
                lambda: self._open_req_dialog_new(d, d),
            )
            if self._day_has_requirements(d):
                menu.addAction(
                    f"See requirements  ({d.strftime('%d %b')})",
                    lambda: self._open_req_info(d),
                )

        menu.exec(btn.mapToGlobal(pos))

    def _day_has_requirements(self, d: date) -> bool:
        return self._task_svc.day_has_requirements(d)

    def _open_req_info(self, d: date):
        dlg = _RequirementInfoDialog(self.db, d, parent=self)
        dlg.exec()

    def _open_req_dialog_new(self, date_from: date, date_to: date):
        from src.ui.dialogs.requirement_dialog import RequirementDialog
        dlg = RequirementDialog(
            self.db, requirement=None,
            prefill_date=date_from, prefill_to=date_to,
            parent=self,
        )
        if dlg.exec() == dlg.DialogCode.Accepted:
            self.refresh()
            self.requirement_changed.emit()

    def _on_day_double_click(self, d: date):
        """Double-click: show informative view if requirements exist, otherwise open new requirement dialog."""
        if self._day_has_requirements(d):
            self._open_req_info(d)
        else:
            self._open_req_dialog_new(d, d)

    def eventFilter(self, watched, event):
        """Handle double-click on day buttons for requirements."""
        if event.type() == QEvent.Type.MouseButtonDblClick:
            iso = watched.property('day_date') if hasattr(watched, 'property') else None
            if iso:
                d = date.fromisoformat(iso)
                self._on_day_double_click(d)
                return True
        return False

    def _go_prev(self):
        self._range_start = self._range_end = None
        if self._month.month == 1:
            self._month = self._month.replace(year=self._month.year - 1, month=12)
        else:
            self._month = self._month.replace(month=self._month.month - 1)
        self.refresh()

    def _go_next(self):
        self._range_start = self._range_end = None
        if self._month.month == 12:
            self._month = self._month.replace(year=self._month.year + 1, month=1)
        else:
            self._month = self._month.replace(month=self._month.month + 1)
        self.refresh()


# ── Day detail panel ──────────────────────────────────────────────────────────

class DayDetailPanel(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._current_date = date.today()
        self._req_date_from: date = date.today()
        self._req_date_to:   date = date.today()
        self._setup_ui()

    def set_range(self, date_from: date, date_to: date):
        """Called by HomeTab when a multi-day range is selected in the calendar."""
        self._req_date_from = date_from
        self._req_date_to   = date_to
        if date_from == date_to:
            self._range_hint_lbl.setVisible(False)
        else:
            self._range_hint_lbl.setText(
                f"Range: {date_from.strftime('%d %b')} → {date_to.strftime('%d %b')}  "
                f"({(date_to - date_from).days + 1} days)  — Shift+click to extend"
            )
            self._range_hint_lbl.setVisible(True)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._date_lbl = QLabel("Select a day")
        self._date_lbl.setObjectName("sectionHeader")
        layout.addWidget(self._date_lbl)

        self._stats_lbl = QLabel()
        self._stats_lbl.setWordWrap(True)
        layout.addWidget(self._stats_lbl)

        self._task_list = QLabel()
        self._task_list.setWordWrap(True)
        self._task_list.setObjectName("dimLabel")
        layout.addWidget(self._task_list)

        # Requirements section
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        req_hdr_row = QHBoxLayout()
        req_hdr_lbl = QLabel("REQUIREMENTS FOR THIS DAY")
        req_hdr_lbl.setObjectName("dimLabel")
        req_hdr_row.addWidget(req_hdr_lbl)
        req_hdr_row.addStretch()
        self._edit_req_btn = QPushButton("[ + SET ]")
        self._edit_req_btn.setFixedWidth(90)
        self._edit_req_btn.clicked.connect(self._on_edit_requirements)
        req_hdr_row.addWidget(self._edit_req_btn)
        layout.addLayout(req_hdr_row)

        self._range_hint_lbl = QLabel()
        self._range_hint_lbl.setObjectName("dimLabel")
        self._range_hint_lbl.setStyleSheet("color: #ffd600; font-size: 12px;")
        self._range_hint_lbl.setWordWrap(True)
        self._range_hint_lbl.setVisible(False)
        layout.addWidget(self._range_hint_lbl)

        self._req_lbl = QLabel("No requirements set.")
        self._req_lbl.setWordWrap(True)
        self._req_lbl.setObjectName("dimLabel")
        layout.addWidget(self._req_lbl)

        self._manage_btn = QPushButton("[ MANAGE ALL REQUIREMENTS ]")
        self._manage_btn.clicked.connect(self._on_manage_all)
        layout.addWidget(self._manage_btn)

        layout.addStretch()

    def load(self, d: date):
        self._current_date  = d
        self._req_date_from = d
        self._req_date_to   = d
        info = get_day_readiness(self.db, d)
        self._date_lbl.setText(d.strftime("%A, %d %B %Y").upper())

        if info['status'] == 'empty':
            self._stats_lbl.setText("No active tasks scheduled.")
            self._task_list.setText("")
        else:
            gap = info['required'] - info['present']
            if gap > 0:
                gap_text = f"  SHORT {abs(gap)} soldier(s)"
            elif gap < 0:
                gap_text = f"  (surplus {abs(gap)})"
            else:
                gap_text = "  Exactly covered"
            color_map = {'surplus': '#00e676', 'ok': '#4caf50',
                         'partial': '#ffd600', 'critical': '#ff1744', 'empty': '#888888'}
            color = color_map.get(info['status'], '#888888')
            self._stats_lbl.setText(
                f"<span style='color:{color};font-weight:bold'>"
                f"{info['status'].upper()}</span><br>"
                f"Tasks: {info['task_count']} &nbsp;|&nbsp; "
                f"Required: {info['required']} &nbsp;|&nbsp; "
                f"Present: {info['present']}{gap_text}"
            )
            task_lines = [
                f"• {_task_label(t)}  [{'UNCOVERED' if (t.coverage_status or 'OK') == 'PARTIAL' else (t.coverage_status or 'OK')}]"
                for t in info['tasks']
            ]
            self._task_list.setText("\n".join(task_lines))

        # Requirements block
        reqs  = info.get('requirements', {})
        min_s = reqs.get('min_soldiers', 0)
        roles = reqs.get('required_roles', {})   # dict {role: count}
        labels = reqs.get('labels', [])
        if isinstance(roles, list):              # legacy compat
            roles = {r: 1 for r in roles}
        if min_s == 0 and not roles:
            self._req_lbl.setText("None set for this day.")
        else:
            lines = []
            if labels:
                lines.append("  ".join(labels))
            if min_s:
                lines.append(f"Min. soldiers: {min_s}")
            if roles:
                parts = [f"{rn}×{cnt}" if cnt > 1 else rn for rn, cnt in roles.items() if rn != "Soldier"]
                roles_str = ", ".join(parts) if parts else "Any"
                lines.append(f"Required roles: {roles_str}")
            self._req_lbl.setText("\n".join(lines))

    def _on_edit_requirements(self):
        from src.ui.dialogs.requirement_dialog import RequirementDialog
        dlg = RequirementDialog(
            self.db,
            requirement=None,
            prefill_date=self._req_date_from,
            prefill_to=self._req_date_to,
            parent=self,
        )
        if dlg.exec() == dlg.DialogCode.Accepted:
            self.load(self._current_date)

    def _on_manage_all(self):
        _RequirementsManagerDialog(self.db, parent=self).exec()
        self.load(self._current_date)


# ── Requirements Manager (full list of all blocks) ────────────────────────────

class _RequirementsManagerDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._task_svc = TaskService(db)
        self.setWindowTitle("MISSION REQUIREMENTS")
        self.setModal(True)
        self.setMinimumSize(720, 480)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        hdr = QLabel("ALL REQUIREMENT BLOCKS")
        hdr.setObjectName("sectionHeader")
        layout.addWidget(hdr)

        hint = QLabel(
            "Each block defines minimum readiness for a date range. "
            "Overlapping blocks are merged (strictest rules apply). "
            "Double-click to edit."
        )
        hint.setObjectName("dimLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["LABEL", "FROM", "TO", "MIN SOLDIERS", "REQUIRED ROLES"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.doubleClicked.connect(self._on_edit)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._table, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        del_btn = QPushButton("[ DELETE SELECTED ]")
        del_btn.clicked.connect(self._on_delete)
        add_btn = QPushButton("[ + NEW BLOCK ]")
        add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(del_btn)
        btn_row.addWidget(add_btn)
        layout.addLayout(btn_row)

        close_btn = QPushButton("[ CLOSE ]")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self._reload()

    def _reload(self):
        reqs = self._task_svc.list_requirements()
        self._table.setRowCount(len(reqs))
        for row, r in enumerate(reqs):
            df = r.date_from.strftime('%d %b %Y') if r.date_from else '—'
            dt = r.date_to.strftime('%d %b %Y')   if r.date_to   else '—'
            rr = r.required_roles or {}
            if isinstance(rr, dict):
                parts = [f"{rn}×{cnt}" if cnt != 1 else rn for rn, cnt in rr.items() if rn != "Soldier"]
            else:
                parts = [x for x in rr if x != "Soldier"]
            roles_str = ', '.join(parts) if parts else 'Any'
            vals = [r.label or '—', df, dt, str(r.min_soldiers or 0), roles_str]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setData(Qt.ItemDataRole.UserRole, r.id)
                self._table.setItem(row, col, item)
        self._table.resizeColumnToContents(1)
        self._table.resizeColumnToContents(2)
        self._table.resizeColumnToContents(3)

    def _selected_req(self):
        rows = self._table.selectedItems()
        if not rows:
            return None
        rid = rows[0].data(Qt.ItemDataRole.UserRole)
        return self._task_svc.get_requirement(rid)

    def _on_add(self):
        from src.ui.dialogs.requirement_dialog import RequirementDialog
        dlg = RequirementDialog(self.db, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self._reload()

    def _on_edit(self, _index=None):
        req = self._selected_req()
        if not req:
            return
        from src.ui.dialogs.requirement_dialog import RequirementDialog
        dlg = RequirementDialog(self.db, requirement=req, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self._task_svc.expire_requirement(req)
            self._reload()

    def _on_delete(self):
        req = self._selected_req()
        if not req:
            QMessageBox.information(self, "Select", "Click a row first.")
            return
        ans = QMessageBox.question(
            self, "Delete", f"Delete requirement block '{req.label or 'unnamed'}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ans == QMessageBox.StandardButton.Yes:
            self._task_svc.delete_requirement_obj(req)
            self._reload()

    def _on_context_menu(self, pos):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.addAction("Edit",   lambda: self._on_edit(None))
        menu.addAction("Delete", self._on_delete)
        menu.exec(self._table.viewport().mapToGlobal(pos))


# ── Read-only requirement info dialog ─────────────────────────────────────

class _RequirementInfoDialog(QDialog):
    """Informative (read-only) view of requirements for a day, with readiness analysis."""

    def __init__(self, db, target_date: date, parent=None):
        super().__init__(parent)
        self.db = db
        self._date = target_date
        self.setWindowTitle(f"REQUIREMENTS — {target_date.strftime('%A, %d %b %Y').upper()}")
        self.setModal(True)
        self.setMinimumSize(560, 420)
        self._setup_ui()

    def _setup_ui(self):
        from src.services.readiness_service import get_day_requirements, get_day_readiness

        soldier_svc = SoldierService(self.db)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        hdr = QLabel(f"REQUIREMENTS — {self._date.strftime('%d %B %Y').upper()}")
        hdr.setObjectName("sectionHeader")
        layout.addWidget(hdr)

        merged = get_day_requirements(self.db, self._date)
        readiness = get_day_readiness(self.db, self._date)
        req_roles = merged.get("required_roles", {})
        min_soldiers = merged.get("min_soldiers", 0)
        labels = merged.get("labels", [])

        if min_soldiers == 0 and not req_roles:
            empty = QLabel("No requirement blocks cover this day.")
            empty.setObjectName("dimLabel")
            layout.addWidget(empty)
            close_btn = QPushButton("[ CLOSE ]")
            close_btn.clicked.connect(self.accept)
            layout.addWidget(close_btn)
            return

        # Requirement block labels
        if labels:
            lbl_text = QLabel("  |  ".join(labels))
            lbl_text.setWordWrap(True)
            lbl_text.setStyleSheet("font-weight: bold; font-size: 13px;")
            layout.addWidget(lbl_text)

        # ── Soldier headcount (match readiness_service semantics) ──────────
        present_count = readiness.get("present", 0)
        day_start = datetime.combine(self._date, time(0, 0, 0))
        day_end = datetime.combine(self._date, time(23, 59, 59))

        present_intervals = soldier_svc.get_all_presence_intervals(
            day_start, day_end, status="PRESENT",
        )

        by_soldier: dict[int, list] = {}
        for iv in present_intervals:
            by_soldier.setdefault(iv.soldier_id, []).append(iv)

        full_day_ids = [sid for sid, ivs in by_soldier.items()
                        if is_full_day_present(ivs, day_start, day_end)]
        active_soldiers = soldier_svc.list_active_soldiers()
        present_soldiers = [s for s in active_soldiers if s.id in full_day_ids]

        soldier_color = "#4caf50" if present_count >= min_soldiers else "#ff1744"
        headcount_lbl = QLabel(
            f"Soldiers:  <b>{present_count}</b> present  /  "
            f"<b>{min_soldiers}</b> required"
            f"{'  ✓' if present_count >= min_soldiers else f'  — SHORT {min_soldiers - present_count}'}"
        )
        headcount_lbl.setTextFormat(Qt.TextFormat.RichText)
        headcount_lbl.setStyleSheet(f"color: {soldier_color}; font-size: 13px;")
        layout.addWidget(headcount_lbl)

        # ── Roles table (show exactly what is missing) ─────────────────────
        role_counts: dict[str, int] = {}
        for s in present_soldiers:
            for r in (s.role or []):
                role_counts[r] = role_counts.get(r, 0) + 1

        filtered_roles = {rn: cnt for rn, cnt in req_roles.items() if rn != "Soldier"}

        if filtered_roles:
            sep1 = QFrame()
            sep1.setFrameShape(QFrame.Shape.HLine)
            layout.addWidget(sep1)

            roles_hdr = QLabel("REQUIRED ROLES")
            roles_hdr.setObjectName("dimLabel")
            layout.addWidget(roles_hdr)

            role_table = QTableWidget()
            role_table.setColumnCount(4)
            role_table.setHorizontalHeaderLabels(["ROLE", "NEEDED", "HAVE", "STATUS"])
            role_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for c in (1, 2, 3):
                role_table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
            role_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            role_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            role_table.verticalHeader().setVisible(False)
            role_table.setAlternatingRowColors(True)
            role_table.setRowCount(len(filtered_roles))

            for row, (role_name, needed) in enumerate(sorted(filtered_roles.items())):
                have = role_counts.get(role_name, 0)
                if have >= needed:
                    status_text = "OK"
                    status_color = QColor("#4caf50")
                else:
                    status_text = f"MISSING {needed - have}"
                    status_color = QColor("#ff1744")

                items = [
                    QTableWidgetItem(role_name),
                    QTableWidgetItem(str(needed)),
                    QTableWidgetItem(str(have)),
                    QTableWidgetItem(status_text),
                ]
                for col, item in enumerate(items):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if col == 3:
                        item.setForeground(status_color)
                    role_table.setItem(row, col, item)

            role_table.resizeRowsToContents()
            h = role_table.horizontalHeader().height() + 4
            for r in range(role_table.rowCount()):
                h += role_table.rowHeight(r)
            role_table.setFixedHeight(min(h, 260))
            layout.addWidget(role_table)

        # ── Readiness verdict ──────────────────────────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep2)

        status = readiness.get("status", "empty")
        problems: list[str] = []

        if present_count < min_soldiers:
            problems.append(
                f"Soldier headcount is below minimum "
                f"({present_count} present, {min_soldiers} required)."
            )

        for rn, needed in filtered_roles.items():
            have = role_counts.get(rn, 0)
            if have < needed:
                problems.append(
                    f"Role \"{rn}\" — need {needed}, only {have} present."
                )

        uncovered_tasks = [
            t for t in readiness.get("tasks", [])
            if (t.coverage_status or "OK") in ("PARTIAL", "UNCOVERED")
        ]
        for t in uncovered_tasks:
            problems.append(
                f"Task \"{t.real_title or f'#{t.id}'}\" is UNCOVERED."
            )

        if not problems:
            verdict = QLabel("✓  MISSION READINESS MET")
            verdict.setObjectName("statusOK")
            verdict.setStyleSheet("font-size: 14px; font-weight: bold; padding: 4px 0;")
            layout.addWidget(verdict)
        else:
            verdict = QLabel("✗  MISSION READINESS NOT MET")
            verdict.setStyleSheet("color: #ff1744; font-size: 14px; font-weight: bold; padding: 4px 0;")
            layout.addWidget(verdict)
            # Problem bullets: use a darker amber for better contrast in light theme.
            for p in problems:
                issue = QLabel(f"  •  {p}")
                issue.setWordWrap(True)
                issue.setStyleSheet("color: #e6a817; font-size: 12px;")
                layout.addWidget(issue)

        layout.addStretch()
        close_btn = QPushButton("[ CLOSE ]")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


# ── Schedule viewer ───────────────────────────────────────────────────────────

class ScheduleViewer(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._config_svc = ConfigService(db)
        self._task_svc = TaskService(db)
        self._date = date.today()
        # Flat list of [task, time, soldiers, status] rows used by CSV/PDF exports
        self._export_data: list[list[str]] = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Section header
        hdr = QLabel("SCHEDULE")
        hdr.setObjectName("sectionHeader")
        layout.addWidget(hdr)

        # Date navigation
        nav = QHBoxLayout()
        self._prev_day = QPushButton("◄")
        self._prev_day.setFixedSize(44, 32)
        self._prev_day.setStyleSheet("padding: 2px; font-size: 18px;")
        self._next_day = QPushButton("►")
        self._next_day.setFixedSize(44, 32)
        self._next_day.setStyleSheet("padding: 2px; font-size: 18px;")
        self._today_btn = QPushButton("TODAY")
        self._today_btn.setFixedWidth(90)
        self._date_lbl = QLabel()
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self._prev_day)
        nav.addWidget(self._date_lbl, 1)
        nav.addWidget(self._today_btn)
        nav.addWidget(self._next_day)
        layout.addLayout(nav)

        # Scroll area that holds the per-task tables
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self._scroll_area, 1)

        # Export buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._print_btn = QPushButton("[ PRINT ]")
        self._csv_btn   = QPushButton("[ EXPORT CSV ]")
        self._pdf_btn   = QPushButton("[ EXPORT PDF ]")
        for b in (self._print_btn, self._csv_btn, self._pdf_btn):
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        self._prev_day.clicked.connect(lambda: self._shift_date(-1))
        self._next_day.clicked.connect(lambda: self._shift_date(1))
        self._today_btn.clicked.connect(self._go_today)
        self._print_btn.clicked.connect(self._do_print)
        self._csv_btn.clicked.connect(self._export_csv)
        self._pdf_btn.clicked.connect(self._export_pdf)

        self._refresh_table()

    def load_date(self, d: date):
        self._date = d
        self._refresh_table()

    def refresh(self):
        self._refresh_table()

    def _shift_date(self, delta: int):
        self._date += timedelta(days=delta)
        self._refresh_table()

    def _go_today(self):
        self._date = date.today()
        self._refresh_table()

    # ── core rebuild ──────────────────────────────────────────────────────────

    def _refresh_table(self):
        from collections import OrderedDict

        self._date_lbl.setText(self._date.strftime("%A  %d %B %Y").upper())
        assignments = get_day_schedule(self.db, self._date)
        self._export_data = []

        # Outer container placed inside the scroll area
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(4, 4, 4, 8)
        content_layout.setSpacing(14)

        if not assignments:
            no_data = QLabel("No assignments scheduled for this day.")
            no_data.setObjectName("dimLabel")
            no_data.setAlignment(Qt.AlignmentFlag.AlignCenter)
            content_layout.addWidget(no_data)
        else:
            status_colors = {'OK': '#4caf50', 'UNCOVERED': '#ff1744'}

            # Group assignments by task (preserving time-sorted order)
            tasks_dict: dict[int, dict] = OrderedDict()
            for asgn in assignments:
                tid = asgn.task_id
                if tid not in tasks_dict:
                    tasks_dict[tid] = {'task': asgn.task, 'assignments': []}
                tasks_dict[tid]['assignments'].append(asgn)

            for group in tasks_dict.values():
                task            = group['task']
                task_assignments = group['assignments']

                raw_status   = task.coverage_status or "OK"
                status       = 'UNCOVERED' if raw_status == 'PARTIAL' else raw_status
                status_color = status_colors.get(status, '#888888')
                task_name    = _task_label(task)

                # Task section header
                hdr_lbl = QLabel(
                    f"▶  {task_name}"
                    f"&nbsp;&nbsp;<span style='color:{status_color}; font-size:12px;'>"
                    f"[{status}]</span>"
                )
                hdr_lbl.setObjectName("sectionHeader")
                hdr_lbl.setTextFormat(Qt.TextFormat.RichText)
                content_layout.addWidget(hdr_lbl)

                # Group rotation slots by (start_time, end_time) — soldiers sharing the
                # same window appear on one row
                rotation_groups: dict[tuple, list] = OrderedDict()
                for asgn in task_assignments:
                    key = (asgn.start_time, asgn.end_time)
                    rotation_groups.setdefault(key, []).append(asgn.soldier)

                # Per-task table: TIME | SOLDIERS | STATUS
                table = QTableWidget()
                table.setColumnCount(3)
                table.setHorizontalHeaderLabels(["TIME", "SOLDIERS", "STATUS"])
                table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
                table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
                table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
                table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
                table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
                table.verticalHeader().setVisible(False)
                table.setAlternatingRowColors(True)
                table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                table.setRowCount(len(rotation_groups))

                for row, ((start_t, end_t), soldiers) in enumerate(rotation_groups.items()):
                    time_str     = f"{start_t.strftime('%H:%M')} – {end_t.strftime('%H:%M')}"
                    soldiers_str = ' / '.join(
                        _display_name(s) for s in soldiers if s is not None
                    ) or '—'

                    row_items = [
                        QTableWidgetItem(time_str),
                        QTableWidgetItem(soldiers_str),
                        QTableWidgetItem(status),
                    ]
                    for col, item in enumerate(row_items):
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        if col == 2:
                            item.setForeground(QColor(status_color))
                        # Store task_id, start_time, end_time for click handling
                        item.setData(Qt.ItemDataRole.UserRole, (task.id, start_t, end_t))
                        table.setItem(row, col, item)

                    self._export_data.append([task_name, time_str, soldiers_str, status])

                table.cellDoubleClicked.connect(
                    lambda r, c, tbl=table: self._on_schedule_row_clicked(tbl, r)
                )

                # Size the table to show all rows without a scrollbar
                table.resizeRowsToContents()
                table.resizeColumnToContents(0)
                table.resizeColumnToContents(2)
                h = table.horizontalHeader().height() + 4
                for r in range(table.rowCount()):
                    h += table.rowHeight(r)
                table.setFixedHeight(h)

                content_layout.addWidget(table)

        content_layout.addStretch()
        self._scroll_area.setWidget(content)

    # ── block edit ─────────────────────────────────────────────────────────────

    def _on_schedule_row_clicked(self, table, row):
        item = table.item(row, 0)
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        task_id, start_t, end_t = data
        task = self._task_svc.get_task(task_id)
        if not task:
            return
        from src.ui.dialogs.block_edit_dialog import BlockEditDialog
        dlg = BlockEditDialog(self.db, task, start_t, end_t, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted and dlg.changed:
            self._refresh_table()

    # ── export helpers ────────────────────────────────────────────────────────

    def _rows_as_data(self) -> list[list[str]]:
        """Flat rows [task, time, soldiers, status] for CSV / PDF exports."""
        return self._export_data

    def _export_csv(self):
        config = self._config_svc.get_config()
        unit = config.unit_codename or "UNIT"
        default_name = f"{unit}_schedule_{self._date.strftime('%Y%m%d')}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", default_name, "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([f"Unit: {unit}", f"Date: {self._date.strftime('%d %b %Y')}"])
                writer.writerow(["TASK", "TIME", "SOLDIERS", "STATUS"])
                writer.writerows(self._rows_as_data())
            QMessageBox.information(self, "Export CSV", f"Saved to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))

    def _export_pdf(self):
        config = self._config_svc.get_config()
        unit = config.unit_codename or "UNIT"
        commander = _commander_display_name(self.db, config) if config else "ACTUAL"
        default_name = f"{unit}_schedule_{self._date.strftime('%Y%m%d')}.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Export PDF", default_name, "PDF (*.pdf)")
        if not path:
            return
        self._print_to_device(path, unit, commander, to_pdf=True)

    def _do_print(self):
        config = self._config_svc.get_config()
        unit = config.unit_codename or "UNIT"
        commander = _commander_display_name(self.db, config) if config else "ACTUAL"
        self._print_to_device(None, unit, commander, to_pdf=False)

    def _print_to_device(self, pdf_path: str | None, unit: str, commander: str, to_pdf: bool):
        from PyQt6.QtGui import QPainter, QFont
        from PyQt6.QtCore import QRect

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageSize(printer.pageSize())

        if to_pdf and pdf_path:
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(pdf_path)
        else:
            dlg = QPrintDialog(printer, self)
            if dlg.exec() != QPrintDialog.DialogCode.Accepted:
                return

        painter = QPainter()
        if not painter.begin(printer):
            QMessageBox.critical(self, "Print Error", "Could not open printer.")
            return

        try:
            page = printer.pageRect(QPrinter.Unit.DevicePixel)
            W, H = int(page.width()), int(page.height())
            margin = int(W * 0.06)
            x, y = margin, margin

            font_title = QFont("Consolas", 16, QFont.Weight.Bold)
            painter.setFont(font_title)
            painter.drawText(x, y + 20, f"KavManager — {unit}")
            font_sub = QFont("Consolas", 11)
            painter.setFont(font_sub)
            y += 40
            painter.drawText(x, y + 16, f"Schedule: {self._date.strftime('%A, %d %B %Y').upper()}")
            y += 32
            painter.drawLine(x, y, W - margin, y)
            y += 20

            cols  = ["TASK", "TIME", "SOLDIERS", "STATUS"]
            col_w = [int(W * p) for p in [0.28, 0.16, 0.42, 0.10]]
            row_h = 28

            font_hdr = QFont("Consolas", 9, QFont.Weight.Bold)
            painter.setFont(font_hdr)
            cx = x
            for i, col in enumerate(cols):
                painter.drawText(QRect(cx, y, col_w[i], row_h),
                                 Qt.AlignmentFlag.AlignCenter, col)
                cx += col_w[i]
            y += row_h
            painter.drawLine(x, y, W - margin, y)
            y += 4

            font_row = QFont("Consolas", 9)
            painter.setFont(font_row)
            for data_row in self._rows_as_data():
                if y + row_h > H - margin * 2:
                    printer.newPage()
                    y = margin
                cx = x
                for i, cell in enumerate(data_row[:4]):
                    painter.drawText(QRect(cx, y, col_w[i], row_h),
                                     Qt.AlignmentFlag.AlignCenter, cell)
                    cx += col_w[i]
                y += row_h

            y = H - margin - 40
            painter.drawLine(x, y, x + 250, y)
            y += 14
            painter.setFont(font_sub)
            painter.drawText(x, y + 14, f"Signed: {commander}")

        finally:
            painter.end()

        if to_pdf and pdf_path:
            QMessageBox.information(self, "Export PDF", f"Saved to:\n{pdf_path}")


# ── Active Now strip ──────────────────────────────────────────────────────────

class ActiveNowStrip(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(16)

        dot = QLabel("◉")
        dot.setStyleSheet("color: #00e676; font-size: 14px;")
        layout.addWidget(dot)

        self._lbl = QLabel("No active tasks right now.")
        self._lbl.setStyleSheet("font-size: 13px; letter-spacing: 1px;")
        layout.addWidget(self._lbl, 1)

        self.setFixedHeight(42)
        self.setObjectName("activeNowStrip")
        self.refresh()

    def refresh(self):
        assignments = get_active_now(self.db)
        if not assignments:
            self._lbl.setText("No active tasks right now.")
            return
        parts = []
        for asgn in assignments:
            task = asgn.task
            soldier = asgn.soldier
            parts.append(f"{_task_label(task)}: {_display_name(soldier)}")
        self._lbl.setText("  |  ".join(parts))


# ── Home Tab ──────────────────────────────────────────────────────────────────

class HomeTab(QWidget):
    def __init__(self, db, main_window):
        super().__init__()
        self.db = db
        self.mw = main_window
        self._setup_ui()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Main split
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left pane: calendar + day detail ──────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)
        left_layout.setSpacing(6)

        self._calendar = MissionCalendar(self.db)
        self._calendar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left_layout.addWidget(self._calendar, 3)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("border: 1px solid #1a3a1a;")
        left_layout.addWidget(sep)

        self._day_detail = DayDetailPanel(self.db)
        left_layout.addWidget(self._day_detail, 2)

        splitter.addWidget(left)

        # ── Right pane: schedule viewer ────────────────────────────────────────
        self._schedule = ScheduleViewer(self.db)
        splitter.addWidget(self._schedule)

        # Keep the mission calendar + detail to at most ~half the width initially
        splitter.setSizes([600, 600])
        outer.addWidget(splitter, 1)

        # ── Bottom strip: Active Now ───────────────────────────────────────────
        self._active_now = ActiveNowStrip(self.db)
        outer.addWidget(self._active_now)

        # Connect calendar → schedule sync
        self._calendar.day_selected.connect(self._on_day_selected)
        self._calendar.range_selected.connect(self._on_range_selected)
        self._calendar.requirement_changed.connect(
            lambda: self._day_detail.load(self._calendar._selected)
        )
        # Seed detail for today
        self._day_detail.load(date.today())

    def _on_day_selected(self, d: date):
        self._day_detail.set_range(d, d)
        self._day_detail.load(d)
        self._schedule.load_date(d)

    def _on_range_selected(self, date_from: date, date_to: date):
        self._day_detail.set_range(date_from, date_to)
        self._schedule.load_date(date_from)

    def refresh(self):
        self._calendar.refresh()
        self._schedule.refresh()
        self._active_now.refresh()
        # Re-load day detail for currently selected date
        self._day_detail.load(self._calendar._selected)
