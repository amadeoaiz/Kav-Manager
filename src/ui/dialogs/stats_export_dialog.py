"""
Stats PDF export — options dialog + generation helpers.

Two modes:
  • Current view: exports the exact charts/metrics visible in the Stats tab.
  • Full report: generates all four combinations
    (weighted/absolute × combined/day-vs-night) in a single PDF.
"""
from __future__ import annotations

import io
import os
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QRadioButton,
    QPushButton, QButtonGroup, QProgressBar, QApplication,
    QFileDialog, QMessageBox,
)
from PyQt6.QtCore import Qt

from fpdf import FPDF

from src.services.config_service import ConfigService
from src.domain.reserve_period import resolve_reserve_period


# ── Options dialog ────────────────────────────────────────────────────────────

class StatsExportDialog(QDialog):
    """Lets the user choose between current-view and full-report export."""

    CURRENT = "current"
    FULL = "full"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Stats PDF")
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        layout.addWidget(QLabel("Choose export scope:"))

        self._radio_current = QRadioButton("Current view - exports what's currently displayed")
        self._radio_full = QRadioButton("Full report - all weighted/absolute and combined/day-night views")
        self._radio_current.setChecked(True)

        self._group = QButtonGroup(self)
        self._group.addButton(self._radio_current)
        self._group.addButton(self._radio_full)

        layout.addWidget(self._radio_current)
        layout.addWidget(self._radio_full)

        btns = QHBoxLayout()
        btns.addStretch()
        ok = QPushButton("Export")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        layout.addLayout(btns)

    @property
    def mode(self) -> str:
        return self.CURRENT if self._radio_current.isChecked() else self.FULL


# ── Helpers ───────────────────────────────────────────────────────────────────

def _period_str(start, end) -> str:
    if start and end:
        return f"{start.strftime('%d %b')} – {end.strftime('%d %b %Y')}"
    return "All data"


def _default_filename(db, start, end, mode: str) -> str:
    config = ConfigService(db).get_config()
    unit = (config.unit_codename or "unit") if config else "unit"
    period = ""
    if start and end:
        period = f"_{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
    tag = "report" if mode == StatsExportDialog.FULL else "view"
    today = datetime.now().strftime("%Y%m%d")
    return f"KavManager_Stats_{unit}{period}_{tag}_{today}.pdf"


def _default_dir(db) -> str:
    config = ConfigService(db).get_config()
    return (config.default_export_dir or "") if config else ""


def _fig_to_bytes(fig, dpi: int = 200) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                facecolor=fig.get_facecolor(), edgecolor="none")
    buf.seek(0)
    return buf.read()


# ── PDF generation: current view ──────────────────────────────────────────────

def export_current_view(tab, filepath: str):
    """Generate a single-page-ish PDF of whatever the Stats tab currently shows."""
    from src.ui.tabs.stats_tab import _period_bounds

    db = tab.db
    config = ConfigService(db).get_config()
    unit = (config.unit_codename or "") if config else ""

    start, end = _period_bounds(tab._period_mode, tab._ref_date, db=db)
    data = tab._compute_data(start, end)

    pdf = _create_pdf()
    _add_header(pdf, unit, start, end, tab._weighted, tab._split)
    _add_metrics_text(pdf, data, tab._weighted, tab._split)

    # Pie charts (only if expanded)
    for key, label in [("combined", "Combined"), ("day", "Day"), ("night", "Night")]:
        if tab._details_expanded.get(key):
            hardness_map = {
                "combined": "total_hardness_combined",
                "day": "total_hardness_day",
                "night": "total_hardness_night",
            }
            _add_pie_section(pdf, label, data[hardness_map[key]])

    # Fairness chart(s)
    _add_fairness_charts(pdf, tab, data, start, end)

    # Trend chart(s)
    _add_trend_charts(pdf, tab, data, start, end)

    pdf.output(filepath)


# ── PDF generation: full report ───────────────────────────────────────────────

def export_full_report(tab, filepath: str, progress_fn=None):
    """
    Generate a comprehensive PDF with all 4 view combinations.
    progress_fn(step, total) is called to update progress.
    """
    from src.ui.tabs.stats_tab import _period_bounds

    db = tab.db
    config = ConfigService(db).get_config()
    unit = (config.unit_codename or "") if config else ""

    start, end = _period_bounds(tab._period_mode, tab._ref_date, db=db)

    total_steps = 8  # 4 combos × 2 chart types
    step = [0]

    def _tick():
        step[0] += 1
        if progress_fn:
            progress_fn(step[0], total_steps)

    pdf = _create_pdf()

    # Title page / summary
    _add_header(pdf, unit, start, end, weighted=None, split=None,
                title="FULL STATISTICS REPORT")

    # Compute data for all combos, save/restore tab state
    orig_weighted = tab._weighted
    orig_split = tab._split

    combos = [
        (True, False,  "Weighted — Combined"),
        (True, True,   "Weighted — Day vs Night"),
        (False, False, "Absolute — Combined"),
        (False, True,  "Absolute — Day vs Night"),
    ]

    try:
        for weighted, split, section_title in combos:
            tab._weighted = weighted
            tab._split = split
            tab._cache_key = None  # force recompute

            data = tab._compute_data(start, end)

            # Section header page
            pdf.add_page()
            pdf.set_font(_FONT, "B", 14)
            pdf.set_text_color(74, 138, 74)
            pdf.cell(0, 12, section_title, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

            _add_metrics_text(pdf, data, weighted, split)

            # Pies for this combo
            if not split:
                _add_pie_section(pdf, "Combined", data["total_hardness_combined"])
            else:
                _add_pie_section(pdf, "Day", data["total_hardness_day"])
                _add_pie_section(pdf, "Night", data["total_hardness_night"])

            _tick()

            # Fairness chart(s)
            _add_fairness_charts(pdf, tab, data, start, end)
            _tick()

            # Trend chart(s)
            _add_trend_charts(pdf, tab, data, start, end)

    finally:
        # Restore original tab state
        tab._weighted = orig_weighted
        tab._split = orig_split
        tab._cache_key = None

    pdf.output(filepath)


# ── Internal PDF helpers ──────────────────────────────────────────────────────

_FONT = "DejaVu"  # Unicode-capable font family name used throughout


def _create_pdf() -> FPDF:
    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(15, 15, 15)
    # Register DejaVu Sans (system TTF) for full Unicode support —
    # covers em-dashes, ±, non-ASCII soldier names, etc.
    pdf.add_font(_FONT, "", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", uni=True)
    pdf.add_font(_FONT, "B", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", uni=True)
    return pdf


def _add_header(pdf: FPDF, unit: str, start, end,
                weighted, split, title: str | None = None):
    pdf.add_page()
    pdf.set_font(_FONT, "B", 16)
    pdf.set_text_color(74, 138, 74)
    header = title or f"KavManager Stats — {unit}"
    pdf.cell(0, 12, header, new_x="LMARGIN", new_y="NEXT")

    pdf.set_font(_FONT, "", 10)
    pdf.set_text_color(140, 140, 140)
    pdf.cell(0, 6, f"Period: {_period_str(start, end)}", new_x="LMARGIN", new_y="NEXT")
    gen = f"Generated: {datetime.now().strftime('%d %b %Y %H:%M')}"
    pdf.cell(0, 6, gen, new_x="LMARGIN", new_y="NEXT")

    if weighted is not None:
        mode = "Weighted" if weighted else "Absolute"
        view = "Day vs Night" if split else "Combined"
        pdf.cell(0, 6, f"Mode: {mode} / {view}", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(4)


def _add_metrics_text(pdf: FPDF, data, weighted: bool, split: bool):
    pdf.set_font(_FONT, "B", 11)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 7, "KEY METRICS", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font(_FONT, "", 9)
    pdf.set_text_color(60, 60, 60)
    unit = "h/present-day" if weighted else "h"

    def _name(s):
        return (s.name or f"#{s.id}") if s else "N/A"

    if split:
        lines = [
            f"Day avg: {data['avg_day']:.1f} {unit}  |  Night avg: {data['avg_night']:.1f} {unit}",
            f"Day spread: ±{data['sd_day']:.1f}h  |  Night spread: ±{data['sd_night']:.1f}h",
            f"Most loaded (day): {_name(data['most_day'])} ({data['most_day_val']:.1f})"
            f"  |  Most loaded (night): {_name(data['most_night'])} ({data['most_night_val']:.1f})",
            f"Least loaded (day): {_name(data['least_day'])} ({data['least_day_val']:.1f})"
            f"  |  Least loaded (night): {_name(data['least_night'])} ({data['least_night_val']:.1f})",
        ]
    else:
        lines = [
            f"Avg per soldier: {data['avg_total']:.1f} {unit}",
            f"Fairness spread: ±{data['sd_total']:.1f}h",
            f"Most loaded: {_name(data['most_loaded'])} — {data['most_val']:.1f} {unit}",
            f"Least loaded: {_name(data['least_loaded'])} — {data['least_val']:.1f} {unit}",
        ]

    for line in lines:
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # Total hours
    total_day = sum(data["per_soldier_day"].values())
    total_night = sum(data["per_soldier_night"].values())
    total = total_day + total_night
    pdf.set_font(_FONT, "B", 9)
    pdf.cell(0, 5, f"Total hours: {total:.1f}h  (Day: {total_day:.1f}h  /  Night: {total_night:.1f}h)",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)


def _add_pie_section(pdf: FPDF, label: str, hardness_data: dict[int, float]):
    """Add a small pie chart + hardness breakdown text."""
    from matplotlib.figure import Figure
    from src.ui.charts.chart_style import HARDNESS_COLORS

    total = sum(hardness_data.get(hi, 0.0) for hi in range(1, 6))
    if total < 0.01:
        return

    # Build pie figure for PDF (white background for print)
    fig = Figure(figsize=(2.5, 2.5), dpi=200)
    fig.patch.set_facecolor("white")
    ax = fig.add_subplot(111)
    ax.set_facecolor("white")

    sizes = []
    colors = []
    levels = []
    for hi in range(1, 6):
        v = hardness_data.get(hi, 0.0)
        if v > 0.001:
            sizes.append(v)
            colors.append(HARDNESS_COLORS[hi])
            levels.append(hi)

    ax.pie(sizes, colors=colors, labels=None, autopct="", startangle=90)
    fig.subplots_adjust(left=0.02, right=0.98, bottom=0.02, top=0.98)

    img_bytes = _fig_to_bytes(fig, dpi=200)
    import matplotlib.pyplot as plt
    plt.close(fig)

    # Check if we need a new page (pie is ~40mm tall)
    if pdf.get_y() + 50 > pdf.h - pdf.b_margin:
        pdf.add_page()

    pdf.set_font(_FONT, "B", 9)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 5, f"Hardness Breakdown — {label}", new_x="LMARGIN", new_y="NEXT")

    y_before = pdf.get_y()
    # Insert pie image
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(img_bytes)
        tmp_path = tmp.name
    try:
        pdf.image(tmp_path, x=pdf.l_margin, y=y_before + 2, w=40)
    finally:
        os.unlink(tmp_path)

    # Text legend next to pie
    pdf.set_xy(pdf.l_margin + 45, y_before + 2)
    pdf.set_font(_FONT, "", 8)
    pdf.set_text_color(60, 60, 60)
    for hi in range(1, 6):
        v = hardness_data.get(hi, 0.0)
        if v < 0.001:
            continue
        pct = v / total * 100
        pdf.cell(0, 4.5, f"Hardness {hi}: {v:.1f}h ({pct:.0f}%)",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(pdf.l_margin + 45)

    pdf.set_y(max(pdf.get_y(), y_before + 42))
    pdf.ln(3)


def _add_fairness_charts(pdf: FPDF, tab, data, start, end):
    """Render fairness chart(s) and add to PDF."""
    from src.ui.charts.chart_style import (
        HARDNESS_COLORS, COLOR_AVG_WARM,
    )
    from matplotlib.figure import Figure
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    import numpy as np

    active_ids = data["active_ids"]
    soldier_map = data["soldier_map"]
    if not active_ids:
        return

    configs = []
    if tab._split:
        configs.append(("HOURS BY SOLDIER — DAY",
                         "per_soldier_day", "per_soldier_hardness_day",
                         "avg_day", "day_frac_sum"))
        configs.append(("HOURS BY SOLDIER — NIGHT",
                         "per_soldier_night", "per_soldier_hardness_night",
                         "avg_night", "night_frac_sum"))
    else:
        configs.append(("HOURS BY SOLDIER",
                         "per_soldier_total", None,
                         "avg_total", "combined_fracs"))

    for title, hours_key, hardness_key, avg_key, frac_key in configs:
        fig = _make_fairness_figure(
            data, tab._weighted, hours_key, hardness_key,
            avg_key, frac_key, title, active_ids, soldier_map,
        )
        _add_chart_to_pdf(pdf, fig, title)


def _make_fairness_figure(data, weighted, hours_key, hardness_key,
                          avg_key, frac_key, title, active_ids, soldier_map):
    """Create a print-ready fairness chart figure."""
    from matplotlib.figure import Figure
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    import numpy as np
    from src.ui.charts.chart_style import HARDNESS_COLORS, COLOR_AVG_WARM

    hours_dict = data[hours_key]
    avg_val = data[avg_key]
    frac_dict = data.get(frac_key)

    if weighted and frac_dict:
        display_vals = {}
        for sid in active_ids:
            f = frac_dict.get(sid, 0.0)
            display_vals[sid] = hours_dict.get(sid, 0.0) / f if f > 0.001 else 0.0
    else:
        display_vals = {sid: hours_dict.get(sid, 0.0) for sid in active_ids}

    sorted_ids = sorted(active_ids, key=lambda sid: display_vals.get(sid, 0.0), reverse=True)
    x = np.arange(len(sorted_ids))
    names = [soldier_map[sid].name or f"#{sid}" for sid in sorted_ids]

    n_soldiers = len(sorted_ids)
    fig_w = max(8.0, n_soldiers * 0.6)
    fig = Figure(figsize=(fig_w, 3.5), dpi=200)
    fig.patch.set_facecolor("white")
    ax = fig.add_subplot(111)
    ax.set_facecolor("#f8f8f8")
    ax.set_title(title, fontsize=10, pad=6, color="#333")

    levels_present = []
    if hardness_key:
        hd = data[hardness_key]
        bottoms = np.zeros(len(sorted_ids))
        for hi in range(1, 6):
            vals = []
            for sid in sorted_ids:
                raw_h = hd.get(sid, {}).get(hi, 0.0)
                if weighted and frac_dict:
                    f = frac_dict.get(sid, 0.0)
                    vals.append(raw_h / f if f > 0.001 else 0.0)
                else:
                    vals.append(raw_h)
            vals = np.array(vals)
            if np.any(vals > 0):
                ax.bar(x, vals, bottom=bottoms, width=0.6, color=HARDNESS_COLORS[hi])
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
                if weighted and frac_dict:
                    f = frac_dict.get(sid, 0.0)
                    vals.append(raw_h / f if f > 0.001 else 0.0)
                else:
                    vals.append(raw_h)
            vals = np.array(vals)
            if np.any(vals > 0):
                ax.bar(x, vals, bottom=bottoms, width=0.6, color=HARDNESS_COLORS[hi])
                levels_present.append(hi)
            bottoms += vals

    for i, sid in enumerate(sorted_ids):
        v = display_vals.get(sid, 0.0)
        if v > 0.01:
            ax.text(i, v + 0.1, f"{v:.1f}", ha="center", va="bottom",
                    fontsize=6, color="#666")

    ax.axhline(y=avg_val, color=COLOR_AVG_WARM, linewidth=1.5, linestyle="--", zorder=5)

    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=45 if len(names) > 8 else 0,
                       ha="right" if len(names) > 8 else "center", fontsize=7)
    ylabel = "Hours / present-day" if weighted else "Hours"
    ax.set_ylabel(ylabel, fontsize=8, color="#555")
    ax.set_ylim(bottom=0)
    ax.tick_params(colors="#666", labelsize=7)

    handles = [Patch(facecolor=HARDNESS_COLORS[hi], edgecolor="none", label=str(hi))
               for hi in levels_present]
    handles.append(Line2D([0], [0], color=COLOR_AVG_WARM, linewidth=1.5,
                          linestyle="--", label=f"Avg {avg_val:.1f}"))
    ax.legend(handles=handles, fontsize=6, loc="upper right",
              ncol=len(handles), columnspacing=0.6,
              handletextpad=0.3, handlelength=0.8,
              title="Hardness", title_fontsize=6)

    fig.tight_layout()
    return fig


def _add_trend_charts(pdf: FPDF, tab, data, start, end):
    """Render trend chart(s) and add to PDF."""
    from src.ui.tabs.stats_tab import _is_night
    from src.ui.charts.chart_style import HARDNESS_COLORS, COLOR_AVG_WARM
    from matplotlib.figure import Figure
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    import numpy as np

    if tab._period_mode == "Day":
        return

    buckets, labels = tab._make_time_buckets(start, end)
    if not buckets:
        return

    configs = []
    if tab._split:
        configs.append(("HOURS OVER TIME — DAY", "day"))
        configs.append(("HOURS OVER TIME — NIGHT", "night"))
    else:
        configs.append(("HOURS OVER TIME", None))

    ns, ne = tab._night_start, tab._night_end
    all_asgns = data["all_asgns"]

    for title, domain in configs:
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
                    from datetime import timedelta
                    cursor = os_
                    step = timedelta(minutes=15)
                    while cursor < oe:
                        sl = min(cursor + step, oe)
                        is_n = _is_night(cursor, ns, ne)
                        if (domain == "night" and is_n) or (domain == "day" and not is_n):
                            bucket_hardness[bi][hardness_idx] += (sl - cursor).total_seconds() / 3600
                        cursor = sl

        fig = Figure(figsize=(8.0, 3.0), dpi=200)
        fig.patch.set_facecolor("white")
        ax = fig.add_subplot(111)
        ax.set_facecolor("#f8f8f8")
        ax.set_title(title, fontsize=10, pad=6, color="#333")

        x = np.arange(len(buckets))
        bottoms = np.zeros(len(buckets))
        levels_present = []
        for hi in range(5):
            vals = np.array([bucket_hardness[bi][hi] for bi in range(len(buckets))])
            if np.any(vals > 0):
                ax.bar(x, vals, bottom=bottoms, width=0.6, color=HARDNESS_COLORS[hi + 1])
                levels_present.append(hi + 1)
            bottoms += vals

        totals = bottoms
        avg = float(np.mean(totals)) if len(totals) > 0 else 0.0
        if len(totals) > 0:
            ax.axhline(y=avg, color=COLOR_AVG_WARM, linewidth=1.5, linestyle="--", zorder=5)

        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=45 if len(labels) > 10 else 0,
                           ha="right" if len(labels) > 10 else "center", fontsize=7)
        ax.set_ylabel("Team hours", fontsize=8, color="#555")
        ax.set_ylim(bottom=0)
        ax.tick_params(colors="#666", labelsize=7)

        handles = [Patch(facecolor=HARDNESS_COLORS[hi], edgecolor="none", label=str(hi))
                   for hi in levels_present]
        handles.append(Line2D([0], [0], color=COLOR_AVG_WARM, linewidth=1.5,
                              linestyle="--", label=f"Avg {avg:.1f}h"))
        ax.legend(handles=handles, fontsize=6, loc="upper right",
                  ncol=len(handles), columnspacing=0.6,
                  handletextpad=0.3, handlelength=0.8,
                  title="Hardness", title_fontsize=6)

        fig.tight_layout()
        _add_chart_to_pdf(pdf, fig, title)


def _add_chart_to_pdf(pdf: FPDF, fig, title: str):
    """Render a matplotlib figure and insert it into the PDF."""
    import tempfile
    import matplotlib.pyplot as plt

    img_bytes = _fig_to_bytes(fig, dpi=200)
    plt.close(fig)

    # Chart images fill most of the landscape page width
    page_w = pdf.w - pdf.l_margin - pdf.r_margin
    chart_h = page_w * 0.35  # approximate aspect ratio

    if pdf.get_y() + chart_h + 5 > pdf.h - pdf.b_margin:
        pdf.add_page()

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(img_bytes)
        tmp_path = tmp.name
    try:
        pdf.image(tmp_path, x=pdf.l_margin, y=pdf.get_y(), w=page_w)
        pdf.set_y(pdf.get_y() + chart_h + 4)
    finally:
        os.unlink(tmp_path)
