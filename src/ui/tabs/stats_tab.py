"""
Stats Tab — unit-wide workload statistics.

Sections:
  1. Key Metrics (headline numbers)
  2. Hours by Soldier (fairness chart — stacked bars by hardness)
  3. Hours Over Time (trend chart — stacked bars by hardness)
"""
from __future__ import annotations

import math
import os
from datetime import datetime, date, time, timedelta
from collections import defaultdict

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QScrollArea, QFrame, QToolButton, QSizePolicy,
    QApplication, QFileDialog, QMessageBox, QProgressBar, QDialog,
)
from PyQt6.QtCore import Qt

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np

from src.core.models import Soldier, TaskAssignment, Task
from src.services.config_service import ConfigService
from src.services.schedule_service import ScheduleService
from src.services.soldier_service import SoldierService
from src.ui.charts.chart_style import (
    create_figure, style_ax, apply_style, get_palette, current_theme,
    HARDNESS_COLORS, CHART_LEGEND_BG, CHART_TEXT_SEC, CHART_TEXT_DIM,
    COLOR_AVG_WARM, CHART_TEXT,
)
from src.domain.presence_calc import (
    compute_domain_fractions, weighted_avg_sd,
)
from src.domain.reserve_period import resolve_reserve_period


# ── Scroll-transparent canvas ────────────────────────────────────────────────

class _ScrollableCanvas(FigureCanvas):
    """FigureCanvas that forwards wheel events to the parent scroll area."""

    def wheelEvent(self, event):  # noqa: N802
        event.ignore()


# ── Shared helpers (same logic as soldier_stats_dialog) ──────────────────────

def _is_night(dt: datetime, night_start: int, night_end: int) -> bool:
    return dt.hour >= night_start or dt.hour < night_end


def _split_day_night_hours(asgn: TaskAssignment,
                           night_start: int, night_end: int) -> tuple[float, float]:
    if not asgn.start_time or not asgn.end_time:
        return 0.0, 0.0
    day_h = night_h = 0.0
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


def _period_bounds(mode: str, ref_date: date, db=None):
    if mode == "Whole period":
        if db is not None:
            return resolve_reserve_period(db)
        return None, None
    elif mode == "Day":
        start = datetime.combine(ref_date, time(0, 0))
        return start, start + timedelta(days=1)
    elif mode == "Week":
        monday = ref_date - timedelta(days=ref_date.weekday())
        start = datetime.combine(monday, time(0, 0))
        return start, start + timedelta(days=7)
    elif mode == "Month":
        start = datetime.combine(ref_date.replace(day=1), time(0, 0))
        if ref_date.month == 12:
            end_date = ref_date.replace(year=ref_date.year + 1, month=1, day=1)
        else:
            end_date = ref_date.replace(month=ref_date.month + 1, day=1)
        return start, datetime.combine(end_date, time(0, 0))
    return None, None


def _fetch_all_active_assignments(db, start, end):
    return ScheduleService(db).get_all_active_assignments(start, end)


# ── Stats Tab ────────────────────────────────────────────────────────────────

class StatsTab(QWidget):
    def __init__(self, db, main_window):
        super().__init__()
        self.db = db
        self._config_svc = ConfigService(db)
        self._soldier_svc = SoldierService(db)
        self.mw = main_window
        self._cache_key = None
        self._cache_data = None

        ns, ne = self._config_svc.get_night_window()
        self._night_start = ns
        self._night_end = ne

        self._period_mode = "Whole period"
        self._ref_date = date.today()
        self._weighted = True
        self._split = False
        self._details_expanded: dict[str, bool] = {
            "combined": False, "day": False, "night": False,
        }
        self._pie_containers: dict[str, QWidget] = {}
        self._detail_btns: dict[str, QPushButton] = {}

        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Fixed control bar ────────────────────────────────────────────
        ctrl_container = QWidget()
        ctrl_container.setObjectName("statsControlBar")
        ctrl_layout = QVBoxLayout(ctrl_container)
        ctrl_layout.setContentsMargins(16, 10, 16, 10)
        ctrl_layout.setSpacing(6)

        # Row 1: period + navigation + day/night toggle
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        row1.addWidget(QLabel("Period:"))
        self._period_combo = QComboBox()
        self._period_combo.addItems(["Whole period", "Month", "Week", "Day"])
        self._period_combo.currentTextChanged.connect(self._on_period_changed)
        row1.addWidget(self._period_combo)

        self._nav_prev = QToolButton()
        self._nav_prev.setText("\u25C0")
        self._nav_prev.setFixedSize(28, 24)
        self._nav_prev.clicked.connect(self._nav_backward)
        self._nav_prev.setVisible(False)
        row1.addWidget(self._nav_prev)

        self._period_label = QLabel()
        self._period_label.setMinimumWidth(160)
        self._period_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row1.addWidget(self._period_label)

        self._nav_next = QToolButton()
        self._nav_next.setText("\u25B6")
        self._nav_next.setFixedSize(28, 24)
        self._nav_next.clicked.connect(self._nav_forward)
        self._nav_next.setVisible(False)
        row1.addWidget(self._nav_next)

        row1.addSpacing(20)

        # Day/Night segmented control
        row1.addWidget(QLabel("View:"))
        self._btn_combined = QPushButton("Combined")
        self._btn_daynight = QPushButton("Day vs Night")
        for btn in (self._btn_combined, self._btn_daynight):
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.setMinimumWidth(90)
        self._btn_combined.setChecked(True)
        self._btn_combined.clicked.connect(lambda: self._set_split(False))
        self._btn_daynight.clicked.connect(lambda: self._set_split(True))
        row1.addWidget(self._btn_combined)
        row1.addWidget(self._btn_daynight)

        row1.addStretch()

        export_btn = QPushButton("[ Export PDF ]")
        export_btn.setFixedHeight(26)
        export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        export_btn.clicked.connect(self._on_export_pdf)
        row1.addWidget(export_btn)

        ctrl_layout.addLayout(row1)

        # Row 2: weighted/absolute toggle
        row2 = QHBoxLayout()
        row2.setSpacing(0)

        self._btn_weighted = QPushButton("Weighted")
        self._btn_absolute = QPushButton("Absolute")
        for btn in (self._btn_weighted, self._btn_absolute):
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.setMinimumWidth(80)
        self._btn_weighted.setChecked(True)
        self._btn_weighted.clicked.connect(lambda: self._set_weight_mode(True))
        self._btn_absolute.clicked.connect(lambda: self._set_weight_mode(False))

        row2.addWidget(self._btn_weighted)
        row2.addWidget(self._btn_absolute)
        row2.addSpacing(8)

        hint = QLabel("Weighted: adjusts for partial presence. Absolute: raw total hours.")
        hint.setObjectName("dimLabel")
        hint.setStyleSheet("font-size: 10px;")
        row2.addWidget(hint)
        row2.addStretch()
        ctrl_layout.addLayout(row2)

        root.addWidget(ctrl_container)

        # ── Separator ────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(sep)

        # ── Scrollable body ──────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(16, 12, 16, 12)
        self._body_layout.setSpacing(12)
        scroll.setWidget(self._body)
        root.addWidget(scroll, 1)

        self._update_toggle_styles()

    # ── Toggle helpers ───────────────────────────────────────────────────────

    def _current_theme(self) -> str:
        return current_theme(self.db)

    def _set_split(self, split: bool):
        self._split = split
        self._btn_combined.setChecked(not split)
        self._btn_daynight.setChecked(split)
        self._update_toggle_styles()
        self._refresh()

    def _set_weight_mode(self, weighted: bool):
        self._weighted = weighted
        self._btn_weighted.setChecked(weighted)
        self._btn_absolute.setChecked(not weighted)
        self._update_toggle_styles()
        self._refresh()

    def _update_toggle_styles(self):
        theme = self._current_theme()
        if theme == "light":
            active = (
                "background:#4a8a4a; color:#fff; border:1px solid #3d6b3d;"
                "font-weight:bold;"
            )
            inactive = (
                "background:#e0e0e0; color:#666; border:1px solid #bbb;"
            )
        else:
            active = (
                "background:#3d6b3d; color:#e0e0e0; border:1px solid #4a8a4a;"
                "font-weight:bold;"
            )
            inactive = (
                "background:#2a2a2a; color:#888; border:1px solid #444;"
            )

        def _seg_style(checked, left=False, right=False):
            base = active if checked else inactive
            tl = "4px" if left else "0"
            bl = "4px" if left else "0"
            tr = "4px" if right else "0"
            br = "4px" if right else "0"
            return (
                f"QPushButton {{ {base}"
                f" border-top-left-radius:{tl}; border-bottom-left-radius:{bl};"
                f" border-top-right-radius:{tr}; border-bottom-right-radius:{br};"
                f" padding:2px 10px; }}"
            )

        self._btn_weighted.setStyleSheet(_seg_style(self._weighted, left=True))
        self._btn_absolute.setStyleSheet(_seg_style(not self._weighted, right=True))
        self._btn_combined.setStyleSheet(_seg_style(not self._split, left=True))
        self._btn_daynight.setStyleSheet(_seg_style(self._split, right=True))

    # ── Navigation ───────────────────────────────────────────────────────────

    def _on_period_changed(self, text: str):
        self._period_mode = text
        nav_visible = text != "Whole period"
        self._nav_prev.setVisible(nav_visible)
        self._nav_next.setVisible(nav_visible)
        self._refresh()

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

    # ── Period label ─────────────────────────────────────────────────────────

    def _update_period_label(self, start, end):
        if self._period_mode == "Whole period":
            if start and end:
                self._period_label.setText(
                    f"{start.strftime('%d %b')} \u2013 {end.strftime('%d %b %Y')}"
                )
            else:
                self._period_label.setText("All data")
        elif self._period_mode == "Day":
            self._period_label.setText(self._ref_date.strftime("%d %b %Y"))
        elif self._period_mode == "Week":
            if start and end:
                self._period_label.setText(
                    f"{start.strftime('%d %b')} \u2013 {(end - timedelta(days=1)).strftime('%d %b %Y')}"
                )
        elif self._period_mode == "Month":
            self._period_label.setText(self._ref_date.strftime("%B %Y"))

    # ── Data computation (cached) ────────────────────────────────────────────

    def _compute_data(self, start, end):
        """Compute all data needed for all sections. Returns a dict."""
        cache_key = (start, end, self._weighted, self._night_start, self._night_end)
        if self._cache_key == cache_key and self._cache_data is not None:
            return self._cache_data

        ns, ne = self._night_start, self._night_end
        all_asgns = _fetch_all_active_assignments(self.db, start, end)
        active_soldiers = self._soldier_svc.list_active_soldiers()
        active_ids = [s.id for s in active_soldiers]
        soldier_map = {s.id: s for s in active_soldiers}
        n_active = max(len(active_ids), 1)

        # Per-soldier hours by domain and hardness
        per_soldier_day: dict[int, float] = defaultdict(float)
        per_soldier_night: dict[int, float] = defaultdict(float)
        per_soldier_total: dict[int, float] = defaultdict(float)
        per_soldier_hardness_day: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        per_soldier_hardness_night: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        # Per time-bucket data
        for a in all_asgns:
            dh, nh = _split_day_night_hours(a, ns, ne)
            per_soldier_day[a.soldier_id] += dh
            per_soldier_night[a.soldier_id] += nh
            per_soldier_total[a.soldier_id] += dh + nh
            hardness = a.task.hardness if a.task else 3
            per_soldier_hardness_day[a.soldier_id][hardness] += dh
            per_soldier_hardness_night[a.soldier_id][hardness] += nh

        # Presence fractions for weighted mode
        day_frac_sum = night_frac_sum = combined_fracs = None
        if self._weighted and start and end:
            day_frac_sum, night_frac_sum = self._aggregate_period_fracs(
                active_ids, start, end, ns, ne,
            )
            combined_fracs = {
                sid: day_frac_sum.get(sid, 0.0) + night_frac_sum.get(sid, 0.0)
                for sid in active_ids
            }

        # Averages and std devs
        if self._weighted and start and end and day_frac_sum:
            avg_day, sd_day = weighted_avg_sd(per_soldier_day, day_frac_sum)
            avg_night, sd_night = weighted_avg_sd(per_soldier_night, night_frac_sum)
            avg_total, sd_total = weighted_avg_sd(per_soldier_total, combined_fracs)
        else:
            avg_day = sum(per_soldier_day.values()) / n_active if n_active else 0
            avg_night = sum(per_soldier_night.values()) / n_active if n_active else 0
            avg_total = sum(per_soldier_total.values()) / n_active if n_active else 0
            vals_total = [per_soldier_total.get(sid, 0.0) for sid in active_ids]
            sd_total = math.sqrt(sum((v - avg_total) ** 2 for v in vals_total) / n_active) if n_active > 1 else 0
            vals_day = [per_soldier_day.get(sid, 0.0) for sid in active_ids]
            sd_day = math.sqrt(sum((v - avg_day) ** 2 for v in vals_day) / n_active) if n_active > 1 else 0
            vals_night = [per_soldier_night.get(sid, 0.0) for sid in active_ids]
            sd_night = math.sqrt(sum((v - avg_night) ** 2 for v in vals_night) / n_active) if n_active > 1 else 0

        # Most/least loaded
        def _find_extreme(hours_dict, frac_dict, find_max=True):
            if not active_ids:
                return None, 0.0
            if self._weighted and frac_dict:
                rates = {}
                for sid in active_ids:
                    f = frac_dict.get(sid, 0.0)
                    rates[sid] = hours_dict.get(sid, 0.0) / f if f > 0.001 else 0.0
                pick = max(rates, key=rates.get) if find_max else min(rates, key=rates.get)
                return soldier_map.get(pick), rates[pick]
            else:
                vals = {sid: hours_dict.get(sid, 0.0) for sid in active_ids}
                pick = max(vals, key=vals.get) if find_max else min(vals, key=vals.get)
                return soldier_map.get(pick), vals[pick]

        # Aggregate hardness totals (for pie charts)
        total_hardness_day: dict[int, float] = defaultdict(float)
        total_hardness_night: dict[int, float] = defaultdict(float)
        for sid in active_ids:
            for hi in range(1, 6):
                total_hardness_day[hi] += per_soldier_hardness_day.get(sid, {}).get(hi, 0.0)
                total_hardness_night[hi] += per_soldier_hardness_night.get(sid, {}).get(hi, 0.0)
        total_hardness_combined: dict[int, float] = {
            hi: total_hardness_day.get(hi, 0.0) + total_hardness_night.get(hi, 0.0)
            for hi in range(1, 6)
        }

        most_loaded, most_val = _find_extreme(per_soldier_total, combined_fracs, True)
        least_loaded, least_val = _find_extreme(per_soldier_total, combined_fracs, False)

        most_day, most_day_val = _find_extreme(per_soldier_day, day_frac_sum, True)
        least_day, least_day_val = _find_extreme(per_soldier_day, day_frac_sum, False)
        most_night, most_night_val = _find_extreme(per_soldier_night, night_frac_sum, True)
        least_night, least_night_val = _find_extreme(per_soldier_night, night_frac_sum, False)

        data = dict(
            all_asgns=all_asgns,
            active_soldiers=active_soldiers,
            active_ids=active_ids,
            soldier_map=soldier_map,
            n_active=n_active,
            per_soldier_day=per_soldier_day,
            per_soldier_night=per_soldier_night,
            per_soldier_total=per_soldier_total,
            per_soldier_hardness_day=per_soldier_hardness_day,
            per_soldier_hardness_night=per_soldier_hardness_night,
            day_frac_sum=day_frac_sum,
            night_frac_sum=night_frac_sum,
            combined_fracs=combined_fracs,
            avg_day=avg_day, sd_day=sd_day,
            avg_night=avg_night, sd_night=sd_night,
            avg_total=avg_total, sd_total=sd_total,
            most_loaded=most_loaded, most_val=most_val,
            least_loaded=least_loaded, least_val=least_val,
            most_day=most_day, most_day_val=most_day_val,
            least_day=least_day, least_day_val=least_day_val,
            most_night=most_night, most_night_val=most_night_val,
            least_night=least_night, least_night_val=least_night_val,
            total_hardness_day=total_hardness_day,
            total_hardness_night=total_hardness_night,
            total_hardness_combined=total_hardness_combined,
        )

        self._cache_key = cache_key
        self._cache_data = data
        return data

    def _aggregate_period_fracs(self, soldier_ids, start, end, ns, ne):
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

    # ── PDF Export ─────────────────────────────────────────────────────────

    def _on_export_pdf(self):
        from src.ui.dialogs.stats_export_dialog import (
            StatsExportDialog, export_current_view, export_full_report,
            _default_filename, _default_dir,
        )

        dlg = StatsExportDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        mode = dlg.mode
        start, end = _period_bounds(self._period_mode, self._ref_date, db=self.db)
        default_name = _default_filename(self.db, start, end, mode)
        default_dir = _default_dir(self.db)
        default_path = os.path.join(default_dir, default_name) if default_dir else default_name

        filepath, _ = QFileDialog.getSaveFileName(
            self, "Save Stats PDF", default_path, "PDF Files (*.pdf)",
        )
        if not filepath:
            return

        try:
            if mode == StatsExportDialog.CURRENT:
                export_current_view(self, filepath)
            else:
                # Show a simple progress dialog for full report
                prog = QDialog(self)
                prog.setWindowTitle("Generating Report")
                prog.setModal(True)
                prog.setFixedSize(320, 100)
                prog.setWindowFlags(
                    prog.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint
                )
                pl = QVBoxLayout(prog)
                pl.setContentsMargins(20, 16, 20, 16)
                plbl = QLabel("Generating full report...")
                plbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                pl.addWidget(plbl)
                pbar = QProgressBar()
                pbar.setRange(0, 8)
                pl.addWidget(pbar)
                prog.show()
                QApplication.processEvents()

                def _progress(step, total):
                    pbar.setValue(step)
                    plbl.setText(f"Generating full report... ({step}/{total})")
                    QApplication.processEvents()

                export_full_report(self, filepath, progress_fn=_progress)
                prog.close()

            QMessageBox.information(self, "Export", f"PDF saved to:\n{filepath}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))

    # ── Refresh ──────────────────────────────────────────────────────────────

    def refresh(self):
        ns, ne = self._config_svc.get_night_window()
        self._night_start = ns
        self._night_end = ne
        self._cache_key = None  # invalidate on refresh
        self._update_toggle_styles()
        self._refresh()

    def _refresh(self):
        # Clear body
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        start, end = _period_bounds(self._period_mode, self._ref_date, db=self.db)
        self._update_period_label(start, end)

        QApplication.processEvents()

        data = self._compute_data(start, end)

        self._add_key_metrics(data, start, end)
        self._add_fairness_chart(data, start, end)
        self._add_trend_chart(data, start, end)
        self._body_layout.addStretch()

    # ── Section 1: Key Metrics ───────────────────────────────────────────────

    def _add_key_metrics(self, data, start, end):
        theme = self._current_theme()
        pal = get_palette(theme)

        total_day = sum(data["per_soldier_day"].values())
        total_night = sum(data["per_soldier_night"].values())
        total_hours = total_day + total_night
        wt_tag = " (weighted)" if self._weighted else ""
        unit = "h/present-day" if self._weighted else "h"

        def _name(s):
            return (s.name or f"#{s.id}") if s else "N/A"

        if self._split:
            metrics_html = (
                f"<div style='padding:8px; border-radius:6px;'>"
                f"<b style='color:{pal['green_mid']}; font-size:14px;'>"
                f"KEY METRICS{wt_tag}</b>"
                f"<table cellspacing='8' style='font-size:13px; margin-top:6px;'>"
                f"<tr>"
                f"<td></td>"
                f"<td style='font-weight:bold; color:{pal['text_secondary']};'>DAY</td>"
                f"<td style='font-weight:bold; color:{pal['text_secondary']};'>NIGHT</td>"
                f"</tr>"
                f"<tr>"
                f"<td style='color:{pal['text_dim']};'>Average</td>"
                f"<td><b>{data['avg_day']:.1f}</b> {unit}</td>"
                f"<td><b>{data['avg_night']:.1f}</b> {unit}</td>"
                f"</tr>"
                f"<tr>"
                f"<td style='color:{pal['text_dim']};'>Most loaded</td>"
                f"<td>{_name(data['most_day'])} ({data['most_day_val']:.1f})</td>"
                f"<td>{_name(data['most_night'])} ({data['most_night_val']:.1f})</td>"
                f"</tr>"
                f"<tr>"
                f"<td style='color:{pal['text_dim']};'>Least loaded</td>"
                f"<td>{_name(data['least_day'])} ({data['least_day_val']:.1f})</td>"
                f"<td>{_name(data['least_night'])} ({data['least_night_val']:.1f})</td>"
                f"</tr>"
                f"<tr>"
                f"<td style='color:{pal['text_dim']};'>Fairness spread</td>"
                f"<td>\u00b1{data['sd_day']:.1f}h</td>"
                f"<td>\u00b1{data['sd_night']:.1f}h</td>"
                f"</tr>"
                f"</table>"
                f"</div>"
            )
        else:
            metrics_html = (
                f"<div style='padding:8px; border-radius:6px;'>"
                f"<b style='color:{pal['green_mid']}; font-size:14px;'>"
                f"KEY METRICS{wt_tag}</b>"
                f"<table cellspacing='8' style='font-size:13px; margin-top:6px;'>"
                f"<tr>"
                f"<td style='color:{pal['text_dim']};'>Avg per soldier</td>"
                f"<td><b>{data['avg_total']:.1f}</b> {unit}</td>"
                f"</tr>"
                f"<tr>"
                f"<td style='color:{pal['text_dim']};'>Most loaded</td>"
                f"<td><b>{_name(data['most_loaded'])}</b> \u2014 {data['most_val']:.1f} {unit}</td>"
                f"</tr>"
                f"<tr>"
                f"<td style='color:{pal['text_dim']};'>Least loaded</td>"
                f"<td><b>{_name(data['least_loaded'])}</b> \u2014 {data['least_val']:.1f} {unit}</td>"
                f"</tr>"
                f"<tr>"
                f"<td style='color:{pal['text_dim']};'>Fairness spread</td>"
                f"<td>\u00b1{data['sd_total']:.1f}h</td>"
                f"</tr>"
                f"</table>"
                f"</div>"
            )

        lbl = QLabel(metrics_html)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        self._body_layout.addWidget(lbl)

        # ── Total hours with per-line Details buttons ────────────────────
        hours_lines: list[tuple[str, str, float, dict[int, float]]] = [
            ("combined", "Combined", total_hours, data["total_hardness_combined"]),
            ("day", "Day", total_day, data["total_hardness_day"]),
            ("night", "Night", total_night, data["total_hardness_night"]),
        ]

        hours_header = QLabel(
            f"<b style='color:{pal['green_mid']}; font-size:13px;'>"
            f"TOTAL HOURS</b>"
        )
        hours_header.setTextFormat(Qt.TextFormat.RichText)
        hours_header.setContentsMargins(8, 0, 0, 0)
        self._body_layout.addWidget(hours_header)

        self._pie_containers.clear()
        self._detail_btns.clear()

        for key, label, hours_val, hardness_data in hours_lines:
            # Row: label + hours + [ Details ▼ ]
            row = QHBoxLayout()
            row.setSpacing(8)
            row.setContentsMargins(16, 0, 0, 0)

            txt = QLabel(
                f"<span style='color:{pal['text_dim']}; font-size:13px;'>"
                f"{label}:</span>"
                f"&nbsp;&nbsp;<b style='font-size:13px;'>{hours_val:.1f}h</b>"
            )
            txt.setTextFormat(Qt.TextFormat.RichText)
            row.addWidget(txt)

            arrow = "\u25B2" if self._details_expanded.get(key) else "\u25BC"
            btn = QPushButton(f"[ Details {arrow} ]")
            btn.setFixedHeight(22)
            btn.setMaximumWidth(110)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("font-size: 11px; padding: 1px 6px;")
            btn.clicked.connect(lambda _, k=key: self._toggle_pie_detail(k))
            row.addWidget(btn)
            row.addStretch()

            self._detail_btns[key] = btn

            row_w = QWidget()
            row_w.setLayout(row)
            self._body_layout.addWidget(row_w)

            # Expandable pie chart container for this domain
            pie_w = QWidget()
            pie_layout = QHBoxLayout(pie_w)
            pie_layout.setContentsMargins(16, 0, 0, 4)
            pie_layout.setSpacing(0)

            if self._details_expanded.get(key):
                self._build_single_pie(pie_layout, label, hardness_data, theme)

            pie_w.setVisible(self._details_expanded.get(key, False))
            self._pie_containers[key] = pie_w
            self._body_layout.addWidget(pie_w)

    def _toggle_pie_detail(self, key: str):
        self._details_expanded[key] = not self._details_expanded.get(key, False)
        expanded = self._details_expanded[key]

        # Update button text
        btn = self._detail_btns.get(key)
        if btn:
            arrow = "\u25B2" if expanded else "\u25BC"
            btn.setText(f"[ Details {arrow} ]")

        pie_w = self._pie_containers.get(key)
        if pie_w is None:
            return

        if expanded:
            layout = pie_w.layout()
            if layout.count() == 0:
                start, end = _period_bounds(self._period_mode, self._ref_date, db=self.db)
                data = self._compute_data(start, end)
                theme = self._current_theme()
                key_map = {
                    "combined": ("Combined", data["total_hardness_combined"]),
                    "day": ("Day", data["total_hardness_day"]),
                    "night": ("Night", data["total_hardness_night"]),
                }
                label, hardness_data = key_map[key]
                self._build_single_pie(layout, label, hardness_data, theme)

        pie_w.setVisible(expanded)

    def _build_single_pie(self, layout: QHBoxLayout, title: str,
                          hardness_data: dict[int, float], theme: str):
        """Build a clean pie chart + side data legend into the layout."""
        from src.ui.charts.chart_style import CHART_BG, CHART_BG_ALT

        pal = get_palette(theme)
        total = sum(hardness_data.get(hi, 0.0) for hi in range(1, 6))

        sizes = []
        colors = []
        levels = []
        for hi in range(1, 6):
            v = hardness_data.get(hi, 0.0)
            if v > 0.001:
                sizes.append(v)
                colors.append(HARDNESS_COLORS[hi])
                levels.append(hi)

        # ── Pie chart (clean, no labels) ─────────────────────────────────
        fig = Figure(figsize=(2.4, 2.4), dpi=100)
        fig.patch.set_facecolor(CHART_BG)
        fig.patch.set_alpha(1.0)

        ax = fig.add_subplot(111)
        if not sizes:
            ax.set_facecolor(CHART_BG_ALT)
            ax.text(0.5, 0.5, "No data", ha="center", va="center",
                    color=CHART_TEXT_SEC, fontsize=10,
                    transform=ax.transAxes)
            ax.set_xticks([])
            ax.set_yticks([])
            for spine in ax.spines.values():
                spine.set_visible(False)
        else:
            ax.set_facecolor(CHART_BG)
            ax.pie(sizes, colors=colors, labels=None, autopct="",
                   startangle=90)

        fig.subplots_adjust(left=0.02, right=0.98, bottom=0.02, top=0.98)

        canvas = _ScrollableCanvas(fig)
        canvas.setFixedSize(240, 240)
        layout.addWidget(canvas)

        # ── Side data legend (QLabel) ────────────────────────────────────
        lines = [
            f"<b style='color:{pal['text_secondary']}; font-size:12px;'>Hardness</b>"
        ]
        for hi in range(1, 6):
            v = hardness_data.get(hi, 0.0)
            if v < 0.001:
                continue
            pct = (v / total * 100) if total > 0.001 else 0
            c = HARDNESS_COLORS[hi]
            lines.append(
                f"<span style='color:{c}; font-size:16px;'>\u25A0</span>"
                f"&nbsp;<span style='color:{pal['text_secondary']}; font-size:12px;'>"
                f"{hi}:</span>"
                f"&nbsp;&nbsp;<span style='font-size:12px;'>"
                f"{v:.1f}h ({pct:.0f}%)</span>"
            )

        legend_lbl = QLabel("<br>".join(lines))
        legend_lbl.setTextFormat(Qt.TextFormat.RichText)
        legend_lbl.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        legend_lbl.setContentsMargins(12, 0, 0, 0)
        layout.addWidget(legend_lbl)
        layout.addStretch()

    # ── Section 2: Hours by Soldier (fairness chart) ─────────────────────────

    def _add_fairness_chart(self, data, start, end):
        theme = self._current_theme()
        ns, ne = self._night_start, self._night_end

        if self._split:
            for domain, label, hours_key, hardness_key, avg_key, frac_key in [
                ("day", "HOURS BY SOLDIER \u2014 DAY",
                 "per_soldier_day", "per_soldier_hardness_day", "avg_day", "day_frac_sum"),
                ("night", "HOURS BY SOLDIER \u2014 NIGHT",
                 "per_soldier_night", "per_soldier_hardness_night", "avg_night", "night_frac_sum"),
            ]:
                fig = create_figure(width=8.0, height=3.0, theme=theme)
                ax = fig.add_subplot(111)
                style_ax(ax, theme)
                self._plot_fairness(
                    ax, data, hours_key, hardness_key, avg_key, frac_key,
                )
                ax.set_title(label, fontsize=11, pad=6, color=CHART_TEXT)
                apply_style(fig, theme)
                canvas = _ScrollableCanvas(fig)
                canvas.setMinimumHeight(240)
                self._body_layout.addWidget(canvas)
        else:
            fig = create_figure(width=8.0, height=3.5, theme=theme)
            ax = fig.add_subplot(111)
            style_ax(ax, theme)
            self._plot_fairness(
                ax, data,
                "per_soldier_total", None, "avg_total", "combined_fracs",
            )
            ax.set_title("HOURS BY SOLDIER", fontsize=11, pad=6, color=CHART_TEXT)
            apply_style(fig, theme)
            canvas = _ScrollableCanvas(fig)
            canvas.setMinimumHeight(280)
            self._body_layout.addWidget(canvas)

    def _plot_fairness(self, ax, data, hours_key, hardness_key, avg_key, frac_key):
        from matplotlib.patches import Patch
        from matplotlib.lines import Line2D

        active_ids = data["active_ids"]
        soldier_map = data["soldier_map"]
        hours_dict = data[hours_key]
        avg_val = data[avg_key]
        frac_dict = data.get(frac_key)

        if not active_ids:
            return

        # Compute display values (weighted rate or absolute)
        if self._weighted and frac_dict:
            display_vals = {}
            for sid in active_ids:
                f = frac_dict.get(sid, 0.0)
                display_vals[sid] = hours_dict.get(sid, 0.0) / f if f > 0.001 else 0.0
        else:
            display_vals = {sid: hours_dict.get(sid, 0.0) for sid in active_ids}

        # Sort by total (highest first)
        sorted_ids = sorted(active_ids, key=lambda sid: display_vals.get(sid, 0.0), reverse=True)

        x = np.arange(len(sorted_ids))
        names = [soldier_map[sid].name or f"#{sid}" for sid in sorted_ids]

        levels_present: list[int] = []

        if hardness_key:
            hardness_dict = data[hardness_key]
            bottoms = np.zeros(len(sorted_ids))
            for hi in range(1, 6):
                vals = []
                for sid in sorted_ids:
                    raw_h = hardness_dict.get(sid, {}).get(hi, 0.0)
                    if self._weighted and frac_dict:
                        f = frac_dict.get(sid, 0.0)
                        vals.append(raw_h / f if f > 0.001 else 0.0)
                    else:
                        vals.append(raw_h)
                vals = np.array(vals)
                if np.any(vals > 0):
                    ax.bar(x, vals, bottom=bottoms, width=0.6,
                           color=HARDNESS_COLORS[hi])
                    levels_present.append(hi)
                bottoms += vals
        else:
            hd_day = data["per_soldier_hardness_day"]
            hd_night = data["per_soldier_hardness_night"]
            bottoms = np.zeros(len(sorted_ids))
            for hi in range(1, 6):
                vals = []
                for sid in sorted_ids:
                    raw_h = hd_day.get(sid, {}).get(hi, 0.0) + hd_night.get(sid, {}).get(hi, 0.0)
                    if self._weighted and frac_dict:
                        f = frac_dict.get(sid, 0.0)
                        vals.append(raw_h / f if f > 0.001 else 0.0)
                    else:
                        vals.append(raw_h)
                vals = np.array(vals)
                if np.any(vals > 0):
                    ax.bar(x, vals, bottom=bottoms, width=0.6,
                           color=HARDNESS_COLORS[hi])
                    levels_present.append(hi)
                bottoms += vals

        # Totals on top of each bar
        for i, sid in enumerate(sorted_ids):
            v = display_vals.get(sid, 0.0)
            if v > 0.01:
                ax.text(i, v + 0.1, f"{v:.1f}", ha="center", va="bottom",
                        fontsize=7, color=CHART_TEXT_SEC)

        # Average line
        ax.axhline(y=avg_val, color=COLOR_AVG_WARM, linewidth=1.5,
                    linestyle="--", zorder=5)

        ax.set_xticks(x)
        ax.set_xticklabels(names, rotation=45 if len(names) > 8 else 0,
                           ha="right" if len(names) > 8 else "center", fontsize=8)
        ylabel = "Hours / present-day" if self._weighted else "Hours"
        ax.set_ylabel(ylabel, fontsize=9)
        ax.set_ylim(bottom=0)

        # Compact legend: "Hardness  ■1 ■2 ... | Avg X.X"
        handles = [
            Patch(facecolor=HARDNESS_COLORS[hi], edgecolor="none", label=str(hi))
            for hi in levels_present
        ]
        handles.append(
            Line2D([0], [0], color=COLOR_AVG_WARM, linewidth=1.5,
                   linestyle="--", label=f"Avg {avg_val:.1f}")
        )
        leg = ax.legend(
            handles=handles, fontsize=7, loc="upper right",
            ncol=len(handles), columnspacing=0.8,
            handletextpad=0.3, handlelength=1.0,
            framealpha=0.7, facecolor=CHART_LEGEND_BG,
            edgecolor=CHART_TEXT_DIM, labelcolor=CHART_TEXT_SEC,
            title="Hardness", title_fontsize=7,
        )
        if leg.get_title():
            leg.get_title().set_color(CHART_TEXT_SEC)

    # ── Section 3: Hours Over Time (trend chart) ─────────────────────────────

    def _add_trend_chart(self, data, start, end):
        theme = self._current_theme()

        if self._period_mode == "Day":
            # Single day — skip trend chart, fairness chart is more useful
            return

        buckets, labels = self._make_time_buckets(start, end)
        if not buckets:
            return

        if self._split:
            for domain, label in [("day", "HOURS OVER TIME \u2014 DAY"),
                                  ("night", "HOURS OVER TIME \u2014 NIGHT")]:
                fig = create_figure(width=8.0, height=2.5, theme=theme)
                ax = fig.add_subplot(111)
                style_ax(ax, theme)
                self._plot_trend(ax, data, buckets, labels, domain=domain)
                ax.set_title(label, fontsize=11, pad=6, color=CHART_TEXT)
                apply_style(fig, theme)
                canvas = _ScrollableCanvas(fig)
                canvas.setMinimumHeight(200)
                self._body_layout.addWidget(canvas)
        else:
            fig = create_figure(width=8.0, height=3.0, theme=theme)
            ax = fig.add_subplot(111)
            style_ax(ax, theme)
            self._plot_trend(ax, data, buckets, labels, domain=None)
            ax.set_title("HOURS OVER TIME", fontsize=11, pad=6, color=CHART_TEXT)
            apply_style(fig, theme)
            canvas = _ScrollableCanvas(fig)
            canvas.setMinimumHeight(240)
            self._body_layout.addWidget(canvas)

    def _make_time_buckets(self, start, end):
        if start is None or end is None:
            return [], []

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
            # Whole period
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

    def _plot_trend(self, ax, data, buckets, labels, domain=None):
        """Stacked bar chart of total team hours per time bucket, by hardness."""
        from matplotlib.patches import Patch
        from matplotlib.lines import Line2D

        ns, ne = self._night_start, self._night_end
        all_asgns = data["all_asgns"]

        bucket_hardness = [[0.0] * 5 for _ in range(len(buckets))]
        for a in all_asgns:
            if not a.start_time or not a.end_time:
                continue
            hardness_idx = (a.task.hardness if a.task else 3) - 1
            for bi, (bs, be) in enumerate(buckets):
                os_ = max(a.start_time, bs)
                oe = min(a.end_time, be)
                if os_ >= oe:
                    continue
                if domain is None:
                    h = (oe - os_).total_seconds() / 3600
                    bucket_hardness[bi][hardness_idx] += h
                else:
                    cursor = os_
                    step = timedelta(minutes=15)
                    while cursor < oe:
                        sl = min(cursor + step, oe)
                        is_n = _is_night(cursor, ns, ne)
                        if (domain == "night" and is_n) or (domain == "day" and not is_n):
                            bucket_hardness[bi][hardness_idx] += (sl - cursor).total_seconds() / 3600
                        cursor = sl

        x = np.arange(len(buckets))
        bottoms = np.zeros(len(buckets))
        levels_present: list[int] = []
        for hi in range(5):
            vals = np.array([bucket_hardness[bi][hi] for bi in range(len(buckets))])
            if np.any(vals > 0):
                ax.bar(x, vals, bottom=bottoms, width=0.6,
                       color=HARDNESS_COLORS[hi + 1])
                levels_present.append(hi + 1)
            bottoms += vals

        # Average line
        totals = bottoms
        avg = float(np.mean(totals)) if len(totals) > 0 else 0.0
        if len(totals) > 0:
            ax.axhline(y=avg, color=COLOR_AVG_WARM, linewidth=1.5,
                        linestyle="--", zorder=5)

        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=45 if len(labels) > 10 else 0,
                           ha="right" if len(labels) > 10 else "center", fontsize=8)
        ax.set_ylabel("Team hours", fontsize=9)
        ax.set_ylim(bottom=0)

        # Compact legend
        handles = [
            Patch(facecolor=HARDNESS_COLORS[hi], edgecolor="none", label=str(hi))
            for hi in levels_present
        ]
        handles.append(
            Line2D([0], [0], color=COLOR_AVG_WARM, linewidth=1.5,
                   linestyle="--", label=f"Avg {avg:.1f}h")
        )
        leg = ax.legend(
            handles=handles, fontsize=7, loc="upper right",
            ncol=len(handles), columnspacing=0.8,
            handletextpad=0.3, handlelength=1.0,
            framealpha=0.7, facecolor=CHART_LEGEND_BG,
            edgecolor=CHART_TEXT_DIM, labelcolor=CHART_TEXT_SEC,
            title="Hardness", title_fontsize=7,
        )
        if leg.get_title():
            leg.get_title().set_color(CHART_TEXT_SEC)
