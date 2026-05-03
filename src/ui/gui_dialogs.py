"""GUI 对话框组件"""

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.config.settings import Settings


class VideoSelectionDialog(QDialog):
    """视频文件选择对话框"""

    def __init__(self, video_files: list[str], parent=None) -> None:
        super().__init__(parent)
        self.video_files = video_files
        self._init_ui()

    def _init_ui(self) -> None:
        self.setWindowTitle("选择视频文件")
        self.resize(600, 500)

        layout = QVBoxLayout(self)

        info_label = QLabel(
            f"共找到 {len(self.video_files)} 个视频文件，请选择需要处理的文件："
        )
        layout.addWidget(info_label)

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        for file_path in self.video_files:
            item = QListWidgetItem(Path(file_path).name)
            item.setData(Qt.ItemDataRole.UserRole, file_path)
            item.setCheckState(Qt.CheckState.Checked)
            self.file_list.addItem(item)
        layout.addWidget(self.file_list)

        button_layout = QHBoxLayout()
        select_all_btn = QPushButton("全选")
        select_all_btn.clicked.connect(self._select_all)
        button_layout.addWidget(select_all_btn)
        deselect_all_btn = QPushButton("取消全选")
        deselect_all_btn.clicked.connect(self._deselect_all)
        button_layout.addWidget(deselect_all_btn)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        ok_cancel_layout = QHBoxLayout()
        ok_btn = QPushButton("确定")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        ok_cancel_layout.addWidget(ok_btn)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        ok_cancel_layout.addWidget(cancel_btn)
        layout.addLayout(ok_cancel_layout)

    def _select_all(self) -> None:
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            item.setCheckState(Qt.CheckState.Checked)

    def _deselect_all(self) -> None:
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            item.setCheckState(Qt.CheckState.Unchecked)

    def get_selected_files(self) -> list[str]:
        selected: list[str] = []
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected.append(item.data(Qt.ItemDataRole.UserRole))
        return selected


_SECTION_LABELS: dict[str, str] = {
    "app": "应用",
    "transcription": "转写",
    "summarization": "总结",
    "preprocessing": "预处理",
    "output": "输出",
    "network": "网络",
    "paths": "路径",
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
    "summarization.ollama_url": "Ollama服务地址",
    "summarization.model_name": "模型名称",
    "summarization.max_length": "最大长度",
    "summarization.temperature": "温度",
    "summarization.timeout": "超时时间",
    "summarization.custom_prompt": "自定义提示词",
    "preprocessing.ffmpeg_path": "FFmpeg路径",
    "preprocessing.audio_sample_rate": "音频采样率",
    "preprocessing.audio_channels": "音频声道数",
    "preprocessing.max_chunk_duration": "最大分段时长",
    "preprocessing.supported_video_formats": "支持的视频格式",
    "output.output_dir": "输出目录",
    "output.transcript_format": "转写格式",
    "output.summary_format": "摘要格式",
    "output.json_output": "JSON输出",
    "network.proxy": "代理地址",
    "paths.models_dir": "模型目录",
    "paths.logs_dir": "日志目录",
    "paths.video_dir": "视频目录",
}


class ConfigEditorDialog(QDialog):
    """配置编辑对话框 —— 按 config.ini 的 section 分 tab 展示所有配置项"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.settings = Settings()
        self._edits: dict[str, dict[str, QLineEdit]] = {}
        self._init_ui()

    def _init_ui(self) -> None:
        self.setWindowTitle("编辑配置")
        self.resize(600, 480)

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
        tab = QWidget()
        form = QFormLayout(tab)
        form.setContentsMargins(8, 8, 8, 8)

        section_edits: dict[str, QLineEdit] = {}
        items = self.settings.config.items(section)

        for key, value in items:
            edit = QLineEdit(value)
            full_key = f"{section}.{key}"
            label = _KEY_LABELS.get(full_key, key)
            if full_key in Settings.PATH_KEYS:
                row = QHBoxLayout()
                row.addWidget(edit, 1)
                browse_btn = QPushButton("浏览")
                browse_btn.setProperty("_path_edit", edit)
                browse_btn.clicked.connect(self._browse_dir)
                row.addWidget(browse_btn)
                form.addRow(f"{label}:", row)
            else:
                form.addRow(f"{label}:", edit)
            section_edits[key] = edit

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

    def _on_button_clicked(self, button: QAbstractButton) -> None:
        role = self._btn_box.buttonRole(button)
        if role == QDialogButtonBox.ButtonRole.AcceptRole:
            self._save()
        elif role == QDialogButtonBox.ButtonRole.ResetRole:
            self._reset()
        else:
            self.reject()

    def _save(self) -> None:
        for section, edits in self._edits.items():
            for key, edit in edits.items():
                self.settings.set(f"{section}.{key}", edit.text())
        self.settings.save()
        self.accept()

    def _reset(self) -> None:
        for section, edits in self._edits.items():
            items = self.settings.config.items(section)
            for key, edit in edits.items():
                edit.setText(dict(items).get(key, ""))
