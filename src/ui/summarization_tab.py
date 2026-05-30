"""总结配置 Tab —— Ollama / NVIDIA / 智谱切换、配置表单、服务管理"""

from typing import Optional

from PySide6.QtCore import QThread
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
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
    OllamaStopServiceWorker,
    ZhipuCheckWorker,
)
from src.utils.logger import get_logger

_SUMM_KEY_LABELS: dict[str, str] = {
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
    "summarization.zhipu_model": "智谱模型",
    "summarization.zhipu_max_tokens": "最大 Token 数",
    "summarization.zhipu_temperature": "温度",
    "summarization.zhipu_mode": "智谱模式",
    "summarization.zhipu_thread_count": "智谱线程数",
    "summarization.zhipu_stream": "智谱流式输出",
}

_SUMM_KEY_TOOLTIPS: dict[str, str] = {
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
    "summarization.zhipu_model": "智谱模型标识，如 glm-4.7",
    "summarization.zhipu_max_tokens": "最大生成 token 数",
    "summarization.zhipu_temperature": "生成温度 (0~2)",
    "summarization.zhipu_mode": "智谱模式: single (单线程，支持流式), multi (多线程并发)",
    "summarization.zhipu_thread_count": "多线程模式下的并发线程数",
    "summarization.zhipu_stream": "是否启用流式输出 (仅单线程模式有效): true / false",
}


class SummarizationTab(QWidget):
    """总结配置 Tab —— 封装 Ollama / NVIDIA / 智谱切换、参数配置表单、服务连接检测与模型管理。"""

    def __init__(self, settings: Settings, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._section_edits: dict[str, QWidget] = {}
        self._init_ui()

    def get_section_edits(self) -> dict[str, QWidget]:
        """返回 summarization section 的所有编辑控件，供 ConfigEditorDialog 统一保存"""
        return self._section_edits

    def get_provider(self) -> str:
        """获取当前选择的总结提供商名称（'ollama'、'nvidia' 或 'zhipu'）。"""
        if self._radio_nvidia.isChecked():
            return "nvidia"
        if self._radio_zhipu.isChecked():
            return "zhipu"
        return "ollama"

    def set_provider(self, provider: str) -> None:
        """设置 provider 选择（用于 _reset）"""
        self._radio_ollama.setChecked(provider not in ("nvidia", "zhipu"))
        self._radio_nvidia.setChecked(provider == "nvidia")
        self._radio_zhipu.setChecked(provider == "zhipu")

    def cleanup_threads(self) -> None:
        """关闭所有异步线程，供 closeEvent 调用"""
        for attr in (
            "_ollama_check_thread",
            "_ollama_list_thread",
            "_ollama_start_thread",
            "_nvidia_check_thread",
            "_zhipu_check_thread",
        ):
            thread = getattr(self, attr, None)
            if thread is not None:
                try:
                    if thread.isRunning():
                        thread.quit()
                        thread.wait(2000)
                except RuntimeError:
                    pass

    # ---- UI 构建 ----

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # ---- 服务商选择 ----
        provider_group = QGroupBox("总结服务")
        provider_layout = QHBoxLayout(provider_group)
        self._radio_ollama = QRadioButton("本地 Ollama 模型")
        self._radio_nvidia = QRadioButton("在线 NVIDIA 模型")
        self._radio_zhipu = QRadioButton("在线智谱模型")
        current_provider = self._settings.get("summarization.provider", "ollama")
        if current_provider == "nvidia":
            self._radio_nvidia.setChecked(True)
        elif current_provider == "zhipu":
            self._radio_zhipu.setChecked(True)
        else:
            self._radio_ollama.setChecked(True)
        provider_layout.addWidget(self._radio_ollama)
        provider_layout.addWidget(self._radio_nvidia)
        provider_layout.addWidget(self._radio_zhipu)
        main_layout.addWidget(provider_group)

        # ---- Ollama 区域 ----
        self._ollama_group = QGroupBox("Ollama 配置")
        ollama_form = QFormLayout(self._ollama_group)
        ollama_form.setContentsMargins(8, 8, 8, 8)

        ollama_items = {
            "ollama_url": self._settings.get(
                "summarization.ollama_url", "http://127.0.0.1:11434"
            ),
            "model_name": self._settings.get("summarization.model_name", ""),
            "max_length": self._settings.get("summarization.max_length", "10000"),
            "temperature": self._settings.get("summarization.temperature", "0.7"),
            "timeout": self._settings.get("summarization.timeout", "600"),
        }

        for key, value in ollama_items.items():
            full_key = f"summarization.{key}"
            if key == "model_name":
                widget = self._create_model_combo(value, ollama_form)
            else:
                widget = QLineEdit(value)
                label = _SUMM_KEY_LABELS.get(full_key, key)
                ollama_form.addRow(f"{label}:", widget)
            tooltip = _SUMM_KEY_TOOLTIPS.get(full_key)
            if tooltip:
                widget.setToolTip(tooltip)
            self._section_edits[key] = widget

        self._add_ollama_service_buttons(ollama_form)
        main_layout.addWidget(self._ollama_group)

        # ---- NVIDIA 区域 ----
        self._nvidia_group = QGroupBox("NVIDIA 配置")
        nvidia_form = QFormLayout(self._nvidia_group)
        nvidia_form.setContentsMargins(8, 8, 8, 8)

        nvidia_items = {
            "nvidia_api_url": self._settings.get(
                "summarization.nvidia_api_url",
                "https://integrate.api.nvidia.com/v1/chat/completions",
            ),
            "nvidia_model": self._settings.get(
                "summarization.nvidia_model", "openai/gpt-oss-120b"
            ),
            "nvidia_max_tokens": self._settings.get(
                "summarization.nvidia_max_tokens", "100000"
            ),
            "nvidia_temperature": self._settings.get(
                "summarization.nvidia_temperature", "1.0"
            ),
            "nvidia_top_p": self._settings.get("summarization.nvidia_top_p", "1.0"),
            "nvidia_frequency_penalty": self._settings.get(
                "summarization.nvidia_frequency_penalty", "0.0"
            ),
            "nvidia_presence_penalty": self._settings.get(
                "summarization.nvidia_presence_penalty", "0.0"
            ),
        }

        for key, value in nvidia_items.items():
            full_key = f"summarization.{key}"
            widget = QLineEdit(value)
            tooltip = _SUMM_KEY_TOOLTIPS.get(full_key)
            if tooltip:
                widget.setToolTip(tooltip)
            label = _SUMM_KEY_LABELS.get(full_key, key)
            nvidia_form.addRow(f"{label}:", widget)
            self._section_edits[key] = widget

        nvidia_mode = self._settings.get("summarization.nvidia_mode", "single")

        self._nvidia_mode_combo = QComboBox()
        self._nvidia_mode_combo.addItem("single", "单线程")
        self._nvidia_mode_combo.addItem("multi", "多线程")
        self._nvidia_mode_combo.setCurrentText(nvidia_mode)
        self._nvidia_mode_combo.setToolTip(
            _SUMM_KEY_TOOLTIPS.get("summarization.nvidia_mode", "")
        )
        nvidia_form.addRow("NVIDIA 模式:", self._nvidia_mode_combo)
        self._section_edits["nvidia_mode"] = self._nvidia_mode_combo

        self._nvidia_stream_combo = QComboBox()
        self._nvidia_stream_combo.addItem("true", "是")
        self._nvidia_stream_combo.addItem("false", "否")
        nvidia_stream_val = self._settings.get("summarization.nvidia_stream", "true")
        self._nvidia_stream_combo.setCurrentText(nvidia_stream_val)
        self._nvidia_stream_combo.setToolTip(
            _SUMM_KEY_TOOLTIPS.get("summarization.nvidia_stream", "")
        )
        self._nvidia_stream_row_label = QLabel("NVIDIA 流式输出:")
        self._nvidia_stream_row = nvidia_form.addRow(
            self._nvidia_stream_row_label, self._nvidia_stream_combo
        )
        self._section_edits["nvidia_stream"] = self._nvidia_stream_combo

        nvidia_thread_count = self._settings.get(
            "summarization.nvidia_thread_count", "5"
        )
        self._nvidia_thread_edit = QLineEdit(nvidia_thread_count)
        self._nvidia_thread_edit.setToolTip(
            _SUMM_KEY_TOOLTIPS.get("summarization.nvidia_thread_count", "")
        )
        self._nvidia_thread_row_label = QLabel("NVIDIA 线程数:")
        self._nvidia_thread_row = nvidia_form.addRow(
            self._nvidia_thread_row_label, self._nvidia_thread_edit
        )
        self._section_edits["nvidia_thread_count"] = self._nvidia_thread_edit

        self._nvidia_mode_combo.currentIndexChanged.connect(
            self._on_nvidia_mode_changed
        )
        self._on_nvidia_mode_changed()

        self._add_nvidia_test_button(nvidia_form)
        main_layout.addWidget(self._nvidia_group)

        # ---- 智谱区域 ----
        self._zhipu_group = QGroupBox("智谱配置")
        zhipu_form = QFormLayout(self._zhipu_group)
        zhipu_form.setContentsMargins(8, 8, 8, 8)

        zhipu_items = {
            "zhipu_model": self._settings.get("summarization.zhipu_model", "glm-4.7"),
            "zhipu_max_tokens": self._settings.get(
                "summarization.zhipu_max_tokens", "65536"
            ),
            "zhipu_temperature": self._settings.get(
                "summarization.zhipu_temperature", "1.0"
            ),
        }

        for key, value in zhipu_items.items():
            full_key = f"summarization.{key}"
            widget = QLineEdit(value)
            tooltip = _SUMM_KEY_TOOLTIPS.get(full_key)
            if tooltip:
                widget.setToolTip(tooltip)
            label = _SUMM_KEY_LABELS.get(full_key, key)
            zhipu_form.addRow(f"{label}:", widget)
            self._section_edits[key] = widget

        zhipu_mode = self._settings.get("summarization.zhipu_mode", "single")

        self._zhipu_mode_combo = QComboBox()
        self._zhipu_mode_combo.addItem("single", "单线程")
        self._zhipu_mode_combo.addItem("multi", "多线程")
        self._zhipu_mode_combo.setCurrentText(zhipu_mode)
        self._zhipu_mode_combo.setToolTip(
            _SUMM_KEY_TOOLTIPS.get("summarization.zhipu_mode", "")
        )
        zhipu_form.addRow("智谱模式:", self._zhipu_mode_combo)
        self._section_edits["zhipu_mode"] = self._zhipu_mode_combo

        self._zhipu_stream_combo = QComboBox()
        self._zhipu_stream_combo.addItem("true", "是")
        self._zhipu_stream_combo.addItem("false", "否")
        zhipu_stream_val = self._settings.get("summarization.zhipu_stream", "true")
        self._zhipu_stream_combo.setCurrentText(zhipu_stream_val)
        self._zhipu_stream_combo.setToolTip(
            _SUMM_KEY_TOOLTIPS.get("summarization.zhipu_stream", "")
        )
        self._zhipu_stream_row_label = QLabel("智谱流式输出:")
        self._zhipu_stream_row = zhipu_form.addRow(
            self._zhipu_stream_row_label, self._zhipu_stream_combo
        )
        self._section_edits["zhipu_stream"] = self._zhipu_stream_combo

        zhipu_thread_count = self._settings.get("summarization.zhipu_thread_count", "5")
        self._zhipu_thread_edit = QLineEdit(zhipu_thread_count)
        self._zhipu_thread_edit.setToolTip(
            _SUMM_KEY_TOOLTIPS.get("summarization.zhipu_thread_count", "")
        )
        self._zhipu_thread_row_label = QLabel("智谱线程数:")
        self._zhipu_thread_row = zhipu_form.addRow(
            self._zhipu_thread_row_label, self._zhipu_thread_edit
        )
        self._section_edits["zhipu_thread_count"] = self._zhipu_thread_edit

        self._zhipu_mode_combo.currentIndexChanged.connect(self._on_zhipu_mode_changed)
        self._on_zhipu_mode_changed()

        self._add_zhipu_test_button(zhipu_form)
        main_layout.addWidget(self._zhipu_group)

        main_layout.addStretch()

        # 连接信号
        self._radio_ollama.toggled.connect(self._on_provider_changed)
        self._radio_nvidia.toggled.connect(self._on_provider_changed)
        self._radio_zhipu.toggled.connect(self._on_provider_changed)
        self._on_provider_changed()

    def _on_provider_changed(self) -> None:
        """切换 Ollama / NVIDIA / 智谱区域的显示"""
        self._ollama_group.setVisible(self._radio_ollama.isChecked())
        self._nvidia_group.setVisible(self._radio_nvidia.isChecked())
        self._zhipu_group.setVisible(self._radio_zhipu.isChecked())

    def _on_nvidia_mode_changed(self) -> None:
        """切换 single/multi 模式时联动显隐流式输出和线程数"""
        is_multi = self._nvidia_mode_combo.currentData() == "多线程"
        self._nvidia_stream_combo.setVisible(not is_multi)
        self._nvidia_stream_row_label.setVisible(not is_multi)
        self._nvidia_thread_edit.setVisible(is_multi)
        self._nvidia_thread_row_label.setVisible(is_multi)

    def _on_zhipu_mode_changed(self) -> None:
        """切换智谱 single/multi 模式时联动显隐流式输出和线程数"""
        is_multi = self._zhipu_mode_combo.currentData() == "多线程"
        self._zhipu_stream_combo.setVisible(not is_multi)
        self._zhipu_stream_row_label.setVisible(not is_multi)
        self._zhipu_thread_edit.setVisible(is_multi)
        self._zhipu_thread_row_label.setVisible(is_multi)

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
        api_url = self._section_edits.get("nvidia_api_url")
        url = (
            api_url.text().strip()
            if api_url
            else "https://integrate.api.nvidia.com/v1/chat/completions"
        )
        model_edit = self._section_edits.get("nvidia_model")
        model = model_edit.text().strip() if model_edit else ""

        self._nvidia_status_label.setText("测试中...")
        self._nvidia_status_label.setStyleSheet("color: orange")
        self._nvidia_test_btn.setEnabled(False)

        self._wait_async_thread("_nvidia_check_thread")
        thread = QThread()
        worker = NvidiaCheckWorker(url, model=model)
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

    def _add_zhipu_test_button(self, form: QFormLayout) -> None:
        """添加智谱测试连接按钮"""
        btn_row = QHBoxLayout()
        self._zhipu_test_btn = QPushButton("测试连接")
        self._zhipu_test_btn.clicked.connect(self._test_zhipu)
        btn_row.addWidget(self._zhipu_test_btn)
        self._zhipu_status_label = QLabel("")
        btn_row.addWidget(self._zhipu_status_label, 1)
        form.addRow(btn_row)

        self._zhipu_check_thread: Optional[QThread] = None
        self._zhipu_check_worker: Optional[ZhipuCheckWorker] = None

    def _test_zhipu(self) -> None:
        """测试智谱 API 连接"""
        model_edit = self._section_edits.get("zhipu_model")
        model = model_edit.text().strip() if model_edit else ""

        self._zhipu_status_label.setText("测试中...")
        self._zhipu_status_label.setStyleSheet("color: orange")
        self._zhipu_test_btn.setEnabled(False)

        self._wait_async_thread("_zhipu_check_thread")
        thread = QThread()
        worker = ZhipuCheckWorker(model=model)
        worker.moveToThread(thread)

        def _on_result(ok: bool, latency_ms: float):
            if ok:
                self._zhipu_status_label.setText(f"连接成功 ({latency_ms:.0f}ms)")
                self._zhipu_status_label.setStyleSheet("color: green")
                get_logger("video2text").info(
                    "智谱 API 连接: ✓ 成功 (%.0fms) | model=%s",
                    latency_ms,
                    model,
                )
            else:
                self._zhipu_status_label.setText("连接失败，请检查 API Key 和网络")
                self._zhipu_status_label.setStyleSheet("color: red")
                get_logger("video2text").warning(
                    "智谱 API 连接: ✗ 失败 | model=%s", model
                )

        def _cleanup():
            self._zhipu_check_thread = None
            self._zhipu_check_worker = None
            self._zhipu_test_btn.setEnabled(True)

        worker.result.connect(_on_result)
        thread.finished.connect(_cleanup)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(worker.deleteLater)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        thread.start()
        self._zhipu_check_thread = thread
        self._zhipu_check_worker = worker

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
        label = _SUMM_KEY_LABELS.get("summarization.model_name", "model_name")
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
        url_edit = self._section_edits.get("ollama_url")
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

    def _on_check_result(self, ok: bool, latency_ms: float, reason: str = "") -> None:
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
            if reason == "connection_failed":
                status_text = "连接失败，请检查 Ollama 服务是否已启动"
            elif reason == "model_not_found":
                status_text = f"连接成功，但模型 '{model}' 不存在"
            elif reason == "error":
                status_text = "检测异常，请查看日志"
            else:
                status_text = "连接失败"
            self._set_ollama_status("连接失败", "red")
            get_logger("video2text").warning(
                "Ollama 连接: ✗ 失败 | reason=%s url=%s model=%s", status_text, url, model
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
        self._ollama_stop_btn.setEnabled(False)
        self._wait_async_thread("_ollama_stop_thread")
        is_external = OllamaClient._service_process is None
        thread = QThread()
        worker = OllamaStopServiceWorker(url, is_external)
        worker.moveToThread(thread)

        def _cleanup():
            self._ollama_stop_thread = None
            self._ollama_stop_worker = None
            self._ollama_stop_btn.setEnabled(True)

        worker.result.connect(self._on_stop_result)
        thread.finished.connect(_cleanup)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(worker.deleteLater)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        thread.start()
        self._ollama_stop_thread = thread
        self._ollama_stop_worker = worker

    def _on_stop_result(self, ok: bool, status: str) -> None:
        if status == "external":
            self._set_ollama_status("Ollama 非本程序启动，无法关闭", "orange")
        elif ok:
            self._set_ollama_status("Ollama 服务已关闭", "green")
        elif status == "still_running":
            self._set_ollama_status("关闭失败，服务仍在运行", "red")
        else:
            self._set_ollama_status("关闭失败", "red")

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
