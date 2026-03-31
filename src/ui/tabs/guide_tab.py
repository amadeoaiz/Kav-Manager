"""
Guide Tab — Static scrollable in-app guide for new commanders.

QTextBrowser rendering pre-built HTML files (assets/guide_en.html,
assets/guide_he.html) with theme CSS injected at load time.
Supports English (LTR) and Hebrew (RTL) with a toggle button.
"""
import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTextBrowser,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from src.core.paths import get_assets_dir
from src.services.config_service import ConfigService

_ASSETS = get_assets_dir()

# ── Theme-aware color palettes ───────────────────────────────────────────────

_DARK_COLORS = {
    "bg": "#3b3428",
    "bg_card": "#443d30",
    "text": "#e8dfd4",
    "text_sec": "#c4b8a8",
    "text_dim": "#9a8e7e",
    "heading": "#00e676",
    "link": "#4caf50",
    "link_hover": "#00e676",
    "border": "#4d4538",
    "separator": "#4d4538",
    "code_bg": "#332d22",
    "code_text": "#e8dfd4",
    "tip_bg": "#2e3828",
    "tip_border": "#4caf50",
}

_LIGHT_COLORS = {
    "bg": "#eaeeea",
    "bg_card": "#dde4dd",
    "text": "#1b2b1b",
    "text_sec": "#264c26",
    "text_dim": "#4e7040",
    "heading": "#205522",
    "link": "#2e7d32",
    "link_hover": "#1b5e20",
    "border": "#a0bca0",
    "separator": "#a0bca0",
    "code_bg": "#d0d8d0",
    "code_text": "#1b2b1b",
    "tip_bg": "#d8e8d0",
    "tip_border": "#2e7d32",
}


def _build_css(c: dict, rtl: bool = False) -> str:
    tip_border_side = "right" if rtl else "left"
    margin_dir = "right" if rtl else "left"
    return f"""
    body {{
        background-color: {c['bg']};
        color: {c['text']};
        font-family: Consolas, 'Courier New', monospace;
        font-size: 14px;
        line-height: 1.6;
        padding: 24px 36px 48px 36px;
        max-width: 820px;
        margin: 0 auto;
    }}
    h1 {{
        color: {c['heading']};
        font-size: 22px;
        letter-spacing: 2px;
        border-bottom: 2px solid {c['heading']};
        padding-bottom: 8px;
        margin-top: 32px;
        margin-bottom: 16px;
    }}
    h2 {{
        color: {c['heading']};
        font-size: 17px;
        letter-spacing: 1px;
        margin-top: 28px;
        margin-bottom: 12px;
        padding-bottom: 4px;
        border-bottom: 1px solid {c['separator']};
    }}
    h3 {{
        color: {c['text']};
        font-size: 15px;
        margin-top: 18px;
        margin-bottom: 8px;
    }}
    p {{
        margin: 8px 0;
        color: {c['text']};
    }}
    a {{
        color: {c['link']};
        text-decoration: none;
    }}
    a:hover {{
        color: {c['link_hover']};
        text-decoration: underline;
    }}
    ul, ol {{
        margin: 6px 0 6px 20px;
        padding: 0;
        padding-{margin_dir}: 20px;
        margin-{margin_dir}: 0;
    }}
    li {{
        margin: 4px 0;
        color: {c['text']};
    }}
    code {{
        background-color: {c['code_bg']};
        color: {c['code_text']};
        padding: 2px 6px;
        font-size: 13px;
        direction: ltr;
        unicode-bidi: embed;
    }}
    .toc {{
        background-color: {c['bg_card']};
        border: 1px solid {c['border']};
        padding: 16px 24px;
        margin-bottom: 24px;
    }}
    .toc-title {{
        color: {c['heading']};
        font-size: 15px;
        font-weight: bold;
        letter-spacing: 1px;
        margin-bottom: 8px;
    }}
    .toc ol {{
        margin: 0;
        margin-{margin_dir}: 20px;
        padding: 0;
    }}
    .toc li {{
        margin: 4px 0;
        color: {c['text_sec']};
    }}
    .separator {{
        border: none;
        border-top: 1px solid {c['separator']};
        margin: 24px 0;
    }}
    .dim {{
        color: {c['text_dim']};
        font-size: 13px;
    }}
    .tip {{
        background-color: {c['tip_bg']};
        border-{tip_border_side}: 3px solid {c['tip_border']};
        padding: 10px 14px;
        margin: 12px 0;
    }}
    .tip-label {{
        color: {c['tip_border']};
        font-weight: bold;
        font-size: 13px;
    }}
    """


def _load_guide(lang: str, colors: dict) -> str:
    """Read the HTML file for *lang* and inject theme CSS."""
    path = os.path.join(_ASSETS, f"guide_{lang}.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    rtl = lang == "he"
    css = _build_css(colors, rtl)
    html = html.replace(
        "/* Injected at runtime by KavManager */",
        css,
    )
    return html


# ── Tab widget ───────────────────────────────────────────────────────────────

class GuideTab(QWidget):
    def __init__(self, db, main_window):
        super().__init__()
        self.db = db
        self.mw = main_window
        self._config_svc = ConfigService(db)
        self._lang = "en"
        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Language toggle bar
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 6, 12, 6)
        bar.addStretch()
        self._lang_btn = QPushButton()
        self._lang_btn.setFixedWidth(100)
        self._lang_btn.clicked.connect(self._toggle_lang)
        self._update_lang_btn()
        bar.addWidget(self._lang_btn)
        layout.addLayout(bar)

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(False)
        self._browser.setOpenLinks(False)
        self._browser.anchorClicked.connect(self._on_anchor_clicked)
        self._browser.setFont(QFont("Consolas", 13))
        layout.addWidget(self._browser)

    def _update_lang_btn(self):
        if self._lang == "en":
            self._lang_btn.setText("\u05e2\u05d1\u05e8\u05d9\u05ea")
        else:
            self._lang_btn.setText("English")

    def _toggle_lang(self):
        self._lang = "he" if self._lang == "en" else "en"
        self._update_lang_btn()
        self._render()

    def _on_anchor_clicked(self, url):
        """Handle internal anchor links -- scroll to the named section."""
        fragment = url.fragment() if hasattr(url, 'fragment') else url.toString().lstrip('#')
        if fragment:
            self._browser.scrollToAnchor(fragment)

    def _render(self):
        config = self._config_svc.get_config()
        theme = config.theme if config else 'dark'
        colors = _DARK_COLORS if theme == 'dark' else _LIGHT_COLORS
        rtl = self._lang == "he"
        direction = (Qt.LayoutDirection.RightToLeft if rtl
                     else Qt.LayoutDirection.LeftToRight)
        self._browser.setLayoutDirection(direction)
        html = _load_guide(self._lang, colors)
        self._browser.setHtml(html)

    def refresh(self):
        vbar = self._browser.verticalScrollBar()
        pos = vbar.value() if vbar else 0
        self._render()
        if pos:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: vbar.setValue(pos))
