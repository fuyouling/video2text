"""独立结果查看窗口 —— 支持全屏、Markdown、多标签、搜索、书签"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from src.ui.background_content import BackgroundContent
from src.config.settings import Settings
from src.storage.bookmark_manager import BookmarkItem, BookmarkManager
from src.storage.file_writer import FileWriter
from src.ui.markdown_renderer import MarkdownRenderer
from src.utils.paths import get_base_dir as _get_base_dir
from src.i18n import t

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QPalette,
    QPixmap,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDockWidget,
    QFileDialog,
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
    QToolButton,
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


def _asset_path(name: str) -> Optional[str]:
    """解析 assets 目录下的图片资源路径（兼容打包环境）。"""
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "assets" / name,
    ]
    try:
        import sys

        if getattr(sys, "frozen", False):
            candidates.append(Path(sys.executable).parent / "assets" / name)
    except Exception:
        pass
    for path in candidates:
        if path.exists():
            return str(path)
    return None


def _find_summary_path(output_dir: str, video_name: str) -> Optional[Path]:
    """查找摘要文件（支持 _summary.txt 和 _summary.md）"""
    return FileWriter(output_dir).find_summary_file(video_name)


class ResultViewerWindow(QMainWindow):
    """独立的结果查看窗口 —— 支持全屏显示、多标签页、搜索替换、书签管理。
    可从主窗口打开，独立浏览转写和总结结果，支持 Markdown 渲染和键盘快捷键。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Window)
        self.setWindowTitle(t("dialogs.result_viewer.title"))
        self.resize(1400, 900)
        # showMaximized() 由调用方 _open_result_viewer 执行，确保内容先加载完成再显示

        icon_path = (
            Path(__file__).resolve().parent.parent.parent
            / "assets"
            / "video2text_logo.ico"
        )
        if not icon_path.exists():
            if getattr(sys, "frozen", False):
                icon_path = (
                    Path(sys.executable).parent / "assets" / "video2text_logo.ico"
                )
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

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

        # 背景图片
        self._bg_pixmap: Optional[QPixmap] = None
        self._bg_opacity: float = 0.4
        self._bg_image_path: str = ""
        self._bookmark_preserved_styles: dict[str, str] = {}

        self._init_ui()
        self._load_bg_settings()
        self._load_bookmarks()

        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.setCurrentIndex(1)

        # 在初始化最后统一应用 ToolTip 样式，避免深色主题下黑底黑字
        self._apply_tooltip_style()

    def paintEvent(self, event: QPaintEvent) -> None:
        """在 QMainWindow 层级绘制背景图片，使 dock widget 区域也能透出背景"""
        super().paintEvent(event)
        if (
            self._bg_pixmap is not None
            and not self._bg_pixmap.isNull()
        ):
            painter = QPainter(self)
            painter.setOpacity(self._bg_opacity)
            scaled = self._bg_pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            painter.end()

    # ─── UI 初始化 ─────────────────────────────────────────────

    def _init_ui(self) -> None:
        """初始化UI布局"""
        self._bg_content = BackgroundContent()
        self.setCentralWidget(self._bg_content)
        layout = QVBoxLayout(self._bg_content)
        layout.setContentsMargins(0, 0, 0, 0)

        # 工具栏
        self._create_toolbar()

        # 主分割器
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：文件列表
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(5, 5, 5, 5)

        left_layout.addWidget(QLabel(t("viewer.file_list_label")))

        # 文件过滤输入框
        self._file_filter = QLineEdit()
        self._file_filter.setPlaceholderText(t("viewer.file_filter_placeholder"))
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
        self.transcript_view.setPlaceholderText(t("viewer.transcript_placeholder_viewer"))
        self.transcript_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.transcript_view.customContextMenuRequested.connect(
            self._show_content_context_menu
        )
        self.tabs.addTab(self.transcript_view, t("viewer.transcript_tab"))

        # 摘要标签页（支持Markdown）
        self.summary_view = QTextBrowser()
        self.summary_view.setOpenExternalLinks(True)
        self.summary_view.setPlaceholderText(t("viewer.summary_placeholder_viewer"))
        self.summary_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.summary_view.customContextMenuRequested.connect(
            self._show_content_context_menu
        )
        self.tabs.addTab(self.summary_view, t("viewer.summary_tab"))

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


    def _apply_bg_transparency(self) -> None:
        """有背景图片时设置面板/控件透明，否则恢复默认样式"""
        has_bg = (
            self._bg_pixmap is not None
            and not self._bg_pixmap.isNull()
        )

        if has_bg:
            # 设置 splitter 透明
            if hasattr(self, "_main_splitter"):
                self._main_splitter.setStyleSheet("background: transparent;")

            # 文件列表相关 — 透明 + 细边框
            self.tabs.setStyleSheet(
                "QTabWidget { background: transparent; }"
                " QTabWidget::pane { background: transparent; border: 1px solid palette(mid); border-radius: 3px; }"
                " QTabBar::tab { padding: 6px 14px; border: 1px solid palette(mid); border-bottom: none;"
                "  border-top-left-radius: 3px; border-top-right-radius: 3px; }"
                " QTabBar::tab:selected { border-bottom: 2px solid palette(highlight); }"
            )
            self._file_filter.setStyleSheet(
                "QLineEdit { background: transparent; border: 1px solid palette(mid); border-radius: 3px; padding: 2px 4px; }"
            )
            self.file_list.setStyleSheet(
                "QListWidget { background: transparent; border: 1px solid palette(mid); border-radius: 3px; }"
            )
            self._folder_tree.setStyleSheet(
                "QTreeWidget { background: transparent; border: 1px solid palette(mid); border-radius: 3px; }"
            )

            # 文本视图 — 透明 + 细边框
            for view in (self.transcript_view, self.summary_view):
                view.setStyleSheet(
                    f"{type(view).__name__} {{ background: transparent; border: 1px solid palette(mid); border-radius: 3px; }}"
                )
                if hasattr(view, "viewport"):
                    view.viewport().setStyleSheet("background: transparent;")

            # ── 所有 QComboBox：按钮透明（可见背景图），下拉列表实底 ──
            self._apply_combo_style()

            # 书签停靠面板 — 透明 + 细边框
            if hasattr(self, "bookmark_dock"):
                self.bookmark_dock.setStyleSheet(
                    "QDockWidget { background: transparent; border: 1px solid palette(mid); border-radius: 3px; }"
                )
                # 内容面板（setWidget）
                dock_widget = self.bookmark_dock.widget()
                if dock_widget:
                    dock_widget.setStyleSheet("background: transparent;")
                    self._make_children_transparent(dock_widget)
                # 自定义标题栏（setTitleBarWidget — 平级控件，需单独处理）
                title_bar = self.bookmark_dock.titleBarWidget()
                if title_bar:
                    title_bar.setStyleSheet("background: transparent;")
                    self._make_children_transparent(title_bar)
        else:
            # 无背景图时恢复默认样式，让系统原生样式生效
            if hasattr(self, "_main_splitter"):
                self._main_splitter.setStyleSheet("")
            self.tabs.setStyleSheet("")
            self._file_filter.setStyleSheet("")
            self.file_list.setStyleSheet("")
            self._folder_tree.setStyleSheet("")
            for view in (self.transcript_view, self.summary_view):
                view.setStyleSheet("")
                if hasattr(view, "viewport"):
                    view.viewport().setStyleSheet("")
            # 恢复书签面板子控件样式
            if hasattr(self, "bookmark_dock"):
                self.bookmark_dock.setStyleSheet("")
                dock_widget = self.bookmark_dock.widget()
                if dock_widget:
                    dock_widget.setStyleSheet("")
                    self._clear_children_stylesheet(dock_widget)
                title_bar = self.bookmark_dock.titleBarWidget()
                if title_bar:
                    title_bar.setStyleSheet("")
                    self._clear_children_stylesheet(title_bar)
            # QComboBox 按钮恢复系统原生样式，但下拉列表始终使用浅色背景以保证可读
            self._apply_combo_style()

        # 不论是否有背景图，始终确保 ToolTip 样式不被覆盖
        self._apply_tooltip_style()

    def _make_children_transparent(self, parent: QWidget) -> None:
        """递归设置子控件透明背景（保留已有内联样式，仅追加透明+边框规则）

        Args:
            parent: 父控件
        """
        for child in parent.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            old = child.styleSheet()
            # 所有可见控件：透明背景 + 细边框
            if isinstance(child, (QLineEdit, QListWidget, QLabel, QPushButton)):
                extra = (
                    f" {type(child).__name__} {{ background: transparent;"
                    f" color: palette(text);"
                    f" border: 1px solid palette(mid); border-radius: 3px; }}"
                )
                child.setStyleSheet(old + "\n" + extra if old else extra.lstrip())
            elif isinstance(child, QWidget):
                if old:
                    if "background" not in old.lower():
                        child.setStyleSheet(old + "\nbackground: transparent;")
                else:
                    child.setStyleSheet("background: transparent;")
            self._make_children_transparent(child)

    def _clear_children_stylesheet(self, parent: QWidget) -> None:
        """递归清除子控件样式（有原始样式的控件恢复原始样式）"""
        for child in parent.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            obj_name = child.objectName()
            if obj_name in self._bookmark_preserved_styles:
                child.setStyleSheet(self._bookmark_preserved_styles[obj_name])
            else:
                child.setStyleSheet("")
            self._clear_children_stylesheet(child)

    def _apply_combo_style(self) -> None:
        """为所有 QComboBox 设置统一样式 —— 关键是下拉列表必须保证白底黑字可见。

        使用 background-color（而非 background 简写）以避免被父控件的 background:transparent 覆盖。
        显式为 ::item、::item:selected 写背景，保证选中行/未选中行都可读。
        """
        combo_style = (
            "QComboBox {"
            "  background-color: transparent;"
            "  color: palette(text);"
            "  border: 1px solid palette(mid);"
            "  border-radius: 3px;"
            "  padding: 2px 6px;"
            "}"
            "QComboBox:hover {"
            "  border: 1px solid palette(highlight);"
            "}"
            "QComboBox::drop-down {"
            "  subcontrol-origin: padding;"
            "  subcontrol-position: top right;"
            "  width: 18px;"
            "  border: none;"
            "}"
            "QComboBox QAbstractItemView {"
            "  background-color: #ffffff;"
            "  color: #000000;"
            "  selection-background-color: #cce5ff;"
            "  selection-color: #000000;"
            "  border: 1px solid #999999;"
            "  outline: 0;"
            "  padding: 2px;"
            "}"
            "QComboBox QAbstractItemView::item {"
            "  background-color: #ffffff;"
            "  color: #000000;"
            "  min-height: 1.4em;"
            "  padding: 2px 4px;"
            "}"
            "QComboBox QAbstractItemView::item:hover {"
            "  background-color: #e6f0ff;"
            "  color: #000000;"
            "}"
            "QComboBox QAbstractItemView::item:selected {"
            "  background-color: #cce5ff;"
            "  color: #000000;"
            "}"
        )
        for combo in self.findChildren(QComboBox):
            combo.setStyleSheet(combo_style)
            view = combo.view()
            if view is not None:
                view.setStyleSheet("background-color: #ffffff; color: #000000;")

    def _apply_tooltip_style(self) -> None:
        """为所有 ToolTip 设置统一样式 —— 避免系统深色主题下黑色背景看不见文字。

        使用 QPalette 直接设置 ToolTip 颜色，不受父控件 QSS 级联影响；
        同时追加 Application 级 QToolTip QSS 以防 platform style 覆盖 palette。
        """
        app = QApplication.instance()
        if app is not None:
            # ── QPalette：直接设置颜色角色，不依赖 QSS 层级 ──
            palette = app.palette()
            palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffe1"))
            palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#000000"))
            app.setPalette(palette)

            # ── Application 级 QSS：移除 guard，每次都确保覆盖 ──
            app_style = app.styleSheet()
            # 移除旧的 QToolTip 声明（如果存在），再追加新声明
            lines = [
                line
                for line in app_style.split("\n")
                if "QToolTip" not in line
            ]
            app.setStyleSheet(
                "\n".join(lines)
                + "\nQToolTip {"
                " background-color: #ffffe1;"
                " color: #000000;"
                " border: 1px solid #999999;"
                " padding: 2px 4px;"
                "}"
            )

    def _create_toolbar(self):
        """创建工具栏"""
        toolbar = QToolBar(t("viewer.main_toolbar"))
        toolbar.setObjectName("MainToolBar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addSeparator()

        # 字体控制
        toolbar.addWidget(QLabel(t("viewer.font_label")))
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 32)
        self.font_size_spin.setValue(14)
        self.font_size_spin.valueChanged.connect(self._update_font_size)
        toolbar.addWidget(self.font_size_spin)

        toolbar.addSeparator()


        toolbar.addSeparator()

        # 书签按钮
        add_bookmark_action = QAction(t("viewer.add_bookmark"), self)
        add_bookmark_action.setShortcut(QKeySequence("Ctrl+B"))
        add_bookmark_action.setToolTip(t("viewer.add_bookmark_tooltip"))
        add_bookmark_action.triggered.connect(self._add_bookmark)
        toolbar.addAction(add_bookmark_action)

        toggle_bookmark_action = QAction(t("viewer.toggle_bookmark_panel"), self)
        toggle_bookmark_action.setShortcut(QKeySequence("Ctrl+Shift+B"))
        toggle_bookmark_action.setToolTip(t("viewer.toggle_bookmark_tooltip"))
        toggle_bookmark_action.triggered.connect(self._toggle_bookmark_dock)
        toolbar.addAction(toggle_bookmark_action)

        toolbar.addSeparator()

        # 文件夹模式按钮
        self._folder_mode_action = QAction(t("viewer.folder_mode"), self)
        self._folder_mode_action.setShortcut(QKeySequence("Ctrl+D"))
        self._folder_mode_action.setToolTip(t("viewer.folder_mode_tooltip"))
        self._folder_mode_action.setCheckable(True)
        self._folder_mode_action.setChecked(False)
        self._folder_mode_action.triggered.connect(self._toggle_folder_mode)
        toolbar.addAction(self._folder_mode_action)

        toolbar.addSeparator()

        # 重新加载按钮
        reload_action = QAction(t("viewer.reload"), self)
        reload_action.setShortcut(QKeySequence("Ctrl+R"))
        reload_action.setToolTip(t("viewer.reload_tooltip"))
        reload_action.triggered.connect(self._reload_content)
        toolbar.addAction(reload_action)

        toolbar.addSeparator()

        # 背景图片（合成按钮：更换 / 清除 / 透明度）
        self._bg_btn = QToolButton()
        self._bg_btn.setText(t("viewer.bg_image"))
        self._bg_btn.setToolTip(t("viewer.bg_image_tooltip"))
        self._bg_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._bg_btn.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        bg_menu = QMenu(self)

        change_action = bg_menu.addAction(t("viewer.change_bg_image"))
        change_action.triggered.connect(self._change_bg_image)

        clear_action = bg_menu.addAction(t("viewer.clear_bg_image"))
        clear_action.triggered.connect(self._clear_bg_image)

        bg_menu.addSeparator()

        transparency_action = bg_menu.addAction(t("viewer.bg_transparency"))
        transparency_action.triggered.connect(self._adjust_bg_transparency)

        self._bg_btn.setMenu(bg_menu)
        toolbar.addWidget(self._bg_btn)

        toolbar.addSeparator()

        # 关闭按钮
        # close_action = QAction("关闭", self)
        # close_action.setShortcut(QKeySequence("Ctrl+W"))
        # close_action.triggered.connect(self.close)
        # toolbar.addAction(close_action)

    def _create_bookmark_dock(self):
        """创建书签停靠窗口"""
        self.bookmark_dock = QDockWidget(t("viewer.bookmark_dock"), self)
        self.bookmark_dock.setObjectName("BookmarkDock")
        self.bookmark_dock.setMinimumWidth(220)
        self.bookmark_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )

        title_bar = QWidget()
        title_bar.setObjectName("BookmarkDockTitleBar")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(10, 4, 4, 4)
        title_label = QLabel(t("viewer.bookmark_dock"))
        title_label.setObjectName("BookmarkTitleLabel")
        self._bookmark_preserved_styles["BookmarkTitleLabel"] = "font-weight: 600;"
        title_label.setStyleSheet(self._bookmark_preserved_styles["BookmarkTitleLabel"])
        title_layout.addWidget(title_label)
        title_layout.addStretch()
        dock_close_btn = QPushButton()
        dock_close_btn.setFixedWidth(28)
        close_icon = _asset_path("close.png")
        if close_icon:
            dock_close_btn.setIcon(QIcon(close_icon))
        else:
            dock_close_btn.setText("✕")
            style_close = "font-size: 16px; font-weight: bold; border: none; padding: 0;"
            dock_close_btn.setObjectName("BookmarkCloseBtn")
            self._bookmark_preserved_styles["BookmarkCloseBtn"] = style_close
            dock_close_btn.setStyleSheet(style_close)
        dock_close_btn.clicked.connect(self.bookmark_dock.close)
        title_layout.addWidget(dock_close_btn)
        self.bookmark_dock.setTitleBarWidget(title_bar)

        bookmark_widget = QWidget()
        bookmark_layout = QVBoxLayout(bookmark_widget)
        bookmark_layout.setContentsMargins(6, 6, 6, 6)
        bookmark_layout.setSpacing(4)

        self._bookmark_count_label = QLabel(t("viewer.bookmark_count", count=0))
        self._bookmark_count_label.setObjectName("BookmarkCountLabel")
        bookmark_layout.addWidget(self._bookmark_count_label)

        self._bookmark_filter_row = QWidget()
        filter_row = QHBoxLayout(self._bookmark_filter_row)
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(4)

        self._bookmark_filter = QLineEdit()
        self._bookmark_filter.setPlaceholderText(t("viewer.bookmark_filter_placeholder"))
        self._bookmark_filter.textChanged.connect(self._filter_bookmarks)
        filter_row.addWidget(self._bookmark_filter, 1)

        self._bookmark_date_combo = QComboBox()
        self._bookmark_date_combo.addItem(t("viewer.all_dates"), "")
        self._bookmark_date_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._bookmark_date_combo.currentIndexChanged.connect(
            self._on_bookmark_date_filter_changed
        )
        filter_row.addWidget(self._bookmark_date_combo)

        self._bookmark_sort_combo = QComboBox()
        self._bookmark_sort_combo.addItems([t("viewer.sort_by_time"), t("viewer.sort_by_name"), t("viewer.sort_by_type")])
        self._bookmark_sort_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._bookmark_sort_combo.currentIndexChanged.connect(
            self._on_bookmark_sort_changed
        )
        filter_row.addWidget(self._bookmark_sort_combo)

        self._bookmark_date_combo.setStyleSheet(
            "QComboBox { background-color: transparent; color: palette(text);"
            " border: 1px solid palette(mid); border-radius: 3px; padding: 2px 6px; }"
            "QComboBox QAbstractItemView { background-color: #ffffff; color: #000000;"
            " selection-background-color: #cce5ff; selection-color: #000000;"
            " border: 1px solid #999999; outline: 0; }"
            "QComboBox QAbstractItemView::item { background-color: #ffffff; color: #000000; }"
            "QComboBox QAbstractItemView::item:selected {"
            " background-color: #cce5ff; color: #000000; }"
        )
        self._bookmark_sort_combo.setStyleSheet(self._bookmark_date_combo.styleSheet())

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

        self._bookmark_empty_label = QLabel(t("viewer.no_bookmarks"))
        self._bookmark_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        style_empty = "color: #aaa; font-size: 12px; padding: 20px 0;"
        self._bookmark_empty_label.setObjectName("BookmarkEmptyLabel")
        self._bookmark_preserved_styles["BookmarkEmptyLabel"] = style_empty
        self._bookmark_empty_label.setStyleSheet(style_empty)
        self._bookmark_empty_label.setVisible(True)
        bookmark_layout.addWidget(self._bookmark_empty_label)

        self._bookmark_btn_row = QWidget()
        bookmark_btn_layout = QHBoxLayout(self._bookmark_btn_row)
        bookmark_btn_layout.setContentsMargins(0, 0, 0, 0)
        bookmark_btn_layout.setSpacing(4)

        delete_bookmark_btn = QPushButton(t("common.delete"))
        delete_bookmark_btn.setObjectName("BookmarkDeleteBtn")
        delete_bookmark_btn.setToolTip(t("viewer.delete_bookmark_tooltip"))
        delete_bookmark_btn.clicked.connect(self._delete_bookmark)
        bookmark_btn_layout.addWidget(delete_bookmark_btn)

        batch_delete_btn = QPushButton(t("viewer.batch_delete"))
        batch_delete_btn.setObjectName("BookmarkDeleteBtn")
        batch_delete_btn.setToolTip(t("viewer.batch_delete_tooltip"))
        batch_delete_btn.clicked.connect(self._batch_delete_bookmarks)
        bookmark_btn_layout.addWidget(batch_delete_btn)

        clear_bookmarks_btn = QPushButton(t("viewer.clear_bookmarks"))
        clear_bookmarks_btn.setObjectName("BookmarkDeleteBtn")
        clear_bookmarks_btn.setToolTip(t("viewer.clear_bookmarks_tooltip"))
        clear_bookmarks_btn.clicked.connect(self._clear_bookmarks)
        bookmark_btn_layout.addWidget(clear_bookmarks_btn)

        bookmark_layout.addWidget(self._bookmark_btn_row)

        self.bookmark_dock.setWidget(bookmark_widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.bookmark_dock)
        self.bookmark_dock.hide()

    # ─── 文件加载与过滤 ────────────────────────────────────────

    def load_files(
        self, video_names: list[str], output_dir: str, folder_mode: bool = False
    ):
        """加载多个文件"""
        self._output_dir = output_dir
        self._root_output_dir = output_dir
        self._flat_video_names = sorted(video_names, key=lambda x: x.lower())
        self._all_video_names = list(self._flat_video_names)
        self._file_filter.clear()

        if folder_mode:
            self._folder_mode_action.setChecked(True)
            self._folder_mode = True
            self.file_list.setVisible(False)
            self._folder_tree.setVisible(True)
            self._populate_file_list(self._all_video_names)
            self._scan_and_build_tree()
            self._filter_folder_tree(self._file_filter.text())
            self._folder_tree.setFocus()
        else:
            self._folder_mode_action.setChecked(False)
            self._folder_mode = False
            self.file_list.setVisible(True)
            self._file_filter.setVisible(True)
            self._folder_tree.setVisible(False)
            self._populate_file_list(self._all_video_names)
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
        """根据输入文本过滤文件列表 / 文件夹模式下的树形列表"""
        if self._folder_mode:
            self._filter_folder_tree(text)
            return

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

    def _filter_folder_tree(self, text: str) -> None:
        """根据输入文本过滤文件夹模式下的树形列表，保留匹配项及其祖先"""
        if not text:
            self._show_all_tree_items(self._folder_tree.invisibleRootItem())
            return

        text_lower = text.lower()
        self._show_matching_tree_items(self._folder_tree.invisibleRootItem(), text_lower)

    def _show_all_tree_items(self, item: QTreeWidgetItem) -> None:
        """递归显示所有树节点"""
        item.setHidden(False)
        for i in range(item.childCount()):
            self._show_all_tree_items(item.child(i))

    def _show_matching_tree_items(self, item: QTreeWidgetItem, text_lower: str) -> bool:
        """递归显示匹配项及其祖先；返回当前子树是否包含匹配"""
        has_match = False
        for i in range(item.childCount()):
            child_match = self._show_matching_tree_items(item.child(i), text_lower)
            if child_match:
                has_match = True

        item_text = item.text(0).lower()
        if text_lower in item_text or has_match:
            item.setHidden(False)
            return True
        item.setHidden(True)
        return False

    def _toggle_folder_mode(self, checked: bool):
        """切换文件夹模式"""
        self._folder_mode = checked
        self.file_list.setVisible(not checked)
        self._folder_tree.setVisible(checked)

        if checked and self._output_dir:
            self._scan_and_build_tree()
            self._filter_folder_tree(self._file_filter.text())
            self._folder_tree.setFocus()
        elif not checked:
            self._output_dir = self._root_output_dir
            self._all_video_names = list(self._flat_video_names)
            self._populate_file_list(self._all_video_names)
            self._filter_file_list(self._file_filter.text())
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

        _TRANSCRIPT_EXTS = (".txt", ".srt", ".vtt", ".json")
        _SKIP_SUFFIXES = ("_summary.txt", "_summary.md", "_keywords.txt")

        try:
            all_files: list[Path] = []
            for ext in _TRANSCRIPT_EXTS:
                all_files.extend(output_path.rglob(f"*{ext}"))
            all_files.sort()
        except OSError as exc:
            logger.warning("扫描目录失败: %s", exc)
            self._folder_tree.blockSignals(False)
            return

        for txt_file in all_files:
            if any(txt_file.name.endswith(s) for s in _SKIP_SUFFIXES):
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
                self.transcript_view.setPlainText(t("viewer.read_failed", error=exc))
        else:
            self.transcript_view.setPlainText(t("viewer.transcript_not_found"))

        # 加载摘要（Markdown渲染）
        summary_path = _find_summary_path(output_dir, video_name)
        if summary_path:
            try:
                summary_text = summary_path.read_text(encoding="utf-8-sig")
                self._display_markdown(summary_text)
            except Exception as exc:
                self.summary_view.setPlainText(t("viewer.read_failed", error=exc))
        else:
            self.summary_view.setPlainText(t("viewer.summary_not_found"))

        self.status_bar.showMessage(t("viewer.loaded", name=video_name))

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

    def _reload_content(self) -> None:
        """重新加载当前文件内容"""
        if not self._current_video_name:
            QMessageBox.information(self, t("common.hint"), t("viewer.select_file_first"))
            return
        self.load_content(self._current_video_name, self._output_dir)
        self.status_bar.showMessage(t("viewer.reloaded"))

    # ─── 内容区右键菜单 ──────────────────────────────────────────

    def _show_content_context_menu(self, pos) -> None:
        """内容查看区右键菜单"""
        menu = QMenu(self)
        reload_action = menu.addAction(t("viewer.refresh"))
        reload_action.setShortcut(QKeySequence("Ctrl+R"))
        reload_action.triggered.connect(self._reload_content)
        menu.exec(self.sender().mapToGlobal(pos))

    # ─── Markdown 渲染 ─────────────────────────────────────────

    def _display_markdown(self, markdown_text: str):
        """渲染Markdown内容（带缓存，仅在文本变化时重新解析）"""
        if not MARKDOWN_AVAILABLE:
            self.summary_view.setPlainText(markdown_text)
            return

        font_size = self.font_size_spin.value()

        default_font = QFont()
        default_font.setPointSize(font_size)
        self.summary_view.document().setDefaultFont(default_font)

        html = self._md_renderer.render(
            markdown_text,
            font_size=font_size,
            theme_css="",
            border_color="#cccccc",
            secondary_bg="#f5f5f5",
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
                    summary_text = summary_path.read_text(encoding="utf-8-sig")
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

        layout.addWidget(QLabel(t("search.find_label")))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(t("search.find_placeholder"))
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.returnPressed.connect(self._search_next)
        self.search_edit.textChanged.connect(self._on_search_text_changed)
        layout.addWidget(self.search_edit, 1)

        self.search_prev_btn = QPushButton()
        self.search_prev_btn.setFixedWidth(32)
        self.search_prev_btn.setToolTip(t("search.prev_tooltip"))
        prev_icon = _asset_path("arrow_up.png")
        if prev_icon:
            self.search_prev_btn.setIcon(QIcon(prev_icon))
        else:
            self.search_prev_btn.setText("▲")
        self.search_prev_btn.clicked.connect(self._search_prev)
        layout.addWidget(self.search_prev_btn)

        self.search_next_btn = QPushButton()
        self.search_next_btn.setFixedWidth(32)
        self.search_next_btn.setToolTip(t("search.next_tooltip"))
        next_icon = _asset_path("arrow_down.png")
        if next_icon:
            self.search_next_btn.setIcon(QIcon(next_icon))
        else:
            self.search_next_btn.setText("▼")
        self.search_next_btn.clicked.connect(self._search_next)
        layout.addWidget(self.search_next_btn)

        self.search_count_label = QLabel("")
        self.search_count_label.setMinimumWidth(90)
        layout.addWidget(self.search_count_label)

        close_btn = QPushButton()
        close_btn.setFixedWidth(28)
        close_btn.setToolTip(t("search.close_tooltip"))
        close_icon = _asset_path("close.png")
        if close_icon:
            close_btn.setIcon(QIcon(close_icon))
        else:
            close_btn.setText("✕")
            close_btn.setStyleSheet(
                "font-size: 16px; font-weight: bold; border: none; padding: 0;"
            )
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
            QMessageBox.warning(self, t("common.hint"), t("viewer.select_file_first"))
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
                    t("viewer.duplicate_bookmark"),
                    t("viewer.duplicate_bookmark_confirm"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                break

        self._bookmark_mgr.add(bookmark)
        self._refresh_bookmark_list()
        self.status_bar.showMessage(t("viewer.bookmark_added"))

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
            t("common.confirm"),
            t("viewer.confirm_delete_bookmark"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._bookmark_mgr.remove([index])
        self._refresh_bookmark_list()
        self.status_bar.showMessage(t("viewer.bookmark_deleted"))

    def _clear_bookmarks(self):
        """清空书签"""
        reply = QMessageBox.question(
            self,
            t("common.confirm"),
            t("viewer.confirm_clear_bookmarks"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._bookmark_mgr.clear()
            self._refresh_bookmark_list()
            self.status_bar.showMessage(t("viewer.bookmarks_cleared"))

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
            self._bookmark_count_label.setText(
                t("viewer.bookmark_count_showing", showing=showing, total=total)
            )
        else:
            self._bookmark_count_label.setText(
                t("viewer.bookmark_count", count=total)
            )
        self._bookmark_empty_label.setVisible(total == 0)
        self.bookmark_list.setVisible(total > 0)
        self._bookmark_count_label.setVisible(total > 0)
        self._bookmark_filter_row.setVisible(total > 0)
        self._bookmark_btn_row.setVisible(total > 0)

        index_map = {
            (b.file_path, b.position, b.created_at): i
            for i, b in enumerate(all_bookmarks)
        }
        type_labels = {"transcript": t("viewer.type_transcript"), "summary": t("viewer.type_summary")}
        # 标签内容详情
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
            self.bookmark_list.addItem(item)

    def _update_date_filter_options(self, bookmarks: list[BookmarkItem]):
        """更新日期过滤下拉框的选项，保留当前选中项"""
        dates: set[str] = set()
        for b in bookmarks:
            if b.created_at and len(b.created_at) >= 10:
                dates.add(b.created_at[:10])

        self._bookmark_date_combo.blockSignals(True)
        self._bookmark_date_combo.clear()
        self._bookmark_date_combo.addItem(t("viewer.all_dates"), "")
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
                t("viewer.bookmark_not_in_list"),
                t("viewer.bookmark_not_in_list_msg", path=resolved),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._switch_to_directory(file_dir, video_name, bookmark)
        else:
            reply = QMessageBox.question(
                self,
                t("viewer.bookmark_invalid"),
                t("viewer.bookmark_invalid_msg", name=bookmark.file_path or bookmark.video_name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._bookmark_mgr.remove([index])
                self._refresh_bookmark_list()
                self.status_bar.showMessage(t("viewer.deleted_invalid_bookmark"))

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
                self, t("common.hint"), t("viewer.no_transcript_in_dir", dir=target_dir)
            )
            return

        video_names.sort(key=lambda x: x.lower())
        self._flat_video_names = video_names
        self._all_video_names = list(video_names)
        self._file_filter.clear()

        if self._folder_mode:
            self._scan_and_build_tree()
            self._filter_folder_tree(self._file_filter.text())
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
        self.status_bar.showMessage(t("viewer.switched_dir", dir=target_dir))

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
            QMessageBox.information(self, t("common.hint"), t("viewer.select_bookmarks_to_delete"))
            return

        count = len(selected_items)
        reply = QMessageBox.question(
            self,
            t("common.confirm"),
            t("viewer.confirm_batch_delete", count=count),
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
        self.status_bar.showMessage(t("viewer.deleted_count", count=count))

    def _show_bookmark_context_menu(self, pos):
        """书签右键上下文菜单"""
        item = self.bookmark_list.itemAt(pos)
        menu = QMenu(self)

        goto_action = menu.addAction(t("viewer.goto_position"))
        copy_action = menu.addAction(t("viewer.copy_bookmark_info"))
        edit_note_action = menu.addAction(t("viewer.edit_note"))
        menu.addSeparator()
        select_all_action = menu.addAction(t("viewer.select_all"))
        invert_action = menu.addAction(t("viewer.invert_selection"))
        menu.addSeparator()
        delete_action = menu.addAction(t("viewer.delete_selected"))

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
        type_labels = {"transcript": t("viewer.type_transcript"), "summary": t("viewer.type_summary")}
        type_label = type_labels.get(bookmark.content_type, bookmark.content_type)
        info = (
            t("viewer.copy_info_format",
              name=bookmark.video_name,
              type_label=type_label,
              position=bookmark.position,
              preview=bookmark.text)
        )
        if bookmark.file_path:
            info += "\n" + t("viewer.copy_info_path", path=bookmark.file_path)
        if bookmark.note:
            info += "\n" + t("viewer.copy_info_note", note=bookmark.note)

        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(info)
        self.status_bar.showMessage(t("viewer.copied_to_clipboard"))

    def _edit_bookmark_note(self, item: QListWidgetItem):
        """编辑书签备注"""
        index = item.data(Qt.ItemDataRole.UserRole)
        all_bookmarks = self._bookmark_mgr.get_all()
        if not (0 <= index < len(all_bookmarks)):
            return
        bookmark = all_bookmarks[index]

        note, ok = QInputDialog.getText(
            self,
            t("viewer.edit_note_title"),
            t("viewer.edit_note_label"),
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
            self.status_bar.showMessage(t("viewer.note_updated"))

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
        type_labels = {"transcript": t("viewer.type_transcript"), "summary": t("viewer.type_summary")}
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
                t("viewer.bookmark_count_showing", showing=total_visible, total=total)
            )
        else:
            self._bookmark_count_label.setText(
                t("viewer.bookmark_count", count=total)
            )

    def _load_bookmarks(self):
        """加载书签"""
        self._refresh_bookmark_list()

    def _save_bookmarks(self):
        """保存书签 — BookmarkManager 的 add/remove/clear 已自动持久化，此方法保留兼容性"""
        pass

    # ─── 背景图片 ────────────────────────────────────────────

    def _load_bg_settings(self) -> None:
        """从配置加载背景图片设置"""
        try:
            settings = Settings()
            path = settings.get("app.result_image_path", "")
            if path:
                p = Path(path)
                if not p.is_absolute():
                    p = _get_base_dir() / path
                if p.exists():
                    self._bg_pixmap = QPixmap(str(p))
                    self._bg_image_path = str(p)
                else:
                    self._bg_pixmap = None
                    self._bg_image_path = ""
            else:
                self._bg_pixmap = None
                self._bg_image_path = ""

            opacity_int = settings.get_int(
                "app.result_transparency", 100
            )
            self._bg_opacity = max(0.0, min(1.0, opacity_int / 255.0))
        except Exception:
            self._bg_pixmap = None
            self._bg_image_path = ""
            self._bg_opacity = 0.4

        if hasattr(self, "_bg_content"):
            self._bg_content.set_bg_pixmap(self._bg_pixmap)
            self._bg_content.set_bg_opacity(self._bg_opacity)
        self._apply_bg_transparency()

    def _save_bg_config(self) -> None:
        """保存背景图片配置到 config.ini"""
        try:
            settings = Settings()
            if self._bg_image_path:
                p = Path(self._bg_image_path)
                base = _get_base_dir()
                try:
                    rel = p.relative_to(base)
                    settings.set("app.result_image_path", str(rel))
                except ValueError:
                    settings.set("app.result_image_path", str(p))
            else:
                settings.set("app.result_image_path", "")

            opacity_int = round(self._bg_opacity * 255)
            settings.set("app.result_transparency", str(opacity_int))
            settings.save()
        except Exception as e:
            logger.warning("保存背景图片配置失败: %s", e)

    def _change_bg_image(self) -> None:
        """通过资源管理器选择并更换背景图片"""
        initial_dir = (
            self._bg_image_path if self._bg_image_path else str(_get_base_dir())
        )
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            t("viewer.choose_bg_image"),
            initial_dir,
            t("viewer.image_filter"),
        )
        if not file_path:
            return

        pixmap = QPixmap(file_path)
        if pixmap.isNull():
            QMessageBox.warning(self, t("common.hint"), t("viewer.load_image_failed"))
            return

        self._bg_pixmap = pixmap
        self._bg_image_path = file_path
        if hasattr(self, "_bg_content"):
            self._bg_content.set_bg_pixmap(self._bg_pixmap)
        self._apply_bg_transparency()
        self._save_bg_config()
        self.update()
        self.status_bar.showMessage(t("viewer.bg_changed", name=Path(file_path).name))

    def _clear_bg_image(self) -> None:
        """清除背景图片"""
        self._bg_pixmap = None
        self._bg_image_path = ""
        if hasattr(self, "_bg_content"):
            self._bg_content.set_bg_pixmap(None)
        self._apply_bg_transparency()
        self._save_bg_config()
        self.update()
        self.status_bar.showMessage(t("viewer.bg_cleared"))

    def _adjust_bg_transparency(self) -> None:
        """弹出输入框修改背景透明度 (0~255)"""
        current_val = round(self._bg_opacity * 255)
        value, ok = QInputDialog.getInt(
            self,
            t("viewer.bg_transparency_title"),
            t("viewer.bg_transparency_label"),
            current_val,
            0,
            255,
            1,
        )
        if ok:
            self._bg_opacity = max(0.0, min(1.0, value / 255.0))
            if hasattr(self, "_bg_content"):
                self._bg_content.set_bg_opacity(self._bg_opacity)
            self._save_bg_config()
            self.update()
            self.status_bar.showMessage(t("viewer.bg_opacity_set", value=value))

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

        elif key == Qt.Key.Key_F11:
            self._toggle_fullscreen()

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
