"""独立结果查看窗口 —— 支持全屏、Markdown、多标签、搜索、导出、书签、主题切换"""

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QSettings, Signal
from PySide6.QtGui import QAction, QFont, QKeySequence, QTextCursor, QTextDocument
from PySide6.QtPrintSupport import QPrinter, QPrintDialog
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

try:
    import markdown

    MARKDOWN_AVAILABLE = True
except ImportError:
    MARKDOWN_AVAILABLE = False

try:
    from pygments import highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import get_lexer_by_name, guess_lexer

    PYGMENTS_AVAILABLE = True
except ImportError:
    PYGMENTS_AVAILABLE = False

logger = logging.getLogger(__name__)


class ThemeManager:
    """主题管理器"""

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
        },
    }

    def __init__(self):
        self._settings = QSettings("Video2Text", "ResultViewer")
        self._current_theme = self._settings.value("theme", "light")

    @property
    def current_theme(self) -> str:
        return self._current_theme

    def set_theme(self, theme: str):
        if theme in self.THEMES:
            self._current_theme = theme
            self._settings.setValue("theme", theme)

    def get_style(self) -> str:
        """获取当前主题的CSS样式"""
        theme = self.THEMES[self._current_theme]
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
            QListWidget {{
                background-color: {theme["secondary_bg"]};
                color: {theme["text_color"]};
                border: 1px solid {theme["border_color"]};
            }}
            QListWidget::item {{
                padding: 4px;
            }}
            QListWidget::item:selected {{
                background-color: {theme["accent_color"]};
                color: white;
            }}
            QDockWidget {{
                background-color: {theme["secondary_bg"]};
                color: {theme["text_color"]};
            }}
            QStatusBar {{
                background-color: {theme["secondary_bg"]};
                color: {theme["text_color"]};
            }}
        """

    def get_markdown_css(self, font_size: int) -> str:
        """获取Markdown渲染的CSS样式"""
        theme = self.THEMES[self._current_theme]
        return f"""
            body {{
                font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif;
                font-size: {font_size}pt;
                line-height: 1.6;
                color: {theme["text_color"]};
                padding: 10px;
                background-color: {theme["bg_color"]};
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
            }}
            li {{
                margin: 0.3em 0;
            }}
            a {{
                color: {theme["accent_color"]};
                text-decoration: none;
            }}
            a:hover {{
                text-decoration: underline;
            }}
            .highlight {{
                background-color: {theme["accent_color"]};
                color: white;
                padding: 1px 2px;
            }}
        """


class BookmarkItem:
    """书签项"""

    def __init__(self, video_name: str, content_type: str, position: int, text: str):
        self.video_name = video_name
        self.content_type = content_type  # 'transcript' or 'summary'
        self.position = position
        self.text = text[:100]  # 保存前100个字符作为预览


class ResultViewerWindow(QMainWindow):
    """独立的结果查看窗口，支持全屏显示、多标签、搜索、导出、书签、主题切换"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("结果查看 - Video2Text")
        self.resize(1400, 900)

        self._theme_manager = ThemeManager()
        self._output_dir = ""
        self._bookmarks: list[BookmarkItem] = []
        self._current_video_name: Optional[str] = None

        self._init_ui()
        self._apply_theme()
        self._load_bookmarks()

    def _init_ui(self):
        """初始化UI布局"""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        # 工具栏
        self._create_toolbar()

        # 主分割器
        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：文件列表
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(5, 5, 5, 5)

        left_layout.addWidget(QLabel("文件列表:"))
        self.file_list = QListWidget()
        self.file_list.currentItemChanged.connect(self._on_file_selected)
        left_layout.addWidget(self.file_list)

        main_splitter.addWidget(left_panel)

        # 右侧：内容查看
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(5, 5, 5, 5)

        # 搜索栏
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("搜索:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入关键词搜索...")
        self.search_edit.textChanged.connect(self._on_search_text_changed)
        search_layout.addWidget(self.search_edit, 1)

        self.search_prev_btn = QPushButton("上一个")
        self.search_prev_btn.clicked.connect(self._search_prev)
        search_layout.addWidget(self.search_prev_btn)

        self.search_next_btn = QPushButton("下一个")
        self.search_next_btn.clicked.connect(self._search_next)
        search_layout.addWidget(self.search_next_btn)

        self.search_count_label = QLabel("0/0")
        search_layout.addWidget(self.search_count_label)

        right_layout.addLayout(search_layout)

        # 标签页
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)

        # 转写文本标签页
        self.transcript_view = QTextEdit()
        self.transcript_view.setFont(QFont("Consolas", 11))
        self.transcript_view.setPlaceholderText("转写文本将显示在此处")
        self.tabs.addTab(self.transcript_view, "转写文本")

        # 摘要标签页（支持Markdown）
        self.summary_view = QTextBrowser()
        self.summary_view.setOpenExternalLinks(True)
        self.summary_view.setPlaceholderText("摘要将显示在此处")
        self.tabs.addTab(self.summary_view, "摘要")

        right_layout.addWidget(self.tabs)

        main_splitter.addWidget(right_panel)

        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 4)
        layout.addWidget(main_splitter)

        # 状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        # 书签停靠窗口
        self._create_bookmark_dock()

    def _create_toolbar(self):
        """创建工具栏"""
        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # 文件信息
        self.file_label = QLabel("当前文件: -")
        toolbar.addWidget(self.file_label)

        toolbar.addSeparator()

        # 字体控制
        toolbar.addWidget(QLabel("字体:"))
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 32)
        self.font_size_spin.setValue(11)
        self.font_size_spin.setSuffix(" pt")
        self.font_size_spin.valueChanged.connect(self._update_font_size)
        toolbar.addWidget(self.font_size_spin)

        toolbar.addSeparator()

        # 主题切换
        toolbar.addWidget(QLabel("主题:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["浅色", "深色"])
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        toolbar.addWidget(self.theme_combo)

        toolbar.addSeparator()

        # 全屏按钮
        fullscreen_action = QAction("全屏", self)
        fullscreen_action.setShortcut(QKeySequence("F11"))
        fullscreen_action.triggered.connect(self._toggle_fullscreen)
        toolbar.addAction(fullscreen_action)

        # 导出按钮（带下拉菜单）
        export_action = QAction("导出", self)
        export_menu = QMenu(self)

        export_html_action = QAction("导出为HTML", self)
        export_html_action.triggered.connect(self._export_html)
        export_menu.addAction(export_html_action)

        export_pdf_action = QAction("导出为PDF", self)
        export_pdf_action.triggered.connect(self._export_pdf)
        export_menu.addAction(export_pdf_action)

        export_txt_action = QAction("导出为TXT", self)
        export_txt_action.triggered.connect(self._export_txt)
        export_menu.addAction(export_txt_action)

        export_action.setMenu(export_menu)
        toolbar.addAction(export_action)

        toolbar.addSeparator()

        # 书签按钮
        add_bookmark_action = QAction("添加书签", self)
        add_bookmark_action.setShortcut(QKeySequence("Ctrl+B"))
        add_bookmark_action.triggered.connect(self._add_bookmark)
        toolbar.addAction(add_bookmark_action)

        toggle_bookmark_action = QAction("书签面板", self)
        toggle_bookmark_action.setShortcut(QKeySequence("Ctrl+Shift+B"))
        toggle_bookmark_action.triggered.connect(self._toggle_bookmark_dock)
        toolbar.addAction(toggle_bookmark_action)

        toolbar.addSeparator()

        # 关闭按钮
        close_action = QAction("关闭", self)
        close_action.setShortcut(QKeySequence("Ctrl+W"))
        close_action.triggered.connect(self.close)
        toolbar.addAction(close_action)

    def _create_bookmark_dock(self):
        """创建书签停靠窗口"""
        self.bookmark_dock = QDockWidget("书签", self)
        self.bookmark_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )

        bookmark_widget = QWidget()
        bookmark_layout = QVBoxLayout(bookmark_widget)

        self.bookmark_list = QListWidget()
        self.bookmark_list.itemDoubleClicked.connect(self._on_bookmark_double_clicked)
        bookmark_layout.addWidget(self.bookmark_list)

        bookmark_btn_layout = QHBoxLayout()

        delete_bookmark_btn = QPushButton("删除")
        delete_bookmark_btn.clicked.connect(self._delete_bookmark)
        bookmark_btn_layout.addWidget(delete_bookmark_btn)

        clear_bookmarks_btn = QPushButton("清空")
        clear_bookmarks_btn.clicked.connect(self._clear_bookmarks)
        bookmark_btn_layout.addWidget(clear_bookmarks_btn)

        bookmark_layout.addLayout(bookmark_btn_layout)

        self.bookmark_dock.setWidget(bookmark_widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.bookmark_dock)
        self.bookmark_dock.hide()

    def _apply_theme(self):
        """应用主题"""
        self.setStyleSheet(self._theme_manager.get_style())
        theme_index = 0 if self._theme_manager.current_theme == "light" else 1
        self.theme_combo.setCurrentIndex(theme_index)

    def _on_theme_changed(self, index: int):
        """主题切换"""
        theme = "dark" if index == 1 else "light"
        self._theme_manager.set_theme(theme)
        self._apply_theme()

        # 重新渲染Markdown内容
        if self._current_video_name:
            summary_path = (
                Path(self._output_dir) / f"{self._current_video_name}_summary.txt"
            )
            if summary_path.exists():
                try:
                    summary_text = summary_path.read_text(encoding="utf-8")
                    self._display_markdown(summary_text)
                except Exception:
                    pass

    def load_files(self, video_names: list[str], output_dir: str):
        """加载多个视频文件"""
        self._output_dir = output_dir
        self.file_list.clear()

        for video_name in video_names:
            item = QListWidgetItem(video_name)
            item.setData(Qt.ItemDataRole.UserRole, video_name)
            self.file_list.addItem(item)

        if video_names:
            self.file_list.setCurrentRow(0)

    def load_content(self, video_name: str, output_dir: str):
        """加载指定视频的转写和摘要内容"""
        self._current_video_name = video_name
        self._output_dir = output_dir
        self.file_label.setText(f"当前文件: {video_name}")

        # 加载转写文本
        transcript_path = Path(output_dir) / f"{video_name}.txt"
        if transcript_path.exists():
            try:
                self.transcript_view.setPlainText(
                    transcript_path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                self.transcript_view.setPlainText(f"读取失败: {exc}")
        else:
            self.transcript_view.setPlainText("(未找到转写文件)")

        # 加载摘要（Markdown渲染）
        summary_path = Path(output_dir) / f"{video_name}_summary.txt"
        if summary_path.exists():
            try:
                summary_text = summary_path.read_text(encoding="utf-8")
                self._display_markdown(summary_text)
            except Exception as exc:
                self.summary_view.setPlainText(f"读取失败: {exc}")
        else:
            self.summary_view.setPlainText("(未找到摘要文件)")

        self.status_bar.showMessage(f"已加载: {video_name}")

    def _display_markdown(self, markdown_text: str):
        """渲染Markdown内容"""
        if not MARKDOWN_AVAILABLE:
            self.summary_view.setPlainText(markdown_text)
            return

        extensions = ["tables", "fenced_code", "nl2br"]
        if PYGMENTS_AVAILABLE:
            extensions.append("codehilite")

        html = markdown.markdown(markdown_text, extensions=extensions)

        # 获取当前字体大小
        font_size = self.font_size_spin.value()

        # 添加CSS样式
        styled_html = f"""
        <style>
            {self._theme_manager.get_markdown_css(font_size)}
        </style>
        {html}
        """
        self.summary_view.setHtml(styled_html)

    def _on_file_selected(self, current: QListWidgetItem, previous: QListWidgetItem):
        """文件选择事件"""
        if current is None:
            return

        video_name = current.data(Qt.ItemDataRole.UserRole)
        self.load_content(video_name, self._output_dir)

    def _update_font_size(self, size: int):
        """更新字体大小"""
        # 更新转写文本字体
        font = QFont("Consolas", size)
        self.transcript_view.setFont(font)

        # 重新渲染摘要（应用新字体大小）
        if self._current_video_name:
            summary_path = (
                Path(self._output_dir) / f"{self._current_video_name}_summary.txt"
            )
            if summary_path.exists():
                try:
                    summary_text = summary_path.read_text(encoding="utf-8")
                    self._display_markdown(summary_text)
                except Exception:
                    pass

    def _toggle_fullscreen(self):
        """切换全屏模式"""
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _on_search_text_changed(self, text: str):
        """搜索文本变化"""
        if not text:
            self.search_count_label.setText("0/0")
            return

        current_view = self.tabs.currentWidget()
        if isinstance(current_view, (QTextEdit, QTextBrowser)):
            document = current_view.document()
            cursor = document.find(text)
            if cursor:
                current_view.setTextCursor(cursor)
                self._update_search_count(text, document)

    def _search_next(self):
        """搜索下一个"""
        text = self.search_edit.text()
        if not text:
            return

        current_view = self.tabs.currentWidget()
        if isinstance(current_view, (QTextEdit, QTextBrowser)):
            cursor = current_view.textCursor()
            cursor = current_view.document().find(text, cursor)
            if cursor.isNull():
                # 从头开始搜索
                cursor = current_view.document().find(text, 0)
            if not cursor.isNull():
                current_view.setTextCursor(cursor)

    def _search_prev(self):
        """搜索上一个"""
        text = self.search_edit.text()
        if not text:
            return

        current_view = self.tabs.currentWidget()
        if isinstance(current_view, (QTextEdit, QTextBrowser)):
            cursor = current_view.textCursor()
            cursor = current_view.document().find(
                text, cursor, QTextDocument.FindFlag.FindBackward
            )
            if cursor.isNull():
                # 从末尾开始搜索
                cursor = current_view.document().find(
                    text, -1, QTextDocument.FindFlag.FindBackward
                )
            if not cursor.isNull():
                current_view.setTextCursor(cursor)

    def _update_search_count(self, text: str, document: QTextDocument):
        """更新搜索计数"""
        count = 0
        cursor = QTextCursor(document)
        while not cursor.isNull():
            cursor = document.find(text, cursor)
            if not cursor.isNull():
                count += 1
        self.search_count_label.setText(f"0/{count}")

    def _export_html(self):
        """导出为HTML"""
        QMessageBox.information(self, "提示", "该功能暂未实现")

    def _export_pdf(self):
        """导出为PDF"""
        QMessageBox.information(self, "提示", "该功能暂未实现")

    def _export_txt(self):
        """导出为TXT"""
        QMessageBox.information(self, "提示", "该功能暂未实现")

    def _add_bookmark(self):
        """添加书签"""
        if not self._current_video_name:
            QMessageBox.warning(self, "提示", "请先选择一个文件")
            return

        current_view = self.tabs.currentWidget()
        if not isinstance(current_view, (QTextEdit, QTextBrowser)):
            return

        cursor = current_view.textCursor()
        position = cursor.position()
        text = current_view.toPlainText()

        content_type = (
            "transcript" if current_view == self.transcript_view else "summary"
        )

        bookmark = BookmarkItem(self._current_video_name, content_type, position, text)
        self._bookmarks.append(bookmark)

        self._refresh_bookmark_list()
        self._save_bookmarks()
        self.status_bar.showMessage("书签已添加")

    def _delete_bookmark(self):
        """删除书签"""
        current_item = self.bookmark_list.currentItem()
        if current_item is None:
            return

        index = self.bookmark_list.row(current_item)
        if 0 <= index < len(self._bookmarks):
            del self._bookmarks[index]
            self._refresh_bookmark_list()
            self._save_bookmarks()
            self.status_bar.showMessage("书签已删除")

    def _clear_bookmarks(self):
        """清空书签"""
        reply = QMessageBox.question(
            self,
            "确认",
            "确定要清空所有书签吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._bookmarks.clear()
            self._refresh_bookmark_list()
            self._save_bookmarks()
            self.status_bar.showMessage("书签已清空")

    def _refresh_bookmark_list(self):
        """刷新书签列表"""
        self.bookmark_list.clear()
        for i, bookmark in enumerate(self._bookmarks):
            item = QListWidgetItem(
                f"{i + 1}. {bookmark.video_name} - {bookmark.content_type}"
            )
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setToolTip(bookmark.text)
            self.bookmark_list.addItem(item)

    def _on_bookmark_double_clicked(self, item: QListWidgetItem):
        """书签双击事件"""
        index = item.data(Qt.ItemDataRole.UserRole)
        if 0 <= index < len(self._bookmarks):
            bookmark = self._bookmarks[index]

            # 切换到对应的文件
            for i in range(self.file_list.count()):
                list_item = self.file_list.item(i)
                if list_item.data(Qt.ItemDataRole.UserRole) == bookmark.video_name:
                    self.file_list.setCurrentItem(list_item)
                    break

            # 切换到对应的标签页
            if bookmark.content_type == "transcript":
                self.tabs.setCurrentWidget(self.transcript_view)
            else:
                self.tabs.setCurrentWidget(self.summary_view)

            # 跳转到书签位置
            current_view = self.tabs.currentWidget()
            if isinstance(current_view, (QTextEdit, QTextBrowser)):
                cursor = QTextCursor(current_view.document())
                cursor.setPosition(bookmark.position)
                current_view.setTextCursor(cursor)
                current_view.setFocus()

    def _toggle_bookmark_dock(self):
        """切换书签面板显示"""
        if self.bookmark_dock.isVisible():
            self.bookmark_dock.hide()
        else:
            self.bookmark_dock.show()

    def _load_bookmarks(self):
        """加载书签"""
        settings = QSettings("Video2Text", "ResultViewer")
        bookmark_data = settings.value("bookmarks", [], list)
        self._bookmarks = []
        for data in bookmark_data:
            if isinstance(data, dict):
                bookmark = BookmarkItem(
                    video_name=data.get("video_name", ""),
                    content_type=data.get("content_type", "transcript"),
                    position=data.get("position", 0),
                    text=data.get("text", ""),
                )
                self._bookmarks.append(bookmark)
        self._refresh_bookmark_list()

    def _save_bookmarks(self):
        """保存书签"""
        settings = QSettings("Video2Text", "ResultViewer")
        bookmark_data = []
        for bookmark in self._bookmarks:
            bookmark_data.append(
                {
                    "video_name": bookmark.video_name,
                    "content_type": bookmark.content_type,
                    "position": bookmark.position,
                    "text": bookmark.text,
                }
            )
        settings.setValue("bookmarks", bookmark_data)

    def _close_tab(self, index: int):
        """关闭标签页"""
        if self.tabs.count() <= 1:
            return
        self.tabs.removeTab(index)

    def keyPressEvent(self, event):
        """处理键盘快捷键"""
        if event.key() == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
        super().keyPressEvent(event)
