"""
Reusable matplotlib chart styling for KavManager.

All charts use a consistent dark background regardless of the app theme.
This avoids contrast issues with average lines and labels across themes.

The app theme (dark/light) is still used for non-chart UI text (e.g. the
+/- diff labels rendered as QLabels outside the chart).
"""
from __future__ import annotations

from matplotlib.figure import Figure


# ── Chart palette (always dark — independent of app theme) ────────────────────

CHART_BG = "#1e1e1e"          # figure/axes background
CHART_BG_ALT = "#282828"      # axes plot area
CHART_TEXT = "#e0e0e0"         # primary text (titles, bold labels)
CHART_TEXT_SEC = "#b0b0b0"     # secondary text (ticks, axis labels)
CHART_TEXT_DIM = "#707070"     # dim text (legends, annotations)
CHART_GRID = "#3a3a3a"        # grid lines
CHART_BORDER = "#444444"      # spine colour
CHART_LEGEND_BG = "#2a2a2a"   # legend background

# Day / Night bar colours
COLOR_DAY = "#5c9fd4"          # soft blue
COLOR_NIGHT = "#b07cd8"        # soft purple

# Muted alpha for context-day bars
MUTED_ALPHA = 0.35

# Day / Night average line colours (distinct from each other)
COLOR_DAY_AVG = "#7ec8e3"      # light blue (matches day bars)
COLOR_NIGHT_AVG = "#d4a0f0"    # light purple (matches night bars)

# Unified warm average line colour for compact chart
COLOR_AVG_WARM = "#FFB347"     # orange-yellow — high contrast on dark bg

# Legacy alias
COLOR_AVG_LINE = "#ffd600"     # amber — used by stats dialog

# Hardness palette  (1=comfortable green → 5=harsh red)
HARDNESS_COLORS = {
    1: "#4caf50",   # green
    2: "#8bc34a",   # light green
    3: "#ffc107",   # amber
    4: "#ff9800",   # orange
    5: "#f44336",   # red
}

# ── App-theme palettes (for non-chart UI elements: QLabels, HTML text) ────────
# These mirror src/ui/stylesheet.py tokens.

_APP_PALETTES = {
    "dark": {
        "bg_primary":     "#3b3428",
        "bg_secondary":   "#443d30",
        "text_primary":   "#e8dfd4",
        "text_secondary": "#c4b8a8",
        "text_dim":       "#9a8e7e",
        "green_bright":   "#00e676",
        "green_mid":      "#4caf50",
        "amber":          "#ffd600",
        "red":            "#ff1744",
        "diff_over":      "#ff8a65",
        "diff_under":     "#66bb6a",
    },
    "light": {
        "bg_primary":     "#eaeeea",
        "bg_secondary":   "#dde4dd",
        "text_primary":   "#1b2b1b",
        "text_secondary": "#264c26",
        "text_dim":       "#4e7040",
        "green_bright":   "#205522",
        "green_mid":      "#2e7d32",
        "amber":          "#f57f17",
        "red":            "#c62828",
        "diff_over":      "#d84315",
        "diff_under":     "#2e7d32",
    },
}


def get_palette(theme: str = "dark") -> dict[str, str]:
    """Return the app-theme colour palette for non-chart UI elements."""
    return _APP_PALETTES.get(theme, _APP_PALETTES["dark"])


def current_theme(db) -> str:
    """Read the current theme from UnitConfig."""
    from src.services.config_service import ConfigService
    return ConfigService(db).current_theme()


# ── Backward-compatible module-level aliases (dark defaults) ──────────────────

_d = _APP_PALETTES["dark"]
BG_PRIMARY = _d["bg_primary"]
BG_SECONDARY = _d["bg_secondary"]
TEXT_PRIMARY = _d["text_primary"]
TEXT_SECONDARY = _d["text_secondary"]
TEXT_DIM = _d["text_dim"]
GRID_COLOR = CHART_GRID
BORDER_COLOR = CHART_BORDER
GREEN_BRIGHT = _d["green_bright"]
GREEN_MID = _d["green_mid"]
AMBER = _d["amber"]
RED = _d["red"]

# Legacy alias kept for imports that haven't been updated
_MUTED_ALPHA = MUTED_ALPHA


# ── Styling functions (always dark chart bg) ──────────────────────────────────

def apply_style(fig: Figure, theme: str = "dark") -> None:
    """Apply the dark chart style to a figure and all its axes.

    The *theme* parameter is accepted for API compatibility but ignored —
    charts always use the dark background.
    """
    fig.patch.set_facecolor(CHART_BG)
    fig.patch.set_alpha(1.0)
    for ax in fig.get_axes():
        style_ax(ax)


def style_ax(ax, theme: str = "dark") -> None:
    """Style a single Axes with the dark chart look."""
    ax.set_facecolor(CHART_BG_ALT)
    ax.tick_params(colors=CHART_TEXT_SEC, labelsize=9)
    ax.xaxis.label.set_color(CHART_TEXT_SEC)
    ax.yaxis.label.set_color(CHART_TEXT_SEC)
    ax.title.set_color(CHART_TEXT)
    for spine in ax.spines.values():
        spine.set_color(CHART_BORDER)
    ax.grid(axis="y", color=CHART_GRID, linewidth=0.5, alpha=0.6)
    ax.set_axisbelow(True)


def create_figure(width: float = 4.0, height: float = 2.5,
                  dpi: int = 100, theme: str = "dark") -> Figure:
    """Create a pre-styled matplotlib Figure (no pyplot — safe for Qt)."""
    fig = Figure(figsize=(width, height), dpi=dpi, tight_layout=True)
    fig.patch.set_facecolor(CHART_BG)
    fig.patch.set_alpha(1.0)
    return fig
