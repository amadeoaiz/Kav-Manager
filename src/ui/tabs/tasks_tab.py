"""
Tasks Tab — Browse tasks by day with a date navigator,
or switch to archive mode to see all tasks in a sortable list.
"""
from datetime import datetime, date, time, timedelta

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter,
    QSizePolicy, QFrame, QDialog, QListWidget, QListWidgetItem,
    QDialogButtonBox, QMessageBox, QApplication,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from src.core.models import Task, TaskAssignment, Soldier
from src.services.config_service import ConfigService
from src.services.schedule_service import ScheduleService
from src.services.soldier_service import SoldierService
from src.services.task_service import TaskService
from src.domain.task_rules import _task_roles_list, format_task_roles_display

_STRIP_STYLE = {
    "dark": (
        "font-size: 13px; font-weight: bold; padding: 6px 12px;"
        "background: #332d22; color: #c4b8a8; border-radius: 4px;"
    ),
    "light": (
        "font-size: 13px; font-weight: bold; padding: 6px 12px;"
        "background: #d0d8d0; color: #264c26; border-radius: 4px;"
    ),
}


def _task_label(t: Task) -> str:
    return t.real_title or f"Task#{t.id}"


def _soldier_name(s: Soldier | None) -> str:
    if s is None:
        return "—"
    return s.name or f"#{s.id}"


_STATUS_COLORS = {
    'OK':        '#4caf50',
    'UNCOVERED': '#ff1744',
}
_UNCOVERED_ROW_BG = QColor('#3a0a0a')


def _normalize_status(raw: str | None) -> str:
    s = (raw or 'OK').upper()
    return 'UNCOVERED' if s == 'PARTIAL' else s


def _format_window(t: Task, show_date: bool = False) -> str:
    s, e = t.start_time, t.end_time
    if not s or not e:
        return "?"
    if show_date:
        if s.date() == e.date():
            return f"{s.strftime('%d %b')}  {s.strftime('%H:%M')} – {e.strftime('%H:%M')}"
        return f"{s.strftime('%d %b %H:%M')} – {e.strftime('%d %b %H:%M')}"
    if s.date() == e.date():
        return f"{s.strftime('%H:%M')} – {e.strftime('%H:%M')}"
    return f"{s.strftime('%H:%M')} – {e.strftime('%H:%M')}+1"


class _SortableItem(QTableWidgetItem):
    """QTableWidgetItem with an explicit sort key for column sorting."""
    def __init__(self, text: str, sort_value=None):
        super().__init__(text)
        self._sv = sort_value if sort_value is not None else text

    def __lt__(self, other):
        if isinstance(other, _SortableItem):
            try:
                return self._sv < other._sv
            except TypeError:
                return str(self._sv) < str(other._sv)
        return super().__lt__(other)


# ── Swap picker dialog ───────────────────────────────────────────────────── #

class _SwapPickerDialog(QDialog):
    """Lets the user pick which soldier to swap when multiple share a time window."""

    def __init__(self, soldiers: list[tuple[int, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Soldier to Swap")
        self.setModal(True)
        self.setMinimumWidth(280)
        self._selected_id: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        hint = QLabel("Multiple soldiers share this time slot.\nSelect one to swap:")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._list = QListWidget()
        for asgn_id, name in soldiers:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, asgn_id)
            self._list.addItem(item)
        self._list.setCurrentRow(0)
        self._list.doubleClicked.connect(self._accept_selected)
        layout.addWidget(self._list)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept_selected)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _accept_selected(self):
        item = self._list.currentItem()
        if item:
            self._selected_id = item.data(Qt.ItemDataRole.UserRole)
            self.accept()

    def selected_assignment_id(self) -> int | None:
        return self._selected_id


# ── Detail panel (right side) ────────────────────────────────────────────── #

class TaskDetailPanel(QWidget):
    def __init__(self, db, main_window, parent=None):
        super().__init__(parent)
        self.db = db
        self.mw = main_window
        self._task_svc = TaskService(db)
        self._task_id = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self._title_lbl = QLabel("Select a task")
        self._title_lbl.setObjectName("sectionHeader")
        layout.addWidget(self._title_lbl)

        self._meta_lbl = QLabel()
        self._meta_lbl.setWordWrap(True)
        self._meta_lbl.setObjectName("dimLabel")
        layout.addWidget(self._meta_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("border: 1px solid #1a3a1a;")
        layout.addWidget(sep)

        slots_hdr = QLabel("ASSIGNED SLOTS")
        slots_hdr.setStyleSheet("font-weight: bold; letter-spacing: 1px; font-size: 12px;")
        layout.addWidget(slots_hdr)

        self._slots_table = QTableWidget()
        self._slots_table.setColumnCount(3)
        self._slots_table.setHorizontalHeaderLabels(["TIME", "SOLDIERS", ""])
        hdr = self._slots_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(2, 70)
        self._slots_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._slots_table.verticalHeader().setVisible(False)
        self._slots_table.cellDoubleClicked.connect(self._on_slot_double_click)
        layout.addWidget(self._slots_table, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._delete_btn = QPushButton("[ DELETE ]")
        self._delete_btn.setObjectName("dangerBtn")
        self._delete_btn.clicked.connect(self._on_delete)
        self._deactivate_btn = QPushButton("[ DEACTIVATE ]")
        self._deactivate_btn.clicked.connect(self._on_deactivate)
        self._edit_btn = QPushButton("[ EDIT TASK ]")
        self._edit_btn.clicked.connect(self._on_edit)
        btn_row.addWidget(self._delete_btn)
        btn_row.addWidget(self._deactivate_btn)
        btn_row.addWidget(self._edit_btn)
        layout.addLayout(btn_row)

    def load(self, task: Task):
        self._task_id = task.id
        code = _task_label(task)
        status = _normalize_status(task.coverage_status)
        color = _STATUS_COLORS.get(status, '#888888')

        self._title_lbl.setText(
            f"{code}  <span style='color:{color};font-size:12px'>[{status}]</span>"
        )
        roles_str = format_task_roles_display(task.required_roles_list, task.required_count or 1)
        window = _format_window(task, show_date=True)
        self._meta_lbl.setText(
            f"Real title: {task.real_title or '—'}\n"
            f"Window:  {window}\n"
            f"Roles: {roles_str}\n"
            f"Weight: {task.base_weight}   Readiness: {task.readiness_minutes} min"
        )

        assignments = self._task_svc.get_task_assignments(task.id)

        # Build sub-intervals at every unique boundary time from all
        # assignments, then collect the soldiers covering each sub-interval.
        # This correctly groups soldiers with different block boundaries
        # (e.g. Gabbay 04:00–05:00 and Gohar 04:00–06:00) into the same
        # display row for the slots they share.
        boundaries: set[datetime] = set()
        for a in assignments:
            boundaries.add(a.start_time)
            boundaries.add(a.end_time)
        sorted_boundaries = sorted(boundaries)

        # For each sub-interval, find all assignments that span it.
        interval_rows: list[tuple[datetime, datetime, list[TaskAssignment]]] = []
        for i in range(len(sorted_boundaries) - 1):
            iv_start = sorted_boundaries[i]
            iv_end = sorted_boundaries[i + 1]
            covering = [
                a for a in assignments
                if a.start_time <= iv_start and a.end_time >= iv_end
            ]
            if covering:
                interval_rows.append((iv_start, iv_end, covering))

        # Merge contiguous intervals with exactly the same soldier set
        # (same names, same count) into a single display row.
        def _names_for(asgns: list[TaskAssignment]) -> tuple[str, ...]:
            return tuple(sorted(_soldier_name(a.soldier) for a in asgns))

        merged_rows: list[tuple[datetime, datetime, list[TaskAssignment]]] = []
        current_start = current_end = None
        current_asgns: list[TaskAssignment] = []
        current_names: tuple[str, ...] = ()

        for iv_start, iv_end, covering in interval_rows:
            names = _names_for(covering)
            if current_start is not None and iv_start == current_end and names == current_names:
                current_end = iv_end
                current_asgns = covering  # keep latest covering list (same soldiers)
            else:
                if current_start is not None:
                    merged_rows.append((current_start, current_end, current_asgns))
                current_start, current_end = iv_start, iv_end
                current_asgns = covering
                current_names = names

        if current_start is not None:
            merged_rows.append((current_start, current_end, current_asgns))

        # Filter out ultra-short artefact windows (e.g. sub-5-minute slices
        # created by freeze-point splits) so the schedule table stays readable.
        display_rows: list[tuple[datetime, datetime, list[TaskAssignment]]] = []
        for start_t, end_t, group_asgns in merged_rows:
            if (end_t - start_t).total_seconds() < 5 * 60:
                continue
            display_rows.append((start_t, end_t, group_asgns))

        self._slots_table.setRowCount(len(display_rows))
        for row, (start_t, end_t, group_asgns) in enumerate(display_rows):
            time_str = (
                f"{start_t.strftime('%d %b %H:%M')}"
                f" – {end_t.strftime('%H:%M')}"
            )
            soldiers_str = " / ".join(
                sorted(_soldier_name(a.soldier) for a in group_asgns)
            ) or "—"

            time_item = QTableWidgetItem(time_str)
            time_item.setData(Qt.ItemDataRole.UserRole, (start_t, end_t))
            self._slots_table.setItem(row, 0, time_item)
            self._slots_table.setItem(row, 1, QTableWidgetItem(soldiers_str))

            swap_btn = QPushButton("SWAP")
            swap_btn.setStyleSheet("padding: 2px 8px; font-size: 11px;")
            asgn_list = [(a.id, _soldier_name(a.soldier)) for a in group_asgns]
            swap_btn.clicked.connect(
                lambda _, al=asgn_list: self._on_swap_group(al)
            )
            self._slots_table.setCellWidget(row, 2, swap_btn)

    def _on_slot_double_click(self, row, col):
        item = self._slots_table.item(row, 0)
        if not item or self._task_id is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        start_t, end_t = data
        task = self._task_svc.get_task(self._task_id)
        if not task:
            return
        from src.ui.dialogs.block_edit_dialog import BlockEditDialog
        dlg = BlockEditDialog(self.db, task, start_t, end_t, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted and dlg.changed:
            self.load(task)

    def _on_edit(self):
        if self._task_id is None:
            return
        task = self._task_svc.get_task(self._task_id)
        if not task:
            return
        from src.ui.dialogs.task_dialog import TaskDialog
        dlg = TaskDialog(self.db, task=task, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self._task_svc.expire_task(task)
            refreshed = self._task_svc.get_task(self._task_id)
            if refreshed:
                self.load(refreshed)
            self.mw.set_dirty(True)

    def _on_delete(self):
        if self._task_id is None:
            return
        task = self._task_svc.get_task(self._task_id)
        if not task:
            return
        from PyQt6.QtWidgets import QMessageBox
        ans = QMessageBox.question(
            self, "Delete task",
            f"Permanently delete '{task.real_title or f'Task#{task.id}'}'?\n"
            "All assignments for this task will also be removed. This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self._task_svc.delete_task(self._task_id)
        self._task_id = None
        self._title_lbl.setText("Select a task")
        self._meta_lbl.setText("")
        self._slots_table.setRowCount(0)
        parent = self.parent()
        while parent and not isinstance(parent, TasksTab):
            parent = parent.parent()
        if parent:
            parent._rebuild()
        self.mw.set_dirty(True)

    def _on_deactivate(self):
        if self._task_id is None:
            return
        task = self._task_svc.get_task(self._task_id)
        if not task:
            return
        from PyQt6.QtWidgets import QMessageBox
        ans = QMessageBox.question(
            self, "Deactivate task",
            f"Deactivate '{task.real_title or f'Task#{task.id}'}'?\n"
            "It will no longer appear in the schedule or be allocated.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self._task_svc.deactivate_task(self._task_id)
            self.mw.set_dirty(True)

    def _on_swap_group(self, asgn_list: list[tuple[int, str]]):
        if len(asgn_list) == 1:
            self._on_swap(asgn_list[0][0])
            return
        dlg = _SwapPickerDialog(asgn_list, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted and dlg.selected_assignment_id() is not None:
            self._on_swap(dlg.selected_assignment_id())

    def _on_swap(self, assignment_id: int):
        from PyQt6.QtWidgets import QInputDialog
        soldiers = SoldierService(self.db).list_active_soldiers()
        names = [s.name or f"#{s.id}" for s in soldiers]
        choice, ok = QInputDialog.getItem(self, "Swap Soldier", "Select new soldier:", names, 0, False)
        if not ok:
            return
        chosen = next((s for s in soldiers if (s.name or f"#{s.id}") == choice), None)
        if not chosen:
            return
        sched_svc = ScheduleService(self.db)
        msg = sched_svc.swap_assignment(assignment_id, chosen.id)
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.information(self, "Swap", msg)
        asgn = sched_svc.get_assignment(assignment_id)
        if asgn:
            self.load(asgn.task)


# ── Main tasks tab ────────────────────────────────────────────────────────── #

class TasksTab(QWidget):
    def __init__(self, db, main_window):
        super().__init__()
        self.db = db
        self._config_svc = ConfigService(db)
        self._task_svc = TaskService(db)
        self.mw = main_window
        self._current_date = date.today()
        self._archive_mode = False
        self._setup_ui()

    def _setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: task list ──────────────────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(6)

        hdr_row = QHBoxLayout()
        hdr = QLabel("TASKS")
        hdr.setObjectName("sectionHeader")
        hdr_row.addWidget(hdr)
        hdr_row.addStretch()
        self._add_btn = QPushButton("[ + NEW TASK ]")
        hdr_row.addWidget(self._add_btn)
        left_layout.addLayout(hdr_row)

        # Navigation bar
        nav_bar = QHBoxLayout()
        nav_bar.setSpacing(4)

        self._prev_btn = QPushButton("◀")
        self._prev_btn.setFixedWidth(32)
        self._prev_btn.setStyleSheet("padding: 4px 0px;")
        self._prev_btn.clicked.connect(self._go_prev)

        self._date_lbl = QLabel()
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._date_lbl.setStyleSheet("font-weight: bold; font-size: 13px;")

        self._next_btn = QPushButton("▶")
        self._next_btn.setFixedWidth(32)
        self._next_btn.setStyleSheet("padding: 4px 0px;")
        self._next_btn.clicked.connect(self._go_next)

        self._today_btn = QPushButton("TODAY")
        self._today_btn.setStyleSheet("padding: 4px 10px; font-size: 11px;")
        self._today_btn.clicked.connect(self._go_today)

        self._mode_btn = QPushButton("ARCHIVE")
        self._mode_btn.setCheckable(True)
        self._mode_btn.setStyleSheet("padding: 4px 10px; font-size: 11px;")
        self._mode_btn.clicked.connect(self._toggle_mode)

        nav_bar.addWidget(self._prev_btn)
        nav_bar.addWidget(self._date_lbl, 1)
        nav_bar.addWidget(self._next_btn)
        nav_bar.addWidget(self._today_btn)
        nav_bar.addSpacing(8)
        nav_bar.addWidget(self._mode_btn)

        left_layout.addLayout(nav_bar)

        # ── Unit workload strip ────────────────────────────────────────────
        self._workload_lbl = QLabel()
        self._workload_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self._workload_lbl)

        # Task table
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(
            ["NAME", "WINDOW", "REQ.", "STATUS"]
        )
        th = self._table.horizontalHeader()
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, 4):
            th.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        left_layout.addWidget(self._table, 1)

        # Task count label
        self._count_lbl = QLabel()
        self._count_lbl.setObjectName("dimLabel")
        left_layout.addWidget(self._count_lbl)

        splitter.addWidget(left)

        # ── Right: task detail ───────────────────────────────────────────
        self._detail = TaskDetailPanel(self.db, self.mw)
        splitter.addWidget(self._detail)

        splitter.setSizes([600, 440])
        outer.addWidget(splitter)

        self._table.itemSelectionChanged.connect(self._on_selection)
        self._table.doubleClicked.connect(self._on_row_double_click)
        self._add_btn.clicked.connect(self._on_add_task)

        self._update_date_label()
        self._rebuild()

    # ── Date navigation ──────────────────────────────────────────────────

    def _update_date_label(self):
        d = self._current_date
        t = date.today()
        if d == t:
            tag = "  (today)"
        elif d == t - timedelta(days=1):
            tag = "  (yesterday)"
        elif d == t + timedelta(days=1):
            tag = "  (tomorrow)"
        else:
            tag = ""
        self._date_lbl.setText(d.strftime("%A, %d %b %Y") + tag)

    def _go_prev(self):
        self._current_date -= timedelta(days=1)
        self._update_date_label()
        self._rebuild()

    def _go_next(self):
        self._current_date += timedelta(days=1)
        self._update_date_label()
        self._rebuild()

    def _go_today(self):
        self._current_date = date.today()
        self._update_date_label()
        self._rebuild()

    def _toggle_mode(self):
        self._archive_mode = self._mode_btn.isChecked()
        if self._archive_mode:
            self._mode_btn.setText("DAILY")
            self._prev_btn.setVisible(False)
            self._next_btn.setVisible(False)
            self._today_btn.setVisible(False)
            self._date_lbl.setText("ALL TASKS — click column headers to sort")
            self._date_lbl.setStyleSheet("font-weight: bold; font-size: 12px;")
            self._table.setSortingEnabled(True)
        else:
            self._mode_btn.setText("ARCHIVE")
            self._prev_btn.setVisible(True)
            self._next_btn.setVisible(True)
            self._today_btn.setVisible(True)
            self._date_lbl.setStyleSheet("font-weight: bold; font-size: 13px;")
            self._table.setSortingEnabled(False)
            self._update_date_label()
        self._rebuild()

    # ── Table population ─────────────────────────────────────────────────

    def _rebuild(self):
        if self._archive_mode:
            self._rebuild_archive()
        else:
            self._rebuild_daily()
        self._update_workload_strip()

    def _update_workload_strip(self):
        """Compute and display unit-level day/night weighted avg hours per soldier."""
        from collections import defaultdict
        from src.domain.presence_calc import (
            compute_domain_fractions, count_present_partial, weighted_avg_sd,
        )

        config = self._config_svc.get_config()
        ns = config.night_start_hour or 23
        ne = config.night_end_hour or 7

        ref = self._current_date
        day_start = datetime.combine(ref, time.min)
        day_end = day_start + timedelta(days=1)
        ns_dt = datetime.combine(ref, time(ns, 0))
        ne_dt = datetime.combine(ref + timedelta(days=1), time(ne, 0))

        active_soldiers = SoldierService(self.db).list_active_soldiers()
        soldier_ids = [s.id for s in active_soldiers]

        sched_svc = ScheduleService(self.db)
        all_asgns = sched_svc.get_assignments_for_soldiers(
            soldier_ids, day_start, day_end,
        )

        per_sol_day: dict[int, float] = defaultdict(float)
        per_sol_night: dict[int, float] = defaultdict(float)
        step = timedelta(minutes=15)
        for a in all_asgns:
            if not a.start_time or not a.end_time:
                continue
            c = max(a.start_time, day_start)
            end = min(a.end_time, day_end)
            while c < end:
                sl = min(c + step, end)
                if not (c.hour >= ns or c.hour < ne):
                    per_sol_day[a.soldier_id] += (sl - c).total_seconds() / 3600
                c = sl
            c = max(a.start_time, ns_dt)
            end = min(a.end_time, ne_dt)
            while c < end:
                sl = min(c + step, end)
                if c.hour >= ns or c.hour < ne:
                    per_sol_night[a.soldier_id] += (sl - c).total_seconds() / 3600
                c = sl

        day_fracs, night_fracs = compute_domain_fractions(
            self.db, soldier_ids, ref, ns, ne,
        )
        avg_d, sd_d = weighted_avg_sd(per_sol_day, day_fracs)
        avg_n, sd_n = weighted_avg_sd(per_sol_night, night_fracs)

        full, partial = count_present_partial(self.db, soldier_ids, ref)

        theme = config.theme if config else "dark"
        self._workload_lbl.setStyleSheet(_STRIP_STYLE.get(theme, _STRIP_STYLE["dark"]))
        self._workload_lbl.setText(
            f"Day avg: {avg_d:.1f}h (\u00b1{sd_d:.1f}h)  |  "
            f"Night avg: {avg_n:.1f}h (\u00b1{sd_n:.1f}h)  |  "
            f"Present: {full}  Partial: {partial}"
        )

    def _rebuild_daily(self):
        tasks = self._task_svc.get_tasks_for_date(self._current_date)
        self._populate_table(tasks, show_date=False)
        self._count_lbl.setText(f"{len(tasks)} task(s)")

    def _rebuild_archive(self):
        self._table.setSortingEnabled(False)
        tasks = self._task_svc.list_tasks(active_only=True)
        # Reverse to show newest first (list_tasks returns by start_time asc)
        tasks = list(reversed(tasks))
        self._populate_table(tasks, show_date=True)
        self._count_lbl.setText(f"{len(tasks)} task(s) total")
        self._table.setSortingEnabled(True)

    def _populate_table(self, tasks, show_date: bool):
        self._table.setRowCount(len(tasks))
        for row, t in enumerate(tasks):
            window_text = _format_window(t, show_date=show_date)
            req = str(len(_task_roles_list(t)))
            status = _normalize_status(t.coverage_status)

            items = [
                _SortableItem(_task_label(t)),
                _SortableItem(window_text, sort_value=t.start_time.timestamp() if t.start_time else 0),
                _SortableItem(req, sort_value=int(req)),
                _SortableItem(status),
            ]
            for col, item in enumerate(items):
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if status == 'UNCOVERED':
                    item.setBackground(_UNCOVERED_ROW_BG)
                if col == 3:
                    item.setForeground(QColor(_STATUS_COLORS.get(status, '#888888')))
                item.setData(Qt.ItemDataRole.UserRole, t.id)
                self._table.setItem(row, col, item)

    # ── Event handlers ───────────────────────────────────────────────────

    def _on_selection(self):
        rows = self._table.selectedItems()
        if not rows:
            return
        task_id = rows[0].data(Qt.ItemDataRole.UserRole)
        task = self._task_svc.get_task(task_id)
        if task:
            self._detail.load(task)

    def _on_row_double_click(self, index):
        item = self._table.item(index.row(), 0)
        if not item:
            return
        task_id = item.data(Qt.ItemDataRole.UserRole)
        task = self._task_svc.get_task(task_id)
        if not task:
            return
        from src.ui.dialogs.task_dialog import TaskDialog
        dlg = TaskDialog(self.db, task=task, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self._run_reconcile()
            self._rebuild()
            self._detail.load(self._task_svc.get_task(task_id))
            self.mw.set_dirty(True)
            self._warn_uncovered()

    def _on_add_task(self):
        from src.ui.dialogs.task_dialog import TaskDialog
        dlg = TaskDialog(self.db, task=None, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self._run_reconcile()
            self._rebuild()
            if dlg.task:
                self._detail.load(dlg.task)
            self.mw.set_dirty(True)
            self._warn_uncovered()

    def _run_reconcile(self):
        try:
            if not self._handle_pinned_pre_reconcile():
                return
            from src.ui.dialogs.schedule_progress_dialog import ScheduleProgressDialog
            svc = ScheduleService(self.db)
            dlg = ScheduleProgressDialog(svc, parent=self)
            if dlg.exec() == dlg.DialogCode.Accepted:
                self.mw.set_dirty(False)
                if hasattr(self.mw, '_update_inbox_badge'):
                    self.mw._update_inbox_badge()
            elif dlg.error and dlg.error != "Cancelled by user":
                QMessageBox.critical(self, "Reconcile failed", dlg.error)
        except Exception as e:
            QMessageBox.critical(self, "Reconcile failed", str(e))

    def _handle_pinned_pre_reconcile(self) -> bool:
        """Check for pinned assignments and prompt. Returns False if cancelled."""
        sched_svc = ScheduleService(self.db)
        pinned = sched_svc.count_future_pinned()
        if pinned == 0:
            return True

        from src.ui.dialogs.pinned_confirm_dialog import PinnedConfirmDialog
        dlg = PinnedConfirmDialog(pinned, parent=self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return False

        if dlg.result_action == PinnedConfirmDialog.CLEAR:
            sched_svc.clear_future_pinned()

        return True

    def _warn_uncovered(self):
        uncovered = self._task_svc.get_uncovered_tasks()
        if not uncovered:
            return

        # Check if any UNCOVERED tasks exclude the commander (include_commander=False)
        retryable = [t for t in uncovered if not t.include_commander]
        if retryable:
            from src.ui.dialogs.retry_uncovered_dialog import RetryUncoveredDialog
            dlg = RetryUncoveredDialog(retryable, parent=self)
            if dlg.exec() == dlg.DialogCode.Accepted and dlg.retry_requested:
                # Flip include_commander for selected tasks
                selected_ids = dlg.selected_task_ids()
                if selected_ids:
                    for tid in selected_ids:
                        self._task_svc.set_include_commander(tid, True)
                    self._task_svc.commit()
                    # Re-run reconcile (one-shot retry)
                    self._run_reconcile()
                    self._rebuild()
                    # Show standard warning for any remaining uncovered
                    self._show_uncovered_warning()
                    return
            # User cancelled — fall through to standard warning

        self._show_uncovered_warning()

    def _show_uncovered_warning(self):
        uncovered = self._task_svc.get_uncovered_tasks()
        if uncovered:
            names = "\n".join(f"  - {t.real_title or f'Task#{t.id}'}" for t in uncovered)
            QMessageBox.warning(
                self,
                "Coverage Warning",
                f"The following tasks could not be fully covered:\n\n"
                f"{names}\n\n"
                f"Consider removing a task or adjusting soldier availability.",
            )

    def refresh(self):
        self._rebuild()
        # Reload detail panel if a task was selected
        if self._detail._task_id is not None:
            task = self._task_svc.get_task(self._detail._task_id)
            if task:
                self._detail.load(task)
