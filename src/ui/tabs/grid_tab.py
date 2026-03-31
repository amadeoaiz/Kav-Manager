"""
Grid Tab — Monthly presence planning board.

Rows = soldiers, Columns = days of the month.
· Navigate months with ← PREV / TODAY / NEXT →
· Select one or more cells in a single soldier row (drag or Shift+click)
· Right-click → context menu:
    Single day  → Present (full/partial sub-choice) | Absent
    Block       → Mark Present (D1 12:00 → Dn 12:00) | Mark Absent
· All edits require one confirmation dialog; writing uses SoldierService.
"""
import calendar
import os
import re
import sys
from datetime import datetime, date, time, timedelta

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QMenu, QMessageBox, QDialog, QFormLayout, QRadioButton,
    QButtonGroup, QTimeEdit, QDialogButtonBox, QGroupBox,
    QFileDialog, QInputDialog, QLineEdit,
    QStyledItemDelegate, QStyle,
)
from PyQt6.QtCore import Qt, QTime
from PyQt6.QtGui import QColor, QBrush, QPainter
from PyQt6.QtPrintSupport import QPrinter

from src.core.models import Soldier
from src.services.config_service import ConfigService
from src.services.soldier_service import SoldierService
from src.ui.stylesheet import CELL_COLORS


class _IntervalRow:
    """Minimal interval for grid coloring (from raw DB row)."""
    __slots__ = ("soldier_id", "start_time", "end_time", "status")

    @staticmethod
    def _parse_dt(value):
        """Convert raw SQLite datetime (string) to datetime. Idempotent if already datetime."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        s = str(value).strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M"):
                try:
                    return datetime.strptime(s[:26], fmt)
                except ValueError:
                    continue
            return None

    def __init__(self, soldier_id, start_time, end_time, status):
        self.soldier_id = soldier_id
        self.start_time = self._parse_dt(start_time)
        self.end_time = self._parse_dt(end_time)
        self.status = status


# ── Delegate: paint cell background from item so QSS doesn't override ────────

class _GridCellDelegate(QStyledItemDelegate):
    """Paints cell background from item roles so grid colours show reliably.

    Also supports drawing a strong frame around \"header\" cells in column 0 for
    selected soldier rows, without changing the behaviour of selected day cells.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._row_highlight_rows: set[int] = set()
        self._row_highlight_color: str = "#00e676"
        self._today_col: int | None = None
        self._today_border_color: str = "#00e676"

    def set_row_highlights(self, rows: set[int], color: str):
        """Configure which rows' name cells (column 0) should be framed."""
        self._row_highlight_rows = set(rows or [])
        self._row_highlight_color = color or "#00e676"

    def set_today_column(self, col: int | None, color: str):
        """Configure which day column is 'today' for a subtle full-column frame."""
        self._today_col = col
        self._today_border_color = color or "#00e676"

    def paint(self, painter: QPainter, option, index):
        try:
            bg   = index.data(Qt.ItemDataRole.BackgroundRole)
            fg   = index.data(Qt.ItemDataRole.ForegroundRole)
            text = index.data(Qt.ItemDataRole.DisplayRole)
            if text is None:
                text = ""
            rect = option.rect
            if not rect.isValid():
                return
            # Paint background (stylesheet often overrides item background; we force it here)
            if isinstance(bg, QBrush) and bg.style() != Qt.BrushStyle.NoBrush:
                painter.fillRect(rect, bg)
            else:
                painter.fillRect(rect, option.palette.base())
            # Selection overlay (option.state is QStyle.StateFlag)
            if option.state & QStyle.StateFlag.State_Selected:
                painter.fillRect(rect, option.palette.highlight())
                painter.setPen(option.palette.highlightedText().color())
            else:
                if isinstance(fg, QBrush) and fg.style() != Qt.BrushStyle.NoBrush:
                    painter.setPen(fg.color())
                else:
                    painter.setPen(option.palette.text().color())
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), str(text))

            # Draw a strong frame around the soldier-name \"header\" cell when its
            # row has selected day cells. We only touch column 0; day cells keep
            # their normal selection behaviour.
            if (
                index.column() == 0
                and self._row_highlight_rows
                and index.row() in self._row_highlight_rows
            ):
                pen = painter.pen()
                painter.setPen(QColor(self._row_highlight_color))
                # Slight inset to keep the frame inside the cell
                painter.drawRect(rect.adjusted(0, 0, -1, -1))
                painter.setPen(pen)

            # Subtle today-column frame for all day cells (excluding selection),
            # so the whole column reads as \"today\" without overpowering status colours.
            if (
                self._today_col is not None
                and index.column() == self._today_col
                and not (option.state & QStyle.StateFlag.State_Selected)
            ):
                pen = painter.pen()
                line_color = QColor(self._today_border_color)
                # Use low alpha so the guide line is barely there — enough to
                # orient in the grid, but much softer than status colours.
                line_color.setAlpha(80)
                painter.setPen(line_color)
                painter.drawRect(rect.adjusted(0, 0, -1, -1))
                painter.setPen(pen)
        except Exception:
            super().paint(painter, option, index)


# ── Delegate: highlight the "today" column header ────────────────────────────

class _TodayHeaderDelegate(QStyledItemDelegate):
    """Paints today's column header with a solid accent background.

    The QSS rule for QHeaderView::section overrides item-level backgrounds,
    so we need a delegate to force the colour through painter calls.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._today_col: int | None = None
        self._bg_color:  str = '#00e676'
        self._fg_color:  str = '#000000'

    def set_today(self, col: int | None, bg_color: str):
        self._today_col = col
        self._bg_color  = bg_color
        # Pick a legible text colour based on the background brightness
        c = QColor(bg_color)
        brightness = (c.red() * 299 + c.green() * 587 + c.blue() * 114) / 1000
        self._fg_color = '#000000' if brightness > 128 else '#ffffff'

    def paint(self, painter: QPainter, option, index):
        # First let the default header painting run so sort indicators, etc. draw.
        super().paint(painter, option, index)

        # Then, if this is the today column, draw a strong frame around the header
        # cell so it clearly stands out in both themes.
        if self._today_col is not None and index.column() == self._today_col:
            painter.save()
            rect = option.rect.adjusted(0, 0, -1, -1)
            painter.setPen(QColor(self._bg_color))
            painter.drawRect(rect)
            painter.restore()


# ── Presence-type dialog (single day) ─────────────────────────────────────────

class _PresenceDialog(QDialog):
    """Ask whether fully or partially present, with optional time fields."""

    def __init__(self, soldier_name: str, day: date, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Set Presence — {soldier_name}  {day.strftime('%d %b %Y')}")
        self.setModal(True)
        self.setFixedWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        layout.addWidget(QLabel(f"<b>{soldier_name}</b>  on  <b>{day.strftime('%d %b %Y')}</b>"))

        grp = QGroupBox("Presence type")
        grp_layout = QVBoxLayout(grp)
        self._btn_full    = QRadioButton("Fully present  (00:00 – 23:59)")
        self._btn_partial = QRadioButton("Partially present  (set times below)")
        self._btn_full.setChecked(True)
        grp_layout.addWidget(self._btn_full)
        grp_layout.addWidget(self._btn_partial)
        layout.addWidget(grp)

        # Time fields (shown/hidden based on radio)
        self._time_grp = QGroupBox("Partial presence window")
        time_form = QFormLayout(self._time_grp)
        self._arrival   = QTimeEdit(QTime(12, 0))
        self._departure = QTimeEdit(QTime(23, 59))
        self._arrival.setDisplayFormat("HH:mm")
        self._departure.setDisplayFormat("HH:mm")
        time_form.addRow("Arrival time:",   self._arrival)
        time_form.addRow("Departure time:", self._departure)
        self._time_grp.setEnabled(False)
        layout.addWidget(self._time_grp)

        self._btn_partial.toggled.connect(self._time_grp.setEnabled)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._validate)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _validate(self):
        if self._btn_partial.isChecked():
            if self._departure.time() <= self._arrival.time():
                QMessageBox.warning(self, "Invalid", "Departure must be after arrival.")
                return
        self.accept()

    def is_full(self) -> bool:
        return self._btn_full.isChecked()

    def arrival_time(self) -> QTime:
        return self._arrival.time()

    def departure_time(self) -> QTime:
        return self._departure.time()


# ── Main GridTab ───────────────────────────────────────────────────────────────

class GridTab(QWidget):
    def __init__(self, db, main_window):
        super().__init__()
        self.db = db
        self._config_svc = ConfigService(db)
        self._soldier_svc = SoldierService(db)
        self.mw = main_window
        self._month = date.today().replace(day=1)
        self._current_theme = "dark"
        self._current_colors = CELL_COLORS["dark"]
        self._setup_ui()

    # ── construction ──────────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # Navigation bar
        nav = QHBoxLayout()
        hdr = QLabel("PRESENCE GRID")
        hdr.setObjectName("sectionHeader")
        nav.addWidget(hdr)
        nav.addStretch()

        self._prev_btn  = QPushButton("◀  PREV MONTH")
        self._today_btn = QPushButton("TODAY")
        self._next_btn  = QPushButton("NEXT MONTH  ▶")
        self._export_pdf_btn = QPushButton("[ EXPORT GRID PDF ]")
        self._export_sheets_btn = QPushButton("[ EXPORT GRID SHEETS ]")
        for b in (self._prev_btn, self._today_btn, self._next_btn):
            nav.addWidget(b)
        nav.addSpacing(8)
        nav.addWidget(self._export_pdf_btn)
        nav.addWidget(self._export_sheets_btn)
        outer.addLayout(nav)

        self._month_lbl = QLabel()
        self._month_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._month_lbl.setObjectName("sectionHeader")
        outer.addWidget(self._month_lbl)

        # Legend (rebuilt on _rebuild)
        self._legend_row = QHBoxLayout()
        outer.addLayout(self._legend_row)

        # Table (delegate paints cell background from BackgroundRole so QSS doesn't override)
        self._table = QTableWidget()
        self._cell_delegate = _GridCellDelegate(self._table)
        self._table.setItemDelegate(self._cell_delegate)
        self._today_hdr_delegate = _TodayHeaderDelegate(self._table.horizontalHeader())
        self._table.horizontalHeader().setItemDelegate(self._today_hdr_delegate)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        outer.addWidget(self._table, 1)

        self._prev_btn.clicked.connect(lambda: self._shift_month(-1))
        self._next_btn.clicked.connect(self._go_today)
        self._today_btn.clicked.connect(self._go_today)
        self._next_btn.clicked.connect(lambda: self._shift_month(1))

        # Fix: reconnect properly
        self._prev_btn.clicked.disconnect()
        self._today_btn.clicked.disconnect()
        self._next_btn.clicked.disconnect()
        self._prev_btn.clicked.connect(lambda: self._shift_month(-1))
        self._today_btn.clicked.connect(self._go_today)
        self._next_btn.clicked.connect(lambda: self._shift_month(1))
        self._export_pdf_btn.clicked.connect(self._export_grid_pdf)
        self._export_sheets_btn.clicked.connect(self._export_grid_sheets)

        self._rebuild()

    # ── navigation ────────────────────────────────────────────────────────────

    def _shift_month(self, delta: int):
        y, m = self._month.year, self._month.month + delta
        if m > 12:
            y += 1; m -= 12
        elif m < 1:
            y -= 1; m += 12
        self._month = date(y, m, 1)
        self._rebuild()

    def _go_today(self):
        self._month = date.today().replace(day=1)
        self._rebuild()

    # ── build / refresh ───────────────────────────────────────────────────────

    def _rebuild(self):
        # Force clean read: rollback any pending state, then read presence from DB (raw SQL so we always see committed rows)
        self._soldier_svc.rollback()
        self._soldier_svc.expire_all()

        config = self._config_svc.get_config()
        theme  = config.theme or 'dark'
        colors = CELL_COLORS[theme]
        self._current_theme = theme
        self._current_colors = colors

        # Rebuild legend
        while self._legend_row.count():
            item = self._legend_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        legend_entries = [
            ("Present", "present"),
            ("Partial", "partial_day"),
            ("Absent", "absent"),
            ("Active, no presence data", "inactive"),
            ("Not active in reserves", "not_active"),
        ]
        for label, key in legend_entries:
            bg, fg = colors.get(key, colors["inactive"])
            lbl = QLabel(f"  {label}  ")
            text_color = "#f0f0f0" if theme == "dark" else "#1b2b1b"
            lbl.setStyleSheet(
                f"background-color: {bg}; color: {text_color};"
                f"padding: 2px 6px; border-radius: 3px; font-size: 12px;"
            )
            self._legend_row.addWidget(lbl)
        self._legend_row.addStretch()

        days_in_month = calendar.monthrange(self._month.year, self._month.month)[1]
        self._month_lbl.setText(self._month.strftime("%B %Y").upper())

        soldiers = self._soldier_svc.list_all_soldiers()

        month_start_dt = datetime.combine(self._month, time(0, 0))
        next_m = (self._month.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end_dt = datetime.combine(next_m, time(0, 0))

        # Load presence intervals via raw SQL (bypasses ORM cache — always sees committed rows)
        raw_presence = self._soldier_svc.get_month_presence_raw(month_start_dt, month_end_dt)
        ivs_by_soldier: dict[int, list] = {}
        for sid, rows in raw_presence.items():
            ivs_by_soldier[sid] = [_IntervalRow(*r) for r in rows]

        n_total = sum(len(v) for v in ivs_by_soldier.values())
        print(f"[Grid] Loaded {n_total} presence interval(s) for month.", file=sys.stderr)

        # Load draft intervals (drafted vs not drafted)
        raw_draft = self._soldier_svc.get_month_draft_raw(month_start_dt, month_end_dt)
        draft_by_soldier: dict[int, list] = {}
        for sid, rows in raw_draft.items():
            draft_by_soldier[sid] = [_IntervalRow(*r) for r in rows]

        # Build headers
        self._table.setRowCount(len(soldiers))
        self._table.setColumnCount(1 + days_in_month)

        today = date.today()
        # Build headers; track today's column so delegates can highlight it
        today_col_idx = None
        tbv = colors.get('today_border', '#00e676')
        today_border_color = tbv[0] if isinstance(tbv, (list, tuple)) else tbv

        self._table.setHorizontalHeaderItem(0, QTableWidgetItem("SOLDIER"))
        for d in range(1, days_in_month + 1):
            dd = self._month.replace(day=d)
            col = d  # column index (0 is soldier name)
            hdr_item = QTableWidgetItem(f"{dd.strftime('%a')}\n{d:02d}")
            hdr_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setHorizontalHeaderItem(col, hdr_item)
            if dd == today:
                today_col_idx = col

        # Keep delegates in sync with today's column (None when month doesn't contain today)
        self._today_hdr_delegate.set_today(today_col_idx, today_border_color)
        if hasattr(self, "_cell_delegate"):
            self._cell_delegate.set_today_column(today_col_idx, today_border_color)

        # Column widths
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        for col in range(1, 1 + days_in_month):
            self._table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.Stretch
            )

        _status_letter = {"present": "", "partial_day": "", "absent": "", "inactive": ""}

        # Populate cells — BackgroundRole/ForegroundRole so stylesheet doesn't override
        for row, soldier in enumerate(soldiers):
            name_item = QTableWidgetItem(soldier.name or f"#{soldier.id}")
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            name_item.setData(Qt.ItemDataRole.UserRole, soldier.id)
            self._table.setItem(row, 0, name_item)

            soldier_ivs = ivs_by_soldier.get(soldier.id, [])
            draft_ivs = draft_by_soldier.get(soldier.id, [])

            for col in range(1, 1 + days_in_month):
                day = self._month.replace(day=col)

                if not self._is_drafted_on_day(draft_ivs, day):
                    status = "not_active"
                else:
                    status, _ = self._cell_status(soldier_ivs, day)

                # Not drafted: leave cell visually empty (no explicit background),
                # drafted-but-no-presence uses 'inactive' which has a grey fill.
                letter = _status_letter.get(status, "")
                item = QTableWidgetItem(letter)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if status != "not_active":
                    bg, fg = colors.get(status, colors['inactive'])
                    item.setData(Qt.ItemDataRole.BackgroundRole, QBrush(QColor(bg)))
                    item.setData(Qt.ItemDataRole.ForegroundRole, QBrush(QColor(fg)))
                item.setData(Qt.ItemDataRole.UserRole,     soldier.id)
                item.setData(Qt.ItemDataRole.UserRole + 1, day.isoformat())
                self._table.setItem(row, col, item)

        self._table.verticalHeader().setDefaultSectionSize(26)
        self._table.repaint()

    @staticmethod
    def _is_drafted_on_day(intervals: list, day: date) -> bool:
        """Return True if any DRAFTED interval overlaps this calendar day."""
        day_start = datetime.combine(day, time(0, 0))
        day_end   = datetime.combine(day, time(23, 59, 59))
        for iv in intervals or []:
            if iv.status != "DRAFTED" or iv.start_time is None or iv.end_time is None:
                continue
            if iv.end_time > day_start and iv.start_time < day_end:
                return True
        return False

    def refresh(self):
        self._rebuild()

    def _build_export_matrix(self):
        """
        Build tabular representation of the month grid:
        P = present all day, PA = partial day, A = absent, - = no data.
        """
        days_in_month = calendar.monthrange(self._month.year, self._month.month)[1]
        month_start_dt = datetime.combine(self._month, time(0, 0))
        next_m = (self._month.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end_dt = datetime.combine(next_m, time(0, 0))

        soldiers, by_soldier = self._soldier_svc.get_month_export_data(
            month_start_dt, month_end_dt,
        )

        headers = ["SOLDIER"] + [str(d) for d in range(1, days_in_month + 1)]
        status_map = {"present": "P", "partial_day": "PA", "absent": "A", "inactive": "-"}
        rows = []
        for soldier in soldiers:
            name = soldier.name or f"#{soldier.id}"
            soldier_ivs = by_soldier.get(soldier.id, [])
            row = [name]
            for day_n in range(1, days_in_month + 1):
                day = self._month.replace(day=day_n)
                status, _ = self._cell_status(soldier_ivs, day)
                row.append(status_map.get(status, "-"))
            rows.append(row)
        return headers, rows

    def _export_grid_pdf(self):
        config = self._config_svc.get_config()
        default_dir  = config.default_export_dir or ""
        default_name = f"presence_grid_{self._month.strftime('%Y%m')}.pdf"
        default_path = os.path.join(default_dir, default_name) if default_dir else default_name
        path, _ = QFileDialog.getSaveFileName(self, "Export Presence Grid PDF", default_path, "PDF (*.pdf)")
        if not path:
            return

        headers, rows = self._build_export_matrix()
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)

        painter = QPainter()
        if not painter.begin(printer):
            QMessageBox.critical(self, "Export Failed", "Could not create PDF file.")
            return

        try:
            page = printer.pageRect(QPrinter.Unit.DevicePixel)
            width = int(page.width())
            height = int(page.height())
            margin = 40
            y = margin

            title = f"KavManager Presence Grid — {self._month.strftime('%B %Y')}"
            painter.drawText(margin, y, title)
            y += 30

            legend = "Legend: P=Present, PA=Partial, A=Absent, -=No data"
            painter.drawText(margin, y, legend)
            y += 25

            # Render in day chunks so month always fits the page width.
            days = headers[1:]
            chunk_size = 10
            soldier_col_w = 170
            row_h = 18
            col_w = max(24, int((width - 2 * margin - soldier_col_w) / (chunk_size if chunk_size else 1)))

            chunk_start = 0
            while chunk_start < len(days):
                chunk_end = min(chunk_start + chunk_size, len(days))
                chunk_days = days[chunk_start:chunk_end]

                if y + row_h * (len(rows) + 3) > height - margin:
                    printer.newPage()
                    y = margin
                    painter.drawText(margin, y, title)
                    y += 30

                x = margin
                painter.drawText(x, y, "SOLDIER")
                x += soldier_col_w
                for day_label in chunk_days:
                    painter.drawText(x, y, day_label)
                    x += col_w
                y += row_h

                for row in rows:
                    x = margin
                    painter.drawText(x, y, row[0])
                    x += soldier_col_w
                    for idx in range(chunk_start, chunk_end):
                        painter.drawText(x, y, row[idx + 1])
                        x += col_w
                    y += row_h

                y += 20
                chunk_start = chunk_end
        finally:
            painter.end()

        QMessageBox.information(self, "Export PDF", f"Presence grid exported to:\n{path}")

    @staticmethod
    def _extract_sheet_key(raw: str) -> str:
        raw = (raw or "").strip()
        m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", raw)
        return m.group(1) if m else raw

    def _export_grid_sheets(self):
        config    = self._config_svc.get_config()
        saved_id  = config.google_sheets_id or ""
        saved_creds = config.google_creds_path or ""

        # Use the saved spreadsheet ID if available; only prompt if missing
        if saved_id.strip():
            sheet_ref = saved_id.strip()
        else:
            sheet_ref, ok = QInputDialog.getText(
                self,
                "Google Sheets",
                "Spreadsheet ID or URL:\n(Save it in Settings → Exports to skip this prompt)",
                QLineEdit.EchoMode.Normal,
            )
            if not ok or not sheet_ref.strip():
                return

        worksheet_title, ok = QInputDialog.getText(
            self,
            "Google Sheets",
            "Worksheet title:",
            QLineEdit.EchoMode.Normal,
            f"Presence_{self._month.strftime('%Y_%m')}",
        )
        if not ok or not worksheet_title.strip():
            return

        # Resolve credentials: saved config → env var → default fallback
        if saved_creds.strip() and os.path.exists(saved_creds.strip()):
            creds_path = saved_creds.strip()
        else:
            from src.core.paths import get_data_dir
            creds_path = os.environ.get(
                "KAVMANAGER_GOOGLE_CREDENTIALS",
                os.path.join(get_data_dir(), "google-service-account.json"),
            )

        try:
            import gspread
        except Exception:
            QMessageBox.critical(
                self,
                "Google Sheets",
                "Google Sheets export requires the 'gspread' package.\n"
                "Install it in your environment and try again.",
            )
            return

        if not os.path.exists(creds_path):
            QMessageBox.critical(
                self,
                "Google Sheets",
                "Service-account credentials file not found.\n"
                "Set KAVMANAGER_GOOGLE_CREDENTIALS or create data/google-service-account.json.",
            )
            return

        headers, rows = self._build_export_matrix()
        values = [headers] + rows

        try:
            gc = gspread.service_account(filename=creds_path)
            spreadsheet = gc.open_by_key(self._extract_sheet_key(sheet_ref))
            try:
                ws = spreadsheet.worksheet(worksheet_title.strip())
                ws.clear()
            except Exception:
                ws = spreadsheet.add_worksheet(
                    title=worksheet_title.strip(),
                    rows=max(100, len(values) + 10),
                    cols=max(20, len(headers) + 2),
                )
            ws.update("A1", values)
            QMessageBox.information(
                self,
                "Google Sheets",
                f"Presence grid exported to sheet '{worksheet_title.strip()}'.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Google Sheets Export Failed", str(exc))

    # ── cell status helper ────────────────────────────────────────────────────

    @staticmethod
    def _cell_status(intervals: list, day: date) -> tuple[str, str | None]:
        """Returns (status_key, partial_info). status_key matches CELL_COLORS keys."""
        day_start = datetime.combine(day, time(0, 0))
        day_end   = datetime.combine(day, time(23, 59, 59))

        # Collect all PRESENT intervals that overlap this day
        any_present = False
        min_start = None
        max_end = None
        first_partial_window: str | None = None
        for iv in intervals:
            if iv.status != 'PRESENT' or iv.start_time is None or iv.end_time is None:
                continue
            if iv.end_time <= day_start or iv.start_time >= day_end:
                continue
            any_present = True
            if min_start is None or iv.start_time < min_start:
                min_start = iv.start_time
            if max_end is None or iv.end_time > max_end:
                max_end = iv.end_time
            if first_partial_window is None:
                first_partial_window = (
                    f"{iv.start_time.strftime('%H:%M')} – {iv.end_time.strftime('%H:%M')}"
                )

        if any_present and min_start is not None and max_end is not None:
            # If the union of intervals covers the full day, treat as full PRESENT
            if min_start <= day_start and max_end >= day_end:
                return 'present', None
            return 'partial_day', first_partial_window

        for iv in intervals:
            if iv.status == 'ABSENT' and iv.start_time is not None and iv.end_time is not None:
                if iv.end_time > day_start and iv.start_time < day_end:
                    return 'absent', None
        return 'inactive', None

    # ── selection helpers ─────────────────────────────────────────────────────

    def _get_selection(self):
        """Returns (soldier, sorted_days) or (None, []) if selection spans multiple rows."""
        indexes = [
            idx for idx in self._table.selectedIndexes()
            if idx.column() > 0
        ]
        if not indexes:
            return None, []

        rows = {idx.row() for idx in indexes}
        if len(rows) > 1:
            return None, []

        row = next(iter(rows))
        soldier_id = self._table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        soldier = self._soldier_svc.get_soldier(soldier_id)

        cols = sorted({idx.column() for idx in indexes})
        days = sorted([self._month.replace(day=col) for col in cols])
        return soldier, days

    def _on_selection_changed(self, _selected, _deselected):
        """Highlight the soldier name for any row that has selected day cells."""
        try:
            indexes = self._table.selectedIndexes()
            rows_with_days = {
                idx.row() for idx in indexes
                if idx.column() > 0
            }
            colors = getattr(self, "_current_colors", CELL_COLORS.get("dark", {}))
            accent = colors.get("today_border", "#00e676")
            if hasattr(self, "_cell_delegate"):
                self._cell_delegate.set_row_highlights(rows_with_days, accent)
            # Trigger a repaint so the new frames become visible immediately.
            self._table.viewport().update()
        except Exception:
            # Best-effort UI nicety — ignore failures.
            pass

    # ── context menu ──────────────────────────────────────────────────────────

    def _on_context_menu(self, pos):
        soldier, days = self._get_selection()

        if not soldier or not days:
            if self._table.selectedIndexes():
                QMessageBox.information(
                    self, "Selection",
                    "Please select cells from a single soldier row at a time."
                )
            return

        menu = QMenu(self)

        if len(days) == 1:
            menu.addAction(
                f"Mark PRESENT — {days[0].strftime('%d %b')}",
                lambda: self._mark_single_present(soldier, days[0])
            )
            menu.addAction(
                f"Mark ABSENT — {days[0].strftime('%d %b')}",
                lambda: self._mark_single_absent(soldier, days[0])
            )
        else:
            d1, dn = days[0], days[-1]
            menu.addAction(
                f"Mark PRESENT  {d1.strftime('%d %b')} 12:00 → {dn.strftime('%d %b')} 12:00",
                lambda: self._mark_block_present(soldier, d1, dn)
            )
            menu.addAction(
                f"Mark ABSENT   {d1.strftime('%d %b')} 12:00 → {dn.strftime('%d %b')} 12:00",
                lambda: self._mark_block_absent(soldier, d1, dn)
            )

        # Leave coverage helper using the selected date range (“ask for leave” flow)
        if len(days) >= 1:
            d1, dn = days[0], days[-1]
            menu.addSeparator()
            menu.addAction(
                f"Ask for leave (coverage)  ({d1.strftime('%d %b')} → {dn.strftime('%d %b')})",
                lambda: self._open_leave_coverage(soldier, d1, dn),
            )

        # Soldier-level drafted/not-drafted toggles for the selected date range
        menu.addSeparator()
        d1, dn = days[0], days[-1]
        menu.addAction(
            f"Set ACTIVE in reserves ({d1.strftime('%d %b')} → {dn.strftime('%d %b')})",
            lambda: self._set_drafted_range(soldier, d1, dn, True),
        )
        menu.addAction(
            f"Set NOT ACTIVE in reserves ({d1.strftime('%d %b')} → {dn.strftime('%d %b')})",
            lambda: self._set_drafted_range(soldier, d1, dn, False),
        )

        menu.addSeparator()
        menu.addAction(
            "Set ACTIVE for entire reserve period",
            lambda: self._set_active_whole_period(soldier),
        )

        menu.exec(self._table.viewport().mapToGlobal(pos))

    # ── edit actions ──────────────────────────────────────────────────────────

    def _check_drafted(self, soldier: Soldier, day: date) -> bool:
        """Check if soldier is drafted on a given day via the service layer."""
        day_start = datetime.combine(day, time(0, 0))
        day_end = datetime.combine(day, time(23, 59, 59))
        draft_ivs = self._soldier_svc.get_draft_intervals(soldier.id, day_start, day_end)
        return self._is_drafted_on_day(draft_ivs, day)

    def _mark_single_present(self, soldier: Soldier, day: date):
        if not self._check_drafted(soldier, day):
            QMessageBox.information(
                self,
                "Not drafted",
                "Cannot mark PRESENT on a day when the soldier is not drafted.",
            )
            return
        name = soldier.name or f"#{soldier.id}"
        dlg = _PresenceDialog(name, day, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        if dlg.is_full():
            ans = QMessageBox.question(
                self, "Confirm",
                f"Mark <b>{name}</b> FULLY PRESENT on <b>{day.strftime('%d %b %Y')}</b>?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
            start_dt = datetime.combine(day, time(0, 0))
            end_dt   = datetime.combine(day, time(23, 59, 59))
        else:
            arr = dlg.arrival_time()
            dep = dlg.departure_time()
            ans = QMessageBox.question(
                self, "Confirm",
                f"Mark <b>{name}</b> PARTIALLY PRESENT on <b>{day.strftime('%d %b %Y')}</b>"
                f"  ({arr.toString('HH:mm')} – {dep.toString('HH:mm')})?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
            start_dt = datetime.combine(day, time(arr.hour(), arr.minute()))
            end_dt   = datetime.combine(day, time(dep.hour(), dep.minute()))

        self._write_interval(soldier.id, start_dt, end_dt, "PRESENT")

    def _mark_single_absent(self, soldier: Soldier, day: date):
        if not self._check_drafted(soldier, day):
            QMessageBox.information(
                self,
                "Not drafted",
                "Cannot mark ABSENT on a day when the soldier is not drafted.",
            )
            return
        name = soldier.name or f"#{soldier.id}"
        return_day = day + timedelta(days=1)
        ans = QMessageBox.question(
            self, "Confirm",
            f"Mark <b>{name}</b> ABSENT on <b>{day.strftime('%d %b %Y')}</b>?<br>"
            f"<small>{day.strftime('%d %b')} will be partial (departure) and "
            f"{return_day.strftime('%d %b')} partial (return).</small>",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        # Use day-level normalisation so partial days are derived from
        # transitions. Mark this day ABSENT, next day PRESENT.
        overrides = {day: "A", return_day: "P"}
        self._soldier_svc.normalize_presence_window(
            soldier_id=soldier.id,
            day_from=day - timedelta(days=1),
            day_to=return_day + timedelta(days=1),
            overrides=overrides,
            expand_same_state="A",
        )
        try:
            self._soldier_svc.recalculate_service_counters(soldier.id)
        except Exception:
            pass
        try:
            self.mw.set_dirty(True)
        except Exception:
            pass
        self._soldier_svc.expire_all()
        self._table.clearSelection()
        self._rebuild()
        if hasattr(self.mw, "home_tab") and hasattr(self.mw.home_tab, "refresh"):
            self.mw.home_tab.refresh()

    def _mark_block_present(self, soldier: Soldier, d1: date, dn: date):
        any_drafted = False
        for offset in range((dn - d1).days + 1):
            day = d1 + timedelta(days=offset)
            if self._check_drafted(soldier, day):
                any_drafted = True
                break
        if not any_drafted:
            QMessageBox.information(
                self,
                "Not drafted",
                "Cannot mark PRESENT for a range where the soldier is never drafted.",
            )
            return
        name = soldier.name or f"#{soldier.id}"
        ans = QMessageBox.question(
            self, "Confirm",
            f"Mark <b>{name}</b> PRESENT<br>"
            f"from <b>{d1.strftime('%d %b')} 12:00</b> to <b>{dn.strftime('%d %b')} 12:00</b>?<br>"
            f"<small>({d1.strftime('%d %b')} and {dn.strftime('%d %b')} will be partial days.)</small>",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        # Day-level normalisation: mark the whole range as PRESENT days and let
        # transitions to/from neighbouring ABSENT blocks create the partials.
        overrides = {}
        d = d1
        while d <= dn:
            overrides[d] = "P"
            d += timedelta(days=1)
        self._soldier_svc.normalize_presence_window(
            soldier_id=soldier.id,
            day_from=d1 - timedelta(days=1),
            day_to=dn + timedelta(days=1),
            overrides=overrides,
            expand_same_state="P",
        )
        try:
            self._soldier_svc.recalculate_service_counters(soldier.id)
        except Exception:
            pass
        try:
            self.mw.set_dirty(True)
        except Exception:
            pass
        self._soldier_svc.expire_all()
        self._table.clearSelection()
        self._rebuild()
        if hasattr(self.mw, "home_tab") and hasattr(self.mw.home_tab, "refresh"):
            self.mw.home_tab.refresh()

    def _mark_block_absent(self, soldier: Soldier, d1: date, dn: date):
        any_drafted = False
        for offset in range((dn - d1).days + 1):
            day = d1 + timedelta(days=offset)
            if self._check_drafted(soldier, day):
                any_drafted = True
                break
        if not any_drafted:
            QMessageBox.information(
                self,
                "Not drafted",
                "Cannot mark ABSENT for a range where the soldier is never drafted.",
            )
            return
        name = soldier.name or f"#{soldier.id}"
        return_day = dn + timedelta(days=1)
        ans = QMessageBox.question(
            self, "Confirm",
            f"Mark <b>{name}</b> ABSENT<br>"
            f"from <b>{d1.strftime('%d %b')}</b> to <b>{dn.strftime('%d %b')}</b>?<br>"
            f"<small>{d1.strftime('%d %b')} = departure (partial), "
            f"{return_day.strftime('%d %b')} = return (partial).</small>",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        # Day-level normalisation: mark d1..dn as ABSENT and the first day
        # after the block as PRESENT, then derive partial days from transitions.
        overrides = {}
        d = d1
        while d <= dn:
            overrides[d] = "A"
            d += timedelta(days=1)
        overrides[return_day] = "P"
        self._soldier_svc.normalize_presence_window(
            soldier_id=soldier.id,
            day_from=d1 - timedelta(days=1),
            day_to=return_day + timedelta(days=1),
            overrides=overrides,
            expand_same_state="A",
        )
        try:
            self._soldier_svc.recalculate_service_counters(soldier.id)
        except Exception:
            pass
        try:
            self.mw.set_dirty(True)
        except Exception:
            pass
        self._soldier_svc.expire_all()
        self._table.clearSelection()
        self._rebuild()
        if hasattr(self.mw, "home_tab") and hasattr(self.mw.home_tab, "refresh"):
            self.mw.home_tab.refresh()

    def _write_interval(self, soldier_id: int, start_dt: datetime, end_dt: datetime, status: str):
        try:
            self._soldier_svc.insert_presence(soldier_id, start_dt, end_dt, status)
            try:
                self._soldier_svc.recalculate_service_counters(soldier_id)
            except Exception:
                pass  # non-critical counters — don't block the write
            self.mw.set_dirty(True)
            # Verify write landed (raw read bypasses ORM cache)
            if self._soldier_svc.count_presence_intervals(soldier_id) == 0:
                QMessageBox.critical(
                    self, "Write failed",
                    "Presence was not saved (0 intervals in DB for this soldier). "
                    "Check that the database is writable and not open elsewhere.",
                )
        except Exception as exc:
            try:
                self._soldier_svc.rollback()
            except Exception:
                pass
            QMessageBox.critical(self, "Write Error", f"Could not save presence change:\n{exc}")
        finally:
            self._soldier_svc.expire_all()
            # Clear selection so new colours are immediately visible, then rebuild.
            self._table.clearSelection()
            self._rebuild()
            # Keep mission calendar / home tab in sync with presence changes.
            if hasattr(self.mw, "home_tab") and hasattr(self.mw.home_tab, "refresh"):
                self.mw.home_tab.refresh()

    def _set_drafted_range(self, soldier: Soldier, d_from: date, d_to: date, drafted: bool):
        """Set or clear drafted status for a contiguous date range, without touching presence."""
        name = soldier.name or f"#{soldier.id}"
        label = "ACTIVE in reserves" if drafted else "NOT ACTIVE in reserves"
        ans = QMessageBox.question(
            self,
            "Confirm",
            f"Set <b>{name}</b> as <b>{label}</b> for "
            f"<b>{d_from.strftime('%d %b')} → {d_to.strftime('%d %b')}</b>?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        range_start = datetime.combine(d_from, time(0, 0))
        range_end = datetime.combine(d_to + timedelta(days=1), time(0, 0))

        try:
            self._soldier_svc.set_drafted_range(soldier.id, range_start, range_end, drafted)
            try:
                self.mw.set_dirty(True)
            except Exception:
                pass
        except Exception as exc:
            try:
                self._soldier_svc.rollback()
            except Exception:
                pass
            QMessageBox.critical(
                self,
                "Update failed",
                f"Could not update drafted status:\n{exc}",
            )
            return
        finally:
            self._soldier_svc.expire_all()

        self._rebuild()
        if hasattr(self.mw, "home_tab") and hasattr(self.mw.home_tab, "refresh"):
            self.mw.home_tab.refresh()

    def _set_active_whole_period(self, soldier: Soldier):
        """Set soldier as DRAFTED for the entire effective reserve period."""
        from src.domain.reserve_period import resolve_reserve_period

        rp_start, rp_end = resolve_reserve_period(self.db)
        if rp_start is None or rp_end is None:
            QMessageBox.warning(
                self,
                "No reserve period",
                "No reserve period defined.\n"
                "Set it in Settings → Unit, or draft at least one soldier first.",
            )
            return

        d_from = rp_start.date() if hasattr(rp_start, "date") else rp_start
        d_to_date = rp_end.date() if hasattr(rp_end, "date") else rp_end
        # _set_drafted_range expects inclusive end date;
        # if rp_end is midnight (exclusive), adjust to previous day.
        if hasattr(rp_end, "hour") and rp_end.hour == 0 and rp_end.minute == 0:
            d_to_date = d_to_date - timedelta(days=1)

        # Delegate to existing method which handles confirmation + merge logic
        self._set_drafted_range(soldier, d_from, d_to_date, True)

    def _open_leave_coverage(self, soldier: Soldier, d_from: date, d_to: date):
        """Open the leave coverage dialog prefilled with the selected range."""
        try:
            from src.ui.dialogs.leave_manager_dialog import LeaveManagerDialog
        except Exception:
            return
        dlg = LeaveManagerDialog(self.db, soldier=soldier, main_window=self.mw, parent=self)
        # Pre-fill the dialog dates if possible
        try:
            dlg._from_date.setDate(d_from)
            dlg._to_date.setDate(d_to)
        except Exception:
            pass
        dlg.exec()
