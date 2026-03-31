"""
KavManager UI Stylesheets.
Night-vision terminal aesthetic for dark mode.
Clean military-green light mode for daytime use.
"""

import os as _os
import tempfile as _tempfile

# SVG arrows are generated at runtime — always use a writable temp dir
_ASSETS_DIR = _os.path.join(_tempfile.gettempdir(), "kavmanager_assets")
_os.makedirs(_ASSETS_DIR, exist_ok=True)


def _make_arrow_svg(direction: str, color: str) -> str:
    """Create a small SVG arrow and return its absolute path (forward-slash)."""
    safe = color.replace("#", "")
    path = _os.path.join(_ASSETS_DIR, f"arrow_{direction}_{safe}.svg")
    if direction == "up":
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="5">'
               f'<polygon points="4,0 8,5 0,5" fill="{color}"/></svg>')
    else:
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="5">'
               f'<polygon points="0,0 8,0 4,5" fill="{color}"/></svg>')
    with open(path, "w") as f:
        f.write(svg)
    return path.replace("\\", "/")


# ── Design tokens ─────────────────────────────────────────────────────────────
_DARK = {
    # Warm khaki-brown base — military-esque palette
    "bg_primary":    "#3b3428",
    "bg_secondary":  "#443d30",
    "bg_tertiary":   "#4d4538",
    "bg_hover":      "#574f40",
    "bg_selected":   "#5e5545",
    "green_bright":  "#00e676",
    "green_mid":     "#4caf50",
    "green_dim":     "#2e7d32",
    "border":        "#4d4538",
    "border_bright": "#4caf50",
    "amber":         "#ffd600",
    "amber_dim":     "#7a6200",
    "red":           "#ff1744",
    "red_dim":       "#7a0010",
    # Warm off-white text for readability on brown backgrounds
    "text_primary":  "#e8dfd4",
    "text_secondary":"#c4b8a8",
    "text_dim":      "#9a8e7e",
    "bg_header":     "#332d22",
    "bg_alt_row":    "#403828",
    "font":          "Consolas, 'Courier New', monospace",
}

_LIGHT = {
    "bg_primary":    "#eaeeea",
    "bg_secondary":  "#dde4dd",
    "bg_tertiary":   "#d0d8d0",
    "bg_hover":      "#c4d0c4",
    "bg_selected":   "#b4c4b4",
    "green_bright":  "#205522",
    "green_mid":     "#2e7d32",
    "green_dim":     "#3c7a3f",
    "border":        "#a0bca0",
    "border_bright": "#2e7d32",
    "amber":         "#f57f17",
    "amber_dim":     "#e6a817",
    "red":           "#c62828",
    "red_dim":       "#e57373",
    "text_primary":  "#1b2b1b",
    "text_secondary":"#264c26",
    "text_dim":      "#4e7040",
    "bg_header":     "#d0d8d0",
    "bg_alt_row":    "#dde4dd",
    "font":          "Consolas, 'Courier New', monospace",
}


_DARK["arrow_up"] = _make_arrow_svg("up", _DARK["green_bright"])
_DARK["arrow_down"] = _make_arrow_svg("down", _DARK["green_bright"])
_LIGHT["arrow_up"] = _make_arrow_svg("up", _LIGHT["green_bright"])
_LIGHT["arrow_down"] = _make_arrow_svg("down", _LIGHT["green_bright"])


def _build_qss(t: dict) -> str:
    return f"""
/* ── Global ─────────────────────────────────────────────────────────────── */
QMainWindow, QWidget {{
    background-color: {t['bg_primary']};
    color: {t['text_primary']};
    font-family: {t['font']};
    font-size: 14px;
}}

QSplitter::handle {{
    background-color: {t['border']};
}}

/* ── Top bar ─────────────────────────────────────────────────────────────── */
#topBar {{
    background-color: {t['bg_secondary']};
    border: none;
}}

#appTitle {{
    color: {t['green_bright']};
    font-size: 16px;
    font-weight: bold;
    letter-spacing: 2px;
}}

#dirtyBadge {{
    color: {t['amber']};
    font-weight: bold;
    padding: 4px 10px;
    border: 1px solid {t['amber']};
    background-color: {t['amber_dim']};
}}

/* ── Tab bar ─────────────────────────────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {t['border']};
    border-top: none;
    background-color: {t['bg_primary']};
    top: -1px;
}}

QTabBar {{
    qproperty-drawBase: false;
}}

QTabBar::tab {{
    background-color: {t['bg_secondary']};
    color: {t['text_secondary']};
    padding: 8px 20px;
    border: 1px solid {t['border']};
    border-bottom: none;
    margin-right: 2px;
    margin-bottom: 0px;
    letter-spacing: 1px;
    font-size: 13px;
    min-height: 20px;
}}

QTabBar::tab:selected {{
    background-color: {t['bg_primary']};
    color: {t['green_bright']};
    border-bottom: 2px solid {t['green_bright']};
    font-weight: bold;
    padding: 8px 20px;
}}

QTabBar::tab:hover:!selected {{
    background-color: {t['bg_hover']};
    color: {t['text_primary']};
}}

/* ── Buttons ─────────────────────────────────────────────────────────────── */
QPushButton {{
    background-color: {t['bg_secondary']};
    color: {t['green_bright']};
    border: 1px solid {t['border_bright']};
    padding: 7px 16px;
    font-family: {t['font']};
    font-size: 13px;
    letter-spacing: 1px;
}}

QPushButton:hover {{
    background-color: {t['bg_hover']};
    border-color: {t['green_bright']};
    color: #ffffff;
}}

QPushButton:pressed {{
    background-color: {t['green_bright']};
    color: {t['bg_primary']};
}}

QPushButton:disabled {{
    color: {t['text_dim']};
    border-color: {t['border']};
    background-color: {t['bg_secondary']};
}}

QPushButton#dangerBtn {{
    border-color: {t['red']};
    color: {t['red']};
}}
QPushButton#dangerBtn:hover {{
    background-color: {t['red_dim']};
    color: #ffffff;
}}

QPushButton#approveBtn {{
    border-color: {t['green_bright']};
    color: {t['green_bright']};
}}
QPushButton#rejectBtn {{
    border-color: {t['red']};
    color: {t['red']};
}}

QPushButton#themeToggleBtn {{
    border-radius: 16px;
    padding: 0px;
    font-size: 20px;
    min-width: 32px;
    min-height: 32px;
    max-width: 32px;
    max-height: 32px;
}}

/* ── Inputs ──────────────────────────────────────────────────────────────── */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {t['bg_secondary']};
    color: {t['text_primary']};
    border: 1px solid {t['border']};
    padding: 6px 10px;
    selection-background-color: {t['bg_selected']};
}}

QLineEdit:focus, QTextEdit:focus {{
    border-color: {t['green_bright']};
}}

QComboBox {{
    background-color: {t['bg_secondary']};
    color: {t['text_primary']};
    border: 1px solid {t['border']};
    padding: 6px 10px;
    min-width: 100px;
}}

QComboBox::drop-down {{
    border: none;
    width: 20px;
}}

QComboBox QAbstractItemView {{
    background-color: {t['bg_secondary']};
    color: {t['text_primary']};
    border: 1px solid {t['border_bright']};
    selection-background-color: {t['bg_selected']};
}}

QSpinBox, QDoubleSpinBox, QTimeEdit, QDateEdit {{
    background-color: {t['bg_secondary']};
    color: {t['text_primary']};
    border: 1px solid {t['border']};
    padding: 6px 10px;
}}

QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QTimeEdit::up-button, QTimeEdit::down-button,
QDateEdit::up-button, QDateEdit::down-button {{
    background-color: {t['bg_tertiary']};
    border: 1px solid {t['border']};
    width: 20px;
    height: 12px;
}}

QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover,
QTimeEdit::up-button:hover, QTimeEdit::down-button:hover,
QDateEdit::up-button:hover, QDateEdit::down-button:hover {{
    background-color: {t['bg_hover']};
    border-color: {t['green_mid']};
}}

QSpinBox::up-arrow, QDoubleSpinBox::up-arrow,
QTimeEdit::up-arrow, QDateEdit::up-arrow {{
    image: url({t['arrow_up']});
    width: 8px;
    height: 5px;
}}

QSpinBox::down-arrow, QDoubleSpinBox::down-arrow,
QTimeEdit::down-arrow, QDateEdit::down-arrow {{
    image: url({t['arrow_down']});
    width: 8px;
    height: 5px;
}}

/* ── Tables ──────────────────────────────────────────────────────────────── */
QTableWidget {{
    background-color: {t['bg_secondary']};
    color: {t['text_primary']};
    gridline-color: {t['border']};
    border: 1px solid {t['border']};
    alternate-background-color: {t['bg_alt_row']};
    selection-background-color: {t['bg_selected']};
    selection-color: #ffffff;
}}

QTableWidget::item {{
    padding: 6px 10px;
    border: none;
}}

QTableWidget::item:selected {{
    background-color: {t['bg_selected']};
    color: #ffffff;
}}

QHeaderView::section {{
    background-color: {t['bg_header']};
    color: {t['text_secondary']};
    border: 1px solid {t['border']};
    padding: 8px 10px;
    font-weight: bold;
    font-size: 12px;
    letter-spacing: 1px;
}}

/* ── Scroll bars ─────────────────────────────────────────────────────────── */
QScrollBar:vertical {{
    background-color: {t['bg_secondary']};
    width: 10px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background-color: {t['bg_hover']};
    min-height: 20px;
    border-radius: 3px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar:horizontal {{
    background-color: {t['bg_secondary']};
    height: 10px;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background-color: {t['bg_hover']};
    min-width: 20px;
    border-radius: 3px;
}}

/* ── Labels ──────────────────────────────────────────────────────────────── */
QLabel {{
    color: {t['text_primary']};
}}

QLabel#sectionHeader {{
    font-size: 16px;
    font-weight: bold;
    color: {t['green_bright']};
    letter-spacing: 2px;
    padding: 6px 0px;
    border-bottom: 1px solid {t['border_bright']};
}}

QLabel#dimLabel {{
    color: {t['text_dim']};
    font-size: 12px;
}}

QLabel#statusOK {{
    color: {t['green_bright']};
    font-weight: bold;
}}

QLabel#statusPartial {{
    color: {t['amber']};
    font-weight: bold;
}}

QLabel#statusCritical {{
    color: {t['red']};
    font-weight: bold;
}}

/* ── Group boxes ─────────────────────────────────────────────────────────── */
QGroupBox {{
    border: 1px solid {t['border']};
    margin-top: 12px;
    padding-top: 8px;
    color: {t['text_secondary']};
    font-weight: bold;
    letter-spacing: 1px;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    color: {t['green_mid']};
}}

/* ── Pill bar (Soldiers tab toggle) ─────────────────────────────────────── */
#pillBar {{
    background-color: {t['bg_secondary']};
    border-bottom: 1px solid {t['border']};
}}

QPushButton#pillBtn {{
    border: 1px solid {t['border_bright']};
    color: {t['text_secondary']};
    padding: 6px 18px;
}}

QPushButton#pillBtn:checked {{
    background-color: {t['bg_selected']};
    color: {t['green_bright']};
    border-color: {t['green_bright']};
    font-weight: bold;
}}

/* ── Active Now strip (Home tab bottom) ──────────────────────────────────── */
#activeNowStrip {{
    background-color: {t['bg_secondary']};
    border-top: 1px solid {t['border']};
}}

/* ── Misc ────────────────────────────────────────────────────────────────── */
QSizeGrip {{
    background-color: transparent;
}}

QStatusBar {{
    background-color: {t['bg_secondary']};
    color: {t['text_secondary']};
    border-top: 1px solid {t['border']};
}}

QToolTip {{
    background-color: {t['bg_tertiary']};
    color: {t['text_primary']};
    border: 1px solid {t['border_bright']};
    padding: 6px 10px;
    font-size: 13px;
}}
"""


DARK_THEME = _build_qss(_DARK)
LIGHT_THEME = _build_qss(_LIGHT)

# Cell color palettes for the calendar/grid widgets (not handled by QSS)
CELL_COLORS = {
    "dark": {
        "surplus":  ("#2d8a2d", "#e0ffe0"),   # bright green (well above minimum)
        "ok":       ("#4a8c3f", "#d0f0c8"),   # medium green (at minimum)
        "partial":  ("#c49a20", "#fff8d0"),   # bright amber (not ready, almost there)
        "critical": ("#cc4444", "#ffe0e0"),   # bright red (not ready, far)
        "empty":    ("#332d22", "#6a7a5a"),
        "absent":   ("#cc4444", "#ffe0e0"),
        "present":  ("#4a8c3f", "#d0f0c8"),
        "partial_day": ("#c49a20", "#fff8d0"),
        # Drafted but no presence info (muted), vs not drafted (fully empty)
        "inactive":   ("#4a4438", "#a09880"),
        "not_active": (_DARK["bg_primary"], _DARK["bg_primary"]),
        "today_border": "#00e676",
        "selected_border": "#ffffff",
    },
    "light": {
        "surplus":  ("#c8e6c9", "#1b5e20"),   # solid green (well above minimum)
        "ok":       ("#e0f2c2", "#33691e"),   # greenish yellow (at minimum)
        "partial":  ("#ffecb3", "#e65100"),   # yellow/orange with red hint (not ready)
        "critical": ("#ffcdd2", "#c62828"),   # red (not ready)
        "empty":    ("#e8eae8", "#546e54"),
        "absent":   ("#ffcdd2", "#b71c1c"),
        "present":  ("#c8e6c9", "#1b5e20"),
        "partial_day": ("#f5fbe7", "#4e7a2f"),
        # Drafted but no presence info (solid grey block), vs not drafted (fully empty)
        "inactive":   ("#cfd3cf", "#555555"),   # drafted, no presence
        "not_active": (_LIGHT["bg_primary"], _LIGHT["bg_primary"]),  # not drafted (blend into background)
        "today_border": "#1b5e20",
        "selected_border": "#000000",
    },
}
