from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QHBoxLayout,
    QPushButton,
)
from PyQt6.QtCore import Qt

from src.core.models import TaskAssignment
from src.core.unit_manager import UnitManager


class PendingReviewDialog(QDialog):
    """Detail view for a single pending review assignment."""

    def __init__(self, db, assignment: TaskAssignment, parent=None):
        super().__init__(parent)
        self.db = db
        self.assignment = assignment
        self.setWindowTitle("Pending review")
        self.setModal(True)
        self.setMinimumWidth(420)
        self._setup_ui()

    def _setup_ui(self):
        asgn = self.assignment
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        soldier = asgn.soldier
        task = asgn.task
        sol_name = soldier.name if soldier and soldier.name else f"#{soldier.id}" if soldier else "Unknown"
        task_name = (task.real_title or f"Task #{task.id}") if task else "Unknown task"

        when = "?"
        if asgn.start_time and asgn.end_time:
            when = (
                f"{asgn.start_time.strftime('%d %b %Y %H:%M')} – "
                f"{asgn.end_time.strftime('%H:%M')}"
            )

        header = QLabel(
            f"<b>{sol_name}</b> reported:\n"
            f"<span style='color:#ffd600'>{task_name}</span>"
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(header)

        when_lbl = QLabel(when)
        when_lbl.setObjectName("dimLabel")
        layout.addWidget(when_lbl)

        if asgn.final_weight_applied is not None:
            pts_lbl = QLabel(f"Recorded points: {asgn.final_weight_applied:.2f}")
            pts_lbl.setObjectName("dimLabel")
            layout.addWidget(pts_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        later_btn = QPushButton("[ REVIEW LATER ]")
        done_btn = QPushButton("[ MARK AS REVIEWED ]")
        btn_row.addWidget(later_btn)
        btn_row.addWidget(done_btn)
        layout.addLayout(btn_row)

        later_btn.clicked.connect(self.reject)
        done_btn.clicked.connect(self._on_mark_reviewed)

    def _on_mark_reviewed(self):
        try:
            um = UnitManager(self.db)
            # Treat "reviewed" as approved for scoring purposes.
            um.review_unplanned_task(self.assignment.id, approved=True)
        except Exception:
            # Best-effort; still close the dialog.
            pass
        self.accept()

