import gc
import os
import sys
from datetime import datetime, timedelta

# PyInstaller with console=False sets sys.stdout/stderr to None.
# Redirect to devnull so print() and faulthandler don't crash.
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')

import faulthandler
faulthandler.enable()  # print Python traceback on segfault

# Ensure project root is on sys.path before any src imports
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.core.paths import get_assets_dir

from src.core.database import init_db, SessionLocal, DB_PATH
from src.utils.maintenance import MaintenanceManager


def run_maintenance_check(db_session):
    """Triggers maintenance if it is a scheduled hour, or > 6 hours since last run."""
    backup_dir = os.path.join(os.path.dirname(DB_PATH), "backups")
    manager = MaintenanceManager(db_session, db_path=DB_PATH, backup_dir=backup_dir)

    now = datetime.now()
    last_run = manager.get_last_run()
    target_hours = [8, 14, 20]

    should_run = False
    reason = ""

    if now.hour in target_hours:
        if last_run is None or last_run.hour != now.hour or (now - last_run) > timedelta(minutes=60):
            should_run = True
            reason = f"Scheduled hour ({now.hour}:00)"

    if not should_run and last_run and (now - last_run) > timedelta(hours=6):
        should_run = True
        reason = "Gap > 6 hours"

    if last_run is None:
        should_run = True
        reason = "Initial setup"

    if should_run:
        print(f"[MAINTENANCE] Triggering: {reason}")
        manager.run_full_maintenance(tag="auto")
        manager.update_last_run()
    else:
        print("[HEALTH] Maintenance up to date.")


def main():
    print("--- KavManager: Reserve Duty Edition ---")

    # On Windows, tell the OS this is its own app (not python.exe) so the
    # taskbar shows our icon instead of the Python icon.  Must be called
    # before QApplication is created.
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "kavmanager.app.1.0"
            )
        except Exception:
            pass

    # 1. Initialize DB (creates tables + seeds roles + seeds UnitConfig)
    init_db()

    # 2. Run maintenance check
    db = SessionLocal()
    try:
        run_maintenance_check(db)
    finally:
        db.close()

    # 3. Launch UI
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QFont, QIcon
    from PyQt6.QtCore import QSize
    from src.ui.main_window import KavManagerWindow
    from src.core.models import UnitConfig

    app = QApplication(sys.argv)
    app.setApplicationName("KavManager")

    # Set app icon — prefer the .ico file (contains all sizes natively),
    # then fall back to individual PNGs.
    assets_dir = get_assets_dir()
    ico_path = os.path.join(assets_dir, "kavmanager.ico")
    icons_dir = os.path.join(assets_dir, "icons")
    icon = QIcon()
    if os.path.exists(ico_path):
        icon.addFile(ico_path)
    for size in [16, 32, 48, 64, 128, 256]:
        path = os.path.join(icons_dir, f"icon_{size}x{size}.png")
        if os.path.exists(path):
            icon.addFile(path, QSize(size, size))
    if not icon.isNull():
        app.setWindowIcon(icon)

    # Open a fresh session for the UI (lives for the duration of the app)
    ui_db = SessionLocal()

    # Apply theme before the window is shown
    config = ui_db.query(UnitConfig).first()
    theme = config.theme if config else 'dark'
    from src.ui.stylesheet import DARK_THEME, LIGHT_THEME
    app.setStyleSheet(DARK_THEME if theme == 'dark' else LIGHT_THEME)

    # Set a monospace font as the default
    font = QFont("Consolas", 13)
    app.setFont(font)

    window = KavManagerWindow(ui_db)
    # Set icon on the window too — on Windows, QApplication.setWindowIcon
    # alone may not propagate to the taskbar / title bar reliably.
    if not icon.isNull():
        window.setWindowIcon(icon)
    window.show()
    window.raise_()
    window.activateWindow()
    window.setGeometry(100, 100, 1200, 800)

    # Start the Matrix bot in a background thread if configured
    bot_runner = None
    if config and config.matrix_homeserver_url and config.matrix_bot_token:
        try:
            from src.api.bot import MatrixBotRunner
            bot_runner = MatrixBotRunner(
                config.matrix_homeserver_url,
                config.matrix_bot_user,
                config.matrix_bot_token,
            )
            bot_runner.start()
            print("[BOT] Matrix bot starting in background (E2E encrypted)…")
        except Exception as e:
            print(f"[BOT] Failed to start Matrix bot: {e}")
    window.bot_runner = bot_runner

    def _cleanup():
        """Stop background threads and release C resources before Qt destroys widgets."""
        if bot_runner:
            print("[BOT] Stopping Matrix bot…")
            bot_runner.stop()
        # Ensure no stray QThreads are running.
        from PyQt6.QtCore import QThread
        for child in window.findChildren(QThread):
            if child.isRunning():
                child.quit()
                child.wait(5000)
            child.setParent(None)
            child.deleteLater()
        # Close all matplotlib figures — their Qt backend canvases hold C++
        # pointers that segfault if the garbage collector touches them after
        # Qt has torn down the C++ side.
        try:
            import matplotlib.pyplot as plt
            plt.close("all")
        except ImportError:
            pass
        # Process pending deleteLater calls so C++ objects are freed now,
        # not later during Python GC when Qt state is half-torn-down.
        app.processEvents()
        # Disable the garbage collector so Python won't try to finalize
        # C extension objects (Qt widgets, SQLAlchemy cyextensions, matplotlib
        # canvases) after their C++ backing has been destroyed.  The OS
        # reclaims all process memory on exit anyway.
        gc.disable()

    app.aboutToQuit.connect(_cleanup)

    # On Windows, faulthandler's vectored exception handler intercepts
    # ALL SEH exceptions — including harmless COM cross-thread calls
    # (0x8001010d / RPC_E_WRONG_THREAD) made by Qt's internal theme-change
    # observer.  Disable it before entering the event loop to suppress
    # the spurious "Windows fatal exception" message.  Real crashes still
    # produce a Windows Error Reporting dialog.
    if sys.platform == "win32":
        faulthandler.disable()

    exit_code = app.exec()
    ui_db.close()
    # _cleanup already disabled GC — just exit immediately to avoid any
    # destructor ordering issues between Python and C++ objects.
    os._exit(exit_code)


if __name__ == "__main__":
    main()
