"""独立结果查看窗口 —— 支持全屏、Markdown、多标签、搜索、书签、主题切换"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from src.storage.bookmark_manager import BookmarkItem, BookmarkManager
from src.storage.file_writer import FileWriter
from src.ui.markdown_renderer import MarkdownRenderer
from src.ui.theme_manager import ThemeManager
from src.utils.paths import get_base_dir as _get_base_dir

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QKeyEvent,
    QKeySequence,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
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
    import importlib.util

    MARKDOWN_AVAILABLE = importlib.util.find_spec("markdown") is not None
except ImportError:
    MARKDOWN_AVAILABLE = False

logger = logging.getLogger(__name__)


def _find_summary_path(output_dir: str, video_name: str) -> Optional[Path]:
    """查找摘要文件（支持 _summary.txt 和 _summary.md）"""
    return FileWriter(output_dir).find_summary_file(video_name)


class ResultViewerWindow(QMainWindow):
    """独立的结果查看窗口 —— 支持全屏显示、多标签页、搜索替换、书签管理、主题切换。

    可从主窗口打开，独立浏览转写和总结结果，支持 Markdown 渲染和键盘快捷键。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("结果查看 - Video2Text")
        self.resize(1400, 900)

        self._theme_manager = ThemeManager()
        self._output_dir = ""
        self._root_output_dir = ""
        self._flat_video_names: list[str] = []
        self._bookmark_mgr = BookmarkManager(_get_base_dir() / "bookmarks.json")
        self._all_video_names: list[str] = []
        self._current_video_name: Optional[str] = None
        self._search_matches: list[tuple[int, int]] = []
        self._current_match_index: int = -1
        self._folder_mode: bool = False
        self._tree_name_map: dict[str, QTreeWidgetItem] = {}
        self._md_renderer = MarkdownRenderer()
        self._selected_bookmark_date: str = ""
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._do_search)

        self._init_ui()
        self._apply_theme()
        self._load_bookmarks()

        self.tabs.currentChanged.connect(self._on_tab_changed)

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

        # 搜索栏（默认隐藏，Ctrl+F 切换）
        self._search_widget = self._create_search_bar()
        self._search_widget.setVisible(False)
        right_layout.addWidget(self._search_widget)

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

        # 搜索按钮
        find_action = QAction("搜索", self)
        find_action.setShortcut(QKeySequence("Ctrl+F"))
        find_action.setToolTip("搜索文本 (Ctrl+F)")
        find_action.triggered.connect(self._toggle_search_bar)
        toolbar.addAction(find_action)

        # 全屏按钮
        fullscreen_action = QAction("全屏", self)
        fullscreen_action.setShortcut(QKeySequence("F11"))
        fullscreen_action.setToolTip("切换全屏 (F11)")
        fullscreen_action.triggered.connect(self._toggle_fullscreen)
        toolbar.addAction(fullscreen_action)

        toolbar.addSeparator()

        # 书签按钮
        add_bookmark_action = QAction("添加书签", self)
        add_bookmark_action.setShortcut(QKeySequence("Ctrl+B"))
        add_bookmark_action.setToolTip("添加书签 (Ctrl+B)")
        add_bookmark_action.triggered.connect(self._add_bookmark)
        toolbar.addAction(add_bookmark_action)

        toggle_bookmark_action = QAction("书签面板", self)
        toggle_bookmark_action.setShortcut(QKeySequence("Ctrl+Shift+B"))
        toggle_bookmark_action.setToolTip("显示/隐藏书签面板 (Ctrl+Shift+B)")
        toggle_bookmark_action.triggered.connect(self._toggle_bookmark_dock)
        toolbar.addAction(toggle_bookmark_action)

        toolbar.addSeparator()

        # 文件夹模式按钮
        self._folder_mode_action = QAction("文件夹模式", self)
        self._folder_mode_action.setShortcut(QKeySequence("Ctrl+D"))
        self._folder_mode_action.setToolTip("切换文件夹模式 (Ctrl+D)")
        self._folder_mode_action.setCheckable(True)
        self._folder_mode_action.setChecked(False)
        self._folder_mode_action.triggered.connect(self._toggle_folder_mode)
        toolbar.addAction(self._folder_mode_action)

        toolbar.addSeparator()

        # 关闭按钮
        # close_action = QAction("关闭", self)
        # close_action.setShortcut(QKeySequence("Ctrl+W"))
        # close_action.triggered.connect(self.close)
        # toolbar.addAction(close_action)

    def _create_bookmark_dock(self):
        """创建书签停靠窗口"""
        self.bookmark_dock = QDockWidget("书签", self)
        self.bookmark_dock.setObjectName("BookmarkDock")
        self.bookmark_dock.setMinimumWidth(220)
        self.bookmark_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )

        title_bar = QWidget()
        title_bar.setObjectName("BookmarkDockTitleBar")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(10, 4, 4, 4)
        title_label = QLabel("书签")
        title_label.setStyleSheet("font-weight: 600;")
        title_layout.addWidget(title_label)
        title_layout.addStretch()
        dock_close_btn = QPushButton("✕")
        dock_close_btn.setFixedWidth(28)
        dock_close_btn.setStyleSheet(
            "font-size: 16px; font-weight: bold; border: none; padding: 0;"
        )
        dock_close_btn.setToolTip("关闭书签面板")
        dock_close_btn.clicked.connect(self.bookmark_dock.close)
        title_layout.addWidget(dock_close_btn)
        self.bookmark_dock.setTitleBarWidget(title_bar)

        bookmark_widget = QWidget()
        bookmark_layout = QVBoxLayout(bookmark_widget)
        bookmark_layout.setContentsMargins(6, 6, 6, 6)
        bookmark_layout.setSpacing(4)

        self._bookmark_count_label = QLabel("共 0 个书签")
        self._bookmark_count_label.setObjectName("BookmarkCountLabel")
        bookmark_layout.addWidget(self._bookmark_count_label)

        self._bookmark_filter_row = QWidget()
        filter_row = QHBoxLayout(self._bookmark_filter_row)
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(4)

        self._bookmark_filter = QLineEdit()
        self._bookmark_filter.setPlaceholderText("过滤书签...")
        self._bookmark_filter.textChanged.connect(self._filter_bookmarks)
        filter_row.addWidget(self._bookmark_filter, 1)

        self._bookmark_date_combo = QComboBox()
        self._bookmark_date_combo.addItem("全部日期", "")
        self._bookmark_date_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._bookmark_date_combo.currentIndexChanged.connect(
            self._on_bookmark_date_filter_changed
        )
        filter_row.addWidget(self._bookmark_date_combo)

        self._bookmark_sort_combo = QComboBox()
        self._bookmark_sort_combo.addItems(["按添加时间", "按文件名", "按内容类型"])
        self._bookmark_sort_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._bookmark_sort_combo.currentIndexChanged.connect(
            self._on_bookmark_sort_changed
        )
        filter_row.addWidget(self._bookmark_sort_combo)

        bookmark_layout.addWidget(self._bookmark_filter_row)

        self.bookmark_list = QListWidget()
        self.bookmark_list.setSpacing(2)
        self.bookmark_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.bookmark_list.itemDoubleClicked.connect(self._on_bookmark_double_clicked)
        self.bookmark_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.bookmark_list.customContextMenuRequested.connect(
            self._show_bookmark_context_menu
        )
        bookmark_layout.addWidget(self.bookmark_list)

        self._bookmark_empty_label = QLabel("暂无书签\nCtrl+B 添加书签")
        self._bookmark_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bookmark_empty_label.setStyleSheet(
            "color: #aaa; font-size: 12px; padding: 20px 0;"
        )
        self._bookmark_empty_label.setVisible(True)
        bookmark_layout.addWidget(self._bookmark_empty_label)

        self._bookmark_btn_row = QWidget()
        bookmark_btn_layout = QHBoxLayout(self._bookmark_btn_row)
        bookmark_btn_layout.setContentsMargins(0, 0, 0, 0)
        bookmark_btn_layout.setSpacing(4)

        delete_bookmark_btn = QPushButton("删除")
        delete_bookmark_btn.setObjectName("BookmarkDeleteBtn")
        delete_bookmark_btn.setToolTip("删除选中书签")
        delete_bookmark_btn.clicked.connect(self._delete_bookmark)
        bookmark_btn_layout.addWidget(delete_bookmark_btn)

        batch_delete_btn = QPushButton("批量删除")
        batch_delete_btn.setObjectName("BookmarkDeleteBtn")
        batch_delete_btn.setToolTip("删除所有选中的书签")
        batch_delete_btn.clicked.connect(self._batch_delete_bookmarks)
        bookmark_btn_layout.addWidget(batch_delete_btn)

        clear_bookmarks_btn = QPushButton("清空")
        clear_bookmarks_btn.setObjectName("BookmarkDeleteBtn")
        clear_bookmarks_btn.setToolTip("清空所有书签")
        clear_bookmarks_btn.clicked.connect(self._clear_bookmarks)
        bookmark_btn_layout.addWidget(clear_bookmarks_btn)

        bookmark_layout.addWidget(self._bookmark_btn_row)

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
            summary_path = _find_summary_path(
                self._output_dir, self._current_video_name
            )
            if summary_path:
                try:
                    summary_text = summary_path.read_text(encoding="utf-8")
                    self._display_markdown(summary_text)
                except Exception:
                    logger.warning(
                        "重新渲染摘要失败（主题切换）: %s", summary_path.name
                    )

    # ─── 文件加载与过滤 ────────────────────────────────────────

    def load_files(self, video_names: list[str], output_dir: str):
        """加载多个文件"""
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
            if txt_file.name.endswith("_summary.txt") or txt_file.name.endswith(
                "_summary.md"
            ):
                continue
            if txt_file.name.endswith("_keywords.txt"):
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

        try:
            summary_files = sorted(
                p
                for p in output_path.rglob("*_summary.*")
                if p.suffix in (".txt", ".md")
            )
        except OSError:
            summary_files = []

        for sf in summary_files:
            vname = sf.stem.removesuffix("_summary")
            if not vname or vname in video_names:
                continue
            video_names.add(vname)

            rel = sf.parent.relative_to(output_path)
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
            child.setText(0, vname)
            child.setData(0, Qt.ItemDataRole.UserRole, vname)
            child.setData(0, Qt.ItemDataRole.UserRole + 1, str(sf.parent))
            parent.addChild(child)
            tree_key = str(sf.parent / vname)
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
        """递归更新文件夹节点名称，后缀显示直接子文件数量"""
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
        """加载指定文件的转写和摘要内容"""
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
        summary_path = _find_summary_path(output_dir, video_name)
        if summary_path:
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

        font_size = self.font_size_spin.value()
        theme = self._theme_manager.THEMES.get(
            self._theme_manager.current_theme, self._theme_manager.THEMES["light"]
        )
        css = self._theme_manager.get_markdown_css(font_size)

        default_font = QFont()
        default_font.setPointSize(font_size)
        self.summary_view.document().setDefaultFont(default_font)

        html = self._md_renderer.render(
            markdown_text,
            font_size=font_size,
            theme_css=css,
            border_color=theme["border_color"],
            secondary_bg=theme["secondary_bg"],
        )
        if html is None:
            self.summary_view.setPlainText(markdown_text)
            return
        self.summary_view.setHtml(html)

    # ─── 字体 ─────────────────────────────────────────────────

    def _update_font_size(self, size: int):
        """更新字体大小"""
        font = QFont("Consolas", size)
        self.transcript_view.setFont(font)

        # 重新渲染摘要（应用新字体大小，清除搜索状态）
        if self._current_video_name:
            self._clear_search_state()
            summary_path = _find_summary_path(
                self._output_dir, self._current_video_name
            )
            if summary_path:
                try:
                    summary_text = summary_path.read_text(encoding="utf-8")
                    self._display_markdown(summary_text)
                except Exception:
                    logger.warning("重新渲染摘要失败: %s", summary_path.name)

    # ─── 全屏 ─────────────────────────────────────────────────

    def _toggle_fullscreen(self):
        """切换全屏模式"""
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ─── 搜索（含全部高亮）─────────────────────────────────────

    def _create_search_bar(self) -> QWidget:
        """创建搜索栏（底部面板，参考主界面实现）"""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 4, 0, 0)

        layout.addWidget(QLabel("查找:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入搜索内容…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.returnPressed.connect(self._search_next)
        self.search_edit.textChanged.connect(self._on_search_text_changed)
        layout.addWidget(self.search_edit, 1)

        self.search_prev_btn = QPushButton("▲")
        self.search_prev_btn.setFixedWidth(32)
        self.search_prev_btn.setToolTip("上一个")
        self.search_prev_btn.clicked.connect(self._search_prev)
        layout.addWidget(self.search_prev_btn)

        self.search_next_btn = QPushButton("▼")
        self.search_next_btn.setFixedWidth(32)
        self.search_next_btn.setToolTip("下一个 (Enter)")
        self.search_next_btn.clicked.connect(self._search_next)
        layout.addWidget(self.search_next_btn)

        self.search_count_label = QLabel("")
        self.search_count_label.setMinimumWidth(90)
        layout.addWidget(self.search_count_label)

        close_btn = QPushButton("✕")
        close_btn.setFixedWidth(28)
        close_btn.setStyleSheet(
            "font-size: 16px; font-weight: bold; border: none; padding: 0;"
        )
        close_btn.setToolTip("关闭搜索栏 (Esc)")
        close_btn.clicked.connect(self._close_search_bar)
        layout.addWidget(close_btn)

        return widget

    def _toggle_search_bar(self):
        """切换搜索栏显示/隐藏"""
        if self._search_widget.isVisible():
            self._close_search_bar()
        else:
            self._search_widget.setVisible(True)
            self.search_edit.setFocus()
            current_view = self.tabs.currentWidget()
            if isinstance(current_view, (QTextEdit, QTextBrowser)):
                cursor = current_view.textCursor()
                if cursor.hasSelection():
                    self.search_edit.setText(cursor.selectedText())
                elif self.search_edit.text():
                    self._do_search()

    def _close_search_bar(self):
        """关闭搜索栏并清除高亮"""
        if not self._search_widget.isVisible():
            return
        self._search_widget.setVisible(False)
        self._clear_search_state()

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

    def _resolve_file_path(self, video_name: str, content_type: str) -> Optional[Path]:
        """根据 video_name 和 content_type 定位实际文件路径"""
        if content_type == "summary":
            return _find_summary_path(self._output_dir, video_name)
        for ext in ("txt", "srt", "vtt", "json"):
            candidate = Path(self._output_dir) / f"{video_name}.{ext}"
            if candidate.exists():
                return candidate
        return None

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

        start = max(0, position - 30)
        end = min(len(full_text), position + 70)
        context_text = full_text[start:end]

        content_type = (
            "transcript" if current_view == self.transcript_view else "summary"
        )

        file_path_obj = self._resolve_file_path(self._current_video_name, content_type)
        file_path_str = str(file_path_obj) if file_path_obj else ""
        relative_path_str = ""
        if file_path_obj and self._root_output_dir:
            try:
                relative_path_str = str(
                    file_path_obj.relative_to(self._root_output_dir)
                )
            except ValueError:
                relative_path_str = file_path_obj.name

        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        bookmark = BookmarkItem(
            video_name=self._current_video_name,
            content_type=content_type,
            position=position,
            text=context_text,
            file_path=file_path_str,
            relative_path=relative_path_str,
            created_at=created_at,
        )

        existing = self._bookmark_mgr.get_all()
        for b in existing:
            if b.file_path == file_path_str and b.position == position:
                reply = QMessageBox.question(
                    self,
                    "重复书签",
                    "该位置已存在书签，是否仍要添加？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                break

        self._bookmark_mgr.add(bookmark)
        self._refresh_bookmark_list()
        self.status_bar.showMessage("书签已添加")

    def _delete_bookmark(self):
        """删除当前选中书签"""
        current_item = self.bookmark_list.currentItem()
        if current_item is None:
            return

        index = current_item.data(Qt.ItemDataRole.UserRole)
        if index is None:
            return

        reply = QMessageBox.question(
            self,
            "确认删除",
            "确定要删除该书签吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._bookmark_mgr.remove([index])
        self._refresh_bookmark_list()
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
            self._bookmark_mgr.clear()
            self._refresh_bookmark_list()
            self.status_bar.showMessage("书签已清空")

    def _refresh_bookmark_list(self, bookmarks: Optional[list[BookmarkItem]] = None):
        """刷新书签列表

        Args:
            bookmarks: 可选的书签列表（用于排序/过滤视图）。为 None 时从 BookmarkManager 读取。
        """
        all_bookmarks = self._bookmark_mgr.get_all()
        display_bookmarks = bookmarks if bookmarks is not None else all_bookmarks

        self._update_date_filter_options(all_bookmarks)

        selected_date = self._selected_bookmark_date
        if selected_date:
            display_bookmarks = [
                b
                for b in display_bookmarks
                if b.created_at and b.created_at.startswith(selected_date)
            ]

        self.bookmark_list.clear()
        total = len(all_bookmarks)
        showing = len(display_bookmarks)

        if (bookmarks is not None and showing != total) or selected_date:
            self._bookmark_count_label.setText(f"显示 {showing} / 共 {total} 个书签")
        else:
            self._bookmark_count_label.setText(f"共 {total} 个书签")
        self._bookmark_empty_label.setVisible(total == 0)
        self.bookmark_list.setVisible(total > 0)
        self._bookmark_count_label.setVisible(total > 0)
        self._bookmark_filter_row.setVisible(total > 0)
        self._bookmark_btn_row.setVisible(total > 0)

        index_map = {
            (b.file_path, b.position, b.created_at): i
            for i, b in enumerate(all_bookmarks)
        }
        type_labels = {"transcript": "转写", "summary": "摘要"}
        for bookmark in display_bookmarks:
            key = (bookmark.file_path, bookmark.position, bookmark.created_at)
            real_index = index_map.get(key, -1)
            if real_index < 0:
                continue

            type_label = type_labels.get(bookmark.content_type, bookmark.content_type)
            path_display = bookmark.relative_path or bookmark.video_name
            display_text = f"[{type_label}] {path_display}"
            if bookmark.text.strip():
                display_text += f"\n  {bookmark.text.strip()[:60]}"

            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, real_index)
            tooltip_parts = [
                f"文件: {bookmark.relative_path or bookmark.video_name}",
                f"类型: {type_label}",
                f"位置: {bookmark.position}",
            ]
            if bookmark.file_path:
                tooltip_parts.append(f"路径: {bookmark.file_path}")
            if bookmark.created_at:
                tooltip_parts.append(f"创建: {bookmark.created_at}")
            if bookmark.note:
                tooltip_parts.append(f"备注: {bookmark.note}")
            tooltip_parts.append("---")
            tooltip_parts.append(bookmark.text)
            item.setToolTip("\n".join(tooltip_parts))
            self.bookmark_list.addItem(item)

    def _update_date_filter_options(self, bookmarks: list[BookmarkItem]):
        """更新日期过滤下拉框的选项，保留当前选中项"""
        dates: set[str] = set()
        for b in bookmarks:
            if b.created_at and len(b.created_at) >= 10:
                dates.add(b.created_at[:10])

        self._bookmark_date_combo.blockSignals(True)
        self._bookmark_date_combo.clear()
        self._bookmark_date_combo.addItem("全部日期", "")
        for d in sorted(dates, reverse=True):
            self._bookmark_date_combo.addItem(d, d)

        restore_index = 0
        if self._selected_bookmark_date:
            for i in range(self._bookmark_date_combo.count()):
                if (
                    self._bookmark_date_combo.itemData(i)
                    == self._selected_bookmark_date
                ):
                    restore_index = i
                    break
        self._bookmark_date_combo.setCurrentIndex(restore_index)
        self._bookmark_date_combo.blockSignals(False)

    def _on_bookmark_date_filter_changed(self, _index: int):
        """日期过滤变更"""
        self._selected_bookmark_date = self._bookmark_date_combo.currentData() or ""
        self._on_bookmark_sort_changed(self._bookmark_sort_combo.currentIndex())

    def _on_bookmark_double_clicked(self, item: QListWidgetItem):
        """书签双击事件 — 在当前列表中查找，不在则检测实际文件并提示切换目录或删除书签"""
        index = item.data(Qt.ItemDataRole.UserRole)
        all_bookmarks = self._bookmark_mgr.get_all()
        if not (0 <= index < len(all_bookmarks)):
            return
        bookmark = all_bookmarks[index]

        video_name = bookmark.video_name

        found = False
        if self._folder_mode:
            target = self._find_tree_item_by_name(video_name)
            if target:
                self._folder_tree.setCurrentItem(target)
                found = True
        else:
            for i in range(self.file_list.count()):
                list_item = self.file_list.item(i)
                if list_item.data(Qt.ItemDataRole.UserRole) == video_name:
                    self.file_list.setCurrentItem(list_item)
                    found = True
                    break

        if found:
            self._navigate_to_bookmark(bookmark)
            return

        resolved = self._resolve_bookmark_file(bookmark)
        if resolved is not None:
            file_dir = resolved.parent
            reply = QMessageBox.question(
                self,
                "文件不在当前列表",
                f"该书签对应的文件不在当前加载列表中。\n\n"
                f"文件实际存在于：\n{resolved}\n\n"
                f"是否切换到该文件所在目录并加载？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._switch_to_directory(file_dir, video_name, bookmark)
        else:
            reply = QMessageBox.question(
                self,
                "书签失效",
                f"书签对应的文件已被删除：\n"
                f"{bookmark.file_path or bookmark.video_name}\n\n"
                f"是否删除该书签？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._bookmark_mgr.remove([index])
                self._refresh_bookmark_list()
                self.status_bar.showMessage("已删除失效书签")

    def _resolve_bookmark_file(self, bookmark: BookmarkItem) -> Optional[Path]:
        """解析书签对应的实际文件路径，返回第一个存在的路径或 None"""
        if bookmark.file_path:
            p = Path(bookmark.file_path)
            if p.exists():
                return p
        if bookmark.relative_path and self._root_output_dir:
            p = Path(self._root_output_dir) / bookmark.relative_path
            if p.exists():
                return p
        return None

    def _switch_to_directory(
        self, target_dir: Path, select_video: str, bookmark: BookmarkItem
    ):
        """切换到指定目录并加载文件列表，然后定位到书签"""
        self._output_dir = str(target_dir)

        video_names: list[str] = []
        try:
            for txt_file in sorted(target_dir.rglob("*.txt")):
                if txt_file.name.endswith("_summary.txt") or txt_file.name.endswith(
                    "_keywords.txt"
                ):
                    continue
                if txt_file.stem:
                    video_names.append(txt_file.stem)
        except OSError:
            pass

        try:
            for sf in sorted(target_dir.rglob("*_summary.*")):
                if sf.suffix in (".txt", ".md"):
                    vname = sf.stem.removesuffix("_summary")
                    if vname and vname not in video_names:
                        video_names.append(vname)
        except OSError:
            pass

        if not video_names:
            QMessageBox.information(
                self, "提示", f"目录 {target_dir} 下未找到任何转写文件"
            )
            return

        video_names.sort(key=lambda x: x.lower())
        self._flat_video_names = video_names
        self._all_video_names = list(video_names)
        self._file_filter.clear()

        if self._folder_mode:
            self._scan_and_build_tree()
            target = self._find_tree_item_by_name(select_video)
            if target:
                self._folder_tree.setCurrentItem(target)
            self._folder_tree.setFocus()
        else:
            self._populate_file_list(video_names)
            for i in range(self.file_list.count()):
                list_item = self.file_list.item(i)
                if list_item.data(Qt.ItemDataRole.UserRole) == select_video:
                    self.file_list.setCurrentItem(list_item)
                    break
            self.file_list.setFocus()

        self._navigate_to_bookmark(bookmark)
        self.status_bar.showMessage(f"已切换到目录: {target_dir}")

    def _navigate_to_bookmark(self, bookmark: BookmarkItem):
        """定位到书签对应的标签页和文本位置"""
        if bookmark.content_type == "transcript":
            self.tabs.setCurrentWidget(self.transcript_view)
        else:
            self.tabs.setCurrentWidget(self.summary_view)

        current_view = self.tabs.currentWidget()
        if isinstance(current_view, (QTextEdit, QTextBrowser)):
            max_pos = len(current_view.toPlainText())
            safe_pos = min(bookmark.position, max_pos)
            cursor = QTextCursor(current_view.document())
            cursor.setPosition(safe_pos)
            current_view.setTextCursor(cursor)
            current_view.setFocus()

    def _batch_delete_bookmarks(self):
        """批量删除选中的书签"""
        selected_items = self.bookmark_list.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "提示", "请先选择要删除的书签")
            return

        count = len(selected_items)
        reply = QMessageBox.question(
            self,
            "确认批量删除",
            f"确定要删除选中的 {count} 个书签吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        indices = []
        for item in selected_items:
            idx = item.data(Qt.ItemDataRole.UserRole)
            if idx is not None:
                indices.append(idx)

        self._bookmark_mgr.remove(indices)
        self._refresh_bookmark_list()
        self.status_bar.showMessage(f"已删除 {count} 个书签")

    def _show_bookmark_context_menu(self, pos):
        """书签右键上下文菜单"""
        item = self.bookmark_list.itemAt(pos)
        menu = QMenu(self)

        goto_action = menu.addAction("跳转到位置")
        copy_action = menu.addAction("复制书签信息")
        edit_note_action = menu.addAction("编辑备注")
        menu.addSeparator()
        select_all_action = menu.addAction("全选")
        invert_action = menu.addAction("反选")
        menu.addSeparator()
        delete_action = menu.addAction("删除选中")

        action = menu.exec(self.bookmark_list.mapToGlobal(pos))
        if action is None:
            return

        if action == goto_action and item is not None:
            self._on_bookmark_double_clicked(item)
        elif action == copy_action and item is not None:
            self._copy_bookmark_info(item)
        elif action == edit_note_action and item is not None:
            self._edit_bookmark_note(item)
        elif action == select_all_action:
            self.bookmark_list.selectAll()
        elif action == invert_action:
            for i in range(self.bookmark_list.count()):
                li = self.bookmark_list.item(i)
                li.setSelected(not li.isSelected())
        elif action == delete_action:
            self._batch_delete_bookmarks()

    def _copy_bookmark_info(self, item: QListWidgetItem):
        """复制书签信息到剪贴板"""
        index = item.data(Qt.ItemDataRole.UserRole)
        all_bookmarks = self._bookmark_mgr.get_all()
        if not (0 <= index < len(all_bookmarks)):
            return
        bookmark = all_bookmarks[index]
        type_labels = {"transcript": "转写", "summary": "摘要"}
        type_label = type_labels.get(bookmark.content_type, bookmark.content_type)
        info = (
            f"文件: {bookmark.video_name}\n"
            f"类型: {type_label}\n"
            f"位置: {bookmark.position}\n"
            f"预览: {bookmark.text}"
        )
        if bookmark.file_path:
            info += f"\n路径: {bookmark.file_path}"
        if bookmark.note:
            info += f"\n备注: {bookmark.note}"

        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(info)
        self.status_bar.showMessage("书签信息已复制到剪贴板")

    def _edit_bookmark_note(self, item: QListWidgetItem):
        """编辑书签备注"""
        index = item.data(Qt.ItemDataRole.UserRole)
        all_bookmarks = self._bookmark_mgr.get_all()
        if not (0 <= index < len(all_bookmarks)):
            return
        bookmark = all_bookmarks[index]

        note, ok = QInputDialog.getText(
            self,
            "编辑备注",
            "请输入备注:",
            QLineEdit.EchoMode.Normal,
            bookmark.note,
        )
        if ok:
            updated = BookmarkItem(
                video_name=bookmark.video_name,
                content_type=bookmark.content_type,
                position=bookmark.position,
                text=bookmark.text,
                file_path=bookmark.file_path,
                relative_path=bookmark.relative_path,
                created_at=bookmark.created_at,
                note=note,
            )
            self._bookmark_mgr.remove([index])
            self._bookmark_mgr.add(updated)
            self._refresh_bookmark_list()
            self.status_bar.showMessage("备注已更新")

    def _on_bookmark_sort_changed(self, index: int):
        """书签排序变更"""
        all_bookmarks = self._bookmark_mgr.get_all()
        if index == 0:
            self._refresh_bookmark_list(all_bookmarks)
        elif index == 1:
            sorted_bm = sorted(all_bookmarks, key=lambda b: b.video_name.lower())
            self._refresh_bookmark_list(sorted_bm)
        elif index == 2:
            sorted_bm = sorted(all_bookmarks, key=lambda b: b.content_type)
            self._refresh_bookmark_list(sorted_bm)

    def _toggle_bookmark_dock(self):
        """切换书签面板显示"""
        if self.bookmark_dock.isVisible():
            self.bookmark_dock.hide()
        else:
            self.bookmark_dock.show()

    def _filter_bookmarks(self, text: str):
        """根据输入过滤书签列表"""
        type_labels = {"transcript": "转写", "summary": "摘要"}
        all_bookmarks = self._bookmark_mgr.get_all()
        total_visible = 0
        for i in range(self.bookmark_list.count()):
            item = self.bookmark_list.item(i)
            index = item.data(Qt.ItemDataRole.UserRole)
            if 0 <= index < len(all_bookmarks):
                bookmark = all_bookmarks[index]
                searchable = (
                    f"{bookmark.video_name} "
                    f"{bookmark.relative_path} "
                    f"{type_labels.get(bookmark.content_type, '')} "
                    f"{bookmark.text} "
                    f"{bookmark.note}"
                ).lower()
                hidden = bool(text) and text.lower() not in searchable
                item.setHidden(hidden)
                if not hidden:
                    total_visible += 1
            else:
                item.setHidden(bool(text))
                if not text:
                    total_visible += 1

        total = len(all_bookmarks)
        if text or self._selected_bookmark_date:
            self._bookmark_count_label.setText(
                f"显示 {total_visible} / 共 {total} 个书签"
            )
        else:
            self._bookmark_count_label.setText(f"共 {total} 个书签")

    def _load_bookmarks(self):
        """加载书签"""
        self._refresh_bookmark_list()

    def _save_bookmarks(self):
        """保存书签 — BookmarkManager 的 add/remove/clear 已自动持久化，此方法保留兼容性"""
        pass

    # ─── 标签页切换 ────────────────────────────────────────────

    def _on_tab_changed(self, index: int):
        """标签页切换时清除搜索状态"""
        self._clear_search_state()

    # ─── 键盘快捷键 ───────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """处理键盘快捷键"""
        key = event.key()
        mods = event.modifiers()

        if key == Qt.Key.Key_Escape:
            if self._search_widget.isVisible():
                self._close_search_bar()
            elif self.isFullScreen():
                self.showNormal()

        elif key == Qt.Key.Key_F and mods == Qt.KeyboardModifier.ControlModifier:
            self._toggle_search_bar()

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
