"""主题管理器 —— 支持浅色/深色主题切换与持久化"""

from PySide6.QtCore import QSettings


class ThemeManager:
    """主题管理器 —— 维护浅色/深色两套配色方案，通过 QSettings 持久化用户选择。"""

    THEMES = {
        "light": {
            "name": "浅色",
            "bg_color": "#ffffff",
            "text_color": "#333333",
            "secondary_bg": "#f4f4f4",
            "border_color": "#dddddd",
            "accent_color": "#3498db",
            "code_bg": "#f8f8f8",
            "blockquote_bg": "#f9f9f9",
            "blockquote_border": "#3498db",
            "danger_color": "#dc3545",
            "dock_close_hover": "#e81123",
            "muted_color": "#888888",
        },
        "dark": {
            "name": "深色",
            "bg_color": "#1e1e1e",
            "text_color": "#d4d4d4",
            "secondary_bg": "#2d2d2d",
            "border_color": "#404040",
            "accent_color": "#4a9eff",
            "code_bg": "#252526",
            "blockquote_bg": "#2d2d2d",
            "blockquote_border": "#4a9eff",
            "danger_color": "#dc3545",
            "dock_close_hover": "#c42b1c",
            "muted_color": "#777777",
        },
    }

    def __init__(self, settings: QSettings):
        """初始化主题管理器。

        Args:
            settings: QSettings 实例，用于持久化主题选择
        """
        self._settings = settings
        self._current_theme = self._settings.value("theme", "light")

    @property
    def current_theme(self) -> str:
        return self._current_theme

    def set_theme(self, theme: str):
        """切换主题并持久化到 QSettings。"""
        if theme in self.THEMES:
            self._current_theme = theme
            self._settings.setValue("theme", theme)

    def get_style(self) -> str:
        """获取当前主题的CSS样式"""
        theme = self.THEMES.get(self._current_theme, self.THEMES["light"])
        return f"""
            QMainWindow {{
                background-color: {theme["bg_color"]};
            }}
            QTabWidget::pane {{
                border: 1px solid {theme["border_color"]};
                background-color: {theme["bg_color"]};
            }}
            QTabBar::tab {{
                background-color: {theme["secondary_bg"]};
                color: {theme["text_color"]};
                padding: 8px 16px;
                border: 1px solid {theme["border_color"]};
                border-bottom: none;
            }}
            QTabBar::tab:selected {{
                background-color: {theme["bg_color"]};
                border-bottom: 2px solid {theme["accent_color"]};
            }}
            QTextEdit, QTextBrowser {{
                background-color: {theme["bg_color"]};
                color: {theme["text_color"]};
                border: 1px solid {theme["border_color"]};
                selection-background-color: {theme["accent_color"]};
            }}
            QLineEdit {{
                background-color: {theme["secondary_bg"]};
                color: {theme["text_color"]};
                border: 1px solid {theme["border_color"]};
                padding: 4px;
            }}
            QPushButton {{
                background-color: {theme["secondary_bg"]};
                color: {theme["text_color"]};
                border: 1px solid {theme["border_color"]};
                padding: 6px 12px;
                border-radius: 3px;
            }}
            QPushButton:hover {{
                background-color: {theme["accent_color"]};
                color: white;
            }}
            QPushButton#BookmarkDeleteBtn:hover {{
                background-color: {theme["danger_color"]};
                color: white;
            }}
            QListWidget {{
                background-color: {theme["secondary_bg"]};
                color: {theme["text_color"]};
                border: 1px solid {theme["border_color"]};
            }}
            QListWidget::item {{
                padding: 6px 8px;
                border-left: 3px solid transparent;
            }}
            QListWidget::item:selected {{
                background-color: {theme["accent_color"]};
                color: white;
                border-left: 3px solid {theme["accent_color"]};
            }}
            QDockWidget {{
                background-color: {theme["secondary_bg"]};
                color: {theme["text_color"]};
            }}
            QDockWidget::title {{
                background: {theme["secondary_bg"]};
                color: {theme["text_color"]};
                padding: 6px 30px 6px 10px;
                border-bottom: 1px solid {theme["border_color"]};
                font-weight: 600;
            }}
            QDockWidget::close-button {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 20px;
                height: 20px;
            }}
            QDockWidget::close-button:hover {{
                background-color: {theme["dock_close_hover"]};
                border-radius: 3px;
            }}
            QDockWidget::float-button {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 20px;
                height: 20px;
            }}
            QDockWidget::float-button:hover {{
                background-color: {theme["accent_color"]};
                border-radius: 3px;
            }}
            QWidget#BookmarkDockTitleBar {{
                background: {theme["secondary_bg"]};
                border-bottom: 1px solid {theme["border_color"]};
            }}
            QLabel#BookmarkCountLabel {{
                color: {theme["muted_color"]};
                font-size: 11px;
            }}
            QStatusBar {{
                background-color: {theme["secondary_bg"]};
                color: {theme["text_color"]};
            }}
            QTreeWidget {{
                background-color: {theme["secondary_bg"]};
                color: {theme["text_color"]};
                border: 1px solid {theme["border_color"]};
            }}
            QTreeWidget::item {{
                padding: 4px;
            }}
            QTreeWidget::item:selected {{
                background-color: {theme["accent_color"]};
                color: white;
            }}
            QTreeWidget::item:!selectable {{
                color: {theme["text_color"]};
                font-weight: 600;
            }}
        """

    def get_markdown_css(self, font_size: int) -> str:
        """获取Markdown渲染的CSS样式"""
        theme = self.THEMES.get(self._current_theme, self.THEMES["light"])
        return f"""
            body {{
                font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif;
                font-size: {font_size}pt;
                line-height: 1.6;
                color: {theme["text_color"]};
                padding: 10px;
                background-color: {theme["bg_color"]};
                margin: 0;
            }}
            * {{
                box-sizing: border-box;
            }}
            h1, h2, h3, h4, h5, h6 {{
                color: {theme["text_color"]};
                margin-top: 1.2em;
                margin-bottom: 0.6em;
                font-weight: 600;
            }}
            h1 {{
                font-size: 1.8em;
                border-bottom: 2px solid {theme["border_color"]};
                padding-bottom: 0.3em;
            }}
            h2 {{
                font-size: 1.5em;
                border-bottom: 1px solid {theme["border_color"]};
                padding-bottom: 0.3em;
            }}
            h3 {{
                font-size: 1.3em;
            }}
            code {{
                background: {theme["code_bg"]};
                padding: 2px 6px;
                border-radius: 3px;
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 0.9em;
                color: {theme["text_color"]};
            }}
            pre {{
                background: {theme["code_bg"]};
                padding: 12px;
                border-radius: 5px;
                overflow-x: auto;
                border: 1px solid {theme["border_color"]};
            }}
            pre code {{
                background: none;
                padding: 0;
            }}
            blockquote {{
                border-left: 4px solid {theme["blockquote_border"]};
                padding-left: 12px;
                margin: 1em 0;
                color: {theme["text_color"]};
                background: {theme["blockquote_bg"]};
                padding: 8px 12px;
            }}
            table {{
                border-collapse: collapse;
                width: 100%;
                margin: 1em 0;
            }}
            th, td {{
                border: 1px solid {theme["border_color"]};
                padding: 8px 12px;
                text-align: left;
            }}
            th {{
                background: {theme["secondary_bg"]};
                font-weight: 600;
            }}
            ul, ol {{
                padding-left: 2em;
                margin: 0.8em 0;
            }}
            ul {{
                list-style-type: disc;
            }}
            ol {{
                list-style-type: decimal;
            }}
            li {{
                margin: 0.5em 0;
                line-height: 1.8;
                padding-left: 0.5em;
            }}
            ul ul, ol ol, ul ol, ol ul {{
                margin-top: 0.4em;
                margin-bottom: 0.4em;
                padding-left: 2em;
            }}
            ul ul {{
                list-style-type: circle;
            }}
            ul ul ul {{
                list-style-type: square;
            }}
            ul li, ol li {{
                margin-left: 0;
            }}
            p {{
                margin: 0.6em 0;
                line-height: 1.6;
            }}
            li > p {{
                margin: 0.3em 0;
            }}
            a {{
                color: {theme["accent_color"]};
                text-decoration: none;
            }}
            a:hover {{
                text-decoration: underline;
            }}
            .codehilite {{
                background: {theme["code_bg"]};
                padding: 12px;
                border-radius: 5px;
                overflow-x: auto;
                border: 1px solid {theme["border_color"]};
            }}
            .codehilite code {{
                background: none;
                padding: 0;
            }}
        """
