"""总结配置 Tab —— Ollama / NVIDIA 切换、配置表单、服务管理"""

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
from src.i18n import t
from src.summarization.ollama_client import OllamaClient
from src.ui.gui_workers import (
    CheckWorker,
    OllamaListModelWorker,
    OllamaStartServiceWorker,
    OllamaStopServiceWorker,
)
from src.utils.logger import get_logger

_SUMM_KEY_LABELS: dict[str, str] = {
    "summarization.ollama_url": "summ_labels.ollama_url",
    "summarization.model_name": "summ_labels.model_name",
    "summarization.max_length": "summ_labels.max_length",
    "summarization.temperature": "summ_labels.temperature",
    "summarization.timeout": "summ_labels.timeout",
    "summarization.nvidia_api_url": "summ_labels.nvidia_api_url",
    "summarization.nvidia_model": "summ_labels.nvidia_model",
    "summarization.nvidia_max_tokens": "summ_labels.nvidia_max_tokens",
    "summarization.nvidia_temperature": "summ_labels.nvidia_temperature",
    "summarization.nvidia_top_p": "summ_labels.nvidia_top_p",
    "summarization.nvidia_frequency_penalty": "summ_labels.nvidia_frequency_penalty",
    "summarization.nvidia_presence_penalty": "summ_labels.nvidia_presence_penalty",
    "summarization.nvidia_timeout": "summ_labels.nvidia_timeout",
    "summarization.nvidia_mode": "summ_labels.nvidia_mode",
    "summarization.nvidia_thread_count": "summ_labels.nvidia_thread_count",
    "summarization.nvidia_stream": "summ_labels.nvidia_stream",

}

_SUMM_KEY_TOOLTIPS: dict[str, str] = {
    "summarization.ollama_url": "summ_tooltips.ollama_url",
    "summarization.model_name": "summ_tooltips.model_name",
    "summarization.max_length": "summ_tooltips.max_length",
    "summarization.temperature": "summ_tooltips.temperature",
    "summarization.timeout": "summ_tooltips.timeout",
    "summarization.nvidia_api_url": "summ_tooltips.nvidia_api_url",
    "summarization.nvidia_model": "summ_tooltips.nvidia_model",
    "summarization.nvidia_max_tokens": "summ_tooltips.nvidia_max_tokens",
    "summarization.nvidia_temperature": "summ_tooltips.nvidia_temperature",
    "summarization.nvidia_top_p": "summ_tooltips.nvidia_top_p",
    "summarization.nvidia_frequency_penalty": "summ_tooltips.nvidia_frequency_penalty",
    "summarization.nvidia_presence_penalty": "summ_tooltips.nvidia_presence_penalty",
    "summarization.nvidia_timeout": "summ_tooltips.nvidia_timeout",
    "summarization.nvidia_mode": "summ_tooltips.nvidia_mode",
    "summarization.nvidia_thread_count": "summ_tooltips.nvidia_thread_count",
    "summarization.nvidia_stream": "summ_tooltips.nvidia_stream",

}


class SummarizationTab(QWidget):
    """总结配置 Tab —— 封装 Ollama / NVIDIA 切换、参数配置表单、服务连接检测与模型管理。"""

    def __init__(self, settings: Settings, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._section_edits: dict[str, QWidget] = {}
        self._init_ui()

    def get_section_edits(self) -> dict[str, QWidget]:
        """返回 summarization section 的所有编辑控件，供 ConfigEditorDialog 统一保存"""
        return self._section_edits

    def get_provider(self) -> str:
        """获取当前选择的总结提供商名称（'ollama' 或 'nvidia'）。"""
        if self._radio_nvidia.isChecked():
            return "nvidia"
        return "ollama"

    def set_provider(self, provider: str) -> None:
        """设置 provider 选择（用于 _reset）"""
        self._radio_ollama.setChecked(provider != "nvidia")
        self._radio_nvidia.setChecked(provider == "nvidia")

    def cleanup_threads(self) -> None:
        """关闭所有异步线程，供 closeEvent 调用"""
        for attr in (
            "_ollama_check_thread",
            "_ollama_list_thread",
            "_ollama_start_thread",
            "_ollama_stop_thread",
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

    # ---- UI 构建 ----

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # ---- 服务商选择 ----
        provider_group = QGroupBox(t("summarization_tab.provider_group"))
        provider_layout = QHBoxLayout(provider_group)
        self._radio_ollama = QRadioButton(t("summarization_tab.radio_ollama"))
        self._radio_nvidia = QRadioButton(t("summarization_tab.radio_nvidia"))
        current_provider = self._settings.get("summarization.provider", "ollama")
        if current_provider == "nvidia":
            self._radio_nvidia.setChecked(True)
        else:
            self._radio_ollama.setChecked(True)
        provider_layout.addWidget(self._radio_ollama)
        provider_layout.addWidget(self._radio_nvidia)
        main_layout.addWidget(provider_group)

        # ---- Ollama 区域 ----
        self._ollama_group = QGroupBox(t("summarization_tab.ollama_group"))
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
                label_key = _SUMM_KEY_LABELS.get(full_key)
                label = t(label_key) if label_key else key
                ollama_form.addRow(f"{label}:", widget)
            tooltip_key = _SUMM_KEY_TOOLTIPS.get(full_key)
            if tooltip_key:
                widget.setToolTip(t(tooltip_key))
            self._section_edits[key] = widget

        self._add_ollama_service_buttons(ollama_form)
        main_layout.addWidget(self._ollama_group)

        # ---- NVIDIA 区域 ----
        self._nvidia_group = QGroupBox(t("summarization_tab.nvidia_group"))
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
            "nvidia_timeout": self._settings.get("summarization.nvidia_timeout", "600"),
        }

        for key, value in nvidia_items.items():
            full_key = f"summarization.{key}"
            widget = QLineEdit(value)
            tooltip_key = _SUMM_KEY_TOOLTIPS.get(full_key)
            if tooltip_key:
                widget.setToolTip(t(tooltip_key))
            label_key = _SUMM_KEY_LABELS.get(full_key)
            label = t(label_key) if label_key else key
            nvidia_form.addRow(f"{label}:", widget)
            self._section_edits[key] = widget

        self._nvidia_mode_combo = QComboBox()
        self._nvidia_mode_combo.addItem(t("summarization_tab.mode_single"), "single")
        self._nvidia_mode_combo.addItem(t("summarization_tab.mode_multi"), "multi")
        nvidia_mode_val = self._settings.get("summarization.nvidia_mode", "single")
        from src.ui.gui_dialogs import ConfigEditorDialog
        ConfigEditorDialog._set_widget_text(self._nvidia_mode_combo, nvidia_mode_val)
        self._nvidia_mode_combo.setToolTip(
            _SUMM_KEY_TOOLTIPS.get("summarization.nvidia_mode", "")
        )
        nvidia_form.addRow(t("summarization_tab.nvidia_mode_label"), self._nvidia_mode_combo)
        self._section_edits["nvidia_mode"] = self._nvidia_mode_combo

        self._nvidia_stream_combo = QComboBox()
        self._nvidia_stream_combo.addItem(t("common.yes"))
        self._nvidia_stream_combo.addItem(t("common.no"))
        nvidia_stream_val = self._settings.get("summarization.nvidia_stream", "true")
        from src.ui.gui_dialogs import ConfigEditorDialog
        ConfigEditorDialog._set_widget_text(self._nvidia_stream_combo, nvidia_stream_val)
        self._nvidia_stream_combo.setToolTip(
            _SUMM_KEY_TOOLTIPS.get("summarization.nvidia_stream", "")
        )
        self._nvidia_stream_row_label = QLabel(t("summarization_tab.nvidia_stream_label"))
        nvidia_form.addRow(
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
        self._nvidia_thread_row_label = QLabel(t("summarization_tab.nvidia_threads_label"))
        nvidia_form.addRow(
            self._nvidia_thread_row_label, self._nvidia_thread_edit
        )
        self._section_edits["nvidia_thread_count"] = self._nvidia_thread_edit

        self._nvidia_mode_combo.currentIndexChanged.connect(
            self._on_nvidia_mode_changed
        )
        self._on_nvidia_mode_changed()

        self._add_nvidia_test_button(nvidia_form)
        main_layout.addWidget(self._nvidia_group)

        main_layout.addStretch()

        # 连接信号
        self._radio_ollama.toggled.connect(self._on_provider_changed)
        self._radio_nvidia.toggled.connect(self._on_provider_changed)
        self._on_provider_changed()

    def _on_provider_changed(self) -> None:
        """切换 Ollama / NVIDIA 区域的显示"""
        self._ollama_group.setVisible(self._radio_ollama.isChecked())
        self._nvidia_group.setVisible(self._radio_nvidia.isChecked())

    def _on_nvidia_mode_changed(self) -> None:
        """切换 single/multi 模式时联动显隐流式输出和线程数"""
        is_multi = self._nvidia_mode_combo.currentData() == "multi"
        self._nvidia_stream_combo.setVisible(not is_multi)
        self._nvidia_stream_row_label.setVisible(not is_multi)
        self._nvidia_thread_edit.setVisible(is_multi)
        self._nvidia_thread_row_label.setVisible(is_multi)

    def _add_nvidia_test_button(self, form: QFormLayout) -> None:
        """添加 NVIDIA 测试连接按钮"""
        btn_row = QHBoxLayout()
        self._nvidia_test_btn = QPushButton(t("summarization_tab.test_btn"))
        self._nvidia_test_btn.clicked.connect(self._test_nvidia)
        btn_row.addWidget(self._nvidia_test_btn)
        self._nvidia_status_label = QLabel("")
        btn_row.addWidget(self._nvidia_status_label, 1)
        form.addRow(btn_row)

        self._nvidia_check_thread: Optional[QThread] = None
        self._nvidia_check_worker: Optional[CheckWorker] = None

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

        self._nvidia_status_label.setText(t("summarization_tab.testing"))
        self._nvidia_status_label.setStyleSheet("color: orange")
        self._nvidia_test_btn.setEnabled(False)

        self._wait_async_thread("_nvidia_check_thread")
        thread = QThread()
        worker = CheckWorker("nvidia", api_url=url, model=model)
        worker.moveToThread(thread)

        def _on_result(ok: bool, latency_ms: float, _detail: str = ""):
            if ok:
                self._nvidia_status_label.setText(t("summarization_tab.status_connected", latency_ms=latency_ms))
                self._nvidia_status_label.setStyleSheet("color: green")
                get_logger("video2text").info(
                    t("summarization_tab.log_nvidia_ok", latency_ms=latency_ms, url=url, model=model)
                )
            else:
                self._nvidia_status_label.setText(t("summarization_tab.status_connect_fail"))
                self._nvidia_status_label.setStyleSheet("color: red")
                get_logger("video2text").warning(
                    t("summarization_tab.log_nvidia_fail", url=url, model=model)
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

    def _create_model_combo(self, current_value: str, form: QFormLayout) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.setMinimumWidth(250)
        combo.setCurrentText(current_value)
        row = QHBoxLayout()
        row.addWidget(combo, 1)
        refresh_btn = QPushButton(t("summarization_tab.refresh_btn"))
        refresh_btn.clicked.connect(self._refresh_model_list)
        row.addWidget(refresh_btn)
        label = t("summarization_tab.model_label")
        form.addRow(f"{label}:", row)
        self._model_combo = combo
        self._refresh_models_btn = refresh_btn
        return combo

    def _refresh_model_list(self) -> None:
        url = self._get_ollama_url()
        self._set_ollama_status(t("summarization_tab.refreshing"), "orange")
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
            self._set_ollama_status(t("summarization_tab.status_models_found", count=len(models)), "green")
        else:
            self._set_ollama_status(t("summarization_tab.status_no_models"), "red")

    def _add_ollama_service_buttons(self, form: QFormLayout) -> None:
        btn_row = QHBoxLayout()
        self._ollama_start_btn = QPushButton(t("summarization_tab.start_btn"))
        self._ollama_start_btn.clicked.connect(self._start_ollama_service)
        btn_row.addWidget(self._ollama_start_btn)
        self._ollama_stop_btn = QPushButton(t("summarization_tab.stop_btn"))
        self._ollama_stop_btn.clicked.connect(self._stop_ollama_service)
        btn_row.addWidget(self._ollama_stop_btn)
        self._ollama_test_btn = QPushButton(t("summarization_tab.test_btn"))
        self._ollama_test_btn.clicked.connect(self._test_ollama)
        btn_row.addWidget(self._ollama_test_btn)
        self._ollama_status_label = QLabel("")
        btn_row.addWidget(self._ollama_status_label, 1)
        form.addRow(btn_row)

        self._ollama_check_thread: Optional[QThread] = None
        self._ollama_check_worker: Optional[CheckWorker] = None
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
        self._set_ollama_status(t("summarization_tab.testing"), "orange")
        self._wait_async_thread("_ollama_check_thread")
        thread = QThread()
        worker = CheckWorker("ollama", url=url, model=model)
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
            self._set_ollama_status(t("summarization_tab.status_connected", latency_ms=latency_ms), "green")
            get_logger("video2text").info(
                t("summarization_tab.log_ollama_ok", latency_ms=latency_ms, url=url, model=model)
            )
        else:
            if reason == "connection_failed":
                status_text = t("summarization_tab.status_connect_fail_ollama")
            elif reason == "model_not_found":
                status_text = t("summarization_tab.status_model_not_found", model=model)
            elif reason == "error":
                status_text = t("summarization_tab.status_check_error")
            else:
                status_text = t("summarization_tab.status_connect_fail")
            self._set_ollama_status(status_text, "red")
            get_logger("video2text").warning(
                t("summarization_tab.log_ollama_fail", reason=status_text, url=url, model=model)
            )

    def _start_ollama_service(self) -> None:
        url = self._get_ollama_url()
        self._set_ollama_status(t("summarization_tab.starting"), "orange")
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
                self._set_ollama_status(t("summarization_tab.status_already_running"), "green")
                logger.info(t("summarization_tab.log_ollama_start_already"))
            else:
                self._set_ollama_status(t("summarization_tab.status_started"), "green")
                logger.info(t("summarization_tab.log_ollama_start_ok"))
        elif status == "not_found":
            self._set_ollama_status(t("summarization_tab.status_cmd_not_found"), "red")
            logger.warning(t("summarization_tab.log_ollama_start_not_found"))
            QMessageBox.warning(
                self,
                t("summarization_tab.ollama_not_found_title"),
                t("summarization_tab.ollama_not_found_msg"),
            )
        elif status == "timeout":
            self._set_ollama_status(t("summarization_tab.status_start_timeout"), "orange")
            logger.warning(t("summarization_tab.log_ollama_start_timeout"))
        else:
            self._set_ollama_status(t("summarization_tab.status_start_failed"), "red")
            logger.error(t("summarization_tab.log_ollama_start_fail"))

    def _stop_ollama_service(self) -> None:
        url = self._get_ollama_url()
        self._set_ollama_status(t("summarization_tab.stopping"), "orange")
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
            self._set_ollama_status(t("summarization_tab.status_stop_external"), "orange")
        elif ok:
            self._set_ollama_status(t("summarization_tab.status_stopped"), "green")
        elif status == "still_running":
            self._set_ollama_status(t("summarization_tab.status_stop_failed"), "red")
        else:
            self._set_ollama_status(t("summarization_tab.status_stop_error"), "red")

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
