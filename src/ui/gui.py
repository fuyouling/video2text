"""Video2Text GUI —— 基于 PySide6 的视频转文本图形界面"""

import logging
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import QFont, QIcon, QTextCursor
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

from src.config.settings import PromptManager, Settings
from src.ui.gui_dialogs import VideoSelectionDialog
from src.ui.gui_workers import (
    PipelineWorker,
    SummarizeWorker,
    TranscribeWorker,
    UiLogHandler,
    UiLogSignal,
)
from src.ui.result_viewer import ResultViewerWindow
from src.utils.logger import get_logger, setup_logger

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

        self._setup_logging()
        self._init_ui()
        self._load_ollama_config()
        self._load_prompt_templates()

    def _setup_logging(self) -> None:
        self._log_signal = UiLogSignal()
        self._log_signal.message.connect(self._append_log)
        self._ui_handler = UiLogHandler(self._log_signal)

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        if self._ui_handler not in root_logger.handlers:
            root_logger.addHandler(self._ui_handler)

    def _append_log(self, msg: str) -> None:
        self.log_text.append(msg)
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
        self.input_multi_btn = QPushButton("选择多个文件")
        self.input_multi_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.input_multi_btn.clicked.connect(self._select_input_multiple_files)
        input_row.addWidget(self.input_multi_btn)
        self.input_folder_btn = QPushButton("选择文件夹")
        self.input_folder_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.input_folder_btn.clicked.connect(self._select_input_folder)
        input_row.addWidget(self.input_folder_btn)
        root.addLayout(input_row)

        # ── output row ──
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("输出:"))
        self.output_edit = QLineEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setText(_DEFAULT_OUTPUT_DIR)
        output_row.addWidget(self.output_edit, 1)
        self.load_history_btn = QPushButton("加载历史")
        self.load_history_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.load_history_btn.setToolTip("加载输出目录中的历史转写和总结文件")
        self.load_history_btn.clicked.connect(self._load_history_files)
        output_row.addWidget(self.load_history_btn)
        self.output_btn = QPushButton("浏览")
        self.output_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.output_btn.clicked.connect(self._select_output_dir)
        output_row.addWidget(self.output_btn)
        root.addLayout(output_row)

        # ── run / progress row ──
        run_row = QHBoxLayout()
        self.progress_label = QLabel("就绪:")
        run_row.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        run_row.addWidget(self.progress_bar, 1)
        self.open_viewer_btn = QPushButton("全屏查看")
        self.open_viewer_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.open_viewer_btn.setToolTip(
            "在独立窗口中查看所有结果，支持全屏、搜索、导出、书签等功能"
        )
        self.open_viewer_btn.clicked.connect(self._open_result_viewer)
        run_row.addWidget(self.open_viewer_btn)
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
        self.pause_btn = QPushButton("暂停")
        self.pause_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.pause_btn.setToolTip("暂停当前转写任务，再次点击可继续")
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._on_pause_resume)
        run_row.addWidget(self.pause_btn)
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
        self.ollama_url_edit.setPlaceholderText("http://127.0.0.1:11434")
        ollama_layout.addRow("服务地址:", self.ollama_url_edit)

        # 模型选择（下拉框 + 手动输入）
        model_row = QHBoxLayout()
        self.ollama_model_combo = QComboBox()
        self.ollama_model_combo.setEditable(True)
        self.ollama_model_combo.setMinimumWidth(250)
        self.ollama_model_combo.setPlaceholderText("qwen2.5:7b-instruct-q4_K_M")
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
        self.ollama_maxlen_spin.setRange(50, 5000)
        self.ollama_maxlen_spin.setSingleStep(50)
        ollama_layout.addRow("最大长度:", self.ollama_maxlen_spin)
        self.ollama_prompt_edit = QTextEdit()
        self.ollama_prompt_edit.setMaximumHeight(100)
        self.ollama_prompt_edit.setPlaceholderText(
            "自定义总结提示词（可选）：\n"
            "输入您希望模型如何总结的指令，例如：\n"
            "「请用英文总结以下文本，列出3个要点」\n"
            "留空则使用默认提示词。"
        )
        ollama_layout.addRow("提示词:", self.ollama_prompt_edit)

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
        ollama_layout.addRow("提示词模板:", prompt_btn_row)
        ollama_btn_row = QHBoxLayout()
        self.ollama_start_btn = QPushButton("启动服务")
        self.ollama_start_btn.clicked.connect(self._start_ollama_service)
        ollama_btn_row.addWidget(self.ollama_start_btn)
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

        right_splitter.setStretchFactor(0, 2)
        right_splitter.setStretchFactor(1, 1)
        splitter.addWidget(right_splitter)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage(f"配置: {self.settings.config_path}")

    # ── Ollama 配置 ──

    def _load_ollama_config(self) -> None:
        url = self.settings.get("summarization.ollama_url", "http://127.0.0.1:11434")
        model = self.settings.get(
            "summarization.model_name", "qwen2.5:7b-instruct-q4_K_M"
        )
        temp = self.settings.get_float("summarization.temperature", 0.7)
        max_len = self.settings.get_int("summarization.max_length", 500)
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
            self.ollama_status_label.setText("配置已保存")
            self.ollama_status_label.setStyleSheet("color: green")
        except Exception as e:
            self.ollama_status_label.setText(f"保存失败: {e}")
            self.ollama_status_label.setStyleSheet("color: red")

    # ── 提示词模板管理 ──

    def _load_prompt_templates(self) -> None:
        self.prompt_template_combo.blockSignals(True)
        self.prompt_template_combo.clear()
        self.prompt_template_combo.addItem("")
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
        if not name:
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
        name, ok = QInputDialog.getText(
            self, "保存提示词模板", "模板名称:", text=current_name
        )
        if not ok or not name.strip():
            return

        name = name.strip()
        self.prompt_manager.set_template(name, content)
        self.prompt_manager.set_last_used(name)

        self.prompt_template_combo.blockSignals(True)
        if self.prompt_template_combo.findText(name) < 0:
            self.prompt_template_combo.addItem(name)
        self.prompt_template_combo.setCurrentText(name)
        self.prompt_template_combo.blockSignals(False)

        self.ollama_status_label.setText(f"提示词「{name}」已保存")
        self.ollama_status_label.setStyleSheet("color: green")

    def _delete_prompt_template(self) -> None:
        name = self.prompt_template_combo.currentText()
        if not name:
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
        self.ollama_status_label.setText(f"提示词「{name}」已删除")
        self.ollama_status_label.setStyleSheet("color: green")

    def _test_ollama(self) -> None:
        from src.summarization.ollama_client import OllamaClient

        url = self.ollama_url_edit.text() or "http://127.0.0.1:11434"
        client = OllamaClient(base_url=url)
        if client.check_connection():
            self.ollama_status_label.setText("连接成功")
            self.ollama_status_label.setStyleSheet("color: green")
        else:
            self.ollama_status_label.setText("连接失败")
            self.ollama_status_label.setStyleSheet("color: red")

    def _refresh_model_list(self) -> None:
        """从 Ollama 获取可用模型列表并填充下拉框"""
        from src.summarization.ollama_client import OllamaClient

        url = self.ollama_url_edit.text() or "http://127.0.0.1:11434"
        client = OllamaClient(base_url=url)
        models = client.list_models()

        current_text = self.ollama_model_combo.currentText()
        self.ollama_model_combo.clear()
        if models:
            self.ollama_model_combo.addItems(models)
            # 恢复之前的选择
            idx = self.ollama_model_combo.findText(current_text)
            if idx >= 0:
                self.ollama_model_combo.setCurrentIndex(idx)
            self.ollama_status_label.setText(f"找到 {len(models)} 个模型")
            self.ollama_status_label.setStyleSheet("color: green")
        else:
            self.ollama_status_label.setText("未找到模型或连接失败")
            self.ollama_status_label.setStyleSheet("color: red")

    def _start_ollama_service(self) -> None:
        import shutil
        import subprocess

        logger = get_logger("video2text")

        ollama_path = shutil.which("ollama")
        if not ollama_path:
            self.ollama_status_label.setText("未找到ollama命令")
            self.ollama_status_label.setStyleSheet("color: red")
            QMessageBox.warning(
                self,
                "提示",
                "未找到ollama命令，请确保已安装Ollama。\n"
                "可以从 https://ollama.com/download 下载安装。",
            )
            return

        try:
            logger.info("正在启动Ollama服务...")
            self.ollama_status_label.setText("正在启动...")
            self.ollama_status_label.setStyleSheet("color: orange")

            subprocess.Popen(
                [ollama_path, "serve"],
                creationflags=subprocess.CREATE_NO_WINDOW
                if sys.platform == "win32"
                else 0,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            logger.info("Ollama服务启动命令已执行")
            QTimer.singleShot(2000, self._check_ollama_after_start)

        except Exception as e:
            logger.error(f"启动Ollama服务失败: {e}")
            self.ollama_status_label.setText(f"启动失败: {e}")
            self.ollama_status_label.setStyleSheet("color: red")

    def _check_ollama_after_start(self) -> None:
        from src.summarization.ollama_client import OllamaClient

        url = self.ollama_url_edit.text() or "http://127.0.0.1:11434"
        client = OllamaClient(base_url=url)

        if client.check_connection():
            self.ollama_status_label.setText("服务已启动")
            self.ollama_status_label.setStyleSheet("color: green")
            self._refresh_model_list()
        else:
            self.ollama_status_label.setText("服务启动中，请稍后测试")
            self.ollama_status_label.setStyleSheet("color: orange")

    def _on_tab_changed(self, index: int) -> None:
        if index == 0:
            self.status_bar.showMessage(
                "文本内容 —— 可直接编辑，编辑后点击「仅总结」将文本发送给 Ollama 进行摘要"
            )
        elif index == 1:
            self.status_bar.showMessage("摘要结果 —— 由 Ollama 生成（只读）")

    # ── input selection slots ──

    def _select_input_files(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择视频文件",
            "",
            "视频文件 (*.mp4 *.avi *.mov *.mkv *.flv *.wmv *.webm *.ts *.mts *.m4v *.3gp *.mpeg *.mpg *.vob *.ogv *.rm *.rmvb);;所有文件 (*.*)",
        )
        if path:
            self.input_edit.setText(path)
            self._video_files = [path]

    def _select_input_multiple_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择视频文件",
            "",
            "视频文件 (*.mp4 *.avi *.mov *.mkv *.flv *.wmv *.webm *.ts *.mts *.m4v *.3gp *.mpeg *.mpg *.vob *.ogv *.rm *.rmvb);;所有文件 (*.*)",
        )
        if paths:
            self.input_edit.setText(f"已选择 {len(paths)} 个文件")
            self._video_files = list(paths)

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
                else:
                    QMessageBox.information(self, "提示", "未选择任何视频文件")

    @staticmethod
    def _scan_video_files(folder: str) -> list[str]:
        folder_path = Path(folder)
        files: list[str] = []
        for ext in SUPPORTED_VIDEO_EXTENSIONS:
            for f in folder_path.rglob(f"*{ext}"):
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

        transcript_files = sorted(output_path.glob("*.txt"))

        loaded_count = 0
        for txt_file in transcript_files:
            if txt_file.name.endswith("_summary.txt"):
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
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        thread.start()

    def _set_busy_state(self, busy: bool) -> None:
        self.transcribe_btn.setEnabled(not busy)
        self.summarize_btn.setEnabled(not busy)
        self.combine_btn.setEnabled(not busy)
        self.input_file_btn.setEnabled(not busy)
        self.input_multi_btn.setEnabled(not busy)
        self.input_folder_btn.setEnabled(not busy)
        self.output_btn.setEnabled(not busy)
        self.pause_btn.setEnabled(busy)
        if not busy:
            self.pause_btn.setText("暂停")

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

        total = len(self._video_files)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"0/{total}")

        self._set_busy_state(True)

        thread = QThread()
        worker = TranscribeWorker(self._video_files, output_dir, self.settings)

        # 每完成一个视频立即显示结果
        worker.video_done.connect(self._on_single_video_transcribed)
        worker.progress.connect(self._on_progress)

        self._start_worker(thread, worker)

    def _on_single_video_transcribed(
        self, video_name: str, segments_count: int, output_paths: list
    ) -> None:
        """单个视频转写完成 —— 立即更新 GUI"""
        if video_name not in self._completed_names:
            self._completed_names.add(video_name)
            item = QListWidgetItem(video_name)
            item.setData(Qt.ItemDataRole.UserRole, video_name)
            self.file_list.addItem(item)

        # 自动选中最新完成的视频并显示内容
        self.file_list.setCurrentItem(self.file_list.item(self.file_list.count() - 1))
        self._load_transcript_content(video_name)

        self.status_bar.showMessage(f"转写完成: {video_name} ({segments_count} 段)")

    def _load_transcript_content(self, video_name: str) -> None:
        """加载指定视频的转写文本到编辑区"""
        output_dir = self.output_edit.text().strip() or _DEFAULT_OUTPUT_DIR
        transcript_path = Path(output_dir) / f"{video_name}.txt"
        if transcript_path.exists():
            try:
                self.transcript_view.setPlainText(
                    transcript_path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeDecodeError):
                pass

    # ── 仅总结 ──

    def _on_summarize(self) -> None:
        if not self._video_files:
            QMessageBox.warning(
                self,
                "提示",
                "请先选择视频文件或文件夹，并完成转写后再进行总结。",
            )
            return

        output_dir = self._get_output_dir()

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

        # 流式 token → 实时追加到摘要区
        worker.stream_token.connect(self._on_stream_token)
        # 单个视频总结完成
        worker.video_done.connect(self._on_single_video_summarized)
        worker.progress.connect(self._on_progress)

        self._start_worker(thread, worker)

    def _on_stream_token(self, token: str) -> None:
        """流式 token —— 追加到摘要区"""
        self.summary_view.moveCursor(QTextCursor.End)
        self.summary_view.insertPlainText(token)

    def _on_single_video_summarized(self, video_name: str, summary: str) -> None:
        """单个视频总结完成"""
        self.summary_view.setPlainText(summary)
        self.ollama_status_label.setText(f"总结完成: {video_name}")
        self.ollama_status_label.setStyleSheet("color: green")

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

        total = len(self._video_files)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"0/{total}")

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

        # 转写完成 → 立即显示转写结果
        worker.transcribe_done.connect(self._on_single_video_transcribed)
        # 流式 token
        worker.stream_token.connect(self._on_stream_token)
        # 总结完成
        worker.summarize_done.connect(self._on_single_video_summarized)
        worker.progress.connect(self._on_progress)

        self._start_worker(thread, worker)

    # ── progress / completion ──

    def _on_progress(self, completed: int, total: int) -> None:
        self.progress_bar.setValue(completed)
        self.progress_label.setText(f"{completed}/{total}")

    def _on_thread_finished(self) -> None:
        self._worker_thread = None
        self._worker = None
        self._set_busy_state(False)

        completed = len(self._completed_names)
        self.progress_label.setText(f"{completed} 个视频处理完成")
        self.status_bar.showMessage("处理完成")

    # ── result file viewer ──

    def _open_result_viewer(self):
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

    def _on_file_selected(
        self, current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem]
    ) -> None:
        if current is None:
            return
        video_name = current.data(Qt.ItemDataRole.UserRole)
        output_dir = self.output_edit.text().strip() or _DEFAULT_OUTPUT_DIR

        transcript_path = Path(output_dir) / f"{video_name}.txt"
        if transcript_path.exists():
            try:
                self.transcript_view.setPlainText(
                    transcript_path.read_text(encoding="utf-8")
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

    def closeEvent(self, event) -> None:
        if self._worker_thread is not None and self._worker_thread.isRunning():
            if self._worker is not None and hasattr(self._worker, "cancel"):
                self._worker.cancel()
            self._worker_thread.quit()
            self._worker_thread.wait(3000)
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
