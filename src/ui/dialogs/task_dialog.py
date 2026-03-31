"""
TaskDialog — create or edit a Task record.

Fields:
  · Identity  : real title, codename, active toggle
  · Schedule  : start date, start time, end date, end time, readiness buffer
  · Manning   : total soldiers, role breakdown, fractionable
  · Tuning    : hardness (1–5)
"""
import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QCheckBox, QFrame,
    QGroupBox, QSpinBox, QDoubleSpinBox,
    QMessageBox, QDialogButtonBox,
    QDateEdit, QScrollArea, QWidget,
    QPushButton, QToolButton, QComboBox, QCompleter,
)
from PyQt6.QtCore import Qt, QDate, QTime

from src.ui.widgets import NoScrollSpinBox
from src.ui.widgets.searchable_select import SearchableSelectWidget

from src.core.models import Task
from src.services.config_service import ConfigService
from src.services.soldier_service import SoldierService
from src.services.task_service import TaskService
from src.services.template_service import TemplateService


class TaskDialog(QDialog):
    """Modal dialog for creating or editing a Task."""

    def __init__(self, db, task: Task | None = None, parent=None):
        super().__init__(parent)
        self.db = db
        self._task_svc = TaskService(db)
        self._tpl_svc = TemplateService(db)
        self.task = task
        self._is_new = task is None
        self._filled_from_template = False
        self._templates: dict[str, object] = {}  # name → TaskTemplate

        self.setModal(True)
        self.setMinimumSize(520, 580)
        title = "NEW TASK" if self._is_new else f"EDIT TASK — {task.real_title or f'#{task.id}'}"
        self.setWindowTitle(title)

        self._setup_ui()
        if not self._is_new:
            self._load_data()

    # ──────────────────────────────────────────────── UI ──── #

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        title_lbl = QLabel("NEW TASK" if self._is_new else
                           f"EDIT — {self.task.real_title or '?'}")
        title_lbl.setObjectName("sectionHeader")
        root.addWidget(title_lbl)

        # ── Identity + assignment type ────────────────────────────────────── #
        id_group = QGroupBox("IDENTITY")
        id_form = QFormLayout(id_group)
        id_form.setSpacing(6)

        self._title = QComboBox()
        self._title.setEditable(True)
        self._title.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._title.lineEdit().setPlaceholderText("Task name")
        self._title.setMaximumWidth(260)
        # Populate with saved templates
        self._reload_template_combo()
        self._title.setCurrentText("")
        # Auto-fill when a template is selected from dropdown
        self._title.activated.connect(self._on_template_selected)
        # Track manual edits to reset _filled_from_template
        self._title.lineEdit().textEdited.connect(self._on_name_edited)

        # Assignment type: explicit two-way choice with visible circles
        self._fractionable_state = True  # True = fractionable, False = not fractionable
        self._fractionable_yes_btn = QToolButton()
        self._fractionable_yes_btn.setAutoRaise(True)
        self._fractionable_no_btn = QToolButton()
        self._fractionable_no_btn.setAutoRaise(True)
        self._fractionable_yes_btn.clicked.connect(lambda: self._set_fractionable(True))
        self._fractionable_no_btn.clicked.connect(lambda: self._set_fractionable(False))
        self._update_fractionable_buttons()
        frac_row = QWidget()
        frac_layout = QHBoxLayout(frac_row)
        frac_layout.setContentsMargins(0, 0, 0, 0)
        frac_layout.setSpacing(8)
        frac_layout.addWidget(self._fractionable_yes_btn)
        frac_layout.addWidget(self._fractionable_no_btn)

        name_row = QWidget()
        name_layout = QHBoxLayout(name_row)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(12)
        name_layout.addWidget(self._title, 2)
        name_layout.addWidget(frac_row, 1)

        id_form.addRow("Name *:", name_row)
        root.addWidget(id_group)

        # ── Schedule ─────────────────────────────────────────────────────── #
        sched_group = QGroupBox("SCHEDULE")
        sched_form = QFormLayout(sched_group)
        sched_form.setSpacing(6)

        now_date = QDate.currentDate()
        now_time = QTime.currentTime()

        # Start window (date + time, with explicit stacked day arrows)
        start_row = QHBoxLayout()
        self._start_date = QDateEdit(now_date)
        self._start_date.setCalendarPopup(True)
        self._start_date.setDisplayFormat("dd/MM/yyyy")
        self._start_date.setFixedWidth(120)
        self._start_up = QToolButton()
        self._start_up.setArrowType(Qt.ArrowType.UpArrow)
        self._start_up.setFixedSize(20, 14)
        self._start_down = QToolButton()
        self._start_down.setArrowType(Qt.ArrowType.DownArrow)
        self._start_down.setFixedSize(20, 14)
        start_arrows = QVBoxLayout()
        start_arrows.setContentsMargins(0, 0, 0, 0)
        start_arrows.setSpacing(0)
        start_arrows.addWidget(self._start_up)
        start_arrows.addWidget(self._start_down)
        # Start time split into hour + minute (15-min grid alignment)
        self._start_hour = NoScrollSpinBox()
        self._start_hour.setRange(0, 23)
        self._start_hour.setValue(now_time.hour())
        # Slightly wider so value + arrows are readable
        self._start_hour.setFixedWidth(72)
        self._start_minute = NoScrollSpinBox()
        self._start_minute.setRange(0, 45)
        self._start_minute.setSingleStep(15)
        self._start_minute.setValue((now_time.minute() // 15) * 15)
        self._start_minute.setProperty("_prev_val", (now_time.minute() // 15) * 15)
        self._start_minute.setFixedWidth(72)
        start_time_row = QHBoxLayout()
        start_time_row.setContentsMargins(0, 0, 0, 0)
        start_time_row.setSpacing(4)
        start_time_row.addWidget(self._start_hour)
        start_time_row.addWidget(self._start_minute)
        start_row.addWidget(self._start_date, 1)
        start_row.addLayout(start_arrows)
        start_row.addLayout(start_time_row, 1)

        # End window (date + time, with explicit stacked day arrows)
        end_row = QHBoxLayout()
        self._end_date = QDateEdit(now_date)
        self._end_date.setCalendarPopup(True)
        self._end_date.setDisplayFormat("dd/MM/yyyy")
        self._end_date.setFixedWidth(120)
        self._end_up = QToolButton()
        self._end_up.setArrowType(Qt.ArrowType.UpArrow)
        self._end_up.setFixedSize(20, 14)
        self._end_down = QToolButton()
        self._end_down.setArrowType(Qt.ArrowType.DownArrow)
        self._end_down.setFixedSize(20, 14)
        end_arrows = QVBoxLayout()
        end_arrows.setContentsMargins(0, 0, 0, 0)
        end_arrows.setSpacing(0)
        end_arrows.addWidget(self._end_up)
        end_arrows.addWidget(self._end_down)
        end_time = QTime(now_time.hour() + 1 if now_time.hour() < 23 else 0,
                         now_time.minute())
        # End time split into hour + minute (15-min grid alignment)
        self._end_hour = NoScrollSpinBox()
        self._end_hour.setRange(0, 23)
        self._end_hour.setValue(end_time.hour())
        self._end_hour.setFixedWidth(72)
        self._end_minute = NoScrollSpinBox()
        self._end_minute.setRange(0, 45)
        self._end_minute.setSingleStep(15)
        self._end_minute.setValue((end_time.minute() // 15) * 15)
        self._end_minute.setProperty("_prev_val", (end_time.minute() // 15) * 15)
        self._end_minute.setFixedWidth(72)
        end_time_row = QHBoxLayout()
        end_time_row.setContentsMargins(0, 0, 0, 0)
        end_time_row.setSpacing(4)
        end_time_row.addWidget(self._end_hour)
        end_time_row.addWidget(self._end_minute)
        end_row.addWidget(self._end_date, 1)
        end_row.addLayout(end_arrows)
        end_row.addLayout(end_time_row, 1)

        self._readiness = QSpinBox()
        self._readiness.setRange(0, 240)
        # Keep field label short; no explanatory suffix text
        self._readiness.setFixedWidth(90)

        sched_form.addRow("Start:", start_row)
        sched_form.addRow("End:",   end_row)
        sched_form.addRow("Readiness buffer:", self._readiness)

        # Wire day arrows
        self._start_up.clicked.connect(lambda: self._nudge_date(self._start_date, 1))
        self._start_down.clicked.connect(lambda: self._nudge_date(self._start_date, -1))
        self._end_up.clicked.connect(lambda: self._nudge_date(self._end_date, 1))
        self._end_down.clicked.connect(lambda: self._nudge_date(self._end_date, -1))

        # Enable wrapping on minute spinners and wire rollover to hour
        self._start_minute.setWrapping(True)
        self._end_minute.setWrapping(True)
        self._minute_rolling = False  # guard against recursive signals
        self._start_minute.valueChanged.connect(
            lambda v: self._on_minute_wrap(v, self._start_minute, self._start_hour)
        )
        self._end_minute.valueChanged.connect(
            lambda v: self._on_minute_wrap(v, self._end_minute, self._end_hour)
        )

        # ── Total soldiers (right column, above scoring) ─────────────────── #
        self._req_count = QSpinBox()
        self._req_count.setRange(1, 50)
        self._req_count.setValue(1)
        self._req_count.valueChanged.connect(self._on_total_changed)
        self._req_count.setFixedWidth(110)

        total_group = QGroupBox("TOTAL SOLDIERS")
        total_form = QFormLayout(total_group)
        total_form.setSpacing(4)
        total_form.addRow("", self._req_count)

        # ── Hardship (hardness only; base weight fixed internally) ───────── #
        tune_group = QGroupBox("HARDSHIP")
        tune_form = QFormLayout(tune_group)
        tune_form.setSpacing(6)

        self._hardness = QSpinBox()
        self._hardness.setRange(1, 5)
        self._hardness.setValue(3)
        self._hardness.setFixedWidth(90)
        self._hardness.setToolTip(
            "Physical/mental hardship:\n"
            "1 = Comfortable, 2 = Slightly uncomfortable,\n"
            "3 = Uncomfortable, 4 = Hard, 5 = Nasty."
        )

        tune_form.addRow("Hardness (1–5):", self._hardness)
        tune_group.setMaximumHeight(100)

        # Place schedule on the left, total + scoring stacked on the right
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        right_col = QVBoxLayout()
        right_col.setSpacing(6)
        right_col.addWidget(total_group)
        right_col.addWidget(tune_group)
        top_row.addWidget(sched_group, 2)
        top_row.addLayout(right_col, 1)
        root.addLayout(top_row)

        # ── Manning: roles take remaining vertical space ─────────────────── #
        roles_group = QGroupBox("ROLE REQUIREMENTS")
        roles_layout = QVBoxLayout(roles_group)
        roles_layout.setContentsMargins(4, 4, 4, 4)
        self._roles_widget = SearchableSelectWidget(show_quantity=True)
        roles = ConfigService(self.db).list_roles_for_picker()
        self._roles_widget.set_items([(r.name, r.name) for r in roles])
        roles_layout.addWidget(self._roles_widget)
        root.addWidget(roles_group, 1)

        # ── Eligibility: exclusion + commander toggle ──────────────────── #
        elig_group = QGroupBox("ELIGIBILITY")
        elig_layout = QVBoxLayout(elig_group)
        elig_layout.setContentsMargins(4, 4, 4, 4)
        elig_layout.setSpacing(6)

        self._include_commander = QCheckBox("  Include commander in assignments")
        self._include_commander.setToolTip(
            "When unchecked, the active commander (based on the chain of command) "
            "will not be assigned to this task"
        )
        self._include_commander.setChecked(False)
        font = self._include_commander.font()
        font.setBold(True)
        self._include_commander.setFont(font)
        self._include_commander.setContentsMargins(4, 4, 4, 4)
        elig_layout.addWidget(self._include_commander)

        elig_layout.addWidget(QLabel("Excluded soldiers:"))
        self._exclusion_widget = SearchableSelectWidget(show_quantity=False)
        soldiers = SoldierService(self.db).list_active_soldiers()
        self._exclusion_widget.set_items([(s.id, s.name or f"#{s.id}") for s in soldiers])
        elig_layout.addWidget(self._exclusion_widget)

        root.addWidget(elig_group)

        # ── Save as Template + Buttons ───────────────────────────────────── #
        bottom_row = QHBoxLayout()
        self._save_tpl_btn = QPushButton("Save as Template")
        self._save_tpl_btn.setMinimumWidth(180)
        self._save_tpl_btn.clicked.connect(self._on_save_as_template)
        self._save_tpl_btn.setEnabled(False)
        # Only enabled for new tasks that weren't filled from a template
        if self._is_new:
            self._title.lineEdit().textChanged.connect(self._update_save_tpl_enabled)
        bottom_row.addWidget(self._save_tpl_btn)
        bottom_row.addStretch()

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(self.reject)
        bottom_row.addWidget(btns)
        root.addLayout(bottom_row)

    # ──────────────────────────────── template helpers ──── #

    def _reload_template_combo(self):
        """Populate the combo box with saved template names."""
        self._templates.clear()
        self._title.clear()
        for tpl in self._tpl_svc.list_templates():
            self._templates[tpl.name] = tpl
            self._title.addItem(tpl.name)

    def _on_template_selected(self, index: int):
        """Auto-fill fields when a template is picked from the dropdown."""
        if not self._is_new:
            return
        name = self._title.itemText(index)
        tpl = self._templates.get(name)
        if not tpl:
            return
        self._filled_from_template = True

        # Time of day
        sh, sm = map(int, tpl.start_time_of_day.split(':'))
        eh, em = map(int, tpl.end_time_of_day.split(':'))
        self._start_hour.setValue(sh)
        self._start_minute.setValue(sm)
        self._start_minute.setProperty("_prev_val", sm)
        self._end_hour.setValue(eh)
        self._end_minute.setValue(em)
        self._end_minute.setProperty("_prev_val", em)

        # Dates: today, and tomorrow if crosses midnight
        today = QDate.currentDate()
        self._start_date.setDate(today)
        if tpl.crosses_midnight:
            self._end_date.setDate(today.addDays(1))
        else:
            self._end_date.setDate(today)

        # Other fields
        self._set_fractionable(bool(tpl.is_fractionable))
        self._req_count.setValue(tpl.required_count or 1)
        self._hardness.setValue(tpl.hardness or 3)

        # Roles
        rl = tpl.required_roles_list or {}
        if isinstance(rl, dict):
            self._roles_widget.set_selected(rl)
        elif isinstance(rl, list):
            self._roles_widget.set_selected({v: 1 for v in rl})

        self._update_save_tpl_enabled()

    def _on_name_edited(self, text: str):
        """Reset template flag when user types a fresh name."""
        self._filled_from_template = False
        self._update_save_tpl_enabled()

    def _update_save_tpl_enabled(self):
        """Enable Save as Template only for new, non-template-filled tasks with a name."""
        enabled = (
            self._is_new
            and not self._filled_from_template
            and bool(self._title.currentText().strip())
        )
        self._save_tpl_btn.setEnabled(enabled)

    def _on_save_as_template(self):
        """Save current dialog fields as a new template."""
        name = self._title.currentText().strip()
        if not name:
            return
        start_tod = f"{self._start_hour.value():02d}:{self._start_minute.value():02d}"
        end_tod = f"{self._end_hour.value():02d}:{self._end_minute.value():02d}"
        roles = {n: q for n, q in self._roles_widget.get_selected()}
        try:
            self._tpl_svc.create_template(
                name=name,
                start_time_of_day=start_tod,
                end_time_of_day=end_tod,
                is_fractionable=self._fractionable_state,
                required_count=self._req_count.value(),
                required_roles_list=roles,
                hardness=self._hardness.value(),
            )
        except ValueError as e:
            QMessageBox.warning(self, "Template Error", str(e))
            return
        self._reload_template_combo()
        self._title.setCurrentText(name)
        self._filled_from_template = True
        self._update_save_tpl_enabled()
        QMessageBox.information(self, "Template Saved", f"Template \"{name}\" saved.")

    # ──────────────────────────────────── manning auto-clamp ──── #

    def _role_sum(self) -> int:
        return sum(qty for _, qty in self._roles_widget.get_selected())

    def _on_total_changed(self, new_total: int):
        """Prevent total from dropping below role sum."""
        role_sum = self._role_sum()
        if new_total < role_sum:
            self._req_count.setValue(role_sum)

    def _set_fractionable(self, is_fractionable: bool):
        self._fractionable_state = is_fractionable
        self._update_fractionable_buttons()

    def _update_fractionable_buttons(self):
        if getattr(self, "_fractionable_state", True):
            self._fractionable_yes_btn.setText("◉ Fractionable")
            self._fractionable_no_btn.setText("○ Not fractionable")
        else:
            self._fractionable_yes_btn.setText("○ Fractionable")
            self._fractionable_no_btn.setText("◉ Not fractionable")

    def _on_minute_wrap(self, value: int, minute_spin: 'NoScrollSpinBox', hour_spin: 'NoScrollSpinBox'):
        """Handle minute spinner wrapping: 45→0 increments hour, 0→45 decrements hour."""
        if self._minute_rolling:
            return
        self._minute_rolling = True
        try:
            # Detect wrap direction by checking the transition
            # When wrapping is on, Qt goes 45→0 (wrap up) or 0→45 (wrap down)
            if value == 0 and minute_spin.property("_prev_val") == 45:
                # Wrapped up: 45 → 0, increment hour
                h = hour_spin.value()
                if h < 23:
                    hour_spin.setValue(h + 1)
                else:
                    hour_spin.setValue(0)
            elif value == 45 and minute_spin.property("_prev_val") == 0:
                # Wrapped down: 0 → 45, decrement hour
                h = hour_spin.value()
                if h > 0:
                    hour_spin.setValue(h - 1)
                else:
                    hour_spin.setValue(23)
            minute_spin.setProperty("_prev_val", value)
        finally:
            self._minute_rolling = False

    def _nudge_date(self, widget: QDateEdit, delta_days: int):
        """Increment/decrement a QDateEdit by whole days."""
        d = widget.date()
        py = d.toPyDate()
        new = py + datetime.timedelta(days=delta_days)
        widget.setDate(QDate(new.year, new.month, new.day))

    # ──────────────────────────────────────────────── data ──── #

    def _load_data(self):
        t = self.task
        self._title.setCurrentText(t.real_title or "")
        self._set_fractionable(bool(t.is_fractionable))
        self._readiness.setValue(t.readiness_minutes or 0)
        # Default hardness to 3 if missing/None.
        hardness = getattr(t, "hardness", None)
        if hardness is None:
            hardness = 3
        self._hardness.setValue(int(hardness))

        # Datetimes — split into date + time widgets
        if t.start_time:
            self._start_date.setDate(QDate(t.start_time.year, t.start_time.month, t.start_time.day))
            self._start_hour.setValue(t.start_time.hour)
            sm = (t.start_time.minute // 15) * 15
            self._start_minute.setValue(sm)
            self._start_minute.setProperty("_prev_val", sm)
        if t.end_time:
            self._end_date.setDate(QDate(t.end_time.year, t.end_time.month, t.end_time.day))
            self._end_hour.setValue(t.end_time.hour)
            em = (t.end_time.minute // 15) * 15
            self._end_minute.setValue(em)
            self._end_minute.setProperty("_prev_val", em)

        # Roles first (so auto-clamp sets minimum before we set total)
        rl = t.required_roles_list or {}
        if isinstance(rl, dict):
            self._roles_widget.set_selected(rl)
        elif isinstance(rl, list):
            self._roles_widget.set_selected({v: 1 for v in rl})

        self._req_count.setValue(t.required_count or 1)

        # Eligibility
        self._exclusion_widget.set_selected(t.excluded_soldier_ids or [])
        self._include_commander.setChecked(bool(t.include_commander))

    def _on_save(self):
        title = self._title.currentText().strip()
        if not title:
            QMessageBox.warning(self, "Validation", "Real title is required.")
            self._title.setFocus()
            return

        sd = self._start_date.date()
        ed = self._end_date.date()
        start = datetime.datetime(
            sd.year(), sd.month(), sd.day(),
            self._start_hour.value(), self._start_minute.value()
        )
        end = datetime.datetime(
            ed.year(), ed.month(), ed.day(),
            self._end_hour.value(), self._end_minute.value()
        )

        if end <= start:
            QMessageBox.warning(self, "Validation", "End time must be after start time.")
            return

        selected_roles = {name: qty for name, qty in self._roles_widget.get_selected()}
        total = self._req_count.value()

        role_sum = sum(selected_roles.values())
        if total < role_sum:
            QMessageBox.warning(self, "Validation",
                                f"Total soldiers ({total}) cannot be less than "
                                f"sum of role requirements ({role_sum}).")
            return

        excluded_ids = self._exclusion_widget.get_selected()
        include_cmdr = self._include_commander.isChecked()

        if self._is_new:
            t = Task(
                real_title=title,
                start_time=start,
                end_time=end,
                readiness_minutes=self._readiness.value(),
                required_count=total,
                required_roles_list=selected_roles,
                is_fractionable=self._fractionable_state,
                base_weight=1.0,
                hardness=self._hardness.value(),
                coverage_status='UNCOVERED',
                excluded_soldier_ids=excluded_ids,
                include_commander=include_cmdr,
            )
            self._task_svc.save_task(t)
            self.task = t
        else:
            t = self.task
            t.real_title         = title
            t.start_time         = start
            t.end_time           = end
            t.readiness_minutes  = self._readiness.value()
            t.required_count     = total
            t.required_roles_list = selected_roles
            t.is_fractionable    = self._fractionable_state
            # Keep base_weight fixed at 1.0; hardness drives perceived hardship.
            t.base_weight        = 1.0
            t.hardness           = self._hardness.value()
            t.excluded_soldier_ids = excluded_ids
            t.include_commander    = include_cmdr
            self._task_svc.commit_task(t)

        self.accept()
