"""GUI 对话框组件"""

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.config.settings import Settings
from src.summarization.ollama_client import OllamaClient
from src.ui.gui_workers import (
    NvidiaCheckWorker,
    OllamaCheckWorker,
    OllamaListModelWorker,
    OllamaStartServiceWorker,
)
from src.utils.logger import get_logger


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


class VideoSelectionDialog(QDialog):
    """媒体文件选择对话框 —— 树形视图 + 批量筛选"""

    def __init__(self, video_files: list[str], parent=None) -> None:
        super().__init__(parent)
        self.video_files = video_files

        settings = Settings()
        self._video_exts: set[str] = set(
            ext.lower()
            for ext in settings.get_list("preprocessing.supported_video_formats")
        )
        self._audio_exts: set[str] = set(
            ext.lower()
            for ext in settings.get_list("preprocessing.supported_audio_formats")
        )

        self._init_ui()

    def _init_ui(self) -> None:
        self.setWindowTitle("选择媒体文件")
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self.resize(700, 550)

        layout = QVBoxLayout(self)

        self._info_label = QLabel(
            f"共找到 {len(self.video_files)} 个媒体文件，请选择需要处理的文件："
        )
        layout.addWidget(self._info_label)

        toolbar = QHBoxLayout()
        only_video_btn = QPushButton("仅视频")
        only_video_btn.clicked.connect(self._select_only_video)
        toolbar.addWidget(only_video_btn)
        only_audio_btn = QPushButton("仅音频")
        only_audio_btn.clicked.connect(self._select_only_audio)
        toolbar.addWidget(only_audio_btn)
        select_all_toolbar_btn = QPushButton("全部")
        select_all_toolbar_btn.clicked.connect(self._select_all)
        toolbar.addWidget(select_all_toolbar_btn)
        toolbar.addSpacing(16)
        toolbar.addWidget(QLabel("后缀:"))
        self._suffix_combo = QComboBox()
        self._suffix_combo.setMinimumWidth(80)
        self._suffix_combo.currentIndexChanged.connect(self._select_by_suffix)
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
        self._size_combo.currentIndexChanged.connect(self._select_by_size)
        toolbar.addWidget(self._size_combo)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["文件名", "类型", "大小"])
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self._tree.setAnimated(True)
        self._build_tree()
        self._tree.header().setStretchLastSection(False)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._tree.header().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        model = self._tree.model()
        _ALIGN_RIGHT = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        model.setHeaderData(
            1,
            Qt.Orientation.Horizontal,
            _ALIGN_RIGHT,
            Qt.ItemDataRole.TextAlignmentRole,
        )
        model.setHeaderData(
            2,
            Qt.Orientation.Horizontal,
            _ALIGN_RIGHT,
            Qt.ItemDataRole.TextAlignmentRole,
        )
        self._tree.expandAll()
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
        bottom_layout.addStretch()
        ok_btn = QPushButton("确定")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        bottom_layout.addWidget(ok_btn)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        bottom_layout.addWidget(cancel_btn)
        layout.addLayout(bottom_layout)

    def _build_tree(self) -> None:
        self._leaf_items: list[QTreeWidgetItem] = []
        paths = [Path(f) for f in self.video_files]
        if not paths:
            return

        try:
            common = Path(os.path.commonpath(paths))
        except ValueError:
            common = None

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
                item = QTreeWidgetItem([f"{icon} {abs_p.name}", media_type, size_str])
                item.setData(0, Qt.ItemDataRole.UserRole, str(abs_p))
                item.setData(0, Qt.ItemDataRole.UserRole + 1, size_bytes)
                item.setCheckState(0, Qt.CheckState.Checked)
                item.setTextAlignment(
                    1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                item.setTextAlignment(
                    2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
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
                    item = QTreeWidgetItem(
                        [f"{icon} {abs_p.name}", media_type, size_str]
                    )
                    item.setData(0, Qt.ItemDataRole.UserRole, str(abs_p))
                    item.setData(0, Qt.ItemDataRole.UserRole + 1, size_bytes)
                    item.setCheckState(0, Qt.CheckState.Checked)
                    item.setTextAlignment(
                        1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                    item.setTextAlignment(
                        2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                    self._tree.addTopLevelItem(item)
                    self._leaf_items.append(item)
                    continue
                for i in range(len(parts)):
                    sub_key = parts[: i + 1]
                    if sub_key not in folder_nodes:
                        fi = QTreeWidgetItem([f"📁 {parts[i]}", "", ""])
                        fi.setFlags(
                            fi.flags()
                            | Qt.ItemFlag.ItemIsAutoTristate
                            | Qt.ItemFlag.ItemIsUserCheckable
                        )
                        fi.setCheckState(0, Qt.CheckState.Checked)
                        if i == 0:
                            self._tree.addTopLevelItem(fi)
                        else:
                            folder_nodes[parts[:i]].addChild(fi)
                        folder_nodes[sub_key] = fi
                self._add_file_item(folder_nodes[parts], abs_p, suffix_map)

        present_suffixes = {p.suffix.lower() for p in paths}
        self._suffix_combo.clear()
        for ext in sorted(present_suffixes):
            self._suffix_combo.addItem(ext)

    def _add_file_item(
        self, parent: QTreeWidgetItem, abs_p: Path, suffix_map: dict
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
        item = QTreeWidgetItem([f"{icon} {abs_p.name}", media_type, size_str])
        item.setData(0, Qt.ItemDataRole.UserRole, str(abs_p))
        item.setData(0, Qt.ItemDataRole.UserRole + 1, size_bytes)
        item.setCheckState(0, Qt.CheckState.Checked)
        item.setTextAlignment(
            1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        item.setTextAlignment(
            2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        parent.addChild(item)
        self._leaf_items.append(item)

    def _iter_leaves(self):
        yield from self._leaf_items

    def _update_info_label(self) -> None:
        total = len(self._leaf_items)
        checked = sum(
            1
            for item in self._leaf_items
            if item.checkState(0) == Qt.CheckState.Checked
        )
        self._info_label.setText(f"共找到 {total} 个媒体文件，已选择 {checked} 个：")

    def _select_only_video(self) -> None:
        self._tree.blockSignals(True)
        for item in self._iter_leaves():
            path = item.data(0, Qt.ItemDataRole.UserRole)
            ext = Path(path).suffix.lower()
            item.setCheckState(
                0,
                Qt.CheckState.Checked
                if ext in self._video_exts
                else Qt.CheckState.Unchecked,
            )
        self._tree.blockSignals(False)
        self._update_info_label()

    def _select_only_audio(self) -> None:
        self._tree.blockSignals(True)
        for item in self._iter_leaves():
            path = item.data(0, Qt.ItemDataRole.UserRole)
            ext = Path(path).suffix.lower()
            item.setCheckState(
                0,
                Qt.CheckState.Checked
                if ext in self._audio_exts
                else Qt.CheckState.Unchecked,
            )
        self._tree.blockSignals(False)
        self._update_info_label()

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

    def _select_by_suffix(self) -> None:
        target = self._suffix_combo.currentText().strip().lower()
        if not target:
            return
        self._tree.blockSignals(True)
        for item in self._iter_leaves():
            path = item.data(0, Qt.ItemDataRole.UserRole)
            ext = Path(path).suffix.lower()
            item.setCheckState(
                0, Qt.CheckState.Checked if ext == target else Qt.CheckState.Unchecked
            )
        self._tree.blockSignals(False)
        self._update_info_label()

    def _select_by_size(self) -> None:
        idx = self._size_combo.currentIndex()
        if idx < 0 or idx >= len(self._size_tiers):
            return
        _, lo, hi = self._size_tiers[idx]
        self._tree.blockSignals(True)
        for item in self._iter_leaves():
            size_bytes: int = item.data(0, Qt.ItemDataRole.UserRole + 1)
            if size_bytes < 0:
                item.setCheckState(0, Qt.CheckState.Unchecked)
                continue
            ok = size_bytes >= lo
            if hi is not None:
                ok = ok and size_bytes < hi
            item.setCheckState(
                0, Qt.CheckState.Checked if ok else Qt.CheckState.Unchecked
            )
        self._tree.blockSignals(False)
        self._update_info_label()

    def get_selected_files(self) -> list[str]:
        selected: list[str] = []
        for item in self._iter_leaves():
            if item.checkState(0) == Qt.CheckState.Checked:
                selected.append(item.data(0, Qt.ItemDataRole.UserRole))
        return selected


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
    "transcription.num_workers": "工作线程数",
    "transcription.vad_filter": "VAD过滤",
    "transcription.condition_on_previous_text": "基于前文条件",
    "transcription.word_timestamps": "词级时间戳",
    "summarization.ollama_url": "Ollama服务地址",
    "summarization.model_name": "模型名称",
    "summarization.max_length": "最大长度",
    "summarization.temperature": "温度",
    "summarization.timeout": "超时时间",
    "summarization.nvidia_api_url": "NVIDIA API 地址",
    "summarization.nvidia_model": "NVIDIA 模型",
    "summarization.nvidia_max_tokens": "最大 Token 数",
    "summarization.nvidia_temperature": "温度",
    "summarization.nvidia_top_p": "Top P",
    "summarization.nvidia_frequency_penalty": "频率惩罚",
    "summarization.nvidia_presence_penalty": "存在惩罚",
    "summarization.nvidia_mode": "NVIDIA 模式",
    "summarization.nvidia_thread_count": "NVIDIA 线程数",
    "summarization.nvidia_stream": "NVIDIA 流式输出",
    "preprocessing.ffmpeg_path": "FFmpeg路径",
    "preprocessing.audio_sample_rate": "音频采样率",
    "preprocessing.audio_channels": "音频声道数",
    "preprocessing.max_chunk_duration": "最大分段时长",
    "preprocessing.supported_video_formats": "支持的视频格式",
    "preprocessing.supported_audio_formats": "支持的音频格式",
    "output.output_dir": "输出目录",
    "output.transcript_format": "转写格式",
    "output.summary_format": "摘要格式",
    "network.proxy": "代理地址",
    "network.hf_mirror_url": "HuggingFace镜像地址",
    "network.download_timeout": "下载超时",
    "network.download_max_retries": "下载重试次数",
    "paths.models_dir": "模型目录",
    "paths.logs_dir": "日志目录",
    "paths.video_dir": "视频目录",
    "tools.watermark_mode": "水印处理模式",
    "tools.watermark_blur_size": "模糊核大小",
    "tools.watermark_inpaint_radius": "修复半径",
    "tools.watermark_output_dir": "输出子目录",
    "tools.watermark_max_batch": "批量上限",
    "text_processing.max_gap": "合并最大间隔",
    "text_processing.min_length": "最小合并长度",
    "text_processing.filler_words": "填充词列表",
}

_KEY_TOOLTIPS: dict[str, str] = {
    "app.log_level": "日志级别: DEBUG / INFO / WARNING / ERROR",
    "transcription.model_path": "Whisper 模型名称或本地路径。可选: large-v3, medium, small, tiny, 或模型目录绝对路径",
    "transcription.device": "推理设备: cuda (NVIDIA GPU), cpu, auto (自动选择)",
    "transcription.language": "转写语言代码: zh (中文), en (英文), ja (日文) 等，留空或 auto 自动检测",
    "transcription.beam_size": "束搜索宽度 (1~10)，越大越准确但越慢",
    "transcription.best_of": "采样候选数量，从 N 个候选中选最优结果",
    "transcription.temperature": "采样温度: 0 为贪心解码 (最确定)，值越高结果越随机",
    "transcription.compute_type": "计算精度: float16, int8, float32。int8 显存占用最少，float16 精度最佳",
    "transcription.num_workers": "并行转写工作线程数，多文件转写时的并发度",
    "transcription.vad_filter": "是否启用 VAD 语音活动检测，过滤静音段可减少幻觉: True / False",
    "transcription.condition_on_previous_text": "是否基于前文上下文条件生成，可提高连贯性但可能传播错误: True / False",
    "transcription.word_timestamps": "是否生成词级时间戳，启用后可精确定位每个词的时间: True / False",
    "summarization.ollama_url": "Ollama 服务地址，默认 http://127.0.0.1:11434",
    "summarization.model_name": "Ollama 模型名称，点击「刷新模型列表」获取可用模型",
    "summarization.max_length": "生成摘要的最大 token 数，长文本建议 10000+",
    "summarization.temperature": "生成温度 (0~2)，越低越确定性，建议 0.3~0.8",
    "summarization.timeout": "请求超时时间 (秒)，长文本建议 600+",
    "summarization.nvidia_api_url": "NVIDIA API 端点地址",
    "summarization.nvidia_model": "NVIDIA 模型标识，如 openai/gpt-oss-120b",
    "summarization.nvidia_max_tokens": "最大生成 token 数",
    "summarization.nvidia_temperature": "生成温度 (0~2)",
    "summarization.nvidia_top_p": "核采样概率阈值 (0~1)，控制输出多样性",
    "summarization.nvidia_frequency_penalty": "频率惩罚 (-2.0~2.0)，降低重复用词概率",
    "summarization.nvidia_presence_penalty": "存在惩罚 (-2.0~2.0)，鼓励谈论新话题",
    "summarization.nvidia_mode": "NVIDIA 模式: single (单线程，支持流式), multi (多线程并发)",
    "summarization.nvidia_thread_count": "多线程模式下的并发线程数",
    "summarization.nvidia_stream": "是否启用流式输出 (仅单线程模式有效): true / false",
    "preprocessing.ffmpeg_path": "FFmpeg 可执行文件路径或命令名，需在 PATH 中或填写完整路径",
    "preprocessing.audio_sample_rate": "音频采样率 (Hz)，Whisper 推荐 16000",
    "preprocessing.audio_channels": "音频声道数: 1=单声道 (推荐), 2=立体声",
    "preprocessing.max_chunk_duration": "长音频分段时长 (秒)，超过此值自动分段处理",
    "preprocessing.supported_video_formats": "支持的视频文件后缀，逗号分隔",
    "preprocessing.supported_audio_formats": "支持的音频文件后缀，逗号分隔",
    "output.output_dir": "输出目录，支持相对路径 (相对程序目录) 和绝对路径",
    "output.transcript_format": "转写输出格式: txt, srt, vtt, json (可选多种，逗号分隔)",
    "output.summary_format": "摘要输出格式: txt (纯文本), md (Markdown)",
    "network.proxy": "HTTP 代理地址 (如 http://127.0.0.1:7890)，用于访问外部 API，留空不使用",
    "network.hf_mirror_url": "HuggingFace 模型下载地址，国内用户可替换为镜像地址",
    "network.download_timeout": "模型下载超时时间 (秒)",
    "network.download_max_retries": "模型下载失败重试次数",
    "paths.models_dir": "模型文件存储目录，支持相对路径和绝对路径",
    "paths.logs_dir": "日志文件存储目录",
    "paths.video_dir": "视频文件默认目录",
    "text_processing.max_gap": "段落合并最大时间间隔 (秒)，间隔超过此值的段落不会合并",
    "text_processing.min_length": "最小文本长度，短于此长度的段落会尝试与相邻段落合并",
    "text_processing.filler_words": "需要清除的填充词，逗号分隔",
    "tools.watermark_mode": "水印处理模式: blur (模糊), inpaint (修复填充)",
    "tools.watermark_blur_size": "模糊核大小 (奇数)，越大模糊效果越强 (仅 blur 模式)",
    "tools.watermark_inpaint_radius": "修复半径，越大修复范围越广 (仅 inpaint 模式)",
    "tools.watermark_output_dir": "去水印输出子目录名，相对于原图目录",
    "tools.watermark_max_batch": "单次批量处理的最大图片数量",
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
            self._create_summarization_tab()
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

    def _create_summarization_tab(self) -> None:
        """创建总结选项卡 —— 包含 Ollama / NVIDIA 切换"""
        tab = QWidget()
        main_layout = QVBoxLayout(tab)
        main_layout.setContentsMargins(8, 8, 8, 8)

        section_edits: dict[str, QWidget] = {}

        # ---- 服务商选择 ----
        provider_group = QGroupBox("总结服务")
        provider_layout = QHBoxLayout(provider_group)
        self._radio_ollama = QRadioButton("本地 Ollama 模型")
        self._radio_nvidia = QRadioButton("在线 NVIDIA 模型")
        current_provider = self.settings.get("summarization.provider", "ollama")
        if current_provider == "nvidia":
            self._radio_nvidia.setChecked(True)
        else:
            self._radio_ollama.setChecked(True)
        provider_layout.addWidget(self._radio_ollama)
        provider_layout.addWidget(self._radio_nvidia)
        main_layout.addWidget(provider_group)

        # ---- Ollama 区域 ----
        self._ollama_group = QGroupBox("Ollama 配置")
        ollama_form = QFormLayout(self._ollama_group)
        ollama_form.setContentsMargins(8, 8, 8, 8)

        ollama_items = {
            "ollama_url": self.settings.get(
                "summarization.ollama_url", "http://127.0.0.1:11434"
            ),
            "model_name": self.settings.get("summarization.model_name", ""),
            "max_length": self.settings.get("summarization.max_length", "10000"),
            "temperature": self.settings.get("summarization.temperature", "0.7"),
            "timeout": self.settings.get("summarization.timeout", "600"),
        }

        for key, value in ollama_items.items():
            full_key = f"summarization.{key}"
            if key == "model_name":
                widget = self._create_model_combo(value, ollama_form)
            else:
                widget = QLineEdit(value)
                label = _KEY_LABELS.get(full_key, key)
                ollama_form.addRow(f"{label}:", widget)
            tooltip = _KEY_TOOLTIPS.get(full_key)
            if tooltip:
                widget.setToolTip(tooltip)
            section_edits[key] = widget

        self._add_ollama_service_buttons(ollama_form)
        main_layout.addWidget(self._ollama_group)

        # ---- NVIDIA 区域 ----
        self._nvidia_group = QGroupBox("NVIDIA 配置")
        nvidia_form = QFormLayout(self._nvidia_group)
        nvidia_form.setContentsMargins(8, 8, 8, 8)

        nvidia_items = {
            "nvidia_api_url": self.settings.get(
                "summarization.nvidia_api_url",
                "https://integrate.api.nvidia.com/v1/chat/completions",
            ),
            "nvidia_model": self.settings.get(
                "summarization.nvidia_model", "openai/gpt-oss-120b"
            ),
            "nvidia_max_tokens": self.settings.get(
                "summarization.nvidia_max_tokens", "100000"
            ),
            "nvidia_temperature": self.settings.get(
                "summarization.nvidia_temperature", "1.0"
            ),
            "nvidia_top_p": self.settings.get("summarization.nvidia_top_p", "1.0"),
            "nvidia_frequency_penalty": self.settings.get(
                "summarization.nvidia_frequency_penalty", "0.0"
            ),
            "nvidia_presence_penalty": self.settings.get(
                "summarization.nvidia_presence_penalty", "0.0"
            ),
        }

        for key, value in nvidia_items.items():
            full_key = f"summarization.{key}"
            widget = QLineEdit(value)
            tooltip = _KEY_TOOLTIPS.get(full_key)
            if tooltip:
                widget.setToolTip(tooltip)
            label = _KEY_LABELS.get(full_key, key)
            nvidia_form.addRow(f"{label}:", widget)
            section_edits[key] = widget

        nvidia_mode = self.settings.get("summarization.nvidia_mode", "single")

        self._nvidia_mode_combo = QComboBox()
        self._nvidia_mode_combo.addItem("single", "单线程")
        self._nvidia_mode_combo.addItem("multi", "多线程")
        self._nvidia_mode_combo.setCurrentText(nvidia_mode)
        self._nvidia_mode_combo.setToolTip(
            _KEY_TOOLTIPS.get("summarization.nvidia_mode", "")
        )
        nvidia_form.addRow("NVIDIA 模式:", self._nvidia_mode_combo)
        section_edits["nvidia_mode"] = self._nvidia_mode_combo

        self._nvidia_stream_combo = QComboBox()
        self._nvidia_stream_combo.addItem("true", "是")
        self._nvidia_stream_combo.addItem("false", "否")
        nvidia_stream_val = self.settings.get("summarization.nvidia_stream", "true")
        self._nvidia_stream_combo.setCurrentText(nvidia_stream_val)
        self._nvidia_stream_combo.setToolTip(
            _KEY_TOOLTIPS.get("summarization.nvidia_stream", "")
        )
        self._nvidia_stream_row_label = QLabel("NVIDIA 流式输出:")
        self._nvidia_stream_row = nvidia_form.addRow(
            self._nvidia_stream_row_label, self._nvidia_stream_combo
        )
        section_edits["nvidia_stream"] = self._nvidia_stream_combo

        nvidia_thread_count = self.settings.get(
            "summarization.nvidia_thread_count", "5"
        )
        self._nvidia_thread_edit = QLineEdit(nvidia_thread_count)
        self._nvidia_thread_edit.setToolTip(
            _KEY_TOOLTIPS.get("summarization.nvidia_thread_count", "")
        )
        self._nvidia_thread_row_label = QLabel("NVIDIA 线程数:")
        self._nvidia_thread_row = nvidia_form.addRow(
            self._nvidia_thread_row_label, self._nvidia_thread_edit
        )
        section_edits["nvidia_thread_count"] = self._nvidia_thread_edit

        self._nvidia_mode_combo.currentIndexChanged.connect(
            self._on_nvidia_mode_changed
        )
        self._on_nvidia_mode_changed()

        self._add_nvidia_test_button(nvidia_form)
        main_layout.addWidget(self._nvidia_group)

        main_layout.addStretch()

        self._edits["summarization"] = section_edits

        # 连接信号
        self._radio_ollama.toggled.connect(self._on_provider_changed)
        self._on_provider_changed()

        self.tab_widget.addTab(tab, "总结")

    def _on_provider_changed(self) -> None:
        """切换 Ollama / NVIDIA 区域的显示"""
        is_ollama = self._radio_ollama.isChecked()
        self._ollama_group.setVisible(is_ollama)
        self._nvidia_group.setVisible(not is_ollama)

    def _on_nvidia_mode_changed(self) -> None:
        """切换 single/multi 模式时联动显隐流式输出和线程数"""
        is_multi = self._nvidia_mode_combo.currentData() == "多线程"
        self._nvidia_stream_combo.setVisible(not is_multi)
        self._nvidia_stream_row_label.setVisible(not is_multi)
        self._nvidia_thread_edit.setVisible(is_multi)
        self._nvidia_thread_row_label.setVisible(is_multi)

    def _add_nvidia_test_button(self, form: QFormLayout) -> None:
        """添加 NVIDIA 测试连接按钮"""
        btn_row = QHBoxLayout()
        self._nvidia_test_btn = QPushButton("测试连接")
        self._nvidia_test_btn.clicked.connect(self._test_nvidia)
        btn_row.addWidget(self._nvidia_test_btn)
        self._nvidia_status_label = QLabel("")
        btn_row.addWidget(self._nvidia_status_label, 1)
        form.addRow(btn_row)

        self._nvidia_check_thread: Optional[QThread] = None
        self._nvidia_check_worker: Optional[NvidiaCheckWorker] = None

    def _test_nvidia(self) -> None:
        """测试 NVIDIA API 连接"""
        edits = self._edits.get("summarization", {})
        api_url = edits.get("nvidia_api_url")
        url = (
            api_url.text().strip()
            if api_url
            else "https://integrate.api.nvidia.com/v1/chat/completions"
        )
        model_edit = edits.get("nvidia_model")
        model = model_edit.text().strip() if model_edit else ""

        self._nvidia_status_label.setText("测试中...")
        self._nvidia_status_label.setStyleSheet("color: orange")
        self._nvidia_test_btn.setEnabled(False)

        self._wait_async_thread("_nvidia_check_thread")
        thread = QThread()
        worker = NvidiaCheckWorker(url)
        worker.moveToThread(thread)

        def _on_result(ok: bool, latency_ms: float):
            if ok:
                self._nvidia_status_label.setText(f"连接成功 ({latency_ms:.0f}ms)")
                self._nvidia_status_label.setStyleSheet("color: green")
                get_logger("video2text").info(
                    "NVIDIA API 连接: ✓ 成功 (%.0fms) | url=%s model=%s",
                    latency_ms,
                    url,
                    model,
                )
            else:
                self._nvidia_status_label.setText("连接失败，请检查 API Key 和网络")
                self._nvidia_status_label.setStyleSheet("color: red")
                get_logger("video2text").warning(
                    "NVIDIA API 连接: ✗ 失败 | url=%s model=%s", url, model
                )

        def _cleanup():
            self._nvidia_check_thread = None
            self._nvidia_check_worker = None
            self._nvidia_test_btn.setEnabled(True)

        worker.result.connect(_on_result)
        thread.finished.connect(_cleanup)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(worker.deleteLater)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        thread.start()
        self._nvidia_check_thread = thread
        self._nvidia_check_worker = worker
        # get_logger("video2text").info("_nvidia_check_thread started for URL: %s", url)

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

    def _create_model_combo(self, current_value: str, form: QFormLayout) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.setMinimumWidth(250)
        combo.setCurrentText(current_value)
        row = QHBoxLayout()
        row.addWidget(combo, 1)
        refresh_btn = QPushButton("刷新模型列表")
        refresh_btn.clicked.connect(self._refresh_model_list)
        row.addWidget(refresh_btn)
        label = _KEY_LABELS.get("summarization.model_name", "model_name")
        form.addRow(f"{label}:", row)
        self._model_combo = combo
        self._refresh_models_btn = refresh_btn
        return combo

    def _refresh_model_list(self) -> None:
        url = self._get_ollama_url()
        self._set_ollama_status("刷新中...", "orange")
        self._refresh_models_btn.setEnabled(False)
        self._wait_async_thread("_ollama_list_thread")
        thread = QThread()
        worker = OllamaListModelWorker(url)
        worker.moveToThread(thread)

        def _cleanup():
            self._ollama_list_thread = None
            self._ollama_list_worker = None
            self._refresh_models_btn.setEnabled(True)

        worker.result.connect(self._on_model_list_received)
        thread.finished.connect(_cleanup)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(worker.deleteLater)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        thread.start()
        self._ollama_list_thread = thread
        self._ollama_list_worker = worker

    def _on_model_list_received(self, models: list) -> None:
        current_text = self._model_combo.currentText()
        self._model_combo.clear()
        if models:
            self._model_combo.addItems(models)
            idx = self._model_combo.findText(current_text)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)
            elif current_text.strip():
                self._model_combo.insertItem(0, current_text)
                self._model_combo.setCurrentIndex(0)
            self._set_ollama_status(f"找到 {len(models)} 个模型", "green")
        else:
            self._set_ollama_status("未找到模型或连接失败", "red")

    def _add_ollama_service_buttons(self, form: QFormLayout) -> None:
        btn_row = QHBoxLayout()
        self._ollama_start_btn = QPushButton("启动服务")
        self._ollama_start_btn.clicked.connect(self._start_ollama_service)
        btn_row.addWidget(self._ollama_start_btn)
        self._ollama_stop_btn = QPushButton("关闭服务")
        self._ollama_stop_btn.clicked.connect(self._stop_ollama_service)
        btn_row.addWidget(self._ollama_stop_btn)
        self._ollama_test_btn = QPushButton("测试连接")
        self._ollama_test_btn.clicked.connect(self._test_ollama)
        btn_row.addWidget(self._ollama_test_btn)
        self._ollama_status_label = QLabel("")
        btn_row.addWidget(self._ollama_status_label, 1)
        form.addRow(btn_row)

        self._ollama_check_thread: Optional[QThread] = None
        self._ollama_check_worker: Optional[OllamaCheckWorker] = None
        self._ollama_list_thread: Optional[QThread] = None
        self._ollama_list_worker: Optional[OllamaListModelWorker] = None

    def _get_ollama_url(self) -> str:
        edits = self._edits.get("summarization", {})
        url_edit = edits.get("ollama_url")
        if url_edit is not None:
            return url_edit.text().strip() or "http://127.0.0.1:11434"
        return "http://127.0.0.1:11434"

    def _set_ollama_status(self, text: str, color: str) -> None:
        self._ollama_status_label.setText(text)
        self._ollama_status_label.setStyleSheet(f"color: {color}")

    def _cleanup_check_thread(self) -> None:
        self._ollama_check_thread = None
        self._ollama_check_worker = None

    def _test_ollama(self) -> None:
        url = self._get_ollama_url()
        model = self._model_combo.currentText().strip() if self._model_combo else ""
        self._set_ollama_status("测试中...", "orange")
        self._wait_async_thread("_ollama_check_thread")
        thread = QThread()
        worker = OllamaCheckWorker(url, model)
        worker.moveToThread(thread)
        worker.result.connect(self._on_check_result)
        thread.finished.connect(self._cleanup_check_thread)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(worker.deleteLater)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        thread.start()
        self._ollama_check_thread = thread
        self._ollama_check_worker = worker

    def _on_check_result(self, ok: bool, latency_ms: float) -> None:
        url = self._get_ollama_url()
        model = self._model_combo.currentText().strip() if self._model_combo else ""
        if ok:
            self._set_ollama_status(f"连接成功 ({latency_ms:.0f}ms)", "green")
            get_logger("video2text").info(
                "Ollama 连接: ✓ 成功 (%.0fms) | url=%s model=%s",
                latency_ms,
                url,
                model,
            )
        else:
            self._set_ollama_status("连接失败", "red")
            get_logger("video2text").warning(
                "Ollama 连接: ✗ 失败 | url=%s model=%s", url, model
            )

    def _start_ollama_service(self) -> None:
        url = self._get_ollama_url()
        self._set_ollama_status("正在启动...", "orange")
        self._ollama_start_btn.setEnabled(False)
        self._wait_async_thread("_ollama_start_thread")
        thread = QThread()
        worker = OllamaStartServiceWorker(url)
        worker.moveToThread(thread)

        def _cleanup():
            self._ollama_start_thread = None
            self._ollama_start_worker = None
            self._ollama_start_btn.setEnabled(True)

        worker.result.connect(self._on_start_result)
        thread.finished.connect(_cleanup)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(worker.deleteLater)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        thread.start()
        self._ollama_start_thread = thread
        self._ollama_start_worker = worker

    def _on_start_result(self, ok: bool, status: str) -> None:
        logger = get_logger("video2text")
        if ok:
            if status == "already_running":
                self._set_ollama_status("Ollama 服务已在运行中", "green")
                logger.info("Ollama 服务启动: ✓ 已在运行")
            else:
                self._set_ollama_status("Ollama 服务已启动", "green")
                logger.info("Ollama 服务启动: ✓ 成功")
        elif status == "not_found":
            self._set_ollama_status("未找到ollama命令", "red")
            logger.warning("Ollama 服务启动: ✗ 未找到ollama命令")
            QMessageBox.warning(
                self,
                "提示",
                "未找到ollama命令，请确保已安装Ollama。\n"
                "可以从 https://ollama.com/download 下载安装。",
            )
        elif status == "timeout":
            self._set_ollama_status("启动超时，请稍后测试连接", "orange")
            logger.warning("Ollama 服务启动: ✗ 超时")
        else:
            self._set_ollama_status("启动失败", "red")
            logger.error("Ollama 服务启动: ✗ 失败")

    def _stop_ollama_service(self) -> None:
        url = self._get_ollama_url()
        self._set_ollama_status("正在关闭...", "orange")
        if OllamaClient._service_process is None:
            self._set_ollama_status("Ollama 非本程序启动，无法关闭", "orange")
            return
        OllamaClient.stop_service()
        if OllamaClient.is_service_running(url):
            self._set_ollama_status("关闭失败，服务仍在运行", "red")
        else:
            self._set_ollama_status("Ollama 服务已关闭", "green")

    def _wait_async_thread(self, attr_name: str, timeout_ms: int = 3000) -> None:
        old_thread = getattr(self, attr_name, None)
        if old_thread is None:
            return
        try:
            if old_thread.isRunning():
                old_thread.quit()
                old_thread.wait(timeout_ms)
        except RuntimeError:
            setattr(self, attr_name, None)

    def closeEvent(self, event) -> None:
        for attr in (
            "_ollama_check_thread",
            "_ollama_list_thread",
            "_ollama_start_thread",
            "_nvidia_check_thread",
        ):
            thread = getattr(self, attr, None)
            if thread is not None:
                try:
                    if thread.isRunning():
                        thread.quit()
                        thread.wait(2000)
                except RuntimeError:
                    pass
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
        return widget.text()  # type: ignore[union-attr]

    @staticmethod
    def _set_widget_text(widget: QWidget, value: str) -> None:
        if isinstance(widget, QComboBox):
            widget.setCurrentText(value)
        else:
            widget.setText(value)  # type: ignore[union-attr]

    def _save(self) -> None:
        for section, edits in self._edits.items():
            for key, widget in edits.items():
                self.settings.set(f"{section}.{key}", self._widget_text(widget))

        # 保存服务商选择
        if hasattr(self, "_radio_nvidia"):
            provider = "nvidia" if self._radio_nvidia.isChecked() else "ollama"
            self.settings.set("summarization.provider", provider)

        self.settings.save()
        self.accept()

    def _reset(self) -> None:
        for section, edits in self._edits.items():
            items = self.settings.config.items(section)
            for key, widget in edits.items():
                self._set_widget_text(widget, dict(items).get(key, ""))

        # 重置服务商选择
        if hasattr(self, "_radio_ollama"):
            provider = self.settings.get("summarization.provider", "ollama")
            self._radio_ollama.setChecked(provider != "nvidia")
            self._radio_nvidia.setChecked(provider == "nvidia")
