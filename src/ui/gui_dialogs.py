"""GUI 对话框组件"""

import os
import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
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
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.config.settings import Settings
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
    """媒体文件选择对话框 —— 树形视图展示文件，支持按类型/后缀/大小组合筛选和勾选。"""

    def __init__(
        self, video_files: list[str], parent=None, folder: Optional[str] = None
    ) -> None:
        super().__init__(parent)
        self.video_files = video_files
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

        self._init_ui()

    def _init_ui(self) -> None:
        self.setWindowTitle("选择媒体文件")
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self.resize(1000, 600)

        layout = QVBoxLayout(self)

        self._info_label = QLabel(
            f"共找到 {len(self.video_files)} 个媒体文件，请选择需要处理的文件："
        )
        layout.addWidget(self._info_label)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("媒体文件:"))
        self._media_type_combo = QComboBox()
        self._media_type_combo.addItems(["全部", "仅视频", "仅音频"])
        self._media_type_combo.currentIndexChanged.connect(self._apply_filters)
        toolbar.addWidget(self._media_type_combo)
        toolbar.addSpacing(16)
        toolbar.addWidget(QLabel("后缀:"))
        self._suffix_combo = QComboBox()
        self._suffix_combo.setMinimumWidth(80)
        self._suffix_combo.currentIndexChanged.connect(self._apply_filters)
        toolbar.addWidget(self._suffix_combo)
        toolbar.addSpacing(16)
        toolbar.addWidget(QLabel("大小:"))
        self._size_combo = QComboBox()
        self._size_combo.setMinimumWidth(120)
        self._size_tiers = [
            ("全部", 0, None),
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
        toolbar.addSpacing(16)
        toolbar.addWidget(QLabel("搜索:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("输入文件名关键字...")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self._search_edit)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["文件名", "类型", "大小", "输出目录拼接"])
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self._tree.setAnimated(True)
        self._tree.setSortingEnabled(False)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
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
        self._tree.expandAll()
        self._tree.header().setSortIndicatorShown(True)
        self._tree.header().setSectionsClickable(True)
        self._sort_order: dict[int, Qt.SortOrder] = {}
        self._tree.header().sectionClicked.connect(self._on_header_clicked)
        self._tree.itemChanged.connect(self._update_info_label)
        self._update_info_label()
        layout.addWidget(self._tree)

        bottom_layout = QHBoxLayout()
        select_all_btn = QPushButton("全选")
        select_all_btn.clicked.connect(self._select_all)
        bottom_layout.addWidget(select_all_btn)
        deselect_all_btn = QPushButton("取消全选")
        deselect_all_btn.clicked.connect(self._deselect_all)
        bottom_layout.addWidget(deselect_all_btn)
        invert_btn = QPushButton("反选")
        invert_btn.clicked.connect(self._invert_selection)
        bottom_layout.addWidget(invert_btn)
        expand_all_btn = QPushButton("展开文件夹")
        expand_all_btn.clicked.connect(self._tree.expandAll)
        bottom_layout.addWidget(expand_all_btn)
        collapse_all_btn = QPushButton("收缩文件夹")
        collapse_all_btn.clicked.connect(self._tree.collapseAll)
        bottom_layout.addWidget(collapse_all_btn)
        bottom_layout.addStretch()

        self._mirror_checkbox = QCheckBox("输出目录拼接")
        self._mirror_checkbox.setToolTip(
            "启用后，输出文件将按照输入目录的子目录结构组织"
        )
        self._mirror_checkbox.toggled.connect(self._on_mirror_changed)
        bottom_layout.addWidget(self._mirror_checkbox)

        bottom_layout.addWidget(QLabel("层级:"))
        self._depth_spin = QSpinBox()
        self._depth_spin.setRange(1, 10)
        default_depth = Settings().get_int("output.mirror_depth", 1)
        self._depth_spin.setValue(default_depth)
        self._depth_spin.setEnabled(False)
        self._depth_spin.valueChanged.connect(self._on_depth_changed)
        bottom_layout.addWidget(self._depth_spin)

        ok_btn = QPushButton("确定")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        bottom_layout.addWidget(ok_btn)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        bottom_layout.addWidget(cancel_btn)
        layout.addLayout(bottom_layout)

        self._apply_mirror_defaults()

    def _build_tree(self) -> None:
        self._leaf_items: list[QTreeWidgetItem] = []
        paths = [Path(f) for f in self.video_files]
        if not paths:
            return

        try:
            common = Path(os.path.commonpath(paths))
        except ValueError:
            common = None
        self._common_path = str(common) if common else None

        suffix_map: dict[str, str] = {}
        for p in paths:
            ext = p.suffix.lower()
            if ext in self._video_exts:
                suffix_map[ext] = "视频"
            elif ext in self._audio_exts:
                suffix_map[ext] = "音频"
            else:
                suffix_map[ext] = "媒体"

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
            for _, abs_p in sorted(rel_pairs, key=lambda x: x[0].name.lower()):
                ext = abs_p.suffix.lower()
                media_type = suffix_map.get(ext, "媒体")
                icon = (
                    "🎬"
                    if media_type == "视频"
                    else "🎵"
                    if media_type == "音频"
                    else "📄"
                )
                try:
                    size_bytes = abs_p.stat().st_size
                    size_str = _format_file_size(size_bytes)
                except OSError:
                    size_bytes = -1
                    size_str = "-"
                rel_parts: tuple[str, ...] = ()
                item = _SortTreeWidgetItem(
                    [f"{icon} {abs_p.name}", media_type, size_str, ""]
                )
                item.setData(0, Qt.ItemDataRole.UserRole, str(abs_p))
                item.setData(0, Qt.ItemDataRole.UserRole + 1, size_bytes)
                item.setData(0, Qt.ItemDataRole.UserRole + 2, rel_parts)
                item.setCheckState(0, Qt.CheckState.Checked)
                for _col in range(1, 4):
                    item.setTextAlignment(
                        _col, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    )
                self._tree.addTopLevelItem(item)
                self._leaf_items.append(item)
        else:
            folder_nodes: dict[tuple[str, ...], QTreeWidgetItem] = {}
            for rel, abs_p in rel_pairs:
                parts = rel.parent.parts
                if not parts:
                    ext = abs_p.suffix.lower()
                    media_type = suffix_map.get(ext, "媒体")
                    icon = (
                        "🎬"
                        if media_type == "视频"
                        else "🎵"
                        if media_type == "音频"
                        else "📄"
                    )
                    try:
                        size_bytes = abs_p.stat().st_size
                        size_str = _format_file_size(size_bytes)
                    except OSError:
                        size_bytes = -1
                        size_str = "-"
                    rel_parts = ()
                    item = _SortTreeWidgetItem(
                        [f"{icon} {abs_p.name}", media_type, size_str, ""]
                    )
                    item.setData(0, Qt.ItemDataRole.UserRole, str(abs_p))
                    item.setData(0, Qt.ItemDataRole.UserRole + 1, size_bytes)
                    item.setData(0, Qt.ItemDataRole.UserRole + 2, rel_parts)
                    item.setCheckState(0, Qt.CheckState.Checked)
                    for _col in range(1, 4):
                        item.setTextAlignment(
                            _col,
                            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        )
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
        self._suffix_combo.addItem("全部")
        for ext in sorted(present_suffixes):
            self._suffix_combo.addItem(ext)

    def _add_file_item(
        self,
        parent: QTreeWidgetItem,
        abs_p: Path,
        suffix_map: dict,
        rel_parts: tuple[str, ...] = (),
    ) -> None:
        ext = abs_p.suffix.lower()
        media_type = suffix_map.get(ext, "媒体")
        icon = "🎬" if media_type == "视频" else "🎵" if media_type == "音频" else "📄"
        try:
            size_bytes = abs_p.stat().st_size
            size_str = _format_file_size(size_bytes)
        except OSError:
            size_bytes = -1
            size_str = "-"
        item = _SortTreeWidgetItem([f"{icon} {abs_p.name}", media_type, size_str, ""])
        item.setData(0, Qt.ItemDataRole.UserRole, str(abs_p))
        item.setData(0, Qt.ItemDataRole.UserRole + 1, size_bytes)
        item.setData(0, Qt.ItemDataRole.UserRole + 2, rel_parts)
        item.setCheckState(0, Qt.CheckState.Checked)
        for _col in range(1, 4):
            item.setTextAlignment(
                _col, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
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
            f"共找到 {total} 个媒体文件，已选择 {checked} 个，共 {size_str}："
        )

    def _apply_filters(self) -> None:
        media_idx = self._media_type_combo.currentIndex()
        media_exts: Optional[set[str]] = None
        if media_idx == 1:
            media_exts = self._video_exts
        elif media_idx == 2:
            media_exts = self._audio_exts

        suffix_target = self._suffix_combo.currentText().strip().lower()

        size_idx = self._size_combo.currentIndex()
        size_lo, size_hi = 0, None
        if 0 <= size_idx < len(self._size_tiers):
            _, size_lo, size_hi = self._size_tiers[size_idx]

        keyword = self._search_edit.text().strip().lower()

        self._tree.blockSignals(True)
        for item in self._iter_leaves():
            path = item.data(0, Qt.ItemDataRole.UserRole)
            ext = Path(path).suffix.lower()
            name = Path(path).stem.lower()
            size_bytes: int = item.data(0, Qt.ItemDataRole.UserRole + 1)

            match = True
            if media_exts is not None and ext not in media_exts:
                match = False
            if suffix_target and suffix_target != "全部" and ext != suffix_target:
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
        self._apply_filters()

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
        open_dir_action = menu.addAction("打开所在目录")
        copy_path_action = menu.addAction("复制路径")
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
                item.setText(3, "—")
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
                    item.setText(3, "—")

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
                item.setText(3, "—")
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
                item.setText(3, "（根目录）")
                item.setToolTip(3, "（根目录）")
        self._tree.blockSignals(False)

    def get_mirror_subdirs(self) -> bool:
        return self._mirror_checkbox.isChecked()

    def get_mirror_depth(self) -> int:
        return self._depth_spin.value()

    def get_input_folder(self) -> Optional[str]:
        return self._common_path


_SECTION_LABELS: dict[str, str] = {
    "app": "应用",
    "transcription": "转写",
    "summarization": "总结",
    "preprocessing": "预处理",
    "output": "输出",
    "network": "网络",
    "paths": "路径",
    "tools": "工具",
    "text_processing": "文本处理",
}

_KEY_LABELS: dict[str, str] = {
    "app.name": "软件名称",
    "app.version": "版本号",
    "app.log_level": "日志级别",
    "transcription.model_path": "模型路径",
    "transcription.device": "设备",
    "transcription.language": "语言",
    "transcription.beam_size": "束搜索宽度",
    "transcription.best_of": "候选数量",
    "transcription.temperature": "温度",
    "transcription.compute_type": "计算类型",
    "transcription.num_workers": "推理线程数",
    "transcription.vad_filter": "VAD过滤",
    "transcription.condition_on_previous_text": "基于前文条件",
    "transcription.word_timestamps": "词级时间戳",
    "preprocessing.audio_sample_rate": "音频采样率",
    "preprocessing.audio_channels": "音频声道数",
    "preprocessing.max_chunk_duration": "最大分段时长",
    "preprocessing.supported_video_formats": "支持的视频格式",
    "preprocessing.supported_audio_formats": "支持的音频格式",
    "output.output_dir": "输出目录",
    "output.transcript_format": "转写格式",
    "output.summary_format": "摘要格式",
    "output.mirror_enabled": "目录拼接开关",
    "output.mirror_depth": "目录拼接层级",
    "network.proxy": "代理地址",
    "paths.models_dir": "模型目录",
    "paths.logs_dir": "日志目录",
    "paths.video_dir": "视频目录",
    "text_processing.max_gap": "合并最大间隔",
    "text_processing.min_length": "最小合并长度",
    "text_processing.filler_words": "填充词列表",
}

_KEY_TOOLTIPS: dict[str, str] = {
    "app.log_level": "日志级别: DEBUG / INFO / WARNING / ERROR",
    "transcription.model_path": "Whisper 模型目录,填写目录名称",
    "transcription.device": "推理设备: cuda (NVIDIA GPU), cpu, auto (自动选择)",
    "transcription.language": "转写语言代码: zh (中文), en (英文), ja (日文) 等，留空或 auto 自动检测",
    "transcription.beam_size": "束搜索宽度 (1~10)，越大越准确但越慢",
    "transcription.best_of": "采样候选数量，从 N 个候选中选最优结果",
    "transcription.temperature": "采样温度: 0 为贪心解码 (最确定)，值越高结果越随机",
    "transcription.compute_type": "计算精度: float16, int8, float32。int8 显存占用最少，float16 精度最佳",
    "transcription.num_workers": "CTranslate2 推理引擎线程数 (1~CPU核心数)，增大可加速单次转写推理",
    "transcription.vad_filter": "是否启用 VAD 语音活动检测，过滤静音段可减少幻觉: True / False",
    "transcription.condition_on_previous_text": "是否基于前文上下文条件生成，可提高连贯性但可能传播错误: True / False",
    "transcription.word_timestamps": "是否生成词级时间戳，启用后可精确定位每个词的时间: True / False",
    "preprocessing.audio_sample_rate": "音频采样率 (Hz)，Whisper 推荐 16000",
    "preprocessing.audio_channels": "音频声道数: 1=单声道 (推荐), 2=立体声",
    "preprocessing.max_chunk_duration": "长音频分段时长 (秒)，超过此值自动分段处理",
    "preprocessing.supported_video_formats": "支持的视频文件后缀，逗号分隔",
    "preprocessing.supported_audio_formats": "支持的音频文件后缀，逗号分隔",
    "output.output_dir": "输出目录，支持相对路径 (相对程序目录) 和绝对路径",
    "output.transcript_format": "转写输出格式: txt, srt, vtt, json (可选多种，逗号分隔)",
    "output.summary_format": "摘要输出格式: txt (纯文本), md (Markdown)",
    "output.mirror_enabled": "是否启用输出目录拼接: True / False",
    "output.mirror_depth": "输出目录拼接的默认层级深度 (1~10)，控制取输入目录的前几层子目录",
    "network.proxy": "HTTP 代理地址 (如 http://127.0.0.1:7890)，用于访问外部 API，留空不使用",
    "paths.models_dir": "模型文件存储目录，支持相对路径和绝对路径",
    "paths.logs_dir": "日志文件存储目录",
    "paths.video_dir": "视频文件默认目录",
    "text_processing.max_gap": "段落合并最大时间间隔 (秒)，间隔超过此值的段落不会合并",
    "text_processing.min_length": "最小文本长度，短于此长度的段落会尝试与相邻段落合并",
    "text_processing.filler_words": "需要清除的填充词，逗号分隔",
}


class ConfigEditorDialog(QDialog):
    """配置编辑对话框 —— 按 config.ini 的 section 分 tab 展示所有配置项"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.settings = Settings()
        self._edits: dict[str, dict[str, QWidget]] = {}
        self._init_ui()

    def _init_ui(self) -> None:
        self.setWindowTitle("编辑配置")
        self.resize(600, 560)

        layout = QVBoxLayout(self)

        self.tab_widget = QTabWidget()
        layout.addWidget(self.tab_widget)

        for section in self.settings.config.sections():
            self._add_section_tab(section)

        btn_box = QDialogButtonBox()
        self._btn_box = btn_box
        self._save_btn = btn_box.addButton(
            "保存", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._reset_btn = btn_box.addButton(
            "重置", QDialogButtonBox.ButtonRole.ResetRole
        )
        btn_box.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        btn_box.clicked.connect(self._on_button_clicked)
        layout.addWidget(btn_box)

    def _add_section_tab(self, section: str) -> None:
        if section == "summarization":
            self._summarization_tab = SummarizationTab(self.settings)
            self._edits["summarization"] = self._summarization_tab.get_section_edits()
            self.tab_widget.addTab(self._summarization_tab, "总结")
            return

        tab = QWidget()
        form = QFormLayout(tab)
        form.setContentsMargins(8, 8, 8, 8)

        section_edits: dict[str, QWidget] = {}
        items = self.settings.config.items(section)

        _SKIP_KEYS = {"summarization.custom_prompt"}

        for key, value in items:
            full_key = f"{section}.{key}"
            if full_key in _SKIP_KEYS:
                continue
            widget = QLineEdit(value)
            tooltip = _KEY_TOOLTIPS.get(full_key)
            if tooltip:
                widget.setToolTip(tooltip)
            label = _KEY_LABELS.get(full_key, key)
            if full_key in Settings.PATH_KEYS:
                row = QHBoxLayout()
                row.addWidget(widget, 1)
                browse_btn = QPushButton("浏览")
                browse_btn.setProperty("_path_edit", widget)
                browse_btn.clicked.connect(self._browse_dir)
                row.addWidget(browse_btn)
                form.addRow(f"{label}:", row)
            else:
                form.addRow(f"{label}:", widget)
            section_edits[key] = widget

        self._edits[section] = section_edits

        tab_label = _SECTION_LABELS.get(section, section)
        self.tab_widget.addTab(tab, tab_label)

    def _browse_dir(self) -> None:
        btn = self.sender()
        if btn is None:
            return
        edit: Optional[QLineEdit] = btn.property("_path_edit")
        if edit is None:
            return
        current = edit.text().strip()
        folder = QFileDialog.getExistingDirectory(self, "选择目录", current)
        if folder:
            edit.setText(folder)

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
            return widget.currentText()
        if hasattr(widget, "text"):
            return widget.text()  # type: ignore[union-attr]
        return ""

    @staticmethod
    def _set_widget_text(widget: QWidget, value: str) -> None:
        if isinstance(widget, QComboBox):
            widget.setCurrentText(value)
        elif hasattr(widget, "setText"):
            widget.setText(value)  # type: ignore[union-attr]

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
