"""GUI 对话框组件"""

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
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.config.settings import Settings, DEFAULT_OLLAMA_URL, DEFAULT_NVIDIA_API_URL
from src.summarization.ollama_client import OllamaClient
from src.ui.gui_workers import (
    NvidiaCheckWorker,
    OllamaCheckWorker,
    OllamaListModelWorker,
    OllamaStartServiceWorker,
)
from src.utils.logger import get_logger


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
    "summarization.nvidia_api_url": "NVIDIA API 地址",
    "summarization.nvidia_model": "NVIDIA 模型",
    "summarization.nvidia_max_tokens": "最大 Token 数",
    "summarization.nvidia_temperature": "温度",
    "summarization.nvidia_top_p": "Top P",
    "summarization.nvidia_frequency_penalty": "频率惩罚",
    "summarization.nvidia_presence_penalty": "存在惩罚",
    "preprocessing.ffmpeg_path": "FFmpeg路径",
    "preprocessing.audio_sample_rate": "音频采样率",
    "preprocessing.audio_channels": "音频声道数",
    "preprocessing.max_chunk_duration": "最大分段时长",
    "preprocessing.supported_video_formats": "支持的视频格式",
    "output.output_dir": "输出目录",
    "output.transcript_format": "转写格式",
    "output.summary_format": "摘要格式",
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
        self._edits: dict[str, dict[str, QWidget]] = {}
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
                "summarization.ollama_url", DEFAULT_OLLAMA_URL
            ),
            "model_name": self.settings.get("summarization.model_name", ""),
            "max_length": self.settings.get("summarization.max_length", "5000"),
            "temperature": self.settings.get("summarization.temperature", "0.7"),
            "timeout": self.settings.get("summarization.timeout", "300"),
        }

        for key, value in ollama_items.items():
            full_key = f"summarization.{key}"
            if key == "model_name":
                widget = self._create_model_combo(value, ollama_form)
            else:
                widget = QLineEdit(value)
                label = _KEY_LABELS.get(full_key, key)
                ollama_form.addRow(f"{label}:", widget)
            section_edits[key] = widget

        self._add_ollama_service_buttons(ollama_form)
        main_layout.addWidget(self._ollama_group)

        # ---- NVIDIA 区域 ----
        self._nvidia_group = QGroupBox("NVIDIA 配置")
        nvidia_form = QFormLayout(self._nvidia_group)
        nvidia_form.setContentsMargins(8, 8, 8, 8)

        nvidia_items = {
            "nvidia_api_url": self.settings.get(
                "summarization.nvidia_api_url", DEFAULT_NVIDIA_API_URL
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
            label = _KEY_LABELS.get(full_key, key)
            nvidia_form.addRow(f"{label}:", widget)
            section_edits[key] = widget

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
        url = api_url.text().strip() if api_url else DEFAULT_NVIDIA_API_URL

        self._nvidia_status_label.setText("测试中...")
        self._nvidia_status_label.setStyleSheet("color: orange")
        self._nvidia_test_btn.setEnabled(False)

        self._wait_async_thread("_nvidia_check_thread")
        thread = QThread()
        worker = NvidiaCheckWorker(url)
        worker.moveToThread(thread)

        def _on_result(ok: bool):
            if ok:
                self._nvidia_status_label.setText("连接成功")
                self._nvidia_status_label.setStyleSheet("color: green")
            else:
                self._nvidia_status_label.setText("连接失败，请检查 API Key 和网络")
                self._nvidia_status_label.setStyleSheet("color: red")

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
            return url_edit.text().strip() or DEFAULT_OLLAMA_URL
        return DEFAULT_OLLAMA_URL

    def _set_ollama_status(self, text: str, color: str) -> None:
        self._ollama_status_label.setText(text)
        self._ollama_status_label.setStyleSheet(f"color: {color}")

    def _cleanup_check_thread(self) -> None:
        self._ollama_check_thread = None
        self._ollama_check_worker = None

    def _test_ollama(self) -> None:
        url = self._get_ollama_url()
        self._set_ollama_status("测试中...", "orange")
        self._wait_async_thread("_ollama_check_thread")
        thread = QThread()
        worker = OllamaCheckWorker(url)
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

    def _on_check_result(self, ok: bool) -> None:
        url = self._get_ollama_url()
        if ok:
            self._set_ollama_status("连接成功", "green")
            get_logger("video2text").info("Ollama 连接测试成功: %s", url)
        else:
            self._set_ollama_status("连接失败", "red")
            get_logger("video2text").warning("Ollama 连接测试失败: %s", url)

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
            else:
                self._set_ollama_status("Ollama 服务已启动", "green")
                logger.info("Ollama 服务启动成功")
        elif status == "not_found":
            self._set_ollama_status("未找到ollama命令", "red")
            QMessageBox.warning(
                self,
                "提示",
                "未找到ollama命令，请确保已安装Ollama。\n"
                "可以从 https://ollama.com/download 下载安装。",
            )
        elif status == "timeout":
            self._set_ollama_status("启动超时，请稍后测试连接", "orange")
            logger.warning("Ollama 服务启动超时")
        else:
            self._set_ollama_status("启动失败", "red")
            logger.error("Ollama 服务启动失败")

    def _stop_ollama_service(self) -> None:
        url = self._get_ollama_url()
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
