"""
Modal progress dialog for LP schedule calculation.

Shows an indeterminate progress bar and a Cancel button.
Runs the LP solve in a QThread so the UI stays responsive.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal


class _SolveWorker(QThread):
    """Runs svc.reconcile() in a background thread."""
    finished = pyqtSignal(object)   # emits the allocator
    failed = pyqtSignal(str)        # emits error message

    def __init__(self, svc):
        super().__init__()
        self.svc = svc

    def run(self):
        try:
            allocator = self.svc.reconcile()
            self.finished.emit(allocator)
        except Exception as e:
            self.failed.emit(str(e))


class ScheduleProgressDialog(QDialog):
    """
    Modal dialog that shows progress while the LP solver runs.

    Usage:
        dlg = ScheduleProgressDialog(svc, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            allocator = dlg.allocator
    """

    def __init__(self, svc, parent=None):
        super().__init__(parent)
        self.svc = svc
        self.allocator = None
        self._error = None
        self._finished = False

        self.setWindowTitle("Schedule Calculation")
        self.setModal(True)
        self.setFixedSize(360, 130)
        self.setWindowFlags(
            self.windowFlags()
            & ~Qt.WindowType.WindowCloseButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        self._label = QLabel("Calculating schedule...")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate pulsing
        layout.addWidget(self._progress)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel)
        layout.addWidget(self._cancel_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # Start the worker thread.
        self._worker = _SolveWorker(self.svc)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_finished(self, allocator):
        self._finished = True
        self.allocator = allocator
        self.accept()

    def _on_failed(self, error_msg):
        self._finished = True
        self._error = error_msg
        self.reject()

    def _on_cancel(self):
        self._label.setText("Cancelling...")
        self._cancel_btn.setEnabled(False)
        self._error = "Cancelled by user"
        # Wait for the worker to finish before closing the dialog.
        # CBC can't be interrupted mid-solve, so we must let it complete
        # to avoid destroying the QThread while it's still running.
        self._worker.finished.disconnect(self._on_finished)
        self._worker.failed.disconnect(self._on_failed)
        self._worker.wait()
        self.reject()

    def done(self, result):
        """Ensure worker thread is stopped and detached before the dialog is destroyed."""
        if self._worker is not None:
            if self._worker.isRunning():
                self._worker.wait()
            # Detach the QThread from this dialog so Python GC won't destroy
            # the C++ QThread object during Qt's widget cleanup (segfault).
            self._worker.setParent(None)
            self._worker.deleteLater()
            self._worker = None
        super().done(result)

    @property
    def error(self):
        return self._error
