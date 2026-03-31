"""
Rashmatz Tab — Unit-level team equipment list.

Columns: Item Name | Qty | Serial No. | Notes
Actions: Add / Edit (double-click or right-click) / Delete (right-click)
Exports: PDF and Google Sheets (same pattern as grid_tab.py)
"""
import os
import re

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QSpinBox, QFormLayout, QMessageBox, QMenu,
    QDialog, QFileDialog, QInputDialog,
)
from PyQt6.QtCore import Qt
from PyQt6.QtPrintSupport import QPrinter
from PyQt6.QtGui import QPainter

from src.services.config_service import ConfigService
from src.services.gear_service import GearService


class RashmatzTab(QWidget):
    """Team equipment list (rashmatz) with add/edit/delete and PDF + Sheets export."""

    def __init__(self, db, main_window):
        super().__init__()
        self.db = db
        self._config_svc = ConfigService(db)
        self._gear_svc = GearService(db)
        self.mw = main_window
        self._setup_ui()
        self._reload()

    # ──────────────────────────────────────────────── UI construction ──── #

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        # ── Header row ────────────────────────────────────────────────────
        hdr_row = QHBoxLayout()

        title = QLabel("RASHMATZ  —  TEAM EQUIPMENT LIST")
        title.setObjectName("sectionHeader")
        hdr_row.addWidget(title)

        hdr_row.addStretch()

        self._add_btn = QPushButton("[ + ADD ITEM ]")
        self._add_btn.clicked.connect(self._on_add)
        hdr_row.addWidget(self._add_btn)

        self._export_pdf_btn = QPushButton("[ EXPORT PDF ]")
        self._export_pdf_btn.clicked.connect(self._export_pdf)
        hdr_row.addWidget(self._export_pdf_btn)

        self._export_sheets_btn = QPushButton("[ EXPORT SHEETS ]")
        self._export_sheets_btn.clicked.connect(self._export_sheets)
        hdr_row.addWidget(self._export_sheets_btn)

        layout.addLayout(hdr_row)

        hint = QLabel("Double-click a row to edit. Right-click for edit / delete.")
        hint.setObjectName("dimLabel")
        layout.addWidget(hint)

        # ── Gear table ────────────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["ITEM NAME", "QTY", "SERIAL NO.", "NOTES"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self._table, 1)

    # ──────────────────────────────────────────────── data ──── #

    def _reload(self):
        items = self._gear_svc.list_team_gear()
        self._table.setRowCount(len(items))
        for row, item in enumerate(items):
            vals = [
                item.item_name,
                str(item.quantity),
                item.serial_number or "—",
                item.notes or "",
            ]
            for col, val in enumerate(vals):
                cell = QTableWidgetItem(val)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                cell.setData(Qt.ItemDataRole.UserRole, item.id)
                self._table.setItem(row, col, cell)
        self._table.resizeRowsToContents()

    def refresh(self):
        self._reload()

    def _item_id_at_row(self, row: int) -> int | None:
        cell = self._table.item(row, 0)
        return cell.data(Qt.ItemDataRole.UserRole) if cell else None

    # ──────────────────────────────────────────────── actions ──── #

    def _on_add(self):
        dlg = _TeamGearItemFormDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._gear_svc.add_team_gear(
                item_name=dlg.item_name(),
                quantity=dlg.quantity(),
                serial_number=dlg.serial_number() or None,
                notes=dlg.notes() or None,
            )
            self._reload()

    def _on_double_click(self, index):
        self._edit_row(index.row())

    def _on_context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        menu = QMenu(self)
        edit_act   = menu.addAction("Edit")
        menu.addSeparator()
        delete_act = menu.addAction("Delete")
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen == edit_act:
            self._edit_row(row)
        elif chosen == delete_act:
            self._delete_row(row)

    def _edit_row(self, row: int):
        item_id = self._item_id_at_row(row)
        if item_id is None:
            return
        items = self._gear_svc.list_team_gear()
        gear = next((g for g in items if g.id == item_id), None)
        if not gear:
            return
        dlg = _TeamGearItemFormDialog(
            item_name=gear.item_name,
            quantity=gear.quantity,
            serial_number=gear.serial_number or "",
            notes=gear.notes or "",
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._gear_svc.update_team_gear(
                item_id,
                item_name=dlg.item_name(),
                quantity=dlg.quantity(),
                serial_number=dlg.serial_number() or None,
                notes=dlg.notes() or None,
            )
            self._reload()

    def _delete_row(self, row: int):
        item_id = self._item_id_at_row(row)
        if item_id is None:
            return
        items = self._gear_svc.list_team_gear()
        gear = next((g for g in items if g.id == item_id), None)
        if not gear:
            return
        ans = QMessageBox.question(
            self, "Delete item",
            f"Delete <b>{gear.item_name}</b> from the team gear list?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self._gear_svc.delete_team_gear(item_id)
            self._reload()

    # ──────────────────────────────────────────────── export helpers ──── #

    def _build_export_matrix(self) -> tuple[list[str], list[list[str]]]:
        """Returns (headers, rows) suitable for PDF and Sheets export."""
        items = self._gear_svc.list_team_gear()
        headers = ["ITEM NAME", "QTY", "SERIAL NO.", "NOTES"]
        rows = [
            [
                item.item_name,
                str(item.quantity),
                item.serial_number or "",
                item.notes or "",
            ]
            for item in items
        ]
        return headers, rows

    # ──────────────────────────────────────────────── export PDF ──── #

    def _export_pdf(self):
        config = self._config_svc.get_config()
        default_dir = config.default_export_dir or ""
        default_path = os.path.join(default_dir, "rashmatz.pdf") if default_dir else "rashmatz.pdf"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Rashmatz PDF", default_path, "PDF (*.pdf)"
        )
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
            page    = printer.pageRect(QPrinter.Unit.DevicePixel)
            width   = int(page.width())
            height  = int(page.height())
            margin  = 40
            y       = margin

            config = self._config_svc.get_config()
            unit_name = config.unit_codename or "KavManager"

            painter.drawText(margin, y, f"{unit_name}  —  RASHMATZ  (Team Equipment List)")
            y += 28
            painter.drawText(margin, y, f"Total items: {len(rows)}")
            y += 30

            col_widths = [
                int(width * 0.35),   # item name
                60,                  # qty
                int(width * 0.20),   # serial
                int(width * 0.30),   # notes
            ]
            row_h = 20

            # Header
            x = margin
            for hdr, cw in zip(headers, col_widths):
                painter.drawText(x, y, hdr)
                x += cw
            y += row_h

            # Divider line
            painter.drawLine(margin, y - 4, width - margin, y - 4)

            for data_row in rows:
                if y + row_h > height - margin:
                    printer.newPage()
                    y = margin
                x = margin
                for val, cw in zip(data_row, col_widths):
                    # Truncate long text to fit column width
                    painter.drawText(x, y, val[:50])
                    x += cw
                y += row_h
        finally:
            painter.end()

        QMessageBox.information(self, "Export PDF", f"Rashmatz exported to:\n{path}")

    # ──────────────────────────────────────────────── export Sheets ──── #

    @staticmethod
    def _extract_sheet_key(raw: str) -> str:
        raw = (raw or "").strip()
        m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", raw)
        return m.group(1) if m else raw

    def _export_sheets(self):
        config    = self._config_svc.get_config()
        saved_id  = config.google_sheets_id or ""
        saved_creds = config.google_creds_path or ""

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
            "Rashmatz",
        )
        if not ok or not worksheet_title.strip():
            return

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
                "Install it with:  pip install gspread",
            )
            return

        if not os.path.exists(creds_path):
            QMessageBox.critical(
                self,
                "Google Sheets",
                "Service-account credentials file not found.\n"
                "Set KAVMANAGER_GOOGLE_CREDENTIALS or create data/google-service-account.json.\n"
                "See docs/GOOGLE_SHEETS_EXPORT_TUTORIAL.md for setup instructions.",
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
                    cols=max(10, len(headers) + 2),
                )
            ws.update("A1", values)
            QMessageBox.information(
                self,
                "Google Sheets",
                f"Rashmatz exported to sheet '{worksheet_title.strip()}'.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Google Sheets Export Failed", str(exc))


# ── Inline form dialog ────────────────────────────────────────────────────── #

class _TeamGearItemFormDialog(QDialog):
    """Small form for adding or editing a single team gear item."""

    def __init__(self, item_name: str = "", quantity: int = 1,
                 serial_number: str = "", notes: str = "", parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setWindowTitle("Add Item" if not item_name else "Edit Item")

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 12)

        form = QFormLayout()
        form.setSpacing(8)

        self._name_edit = QLineEdit(item_name)
        self._name_edit.setPlaceholderText("e.g. Helmet, Vest, Radio…")

        self._qty_spin = QSpinBox()
        self._qty_spin.setMinimum(1)
        self._qty_spin.setMaximum(9999)
        self._qty_spin.setValue(max(1, quantity))

        self._serial_edit = QLineEdit(serial_number)
        self._serial_edit.setPlaceholderText("Optional — leave blank if none")

        self._notes_edit = QLineEdit(notes)
        self._notes_edit.setPlaceholderText("Optional — condition, storage location, etc.")

        form.addRow("Item name *:", self._name_edit)
        form.addRow("Quantity:",    self._qty_spin)
        form.addRow("Serial no.:",  self._serial_edit)
        form.addRow("Notes:",       self._notes_edit)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("[ CANCEL ]")
        ok_btn     = QPushButton("[ SAVE ]")
        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def _on_ok(self):
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "Required", "Item name cannot be empty.")
            self._name_edit.setFocus()
            return
        self.accept()

    def item_name(self) -> str:
        return self._name_edit.text().strip()

    def quantity(self) -> int:
        return self._qty_spin.value()

    def serial_number(self) -> str:
        return self._serial_edit.text().strip()

    def notes(self) -> str:
        return self._notes_edit.text().strip()
