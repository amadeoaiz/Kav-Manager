"""
RetryUncoveredDialog — shown after reconcile when UNCOVERED tasks
have include_commander=False, offering to flip the flag and retry.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox,
    QWidget,
)
from PyQt6.QtCore import Qt

from src.core.models import Task


class RetryUncoveredDialog(QDialog):
    """Shows UNCOVERED tasks that exclude the commander, lets user opt-in and retry."""

    def __init__(self, tasks: list[Task], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Uncovered Tasks — Include Commander?")
        self.setModal(True)
        self.setMinimumSize(520, 300)

        self._tasks = tasks
        self._checkboxes: dict[int, QCheckBox] = {}
        self._retry_requested = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        hdr = QLabel("UNCOVERED TASKS")
        hdr.setObjectName("sectionHeader")
        layout.addWidget(hdr)

        hint = QLabel(
            "The following tasks could not be fully covered and currently exclude\n"
            "the commander. Check tasks below to include the commander and retry."
        )
        hint.setObjectName("dimLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["TASK", "WINDOW", "INCLUDE CMDR"])
        th = self._table.horizontalHeader()
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        th.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setRowCount(len(self._tasks))

        for row, t in enumerate(self._tasks):
            name = t.real_title or f"Task#{t.id}"
            self._table.setItem(row, 0, QTableWidgetItem(name))

            window = ""
            if t.start_time and t.end_time:
                if t.start_time.date() == t.end_time.date():
                    window = f"{t.start_time.strftime('%d %b')}  {t.start_time.strftime('%H:%M')} – {t.end_time.strftime('%H:%M')}"
                else:
                    window = f"{t.start_time.strftime('%d %b %H:%M')} – {t.end_time.strftime('%d %b %H:%M')}"
            self._table.setItem(row, 1, QTableWidgetItem(window))

            cb = QCheckBox()
            cb.setChecked(True)
            self._checkboxes[t.id] = cb
            container = QWidget()
            cb_layout = QHBoxLayout(container)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            cb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_layout.addWidget(cb)
            self._table.setCellWidget(row, 2, container)

        layout.addWidget(self._table, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        keep_btn = QPushButton("[ KEEP AS IS ]")
        keep_btn.clicked.connect(self.reject)
        retry_btn = QPushButton("[ INCLUDE SELECTED AND RETRY ]")
        retry_btn.clicked.connect(self._on_retry)
        btn_row.addWidget(keep_btn)
        btn_row.addWidget(retry_btn)
        layout.addLayout(btn_row)

    def _on_retry(self):
        self._retry_requested = True
        self.accept()

    @property
    def retry_requested(self) -> bool:
        return self._retry_requested

    def selected_task_ids(self) -> list[int]:
        """Return IDs of tasks where the user checked 'include commander'."""
        return [tid for tid, cb in self._checkboxes.items() if cb.isChecked()]
