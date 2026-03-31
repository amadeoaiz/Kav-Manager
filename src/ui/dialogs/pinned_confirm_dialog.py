"""
Pre-reconcile confirmation dialog — shown when future pinned assignments exist.
"""
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
from PyQt6.QtCore import Qt


class PinnedConfirmDialog(QDialog):
    """
    Asks the commander what to do with manually pinned assignments before reconcile.

    Result codes:
        Accepted + keep_pinned=True  → keep pinned, plan around them
        Accepted + keep_pinned=False → clear all pins, re-plan from scratch
        Rejected                     → cancel reconcile
    """

    KEEP = "keep"
    CLEAR = "clear"
    CANCEL = "cancel"

    def __init__(self, pinned_count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manual Assignments Found")
        self.setModal(True)
        self.setMinimumWidth(400)
        self._result = self.CANCEL

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        hdr = QLabel(f"You have {pinned_count} manually assigned slot(s).")
        hdr.setObjectName("sectionHeader")
        hdr.setWordWrap(True)
        layout.addWidget(hdr)

        hint = QLabel("What should I do?")
        hint.setObjectName("dimLabel")
        layout.addWidget(hint)

        layout.addSpacing(8)

        keep_btn = QPushButton("[ KEEP AND PLAN AROUND THEM ]")
        keep_btn.clicked.connect(self._on_keep)
        layout.addWidget(keep_btn)

        clear_btn = QPushButton("[ CLEAR ALL AND RE-PLAN ]")
        clear_btn.clicked.connect(self._on_clear)
        layout.addWidget(clear_btn)

        cancel_btn = QPushButton("[ CANCEL ]")
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(cancel_btn)

    def _on_keep(self):
        self._result = self.KEEP
        self.accept()

    def _on_clear(self):
        self._result = self.CLEAR
        self.accept()

    @property
    def result_action(self) -> str:
        return self._result
