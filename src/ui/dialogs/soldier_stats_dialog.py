"""
Soldier Stats Dialog — detailed statistics for a single soldier.

Sections:
  1. Header (name, roles, active/present days, total hours, Day/Night/Total +/-)
  2. Hours summary with unit-average comparison
  3. Hours over time (stacked by hardness)
  4. Key metrics (rank, std dev)
"""
from __future__ import annotations

import math
from datetime import datetime, date, time, timedelta
from collections import defaultdict

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QScrollArea, QWidget, QFrame, QToolButton,
)
from PyQt6.QtCore import Qt

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
import numpy as np

from src.core.models import Soldier, TaskAssignment, Task
from src.services.config_service import ConfigService
from src.services.schedule_service import ScheduleService
from src.services.soldier_service import SoldierService
from src.ui.charts.chart_style import (
    create_figure, style_ax, apply_style, get_palette, current_theme,
    COLOR_DAY, COLOR_NIGHT, COLOR_AVG_LINE,
    HARDNESS_COLORS, CHART_LEGEND_BG, CHART_TEXT_SEC, CHART_TEXT_DIM,
)
from src.domain.presence_calc import (
    compute_domain_fractions, weighted_avg_sd, domain_window_hours,
)
from src.domain.reserve_period import resolve_reserve_period


def _name(s: Soldier) -> str:
    return s.name or f"#{s.id}"


def _is_night(dt: datetime, night_start: int, night_end: int) -> bool:
    return dt.hour >= night_start or dt.hour < night_end


# ── Data helpers ──────────────────────────────────────────────────────────────

def _fetch_assignments(db, soldier_id: int,
                       start: datetime | None, end: datetime | None):
    """Return TaskAssignment rows for a soldier, optionally filtered by period."""
    return ScheduleService(db).get_soldier_assignments(soldier_id, start, end)


def _fetch_all_active_assignments(db, start: datetime | None,
                                  end: datetime | None):
    """Return all assignments for active soldiers in a period."""
    return ScheduleService(db).get_all_active_assignments(start, end)


def _split_day_night_hours(asgn: TaskAssignment,
                           night_start: int, night_end: int) -> tuple[float, float]:
    """Split an assignment into day-hours and night-hours."""
    if not asgn.start_time or not asgn.end_time:
        return 0.0, 0.0
    day_h = 0.0
    night_h = 0.0
    cursor = asgn.start_time
    step = timedelta(minutes=15)
    while cursor < asgn.end_time:
        slice_end = min(cursor + step, asgn.end_time)
        h = (slice_end - cursor).total_seconds() / 3600
        if _is_night(cursor, night_start, night_end):
            night_h += h
        else:
            day_h += h
        cursor = slice_end
    return day_h, night_h


def _period_bounds(mode: str, ref_date: date, db=None) -> tuple[datetime | None, datetime | None]:
    """Return (start, end) datetimes for the selected period mode + reference date."""
    if mode == "Whole period":
        if db is not None:
            rp_start, rp_end = resolve_reserve_period(db)
            return rp_start, rp_end
        return None, None
    elif mode == "Day":
        start = datetime.combine(ref_date, time(0, 0))
        end = start + timedelta(days=1)
        return start, end
    elif mode == "Week":
        monday = ref_date - timedelta(days=ref_date.weekday())
        start = datetime.combine(monday, time(0, 0))
        end = start + timedelta(days=7)
        return start, end
    elif mode == "Month":
        start = datetime.combine(ref_date.replace(day=1), time(0, 0))
        if ref_date.month == 12:
            end_date = ref_date.replace(year=ref_date.year + 1, month=1, day=1)
        else:
            end_date = ref_date.replace(month=ref_date.month + 1, day=1)
        end = datetime.combine(end_date, time(0, 0))
        return start, end
    return None, None


def _ordinal(n: int) -> str:
    if n % 100 in (11, 12, 13):
        return f"{n}th"
    return f"{n}{['th','st','nd','rd'][n%10] if n%10 < 4 else 'th'}"


class SoldierStatsDialog(QDialog):
    """Full stats dialog for a single soldier."""

    def __init__(self, db, soldier: Soldier, parent=None):
        super().__init__(parent)
        self.db = db
        self.soldier = soldier
        self._soldier_svc = SoldierService(db)

        config = ConfigService(db).get_config()
        self._night_start = config.night_start_hour if config else 23
        self._night_end = config.night_end_hour if config else 7
        self._theme = config.theme if config else "dark"
        self._pal = get_palette(self._theme)

        self._period_mode = "Whole period"
        self._ref_date = date.today()
        self._weighted = True

        self.setModal(True)
        self.setMinimumSize(720, 780)
        self.setWindowTitle(f"STATS — {_name(soldier).upper()}")
        self._setup_ui()
        self._refresh()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        # ── Control bar row 1: period + navigation ───────────────────────────
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)

        ctrl.addWidget(QLabel("Period:"))
        self._period_combo = QComboBox()
        self._period_combo.addItems(["Whole period", "Month", "Week", "Day"])
        self._period_combo.currentTextChanged.connect(self._on_period_changed)
        ctrl.addWidget(self._period_combo)

        self._nav_prev = QToolButton()
        self._nav_prev.setArrowType(Qt.ArrowType.LeftArrow)
        self._nav_prev.setFixedSize(28, 24)
        self._nav_prev.clicked.connect(self._nav_backward)
        ctrl.addWidget(self._nav_prev)

        self._period_label = QLabel()
        self._period_label.setMinimumWidth(140)
        self._period_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ctrl.addWidget(self._period_label)

        self._nav_next = QToolButton()
        self._nav_next.setArrowType(Qt.ArrowType.RightArrow)
        self._nav_next.setFixedSize(28, 24)
        self._nav_next.clicked.connect(self._nav_forward)
        ctrl.addWidget(self._nav_next)

        ctrl.addSpacing(16)
        ctrl.addWidget(QLabel("View:"))
        self._view_combo = QComboBox()
        self._view_combo.addItems(["Combined", "Day vs Night"])
        self._view_combo.currentTextChanged.connect(lambda _: self._refresh())
        ctrl.addWidget(self._view_combo)

        ctrl.addStretch()
        root.addLayout(ctrl)

        # ── Control bar row 2: weighted / absolute toggle ──────────────────
        ctrl2 = QHBoxLayout()
        ctrl2.setSpacing(0)

        self._btn_weighted = QPushButton("Weighted")
        self._btn_absolute = QPushButton("Absolute")
        for btn in (self._btn_weighted, self._btn_absolute):
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.setMinimumWidth(80)
        self._btn_weighted.setChecked(True)
        self._btn_weighted.clicked.connect(lambda: self._set_weight_mode(True))
        self._btn_absolute.clicked.connect(lambda: self._set_weight_mode(False))
        self._update_toggle_style()

        ctrl2.addWidget(self._btn_weighted)
        ctrl2.addWidget(self._btn_absolute)
        ctrl2.addSpacing(8)

        hint = QLabel("Weighted: adjusts for partial presence. Absolute: raw total hours.")
        hint.setStyleSheet("font-size: 10px; color: #9a8e7e;")
        ctrl2.addWidget(hint)
        ctrl2.addStretch()
        root.addLayout(ctrl2)

        # ── Scrollable body ───────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(12)
        scroll.setWidget(self._body)
        root.addWidget(scroll, 1)

        # ── Close button ──────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("[ CLOSE ]")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── Navigation ────────────────────────────────────────────────────────────

    def _on_period_changed(self, text: str):
        self._period_mode = text
        nav_visible = text != "Whole period"
        self._nav_prev.setVisible(nav_visible)
        self._nav_next.setVisible(nav_visible)
        self._refresh()

    def _set_weight_mode(self, weighted: bool):
        self._weighted = weighted
        self._btn_weighted.setChecked(weighted)
        self._btn_absolute.setChecked(not weighted)
        self._update_toggle_style()
        self._refresh()

    def _update_toggle_style(self):
        """Style the toggle pair as a segmented control."""
        active = (
            "background:#3d6b3d; color:#e0e0e0; border:1px solid #4a8a4a;"
            "font-weight:bold;"
        )
        inactive = (
            "background:#2a2a2a; color:#888; border:1px solid #444;"
        )
        if self._theme == "light":
            active = (
                "background:#4a8a4a; color:#fff; border:1px solid #3d6b3d;"
                "font-weight:bold;"
            )
            inactive = (
                "background:#e0e0e0; color:#666; border:1px solid #bbb;"
            )
        self._btn_weighted.setStyleSheet(
            f"QPushButton {{ {active if self._weighted else inactive}"
            f" border-top-left-radius:4px; border-bottom-left-radius:4px;"
            f" border-top-right-radius:0; border-bottom-right-radius:0; padding:2px 10px; }}"
        )
        self._btn_absolute.setStyleSheet(
            f"QPushButton {{ {inactive if self._weighted else active}"
            f" border-top-right-radius:4px; border-bottom-right-radius:4px;"
            f" border-top-left-radius:0; border-bottom-left-radius:0; padding:2px 10px; }}"
        )

    def _nav_backward(self):
        if self._period_mode == "Day":
            self._ref_date -= timedelta(days=1)
        elif self._period_mode == "Week":
            self._ref_date -= timedelta(weeks=1)
        elif self._period_mode == "Month":
            if self._ref_date.month == 1:
                self._ref_date = self._ref_date.replace(year=self._ref_date.year - 1, month=12)
            else:
                self._ref_date = self._ref_date.replace(month=self._ref_date.month - 1)
        self._refresh()

    def _nav_forward(self):
        if self._period_mode == "Day":
            self._ref_date += timedelta(days=1)
        elif self._period_mode == "Week":
            self._ref_date += timedelta(weeks=1)
        elif self._period_mode == "Month":
            if self._ref_date.month == 12:
                self._ref_date = self._ref_date.replace(year=self._ref_date.year + 1, month=1)
            else:
                self._ref_date = self._ref_date.replace(month=self._ref_date.month + 1)
        self._refresh()

    # ── Refresh all sections ──────────────────────────────────────────────────

    def _refresh(self):
        # Clear body
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        start, end = _period_bounds(self._period_mode, self._ref_date, db=self.db)
        self._update_period_label(start, end)

        soldier_asgns = _fetch_assignments(self.db, self.soldier.id, start, end)
        all_asgns = _fetch_all_active_assignments(self.db, start, end)

        ns, ne = self._night_start, self._night_end
        sol_day_h = sol_night_h = 0.0
        sol_hardness_day: dict[int, float] = defaultdict(float)
        sol_hardness_night: dict[int, float] = defaultdict(float)
        for a in soldier_asgns:
            dh, nh = _split_day_night_hours(a, ns, ne)
            sol_day_h += dh
            sol_night_h += nh
            hardness = a.task.hardness if a.task else 3
            sol_hardness_day[hardness] += dh
            sol_hardness_night[hardness] += nh

        per_soldier_day: dict[int, float] = defaultdict(float)
        per_soldier_night: dict[int, float] = defaultdict(float)
        per_soldier_total: dict[int, float] = defaultdict(float)
        for a in all_asgns:
            dh, nh = _split_day_night_hours(a, ns, ne)
            per_soldier_day[a.soldier_id] += dh
            per_soldier_night[a.soldier_id] += nh
            per_soldier_total[a.soldier_id] += dh + nh

        active_soldiers = self._soldier_svc.list_active_soldiers()
        active_ids = [s.id for s in active_soldiers]
        n_active = max(len(active_ids), 1)

        # Compute presence-weighted or absolute averages for the period.
        if self._weighted and start and end:
            # Aggregate fractions across all days in the period.
            day_frac_sum, night_frac_sum = self._aggregate_period_fracs(
                active_ids, start, end, ns, ne,
            )
            avg_day, _ = weighted_avg_sd(per_soldier_day, day_frac_sum)
            avg_night, _ = weighted_avg_sd(per_soldier_night, night_frac_sum)
            combined_fracs = {
                sid: day_frac_sum.get(sid, 0.0) + night_frac_sum.get(sid, 0.0)
                for sid in active_ids
            }
            avg_total, _ = weighted_avg_sd(per_soldier_total, combined_fracs)
        else:
            avg_day = sum(per_soldier_day.values()) / n_active
            avg_night = sum(per_soldier_night.values()) / n_active
            avg_total = sum(per_soldier_total.values()) / n_active

        split = self._view_combo.currentText() == "Day vs Night"

        self._add_header(sol_day_h, sol_night_h, start, end)
        self._add_hours_summary(sol_day_h, sol_night_h, avg_day, avg_night, avg_total)
        self._add_hours_chart(
            soldier_asgns, all_asgns, active_ids, start, end, split,
            sol_hardness_day, sol_hardness_night,
        )
        self._add_key_metrics(
            per_soldier_day, per_soldier_night, per_soldier_total,
            active_ids, n_active, start, end,
        )
        self._body_layout.addStretch()

    def _aggregate_period_fracs(
        self, soldier_ids, start, end, ns, ne,
    ) -> tuple[dict[int, float], dict[int, float]]:
        """Sum per-day presence fractions across all days in [start, end)."""
        day_frac_sum: dict[int, float] = {sid: 0.0 for sid in soldier_ids}
        night_frac_sum: dict[int, float] = {sid: 0.0 for sid in soldier_ids}
        cur = start.date()
        end_d = end.date()
        while cur < end_d:
            df, nf = compute_domain_fractions(self.db, soldier_ids, cur, ns, ne)
            for sid in soldier_ids:
                day_frac_sum[sid] += df.get(sid, 0.0)
                night_frac_sum[sid] += nf.get(sid, 0.0)
            cur += timedelta(days=1)
        return day_frac_sum, night_frac_sum

    def _update_period_label(self, start, end):
        if self._period_mode == "Whole period":
            if start and end:
                self._period_label.setText(
                    f"{start.strftime('%d %b')} – {end.strftime('%d %b %Y')}"
                )
            else:
                self._period_label.setText("All data")
        elif self._period_mode == "Day":
            self._period_label.setText(self._ref_date.strftime("%d %b %Y"))
        elif self._period_mode == "Week":
            if start and end:
                self._period_label.setText(
                    f"{start.strftime('%d %b')} – {(end - timedelta(days=1)).strftime('%d %b %Y')}"
                )
        elif self._period_mode == "Month":
            self._period_label.setText(self._ref_date.strftime("%B %Y"))

    # ── Section 1: Header ─────────────────────────────────────────────────────

    def _add_header(self, sol_day_h: float, sol_night_h: float, start, end):
        p = self._pal
        s = self.soldier
        roles = ", ".join(r for r in (s.role or []) if r != "Soldier") or "None"
        total = sol_day_h + sol_night_h

        # +/- values from persisted columns
        day_pm = s.total_day_points or 0.0
        night_pm = s.total_night_points or 0.0
        total_pm = day_pm + night_pm

        def _pm_html(val: float, label: str) -> str:
            if val > 0.01:
                c = p["diff_over"]
                sign = "+"
            elif val < -0.01:
                c = p["diff_under"]
                sign = ""
            else:
                c = p["text_secondary"]
                sign = ""
            return (
                f"<span style='color:{p['text_dim']}; font-size:11px;'>{label}</span> "
                f"<span style='color:{c}; font-size:13px; font-weight:bold;'>"
                f"{sign}{val:.2f}</span>"
            )

        pm_line = (
            f"{_pm_html(day_pm, 'Day +/\u2212:')}"
            f"&nbsp;&nbsp;&nbsp;"
            f"{_pm_html(night_pm, 'Night +/\u2212:')}"
            f"&nbsp;&nbsp;&nbsp;"
            f"{_pm_html(total_pm, 'Total +/\u2212:')}"
        )

        text = (
            f"<span style='font-size:16px; font-weight:bold; color:{p['green_mid']};'>"
            f"{_name(s).upper()}</span><br>"
            f"<span style='color:{p['text_dim']}; font-size:12px;'>Roles: {roles}</span><br>"
            f"<span style='color:{p['text_secondary']}; font-size:13px;'>"
            f"Active: {s.active_reserve_days or 0}d &nbsp;|&nbsp; "
            f"Present: {s.present_days_count or 0:.1f}d &nbsp;|&nbsp; "
            f"Total hours: {total:.1f}h</span><br>"
            f"{pm_line}"
        )
        lbl = QLabel(text)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        self._body_layout.addWidget(lbl)

    # ── Section 2: Hours summary with comparison ──────────────────────────────

    def _add_hours_summary(self, sol_day, sol_night, avg_day, avg_night, avg_total):
        p = self._pal

        def _diff_html(val: float, avg: float) -> str:
            diff = val - avg
            sign = "+" if diff >= 0 else ""
            color = p["diff_over"] if diff > 0 else p["diff_under"] if diff < 0 else p["text_secondary"]
            return f"<span style='color:{color};'>{sign}{diff:.1f}h</span>"

        sol_total = sol_day + sol_night
        wt_tag = " <span style='color:{};font-size:10px;'>(weighted)</span>".format(
            p['text_dim']
        ) if self._weighted else ""
        html = (
            f"<b style='color:{p['green_mid']}; font-size:13px;'>"
            f"HOURS SUMMARY{wt_tag}</b>"
            f"<table cellspacing='6'>"
            f"<tr><td style='color:{p['text_dim']};'>DAY</td>"
            f"<td>{sol_day:.1f}h</td>"
            f"<td style='color:{p['text_dim']};'>avg {avg_day:.1f}h</td>"
            f"<td>{_diff_html(sol_day, avg_day)}</td></tr>"
            f"<tr><td style='color:{p['text_dim']};'>NIGHT</td>"
            f"<td>{sol_night:.1f}h</td>"
            f"<td style='color:{p['text_dim']};'>avg {avg_night:.1f}h</td>"
            f"<td>{_diff_html(sol_night, avg_night)}</td></tr>"
            f"<tr><td style='color:{p['text_dim']};'>COMBINED</td>"
            f"<td><b>{sol_total:.1f}h</b></td>"
            f"<td style='color:{p['text_dim']};'>avg {avg_total:.1f}h</td>"
            f"<td>{_diff_html(sol_total, avg_total)}</td></tr>"
            f"</table>"
        )
        lbl = QLabel(html)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        self._body_layout.addWidget(lbl)

    # ── Section 3: Hours chart ────────────────────────────────────────────────

    def _add_hours_chart(self, soldier_asgns, all_asgns, active_ids,
                         start, end, split: bool,
                         sol_hardness_day, sol_hardness_night):
        ns, ne = self._night_start, self._night_end
        t = self._theme

        if self._period_mode == "Day":
            self._add_day_chart(
                sol_hardness_day, sol_hardness_night, split, t,
            )
        else:
            buckets, bucket_labels = self._make_time_buckets(start, end)
            if not buckets:
                return
            if split:
                for domain, domain_label in [("day", "DAY HOURS"), ("night", "NIGHT HOURS")]:
                    fig = create_figure(width=6.5, height=2.0, theme=t)
                    ax = fig.add_subplot(111)
                    style_ax(ax, t)
                    self._plot_hours_over_time_domain(
                        ax, soldier_asgns, all_asgns, active_ids,
                        buckets, bucket_labels, ns, ne, domain,
                    )
                    ax.set_title(domain_label, fontsize=11, pad=4)
                    apply_style(fig, t)
                    canvas = FigureCanvas(fig)
                    canvas.setMinimumHeight(160)
                    self._body_layout.addWidget(canvas)
            else:
                fig = create_figure(width=6.5, height=2.5, theme=t)
                ax = fig.add_subplot(111)
                style_ax(ax, t)
                self._plot_hours_over_time_stacked(
                    ax, soldier_asgns, all_asgns, active_ids,
                    buckets, bucket_labels, ns, ne,
                )
                ax.set_title("HOURS OVER TIME", fontsize=11, pad=4)
                apply_style(fig, t)
                canvas = FigureCanvas(fig)
                canvas.setMinimumHeight(200)
                self._body_layout.addWidget(canvas)

    def _add_day_chart(self, sol_hardness_day, sol_hardness_night,
                       split: bool, theme: str):
        """Single-day view: one or two bars stacked by hardness."""
        if split:
            fig = create_figure(width=4.0, height=2.5, theme=theme)
            ax = fig.add_subplot(111)
            style_ax(ax, theme)
            x = np.array([0.0, 1.0])
            labels = ["Day", "Night"]
            for hi in range(5):
                dv = sol_hardness_day.get(hi + 1, 0.0)
                nv = sol_hardness_night.get(hi + 1, 0.0)
                if dv < 0.001 and nv < 0.001:
                    continue
                ax.bar(x, [dv, nv],
                       bottom=[sum(sol_hardness_day.get(j, 0.0) for j in range(1, hi + 1)),
                               sum(sol_hardness_night.get(j, 0.0) for j in range(1, hi + 1))],
                       width=0.5, color=HARDNESS_COLORS[hi + 1],
                       label=f"H{hi+1}")
            ax.set_xticks(x)
            ax.set_xticklabels(labels, fontsize=10)
        else:
            fig = create_figure(width=3.0, height=2.5, theme=theme)
            ax = fig.add_subplot(111)
            style_ax(ax, theme)
            combined = defaultdict(float)
            for hi in range(1, 6):
                combined[hi] = sol_hardness_day.get(hi, 0.0) + sol_hardness_night.get(hi, 0.0)
            bottom = 0.0
            for hi in range(1, 6):
                v = combined[hi]
                if v < 0.001:
                    continue
                ax.bar(0, v, bottom=bottom, width=0.5,
                       color=HARDNESS_COLORS[hi], label=f"H{hi}")
                bottom += v
            ax.set_xticks([0])
            ax.set_xticklabels([self._ref_date.strftime("%a %d %b")], fontsize=10)

        ax.set_ylabel("Hours", fontsize=9)
        ax.set_ylim(bottom=0)
        ax.legend(fontsize=7, loc="upper right", framealpha=0.7,
                  facecolor=CHART_LEGEND_BG, edgecolor=CHART_TEXT_DIM,
                  labelcolor=CHART_TEXT_SEC)
        ax.set_title("HOURS TODAY", fontsize=11, pad=4)
        apply_style(fig, theme)
        canvas = FigureCanvas(fig)
        canvas.setMinimumHeight(180)
        self._body_layout.addWidget(canvas)

    def _make_time_buckets(self, start, end):
        """Return list of (bucket_start, bucket_end) and labels."""
        if start is None or end is None:
            first_asgn = ScheduleService(self.db).get_first_assignment(self.soldier.id)
            if not first_asgn or not first_asgn.start_time:
                return [], []
            start = first_asgn.start_time.replace(hour=0, minute=0, second=0)
            end = datetime.now()

        total_days = (end - start).days
        if total_days <= 0:
            return [], []

        if self._period_mode == "Week":
            step = timedelta(days=1)
            fmt = "%a"
        elif self._period_mode == "Month":
            step = timedelta(days=1) if total_days <= 35 else timedelta(weeks=1)
            fmt = "%d" if total_days <= 35 else "%d %b"
        else:
            # Whole period — auto-select bucket size
            if total_days < 14:
                step = timedelta(days=1)
                fmt = "%d %b"
            elif total_days <= 30:
                step = timedelta(days=2)
                fmt = "%d %b"
            else:
                step = timedelta(weeks=1)
                fmt = "%d %b"

        buckets = []
        labels = []
        cursor = start
        while cursor < end:
            b_end = min(cursor + step, end)
            buckets.append((cursor, b_end))
            labels.append(cursor.strftime(fmt))
            cursor = b_end
        return buckets, labels

    def _compute_bucket_avg(self, all_asgns, active_ids, buckets, ns, ne,
                            domain=None):
        """Compute per-bucket average (presence-weighted or absolute).

        domain: None=combined, "day", or "night".
        Returns list of floats, one per bucket.
        """
        n_active = max(len(active_ids), 1)
        bucket_avg = []

        for bs, be in buckets:
            per_sol: dict[int, float] = defaultdict(float)
            for a in all_asgns:
                if not a.start_time or not a.end_time:
                    continue
                os_ = max(a.start_time, bs)
                oe = min(a.end_time, be)
                if os_ >= oe:
                    continue
                if domain is None:
                    per_sol[a.soldier_id] += (oe - os_).total_seconds() / 3600
                else:
                    cursor = os_
                    step = timedelta(minutes=15)
                    while cursor < oe:
                        sl = min(cursor + step, oe)
                        is_n = _is_night(cursor, self._night_start, self._night_end)
                        if (domain == "night" and is_n) or (domain == "day" and not is_n):
                            per_sol[a.soldier_id] += (sl - cursor).total_seconds() / 3600
                        cursor = sl

            if self._weighted:
                # Aggregate fracs over bucket days.
                bucket_days = (be - bs).days or 1
                frac_sum: dict[int, float] = {sid: 0.0 for sid in active_ids}
                cur_d = bs.date()
                end_d = be.date()
                while cur_d < end_d:
                    df, nf = compute_domain_fractions(
                        self.db, active_ids, cur_d, ns, ne,
                    )
                    for sid in active_ids:
                        if domain == "day":
                            frac_sum[sid] += df.get(sid, 0.0)
                        elif domain == "night":
                            frac_sum[sid] += nf.get(sid, 0.0)
                        else:
                            frac_sum[sid] += df.get(sid, 0.0) + nf.get(sid, 0.0)
                    cur_d += timedelta(days=1)
                avg, _ = weighted_avg_sd(per_sol, frac_sum)
                bucket_avg.append(avg)
            else:
                bucket_avg.append(sum(per_sol.values()) / n_active)

        return bucket_avg

    def _plot_hours_over_time_stacked(self, ax, soldier_asgns, all_asgns,
                                      active_ids, buckets, labels, ns, ne):
        """Stacked bar by hardness for combined hours."""
        x = np.arange(len(buckets))
        bucket_hardness = [[0.0] * 5 for _ in range(len(buckets))]
        for a in soldier_asgns:
            if not a.start_time or not a.end_time:
                continue
            hardness = (a.task.hardness if a.task else 3) - 1
            for bi, (bs, be) in enumerate(buckets):
                overlap_start = max(a.start_time, bs)
                overlap_end = min(a.end_time, be)
                if overlap_start < overlap_end:
                    h = (overlap_end - overlap_start).total_seconds() / 3600
                    bucket_hardness[bi][hardness] += h

        bottoms = [0.0] * len(buckets)
        for hi in range(5):
            vals = [bucket_hardness[bi][hi] for bi in range(len(buckets))]
            if any(v > 0 for v in vals):
                ax.bar(x, vals, bottom=bottoms, width=0.6,
                       color=HARDNESS_COLORS[hi + 1], label=f"H{hi+1}")
            bottoms = [b + v for b, v in zip(bottoms, vals)]

        bucket_avg = self._compute_bucket_avg(all_asgns, active_ids, buckets, ns, ne)
        ax.plot(x, bucket_avg, color=COLOR_AVG_LINE, linewidth=1.5,
                linestyle="--", label="Unit avg", zorder=5)

        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=45 if len(labels) > 10 else 0,
                           ha="right" if len(labels) > 10 else "center", fontsize=8)
        ax.set_ylabel("Hours", fontsize=9)
        ax.legend(fontsize=7, loc="upper right", framealpha=0.7,
                  facecolor=CHART_LEGEND_BG, edgecolor=CHART_TEXT_DIM, labelcolor=CHART_TEXT_SEC)

    def _plot_hours_over_time_domain(self, ax, soldier_asgns, all_asgns,
                                     active_ids, buckets, labels, ns, ne, domain):
        """Single-domain (day or night) stacked bars."""
        x = np.arange(len(buckets))
        bucket_hardness = [[0.0] * 5 for _ in range(len(buckets))]

        for a in soldier_asgns:
            if not a.start_time or not a.end_time:
                continue
            hardness = (a.task.hardness if a.task else 3) - 1
            for bi, (bs, be) in enumerate(buckets):
                overlap_start = max(a.start_time, bs)
                overlap_end = min(a.end_time, be)
                if overlap_start < overlap_end:
                    cursor = overlap_start
                    step = timedelta(minutes=15)
                    while cursor < overlap_end:
                        sl_end = min(cursor + step, overlap_end)
                        is_n = _is_night(cursor, ns, ne)
                        if (domain == "night" and is_n) or (domain == "day" and not is_n):
                            bucket_hardness[bi][hardness] += (sl_end - cursor).total_seconds() / 3600
                        cursor = sl_end

        bottoms = [0.0] * len(buckets)
        for hi in range(5):
            vals = [bucket_hardness[bi][hi] for bi in range(len(buckets))]
            if any(v > 0 for v in vals):
                ax.bar(x, vals, bottom=bottoms, width=0.6,
                       color=HARDNESS_COLORS[hi + 1], label=f"H{hi+1}")
            bottoms = [b + v for b, v in zip(bottoms, vals)]

        bucket_avg = self._compute_bucket_avg(
            all_asgns, active_ids, buckets, ns, ne, domain=domain,
        )
        ax.plot(x, bucket_avg, color=COLOR_AVG_LINE, linewidth=1.5,
                linestyle="--", label="Unit avg", zorder=5)

        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=45 if len(labels) > 10 else 0,
                           ha="right" if len(labels) > 10 else "center", fontsize=8)
        ax.set_ylabel("Hours", fontsize=9)
        ax.legend(fontsize=7, loc="upper right", framealpha=0.7,
                  facecolor=CHART_LEGEND_BG, edgecolor=CHART_TEXT_DIM, labelcolor=CHART_TEXT_SEC)

    # ── Section 4: Key metrics ────────────────────────────────────────────────

    def _add_key_metrics(self, per_soldier_day, per_soldier_night,
                         per_soldier_total, active_ids, n_active, start, end):
        ns, ne = self._night_start, self._night_end

        if self._weighted and start and end:
            day_frac_sum, night_frac_sum = self._aggregate_period_fracs(
                active_ids, start, end, ns, ne,
            )
            combined_frac_sum = {
                sid: day_frac_sum.get(sid, 0.0) + night_frac_sum.get(sid, 0.0)
                for sid in active_ids
            }
            # Rank by weighted rate
            rates: dict[int, float] = {}
            for sid in active_ids:
                f = combined_frac_sum.get(sid, 0.0)
                rates[sid] = per_soldier_total.get(sid, 0.0) / f if f > 0.001 else 0.0

            sol_rate = rates.get(self.soldier.id, 0.0)
            sorted_rates = sorted(rates.values(), reverse=True)
            rank = 1
            for r in sorted_rates:
                if r <= sol_rate + 0.0001:
                    break
                rank += 1

            avg_total, stddev = weighted_avg_sd(per_soldier_total, combined_frac_sum)
            sol_total_val = per_soldier_total.get(self.soldier.id, 0.0)
        else:
            sol_total_val = per_soldier_total.get(self.soldier.id, 0.0)
            avg_total = sum(per_soldier_total.values()) / n_active if n_active else 0

            sorted_totals = sorted(per_soldier_total.values(), reverse=True)
            rank = 1
            for t in sorted_totals:
                if t <= sol_total_val + 0.0001:
                    break
                rank += 1

            if n_active > 1:
                var = sum((v - avg_total) ** 2 for v in per_soldier_total.values()) / n_active
                stddev = math.sqrt(var)
            else:
                stddev = 0.0

        p = self._pal
        wt_tag = " (weighted)" if self._weighted else ""
        html = (
            f"<b style='color:{p['green_mid']}; font-size:13px;'>KEY METRICS{wt_tag}</b><br>"
            f"<table cellspacing='4'>"
            f"<tr><td style='color:{p['text_dim']};'>Total hours</td>"
            f"<td>{sol_total_val:.1f}h</td></tr>"
            f"<tr><td style='color:{p['text_dim']};'>Unit: average</td>"
            f"<td>{avg_total:.1f}h</td></tr>"
            f"<tr><td style='color:{p['text_dim']};'>Unit: rank</td>"
            f"<td>{_ordinal(rank)} most loaded of {n_active}</td></tr>"
            f"<tr><td style='color:{p['text_dim']};'>Unit: std dev</td>"
            f"<td>{stddev:.1f}h</td></tr>"
            f"</table>"
        )
        lbl = QLabel(html)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        self._body_layout.addWidget(lbl)
