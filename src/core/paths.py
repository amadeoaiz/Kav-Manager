"""
Frozen-aware path helpers for PyInstaller compatibility.

When running from a PyInstaller bundle, bundled resources (assets, docs)
live in sys._MEIPASS, but runtime data (database, nio-store) must live
in a writable directory next to the executable.
"""
import sys
import os


def is_frozen():
    """True if running from a PyInstaller bundle."""
    return getattr(sys, 'frozen', False)


def get_bundle_dir():
    """Directory containing bundled resources (assets, docs).
    In dev: project root. In PyInstaller: sys._MEIPASS."""
    if is_frozen():
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def get_data_dir():
    """Writable directory for runtime data (database, nio-store, backups).
    In dev: project root / data.
    In PyInstaller: next to the exe / data."""
    if is_frozen():
        base = os.path.join(os.path.dirname(sys.executable), 'data')
    else:
        base = os.path.join(get_bundle_dir(), 'data')
    os.makedirs(base, exist_ok=True)
    return base


def get_assets_dir():
    """Directory containing assets (icons, guide HTML files)."""
    return os.path.join(get_bundle_dir(), 'src', 'assets')


def get_docs_dir():
    """Directory containing docs."""
    return os.path.join(get_bundle_dir(), 'docs')


def get_project_root():
    """Project root — same as get_bundle_dir() but named explicitly
    for sys.path manipulation in dev mode."""
    return get_bundle_dir()
