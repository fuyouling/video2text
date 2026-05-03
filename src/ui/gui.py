"""Video2Text GUI —— 基于 PySide6 的视频转文本图形界面"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import QFont, QIcon, QTextCursor, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.config.settings import (
    PromptManager,
    Settings,
    APP_VERSION,
    DEFAULT_OLLAMA_URL,
    DEFAULT_OLLAMA_MODEL,
)
from src.summarization.ollama_client import OllamaClient
from src.ui.gui_dialogs import ConfigEditorDialog, VideoSelectionDialog
from src.ui.gui_workers import (
    OllamaCheckWorker,
    OllamaListModelWorker,
    PipelineWorker,
    SummarizeWorker,
    TranscribeWorker,
    UiLogHandler,
    UiLogSignal,
)
from src.ui.result_viewer import ResultViewerWindow
from src.transcription.transcriber import _model_cache as _transcriber_cache
from src.utils.logger import get_logger

SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4",
    ".avi",
    ".mov",
    ".mkv",
    ".flv",
    ".wmv",
    ".webm",
    ".ts",
    ".mts",
    ".m4v",
    ".3gp",
    ".mpeg",
    ".mpg",
    ".vob",
    ".ogv",
    ".rm",
    ".rmvb",
}

if getattr(sys, "frozen", False):
    _PROJECT_ROOT = Path(sys.executable).parent
else:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_OUTPUT_DIR = str(_PROJECT_ROOT / "output")

_BTN_MIN_WIDTH = 100

_VIDEO_FILTER_STR = (
    "视频文件 ("
    + " ".join(f"*{ext}" for ext in sorted(SUPPORTED_VIDEO_EXTENSIONS))
    + ");;所有文件 (*.*)"
)


class MainWindow(QMainWindow):
    """Video2Text 主窗口"""

    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings()
        self.prompt_manager = PromptManager()
        self._video_files: list[str] = []
        self._completed_names: set[str] = set()
        self._worker_thread: Optional[QThread] = None
        self._worker = None
        self._combined = False
        self._result_viewer: Optional[ResultViewerWindow] = None
        self._current_mode = ""
        self._tx_success = 0
        self._tx_fail = 0
        self._sum_success = 0
        self._sum_fail = 0
        self._fail_records: list[tuple[str, str, str]] = []
        self._current_video_name: Optional[str] = None

        self._setup_logging()
        self._init_ui()
        self._load_ollama_config()
        self._load_prompt_templates()

    def _setup_logging(self) -> None:
        self._log_signal = UiLogSignal()
        self._log_signal.message.connect(self._append_log)
        self._ui_handler = UiLogHandler(self._log_signal)

        for name in ("video2text", "src"):
            lg = logging.getLogger(name)
            lg.setLevel(logging.INFO)
            if self._ui_handler not in lg.handlers:
                lg.addHandler(self._ui_handler)

    _MAX_LOG_BLOCKS = 5000

    def _append_log(self, msg: str) -> None:
        self.log_text.append(msg)
        doc = self.log_text.document()
        if doc.blockCount() > self._MAX_LOG_BLOCKS:
            cursor = QTextCursor(doc)
            cursor.movePosition(QTextCursor.Start)
            cursor.movePosition(
                QTextCursor.Down,
                QTextCursor.KeepAnchor,
                doc.blockCount() - self._MAX_LOG_BLOCKS,
            )
            cursor.removeSelectedText()
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_text.setTextCursor(cursor)

    def _init_ui(self) -> None:
        self.setWindowTitle("Video2Text - 视频转文本工具")
        self.resize(1200, 800)

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

        self._create_menu_bar()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ── input row ──
        input_row = QHBoxLayout()
        input_row.addWidget(QLabel("输入:"))
        self.input_edit = QLineEdit()
        self.input_edit.setReadOnly(True)
        self.input_edit.setPlaceholderText("请选择视频文件或文件夹…")
        input_row.addWidget(self.input_edit, 1)

        self.input_file_btn = QPushButton("选择文件")
        self.input_file_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.input_file_btn.clicked.connect(self._select_input_files)
        input_row.addWidget(self.input_file_btn)
        self.input_folder_btn = QPushButton("选择文件夹")
        self.input_folder_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.input_folder_btn.clicked.connect(self._select_input_folder)
        input_row.addWidget(self.input_folder_btn)
        self.open_viewer_btn = QPushButton("全屏查看")
        self.open_viewer_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.open_viewer_btn.setToolTip(
            "在独立窗口中查看所有结果，支持全屏、搜索、导出、书签等功能"
        )
        self.open_viewer_btn.clicked.connect(self._open_result_viewer)
        input_row.addWidget(self.open_viewer_btn)
        root.addLayout(input_row)

        # ── output row ──
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("输出:"))
        self.output_edit = QLineEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setText(_DEFAULT_OUTPUT_DIR)
        output_row.addWidget(self.output_edit, 1)
        self.output_btn = QPushButton("浏览")
        self.output_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.output_btn.clicked.connect(self._select_output_dir)
        output_row.addWidget(self.output_btn)
        self.load_history_btn = QPushButton("加载历史")
        self.load_history_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.load_history_btn.setToolTip("加载输出目录中的历史转写和总结文件")
        self.load_history_btn.clicked.connect(self._load_history_files)
        output_row.addWidget(self.load_history_btn)
        self.pause_btn = QPushButton("暂停")
        self.pause_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.pause_btn.setToolTip("暂停当前转写任务，再次点击可继续")
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._on_pause_resume)
        output_row.addWidget(self.pause_btn)
        root.addLayout(output_row)

        # ── run / progress row ──
        run_row = QHBoxLayout()
        self.progress_label = QLabel("就绪:")
        run_row.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        run_row.addWidget(self.progress_bar, 1)
        self.transcribe_btn = QPushButton("仅转写")
        self.transcribe_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.transcribe_btn.setToolTip("仅执行语音转写，不进行摘要总结")
        self.transcribe_btn.clicked.connect(self._on_transcribe)
        run_row.addWidget(self.transcribe_btn)
        self.summarize_btn = QPushButton("仅总结")
        self.summarize_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.summarize_btn.setToolTip("仅对「文本内容」标签页中的文字进行摘要总结")
        self.summarize_btn.clicked.connect(self._on_summarize)
        run_row.addWidget(self.summarize_btn)
        self.combine_btn = QPushButton("转写+总结")
        self.combine_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.combine_btn.setToolTip("先执行语音转写，完成后自动对转写文本进行摘要总结")
        self.combine_btn.clicked.connect(self._on_pipeline)
        run_row.addWidget(self.combine_btn)
        root.addLayout(run_row)

        # ── splitter: logs + right panel ──
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # left: log panel
        log_group = QGroupBox("日志输出")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        log_layout.addWidget(self.log_text)
        splitter.addWidget(log_group)

        # right: results + ollama config
        right_splitter = QSplitter(Qt.Orientation.Vertical)

        results_group = QGroupBox("结果查看")
        results_layout = QVBoxLayout(results_group)

        content_layout = QHBoxLayout()
        self.file_list = QListWidget()
        self.file_list.setMinimumWidth(180)
        self.file_list.currentItemChanged.connect(self._on_file_selected)
        self.file_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._show_file_context_menu)
        content_layout.addWidget(self.file_list, 1)

        self.result_tabs = QTabWidget()
        self.transcript_view = QTextEdit()
        self.transcript_view.setFont(QFont("Consolas", 9))
        self.transcript_view.setPlaceholderText(
            "转写完成后文本自动填充到此处，可直接编辑修改，\n"
            "修改后点击「仅总结」将编辑后的文本发送给 Ollama 进行摘要。\n"
            "也可直接粘贴任意文本到此处，用于单独总结。"
        )
        self.result_tabs.addTab(self.transcript_view, "文本内容")
        self.summary_view = QTextEdit()
        self.summary_view.setReadOnly(True)
        self.summary_view.setFont(QFont("Consolas", 9))
        self.result_tabs.addTab(self.summary_view, "摘要")
        self.result_tabs.currentChanged.connect(self._on_tab_changed)
        content_layout.addWidget(self.result_tabs, 3)
        results_layout.addLayout(content_layout)
        right_splitter.addWidget(results_group)

        # ollama config panel
        ollama_group = QGroupBox("Ollama 配置(总结模型)")
        ollama_layout = QFormLayout(ollama_group)
        self.ollama_url_edit = QLineEdit()
        self.ollama_url_edit.setPlaceholderText(DEFAULT_OLLAMA_URL)
        ollama_layout.addRow("服务地址:", self.ollama_url_edit)

        # 模型选择（下拉框 + 手动输入）
        model_row = QHBoxLayout()
        self.ollama_model_combo = QComboBox()
        self.ollama_model_combo.setEditable(True)
        self.ollama_model_combo.setMinimumWidth(250)
        self.ollama_model_combo.setPlaceholderText(DEFAULT_OLLAMA_MODEL)
        model_row.addWidget(self.ollama_model_combo, 1)
        self.refresh_models_btn = QPushButton("刷新模型列表")
        self.refresh_models_btn.clicked.connect(self._refresh_model_list)
        model_row.addWidget(self.refresh_models_btn)
        ollama_layout.addRow("模型名称:", model_row)

        self.ollama_temp_spin = QDoubleSpinBox()
        self.ollama_temp_spin.setRange(0.0, 2.0)
        self.ollama_temp_spin.setSingleStep(0.1)
        self.ollama_temp_spin.setDecimals(1)
        ollama_layout.addRow("温度:", self.ollama_temp_spin)
        self.ollama_maxlen_spin = QSpinBox()
        self.ollama_maxlen_spin.setRange(50, 10000)
        self.ollama_maxlen_spin.setSingleStep(50)
        ollama_layout.addRow("最大长度:", self.ollama_maxlen_spin)
        ollama_btn_row = QHBoxLayout()
        self.ollama_start_btn = QPushButton("启动服务")
        self.ollama_start_btn.clicked.connect(self._start_ollama_service)
        ollama_btn_row.addWidget(self.ollama_start_btn)
        self.ollama_stop_btn = QPushButton("关闭服务")
        self.ollama_stop_btn.clicked.connect(self._stop_ollama_service)
        ollama_btn_row.addWidget(self.ollama_stop_btn)
        self.ollama_test_btn = QPushButton("测试连接")
        self.ollama_test_btn.clicked.connect(self._test_ollama)
        ollama_btn_row.addWidget(self.ollama_test_btn)
        self.ollama_save_btn = QPushButton("保存配置")
        self.ollama_save_btn.clicked.connect(self._save_ollama_config)
        ollama_btn_row.addWidget(self.ollama_save_btn)
        self.ollama_status_label = QLabel("")
        ollama_btn_row.addWidget(self.ollama_status_label, 1)
        ollama_layout.addRow(ollama_btn_row)
        right_splitter.addWidget(ollama_group)

        # ── 提示词配置面板 ──
        prompt_group = QGroupBox("提示词配置")
        prompt_layout = QVBoxLayout(prompt_group)

        self.ollama_prompt_edit = QTextEdit()
        self.ollama_prompt_edit.setMaximumHeight(100)
        self.ollama_prompt_edit.setPlaceholderText(
            "自定义总结提示词（可选）：\n"
            "输入您希望模型如何总结的指令，例如：\n"
            "「请用英文总结以下文本，列出3个要点」\n"
            "留空则使用默认提示词。"
        )
        prompt_layout.addWidget(self.ollama_prompt_edit)

        prompt_btn_row = QHBoxLayout()
        self.prompt_template_combo = QComboBox()
        self.prompt_template_combo.setMinimumWidth(150)
        self.prompt_template_combo.setPlaceholderText("选择已保存的提示词…")
        self.prompt_template_combo.currentTextChanged.connect(
            self._on_prompt_template_selected
        )
        prompt_btn_row.addWidget(self.prompt_template_combo, 1)
        self.prompt_save_btn = QPushButton("保存提示词")
        self.prompt_save_btn.clicked.connect(self._save_prompt_template)
        prompt_btn_row.addWidget(self.prompt_save_btn)
        self.prompt_delete_btn = QPushButton("删除提示词")
        self.prompt_delete_btn.clicked.connect(self._delete_prompt_template)
        prompt_btn_row.addWidget(self.prompt_delete_btn)
        prompt_layout.addLayout(prompt_btn_row)

        right_splitter.addWidget(prompt_group)

        right_splitter.setStretchFactor(0, 3)
        right_splitter.setStretchFactor(1, 1)
        right_splitter.setStretchFactor(2, 1)
        splitter.addWidget(right_splitter)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage(f"配置: {self.settings.config_path}")

    def _create_menu_bar(self) -> None:
        menu_bar = self.menuBar()

        settings_menu = menu_bar.addMenu("设置")
        edit_config_action = settings_menu.addAction("编辑配置")
        edit_config_action.triggered.connect(self._show_config_editor)

        help_menu = menu_bar.addMenu("帮助")
        about_action = help_menu.addAction("关于")
        about_action.triggered.connect(self._show_about)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "关于 Video2Text",
            f"<h3>Video2Text</h3>"
            f"<p>版本: {APP_VERSION}</p>"
            f"<p>视频转文本工具 —— 基于 faster-whisper + Ollama 的视频转写与摘要总结工具</p>"
            f"<p>技术栈: faster-whisper · Ollama · PySide6</p>"
            f"<p>许可证: GNU GPL v3</p>"
            f"<p>讨论群: QQ群 296875960</p>",
        )

    def _show_config_editor(self) -> None:
        dialog = ConfigEditorDialog(self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self._load_ollama_config()
            self.status_bar.showMessage("配置已保存", 5000)

    # ── Ollama 配置 ──

    def _load_ollama_config(self) -> None:
        url = self.settings.get("summarization.ollama_url", DEFAULT_OLLAMA_URL)
        model = self.settings.get("summarization.model_name", DEFAULT_OLLAMA_MODEL)
        temp = self.settings.get_float("summarization.temperature", 0.7)
        max_len = self.settings.get_int("summarization.max_length", 5000)
        prompt = self.settings.get("summarization.custom_prompt", "")
        self.ollama_url_edit.setText(url)
        self.ollama_model_combo.setCurrentText(model)
        self.ollama_temp_spin.setValue(temp)
        self.ollama_maxlen_spin.setValue(max_len)
        self.ollama_prompt_edit.setPlainText(prompt)

    def _save_ollama_config(self) -> None:
        self.settings.set("summarization.ollama_url", self.ollama_url_edit.text())
        self.settings.set(
            "summarization.model_name", self.ollama_model_combo.currentText()
        )
        self.settings.set(
            "summarization.temperature", str(self.ollama_temp_spin.value())
        )
        self.settings.set(
            "summarization.max_length", str(self.ollama_maxlen_spin.value())
        )
        self.settings.set(
            "summarization.custom_prompt", self.ollama_prompt_edit.toPlainText()
        )
        try:
            self.settings.save()
            self._set_ollama_status("配置已保存", "green")
        except Exception as e:
            self._set_ollama_status(f"保存失败: {e}", "red")

    # ── 提示词模板管理 ──

    _PLACEHOLDER_PROMPT = "新建"

    def _load_prompt_templates(self) -> None:
        self.prompt_template_combo.blockSignals(True)
        self.prompt_template_combo.clear()
        self.prompt_template_combo.addItem(self._PLACEHOLDER_PROMPT)
        for name in self.prompt_manager.get_names():
            self.prompt_template_combo.addItem(name)
        last_used = self.prompt_manager.get_last_used()
        if last_used:
            idx = self.prompt_template_combo.findText(last_used)
            if idx >= 0:
                self.prompt_template_combo.setCurrentIndex(idx)
                self.ollama_prompt_edit.setPlainText(
                    self.prompt_manager.get_content(last_used)
                )
        self.prompt_template_combo.blockSignals(False)

    def _on_prompt_template_selected(self, name: str) -> None:
        if not name or name == self._PLACEHOLDER_PROMPT:
            return
        content = self.prompt_manager.get_content(name)
        if content is not None:
            self.ollama_prompt_edit.setPlainText(content)
            self.prompt_manager.set_last_used(name)

    def _save_prompt_template(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        content = self.ollama_prompt_edit.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "提示", "提示词内容为空，无法保存。")
            return

        current_name = self.prompt_template_combo.currentText()
        if current_name == self._PLACEHOLDER_PROMPT:
            current_name = ""
        name, ok = QInputDialog.getText(
            self, "保存提示词模板", "模板名称:", text=current_name
        )
        if not ok or not name.strip():
            return

        name = name.strip()
        if name == self._PLACEHOLDER_PROMPT:
            QMessageBox.warning(
                self,
                "提示",
                f"「{self._PLACEHOLDER_PROMPT}」是保留名称，请使用其他名称。",
            )
            return
        self.prompt_manager.set_template(name, content)
        self.prompt_manager.set_last_used(name)

        self.prompt_template_combo.blockSignals(True)
        if self.prompt_template_combo.findText(name) < 0:
            self.prompt_template_combo.addItem(name)
        self.prompt_template_combo.setCurrentText(name)
        self.prompt_template_combo.blockSignals(False)

        self.status_bar.showMessage(f"提示词「{name}」已保存", 5000)

    def _delete_prompt_template(self) -> None:
        name = self.prompt_template_combo.currentText()
        if not name or name == self._PLACEHOLDER_PROMPT:
            QMessageBox.warning(self, "提示", "请先选择要删除的提示词模板。")
            return

        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除提示词模板「{name}」吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.prompt_manager.delete_template(name)

        self.prompt_template_combo.blockSignals(True)
        idx = self.prompt_template_combo.currentIndex()
        self.prompt_template_combo.removeItem(idx)
        self.prompt_template_combo.setCurrentIndex(0)
        self.prompt_template_combo.blockSignals(False)

        self.ollama_prompt_edit.clear()
        self.status_bar.showMessage(f"提示词「{name}」已删除", 5000)

    def _test_ollama(self) -> None:
        url = self.ollama_url_edit.text() or DEFAULT_OLLAMA_URL
        self._set_ollama_status("测试中...", "orange")
        self._wait_async_thread("_ollama_check_thread")
        self._ollama_check_url = url
        self._ollama_check_mode = "test"
        thread = QThread()
        worker = OllamaCheckWorker(url)
        worker.moveToThread(thread)

        def _cleanup():
            self._ollama_check_thread = None
            self._ollama_check_worker = None

        worker.result.connect(self._on_check_result)
        thread.finished.connect(_cleanup)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(worker.deleteLater)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        thread.start()
        self._ollama_check_thread = thread
        self._ollama_check_worker = worker

    def _on_check_result(self, ok: bool) -> None:
        mode = getattr(self, "_ollama_check_mode", "test")
        url = getattr(self, "_ollama_check_url", "")
        if mode == "test":
            if ok:
                self._set_ollama_status("连接成功", "green")
                get_logger("video2text").info("Ollama 连接测试成功: %s", url)
            else:
                self._set_ollama_status("连接失败", "red")
                get_logger("video2text").warning("Ollama 连接测试失败: %s", url)
        elif mode == "start":
            if ok:
                self._set_ollama_status("Ollama 服务已启动", "green")
                get_logger("video2text").info("Ollama 服务启动成功")
            else:
                self._set_ollama_status("服务启动中，请稍后测试", "orange")
                get_logger("video2text").warning("Ollama 服务启动中，请稍后测试连接")

    def _refresh_model_list(self) -> None:
        """从 Ollama 获取可用模型列表并填充下拉框（异步）"""
        url = self.ollama_url_edit.text() or DEFAULT_OLLAMA_URL
        self._set_ollama_status("刷新中...", "orange")
        self.refresh_models_btn.setEnabled(False)
        self._wait_async_thread("_ollama_list_thread")
        thread = QThread()
        worker = OllamaListModelWorker(url)
        worker.moveToThread(thread)

        def _cleanup():
            self._ollama_list_thread = None
            self._ollama_list_worker = None
            self.refresh_models_btn.setEnabled(True)

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
        current_text = self.ollama_model_combo.currentText()
        self.ollama_model_combo.clear()
        if models:
            self.ollama_model_combo.addItems(models)
            idx = self.ollama_model_combo.findText(current_text)
            if idx >= 0:
                self.ollama_model_combo.setCurrentIndex(idx)
            elif current_text.strip():
                self.ollama_model_combo.insertItem(0, current_text)
                self.ollama_model_combo.setCurrentIndex(0)
            self._set_ollama_status(f"找到 {len(models)} 个模型", "green")
            get_logger("video2text").info(
                "模型列表刷新成功，共 %d 个模型: %s", len(models), models
            )
        else:
            self._set_ollama_status("未找到模型或连接失败", "red")
            get_logger("video2text").warning("获取模型列表失败")

    def _start_ollama_service(self) -> None:
        logger = get_logger("video2text")
        url = self.ollama_url_edit.text() or DEFAULT_OLLAMA_URL

        if OllamaClient.is_service_running(url):
            self._set_ollama_status("Ollama 服务已在运行中", "green")
            return

        try:
            logger.info("正在启动Ollama服务...")
            self._set_ollama_status("正在启动...", "orange")

            if not OllamaClient.start_service(url):
                self._set_ollama_status("未找到ollama命令", "red")
                QMessageBox.warning(
                    self,
                    "提示",
                    "未找到ollama命令，请确保已安装Ollama。\n"
                    "可以从 https://ollama.com/download 下载安装。",
                )
                return

            logger.info("Ollama服务启动命令已执行")
            QTimer.singleShot(2000, self._check_ollama_after_start)

        except Exception as e:
            logger.error(f"启动Ollama服务失败: {e}")
            self._set_ollama_status(f"启动失败: {e}", "red")

    def _stop_ollama_service(self) -> None:
        url = self.ollama_url_edit.text() or DEFAULT_OLLAMA_URL
        if OllamaClient._service_process is None:
            self._set_ollama_status("Ollama 非本程序启动，无法关闭", "orange")
            return
        OllamaClient.stop_service()
        if OllamaClient.is_service_running(url):
            self._set_ollama_status("关闭失败，服务仍在运行", "red")
        else:
            self._set_ollama_status("Ollama 服务已关闭", "green")

    def _check_ollama_after_start(self) -> None:
        url = self.ollama_url_edit.text() or DEFAULT_OLLAMA_URL
        self._ollama_check_url = url
        self._ollama_check_mode = "start"
        thread = QThread()
        worker = OllamaCheckWorker(url)
        worker.moveToThread(thread)

        def _cleanup():
            self._ollama_check_thread = None
            self._ollama_check_worker = None

        worker.result.connect(self._on_check_result)
        thread.finished.connect(_cleanup)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(worker.deleteLater)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        thread.start()
        self._ollama_check_thread = thread
        self._ollama_check_worker = worker

    def _on_tab_changed(self, index: int) -> None:
        if index == 0:
            self.status_bar.showMessage(
                "文本内容 —— 可直接编辑，编辑后点击「仅总结」将文本发送给 Ollama 进行摘要"
            )
        elif index == 1:
            self.status_bar.showMessage("摘要结果 —— 由 Ollama 生成（只读）")

    # ── input selection slots ──

    def _select_input_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择视频文件",
            "",
            _VIDEO_FILTER_STR,
        )
        if paths:
            if len(paths) == 1:
                self.input_edit.setText(paths[0])
            else:
                self.input_edit.setText(f"已选择 {len(paths)} 个文件")
            self._video_files = list(paths)
            last_dir = Path(paths[0]).parent.name
            self.output_edit.setText(str(_PROJECT_ROOT / "output" / last_dir))

    def _select_input_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择视频文件夹")
        if folder:
            video_files = self._scan_video_files(folder)
            if not video_files:
                QMessageBox.information(
                    self, "提示", "该文件夹及其子目录中未找到支持的视频文件"
                )
                return

            dialog = VideoSelectionDialog(video_files, self)
            if dialog.exec() == dialog.DialogCode.Accepted:
                selected_files = dialog.get_selected_files()
                if selected_files:
                    self.input_edit.setText(
                        f"{folder} (已选择 {len(selected_files)} 个视频)"
                    )
                    self._video_files = selected_files
                    last_dir = Path(folder).name
                    self.output_edit.setText(str(_PROJECT_ROOT / "output" / last_dir))
                else:
                    QMessageBox.information(self, "提示", "未选择任何视频文件")

    @staticmethod
    def _scan_video_files(folder: str) -> list[str]:
        folder_path = Path(folder)
        files: list[str] = []
        seen: set[str] = set()
        for ext in SUPPORTED_VIDEO_EXTENSIONS:
            for f in folder_path.rglob(f"*{ext}"):
                if f.is_file() and str(f) not in seen:
                    seen.add(str(f))
                    files.append(str(f))
        return sorted(files)

    def _select_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if folder:
            self.output_edit.setText(folder)

    def _load_history_files(self) -> None:
        output_dir = self.output_edit.text().strip() or _DEFAULT_OUTPUT_DIR
        output_path = Path(output_dir)

        if not output_path.exists():
            QMessageBox.warning(self, "提示", f"输出目录不存在: {output_dir}")
            return

        self.file_list.clear()
        self._completed_names.clear()

        transcript_files: list[Path] = []
        for ext in ("txt", "srt", "vtt", "json"):
            transcript_files.extend(output_path.glob(f"*.{ext}"))
        transcript_files.sort(key=lambda p: p.name.lower())

        loaded_count = 0
        for txt_file in transcript_files:
            if txt_file.name.endswith("_summary.txt"):
                continue
            if txt_file.name.endswith("_keywords.txt"):
                continue
            if txt_file.name.endswith("_full.json"):
                continue

            video_name = txt_file.stem
            if video_name not in self._completed_names:
                self._completed_names.add(video_name)
                item = QListWidgetItem(video_name)
                item.setData(Qt.ItemDataRole.UserRole, video_name)
                self.file_list.addItem(item)
                loaded_count += 1

        if loaded_count > 0:
            self.status_bar.showMessage(f"已加载 {loaded_count} 个历史文件")
            self.file_list.setCurrentRow(0)
        else:
            self.status_bar.showMessage("未找到历史文件")

    # ── worker 生成 ──

    def _get_output_dir(self) -> str:
        output_dir = self.output_edit.text().strip() or _DEFAULT_OUTPUT_DIR
        self.output_edit.setText(output_dir)
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        return output_dir

    def _start_worker(self, thread: QThread, worker) -> None:
        """启动 worker 线程并连接通用信号"""
        self._worker_thread = thread
        self._worker = worker

        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        thread.finished.connect(worker.deleteLater)
        thread.start()

    def _set_busy_state(self, busy: bool) -> None:
        self.transcribe_btn.setEnabled(not busy)
        self.summarize_btn.setEnabled(not busy)
        self.combine_btn.setEnabled(not busy)
        self.input_file_btn.setEnabled(not busy)
        self.input_folder_btn.setEnabled(not busy)
        self.output_btn.setEnabled(not busy)
        can_pause = busy and self._current_mode in ("transcribe", "pipeline")
        self.pause_btn.setEnabled(can_pause)
        if not busy:
            self.pause_btn.setText("暂停")

    def _wait_async_thread(self, attr_name: str, timeout_ms: int = 3000) -> None:
        """等待指定属性存储的旧 QThread 结束，防止引用泄漏。"""
        old_thread = getattr(self, attr_name, None)
        if old_thread is None:
            return
        try:
            if old_thread.isRunning():
                old_thread.quit()
                old_thread.wait(timeout_ms)
        except RuntimeError:
            setattr(self, attr_name, None)

    def _reset_counters(self) -> None:
        self._tx_success = 0
        self._tx_fail = 0
        self._sum_success = 0
        self._sum_fail = 0
        self._fail_records = []

    def _set_ollama_status(self, text: str, color: str) -> None:
        self.ollama_status_label.setText(text)
        self.ollama_status_label.setStyleSheet(f"color: {color}")

    def _on_worker_error(self, msg: str) -> None:
        mode_map = {
            "transcribe": "转写",
            "summarize": "总结",
            "pipeline": "管道",
        }
        label = mode_map.get(self._current_mode, "任务")
        self.status_bar.showMessage(f"{label}异常: {msg}", 5000)

    def _on_pause_resume(self) -> None:
        if self._worker is None:
            return
        if not hasattr(self._worker, "pause") or not hasattr(self._worker, "resume"):
            return

        if self._worker.is_paused:
            self._worker.resume()
            self.pause_btn.setText("暂停")
            self.status_bar.showMessage("转写已继续")
        else:
            self._worker.pause()
            self.pause_btn.setText("继续")
            self.status_bar.showMessage("转写已暂停")

    # ── 仅转写 ──

    def _on_transcribe(self) -> None:
        if not self._video_files:
            QMessageBox.warning(self, "提示", "请先选择输入视频文件或文件夹。")
            return

        output_dir = self._get_output_dir()

        self.file_list.clear()
        self.transcript_view.clear()
        self.summary_view.clear()
        self._completed_names.clear()
        self.log_text.clear()

        self._current_mode = "transcribe"
        self._reset_counters()

        total = len(self._video_files)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"0/{total}")

        self._set_busy_state(True)

        thread = QThread()
        worker = TranscribeWorker(self._video_files, output_dir, self.settings)

        worker.video_done.connect(self._on_single_video_transcribed)
        worker.video_error.connect(self._on_transcribe_error)
        worker.progress.connect(self._on_progress)
        worker.error.connect(self._on_worker_error)

        self._start_worker(thread, worker)

    def _on_single_video_transcribed(
        self, video_name: str, segments_count: int, output_paths: list
    ) -> None:
        """单个视频转写完成 —— 立即更新 GUI"""
        self._tx_success += 1
        self._current_video_name = video_name
        if video_name not in self._completed_names:
            self._completed_names.add(video_name)
            item = QListWidgetItem(video_name)
            item.setData(Qt.ItemDataRole.UserRole, video_name)
            self.file_list.addItem(item)

        self.file_list.setCurrentItem(self.file_list.item(self.file_list.count() - 1))
        self._load_transcript_content(video_name)

        self.status_bar.showMessage(
            f"转写完成: {video_name} ({segments_count} 段)", 5000
        )

    def _load_transcript_content(self, video_name: str) -> None:
        """加载指定视频的转写文本到编辑区"""
        output_dir = self.output_edit.text().strip() or _DEFAULT_OUTPUT_DIR
        transcript_path = None
        for ext in ("txt", "srt", "vtt", "json"):
            candidate = Path(output_dir) / f"{video_name}.{ext}"
            if candidate.exists():
                transcript_path = candidate
                break
        if transcript_path is None:
            return
        try:
            self.transcript_view.setPlainText(
                transcript_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, UnicodeDecodeError) as exc:
            get_logger("video2text").warning(
                "读取转写文件失败: %s (%s)", transcript_path, exc
            )

    def _on_transcribe_error(self, video_name: str, error_msg: str) -> None:
        """单个视频转写失败"""
        self._tx_fail += 1
        self._fail_records.append((video_name, "转写", error_msg))
        self.status_bar.showMessage(f"转写失败: {video_name} — {error_msg}", 5000)

    # ── 仅总结 ──

    def _on_summarize(self) -> None:
        output_dir = self._get_output_dir()

        standalone_text = self.transcript_view.toPlainText().strip()

        if not self._video_files and standalone_text:
            self._summarize_standalone(standalone_text, output_dir)
            return

        if not self._video_files:
            QMessageBox.warning(
                self,
                "提示",
                "请先选择视频文件或文件夹，并完成转写后再进行总结。\n"
                "或在「文本内容」标签页中粘贴文本后点击「仅总结」。",
            )
            return

        if self._current_video_name:
            if standalone_text:
                transcript_path = Path(output_dir) / f"{self._current_video_name}.txt"
                try:
                    transcript_path.write_text(standalone_text, encoding="utf-8")
                except OSError as exc:
                    get_logger("video2text").warning(
                        "保存编辑文本失败: %s (%s)", transcript_path, exc
                    )

        self._current_mode = "summarize"
        self._reset_counters()

        total = len(self._video_files)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"0/{total}")

        self._set_busy_state(True)

        custom_prompt = self.ollama_prompt_edit.toPlainText().strip()

        thread = QThread()
        worker = SummarizeWorker(
            self._video_files,
            output_dir,
            self.settings,
            custom_prompt,
            stream=True,
        )

        worker.stream_token.connect(self._on_stream_token)
        worker.summarize_started.connect(self._on_summarize_started)
        worker.video_done.connect(self._on_single_video_summarized)
        worker.video_error.connect(self._on_summarize_error)
        worker.progress.connect(self._on_progress)
        worker.error.connect(self._on_worker_error)

        self._start_worker(thread, worker)

    def _summarize_standalone(self, text: str, output_dir: str) -> None:
        """独立文本总结（不依赖视频文件列表）"""
        self._current_mode = "summarize"
        self._reset_counters()

        self.progress_bar.setMaximum(1)
        self.progress_bar.setValue(0)
        self.progress_label.setText("0/1")

        self._set_busy_state(True)
        self.summary_view.clear()

        custom_prompt = self.ollama_prompt_edit.toPlainText().strip()

        thread = QThread()
        worker = SummarizeWorker(
            [],
            output_dir,
            self.settings,
            custom_prompt,
            stream=True,
        )

        worker.set_standalone_text(text)
        worker.stream_token.connect(self._on_stream_token)
        worker.summarize_started.connect(self._on_summarize_started)
        worker.video_done.connect(self._on_single_video_summarized)
        worker.video_error.connect(self._on_summarize_error)
        worker.progress.connect(self._on_progress)
        worker.error.connect(self._on_worker_error)

        self._start_worker(thread, worker)

    def _on_stream_token(self, token: str) -> None:
        """流式 token —— 追加到摘要区"""
        self.summary_view.moveCursor(QTextCursor.End)
        self.summary_view.insertPlainText(token)

    def _on_summarize_started(self, video_name: str) -> None:
        """开始总结新视频时清空摘要区"""
        self.summary_view.clear()

    def _on_single_video_summarized(self, video_name: str, summary: str) -> None:
        """单个视频总结完成"""
        self._sum_success += 1
        self.summary_view.setPlainText(summary)
        self.status_bar.showMessage(f"总结完成: {video_name}", 5000)

    def _on_summarize_error(self, video_name: str, error_msg: str) -> None:
        """单个视频总结失败"""
        self._sum_fail += 1
        self._fail_records.append((video_name, "总结", error_msg))
        self.status_bar.showMessage(f"总结失败: {video_name} — {error_msg}", 5000)

    # ── 转写+总结管道 ──

    def _on_pipeline(self) -> None:
        if not self._video_files:
            QMessageBox.warning(self, "提示", "请先选择输入视频文件或文件夹。")
            return

        output_dir = self._get_output_dir()

        self.file_list.clear()
        self.transcript_view.clear()
        self.summary_view.clear()
        self._completed_names.clear()
        self.log_text.clear()

        self._current_mode = "pipeline"
        self._reset_counters()

        total = len(self._video_files)
        self.progress_bar.setMaximum(total * 2)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"0/{total * 2}")

        self._set_busy_state(True)

        custom_prompt = self.ollama_prompt_edit.toPlainText().strip()

        thread = QThread()
        worker = PipelineWorker(
            self._video_files,
            output_dir,
            self.settings,
            custom_prompt,
            stream=True,
        )

        worker.transcribe_done.connect(self._on_single_video_transcribed)
        worker.transcribe_error.connect(self._on_transcribe_error)
        worker.summarize_started.connect(self._on_summarize_started)
        worker.stream_token.connect(self._on_stream_token)
        worker.summarize_done.connect(self._on_single_video_summarized)
        worker.summarize_error.connect(self._on_summarize_error)
        worker.progress.connect(self._on_progress)
        worker.error.connect(self._on_worker_error)

        self._start_worker(thread, worker)

    # ── progress / completion ──

    def _on_progress(self, completed: int, total: int) -> None:
        self.progress_bar.setValue(completed)
        self.progress_label.setText(f"{completed}/{total}")

    def _on_thread_finished(self) -> None:
        self._set_busy_state(False)
        self._worker = None
        self._worker_thread = None

        self.progress_bar.setValue(self.progress_bar.maximum())

        if self._current_mode == "transcribe":
            msg = f"转写完成 — 成功: {self._tx_success}, 失败: {self._tx_fail}"
        elif self._current_mode == "summarize":
            msg = f"总结完成 — 成功: {self._sum_success}, 失败: {self._sum_fail}"
        elif self._current_mode == "pipeline":
            msg = (
                f"转写: 成功 {self._tx_success} / 失败 {self._tx_fail} | "
                f"总结: 成功 {self._sum_success} / 失败 {self._sum_fail}"
            )
        else:
            msg = "处理完成"

        self.status_bar.showMessage(msg)
        self._save_fail_records()

    def _save_fail_records(self) -> None:
        if not self._fail_records:
            return
        logs_dir = self.settings.get("paths.logs_dir", "logs")
        log_path = Path(logs_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        fail_path = log_path / "fail_log.log"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mode_map = {
            "transcribe": "仅转写",
            "summarize": "仅总结",
            "pipeline": "转写+总结",
        }
        mode_label = mode_map.get(self._current_mode, self._current_mode)
        try:
            with open(fail_path, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] 模式: {mode_label}\n")
                for video_name, stage, error_msg in self._fail_records:
                    f.write(f"  {stage}失败 | {video_name} | {error_msg}\n")
                f.write("\n")
        except OSError as exc:
            get_logger("video2text").warning("写入失败日志失败: %s", exc)

    # ── result file viewer ──

    def _open_result_viewer(self) -> None:
        if not self._completed_names:
            QMessageBox.warning(self, "提示", "请先完成转写或加载历史文件")
            return

        output_dir = self.output_edit.text().strip() or _DEFAULT_OUTPUT_DIR
        video_names = list(self._completed_names)

        if self._result_viewer is None or not self._result_viewer.isVisible():
            self._result_viewer = ResultViewerWindow(self)

        self._result_viewer.load_files(video_names, output_dir)
        self._result_viewer.show()
        self._result_viewer.raise_()
        self._result_viewer.activateWindow()

    def _show_file_context_menu(self, pos) -> None:
        item = self.file_list.itemAt(pos)
        if item is None:
            return
        video_name = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        retranscribe_action = menu.addAction("重新转写")
        resummarize_action = menu.addAction("重新总结")
        action = menu.exec(self.file_list.viewport().mapToGlobal(pos))
        if action == retranscribe_action:
            self._on_retranscribe(video_name)
        elif action == resummarize_action:
            self._on_resummarize(video_name)

    def _find_video_path_by_name(self, video_name: str) -> Optional[str]:
        for path in self._video_files:
            if Path(path).stem == video_name:
                return path
        return None

    def _on_retranscribe(self, video_name: str) -> None:
        if self._worker_thread is not None and self._worker_thread.isRunning():
            QMessageBox.warning(self, "提示", "当前有任务正在运行，请等待完成后再试。")
            return

        video_path = self._find_video_path_by_name(video_name)
        if video_path is None:
            QMessageBox.warning(
                self,
                "提示",
                f"未找到原始视频文件: {video_name}\n请先在主界面加载包含该视频的文件或文件夹。",
            )
            return

        output_dir = self._get_output_dir()
        self._current_mode = "transcribe"
        self._reset_counters()
        self.progress_bar.setMaximum(1)
        self.progress_bar.setValue(0)
        self.progress_label.setText("0/1")
        self._set_busy_state(True)

        thread = QThread()
        worker = TranscribeWorker([video_path], output_dir, self.settings)
        worker.video_done.connect(self._on_single_video_transcribed)
        worker.video_error.connect(self._on_transcribe_error)
        worker.progress.connect(self._on_progress)
        worker.error.connect(self._on_worker_error)
        self._start_worker(thread, worker)

    def _on_resummarize(self, video_name: str) -> None:
        if self._worker_thread is not None and self._worker_thread.isRunning():
            QMessageBox.warning(self, "提示", "当前有任务正在运行，请等待完成后再试。")
            return

        output_dir = self._get_output_dir()
        transcript_path = None
        for ext in ("txt", "srt", "vtt", "json"):
            candidate = Path(output_dir) / f"{video_name}.{ext}"
            if candidate.exists():
                transcript_path = candidate
                break
        if transcript_path is None:
            QMessageBox.warning(self, "提示", f"未找到转写文件: {video_name}")
            return

        self._current_mode = "summarize"
        self._reset_counters()
        self.progress_bar.setMaximum(1)
        self.progress_bar.setValue(0)
        self.progress_label.setText("0/1")
        self._set_busy_state(True)
        self.summary_view.clear()

        custom_prompt = self.ollama_prompt_edit.toPlainText().strip()

        thread = QThread()
        worker = SummarizeWorker(
            [video_name],
            output_dir,
            self.settings,
            custom_prompt,
            stream=True,
        )
        worker.stream_token.connect(self._on_stream_token)
        worker.summarize_started.connect(self._on_summarize_started)
        worker.video_done.connect(self._on_single_video_summarized)
        worker.video_error.connect(self._on_summarize_error)
        worker.progress.connect(self._on_progress)
        worker.error.connect(self._on_worker_error)
        self._start_worker(thread, worker)

    def _on_file_selected(
        self, current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem]
    ) -> None:
        if current is None:
            return
        video_name = current.data(Qt.ItemDataRole.UserRole)
        self._current_video_name = video_name
        output_dir = self.output_edit.text().strip() or _DEFAULT_OUTPUT_DIR

        QTimer.singleShot(0, lambda: self._load_file_content(video_name, output_dir))

    def _load_file_content(self, video_name: str, output_dir: str) -> None:
        """在事件循环空闲时加载文件内容，避免阻塞 GUI 线程。"""
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
            except (OSError, UnicodeDecodeError) as exc:
                self.transcript_view.setPlainText(f"读取失败: {exc}")
        else:
            self.transcript_view.setPlainText("(未找到转写文件)")

        summary_path = Path(output_dir) / f"{video_name}_summary.txt"
        if summary_path.exists():
            try:
                self.summary_view.setPlainText(summary_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError) as exc:
                self.summary_view.setPlainText(f"读取失败: {exc}")
        else:
            self.summary_view.setPlainText("(未找到摘要文件)")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._worker_thread is not None and self._worker_thread.isRunning():
            if self._worker is not None and hasattr(self._worker, "cancel"):
                self._worker.cancel()
            if self._worker is not None and hasattr(self._worker, "_pause_event"):
                self._worker._pause_event.set()
            if self._worker is not None and hasattr(self._worker, "_service"):
                service = self._worker._service
                if service is not None and hasattr(service, "_pause_event"):
                    service._pause_event.set()
            self._worker_thread.quit()
            if not self._worker_thread.wait(3000):
                self._worker_thread.terminate()
                self._worker_thread.wait(1000)

        for attr in ("_ollama_check_thread", "_ollama_list_thread"):
            thread = getattr(self, attr, None)
            if thread is not None:
                try:
                    if thread.isRunning():
                        thread.quit()
                        thread.wait(2000)
                except RuntimeError:
                    pass
        for attr in ("_ollama_check_worker", "_ollama_list_worker"):
            worker = getattr(self, attr, None)
            if worker is not None:
                try:
                    worker.deleteLater()
                except Exception:
                    pass

        for name in ("video2text", "src"):
            lg = logging.getLogger(name)
            if self._ui_handler in lg.handlers:
                lg.removeHandler(self._ui_handler)

        OllamaClient.stop_service()

        for cached_transcriber in list(_transcriber_cache.values()):
            try:
                cached_transcriber.unload_model()
            except Exception:
                pass
        _transcriber_cache.clear()

        if self._result_viewer is not None:
            self._result_viewer.close()
        event.accept()


def main() -> None:
    app = QApplication()
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
