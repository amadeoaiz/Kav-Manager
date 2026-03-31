"""
GearListDialog — view and manage the equipment list for a single soldier.

Columns: Item Name | Qty | Serial No.
Actions: + ADD ITEM button, double-click to edit, right-click to edit or delete.
All writes are committed immediately; no separate save step required.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QSpinBox, QFormLayout, QMessageBox, QMenu, QWidget,
)
from PyQt6.QtCore import Qt

from src.core.models import Soldier
from src.services.gear_service import GearService


def _soldier_label(soldier: Soldier) -> str:
    return soldier.name or f"#{soldier.id}"


class GearListDialog(QDialog):
    """Modal dialog for managing a soldier's gear/equipment list."""

    def __init__(self, db, soldier: Soldier, parent=None):
        super().__init__(parent)
        self.db = db
        self.soldier = soldier
        self._gear_svc = GearService(db)

        self.setModal(True)
        self.setMinimumSize(560, 420)
        self.setWindowTitle(f"GEAR LIST — {_soldier_label(soldier).upper()}")

        self._setup_ui()
        self._reload()

    # ──────────────────────────────────────────────── UI construction ──── #

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        # Header row
        hdr_row = QHBoxLayout()
        title = QLabel(f"GEAR — {_soldier_label(self.soldier).upper()}")
        title.setObjectName("sectionHeader")
        hdr_row.addWidget(title)
        hdr_row.addStretch()
        self._add_btn = QPushButton("[ + ADD ITEM ]")
        self._add_btn.clicked.connect(self._on_add)
        hdr_row.addWidget(self._add_btn)
        root.addLayout(hdr_row)

        hint = QLabel("Double-click a row to edit. Right-click for edit / delete.")
        hint.setObjectName("dimLabel")
        root.addWidget(hint)

        # Gear table
        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["ITEM NAME", "QTY", "SERIAL NO."])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.doubleClicked.connect(self._on_double_click)
        root.addWidget(self._table, 1)

        # Close button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("[ CLOSE ]")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ──────────────────────────────────────────────── data ──── #

    def _reload(self):
        items = self._gear_svc.list_soldier_gear(self.soldier.id)
        self._table.setRowCount(len(items))
        for row, item in enumerate(items):
            serial = item.serial_number or "—"
            for col, val in enumerate([item.item_name, str(item.quantity), serial]):
                cell = QTableWidgetItem(val)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                cell.setData(Qt.ItemDataRole.UserRole, item.id)
                self._table.setItem(row, col, cell)
        self._table.resizeRowsToContents()

    def _item_id_at_row(self, row: int) -> int | None:
        cell = self._table.item(row, 0)
        return cell.data(Qt.ItemDataRole.UserRole) if cell else None

    # ──────────────────────────────────────────────── actions ──── #

    def _on_add(self):
        dlg = _GearItemFormDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._gear_svc.add_soldier_gear(
                soldier_id=self.soldier.id,
                item_name=dlg.item_name(),
                quantity=dlg.quantity(),
                serial_number=dlg.serial_number() or None,
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
        # Read current values for the form
        items = self._gear_svc.list_soldier_gear(self.soldier.id)
        gear = next((g for g in items if g.id == item_id), None)
        if not gear:
            return
        dlg = _GearItemFormDialog(
            item_name=gear.item_name,
            quantity=gear.quantity,
            serial_number=gear.serial_number or "",
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._gear_svc.update_soldier_gear(
                item_id,
                item_name=dlg.item_name(),
                quantity=dlg.quantity(),
                serial_number=dlg.serial_number() or None,
            )
            self._reload()

    def _delete_row(self, row: int):
        item_id = self._item_id_at_row(row)
        if item_id is None:
            return
        # Read current item for confirmation message
        items = self._gear_svc.list_soldier_gear(self.soldier.id)
        gear = next((g for g in items if g.id == item_id), None)
        if not gear:
            return
        ans = QMessageBox.question(
            self, "Delete item",
            f"Delete <b>{gear.item_name}</b> from this soldier's gear list?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self._gear_svc.delete_soldier_gear(item_id)
            self._reload()


# ── Inline form dialog ────────────────────────────────────────────────────── #

class _GearItemFormDialog(QDialog):
    """Small form for adding or editing a single gear item."""

    def __init__(self, item_name: str = "", quantity: int = 1,
                 serial_number: str = "", parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumWidth(360)
        self.setWindowTitle("Add Item" if not item_name else "Edit Item")

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 12)

        form = QFormLayout()
        form.setSpacing(8)

        self._name_edit = QLineEdit(item_name)
        self._name_edit.setPlaceholderText("e.g. M16 Rifle, Vest, Helmet…")

        self._qty_spin = QSpinBox()
        self._qty_spin.setMinimum(1)
        self._qty_spin.setMaximum(9999)
        self._qty_spin.setValue(max(1, quantity))

        self._serial_edit = QLineEdit(serial_number)
        self._serial_edit.setPlaceholderText("Optional — leave blank if none")

        form.addRow("Item name *:", self._name_edit)
        form.addRow("Quantity:",     self._qty_spin)
        form.addRow("Serial no.:",   self._serial_edit)
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
