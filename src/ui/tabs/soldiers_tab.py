"""
Soldiers + Inbox Tab — Roster view and pending review inbox in one tab.
Toggle between views with the pill selector at the top.
"""
from datetime import datetime, date, time, timedelta
from collections import defaultdict

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter,
    QStackedWidget, QFrame, QScrollArea,
    QDateEdit, QMessageBox, QDialog, QComboBox, QTextEdit,
    QToolButton,
)
from PyQt6.QtCore import Qt, QDate, QEvent
from PyQt6.QtGui import QColor

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from src.core.models import Soldier, TaskAssignment, Task
from src.services.config_service import ConfigService
from src.services.schedule_service import ScheduleService
from src.ui.charts.chart_style import (
    create_figure, style_ax, apply_style, get_palette, current_theme,
    COLOR_DAY, COLOR_NIGHT, COLOR_AVG_WARM,
    MUTED_ALPHA, CHART_LEGEND_BG, CHART_TEXT_SEC, CHART_TEXT,
)
# Distinct status text colors that work well on both dark and light table bg
_STATUS_TEXT_COLORS = {
    "dark": {
        "present":     "#66bb6a",  # bright green
        "partial_day": "#ffd54f",  # amber/yellow
        "absent":      "#ef5350",  # bright red
        "inactive":    "#9e9e9e",  # gray
    },
    "light": {
        "present":     "#2e7d32",  # dark green
        "partial_day": "#f57f17",  # dark amber
        "absent":      "#c62828",  # dark red
        "inactive":    "#757575",  # gray
    },
}

from src.services.soldier_service import SoldierService
from src.domain.command_rules import resolve_active_commander


def _name(s: Soldier) -> str:
    return s.name or f"#{s.id}"


class _NumericItem(QTableWidgetItem):
    """QTableWidgetItem that sorts numerically instead of lexicographically."""
    def __lt__(self, other):
        try:
            return float(self.text()) < float(other.text())
        except (ValueError, TypeError):
            return super().__lt__(other)


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

_HINT_STYLE = {
    "dark": "font-size: 10px; color: #9a8e7e; padding: 1px 8px;",
    "light": "font-size: 10px; color: #4e7040; padding: 1px 8px;",
}


def _rate_bg_color(val: float, theme: str = "dark") -> str:
    """Return a background color for a +/- rate cell.

    Green gradient for negative (underloaded), orange/red for positive
    (overloaded).  Intensity scales with magnitude, capped at ±1.0.
    """
    t = min(abs(val), 1.0)  # 0..1 intensity
    if val < -0.01:
        # Underloaded: green tint
        if theme == "dark":
            r, g, b = int(30 + t * 10), int(40 + t * 60), int(30 + t * 10)
        else:
            r, g, b = int(220 - t * 50), int(240 - t * 10), int(220 - t * 50)
    elif val > 0.01:
        # Overloaded: orange/red tint
        if theme == "dark":
            r, g, b = int(50 + t * 70), int(35 + t * 15), int(25)
        else:
            r, g, b = int(255 - t * 20), int(230 - t * 60), int(210 - t * 70)
    else:
        return "transparent"
    return f"#{r:02x}{g:02x}{b:02x}"


def _rate_fg_color(val: float, theme: str = "dark") -> str:
    """Return a foreground color for a +/- rate cell."""
    if abs(val) < 0.01:
        return "#9e9e9e" if theme == "dark" else "#757575"
    if val < 0:
        return "#66bb6a" if theme == "dark" else "#2e7d32"
    return "#ff8a65" if theme == "dark" else "#d84315"


# ── Small dialog for adding a request / note ──────────────────────────────────

class _AddRequestDialog(QDialog):
    REQUEST_TYPES = ["NOTE", "LEAVE_REQUEST", "ROLE_CHANGE", "SWAP_REQUEST", "OTHER"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Request / Note")
        self.setModal(True)
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Request type:"))
        self._type_combo = QComboBox()
        self._type_combo.addItems(self.REQUEST_TYPES)
        layout.addWidget(self._type_combo)

        layout.addWidget(QLabel("Description:"))
        self._desc = QTextEdit()
        self._desc.setPlaceholderText("Enter details…")
        self._desc.setMaximumHeight(100)
        layout.addWidget(self._desc)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("[ CANCEL ]")
        ok     = QPushButton("[ ADD ]")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._on_ok)
        btn_row.addWidget(cancel)
        btn_row.addWidget(ok)
        layout.addLayout(btn_row)

    def _on_ok(self):
        if not self._desc.toPlainText().strip():
            QMessageBox.warning(self, "Empty", "Please enter a description.")
            return
        self.accept()

    def req_type(self) -> str:
        return self._type_combo.currentText()

    def description(self) -> str:
        return self._desc.toPlainText().strip()


# ── Soldier detail panel ──────────────────────────────────────────────────────

class SoldierDetailPanel(QWidget):
    REQUEST_STATUSES = ["PENDING", "APPROVED", "REJECTED", "NOTED"]

    def __init__(self, db, main_window, parent=None):
        super().__init__(parent)
        self.db = db
        self._config_svc = ConfigService(db)
        self._soldier_svc = SoldierService(db)
        self.mw = main_window
        self._soldier_id = None
        self._schedule_date = date.today()
        self._setup_ui()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Wrap everything in a scroll area so layout survives short windows
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        # ── Identity & stats ──────────────────────────────────────────────────
        self._name_lbl = QLabel("Select a soldier")
        self._name_lbl.setObjectName("sectionHeader")
        layout.addWidget(self._name_lbl)

        self._roles_lbl = QLabel()
        self._roles_lbl.setWordWrap(True)
        self._roles_lbl.setObjectName("dimLabel")
        layout.addWidget(self._roles_lbl)

        self._stats_row = QLabel("Active: — | Present: —")
        self._stats_row.setStyleSheet("font-size: 12px;")
        layout.addWidget(self._stats_row)

        # ── Date navigation (shared: chart + schedule) ────────────────────────
        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)
        self._sched_prev_btn = QToolButton()
        self._sched_prev_btn.setArrowType(Qt.ArrowType.LeftArrow)
        self._sched_prev_btn.setFixedSize(28, 24)
        self._sched_prev_btn.setToolTip("Previous day")
        self._sched_prev_btn.clicked.connect(self._schedule_prev_day)
        nav_row.addWidget(self._sched_prev_btn)
        self._schedule_date_edit = QDateEdit(QDate.currentDate())
        self._schedule_date_edit.setCalendarPopup(True)
        self._schedule_date_edit.setDisplayFormat("ddd dd MMM yyyy")
        self._schedule_date_edit.setFixedWidth(190)
        self._schedule_date_edit.dateChanged.connect(self._on_schedule_date_changed)
        nav_row.addWidget(self._schedule_date_edit)
        self._sched_next_btn = QToolButton()
        self._sched_next_btn.setArrowType(Qt.ArrowType.RightArrow)
        self._sched_next_btn.setFixedSize(28, 24)
        self._sched_next_btn.setToolTip("Next day")
        self._sched_next_btn.clicked.connect(self._schedule_next_day)
        nav_row.addWidget(self._sched_next_btn)
        self._sched_today_btn = QPushButton("TODAY")
        self._sched_today_btn.setFixedWidth(90)
        self._sched_today_btn.setToolTip("Jump to today")
        self._sched_today_btn.clicked.connect(self._schedule_go_today)
        nav_row.addWidget(self._sched_today_btn)
        nav_row.addStretch()
        layout.addLayout(nav_row)

        # ── 3-day context chart (compact) ─────────────────────────────────────
        self._chart_fig = create_figure(width=3.5, height=1.8)
        self._chart_canvas = FigureCanvas(self._chart_fig)
        self._chart_canvas.setMinimumHeight(120)
        self._chart_canvas.setMaximumHeight(160)
        layout.addWidget(self._chart_canvas)

        # +/- diff label below chart
        self._diff_lbl = QLabel()
        self._diff_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._diff_lbl.setStyleSheet("font-size: 12px;")
        layout.addWidget(self._diff_lbl)

        # ── VIEW FULL STATS button ────────────────────────────────────────────
        self._stats_btn = QPushButton("[ VIEW FULL STATS ]")
        self._stats_btn.setEnabled(False)
        self._stats_btn.clicked.connect(self._on_view_full_stats)
        layout.addWidget(self._stats_btn)

        # ── Schedule table ────────────────────────────────────────────────────
        sched_hdr = QLabel("SCHEDULE")
        sched_hdr.setStyleSheet(
            "font-weight: bold; letter-spacing: 1px; font-size: 13px;"
        )
        layout.addWidget(sched_hdr)

        self._today_table = QTableWidget()
        self._today_table.setColumnCount(3)
        self._today_table.setHorizontalHeaderLabels(["TIME", "TASK", "FLAGS"])
        th = self._today_table.horizontalHeader()
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        th.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._today_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._today_table.verticalHeader().setVisible(False)
        self._today_table.setMinimumHeight(100)
        self._today_table.setMaximumHeight(180)
        layout.addWidget(self._today_table)

        # ── Requests / notes / pending review ─────────────────────────────────
        req_hdr_row = QHBoxLayout()
        req_hdr_lbl = QLabel("REQUESTS / NOTES / PENDING REVIEW")
        req_hdr_lbl.setStyleSheet(
            "font-weight: bold; letter-spacing: 1px; font-size: 13px;"
        )
        req_hdr_row.addWidget(req_hdr_lbl)
        req_hdr_row.addStretch()
        self._add_req_btn = QPushButton("[ + ADD ]")
        self._add_req_btn.setFixedWidth(80)
        self._add_req_btn.setEnabled(False)
        self._add_req_btn.clicked.connect(self._on_add_request)
        req_hdr_row.addWidget(self._add_req_btn)
        layout.addLayout(req_hdr_row)

        self._activity_table = QTableWidget()
        self._activity_table.setColumnCount(4)
        self._activity_table.setHorizontalHeaderLabels(
            ["DATE / TIME", "TYPE", "DETAIL", "STATUS"]
        )
        hdr = self._activity_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._activity_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._activity_table.verticalHeader().setVisible(False)
        self._activity_table.setAlternatingRowColors(True)
        self._activity_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._activity_table.customContextMenuRequested.connect(self._on_req_context_menu)
        self._activity_table.doubleClicked.connect(self._on_pend_double_click)
        self._activity_table.setMinimumHeight(140)
        self._activity_table.setMaximumHeight(220)
        layout.addWidget(self._activity_table)

        # ── Action buttons ────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._edit_btn  = QPushButton("[ EDIT SOLDIER ]")
        self._gear_btn  = QPushButton("[ GEAR LIST ]")
        self._gear_btn.setEnabled(False)
        for b in (self._edit_btn, self._gear_btn):
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        layout.addStretch()

        self._edit_btn.clicked.connect(self._on_edit)
        self._gear_btn.clicked.connect(self._on_gear_list)

    # ── Data loading ──────────────────────────────────────────────────────────

    def load(self, soldier: Soldier, initial_date: date | None = None):
        self._soldier_id = soldier.id
        self._gear_btn.setEnabled(True)
        self._add_req_btn.setEnabled(True)
        self._stats_btn.setEnabled(True)

        label = _name(soldier)
        roles = ", ".join(r for r in (soldier.role or []) if r != "Soldier") or "None"
        self._name_lbl.setText(label.upper())
        self._roles_lbl.setText(f"Roles: {roles}")

        self._stats_row.setText(
            f"Active: {soldier.active_reserve_days or 0}d  |  "
            f"Present: {soldier.present_days_count or 0:.1f}d"
        )

        self._schedule_date = initial_date or date.today()
        self._schedule_date_edit.setDate(
            QDate(self._schedule_date.year, self._schedule_date.month, self._schedule_date.day)
        )
        self._reload_activity()
        self._refresh_date_views()

    # ── Date navigation (shared: chart + schedule) ──────────────────────────

    def _schedule_prev_day(self):
        self._schedule_date -= timedelta(days=1)
        self._schedule_date_edit.setDate(
            QDate(self._schedule_date.year, self._schedule_date.month, self._schedule_date.day)
        )
        self._refresh_date_views()

    def _schedule_next_day(self):
        self._schedule_date += timedelta(days=1)
        self._schedule_date_edit.setDate(
            QDate(self._schedule_date.year, self._schedule_date.month, self._schedule_date.day)
        )
        self._refresh_date_views()

    def _schedule_go_today(self):
        self._schedule_date = date.today()
        self._schedule_date_edit.setDate(
            QDate(self._schedule_date.year, self._schedule_date.month, self._schedule_date.day)
        )
        self._refresh_date_views()

    def _on_schedule_date_changed(self, qdate: QDate):
        self._schedule_date = qdate.toPyDate()
        self._refresh_date_views()

    def _refresh_date_views(self):
        """Refresh both chart and schedule for the current date."""
        if self._soldier_id is not None:
            soldier = self._soldier_svc.get_soldier(self._soldier_id)
            if soldier:
                self._update_compact_chart(soldier)
        self._reload_schedule()

    # ── 3-day context chart ───────────────────────────────────────────────────

    def _update_compact_chart(self, soldier: Soldier):
        """Render the 3-day context chart: yesterday, selected date, tomorrow."""
        from src.domain.presence_calc import compute_domain_fractions, weighted_avg_sd

        config = self._config_svc.get_config()
        ns = config.night_start_hour or 23
        ne = config.night_end_hour or 7
        theme = config.theme or "dark"
        pal = get_palette(theme)

        center = self._schedule_date
        days = [center - timedelta(days=1), center, center + timedelta(days=1)]

        # Fetch this soldier's assignments spanning the 3-day window
        window_start = datetime.combine(days[0], time(0, 0))
        window_end = datetime.combine(days[2] + timedelta(days=1), time(0, 0))

        sched_svc = ScheduleService(self.db)
        sol_asgns = sched_svc.get_soldier_assignments(
            soldier.id, start=window_start, end=window_end,
        )

        # All active soldiers and their assignments in the window
        active_soldiers = self._soldier_svc.list_active_soldiers()
        active_ids = [s.id for s in active_soldiers]

        all_asgns = sched_svc.get_assignments_for_soldiers(
            active_ids, window_start, window_end,
        )

        def _day_hours_for(asgns, target_date, night_start, night_end):
            """Sum day-domain hours on target_date from assignments."""
            ds = datetime.combine(target_date, time(0, 0))
            de = datetime.combine(target_date + timedelta(days=1), time(0, 0))
            total = 0.0
            step = timedelta(minutes=15)
            for a in asgns:
                if not a.start_time or not a.end_time:
                    continue
                c = max(a.start_time, ds)
                end = min(a.end_time, de)
                while c < end:
                    sl = min(c + step, end)
                    if not (c.hour >= night_start or c.hour < night_end):
                        total += (sl - c).total_seconds() / 3600
                    c = sl
            return total

        def _night_hours_for(asgns, target_date, night_start, night_end):
            """Sum night-domain hours for the night starting on target_date evening.

            Night window = [night_start on target_date .. night_end on target_date+1].
            """
            ns_dt = datetime.combine(target_date, time(night_start, 0))
            ne_dt = datetime.combine(target_date + timedelta(days=1), time(night_end, 0))
            total = 0.0
            step = timedelta(minutes=15)
            for a in asgns:
                if not a.start_time or not a.end_time:
                    continue
                c = max(a.start_time, ns_dt)
                end = min(a.end_time, ne_dt)
                while c < end:
                    sl = min(c + step, end)
                    if c.hour >= night_start or c.hour < night_end:
                        total += (sl - c).total_seconds() / 3600
                    c = sl
            return total

        # Per-day soldier values
        sol_day_vals = [_day_hours_for(sol_asgns, d, ns, ne) for d in days]
        sol_night_vals = [_night_hours_for(sol_asgns, d, ns, ne) for d in days]

        # Presence-weighted unit averages for ALL 3 days
        avg_day_vals = []
        avg_night_vals = []
        step = timedelta(minutes=15)
        for d in days:
            per_sol_day: dict[int, float] = defaultdict(float)
            per_sol_night: dict[int, float] = defaultdict(float)
            d_ds = datetime.combine(d, time(0, 0))
            d_de = datetime.combine(d + timedelta(days=1), time(0, 0))
            d_ns_dt = datetime.combine(d, time(ns, 0))
            d_ne_dt = datetime.combine(d + timedelta(days=1), time(ne, 0))
            for a in all_asgns:
                if not a.start_time or not a.end_time:
                    continue
                c = max(a.start_time, d_ds)
                end = min(a.end_time, d_de)
                while c < end:
                    sl = min(c + step, end)
                    if not (c.hour >= ns or c.hour < ne):
                        per_sol_day[a.soldier_id] += (sl - c).total_seconds() / 3600
                    c = sl
                c = max(a.start_time, d_ns_dt)
                end = min(a.end_time, d_ne_dt)
                while c < end:
                    sl = min(c + step, end)
                    if c.hour >= ns or c.hour < ne:
                        per_sol_night[a.soldier_id] += (sl - c).total_seconds() / 3600
                    c = sl

            day_fracs, night_fracs = compute_domain_fractions(
                self.db, active_ids, d, ns, ne,
            )
            avg_d, _ = weighted_avg_sd(per_sol_day, day_fracs)
            avg_n, _ = weighted_avg_sd(per_sol_night, night_fracs)
            avg_day_vals.append(avg_d)
            avg_night_vals.append(avg_n)

        # ── Draw chart ────────────────────────────────────────────────────────
        import numpy as np

        self._chart_fig.clear()
        ax = self._chart_fig.add_subplot(111)
        style_ax(ax, theme)

        # 3 groups, each with 2 bars (day + night), grouped
        bar_w = 0.3
        group_positions = np.array([0.0, 1.2, 2.4])  # spacing between groups
        day_x = group_positions - bar_w / 2
        night_x = group_positions + bar_w / 2

        bar_pad = 0.03  # small overshoot for avg lines beyond bar edges

        for i, d in enumerate(days):
            is_center = (i == 1)
            alpha = 1.0 if is_center else MUTED_ALPHA
            ax.bar(day_x[i], sol_day_vals[i], bar_w, color=COLOR_DAY,
                   alpha=alpha, zorder=3)
            ax.bar(night_x[i], sol_night_vals[i], bar_w, color=COLOR_NIGHT,
                   alpha=alpha, zorder=3)

            # Hour value labels inside (or above) bars
            for bx, bval in [(day_x[i], sol_day_vals[i]),
                             (night_x[i], sol_night_vals[i])]:
                if bval < 0.05:
                    continue  # skip near-zero bars
                txt_color = "#1a1a1a"  # dark text for readability on light bars
                if bval < 0.5:
                    # Too short — place above bar
                    ax.text(bx, bval + 0.05, f"{bval:.1f}",
                            ha="center", va="bottom", fontsize=7,
                            color=CHART_TEXT_SEC, alpha=alpha, zorder=5)
                else:
                    ax.text(bx, bval / 2, f"{bval:.1f}",
                            ha="center", va="center", fontsize=7,
                            color=txt_color, alpha=alpha, zorder=5,
                            fontweight="bold")

        # Highlight center group with subtle background
        ax.axvspan(group_positions[1] - 0.45, group_positions[1] + 0.45,
                   color=CHART_TEXT_SEC, alpha=0.08, zorder=1)

        # Average lines — restricted to each bar's own width
        # Both use the same warm orange-yellow; line style distinguishes them
        for i in range(3):
            line_alpha = 1.0 if i == 1 else 0.5
            # Day avg line (dashed) — over day bar only
            dl = day_x[i] - bar_w / 2 - bar_pad
            dr = day_x[i] + bar_w / 2 + bar_pad
            ax.hlines(avg_day_vals[i], dl, dr,
                      colors=COLOR_AVG_WARM, linewidths=1.5, linestyles="--",
                      zorder=4, alpha=line_alpha)
            # Night avg line (dotted) — over night bar only
            nl = night_x[i] - bar_w / 2 - bar_pad
            nr = night_x[i] + bar_w / 2 + bar_pad
            ax.hlines(avg_night_vals[i], nl, nr,
                      colors=COLOR_AVG_WARM, linewidths=1.5, linestyles=":",
                      zorder=4, alpha=line_alpha)
            # Value labels on center day's avg lines — badge style
            if i == 1:
                _badge = dict(boxstyle="round,pad=0.15", facecolor=COLOR_AVG_WARM,
                              edgecolor="none", alpha=0.9)
                if avg_day_vals[i] > 0.01:
                    ax.text(dr + 0.04, avg_day_vals[i], f"{avg_day_vals[i]:.1f}",
                            ha="left", va="center", fontsize=7,
                            color="#1a1a1a", fontweight="bold",
                            bbox=_badge, zorder=6)
                if avg_night_vals[i] > 0.01:
                    ax.text(nr + 0.04, avg_night_vals[i], f"{avg_night_vals[i]:.1f}",
                            ha="left", va="center", fontsize=7,
                            color="#1a1a1a", fontweight="bold",
                            bbox=_badge, zorder=6)

        # Compact legend
        from matplotlib.lines import Line2D
        legend_handles = [
            Line2D([0], [0], color=COLOR_AVG_WARM, linestyle="--", linewidth=1.5, label="Day avg"),
            Line2D([0], [0], color=COLOR_AVG_WARM, linestyle=":", linewidth=1.5, label="Night avg"),
        ]
        ax.legend(handles=legend_handles, fontsize=7, loc="upper right",
                  framealpha=0.7, facecolor=CHART_LEGEND_BG,
                  edgecolor=CHART_TEXT_SEC, labelcolor=CHART_TEXT_SEC)

        # X-axis labels — date for each group
        labels = [d.strftime("%a %d") for d in days]
        ax.set_xticks(group_positions)
        ax.set_xticklabels(labels, fontsize=9)
        # Bold the center label
        tick_labels = ax.get_xticklabels()
        if len(tick_labels) > 1:
            tick_labels[1].set_fontweight("bold")
            tick_labels[1].set_color(CHART_TEXT)

        ax.set_ylabel("Hours", fontsize=9)
        ax.set_ylim(bottom=0)
        ax.margins(y=0.1)
        apply_style(self._chart_fig, theme)
        self._chart_canvas.draw()

        # ── Diff label below chart (selected date only) ───────────────────────
        # Expected hours = avg rate × soldier's presence fraction for that day.
        center_day_fracs, center_night_fracs = compute_domain_fractions(
            self.db, [soldier.id], center, ns, ne,
        )
        sol_day_frac = center_day_fracs.get(soldier.id, 0.0)
        sol_night_frac = center_night_fracs.get(soldier.id, 0.0)
        day_diff = sol_day_vals[1] - avg_day_vals[1] * sol_day_frac
        night_diff = sol_night_vals[1] - avg_night_vals[1] * sol_night_frac

        def _fmt(diff: float) -> tuple[str, str]:
            sign = "+" if diff >= 0 else ""
            color = pal["diff_over"] if diff > 0 else pal["diff_under"] if diff < 0 else pal["text_secondary"]
            return f"{sign}{diff:.1f}h", color

        day_txt, day_c = _fmt(day_diff)
        night_txt, night_c = _fmt(night_diff)
        self._diff_lbl.setText("")  # clear plain text
        self._diff_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._diff_lbl.setText(
            f"<span style='color:{day_c};'>Day: {day_txt}</span>"
            f"&nbsp;&nbsp;|&nbsp;&nbsp;"
            f"<span style='color:{night_c};'>Night: {night_txt}</span>"
        )

    def _on_view_full_stats(self):
        if self._soldier_id is None:
            return
        soldier = self._soldier_svc.get_soldier(self._soldier_id)
        if not soldier:
            return
        from src.ui.dialogs.soldier_stats_dialog import SoldierStatsDialog
        dlg = SoldierStatsDialog(self.db, soldier, parent=self)
        dlg.exec()

    # ── Activity (requests + pending review) ───────────────────────────────────


    def _reload_activity(self):
        """Populate merged table with SoldierRequest rows and pending TaskAssignments."""
        if self._soldier_id is None:
            self._activity_table.setRowCount(0)
            return

        reqs = self._soldier_svc.get_soldier_requests(self._soldier_id)
        pending = ScheduleService(self.db).get_soldier_pending_review(self._soldier_id)

        total_rows = len(reqs) + len(pending)
        self._activity_table.setRowCount(total_rows)

        row_idx = 0
        # First, personal requests / notes
        for req in reqs:
            date_str = req.created_at.strftime('%d %b %Y') if req.created_at else '—'
            type_str = req.request_type or "NOTE"
            detail   = req.description or '—'
            status   = req.status or "PENDING"
            for col, val in enumerate([date_str, type_str, detail, status]):
                item = QTableWidgetItem(val)
                if col != 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                # Tag row as "request" so context menu knows what to offer
                item.setData(Qt.ItemDataRole.UserRole, ("request", req.id))
                if col == 2:
                    # Light status hint via colour primarily on detail text.
                    if req.status == 'PENDING':
                        item.setForeground(QColor('#ffd600'))
                    elif req.status == 'APPROVED':
                        item.setForeground(QColor('#4caf50'))
                    elif req.status == 'REJECTED':
                        item.setForeground(QColor('#ff5252'))
                self._activity_table.setItem(row_idx, col, item)
            row_idx += 1

        # Then, pending review assignments
        for asgn in pending:
            date_str = asgn.start_time.strftime('%d %b %H:%M') if asgn.start_time else '—'
            type_str = "UNPLANNED TASK"
            task_name = '—'
            if asgn.task:
                task_name = asgn.task.real_title or '—'
            status = "PENDING REVIEW"
            for col, val in enumerate([date_str, type_str, task_name, status]):
                item = QTableWidgetItem(val)
                if col != 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                # Tag row as "pending" with assignment id
                item.setData(Qt.ItemDataRole.UserRole, ("pending", asgn.id))
                if col == 2:
                    item.setForeground(QColor('#ffd600'))
                self._activity_table.setItem(row_idx, col, item)
            row_idx += 1

    def _reload_schedule(self):
        """Populate schedule table for the selected soldier and _schedule_date."""
        if self._soldier_id is None:
            self._today_table.setRowCount(0)
            return
        day = self._schedule_date
        start_dt = datetime.combine(day, time(0, 0))
        end_dt   = datetime.combine(day, time(23, 59, 59))

        assignments = ScheduleService(self.db).get_soldier_assignments(
            self._soldier_id, start=start_dt, end=end_dt,
        )
        # Merge adjacent assignments for the same task into a single display
        # row so that e.g. 17:00–18:00 Guard + 18:00–19:00 Guard shows as
        # a single 17:00–19:00 Guard row.  Assignments are already ordered
        # by start_time; we merge only when end == next start and task matches.
        merged: list[dict] = []
        for asgn in assignments:
            task_name = (
                asgn.task.real_title
                if asgn.task and asgn.task.real_title
                else f"Task #{asgn.task_id}" if asgn.task_id else "—"
            )
            pending = getattr(asgn, "pending_review", False)
            if (
                merged
                and merged[-1]["task_id"] == asgn.task_id
                and merged[-1]["end"] == asgn.start_time
                and merged[-1]["pending"] == pending
            ):
                merged[-1]["end"] = asgn.end_time
            else:
                merged.append({
                    "start": asgn.start_time,
                    "end": asgn.end_time,
                    "task_id": asgn.task_id,
                    "task_name": task_name,
                    "pending": pending,
                })

        self._today_table.setRowCount(len(merged))
        for row, m in enumerate(merged):
            if m["start"] and m["end"]:
                time_str = f"{m['start'].strftime('%H:%M')} – {m['end'].strftime('%H:%M')}"
            elif m["start"]:
                time_str = m["start"].strftime('%H:%M')
            else:
                time_str = "—"
            flags_str = "PENDING REVIEW" if m["pending"] else ""
            for col, val in enumerate([time_str, m["task_name"], flags_str]):
                item = QTableWidgetItem(val)
                if col != 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._today_table.setItem(row, col, item)

    def _on_pend_double_click(self, index):
        item = self._activity_table.item(index.row(), 0)
        if not item:
            return
        kind, asgn_id = item.data(Qt.ItemDataRole.UserRole) or (None, None)
        if kind != "pending":
            return
        if asgn_id is None:
            return
        asgn = ScheduleService(self.db).get_assignment(asgn_id)
        if not asgn:
            return
        from src.ui.dialogs.pending_review_dialog import PendingReviewDialog
        dlg = PendingReviewDialog(self.db, asgn, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # Dialog handles marking as reviewed; just refresh tables.
            self._reload_activity()
            self._reload_schedule()

    # ── Request actions ───────────────────────────────────────────────────────

    def _on_add_request(self):
        if self._soldier_id is None:
            return
        dlg = _AddRequestDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._soldier_svc.create_request(
                soldier_id=self._soldier_id,
                request_type=dlg.req_type(),
                description=dlg.description(),
            )
            self._reload_activity()

    def _on_req_context_menu(self, pos):
        from PyQt6.QtWidgets import QMenu
        row = self._activity_table.rowAt(pos.y())
        if row < 0:
            return
        item = self._activity_table.item(row, 0)
        if not item:
            return
        kind, req_id = item.data(Qt.ItemDataRole.UserRole) or (None, None)
        if kind != "request":
            # Only SoldierRequest rows get a context menu
            return

        menu = QMenu(self)
        for status in self.REQUEST_STATUSES:
            act = menu.addAction(f"Mark as {status}")
            act.setData(('status', req_id, status))
        menu.addSeparator()
        del_act = menu.addAction("Delete this entry")
        del_act.setData(('delete', req_id, None))

        chosen = menu.exec(self._activity_table.viewport().mapToGlobal(pos))
        if not chosen:
            return
        action, rid, payload = chosen.data()
        if action == 'status':
            fields = {'status': payload}
            if payload in ('APPROVED', 'REJECTED', 'NOTED'):
                fields['resolved_at'] = datetime.now()
            self._soldier_svc.update_request(rid, **fields)
        elif action == 'delete':
            self._soldier_svc.delete_request(rid)
        self._reload_activity()

    # ── Other actions ─────────────────────────────────────────────────────────
    def _on_edit(self):
        self._open_soldier_dialog()

    def _on_leave_coverage(self):
        if self._soldier_id is None:
            return
        soldier = self._soldier_svc.get_soldier(self._soldier_id)
        if not soldier:
            return
        from src.ui.dialogs.leave_manager_dialog import LeaveManagerDialog
        dlg = LeaveManagerDialog(self.db, soldier=soldier, main_window=self.mw, parent=self)
        dlg.exec()

    def _on_gear_list(self):
        if self._soldier_id is None:
            return
        soldier = self._soldier_svc.get_soldier(self._soldier_id)
        if not soldier:
            return
        from src.ui.dialogs.gear_list_dialog import GearListDialog
        dlg = GearListDialog(self.db, soldier=soldier, parent=self)
        dlg.exec()

    def _open_soldier_dialog(self):
        if self._soldier_id is None:
            return
        soldier = self._soldier_svc.get_soldier(self._soldier_id)
        if not soldier:
            return
        from src.ui.dialogs.soldier_dialog import SoldierDialog
        dlg = SoldierDialog(self.db, soldier=soldier, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self._soldier_svc.expire(soldier)
            self.load(self._soldier_svc.get_soldier(self._soldier_id))


# ── Inbox panel ───────────────────────────────────────────────────────────────

class InboxPanel(QWidget):
    def __init__(self, db, main_window, parent=None):
        super().__init__(parent)
        self.db = db
        self.mw = main_window
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        hdr = QLabel("PENDING REVIEWS")
        hdr.setObjectName("sectionHeader")
        layout.addWidget(hdr)

        hint = QLabel("Self-reported unplanned tasks waiting for commander approval.")
        hint.setObjectName("dimLabel")
        layout.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._cards_widget = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._cards_widget)
        layout.addWidget(scroll, 1)

        self.refresh()

    def refresh(self):
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        pending = ScheduleService(self.db).get_pending_review_assignments()

        if not pending:
            lbl = QLabel("✓  All clear — no pending reviews.")
            lbl.setStyleSheet("color: #4caf50; font-size: 13px; padding: 20px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._cards_layout.addWidget(lbl)
            return

        for asgn in pending:
            self._cards_layout.addWidget(self._make_card(asgn))

    def _make_card(self, asgn: TaskAssignment) -> QWidget:
        card = QFrame()
        card.setStyleSheet(
            "background-color: #111f11; border: 1px solid #2e7d32; padding: 6px;"
        )
        clayout = QVBoxLayout(card)
        clayout.setSpacing(4)

        soldier  = asgn.soldier
        task     = asgn.task
        sol_name  = _name(soldier) if soldier else "Unknown"
        task_name = (task.real_title or f"Task #{task.id}") if task else "Unknown task"
        time_str  = (
            f"{asgn.start_time.strftime('%d %b %H:%M')} – "
            f"{asgn.end_time.strftime('%H:%M')}"
        ) if asgn.start_time else "?"

        info = QLabel(
            f"<b>{sol_name}</b>  reports:  <span style='color:#ffd600'>{task_name}</span>"
            f"<br><span style='font-size:12px; color:#4caf50'>{time_str}"
            f"  •  {asgn.final_weight_applied:.2f} pts</span>"
        )
        info.setTextFormat(Qt.TextFormat.RichText)
        clayout.addWidget(info)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        approve_btn = QPushButton("✓  APPROVE")
        approve_btn.setObjectName("approveBtn")
        reject_btn  = QPushButton("✗  REJECT")
        reject_btn.setObjectName("rejectBtn")
        btn_row.addWidget(approve_btn)
        btn_row.addWidget(reject_btn)
        clayout.addLayout(btn_row)

        approve_btn.clicked.connect(lambda _, aid=asgn.id: self._review(aid, True))
        reject_btn.clicked.connect(lambda _, aid=asgn.id: self._review(aid, False))

        return card

    def _review(self, assignment_id: int, approved: bool):
        from src.core.unit_manager import UnitManager
        um  = UnitManager(self.db)
        msg = um.review_unplanned_task(assignment_id, approved)
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.information(self, "Review", msg)
        self.refresh()
        self.mw._update_inbox_badge()


# ── Soldiers tab ──────────────────────────────────────────────────────────────

class SoldiersTab(QWidget):
    def __init__(self, db, main_window):
        super().__init__()
        self.db = db
        self._config_svc = ConfigService(db)
        self._soldier_svc = SoldierService(db)
        self.mw = main_window
        self._roster_date = date.today()
        self._setup_ui()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Pill selector ──────────────────────────────────────────────────────
        pill_bar = QWidget()
        pill_bar.setObjectName("pillBar")
        pill_bar.setFixedHeight(48)
        pill_layout = QHBoxLayout(pill_bar)
        pill_layout.setContentsMargins(12, 6, 12, 6)
        pill_layout.setSpacing(8)

        self._roster_btn = QPushButton("SOLDIERS")
        self._inbox_btn  = QPushButton("INBOX")
        for b in (self._roster_btn, self._inbox_btn):
            b.setCheckable(True)
            b.setFixedWidth(110)
            b.setObjectName("pillBtn")
        self._roster_btn.setChecked(True)
        pill_layout.addWidget(self._roster_btn)
        pill_layout.addWidget(self._inbox_btn)
        pill_layout.addStretch()

        self._add_soldier_btn = QPushButton("[ + NEW SOLDIER ]")
        self._remove_soldier_btn = QPushButton("[ REMOVE SOLDIER ]")
        self._remove_soldier_btn.setEnabled(False)
        pill_layout.addWidget(self._add_soldier_btn)
        pill_layout.addWidget(self._remove_soldier_btn)

        outer.addWidget(pill_bar)

        # ── Date navigation (for roster view) ──────────────────────────────────
        self._date_bar = QWidget()
        date_layout = QHBoxLayout(self._date_bar)
        date_layout.setContentsMargins(12, 4, 12, 4)
        date_layout.setSpacing(8)

        self._roster_prev_btn = QToolButton()
        self._roster_prev_btn.setArrowType(Qt.ArrowType.LeftArrow)
        self._roster_prev_btn.setFixedSize(28, 24)
        self._roster_prev_btn.setToolTip("Previous day")
        self._roster_prev_btn.clicked.connect(self._roster_prev_day)
        date_layout.addWidget(self._roster_prev_btn)

        self._roster_date_edit = QDateEdit(QDate.currentDate())
        self._roster_date_edit.setCalendarPopup(True)
        self._roster_date_edit.setDisplayFormat("ddd dd MMM yyyy")
        self._roster_date_edit.setFixedWidth(190)
        self._roster_date_edit.dateChanged.connect(self._on_roster_date_changed)
        date_layout.addWidget(self._roster_date_edit)

        self._roster_next_btn = QToolButton()
        self._roster_next_btn.setArrowType(Qt.ArrowType.RightArrow)
        self._roster_next_btn.setFixedSize(28, 24)
        self._roster_next_btn.setToolTip("Next day")
        self._roster_next_btn.clicked.connect(self._roster_next_day)
        date_layout.addWidget(self._roster_next_btn)

        self._roster_today_btn = QPushButton("TODAY")
        self._roster_today_btn.setFixedWidth(90)
        self._roster_today_btn.setToolTip("Jump to today")
        self._roster_today_btn.clicked.connect(self._roster_go_today)
        date_layout.addWidget(self._roster_today_btn)

        date_layout.addStretch()
        outer.addWidget(self._date_bar)

        # ── Unit workload strip ────────────────────────────────────────────────
        self._workload_lbl = QLabel()
        self._workload_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self._workload_lbl)

        # ── Stacked content ────────────────────────────────────────────────────
        self._stack = QStackedWidget()

        # Page 0: roster
        roster_page   = QWidget()
        roster_layout = QHBoxLayout(roster_page)
        roster_layout.setContentsMargins(0, 0, 0, 0)
        roster_layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Roster table
        table_widget = QWidget()
        tl = QVBoxLayout(table_widget)
        tl.setContentsMargins(8, 8, 8, 8)

        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            ["NAME", "ROLES", "DAY +/−", "NIGHT +/−", "STATUS"]
        )
        hdr = self._table.horizontalHeader()
        _rate_tip = "Hours/present-day relative to unit average. Positive = worked more than average."
        for col_idx in (2, 3):
            hdr_item = self._table.horizontalHeaderItem(col_idx)
            if hdr_item:
                hdr_item.setToolTip(_rate_tip)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)   # NAME
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)            # ROLES
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)   # DAY +/-
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)   # NIGHT +/-
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)   # STATUS
        hdr.resizeSection(2, 80)
        hdr.resizeSection(3, 80)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.setSortingEnabled(True)
        hdr.setSortIndicatorShown(True)
        tl.addWidget(self._table)

        self._rate_hint = QLabel(
            "DAY +/\u2212 and NIGHT +/\u2212 show avg hours per present day, "
            "relative to the unit average. Positive = more than average."
        )
        self._rate_hint.setWordWrap(True)
        tl.addWidget(self._rate_hint)

        splitter.addWidget(table_widget)

        self._detail = SoldierDetailPanel(self.db, self.mw)
        splitter.addWidget(self._detail)
        splitter.setSizes([600, 440])

        roster_layout.addWidget(splitter)
        self._stack.addWidget(roster_page)

        # Page 1: inbox
        self._inbox = InboxPanel(self.db, self.mw)
        self._stack.addWidget(self._inbox)

        outer.addWidget(self._stack, 1)

        # Connect
        self._roster_btn.clicked.connect(lambda: self._switch_view(0))
        self._inbox_btn.clicked.connect(lambda: self._switch_view(1))
        self._table.itemSelectionChanged.connect(self._on_selection)
        self._table.doubleClicked.connect(self._on_row_double_click)
        self._add_soldier_btn.clicked.connect(self._on_add_soldier)
        self._remove_soldier_btn.clicked.connect(self._on_remove_soldier)

        # Left/right arrow keys on table → move detail panel date
        self._table.installEventFilter(self)

        self._rebuild_roster()

    def eventFilter(self, obj, event):
        """Intercept left/right arrow keys on the roster table to move the detail panel date."""
        if obj is self._table and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Left:
                if self._detail._soldier_id is not None:
                    self._detail._schedule_prev_day()
                return True
            elif key == Qt.Key.Key_Right:
                if self._detail._soldier_id is not None:
                    self._detail._schedule_next_day()
                return True
        return super().eventFilter(obj, event)

    def _switch_view(self, idx: int):
        self._stack.setCurrentIndex(idx)
        self._roster_btn.setChecked(idx == 0)
        self._inbox_btn.setChecked(idx == 1)
        if idx == 1:
            self._inbox.refresh()

    def _roster_prev_day(self):
        self._roster_date -= timedelta(days=1)
        self._roster_date_edit.setDate(
            QDate(self._roster_date.year, self._roster_date.month, self._roster_date.day)
        )
        self._rebuild_roster()

    def _roster_next_day(self):
        self._roster_date += timedelta(days=1)
        self._roster_date_edit.setDate(
            QDate(self._roster_date.year, self._roster_date.month, self._roster_date.day)
        )
        self._rebuild_roster()

    def _roster_go_today(self):
        self._roster_date = date.today()
        self._roster_date_edit.setDate(
            QDate(self._roster_date.year, self._roster_date.month, self._roster_date.day)
        )
        self._rebuild_roster()

    def _on_roster_date_changed(self, qdate: QDate):
        self._roster_date = qdate.toPyDate()
        self._rebuild_roster()

    def _resolve_commander_id(self, config, soldiers) -> int | None:
        """Resolve the active commander for the roster date using the chain of command."""
        chain = config.command_chain if config else []
        if not chain:
            return None
        midday = datetime.combine(self._roster_date, time(12, 0))
        presence_map: dict[int, list[tuple[datetime, datetime]]] = {}
        for sid in set(chain):
            intervals = self._soldier_svc.get_presence_intervals_for_status(
                sid, "PRESENT", midday, midday,
            )
            presence_map[sid] = [(iv.start_time, iv.end_time) for iv in intervals]
        return resolve_active_commander(chain, presence_map, midday)

    def _rebuild_roster(self):
        self._table.setSortingEnabled(False)
        config = self._config_svc.get_config()
        theme = config.theme or "dark"
        status_colors = _STATUS_TEXT_COLORS.get(theme, _STATUS_TEXT_COLORS["dark"])

        ref_date = self._roster_date

        soldiers = self._soldier_svc.list_active_soldiers()

        # Resolve active commander for display
        cmdr_id = self._resolve_commander_id(config, soldiers)

        self._table.setRowCount(len(soldiers))
        for row, s in enumerate(soldiers):
            pending = ScheduleService(self.db).count_soldier_pending_review(s.id)

            display_name = s.name or f"#{s.id}"
            roles_str  = ", ".join(r for r in (s.role or []) if r != "Soldier") or "—"

            # Presence status from grid semantics
            code = self._soldier_svc.get_daily_status_code(s.id, ref_date)
            if code == "a":
                pres_label = "Present"
                color_key = "present"
            elif code == "b":
                pres_label = "Partially present"
                color_key = "partial_day"
            elif code == "c":
                pres_label = "Absent"
                color_key = "absent"
            else:
                pres_label = "No data"
                color_key = "inactive"
            pres_color = status_colors.get(color_key, status_colors["present"])

            if pending:
                status = f"{pres_label} • ⚠ REVIEW"
                status_color = status_colors["partial_day"]  # amber
            else:
                status = pres_label
                status_color = pres_color

            is_cmdr = s.id == cmdr_id

            day_val = s.total_day_points or 0.0
            night_val = s.total_night_points or 0.0
            day_str = f"{day_val:+.2f}" if day_val != 0.0 else "0.00"
            night_str = f"{night_val:+.2f}" if night_val != 0.0 else "0.00"

            row_data = [
                display_name,
                roles_str,
                day_str,
                night_str,
                status,
            ]
            for col, val in enumerate(row_data):
                item = _NumericItem(val) if col in (2, 3) else QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 0 and is_cmdr:
                    item.setBackground(QColor('#5c4a1e') if theme == 'dark' else QColor('#fff3c4'))
                    item.setForeground(QColor('#ffd54f') if theme == 'dark' else QColor('#6d4c00'))
                    bold_font = item.font()
                    bold_font.setBold(True)
                    item.setFont(bold_font)
                if col in (2, 3):
                    v = day_val if col == 2 else night_val
                    item.setBackground(QColor(_rate_bg_color(v, theme)))
                    item.setForeground(QColor(_rate_fg_color(v, theme)))
                if col == 4:
                    item.setForeground(QColor(status_color))
                item.setData(Qt.ItemDataRole.UserRole, s.id)
                self._table.setItem(row, col, item)
        self._table.setSortingEnabled(True)
        self._update_workload_strip()

    def _update_workload_strip(self):
        """Compute and display unit-level day/night weighted avg hours per soldier."""
        from src.domain.presence_calc import (
            compute_domain_fractions, count_present_partial, weighted_avg_sd,
        )

        config = self._config_svc.get_config()
        ns = config.night_start_hour or 23
        ne = config.night_end_hour or 7

        ref = self._roster_date
        day_start = datetime.combine(ref, time(0, 0))
        day_end = datetime.combine(ref + timedelta(days=1), time(0, 0))
        ns_dt = datetime.combine(ref, time(ns, 0))
        ne_dt = datetime.combine(ref + timedelta(days=1), time(ne, 0))

        active_soldiers = self._soldier_svc.list_active_soldiers()
        soldier_ids = [s.id for s in active_soldiers]

        all_asgns = ScheduleService(self.db).get_assignments_for_soldiers(
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
        self._rate_hint.setStyleSheet(_HINT_STYLE.get(theme, _HINT_STYLE["dark"]))

    def _on_selection(self):
        rows = self._table.selectedItems()
        if not rows:
            self._remove_soldier_btn.setEnabled(False)
            return
        soldier_id = rows[0].data(Qt.ItemDataRole.UserRole)
        soldier = self._soldier_svc.get_soldier(soldier_id)
        if soldier:
            self._detail.load(soldier, initial_date=self._roster_date)
            self._remove_soldier_btn.setEnabled(True)

    def _on_row_double_click(self, index):
        item = self._table.item(index.row(), 0)
        if not item:
            return
        soldier_id = item.data(Qt.ItemDataRole.UserRole)
        soldier = self._soldier_svc.get_soldier(soldier_id)
        if not soldier:
            return
        from src.ui.dialogs.soldier_dialog import SoldierDialog
        dlg = SoldierDialog(self.db, soldier=soldier, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self._rebuild_roster()
            self._detail.load(self._soldier_svc.get_soldier(soldier_id))

    def _on_add_soldier(self):
        from src.ui.dialogs.soldier_dialog import SoldierDialog
        dlg = SoldierDialog(self.db, soldier=None, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self._rebuild_roster()
            if dlg.soldier:
                self._detail.load(dlg.soldier)

    def _on_remove_soldier(self):
        """Soft-remove the selected soldier from Kav by marking them inactive."""
        rows = self._table.selectedItems()
        if not rows:
            QMessageBox.information(self, "Remove Soldier", "Please select a soldier first.")
            return
        soldier_id = rows[0].data(Qt.ItemDataRole.UserRole)
        if soldier_id is None:
            return
        soldier = self._soldier_svc.get_soldier(soldier_id)
        if not soldier:
            QMessageBox.warning(self, "Remove Soldier", "Could not find selected soldier in database.")
            return

        name = soldier.name or f"#{soldier.id}"
        ans = QMessageBox.question(
            self,
            "Remove Soldier",
            f"Remove <b>{name}</b> from KavManager roster?\n\n"
            "This will hide them from the roster and planners, but keep past data.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        try:
            self._soldier_svc.soft_delete_soldier(soldier_id)
        except Exception as exc:
            try:
                self._soldier_svc.rollback()
            except Exception:
                pass
            QMessageBox.critical(self, "Remove Soldier", f"Could not update soldier:\n{exc}")
            return

        # Refresh roster and clear detail panel selection
        self._rebuild_roster()
        self._table.clearSelection()
        self._detail._soldier_id = None
        self._detail._name_lbl.setText("Select a soldier")
        self._detail._roles_lbl.setText("")
        self._detail._stats_row.setText("Active: — | Present: —")
        self._detail._diff_lbl.setText("")
        self._detail._chart_fig.clear()
        self._detail._chart_canvas.draw()
        self._detail._stats_btn.setEnabled(False)
        self._remove_soldier_btn.setEnabled(False)

    def refresh(self):
        self._rebuild_roster()
        # Re-render detail panel chart (e.g. after theme switch)
        if self._detail._soldier_id is not None:
            self._detail._refresh_date_views()
        if self._stack.currentIndex() == 1:
            self._inbox.refresh()
