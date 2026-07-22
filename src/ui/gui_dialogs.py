"""GUI 对话框组件"""

import os
import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QClipboard, QCursor
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.config.settings import Settings
from src.i18n import available_languages, language_meta, t
from src.ui.summarization_tab import SummarizationTab


def _format_file_size(size_bytes: int) -> str:
    """将字节数格式化为可读的文件大小字符串"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


class _SortTreeWidgetItem(QTreeWidgetItem):
    """支持按数值排序的树节点（大小列按字节数排序）"""

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        col = self.treeWidget().sortColumn()
        if col == 2:
            a = self.data(0, Qt.ItemDataRole.UserRole + 1)
            b = other.data(0, Qt.ItemDataRole.UserRole + 1)
            if a is not None and b is not None:
                return int(a) < int(b)
        return self.text(col) < other.text(col)


class VideoSelectionDialog(QDialog):
    """音视频文件选择对话框 —— 树形视图展示文件，支持按类型/后缀/大小组合筛选和勾选。"""

    def __init__(
        self,
        file_metas: list[tuple[str, int]],
        parent=None,
        folder: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self._file_metas = file_metas
        self._paths = [Path(p) for p, _ in file_metas]
        self._path_to_size: dict[str, int] = dict(file_metas)
        self._input_folder = folder

        settings = Settings()
        self._video_exts: set[str] = set(
            ext.lower()
            for ext in settings.get_list("preprocessing.supported_video_formats")
        )
        self._audio_exts: set[str] = set(
            ext.lower()
            for ext in settings.get_list("preprocessing.supported_audio_formats")
        )
        self._common_path: Optional[str] = None
        self._max_depth: int = 0
        self._leaf_items: list[QTreeWidgetItem] = []

        self._init_ui()

    _STYLE_SHEET = """
        QDialog {
            background-color: #f5f6f8;
            font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
            font-size: 13px;
        }
        QLabel { color: #34495e; }

        #_info_bar {
            background-color: #eaf2fb;
            border: 1px solid #cfe0f5;
            border-radius: 8px;
            padding: 10px 14px;
            color: #2c3e50;
            font-size: 13px;
        }

        #_toolbar {
            background-color: #ffffff;
            border: 1px solid #dfe3e8;
            border-radius: 8px;
            padding: 10px 12px;
        }
        #_toolbar QLabel {
            color: #5a6b7b;
            font-size: 12px;
            padding-right: 2px;
        }

        QTreeWidget {
            background-color: #ffffff;
            border: 1px solid #dfe3e8;
            border-radius: 8px;
            font-size: 13px;
            padding: 4px;
        }
        QTreeWidget::item {
            padding: 4px 2px;
            border-radius: 4px;
        }
        QTreeWidget::item:hover {
            background-color: #f0f5fb;
        }
        QTreeWidget::item:selected {
            background-color: #dbe9f9;
            color: #1f3a5f;
        }
        QTreeWidget::branch:closed:has-children {
            image: url(assets/tree_closed.png);
        }
        QTreeWidget::branch:open:has-children {
            image: url(assets/tree_open.png);
        }
        QHeaderView::section {
            background-color: #eef1f5;
            color: #2c3e50;
            border: none;
            border-bottom: 1px solid #dfe3e8;
            padding: 8px 10px;
            font-weight: 600;
        }
        QHeaderView::section:hover {
            background-color: #e3e9f1;
            cursor: pointer;
        }

        QLineEdit, QComboBox {
            border: 1px solid #ced4da;
            border-radius: 6px;
            padding: 5px 8px;
            background-color: #ffffff;
            min-height: 22px;
            color: #2c3e50;
        }
        QLineEdit:focus, QComboBox:focus {
            border: 1px solid #4a90d9;
        }
        QLineEdit:hover, QComboBox:hover {
            border: 1px solid #aac4e4;
        }
        QLineEdit::clear-button {
            subcontrol-origin: padding;
            subcontrol-position: center right;
            padding-right: 4px;
        }
        QComboBox QAbstractItemView {
            border: 1px solid #ced4da;
            border-radius: 6px;
            selection-background-color: #4a90d9;
        }
        QComboBox::drop-down {
            width: 24px;
            border-left: none;
            subcontrol-origin: padding;
            subcontrol-position: center right;
            padding-right: 4px;
        }
        QComboBox::down-arrow {
            image: url(assets/arrow_down.png);
            width: 16px;
            height: 16px;
        }

        QPushButton {
            background-color: #4a90d9;
            color: #ffffff;
            border: none;
            border-radius: 6px;
            padding: 6px 16px;
            min-height: 22px;
        }
        QPushButton:hover { background-color: #357abd; }
        QPushButton:pressed { background-color: #2c639b; }

        QPushButton#_secondary_btn {
            background-color: #ffffff;
            color: #4a90d9;
            border: 1px solid #4a90d9;
        }
        QPushButton#_secondary_btn:hover { background-color: #eaf2fb; }

        QPushButton#_ghost_btn {
            background-color: #eef1f5;
            color: #5a6b7b;
            border: 1px solid #dfe3e8;
        }
        QPushButton#_ghost_btn:hover { background-color: #e3e9f1; }

        QCheckBox {
            color: #34495e;
            spacing: 6px;
        }
        QCheckBox::indicator {
            width: 18px;
            height: 18px;
            border: 1px solid #b7c0cc;
            border-radius: 4px;
            background-color: #ffffff;
        }
        QCheckBox::indicator:checked {
            image: url(assets/check.png);
            background-color: #4a90d9;
            border: 1px solid #4a90d9;
        }

        QSpinBox {
            border: 1px solid #ced4da;
            border-radius: 6px;
            padding: 4px 6px;
            background-color: #ffffff;
            min-height: 22px;
            color: #2c3e50;
        }
        QSpinBox:focus { border: 1px solid #4a90d9; }
        QSpinBox::up-button, QSpinBox::down-button {
            subcontrol-origin: border;
            width: 16px;
            border: none;
        }
        QSpinBox::up-arrow { image: url(assets/arrow_up.png); width: 10px; height: 10px; }
        QSpinBox::down-arrow { image: url(assets/arrow_down.png); width: 10px; height: 10px; }

        #_bottom_bar {
            background-color: #ffffff;
            border: 1px solid #dfe3e8;
            border-radius: 8px;
            padding: 10px 12px;
        }
    """

    def _init_ui(self) -> None:
        self.setWindowTitle(t("dialogs.file_select.title"))
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self.resize(1000, 600)
        self.setStyleSheet(self._STYLE_SHEET)
        self.setMinimumSize(760, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        self._info_label = QLabel(
            t("dialogs.file_select.info_prefix", count=len(self._file_metas))
        )
        self._info_label.setObjectName("_info_bar")
        self._info_label.setTextFormat(Qt.TextFormat.PlainText)
        self._info_label.setMinimumHeight(40)
        layout.addWidget(self._info_label)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)
        toolbar.setObjectName("_toolbar")
        toolbar_widget = QWidget()
        toolbar_widget.setObjectName("_toolbar")
        toolbar_widget.setLayout(toolbar)

        toolbar.addWidget(QLabel(t("dialogs.file_select.type_label")))
        self._file_type_combo = QComboBox()
        self._file_type_combo.addItems([t("dialogs.file_select.type_all"), t("dialogs.file_select.type_video"), t("dialogs.file_select.type_audio")])
        self._file_type_combo.setMinimumWidth(90)
        self._file_type_combo.currentIndexChanged.connect(self._apply_filters)
        toolbar.addWidget(self._file_type_combo)
        toolbar.addSpacing(8)
        toolbar.addWidget(QLabel(t("dialogs.file_select.suffix_label")))
        self._suffix_combo = QComboBox()
        self._suffix_combo.setMinimumWidth(80)
        self._suffix_combo.currentIndexChanged.connect(self._apply_filters)
        toolbar.addWidget(self._suffix_combo)
        toolbar.addSpacing(8)
        toolbar.addWidget(QLabel(t("dialogs.file_select.size_label")))
        self._size_combo = QComboBox()
        self._size_combo.setMinimumWidth(120)
        self._size_tiers = [
            (t("dialogs.file_select.type_all"), 0, None),
            ("< 10 MB", 0, 10 * 1024 * 1024),
            ("10 - 100 MB", 10 * 1024 * 1024, 100 * 1024 * 1024),
            ("100 MB - 1 GB", 100 * 1024 * 1024, 1024 * 1024 * 1024),
            ("1 - 5 GB", 1024 * 1024 * 1024, 5 * 1024 * 1024 * 1024),
            ("> 5 GB", 5 * 1024 * 1024 * 1024, None),
        ]
        for label, _, _ in self._size_tiers:
            self._size_combo.addItem(label)
        self._size_combo.currentIndexChanged.connect(self._apply_filters)
        toolbar.addWidget(self._size_combo)
        toolbar.addStretch()
        toolbar.addWidget(QLabel(t("dialogs.file_select.search_label")))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(t("dialogs.file_select.search_placeholder"))
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMinimumWidth(180)
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filters)
        self._search_edit.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self._search_edit)
        layout.addWidget(toolbar_widget)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels([t("dialogs.file_select.header_filename"), t("dialogs.file_select.header_type"), t("dialogs.file_select.header_size"), t("dialogs.file_select.header_output")])
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self._tree.setAnimated(True)
        self._tree.setSortingEnabled(False)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.setAlternatingRowColors(False)
        self._tree.header().setMinimumSectionSize(50)
        self._tree.header().setStretchLastSection(False)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._tree.header().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self._tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self._tree.header().resizeSection(3, 500)
        self._tree.header().setSortIndicatorShown(True)
        self._tree.header().setSectionsClickable(True)
        self._sort_order: dict[int, Qt.SortOrder] = {}
        self._tree.header().sectionClicked.connect(self._on_header_clicked)
        self._tree.itemChanged.connect(self._update_info_label)
        layout.addWidget(self._tree, 1)

        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(8)
        bottom_widget = QWidget()
        bottom_widget.setObjectName("_bottom_bar")
        bottom_widget.setLayout(bottom_layout)

        select_all_btn = QPushButton(t("common.select_all"))
        select_all_btn.setObjectName("_ghost_btn")
        select_all_btn.clicked.connect(self._select_all)
        bottom_layout.addWidget(select_all_btn)
        deselect_all_btn = QPushButton(t("common.deselect_all"))
        deselect_all_btn.setObjectName("_ghost_btn")
        deselect_all_btn.clicked.connect(self._deselect_all)
        bottom_layout.addWidget(deselect_all_btn)
        invert_btn = QPushButton(t("common.invert"))
        invert_btn.setObjectName("_ghost_btn")
        invert_btn.clicked.connect(self._invert_selection)
        bottom_layout.addWidget(invert_btn)
        expand_all_btn = QPushButton(t("common.expand_all"))
        expand_all_btn.setObjectName("_ghost_btn")
        expand_all_btn.clicked.connect(self._tree.expandAll)
        bottom_layout.addWidget(expand_all_btn)
        collapse_all_btn = QPushButton(t("common.collapse_all"))
        collapse_all_btn.setObjectName("_ghost_btn")
        collapse_all_btn.clicked.connect(self._tree.collapseAll)
        bottom_layout.addWidget(collapse_all_btn)
        bottom_layout.addStretch()

        self._mirror_checkbox = QCheckBox(t("dialogs.file_select.mirror_cb"))
        self._mirror_checkbox.setToolTip(t("dialogs.file_select.mirror_tooltip"))
        self._mirror_checkbox.toggled.connect(self._on_mirror_changed)
        bottom_layout.addWidget(self._mirror_checkbox)

        bottom_layout.addWidget(QLabel(t("dialogs.file_select.depth_label")))
        self._depth_spin = QSpinBox()
        self._depth_spin.setRange(1, 10)
        default_depth = Settings().get_int("output.mirror_depth", 1)
        self._depth_spin.setValue(default_depth)
        self._depth_spin.setEnabled(False)
        self._depth_spin.valueChanged.connect(self._on_depth_changed)
        bottom_layout.addWidget(self._depth_spin)

        ok_btn = QPushButton(t("common.ok"))
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        bottom_layout.addWidget(ok_btn)
        cancel_btn = QPushButton(t("common.cancel"))
        cancel_btn.setObjectName("_secondary_btn")
        cancel_btn.clicked.connect(self.reject)
        bottom_layout.addWidget(cancel_btn)
        layout.addWidget(bottom_widget)

        QTimer.singleShot(0, self._deferred_populate)

    def _deferred_populate(self) -> None:
        self._tree.setSortingEnabled(False)
        self._tree.setUpdatesEnabled(False)
        self._build_tree()
        model = self._tree.model()
        _ALIGN_LEFT = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for _col in range(4):
            model.setHeaderData(
                _col,
                Qt.Orientation.Horizontal,
                _ALIGN_LEFT,
                Qt.ItemDataRole.TextAlignmentRole,
            )
        self._tree.setUpdatesEnabled(True)
        self._tree.expandAll()
        self._update_info_label()
        self._apply_mirror_defaults()

    def _build_tree(self) -> None:
        paths = self._paths
        if not paths:
            return

        try:
            common = Path(os.path.commonpath(paths))
        except ValueError:
            common = None
        self._common_path = str(common) if common else None

        _t_video = t("dialogs.file_select.type_video_short")
        _t_audio = t("dialogs.file_select.type_audio_short")
        _t_av = t("dialogs.file_select.type_av_short")
        self._type_video_short = _t_video
        self._type_audio_short = _t_audio
        self._type_av_short = _t_av
        suffix_map: dict[str, str] = {}
        for p in paths:
            ext = p.suffix.lower()
            if ext in self._video_exts:
                suffix_map[ext] = _t_video
            elif ext in self._audio_exts:
                suffix_map[ext] = _t_audio
            else:
                suffix_map[ext] = _t_av

        rel_pairs: list[tuple[Path, Path]] = []
        for p in paths:
            if common:
                try:
                    rel = p.relative_to(common)
                except ValueError:
                    rel = p
            else:
                rel = p
            rel_pairs.append((rel, p))

        need_folder = any(rel.parent != Path(".") for rel, _ in rel_pairs)

        max_depth = 0
        for rel, _ in rel_pairs:
            depth = len(rel.parent.parts)
            if depth > max_depth:
                max_depth = depth
        self._max_depth = max_depth

        if not need_folder:
            batch: list[QTreeWidgetItem] = []
            for _, abs_p in sorted(rel_pairs, key=lambda x: x[0].name.lower()):
                item = self._make_file_item(abs_p, suffix_map, ())
                batch.append(item)
                self._leaf_items.append(item)
            self._tree.addTopLevelItems(batch)
        else:
            folder_nodes: dict[tuple[str, ...], QTreeWidgetItem] = {}
            for rel, abs_p in rel_pairs:
                parts = rel.parent.parts
                if not parts:
                    item = self._make_file_item(abs_p, suffix_map, ())
                    self._tree.addTopLevelItem(item)
                    self._leaf_items.append(item)
                    continue
                for i in range(len(parts)):
                    sub_key = parts[: i + 1]
                    if sub_key not in folder_nodes:
                        fi = QTreeWidgetItem([f"📁 {parts[i]}", "", "", ""])
                        fi.setFlags(
                            fi.flags()
                            | Qt.ItemFlag.ItemIsAutoTristate
                            | Qt.ItemFlag.ItemIsUserCheckable
                        )
                        fi.setCheckState(0, Qt.CheckState.Checked)
                        fi.setData(0, Qt.ItemDataRole.UserRole + 2, sub_key)
                        if i == 0:
                            self._tree.addTopLevelItem(fi)
                        else:
                            folder_nodes[parts[:i]].addChild(fi)
                        folder_nodes[sub_key] = fi
                self._add_file_item(folder_nodes[parts], abs_p, suffix_map, parts)

        present_suffixes = {p.suffix.lower() for p in paths}
        self._suffix_combo.clear()
        self._suffix_combo.addItem(t("dialogs.file_select.type_all"))
        for ext in sorted(present_suffixes):
            self._suffix_combo.addItem(ext)

    def _make_file_item(
        self,
        abs_p: Path,
        suffix_map: dict,
        rel_parts: tuple[str, ...] = (),
    ) -> _SortTreeWidgetItem:
        abs_path_str = str(abs_p)
        ext = abs_p.suffix.lower()
        file_type = suffix_map.get(ext, self._type_av_short)
        icon = "🎬" if file_type == self._type_video_short else "🎵" if file_type == self._type_audio_short else "📄"
        size_bytes = self._path_to_size.get(abs_path_str, -1)
        size_str = _format_file_size(size_bytes) if size_bytes >= 0 else "-"
        name_stem = abs_p.stem.lower()

        item = _SortTreeWidgetItem([f"{icon} {abs_p.name}", file_type, size_str, ""])
        item.setData(0, Qt.ItemDataRole.UserRole, abs_path_str)
        item.setData(0, Qt.ItemDataRole.UserRole + 1, size_bytes)
        item.setData(0, Qt.ItemDataRole.UserRole + 2, rel_parts)
        item.setData(0, Qt.ItemDataRole.UserRole + 3, ext)
        item.setData(0, Qt.ItemDataRole.UserRole + 4, name_stem)
        item.setCheckState(0, Qt.CheckState.Checked)
        for _col in range(1, 4):
            item.setTextAlignment(
                _col, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
        return item

    def _add_file_item(
        self,
        parent: QTreeWidgetItem,
        abs_p: Path,
        suffix_map: dict,
        rel_parts: tuple[str, ...] = (),
    ) -> None:
        item = self._make_file_item(abs_p, suffix_map, rel_parts)
        parent.addChild(item)
        self._leaf_items.append(item)

    def _iter_leaves(self):
        yield from self._leaf_items

    def _on_header_clicked(self, logical_index: int) -> None:
        cur_order = self._sort_order.get(logical_index, Qt.SortOrder.AscendingOrder)
        self._tree.sortByColumn(logical_index, cur_order)
        self._sort_order[logical_index] = (
            Qt.SortOrder.DescendingOrder
            if cur_order == Qt.SortOrder.AscendingOrder
            else Qt.SortOrder.AscendingOrder
        )

    def _update_info_label(self) -> None:
        total = len(self._leaf_items)
        checked = 0
        checked_size = 0
        for item in self._leaf_items:
            if item.checkState(0) == Qt.CheckState.Checked:
                checked += 1
                size_bytes: int = item.data(0, Qt.ItemDataRole.UserRole + 1)
                if size_bytes > 0:
                    checked_size += size_bytes
        size_str = _format_file_size(checked_size) if checked_size > 0 else "0 B"
        self._info_label.setText(
            t("dialogs.file_select.info_selected", total=total, checked=checked, size=size_str)
        )

    def _apply_filters(self) -> None:
        if not self._leaf_items:
            return

        file_type_idx = self._file_type_combo.currentIndex()
        input_exts: Optional[set[str]] = None
        if file_type_idx == 1:
            input_exts = self._video_exts
        elif file_type_idx == 2:
            input_exts = self._audio_exts

        suffix_target = self._suffix_combo.currentText().strip().lower()

        size_idx = self._size_combo.currentIndex()
        size_lo, size_hi = 0, None
        if 0 <= size_idx < len(self._size_tiers):
            _, size_lo, size_hi = self._size_tiers[size_idx]

        keyword = self._search_edit.text().strip().lower()

        self._tree.blockSignals(True)
        for item in self._iter_leaves():
            ext = item.data(0, Qt.ItemDataRole.UserRole + 3)
            name = item.data(0, Qt.ItemDataRole.UserRole + 4)
            size_bytes: int = item.data(0, Qt.ItemDataRole.UserRole + 1)

            match = True
            if input_exts is not None and ext not in input_exts:
                match = False
            if suffix_target and suffix_target != t("dialogs.file_select.type_all") and ext != suffix_target:
                match = False
            if size_idx > 0:
                if size_bytes < 0:
                    match = False
                else:
                    if size_bytes < size_lo:
                        match = False
                    if size_hi is not None and size_bytes >= size_hi:
                        match = False
            if keyword and keyword not in name:
                match = False

            item.setCheckState(
                0, Qt.CheckState.Checked if match else Qt.CheckState.Unchecked
            )
        self._tree.blockSignals(False)
        self._update_info_label()

    def _on_search_changed(self, _text: str) -> None:
        self._search_timer.start(150)

    def _select_all(self) -> None:
        self._tree.blockSignals(True)
        for item in self._iter_leaves():
            item.setCheckState(0, Qt.CheckState.Checked)
        self._tree.blockSignals(False)
        self._update_info_label()

    def _deselect_all(self) -> None:
        self._tree.blockSignals(True)
        for item in self._iter_leaves():
            item.setCheckState(0, Qt.CheckState.Unchecked)
        self._tree.blockSignals(False)
        self._update_info_label()

    def _invert_selection(self) -> None:
        self._tree.blockSignals(True)
        for item in self._iter_leaves():
            if item.checkState(0) == Qt.CheckState.Checked:
                item.setCheckState(0, Qt.CheckState.Unchecked)
            else:
                item.setCheckState(0, Qt.CheckState.Checked)
        self._tree.blockSignals(False)
        self._update_info_label()

    def _show_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return
        file_path = item.data(0, Qt.ItemDataRole.UserRole)
        if not file_path:
            return

        menu = QMenu(self)
        open_dir_action = menu.addAction(t("dialogs.file_select.context_opendir"))
        copy_path_action = menu.addAction(t("dialogs.file_select.context_copypath"))
        action = menu.exec(QCursor.pos())
        if action == open_dir_action:
            folder = str(Path(file_path).parent)
            if os.name == "nt":
                subprocess.Popen(["explorer", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        elif action == copy_path_action:
            QApplication.clipboard().setText(file_path)

    def get_selected_files(self) -> list[str]:
        selected: list[str] = []
        for item in self._iter_leaves():
            if item.checkState(0) == Qt.CheckState.Checked:
                selected.append(item.data(0, Qt.ItemDataRole.UserRole))
        return selected

    def _apply_mirror_defaults(self) -> None:
        settings = Settings()
        if self._max_depth == 0:
            self._mirror_checkbox.setChecked(False)
            self._mirror_checkbox.setEnabled(False)
            self._depth_spin.setEnabled(False)
            for item in self._iter_leaves():
                item.setText(3, t("dialogs.file_select.mirror_disabled"))
        else:
            saved_enabled = settings.get_bool("output.mirror_enabled", True)
            self._mirror_checkbox.blockSignals(True)
            self._mirror_checkbox.setChecked(saved_enabled)
            self._mirror_checkbox.blockSignals(False)
            default_depth = settings.get_int("output.mirror_depth", 1)
            clamped = min(default_depth, self._max_depth)
            self._depth_spin.blockSignals(True)
            self._depth_spin.setRange(1, self._max_depth)
            self._depth_spin.setValue(clamped)
            self._depth_spin.blockSignals(False)
            if saved_enabled:
                self._depth_spin.setEnabled(True)
                self._update_mirror_column(clamped)
            else:
                self._depth_spin.setEnabled(False)
                for item in self._iter_leaves():
                    item.setText(3, t("dialogs.file_select.mirror_disabled"))

    def _on_mirror_changed(self, checked: bool) -> None:
        settings = Settings()
        settings.set("output.mirror_enabled", str(checked))
        settings.save()
        if checked:
            depth = self._depth_spin.value()
            self._depth_spin.setEnabled(True)
            self._update_mirror_column(depth)
        else:
            self._depth_spin.setEnabled(False)
            self._tree.blockSignals(True)
            for item in self._iter_leaves():
                item.setText(3, t("dialogs.file_select.mirror_disabled"))
            self._tree.blockSignals(False)

    def _on_depth_changed(self, value: int) -> None:
        self._update_mirror_column(value)
        settings = Settings()
        settings.set("output.mirror_depth", str(value))
        settings.save()

    def _update_mirror_column(self, depth: int) -> None:
        self._tree.blockSignals(True)
        for item in self._iter_leaves():
            rel_parts = item.data(0, Qt.ItemDataRole.UserRole + 2)
            if rel_parts and len(rel_parts) > 0:
                truncated = rel_parts[:depth]
                text = str(Path(*truncated))
                item.setText(3, text)
                item.setToolTip(3, text)
            else:
                item.setText(3, t("dialogs.file_select.root_dir"))
                item.setToolTip(3, t("dialogs.file_select.root_dir"))
        self._tree.blockSignals(False)

    def get_mirror_subdirs(self) -> bool:
        return self._mirror_checkbox.isChecked()

    def get_mirror_depth(self) -> int:
        return self._depth_spin.value()

    def get_input_folder(self) -> Optional[str]:
        return self._common_path


_SECTION_LABEL_KEYS: dict[str, str] = {
    "app": "config.section.app",
    "transcription": "config.section.transcription",
    "summarization": "config.section.summarization",
    "preprocessing": "config.section.preprocessing",
    "output": "config.section.output",
    "paths": "config.section.paths",
    "tools": "config.section.tools",
    "text_processing": "config.section.text_processing",
    "voice_to_text": "config.section.voice_to_text",
}

_KEY_LABEL_KEYS: dict[str, str] = {
    "app.name": "config.label.app.name",
    "app.version": "config.label.app.version",
    "app.log_level": "config.label.app.log_level",
    "app.incremental_mode": "config.label.app.incremental_mode",
    "app.is_check_model_file": "config.label.app.is_check_model_file",
    "app.is_check_dll_file": "config.label.app.is_check_dll_file",
    "app.proxy": "config.label.app.proxy",
    "app.ui_language": "config.label.app.ui_language",
    "app.result_image_path": "config.label.app.result_image_path",
    "app.result_transparency": "config.label.app.result_transparency",
    "app.main_image_path": "config.label.app.main_image_path",
    "app.main_transparency": "config.label.app.main_transparency",
    "transcription.model_path": "config.label.transcription.model_path",
    "transcription.device": "config.label.transcription.device",
    "transcription.language": "config.label.transcription.language",
    "transcription.beam_size": "config.label.transcription.beam_size",
    "transcription.best_of": "config.label.transcription.best_of",
    "transcription.temperature": "config.label.transcription.temperature",
    "transcription.compute_type": "config.label.transcription.compute_type",
    "transcription.num_workers": "config.label.transcription.num_workers",
    "transcription.vad_filter": "config.label.transcription.vad_filter",
    "transcription.condition_on_previous_text": "config.label.transcription.condition_on_previous_text",
    "transcription.word_timestamps": "config.label.transcription.word_timestamps",
    "transcription.compression_ratio_threshold": "config.label.transcription.compression_ratio_threshold",
    "transcription.log_prob_threshold": "config.label.transcription.log_prob_threshold",
    "transcription.no_speech_threshold": "config.label.transcription.no_speech_threshold",
    "transcription.repetition_penalty": "config.label.transcription.repetition_penalty",
    "transcription.no_repeat_ngram_size": "config.label.transcription.no_repeat_ngram_size",
    "transcription.vad_threshold": "config.label.transcription.vad_threshold",
    "transcription.vad_min_silence_ms": "config.label.transcription.vad_min_silence_ms",
    "transcription.vad_speech_pad_ms": "config.label.transcription.vad_speech_pad_ms",
    "transcription.vad_max_speech_s": "config.label.transcription.vad_max_speech_s",
    "preprocessing.audio_sample_rate": "config.label.preprocessing.audio_sample_rate",
    "preprocessing.audio_channels": "config.label.preprocessing.audio_channels",
    "preprocessing.max_chunk_duration": "config.label.preprocessing.max_chunk_duration",
    "preprocessing.supported_video_formats": "config.label.preprocessing.supported_video_formats",
    "preprocessing.supported_audio_formats": "config.label.preprocessing.supported_audio_formats",
    "output.output_dir": "config.label.output.output_dir",
    "output.transcript_format": "config.label.output.transcript_format",
    "output.summary_format": "config.label.output.summary_format",
    "output.mirror_enabled": "config.label.output.mirror_enabled",
    "output.mirror_depth": "config.label.output.mirror_depth",
    "paths.models_dir": "config.label.paths.models_dir",
    "paths.logs_dir": "config.label.paths.logs_dir",
    "paths.video_dir": "config.label.paths.video_dir",
    "text_processing.max_gap": "config.label.text_processing.max_gap",
    "text_processing.min_length": "config.label.text_processing.min_length",
    "text_processing.filler_words": "config.label.text_processing.filler_words",
    "voice_to_text.voice_dir": "config.label.voice_to_text.voice_dir",
    "voice_to_text.summary_dir": "config.label.voice_to_text.summary_dir",
    "voice_to_text.realtime_auto_send_interval": "config.label.voice_to_text.realtime_auto_send_interval",
    "voice_to_text.model_path": "config.label.voice_to_text.model_path",
    "voice_to_text.device": "config.label.voice_to_text.device",
    "voice_to_text.compute_type": "config.label.voice_to_text.compute_type",
    "voice_to_text.language": "config.label.voice_to_text.language",
    "voice_to_text.num_workers": "config.label.voice_to_text.num_workers",
    "voice_to_text.audio_sample_rate": "config.label.voice_to_text.audio_sample_rate",
    "voice_to_text.audio_channels": "config.label.voice_to_text.audio_channels",
    "voice_to_text.vad_filter": "config.label.voice_to_text.vad_filter",
    "voice_to_text.vad_threshold": "config.label.voice_to_text.vad_threshold",
    "voice_to_text.vad_min_silence_ms": "config.label.voice_to_text.vad_min_silence_ms",
    "voice_to_text.vad_speech_pad_ms": "config.label.voice_to_text.vad_speech_pad_ms",
    "voice_to_text.vad_max_speech_s": "config.label.voice_to_text.vad_max_speech_s",
    "voice_to_text.initial_prompt": "config.label.voice_to_text.initial_prompt",
    "voice_to_text.bg_image_path": "config.label.voice_to_text.bg_image_path",
    "voice_to_text.bg_transparency": "config.label.voice_to_text.bg_transparency",
    "voice_to_text.vad_endpoint_detection": "config.label.voice_to_text.vad_endpoint_detection",
    "voice_to_text.vad_energy_threshold": "config.label.voice_to_text.vad_energy_threshold",
    "voice_to_text.vad_silence_frames": "config.label.voice_to_text.vad_silence_frames",
    "voice_to_text.vad_min_speech_frames": "config.label.voice_to_text.vad_min_speech_frames",
    "voice_to_text.vad_calibration_frames": "config.label.voice_to_text.vad_calibration_frames",
    "voice_to_text.context_max_chars": "config.label.voice_to_text.context_max_chars",
}

_KEY_TOOLTIP_KEYS: dict[str, str] = {
    "app.log_level": "config.tooltip.app.log_level",
    "app.incremental_mode": "config.tooltip.app.incremental_mode",
    "app.is_check_model_file": "config.tooltip.app.is_check_model_file",
    "app.is_check_dll_file": "config.tooltip.app.is_check_dll_file",
    "app.proxy": "config.tooltip.app.proxy",
    "app.ui_language": "config.tooltip.app.ui_language",
    "transcription.model_path": "config.tooltip.transcription.model_path",
    "transcription.device": "config.tooltip.transcription.device",
    "transcription.language": "config.tooltip.transcription.language",
    "transcription.beam_size": "config.tooltip.transcription.beam_size",
    "transcription.temperature": "config.tooltip.transcription.temperature",
    "transcription.condition_on_previous_text": "config.tooltip.transcription.condition_on_previous_text",
    "transcription.best_of": "config.tooltip.transcription.best_of",
    "transcription.compute_type": "config.tooltip.transcription.compute_type",
    "transcription.num_workers": "config.tooltip.transcription.num_workers",
    "transcription.vad_filter": "config.tooltip.transcription.vad_filter",
    "transcription.word_timestamps": "config.tooltip.transcription.word_timestamps",
    "transcription.compression_ratio_threshold": "config.tooltip.transcription.compression_ratio_threshold",
    "transcription.log_prob_threshold": "config.tooltip.transcription.log_prob_threshold",
    "transcription.no_speech_threshold": "config.tooltip.transcription.no_speech_threshold",
    "transcription.repetition_penalty": "config.tooltip.transcription.repetition_penalty",
    "transcription.no_repeat_ngram_size": "config.tooltip.transcription.no_repeat_ngram_size",
    "transcription.vad_threshold": "config.tooltip.transcription.vad_threshold",
    "transcription.vad_min_silence_ms": "config.tooltip.transcription.vad_min_silence_ms",
    "transcription.vad_speech_pad_ms": "config.tooltip.transcription.vad_speech_pad_ms",
    "transcription.vad_max_speech_s": "config.tooltip.transcription.vad_max_speech_s",
    "preprocessing.audio_sample_rate": "config.tooltip.preprocessing.audio_sample_rate",
    "preprocessing.audio_channels": "config.tooltip.preprocessing.audio_channels",
    "preprocessing.max_chunk_duration": "config.tooltip.preprocessing.max_chunk_duration",
    "preprocessing.supported_video_formats": "config.tooltip.preprocessing.supported_video_formats",
    "preprocessing.supported_audio_formats": "config.tooltip.preprocessing.supported_audio_formats",
    "output.output_dir": "config.tooltip.output.output_dir",
    "output.transcript_format": "config.tooltip.output.transcript_format",
    "output.summary_format": "config.tooltip.output.summary_format",
    "output.mirror_enabled": "config.tooltip.output.mirror_enabled",
    "output.mirror_depth": "config.tooltip.output.mirror_depth",
    "paths.models_dir": "config.tooltip.paths.models_dir",
    "paths.logs_dir": "config.tooltip.paths.logs_dir",
    "paths.video_dir": "config.tooltip.paths.video_dir",
    "text_processing.max_gap": "config.tooltip.text_processing.max_gap",
    "text_processing.min_length": "config.tooltip.text_processing.min_length",
    "text_processing.filler_words": "config.tooltip.text_processing.filler_words",
    "voice_to_text.voice_dir": "config.tooltip.voice_to_text.voice_dir",
    "voice_to_text.summary_dir": "config.tooltip.voice_to_text.summary_dir",
    "voice_to_text.realtime_auto_send_interval": "config.tooltip.voice_to_text.realtime_auto_send_interval",
    "voice_to_text.model_path": "config.tooltip.voice_to_text.model_path",
    "voice_to_text.device": "config.tooltip.voice_to_text.device",
    "voice_to_text.compute_type": "config.tooltip.voice_to_text.compute_type",
    "voice_to_text.language": "config.tooltip.voice_to_text.language",
    "voice_to_text.num_workers": "config.tooltip.voice_to_text.num_workers",
    "voice_to_text.audio_sample_rate": "config.tooltip.voice_to_text.audio_sample_rate",
    "voice_to_text.audio_channels": "config.tooltip.voice_to_text.audio_channels",
    "voice_to_text.vad_filter": "config.tooltip.voice_to_text.vad_filter",
    "voice_to_text.vad_threshold": "config.tooltip.voice_to_text.vad_threshold",
    "voice_to_text.vad_min_silence_ms": "config.tooltip.voice_to_text.vad_min_silence_ms",
    "voice_to_text.vad_speech_pad_ms": "config.tooltip.voice_to_text.vad_speech_pad_ms",
    "voice_to_text.vad_max_speech_s": "config.tooltip.voice_to_text.vad_max_speech_s",
    "voice_to_text.initial_prompt": "config.tooltip.voice_to_text.initial_prompt",
    "voice_to_text.bg_image_path": "config.tooltip.voice_to_text.bg_image_path",
    "voice_to_text.bg_transparency": "config.tooltip.voice_to_text.bg_transparency",
    "voice_to_text.vad_endpoint_detection": "config.tooltip.voice_to_text.vad_endpoint_detection",
    "voice_to_text.vad_energy_threshold": "config.tooltip.voice_to_text.vad_energy_threshold",
    "voice_to_text.vad_silence_frames": "config.tooltip.voice_to_text.vad_silence_frames",
    "voice_to_text.vad_min_speech_frames": "config.tooltip.voice_to_text.vad_min_speech_frames",
    "voice_to_text.vad_calibration_frames": "config.tooltip.voice_to_text.vad_calibration_frames",
    "voice_to_text.context_max_chars": "config.tooltip.voice_to_text.context_max_chars",
    "app.result_image_path": "config.tooltip.app.result_image_path",
    "app.result_transparency": "config.tooltip.app.result_transparency",
    "app.main_image_path": "config.tooltip.app.main_image_path",
    "app.main_transparency": "config.tooltip.app.main_transparency",
}

_SECTION_GROUP_KEYS: dict[str, dict[str, list[str]]] = {
    "app": {
        "config.group.app.general": ["log_level", "incremental_mode", "is_check_model_file", "is_check_dll_file", "proxy", "ui_language"],
        "config.group.app.background": [
            "main_image_path",
            "main_transparency",
            "result_image_path",
            "result_transparency",
        ],
    },
    "transcription": {
        "config.group.transcription.basic": ["model_path", "device", "language", "compute_type", "num_workers"],
        "config.group.transcription.decode": ["beam_size", "best_of", "temperature"],
        "config.group.transcription.vad": [
            "vad_filter",
            "vad_threshold",
            "vad_min_silence_ms",
            "vad_speech_pad_ms",
            "vad_max_speech_s",
        ],
        "config.group.transcription.quality": [
            "condition_on_previous_text",
            "word_timestamps",
            "compression_ratio_threshold",
            "log_prob_threshold",
            "no_speech_threshold",
        ],
        "config.group.transcription.repeat": ["repetition_penalty", "no_repeat_ngram_size"],
    },
    "preprocessing": {
        "config.group.preprocessing.audio": ["audio_sample_rate", "audio_channels", "max_chunk_duration"],
        "config.group.preprocessing.format": ["supported_video_formats", "supported_audio_formats"],
    },
    "output": {
        "config.group.output.settings": ["output_dir", "transcript_format", "summary_format"],
        "config.group.output.mirror": ["mirror_enabled", "mirror_depth"],
    },
    "text_processing": {
        "config.group.text_processing.merge": ["max_gap", "min_length", "filler_words"],
    },
    "voice_to_text": {
        "config.group.voice_to_text.basic": ["voice_dir", "summary_dir", "realtime_auto_send_interval", "model_path", "device", "compute_type", "language", "num_workers"],
        "config.group.voice_to_text.audio_vad": [
            "audio_sample_rate",
            "audio_channels",
            "vad_filter",
            "vad_threshold",
            "vad_min_silence_ms",
            "vad_speech_pad_ms",
            "vad_max_speech_s",
        ],
        "config.group.voice_to_text.endpoint": [
            "vad_endpoint_detection",
            "vad_energy_threshold",
            "vad_silence_frames",
            "vad_min_speech_frames",
            "vad_calibration_frames",
            "context_max_chars",
        ],
        "config.group.voice_to_text.prompt": ["initial_prompt"],
        "config.group.voice_to_text.background": ["bg_image_path", "bg_transparency"],
    },
}

_FILE_KEYS: set[str] = {
    "app.result_image_path",
    "app.main_image_path",
    "voice_to_text.bg_image_path",
}

_BOOL_COMBO_KEYS: set[str] = {
    "app.incremental_mode",
    "app.is_check_model_file",
    "app.is_check_dll_file",
    "transcription.vad_filter",
    "transcription.condition_on_previous_text",
    "transcription.word_timestamps",
    "output.mirror_enabled",
    "voice_to_text.vad_filter",
    "voice_to_text.vad_endpoint_detection",
}

_COMBO_KEYS: set[str] = set(_BOOL_COMBO_KEYS) | {
    "app.ui_language",
    "summarization.nvidia_stream",
    "summarization.nvidia_mode",
    "app.log_level",
    "transcription.device",
    "transcription.compute_type",
    "voice_to_text.device",
    "voice_to_text.compute_type",
}

_COMBO_OPTIONS: dict[str, list[str]] = {
    key: ["common.yes", "common.no"]
    for key in _BOOL_COMBO_KEYS
}
_COMBO_OPTIONS.update(
    {
        "summarization.nvidia_stream": ["common.yes", "common.no"],
        "summarization.nvidia_mode": ["config.combo.mode_single", "config.combo.mode_multi"],
    }
)

_COMBO_VALUE_MAP: dict[str, dict[str, str]] = {
    key: {"common.yes": "True", "common.no": "False"}
    for key in _BOOL_COMBO_KEYS
}
_COMBO_VALUE_MAP.update(
    {
        "summarization.nvidia_stream": {"common.yes": "True", "common.no": "False"},
        "summarization.nvidia_mode": {"config.combo.mode_single": "single", "config.combo.mode_multi": "multi"},
    }
)

_COMBO_OPTIONS.update(
    {
        "app.log_level": ["DEBUG", "INFO", "WARNING", "ERROR"],
        "transcription.device": ["cuda", "cpu", "auto"],
        "transcription.compute_type": ["float16", "int8", "float32"],
        "voice_to_text.device": ["cuda", "cpu", "auto"],
        "voice_to_text.compute_type": ["float16", "int8", "float32"],
    }
)

_COMBO_VALUE_MAP.update(
    {
        "app.log_level": {
            "DEBUG": "DEBUG",
            "INFO": "INFO",
            "WARNING": "WARNING",
            "ERROR": "ERROR",
        },
        "transcription.device": {
            "cuda": "cuda",
            "cpu": "cpu",
            "auto": "auto",
        },
        "transcription.compute_type": {
            "float16": "float16",
            "int8": "int8",
            "float32": "float32",
        },
        "voice_to_text.device": {
            "cuda": "cuda",
            "cpu": "cpu",
            "auto": "auto",
        },
        "voice_to_text.compute_type": {
            "float16": "float16",
            "int8": "int8",
            "float32": "float32",
        },
    }
)

# ---- 界面语言选择（从 i18n 注册表动态生成） ----
_COMBO_OPTIONS["app.ui_language"] = [
    "config.combo.auto_language",  # i18n key，渲染时延迟翻译
] + [
    language_meta(code).get("name", code) for code in available_languages()
]
_COMBO_VALUE_MAP["app.ui_language"] = {
    "config.combo.auto_language": "auto",
} | {
    language_meta(code).get("name", code): code for code in available_languages()
}



class ConfigEditorDialog(QDialog):
    """配置编辑对话框 —— 按 config.ini 的 section 分 tab 展示所有配置项"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.settings = Settings()
        self._edits: dict[str, dict[str, QWidget]] = {}
        self._init_ui()

    _STYLE_SHEET = """
        QDialog {
            background-color: #f5f6f8;
            font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
            font-size: 13px;
        }
        QLabel { color: #34495e; }
        QGroupBox {
            border: 1px solid #dfe3e8;
            border-radius: 8px;
            margin-top: 16px;
            padding: 14px 12px 12px 12px;
            background-color: #ffffff;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 12px;
            padding: 0 6px;
            color: #2c3e50;
            font-weight: 600;
            background-color: #f5f6f8;
        }
        QLineEdit, QComboBox {
            border: 1px solid #ced4da;
            border-radius: 6px;
            padding: 5px 8px;
            background-color: #ffffff;
            min-height: 22px;
            color: #2c3e50;
        }
        QLineEdit:focus, QComboBox:focus {
            border: 1px solid #4a90d9;
        }
        QLineEdit:hover, QComboBox:hover {
            border: 1px solid #aac4e4;
        }
        QComboBox QAbstractItemView {
            border: 1px solid #ced4da;
            border-radius: 6px;
            selection-background-color: #4a90d9;
        }
        QComboBox::drop-down {
            width: 24px;
            border-left: none;
            subcontrol-origin: padding;
            subcontrol-position: center right;
            padding-right: 4px;
        }
        QComboBox::down-arrow {
            image: url(assets/arrow_down.png);
            width: 16px;
            height: 16px;
        }
        QPushButton {
            background-color: #4a90d9;
            color: #ffffff;
            border: none;
            border-radius: 6px;
            padding: 6px 16px;
            min-height: 22px;
        }
        QPushButton:hover { background-color: #357abd; }
        QPushButton:pressed { background-color: #2c639b; }
        QPushButton#_secondary_btn {
            background-color: #ffffff;
            color: #4a90d9;
            border: 1px solid #4a90d9;
        }
        QPushButton#_secondary_btn:hover {
            background-color: #eaf2fb;
        }
        QPushButton#_browse_btn {
            background-color: #eef1f5;
            color: #4a90d9;
            border: 1px solid #ced4da;
            border-radius: 6px;
            padding: 4px 10px;
            min-width: 34px;
        }
        QPushButton#_browse_btn:hover {
            background-color: #e3e9f1;
        }
        QTabWidget::pane {
            border: 1px solid #dfe3e8;
            border-radius: 8px;
            top: -1px;
            background-color: #f5f6f8;
        }
        QTabBar::tab {
            background: #e4e8ed;
            color: #5a6b7b;
            padding: 8px 18px;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            margin-right: 2px;
        }
        QTabBar::tab:selected {
            background: #ffffff;
            color: #2c3e50;
            font-weight: 600;
        }
        QTabBar::tab:hover:!selected {
            background: #eef1f5;
        }
        QScrollArea {
            border: none;
            background-color: transparent;
        }
        QScrollBar:vertical {
            border: none;
            background: #e9edf2;
            width: 10px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical {
            background: #c2cad4;
            border-radius: 5px;
            min-height: 24px;
        }
        QScrollBar::handle:vertical:hover { background: #aab4c0; }
        QLineEdit[invalid="true"] {
            border: 1px solid #e74c3c;
            background-color: #fdecea;
        }
    """

    def _init_ui(self) -> None:
        self.setWindowTitle(t("dialogs.config_editor.title"))
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self.setStyleSheet(self._STYLE_SHEET)
        self.resize(960, 640)
        self.setMinimumSize(520, 400)
        available = self.screen().availableGeometry() if self.screen() else None
        if available is not None:
            max_h = int(available.height() * 0.85)
            if self.height() > max_h:
                self.resize(self.width(), max_h)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.tab_widget = QTabWidget()
        layout.addWidget(self.tab_widget, 1)

        for section in self.settings.config.sections():
            self._add_section_tab(section)

        # device 与 compute_type 联动: 选择 cpu 时禁用 float16
        self._setup_device_compute_links()

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        sep.setStyleSheet("color: #dfe3e8;")
        layout.addWidget(sep)

        btn_box = QDialogButtonBox()
        self._btn_box = btn_box
        self._save_btn = btn_box.addButton(
            t("dialogs.config_editor.save"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._reset_btn = btn_box.addButton(
            t("dialogs.config_editor.reset"), QDialogButtonBox.ButtonRole.ResetRole
        )
        self._reset_btn.setObjectName("_secondary_btn")
        cancel_btn = btn_box.addButton(t("dialogs.config_editor.cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        cancel_btn.setObjectName("_secondary_btn")
        btn_box.clicked.connect(self._on_button_clicked)
        layout.addWidget(btn_box)

    def _add_section_tab(self, section: str) -> None:
        if section == "summarization":
            self._summarization_tab = SummarizationTab(self.settings)
            self._edits["summarization"] = self._summarization_tab.get_section_edits()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(self._summarization_tab)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.tab_widget.addTab(scroll, t("config.section.summarization"))
            return

        content = QWidget()
        groups = _SECTION_GROUP_KEYS.get(section)
        if groups:
            tab_layout = QVBoxLayout(content)
            tab_layout.setContentsMargins(6, 6, 6, 6)
            tab_layout.setSpacing(14)
        else:
            form = QFormLayout(content)
            form.setContentsMargins(10, 12, 10, 12)
            form.setSpacing(10)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            form.setHorizontalSpacing(14)

        section_edits: dict[str, QWidget] = {}
        items = self.settings.config.items(section)

        _SKIP_KEYS = {"summarization.custom_prompt"}

        if groups:
            for group_key, keys in groups.items():
                gb = QGroupBox(t(group_key))
                gb_form = QFormLayout(gb)
                gb_form.setContentsMargins(10, 14, 10, 12)
                gb_form.setSpacing(10)
                gb_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
                gb_form.setHorizontalSpacing(14)
                for key in keys:
                    full_key = f"{section}.{key}"
                    if full_key in _SKIP_KEYS:
                        continue
                    value = dict(items).get(key, "")
                    tooltip = _KEY_TOOLTIP_KEYS.get(full_key)
                    label_key = _KEY_LABEL_KEYS.get(full_key)
                    label = t(label_key) if label_key else key
                    widget = self._create_edit_widget(full_key, value, t(tooltip) if tooltip else None)
                    if widget is not None:
                        gb_form.addRow(f"{label}:", widget)
                        section_edits[key] = widget
                tab_layout.addWidget(gb)
            # 未在分组中定义的 key 在最后以平铺方式展示
            grouped_keys = set()
            for keys in groups.values():
                grouped_keys.update(keys)
            extra_keys = [k for k in dict(items) if k not in grouped_keys and f"{section}.{k}" not in _SKIP_KEYS]
            if extra_keys:
                gb = QGroupBox(t("dialogs.config_editor.other_group"))
                gb_form = QFormLayout(gb)
                gb_form.setContentsMargins(10, 14, 10, 12)
                gb_form.setSpacing(10)
                gb_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
                gb_form.setHorizontalSpacing(14)
                for key in extra_keys:
                    full_key = f"{section}.{key}"
                    value = dict(items).get(key, "")
                    tooltip = _KEY_TOOLTIP_KEYS.get(full_key)
                    label_key = _KEY_LABEL_KEYS.get(full_key)
                    label = t(label_key) if label_key else key
                    widget = self._create_edit_widget(full_key, value, t(tooltip) if tooltip else None)
                    if widget is not None:
                        gb_form.addRow(f"{label}:", widget)
                        section_edits[key] = widget
                tab_layout.addWidget(gb)
            tab_layout.addStretch()
        else:
            for key, value in items:
                full_key = f"{section}.{key}"
                if full_key in _SKIP_KEYS:
                    continue
                tooltip = _KEY_TOOLTIP_KEYS.get(full_key)
                label_key = _KEY_LABEL_KEYS.get(full_key)
                label = t(label_key) if label_key else key
                widget = self._create_edit_widget(full_key, value, t(tooltip) if tooltip else None)
                if widget is not None:
                    form.addRow(f"{label}:", widget)
                    section_edits[key] = widget

        self._edits[section] = section_edits

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        label_key = _SECTION_LABEL_KEYS.get(section)
        tab_label = t(label_key) if label_key else section
        self.tab_widget.addTab(scroll, tab_label)


    def _setup_device_compute_links(self) -> None:
        """建立 device 与 compute_type 联动: 选择 cpu 时 compute_type 仅保留 int8 / float32。

        CTranslate2/faster-whisper 在 CPU 上不支持 float16，故 device=cpu 时
        动态移除 compute_type 下拉中的 float16 选项，切回 cuda/auto 时恢复。
        """
        pairs = [
            ("transcription", "device", "compute_type"),
            ("voice_to_text", "device", "compute_type"),
        ]
        for section, device_key, compute_key in pairs:
            edits = self._edits.get(section, {})
            device_widget = edits.get(device_key)
            compute_widget = edits.get(compute_key)
            if not isinstance(device_widget, QComboBox) or not isinstance(
                compute_widget, QComboBox
            ):
                continue

            def _make_handler(
                dev: QComboBox = device_widget, comp: QComboBox = compute_widget
            ):
                def _on_device_changed(_text: str = "") -> None:
                    self._update_compute_options(dev, comp)

                return _on_device_changed

            handler = _make_handler()
            device_widget.currentTextChanged.connect(handler)
            # 初始化一次, 使加载配置后即刻应用限制
            handler()

    @staticmethod
    def _update_compute_options(
        device_widget: QComboBox, compute_widget: QComboBox
    ) -> None:
        """根据当前 device 刷新 compute_type 可选项。"""
        device = device_widget.currentText().strip().lower()
        if device == "cpu":
            allowed = ["int8", "float32"]
        else:
            allowed = ["float16", "int8", "float32"]

        current = compute_widget.currentText().strip()
        compute_widget.blockSignals(True)
        compute_widget.clear()
        compute_widget.addItems(allowed)
        if current in allowed:
            compute_widget.setCurrentText(current)
        else:
            # 原值不可用(如 cpu 下的 float16), 回退到 int8
            compute_widget.setCurrentText("int8")
        compute_widget.blockSignals(False)

    def _create_edit_widget(
        self, full_key: str, value: str, tooltip: Optional[str]
    ) -> Optional[QWidget]:
        """根据 key 类型创建对应的编辑控件"""
        if full_key in _COMBO_KEYS:
            widget = QComboBox()
            widget.setProperty("_combo_key", full_key)
            widget.addItems(self._get_combo_options(full_key))
            ConfigEditorDialog._set_widget_text(widget, value)
            if tooltip:
                widget.setToolTip(tooltip)
            return widget
        elif full_key in Settings.PATH_KEYS:
            widget = QLineEdit(value)
            if tooltip:
                widget.setToolTip(tooltip)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            row.addWidget(widget, 1)
            browse_btn = QPushButton("📁")
            browse_btn.setObjectName("_browse_btn")
            browse_btn.setToolTip(t("dialogs.config_editor.browse_tooltip_dir"))
            browse_btn.setProperty("_path_edit", widget)
            browse_btn.clicked.connect(self._browse_dir)
            row.addWidget(browse_btn)
            container = QWidget()
            container.setLayout(row)
            return container
        elif full_key in _FILE_KEYS:
            widget = QLineEdit(value)
            if tooltip:
                widget.setToolTip(tooltip)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            row.addWidget(widget, 1)
            browse_btn = QPushButton("📁")
            browse_btn.setObjectName("_browse_btn")
            browse_btn.setToolTip(t("dialogs.config_editor.browse_tooltip_image"))
            browse_btn.setProperty("_path_edit", widget)
            browse_btn.clicked.connect(self._browse_file)
            row.addWidget(browse_btn)
            container = QWidget()
            container.setLayout(row)
            return container
        else:
            widget = QLineEdit(value)
            if tooltip:
                widget.setToolTip(tooltip)
            return widget

    @staticmethod
    def _get_combo_options(full_key: str) -> list[str]:
        """根据 full_key 获取翻译后的 ComboBox 选项列表"""
        options = _COMBO_OPTIONS.get(full_key, [])
        if not options:
            return options
        # app.ui_language: 第一项是 i18n key，其余是语言名称（普通文本）
        if full_key == "app.ui_language":
            return [t(options[0])] + options[1:]
        # 通用：全部是 i18n key
        if isinstance(options[0], str) and (options[0].startswith("common.") or options[0].startswith("config.")):
            return [t(o) for o in options]
        return options

    @staticmethod
    def _get_combo_value_map(full_key: str) -> dict[str, str]:
        """获取 ComboBox 显示文本到配置值的映射（以当前语言为键）"""
        mapping = _COMBO_VALUE_MAP.get(full_key, {})
        if not mapping:
            return mapping
        # app.ui_language: 键可能是 i18n key 或普通语言名称
        if full_key == "app.ui_language":
            return {t(k) if k.startswith("config.") else k: v for k, v in mapping.items()}
        # 通用：全部是 i18n key
        first_key = next(iter(mapping), "")
        if first_key.startswith("common.") or first_key.startswith("config."):
            return {t(k): v for k, v in mapping.items()}
        return mapping

    def _browse_dir(self) -> None:
        btn = self.sender()
        if btn is None:
            return
        edit: Optional[QLineEdit] = btn.property("_path_edit")
        if edit is None:
            return
        current = edit.text().strip()
        folder = QFileDialog.getExistingDirectory(self, t("dialogs.config_editor.browse_dir"), current)
        if folder:
            edit.setText(folder)

    def _browse_file(self) -> None:
        """浏览图片文件"""
        btn = self.sender()
        if btn is None:
            return
        edit: Optional[QLineEdit] = btn.property("_path_edit")
        if edit is None:
            return
        current = edit.text().strip()
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            t("dialogs.config_editor.browse_image"),
            current,
            t("dialogs.config_editor.image_filter"),
        )
        if file_path:
            from src.utils.paths import get_base_dir as _get_base_dir
            base = _get_base_dir()
            try:
                rel = Path(file_path).relative_to(base)
                edit.setText(str(rel))
            except ValueError:
                edit.setText(file_path)

    def closeEvent(self, event) -> None:
        if hasattr(self, "_summarization_tab"):
            self._summarization_tab.cleanup_threads()
        super().closeEvent(event)

    def _on_button_clicked(self, button: QAbstractButton) -> None:
        role = self._btn_box.buttonRole(button)
        if role == QDialogButtonBox.ButtonRole.AcceptRole:
            self._save()
        elif role == QDialogButtonBox.ButtonRole.ResetRole:
            self._reset()
        else:
            self.reject()

    @staticmethod
    def _widget_text(widget: QWidget) -> str:
        if isinstance(widget, QComboBox):
            display = widget.currentText()
            full_key = widget.property("_combo_key")
            if full_key:
                mapping = ConfigEditorDialog._get_combo_value_map(full_key)
                if display in mapping:
                    return mapping[display]
            for mapping in _COMBO_VALUE_MAP.values():
                if display in mapping:
                    return mapping[display]
            return display
        # 容器控件（PATH_KEYS / _FILE_KEYS 带浏览按钮）取内部的 QLineEdit
        if not hasattr(widget, "text"):
            line_edit = widget.findChild(QLineEdit)
            if line_edit is not None:
                return line_edit.text()
            return ""
        return widget.text()  # type: ignore[union-attr]

    @staticmethod
    def _set_widget_text(widget: QWidget, value: str) -> None:
        if isinstance(widget, QComboBox):
            value = value.strip()
            full_key = widget.property("_combo_key")
            if full_key:
                mapping = ConfigEditorDialog._get_combo_value_map(full_key)
                for display_text, config_value in mapping.items():
                    if config_value.lower() == value.lower():
                        index = widget.findText(display_text, Qt.MatchFlag.MatchFixedString)
                        if index >= 0:
                            widget.setCurrentIndex(index)
                            return
            for mapping in _COMBO_VALUE_MAP.values():
                for display_text, config_value in mapping.items():
                    if config_value.lower() == value.lower():
                        index = widget.findText(display_text, Qt.MatchFlag.MatchFixedString)
                        if index >= 0:
                            widget.setCurrentIndex(index)
                            return
            if hasattr(widget, "currentData"):
                for i in range(widget.count()):
                    data = widget.itemData(i)
                    if isinstance(data, str) and data.lower() == value.lower():
                        widget.setCurrentIndex(i)
                        return
            widget.setCurrentText(value)
        elif hasattr(widget, "setText"):
            widget.setText(value)  # type: ignore[union-attr]
        else:
            line_edit = widget.findChild(QLineEdit)
            if line_edit is not None:
                line_edit.setText(value)

    def _save(self) -> None:
        for section, edits in self._edits.items():
            for key, widget in edits.items():
                self.settings.set(f"{section}.{key}", self._widget_text(widget))

        if hasattr(self, "_summarization_tab"):
            self.settings.set(
                "summarization.provider", self._summarization_tab.get_provider()
            )

        self.settings.save()
        self.accept()

    def _reset(self) -> None:
        for section, edits in self._edits.items():
            items = self.settings.config.items(section)
            for key, widget in edits.items():
                self._set_widget_text(widget, dict(items).get(key, ""))

        if hasattr(self, "_summarization_tab"):
            provider = self.settings.get("summarization.provider", "ollama")
            self._summarization_tab.set_provider(provider)
