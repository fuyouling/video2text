"""独立结果查看窗口 —— 支持全屏、Markdown、多标签、搜索、书签、主题切换"""

import logging
import re
from pathlib import Path
from typing import Optional, Union

from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QFont,
    QKeyEvent,
    QKeySequence,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

try:
    import markdown

    MARKDOWN_AVAILABLE = True
except ImportError:
    MARKDOWN_AVAILABLE = False

try:
    import importlib.util

    PYGMENTS_AVAILABLE = importlib.util.find_spec("pygments") is not None
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
                titlebar-close-icon: none;
            }}
            QDockWidget::title {{
                background: {theme["secondary_bg"]};
                color: {theme["text_color"]};
                padding: 6px 8px;
                border-bottom: 1px solid {theme["border_color"]};
                font-weight: 600;
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


class BookmarkItem:
    """书签项"""

    def __init__(self, video_name: str, content_type: str, position: int, text: str):
        self.video_name = video_name
        self.content_type = content_type  # 'transcript' or 'summary'
        self.position = position
        self.text = text[:100]  # 保存前100个字符作为预览


class ResultViewerWindow(QMainWindow):
    """独立的结果查看窗口，支持全屏显示、多标签、搜索、书签、主题切换"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("结果查看 - Video2Text")
        self.resize(1400, 900)

        self._theme_manager = ThemeManager()
        self._output_dir = ""
        self._root_output_dir = ""
        self._flat_video_names: list[str] = []
        self._bookmarks: list[BookmarkItem] = []
        self._all_video_names: list[str] = []
        self._current_video_name: Optional[str] = None
        self._search_matches: list[tuple[int, int]] = []
        self._current_match_index: int = -1
        self._folder_mode: bool = False
        self._tree_name_map: dict[str, QTreeWidgetItem] = {}
        self._cached_md_text: str = ""
        self._cached_html: str = ""
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._do_search)

        self._init_ui()
        self._apply_theme()
        self._load_bookmarks()

        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._restore_window_state()

    # ─── UI 初始化 ─────────────────────────────────────────────

    def _init_ui(self) -> None:
        """初始化UI布局"""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        # 工具栏
        self._create_toolbar()

        # 主分割器
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：文件列表
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(5, 5, 5, 5)

        left_layout.addWidget(QLabel("文件列表:"))

        # 文件过滤输入框
        self._file_filter = QLineEdit()
        self._file_filter.setPlaceholderText("过滤文件名...")
        self._file_filter.textChanged.connect(self._filter_file_list)
        left_layout.addWidget(self._file_filter)

        self.file_list = QListWidget()
        self.file_list.currentItemChanged.connect(self._on_file_selected)
        left_layout.addWidget(self.file_list)

        # 文件夹模式树形视图
        self._folder_tree = QTreeWidget()
        self._folder_tree.setHeaderHidden(True)
        self._folder_tree.setAnimated(True)
        self._folder_tree.currentItemChanged.connect(self._on_folder_item_changed)
        self._folder_tree.setVisible(False)
        left_layout.addWidget(self._folder_tree)

        self._main_splitter.addWidget(left_panel)

        # 右侧：内容查看
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(5, 5, 5, 5)

        # 搜索栏
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("搜索:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入关键词搜索... (Ctrl+F)")
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
        self.tabs.setTabsClosable(False)

        # 转写文本标签页
        self.transcript_view = QTextEdit()
        self.transcript_view.setFont(QFont("Consolas", 14))
        self.transcript_view.setPlaceholderText("转写文本将显示在此处")
        self.tabs.addTab(self.transcript_view, "转写文本")

        # 摘要标签页（支持Markdown）
        self.summary_view = QTextBrowser()
        self.summary_view.setOpenExternalLinks(True)
        self.summary_view.setPlaceholderText("摘要将显示在此处")
        self.tabs.addTab(self.summary_view, "摘要")

        right_layout.addWidget(self.tabs)

        self._main_splitter.addWidget(right_panel)

        self._main_splitter.setStretchFactor(0, 1)
        self._main_splitter.setStretchFactor(1, 4)
        layout.addWidget(self._main_splitter)

        # 状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        # 书签停靠窗口
        self._create_bookmark_dock()

    def _create_toolbar(self):
        """创建工具栏"""
        toolbar = QToolBar("主工具栏")
        toolbar.setObjectName("MainToolBar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addSeparator()

        # 字体控制
        toolbar.addWidget(QLabel("字体:"))
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 32)
        self.font_size_spin.setValue(14)
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

        # 文件夹模式按钮
        self._folder_mode_action = QAction("文件夹模式", self)
        self._folder_mode_action.setShortcut(QKeySequence("Ctrl+D"))
        self._folder_mode_action.setCheckable(True)
        self._folder_mode_action.setChecked(False)
        self._folder_mode_action.triggered.connect(self._toggle_folder_mode)
        toolbar.addAction(self._folder_mode_action)

        toolbar.addSeparator()

        # 关闭按钮
        close_action = QAction("关闭", self)
        close_action.setShortcut(QKeySequence("Ctrl+W"))
        close_action.triggered.connect(self.close)
        toolbar.addAction(close_action)

    def _create_bookmark_dock(self):
        """创建书签停靠窗口"""
        self.bookmark_dock = QDockWidget("书签", self)
        self.bookmark_dock.setObjectName("BookmarkDock")
        self.bookmark_dock.setMinimumWidth(220)
        self.bookmark_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )

        bookmark_widget = QWidget()
        bookmark_layout = QVBoxLayout(bookmark_widget)
        bookmark_layout.setContentsMargins(6, 6, 6, 6)
        bookmark_layout.setSpacing(4)

        # 顶部：计数标签
        self._bookmark_count_label = QLabel("共 0 个书签")
        self._bookmark_count_label.setStyleSheet("color: #888; font-size: 11px;")
        bookmark_layout.addWidget(self._bookmark_count_label)

        # 过滤输入框
        self._bookmark_filter = QLineEdit()
        self._bookmark_filter.setPlaceholderText("过滤书签...")
        self._bookmark_filter.textChanged.connect(self._filter_bookmarks)
        bookmark_layout.addWidget(self._bookmark_filter)

        # 书签列表
        self.bookmark_list = QListWidget()
        self.bookmark_list.setSpacing(2)
        self.bookmark_list.itemDoubleClicked.connect(self._on_bookmark_double_clicked)
        bookmark_layout.addWidget(self.bookmark_list)

        # 空状态提示
        self._bookmark_empty_label = QLabel("暂无书签\n双击可跳转到书签位置")
        self._bookmark_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bookmark_empty_label.setStyleSheet("color: #aaa; font-size: 12px;")
        self._bookmark_empty_label.setVisible(True)
        bookmark_layout.addWidget(self._bookmark_empty_label)

        # 按钮栏
        bookmark_btn_layout = QHBoxLayout()
        bookmark_btn_layout.setSpacing(4)

        delete_bookmark_btn = QPushButton("删除")
        delete_bookmark_btn.setToolTip("删除选中书签")
        delete_bookmark_btn.clicked.connect(self._delete_bookmark)
        bookmark_btn_layout.addWidget(delete_bookmark_btn)

        clear_bookmarks_btn = QPushButton("清空")
        clear_bookmarks_btn.setToolTip("清空所有书签")
        clear_bookmarks_btn.clicked.connect(self._clear_bookmarks)
        bookmark_btn_layout.addWidget(clear_bookmarks_btn)

        bookmark_layout.addLayout(bookmark_btn_layout)

        self.bookmark_dock.setWidget(bookmark_widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.bookmark_dock)
        self.bookmark_dock.hide()

    # ─── 主题 ──────────────────────────────────────────────────

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

        # 重新渲染Markdown内容（清除搜索状态）
        if self._current_video_name:
            self._clear_search_state()
            summary_path = (
                Path(self._output_dir) / f"{self._current_video_name}_summary.txt"
            )
            if summary_path.exists():
                try:
                    summary_text = summary_path.read_text(encoding="utf-8")
                    self._display_markdown(summary_text)
                except Exception:
                    logger.warning("重新渲染摘要失败（主题切换）: %s", summary_path)

    # ─── 文件加载与过滤 ────────────────────────────────────────

    def load_files(self, video_names: list[str], output_dir: str):
        """加载多个视频文件"""
        self._output_dir = output_dir
        self._root_output_dir = output_dir
        self._flat_video_names = sorted(video_names, key=lambda x: x.lower())
        self._all_video_names = list(self._flat_video_names)
        self._file_filter.clear()
        self._populate_file_list(self._all_video_names)

        if self._folder_mode:
            self._scan_and_build_tree()
            self._folder_tree.setFocus()
        else:
            self.file_list.setFocus()

    def _populate_file_list(self, names: list[str]):
        """填充文件列表（blockSignals 防止逐项触发选择事件）"""
        self.file_list.blockSignals(True)
        self.file_list.clear()
        for video_name in names:
            item = QListWidgetItem(video_name)
            item.setData(Qt.ItemDataRole.UserRole, video_name)
            self.file_list.addItem(item)
        self.file_list.blockSignals(False)
        if names:
            self.file_list.setCurrentRow(0)

    def _filter_file_list(self, text: str):
        """根据输入文本过滤文件列表，保持当前选中项"""
        if not text:
            filtered = self._all_video_names
        else:
            text_lower = text.lower()
            filtered = [n for n in self._all_video_names if text_lower in n.lower()]

        current_name = self._current_video_name

        self.file_list.blockSignals(True)
        self.file_list.clear()
        restore_item = None
        for video_name in filtered:
            item = QListWidgetItem(video_name)
            item.setData(Qt.ItemDataRole.UserRole, video_name)
            self.file_list.addItem(item)
            if video_name == current_name:
                restore_item = item
        self.file_list.blockSignals(False)

        if restore_item is not None:
            self.file_list.setCurrentItem(restore_item)
        elif filtered:
            self.file_list.setCurrentRow(0)

    def _toggle_folder_mode(self, checked: bool):
        """切换文件夹模式"""
        self._folder_mode = checked
        self.file_list.setVisible(not checked)
        self._file_filter.setVisible(not checked)
        self._folder_tree.setVisible(checked)

        if checked and self._output_dir:
            self._scan_and_build_tree()
            self._folder_tree.setFocus()
        elif not checked:
            self._output_dir = self._root_output_dir
            self._all_video_names = list(self._flat_video_names)
            self._populate_file_list(self._all_video_names)
            if self._current_video_name:
                for i in range(self.file_list.count()):
                    item = self.file_list.item(i)
                    if item.data(Qt.ItemDataRole.UserRole) == self._current_video_name:
                        self.file_list.setCurrentItem(item)
                        break
            self.file_list.setFocus()

    def _scan_and_build_tree(self):
        """扫描输出目录下所有转写和摘要文件，构建按子目录分层的树形列表"""
        output_path = Path(self._output_dir)
        if not output_path.exists():
            return

        self._folder_tree.blockSignals(True)
        self._folder_tree.clear()
        self._tree_name_map.clear()

        root = QTreeWidgetItem([output_path.name])
        root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        f = root.font(0)
        f.setBold(True)
        root.setFont(0, f)
        self._folder_tree.addTopLevelItem(root)

        dir_nodes: dict[str, QTreeWidgetItem] = {"": root}
        video_names: set[str] = set()

        try:
            txt_files = sorted(output_path.rglob("*.txt"))
        except OSError as exc:
            logger.warning("扫描目录失败: %s", exc)
            self._folder_tree.blockSignals(False)
            return

        for txt_file in txt_files:
            if txt_file.name.endswith("_summary.txt"):
                continue
            if txt_file.name.endswith("_keywords.txt"):
                continue
            if txt_file.name.endswith("_full.json"):
                continue
            video_name = txt_file.stem
            if not video_name:
                continue
            video_names.add(video_name)

            rel = txt_file.parent.relative_to(output_path)
            parts = list(rel.parts)

            parent = root
            for depth, part in enumerate(parts):
                dir_key = "/".join(parts[: depth + 1])
                if dir_key not in dir_nodes:
                    node = QTreeWidgetItem()
                    node.setText(0, part)
                    node.setFlags(node.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                    nf = node.font(0)
                    nf.setBold(True)
                    node.setFont(0, nf)
                    parent.addChild(node)
                    dir_nodes[dir_key] = node
                parent = dir_nodes[dir_key]

            child = QTreeWidgetItem()
            child.setText(0, video_name)
            child.setData(0, Qt.ItemDataRole.UserRole, video_name)
            child.setData(0, Qt.ItemDataRole.UserRole + 1, str(txt_file.parent))
            parent.addChild(child)
            tree_key = str(txt_file.parent / video_name)
            self._tree_name_map[tree_key] = child

        self._sort_folders_first(root)
        self._update_folder_counts(root)

        root.setExpanded(True)

        self._folder_tree.blockSignals(False)

        if video_names:
            self._all_video_names = sorted(video_names, key=lambda x: x.lower())
            target: Optional[QTreeWidgetItem] = None
            for i in range(root.childCount()):
                child = root.child(i)
                if child.flags() & Qt.ItemFlag.ItemIsSelectable:
                    target = child
                    break
            if target is None:
                for i in range(root.childCount()):
                    child = root.child(i)
                    if child.childCount() > 0:
                        target = child.child(0)
                        while target and target.childCount() > 0:
                            target = target.child(0)
                        break
            if target:
                self._folder_tree.setCurrentItem(target)

    def _sort_folders_first(self, item: QTreeWidgetItem):
        """递归排序：每层文件夹排在文件前面，子文件夹默认闭合"""
        folders: list[QTreeWidgetItem] = []
        files: list[QTreeWidgetItem] = []
        for i in range(item.childCount()):
            child = item.child(i)
            if child.flags() & Qt.ItemFlag.ItemIsSelectable:
                files.append(child)
            else:
                folders.append(child)
                child.setExpanded(False)
                self._sort_folders_first(child)

        for child in folders + files:
            item.removeChild(child)
        for child in folders:
            item.addChild(child)
        for child in files:
            item.addChild(child)

    def _update_folder_counts(self, item: QTreeWidgetItem):
        """递归更新文件夹节点名称，后缀显示直接子视频数量"""
        video_count = sum(
            1
            for i in range(item.childCount())
            if item.child(i).flags() & Qt.ItemFlag.ItemIsSelectable
        )
        base_name = item.data(0, Qt.ItemDataRole.UserRole + 2) or item.text(0)
        item.setData(0, Qt.ItemDataRole.UserRole + 2, base_name)
        item.setText(0, f"{base_name} ({video_count})")
        for i in range(item.childCount()):
            child = item.child(i)
            if not (child.flags() & Qt.ItemFlag.ItemIsSelectable):
                self._update_folder_counts(child)

    def _on_folder_item_changed(
        self, current: QTreeWidgetItem, _previous: QTreeWidgetItem
    ):
        """文件夹模式：键盘上下移动或点击切换文件时加载内容"""
        if current is None:
            return
        video_name = current.data(0, Qt.ItemDataRole.UserRole)
        if video_name is None:
            return
        output_dir = current.data(0, Qt.ItemDataRole.UserRole + 1)
        if output_dir is None:
            output_dir = self._output_dir
        self.load_content(video_name, output_dir)

    def _find_tree_item_by_name(self, video_name: str) -> Optional[QTreeWidgetItem]:
        for key, item in self._tree_name_map.items():
            if Path(key).name == video_name or key == video_name:
                return item
        return None

    def load_content(self, video_name: str, output_dir: str):
        """加载指定视频的转写和摘要内容"""
        self._current_video_name = video_name
        self._output_dir = output_dir

        # 清除搜索状态（文档内容将改变，旧的位置信息失效）
        self._search_matches = []
        self._current_match_index = -1
        self.search_count_label.setText("0/0")
        self.search_edit.clear()
        self.transcript_view.setExtraSelections([])
        self.summary_view.setExtraSelections([])

        # 加载转写文本
        transcript_path = None
        for ext in ("txt", "srt", "vtt", "json"):
            candidate = Path(output_dir) / f"{video_name}.{ext}"
            if candidate.exists():
                transcript_path = candidate
                break
        if transcript_path is not None:
            try:
                self.transcript_view.setPlainText(
                    transcript_path.read_text(encoding="utf-8-sig")
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

    def _on_file_selected(
        self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem]
    ) -> None:
        """文件选择事件"""
        if current is None:
            return
        video_name = current.data(Qt.ItemDataRole.UserRole)
        if video_name == self._current_video_name:
            return
        self.load_content(video_name, self._output_dir)

    # ─── Markdown 渲染 ─────────────────────────────────────────

    def _display_markdown(self, markdown_text: str):
        """渲染Markdown内容（带缓存，仅在文本变化时重新解析）"""
        if not MARKDOWN_AVAILABLE:
            self.summary_view.setPlainText(markdown_text)
            return

        if markdown_text != self._cached_md_text:
            safe_text = re.sub(
                r"<\s*(script|style|iframe|object|embed|form|input|textarea|button)[^>]*>.*?<\s*/\s*\1\s*>",
                "",
                markdown_text,
                flags=re.DOTALL | re.IGNORECASE,
            )
            safe_text = re.sub(
                r"<\s*/?\s*(script|style|iframe|object|embed|form|input|textarea|button)[^>]*>",
                "",
                safe_text,
                flags=re.IGNORECASE,
            )

            try:
                extensions = ["tables", "fenced_code", "extra", "sane_lists"]
                if PYGMENTS_AVAILABLE:
                    extensions.append("codehilite")

                self._cached_html = markdown.markdown(safe_text, extensions=extensions)
                self._cached_md_text = markdown_text
            except Exception as exc:
                logger.warning(f"Markdown 渲染失败: {exc}")
                self.summary_view.setPlainText(markdown_text)
                return

        font_size = self.font_size_spin.value()
        css = self._theme_manager.get_markdown_css(font_size)

        default_font = QFont()
        default_font.setPointSize(font_size)
        self.summary_view.document().setDefaultFont(default_font)

        styled_html = f"""
        <style>
            {css}
        </style>
        {self._cached_html}
        """
        self.summary_view.setHtml(styled_html)

    # ─── 字体 ─────────────────────────────────────────────────

    def _update_font_size(self, size: int):
        """更新字体大小"""
        font = QFont("Consolas", size)
        self.transcript_view.setFont(font)

        # 重新渲染摘要（应用新字体大小，清除搜索状态）
        if self._current_video_name:
            self._clear_search_state()
            summary_path = (
                Path(self._output_dir) / f"{self._current_video_name}_summary.txt"
            )
            if summary_path.exists():
                try:
                    summary_text = summary_path.read_text(encoding="utf-8")
                    self._display_markdown(summary_text)
                except Exception:
                    logger.warning(f"重新渲染摘要失败: {summary_path}")

    # ─── 全屏 ─────────────────────────────────────────────────

    def _toggle_fullscreen(self):
        """切换全屏模式"""
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ─── 搜索（含全部高亮）─────────────────────────────────────

    def _clear_search_state(self):
        """清除搜索状态和高亮"""
        self._search_matches = []
        self._current_match_index = -1
        self.search_count_label.setText("0/0")
        self.search_edit.clear()
        self.transcript_view.setExtraSelections([])
        self.summary_view.setExtraSelections([])

    def _on_search_text_changed(self, text: str):
        """搜索文本变化（带防抖）"""
        if not text:
            self.search_count_label.setText("0/0")
            self._search_matches = []
            self._current_match_index = -1
            self.transcript_view.setExtraSelections([])
            self.summary_view.setExtraSelections([])
            self._search_timer.stop()
            return

        self._search_timer.start()

    def _do_search(self):
        """实际执行搜索（由防抖定时器触发）"""
        text = self.search_edit.text()
        if not text:
            return

        current_view = self.tabs.currentWidget()
        if isinstance(current_view, (QTextEdit, QTextBrowser)):
            document = current_view.document()
            self._find_all_matches(text, document)
            if self._search_matches:
                self._current_match_index = 0
                self._navigate_to_match(current_view, text)
            else:
                self.search_count_label.setText("0/0")
            self._apply_search_highlights(current_view, text)

    def _search_next(self):
        """搜索下一个"""
        text = self.search_edit.text()
        if not text or not self._search_matches:
            return

        if self._current_match_index < len(self._search_matches) - 1:
            self._current_match_index += 1
        else:
            self._current_match_index = 0

        current_view = self.tabs.currentWidget()
        if isinstance(current_view, (QTextEdit, QTextBrowser)):
            self._navigate_to_match(current_view, text)
            self._apply_search_highlights(current_view, text)

    def _search_prev(self):
        """搜索上一个"""
        text = self.search_edit.text()
        if not text or not self._search_matches:
            return

        if self._current_match_index > 0:
            self._current_match_index -= 1
        else:
            self._current_match_index = len(self._search_matches) - 1

        current_view = self.tabs.currentWidget()
        if isinstance(current_view, (QTextEdit, QTextBrowser)):
            self._navigate_to_match(current_view, text)
            self._apply_search_highlights(current_view, text)

    def _navigate_to_match(
        self, view: Union[QTextEdit, QTextBrowser], text: str
    ) -> None:
        """导航到当前匹配项并选中"""
        start, end = self._search_matches[self._current_match_index]
        cursor = QTextCursor(view.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        view.setTextCursor(cursor)
        self._update_search_count_label()

    def _apply_search_highlights(
        self, view: Union[QTextEdit, QTextBrowser], text: str
    ) -> None:
        """高亮所有匹配项（当前项橙色，其他项黄色）"""
        extra_selections = []
        if self._theme_manager.current_theme == "dark":
            current_color = QColor("#b86e00")
            other_color = QColor("#3d3d00")
            current_fg = QColor("#ffffff")
        else:
            current_color = QColor("#ff9632")
            other_color = QColor("#fff3a8")
            current_fg = QColor("#ffffff")

        for i, (start, end) in enumerate(self._search_matches):
            selection = QTextEdit.ExtraSelection()
            if i == self._current_match_index:
                selection.format.setBackground(current_color)
                selection.format.setForeground(current_fg)
            else:
                selection.format.setBackground(other_color)
            cursor = QTextCursor(view.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            selection.cursor = cursor
            extra_selections.append(selection)
        view.setExtraSelections(extra_selections)

    def _find_all_matches(self, text: str, document: QTextDocument):
        """查找所有匹配项，存储 (start, end) 元组"""
        self._search_matches = []
        cursor = QTextCursor(document)
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        while not cursor.isNull():
            cursor = document.find(text, cursor)
            if not cursor.isNull():
                self._search_matches.append(
                    (cursor.selectionStart(), cursor.selectionEnd())
                )
                cursor.movePosition(QTextCursor.MoveOperation.NextCharacter)

    def _update_search_count_label(self):
        """更新搜索计数标签"""
        if self._search_matches:
            self.search_count_label.setText(
                f"{self._current_match_index + 1}/{len(self._search_matches)}"
            )
        else:
            self.search_count_label.setText("0/0")

    # ─── 书签 ─────────────────────────────────────────────────

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
        full_text = current_view.toPlainText()

        # 截取光标位置附近的文本作为预览（前30字符 + 后70字符）
        start = max(0, position - 30)
        end = min(len(full_text), position + 70)
        context_text = full_text[start:end]

        content_type = (
            "transcript" if current_view == self.transcript_view else "summary"
        )

        bookmark = BookmarkItem(
            self._current_video_name, content_type, position, context_text
        )
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
        self._bookmark_count_label.setText(f"共 {len(self._bookmarks)} 个书签")
        self._bookmark_empty_label.setVisible(len(self._bookmarks) == 0)
        self.bookmark_list.setVisible(len(self._bookmarks) > 0)

        type_labels = {"transcript": "转写", "summary": "摘要"}
        for i, bookmark in enumerate(self._bookmarks):
            type_label = type_labels.get(bookmark.content_type, bookmark.content_type)
            display_text = f"[{type_label}] {bookmark.video_name}"
            if bookmark.text.strip():
                display_text += f"\n  {bookmark.text.strip()[:60]}"

            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setToolTip(
                f"文件: {bookmark.video_name}\n"
                f"类型: {type_label}\n"
                f"位置: {bookmark.position}\n"
                f"---\n{bookmark.text}"
            )
            self.bookmark_list.addItem(item)

    def _on_bookmark_double_clicked(self, item: QListWidgetItem):
        """书签双击事件"""
        index = item.data(Qt.ItemDataRole.UserRole)
        if not (0 <= index < len(self._bookmarks)):
            return
        bookmark = self._bookmarks[index]

        found = False
        if self._folder_mode:
            target = self._find_tree_item_by_name(bookmark.video_name)
            if target:
                self._folder_tree.setCurrentItem(target)
                found = True
        else:
            for i in range(self.file_list.count()):
                list_item = self.file_list.item(i)
                if list_item.data(Qt.ItemDataRole.UserRole) == bookmark.video_name:
                    self.file_list.setCurrentItem(list_item)
                    found = True
                    break

        if not found:
            self.status_bar.showMessage(f"未找到文件: {bookmark.video_name}")
            return

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

    def _filter_bookmarks(self, text: str):
        """根据输入过滤书签列表"""
        type_labels = {"transcript": "转写", "summary": "摘要"}
        for i in range(self.bookmark_list.count()):
            item = self.bookmark_list.item(i)
            index = item.data(Qt.ItemDataRole.UserRole)
            if 0 <= index < len(self._bookmarks):
                bookmark = self._bookmarks[index]
                searchable = (
                    f"{bookmark.video_name} "
                    f"{type_labels.get(bookmark.content_type, '')} "
                    f"{bookmark.text}"
                ).lower()
                item.setHidden(bool(text) and text.lower() not in searchable)

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

    # ─── 标签页切换 ────────────────────────────────────────────

    def _on_tab_changed(self, index: int):
        """标签页切换时清除搜索状态"""
        self._clear_search_state()

    # ─── 窗口状态持久化 ───────────────────────────────────────

    def _restore_window_state(self):
        """恢复窗口大小、位置、工具栏和分割器状态"""
        settings = QSettings("Video2Text", "ResultViewer")
        geometry = settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        state = settings.value("windowState")
        if state is not None:
            self.restoreState(state)
        splitter_state = settings.value("splitterState")
        if splitter_state is not None:
            self._main_splitter.restoreState(splitter_state)

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭时保存窗口状态"""
        settings = QSettings("Video2Text", "ResultViewer")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("windowState", self.saveState())
        settings.setValue("splitterState", self._main_splitter.saveState())
        super().closeEvent(event)

    # ─── 键盘快捷键 ───────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """处理键盘快捷键"""
        key = event.key()
        mods = event.modifiers()

        if key == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.showNormal()

        elif key == Qt.Key.Key_F and mods == Qt.KeyboardModifier.ControlModifier:
            self.search_edit.setFocus()
            self.search_edit.selectAll()

        elif key == Qt.Key.Key_F3:
            if mods == Qt.KeyboardModifier.ShiftModifier:
                self._search_prev()
            else:
                self._search_next()

        elif key == Qt.Key.Key_G:
            if mods == Qt.KeyboardModifier.ControlModifier:
                self._search_next()
            elif mods == (
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
            ):
                self._search_prev()

        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal) and (
            mods == Qt.KeyboardModifier.ControlModifier
        ):
            self.font_size_spin.setValue(self.font_size_spin.value() + 1)

        elif key == Qt.Key.Key_Minus and (mods == Qt.KeyboardModifier.ControlModifier):
            self.font_size_spin.setValue(self.font_size_spin.value() - 1)

        elif key == Qt.Key.Key_0 and (mods == Qt.KeyboardModifier.ControlModifier):
            self.font_size_spin.setValue(14)

        super().keyPressEvent(event)
