from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QTabWidget, QApplication, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

from src.ui.stylesheet import DARK_THEME, LIGHT_THEME
from src.ui.tabs.home_tab import HomeTab
from src.ui.tabs.grid_tab import GridTab
from src.ui.tabs.tasks_tab import TasksTab
from src.ui.tabs.soldiers_tab import SoldiersTab
from src.ui.tabs.rashmatz_tab import RashmatzTab
from src.ui.tabs.stats_tab import StatsTab
from src.ui.tabs.settings_tab import SettingsTab
from src.ui.tabs.guide_tab import GuideTab
from src.services.config_service import ConfigService
from src.services.task_service import TaskService
from src.services.schedule_service import ScheduleService


class KavManagerWindow(QMainWindow):
    """
    Main application window. Owns the top bar (dirty flag, reconcile button,
    notify button), the tab widget, and the 60-second auto-refresh timer.
    """

    def __init__(self, db):
        super().__init__()
        self.db = db
        self._config_svc = ConfigService(db)
        self._task_svc = TaskService(db)
        self._dirty = False
        self.bot_runner = None

        self._load_theme()
        self._build_ui()
        self._start_refresh_timer()

    # ─── Theme ────────────────────────────────────────────────────────────────

    def _load_theme(self):
        config = self._config_svc.get_config()
        self._theme = config.theme or 'dark'
        self._apply_theme(self._theme)

    def _apply_theme(self, theme: str):
        self._theme = theme
        qss = DARK_THEME if theme == 'dark' else LIGHT_THEME
        QApplication.instance().setStyleSheet(qss)

    def update_unit_name(self, name: str):
        label = f"◈  KavManager  ·  {name}" if name else "◈  KavManager"
        self._title_label.setText(label)
        self.setWindowTitle(f"KavManager — {name}" if name else "KavManager")

    def toggle_theme(self):
        new_theme = 'light' if self._theme == 'dark' else 'dark'
        self._config_svc.save_config(theme=new_theme)
        self._apply_theme(new_theme)
        # Refresh all tabs so theme-sensitive widgets (cells, legends) update immediately
        for attr in ('home_tab', 'grid_tab', 'tasks_tab', 'soldiers_tab', 'rashmatz_tab', 'stats_tab', 'settings_tab', 'guide_tab'):
            tab = getattr(self, attr, None)
            if tab and hasattr(tab, 'refresh'):
                tab.refresh()

    # ─── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        unit = self._config_svc.get_unit_codename() or "KavManager"

        self.setWindowTitle(f"KavManager — {unit}")
        self.setMinimumSize(1280, 760)

        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_top_bar(unit))

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        root_layout.addWidget(self.tabs)

        self.home_tab      = HomeTab(self.db, self)
        self.grid_tab      = GridTab(self.db, self)
        self.tasks_tab     = TasksTab(self.db, self)
        self.soldiers_tab  = SoldiersTab(self.db, self)
        self.rashmatz_tab  = RashmatzTab(self.db, self)
        self.stats_tab     = StatsTab(self.db, self)
        self.settings_tab  = SettingsTab(self.db, self)
        self.guide_tab     = GuideTab(self.db, self)

        self.tabs.addTab(self.home_tab,      "  HOME  ")
        self.tabs.addTab(self.grid_tab,      "  GRID  ")
        self.tabs.addTab(self.tasks_tab,     "  TASKS  ")
        self.tabs.addTab(self.soldiers_tab,  "  SOLDIERS  ")
        self.tabs.addTab(self.rashmatz_tab,  "  RASHMATZ  ")
        self.tabs.addTab(self.stats_tab,     "  STATS  ")
        self.tabs.addTab(self.settings_tab,  "  SETTINGS  ")
        self.tabs.addTab(self.guide_tab,     "  GUIDE  ")

        # Inbox badge on Soldiers tab — updated on refresh
        self._update_inbox_badge()

        # Refresh the tab when the user switches to it
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index: int):
        tab = self.tabs.widget(index)
        if hasattr(tab, 'refresh'):
            tab.refresh()

    def _build_top_bar(self, unit: str) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setFixedHeight(50)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)

        self._title_label = QLabel(f"◈  KavManager  ·  {unit}")
        self._title_label.setObjectName("appTitle")
        layout.addWidget(self._title_label)

        layout.addStretch()

        self.dirty_badge = QLabel("⚠   SCHEDULE NEEDS RECALCULATION")
        self.dirty_badge.setObjectName("dirtyBadge")
        self.dirty_badge.setVisible(False)
        layout.addWidget(self.dirty_badge)

        self.reconcile_btn = QPushButton("[ RECALCULATE SCHEDULE ]")
        self.reconcile_btn.setToolTip("Rebuild the schedule from current presence and task data")
        self.reconcile_btn.clicked.connect(self._on_reconcile)
        layout.addWidget(self.reconcile_btn)

        self.notify_btn = QPushButton("[ NOTIFY SOLDIERS ]")
        self.notify_btn.setToolTip("Send updated schedule to all soldiers via Matrix")
        self.notify_btn.setEnabled(False)
        self.notify_btn.clicked.connect(self._on_notify_soldiers)
        layout.addWidget(self.notify_btn)

        theme_btn = QPushButton("◑")
        theme_btn.setObjectName("themeToggleBtn")
        theme_btn.setToolTip("Toggle dark / light theme")
        theme_btn.setFixedSize(32, 32)
        theme_btn.clicked.connect(self.toggle_theme)
        layout.addWidget(theme_btn)

        return bar

    # ─── Dirty flag ───────────────────────────────────────────────────────────

    def set_dirty(self, dirty: bool):
        self._dirty = dirty
        self.dirty_badge.setVisible(dirty)

    def request_reconcile_if_dirty(self, reason: str = ""):
        """Called by tabs after any schedule-affecting change. Prompts the commander."""
        if not self._dirty:
            return
        msg = QMessageBox(self)
        msg.setWindowTitle("Reconcile Schedule?")
        msg.setText(
            f"Changes detected{': ' + reason if reason else ''}.\n\n"
            "Rebuild the schedule now to apply them?"
        )
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        msg.setDefaultButton(QMessageBox.StandardButton.Yes)
        if msg.exec() == QMessageBox.StandardButton.Yes:
            self._on_reconcile()

    def mark_dirty(self, reason: str = ""):
        self.set_dirty(True)
        self.request_reconcile_if_dirty(reason)

    # ─── Reconcile ────────────────────────────────────────────────────────────

    def _on_reconcile(self):
        try:
            if not self._handle_pinned_pre_reconcile():
                return  # cancelled
            from src.ui.dialogs.schedule_progress_dialog import ScheduleProgressDialog
            svc = ScheduleService(self.db)
            dlg = ScheduleProgressDialog(svc, parent=self)
            if dlg.exec() == dlg.DialogCode.Accepted:
                self.set_dirty(False)
                self.refresh_current_tab()
                self._update_inbox_badge()
                self._check_uncovered_retry(svc)
            elif dlg.error and dlg.error != "Cancelled by user":
                QMessageBox.critical(self, "Reconcile Failed", dlg.error)
        except Exception as e:
            QMessageBox.critical(self, "Reconcile Failed", str(e))

    def _handle_pinned_pre_reconcile(self) -> bool:
        """Check for pinned assignments and prompt. Returns False if cancelled."""
        sched_svc = ScheduleService(self.db)
        pinned = sched_svc.count_future_pinned()
        if pinned == 0:
            return True

        from src.ui.dialogs.pinned_confirm_dialog import PinnedConfirmDialog
        dlg = PinnedConfirmDialog(pinned, parent=self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return False

        if dlg.result_action == PinnedConfirmDialog.CLEAR:
            sched_svc.clear_future_pinned()

        return True

    def _check_uncovered_retry(self, svc: ScheduleService):
        """After reconcile, offer to include commander for uncovered tasks."""
        uncovered = self._task_svc.get_uncovered_tasks()
        if not uncovered:
            return

        retryable = [t for t in uncovered if not t.include_commander]
        if retryable:
            from src.ui.dialogs.retry_uncovered_dialog import RetryUncoveredDialog
            dlg = RetryUncoveredDialog(retryable, parent=self)
            if dlg.exec() == dlg.DialogCode.Accepted and dlg.retry_requested:
                selected_ids = dlg.selected_task_ids()
                if selected_ids:
                    for tid in selected_ids:
                        self._task_svc.set_include_commander(tid, True)
                    self._task_svc.commit()
                    # Re-run reconcile (one-shot)
                    from src.ui.dialogs.schedule_progress_dialog import ScheduleProgressDialog
                    dlg2 = ScheduleProgressDialog(svc, parent=self)
                    if dlg2.exec() == dlg2.DialogCode.Accepted:
                        self.set_dirty(False)
                        self.refresh_current_tab()
                        self._update_inbox_badge()
                    # Show standard warning for remaining uncovered
                    self._show_uncovered_warning()
                    return
            # User cancelled — fall through

        self._show_uncovered_warning()

    def _show_uncovered_warning(self):
        uncovered = self._task_svc.get_uncovered_tasks()
        if uncovered:
            names = "\n".join(f"  - {t.real_title or f'Task#{t.id}'}" for t in uncovered)
            QMessageBox.warning(
                self,
                "Coverage Warning",
                f"The following tasks could not be fully covered:\n\n"
                f"{names}\n\n"
                f"Consider removing a task or adjusting soldier availability.",
            )

    # ─── Refresh ──────────────────────────────────────────────────────────────

    def _start_refresh_timer(self):
        self._timer = QTimer(self)
        self._timer.setInterval(60_000)     # 60 seconds
        self._timer.timeout.connect(self._auto_refresh)
        self._timer.start()

    def _auto_refresh(self):
        self.refresh_current_tab()
        self._update_inbox_badge()
        self._sync_notify_button()

    def refresh_current_tab(self):
        tab = self.tabs.currentWidget()
        if hasattr(tab, 'refresh'):
            tab.refresh()

    def _update_inbox_badge(self):
        count = ScheduleService(self.db).count_pending_review()
        label = f"  SOLDIERS{f'  ●{count}' if count else ''}  "
        self.tabs.setTabText(3, label)

    # ─── Matrix notify ──────────────────────────────────────────────────────

    def _sync_notify_button(self):
        runner = self.bot_runner
        self.notify_btn.setEnabled(bool(runner and runner.running))

    def _on_notify_soldiers(self):
        """Send a one-shot schedule notification to all soldiers via Matrix."""
        runner = self.bot_runner
        if not runner or not runner.running:
            QMessageBox.warning(self, "Bot not running",
                                "Start the Matrix bot first (Settings > MATRIX CHAT).")
            return

        try:
            notified = runner.notify_all_sync()
            QMessageBox.information(
                self, "Notify",
                f"Schedule notifications sent to {notified} soldier(s)."
            )
        except Exception as e:
            QMessageBox.critical(self, "Notify failed", str(e))
