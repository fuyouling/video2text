"""Video2Text GUI —— 基于 PySide6 的媒体转文本图形界面"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QFont,
    QIcon,
    QKeySequence,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.config.directory_manager import DirectoryManager
from src.config.settings import Settings
from src.config.version import APP_VERSION
from src.services.transcription_service import TranscriptionService
from src.summarization.prompt_manager import PromptManager
from src.storage.file_writer import FileWriter
from src.summarization.ollama_client import OllamaClient
from src.ui.gui_dialogs import ConfigEditorDialog, VideoSelectionDialog
from src.ui.gui_workers import (
    PipelineWorker,
    ScanFilesWorker,
    SummarizeWorker,
    TranscribeWorker,
)
from src.ui.favorite_dir_helper import FavoriteDirHelper
from src.ui.log_panel import LogPanel
from src.ui.result_viewer import ResultViewerWindow, _find_summary_path
from src.ui.search_controller import SearchController
from src.transcription.transcriber import _model_cache as _transcriber_cache
from src.utils.logger import get_logger, setup_logger

logger = get_logger(__name__)

if getattr(sys, "frozen", False):
    _PROJECT_ROOT = Path(sys.executable).parent
else:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_OUTPUT_DIR = str(_PROJECT_ROOT / "output")

_BTN_MIN_WIDTH = 100


class MainWindow(QMainWindow):
    """Video2Text 主窗口 —— 媒体转文本工具的图形界面主入口。

    功能包括：媒体文件选择、转写、总结、结果查看、暂停/继续、历史加载等。
    """

    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings()
        self.prompt_manager = PromptManager()
        self._video_exts = set(
            ext.lower()
            for ext in self.settings.get_list("preprocessing.supported_video_formats")
        )
        self._audio_exts = set(
            ext.lower()
            for ext in self.settings.get_list("preprocessing.supported_audio_formats")
        )
        self._media_exts = self._video_exts | self._audio_exts
        self._video_files: list[str] = []
        self._completed_names: set[str] = set()
        self._worker_thread: Optional[QThread] = None
        self._worker = None
        self._result_viewer: Optional[ResultViewerWindow] = None
        self._current_mode = ""
        self._tx_success = 0
        self._tx_fail = 0
        self._sum_success = 0
        self._sum_fail = 0
        self._fail_records: list[tuple[str, str, str]] = []
        self._current_video_name: Optional[str] = None
        self._is_multi_thread = False
        self._history_loaded = False
        self._scan_context: Optional[dict] = None
        self._scan_thread: Optional[QThread] = None
        self._scan_worker = None
        self._input_folder: Optional[str] = None
        self._mirror_subdirs: bool = False
        self._mirror_depth: int = 1
        self._name_to_output_dir: dict[str, str] = {}
        self._current_phase = "transcribe"  # 管道当前阶段

        self._dir_manager = DirectoryManager(_PROJECT_ROOT / "favorite_dirs.json")

        self._init_ui()

        self._fav_helper = FavoriteDirHelper(
            dir_manager=self._dir_manager,
            input_combo=self.input_combo,
            output_combo=self.output_combo,
            default_output_dir=_DEFAULT_OUTPUT_DIR,
            status_callback=lambda msg, t: self.status_bar.showMessage(msg, t),
            parent=self,
        )
        self._fav_helper.load()
        self._load_prompt_config()
        self._load_prompt_templates()

    def _init_ui(self) -> None:
        """初始化主窗口 UI 布局：菜单栏、输入输出行、进度条、日志面板、结果面板。"""
        self.setWindowTitle("Video2Text - 媒体转文本工具")
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

        root.addLayout(self._create_input_row())
        root.addLayout(self._create_output_row())
        root.addLayout(self._create_run_row())

        # ── splitter: logs + right panel ──
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.log_panel = LogPanel()
        splitter.addWidget(self.log_panel)

        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.addWidget(self._create_results_panel())
        right_splitter.addWidget(self._create_prompt_panel())
        right_splitter.setStretchFactor(0, 3)
        right_splitter.setStretchFactor(1, 1)
        splitter.addWidget(right_splitter)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage(f"配置: {self.settings.config_path}")

    def _create_input_row(self) -> QHBoxLayout:
        input_row = QHBoxLayout()
        input_row.addWidget(QLabel("输入:"))
        self.input_combo = QComboBox()
        self.input_combo.setEditable(True)
        self.input_combo.setPlaceholderText("请选择视频/音频文件或文件夹…")
        self.input_combo.setMinimumWidth(300)
        self.input_combo.activated.connect(self._on_input_combo_activated)
        input_row.addWidget(self.input_combo, 1)

        self.input_file_btn = QPushButton("选择文件")
        self.input_file_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.input_file_btn.clicked.connect(self._select_input_files)
        input_row.addWidget(self.input_file_btn)
        self.input_folder_btn = QPushButton("选择文件夹")
        self.input_folder_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.input_folder_btn.clicked.connect(self._select_input_folder)
        input_row.addWidget(self.input_folder_btn)
        self.pause_btn = QPushButton("暂停")
        self.pause_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.pause_btn.setToolTip("暂停当前转写任务，再次点击可继续")
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._on_pause_resume)
        input_row.addWidget(self.pause_btn)
        return input_row

    def _create_output_row(self) -> QHBoxLayout:
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("输出:"))
        self.output_combo = QComboBox()
        self.output_combo.setEditable(True)
        self.output_combo.setCurrentText(_DEFAULT_OUTPUT_DIR)
        self.output_combo.setMinimumWidth(300)
        output_row.addWidget(self.output_combo, 1)
        self.output_btn = QPushButton("浏览")
        self.output_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.output_btn.clicked.connect(self._select_output_dir)
        output_row.addWidget(self.output_btn)
        self.load_history_btn = QPushButton("加载历史")
        self.load_history_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.load_history_btn.setToolTip("加载输出目录中的历史转写和总结文件")
        self.load_history_btn.clicked.connect(self._load_history_files)
        output_row.addWidget(self.load_history_btn)
        self.open_viewer_btn = QPushButton("全屏查看")
        self.open_viewer_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.open_viewer_btn.setToolTip(
            "在独立窗口中查看所有结果，支持全屏、搜索、导出、书签等功能"
        )
        self.open_viewer_btn.clicked.connect(self._open_result_viewer)
        output_row.addWidget(self.open_viewer_btn)
        return output_row

    def _create_run_row(self) -> QHBoxLayout:
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
        self.combine_btn = QPushButton("转写总结")
        self.combine_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.combine_btn.setToolTip("先执行语音转写，完成后自动对转写文本进行摘要总结")
        self.combine_btn.clicked.connect(self._on_pipeline)
        run_row.addWidget(self.combine_btn)
        return run_row

    def _create_results_panel(self) -> QWidget:
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
            "可直接编辑修改，Ctrl+S 保存，Ctrl+F 查找替换,保存后右键点击文件列表中文件可重新转写和摘要"
        )
        self.result_tabs.addTab(self.transcript_view, "文本内容")
        self.summary_view = QTextEdit()
        self.summary_view.setFont(QFont("Consolas", 9))
        self.summary_view.setPlaceholderText(
            "摘要结果，可直接编辑修改，Ctrl+S 保存，Ctrl+F 查找替换。"
        )
        self.result_tabs.addTab(self.summary_view, "摘要")
        self.result_tabs.currentChanged.connect(self._on_tab_changed)
        content_layout.addWidget(self.result_tabs, 3)

        save_transcript_action = QAction("保存文本", self)
        save_transcript_action.setShortcut(QKeySequence("Ctrl+S"))
        save_transcript_action.triggered.connect(self._save_transcript)
        self.addAction(save_transcript_action)

        find_action = QAction("查找替换", self)
        find_action.setShortcut(QKeySequence("Ctrl+F"))
        find_action.triggered.connect(self._toggle_search)
        self.addAction(find_action)

        results_layout.addLayout(content_layout)
        self._search_controller = SearchController(
            get_active_edit=self._active_edit,
            clear_all_highlights=self._clear_all_highlights,
            on_replace_count=self._on_replace_count,
        )
        results_layout.addWidget(self._search_controller)
        return results_group

    def _create_prompt_panel(self) -> QWidget:
        prompt_group = QGroupBox("提示词配置")
        prompt_layout = QVBoxLayout(prompt_group)

        self.ollama_prompt_edit = QTextEdit()
        self.ollama_prompt_edit.setMaximumHeight(100)
        self.ollama_prompt_edit.setPlaceholderText(
            "自定义总结提示词（可选），留空则使用默认提示词"
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
        self.markdown_enabled_cb = QCheckBox("Markdown格式")
        self.markdown_enabled_cb.setToolTip(
            "勾选后总结输出将自动应用Markdown格式指令。\n"
            "如需自定义Markdown格式，请直接编辑 prompts.json 中的 markdown_prompt 字段。"
        )
        self.markdown_enabled_cb.setChecked(self.prompt_manager.get_markdown_enabled())
        self.markdown_enabled_cb.toggled.connect(self._on_markdown_toggled)
        prompt_btn_row.addWidget(self.markdown_enabled_cb)
        prompt_layout.addLayout(prompt_btn_row)
        return prompt_group

    def _create_menu_bar(self) -> None:
        menu_bar = self.menuBar()

        settings_menu = menu_bar.addMenu("设置")
        edit_config_action = settings_menu.addAction("编辑配置")
        edit_config_action.triggered.connect(self._show_config_editor)

        fav_menu = settings_menu.addMenu("收藏")
        fav_input_action = fav_menu.addAction("收藏输入文件夹")
        fav_input_action.triggered.connect(self._fav_input_dir)
        fav_output_action = fav_menu.addAction("收藏输出文件夹")
        fav_output_action.triggered.connect(self._fav_output_dir)
        fav_both_action = fav_menu.addAction("收藏输入和输出文件夹")
        fav_both_action.triggered.connect(self._fav_both_dirs)
        fav_menu.addSeparator()
        clear_input_action = fav_menu.addAction("移除所有输入目录")
        clear_input_action.triggered.connect(self._clear_all_input_dirs)
        clear_output_action = fav_menu.addAction("移除所有输出目录")
        clear_output_action.triggered.connect(self._clear_all_output_dirs)

        help_menu = menu_bar.addMenu("帮助")
        donate_action = help_menu.addAction("捐赠支持")
        donate_action.triggered.connect(self._show_donate)
        about_action = help_menu.addAction("关于")
        about_action.triggered.connect(self._show_about)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "关于 Video2Text",
            "<div style='min-width:420px'>"
            "<h2 style='margin-bottom:4px'>🎬 Video2Text</h2>"
            f"<p style='color:#666;margin-top:0'>版本 {APP_VERSION} · 媒体转文本工具</p>"
            "<hr>"
            "<p>基于 <b>faster-whisper</b> 的高精度语音转和智能总结工具。</p>"
            "<p><b>核心功能：</b></p>"
            "<ul style='margin-top:2px'>"
            "<li>支持视频和音频格式转写,长音频自动分段</li>"
            "<li>转写和总结的模型都可切换</li>"
            "<li>图形化配置编辑和收藏目录管理</li>"
            "<li>总结结果markdown格式查看和书签功能</li>"
            "</ul>"
            "<hr>"
            "<table style='font-size:13px'>"
            "<tr><td style='padding:2px 12px 2px 0;color:#888'>作者</td><td>喵王龙</td></tr>"
            "<tr><td style='padding:2px 12px 2px 0;color:#888'>许可证</td><td>GNU GPL v3</td></tr>"
            "<tr><td style='padding:2px 12px 2px 0;color:#888'>技术栈</td><td>faster-whisper · PySide6</td></tr>"
            "<tr><td style='padding:2px 12px 2px 0;color:#888'>讨论群</td><td>QQ群 296875960</td></tr>"
            "</table>"
            "<hr>"
            "<p>"
            '<a href="https://github.com/fuyouling/video2text">GitHub 仓库</a> · '
            '<a href="https://github.com/fuyouling/video2text/wiki">使用文档</a>'
            "</p>"
            "<p style='color:#999;font-size:12px'>版权所有 © 2026 喵王龙</p>"
            "</div>",
        )

    def _show_donate(self) -> None:
        from src.ui.donate_dialog import DonateDialog

        dialog = DonateDialog(self)
        dialog.exec()

    def _show_config_editor(self) -> None:
        dialog = ConfigEditorDialog(self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self._load_prompt_config()
            self.status_bar.showMessage("配置已保存", 5000)

    def _load_prompt_config(self) -> None:
        prompt = self.settings.get("summarization.custom_prompt", "")
        self.ollama_prompt_edit.setPlainText(prompt)

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
                content = self.prompt_manager.get_content(last_used)
                if content:
                    self.ollama_prompt_edit.setPlainText(content)
        self.prompt_template_combo.blockSignals(False)

    def _on_prompt_template_selected(self, name: str) -> None:
        if not name or name == self._PLACEHOLDER_PROMPT:
            self.ollama_prompt_edit.clear()
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

    def _on_markdown_toggled(self, checked: bool) -> None:
        self.prompt_manager.set_markdown_enabled(checked)

    def _on_tab_changed(self, index: int) -> None:
        if index == 0:
            self.status_bar.showMessage(
                "文本内容 —— 可直接编辑修改，Ctrl+S 保存，Ctrl+F 查找替换"
            )
        elif index == 1:
            self.status_bar.showMessage(
                "摘要结果 —— 可直接编辑修改，Ctrl+S 保存，Ctrl+F 查找替换"
            )
        self._search_controller.refresh_if_active()

    def _save_transcript(self) -> None:
        """保存当前活动标签页的内容到文件（根据配置的输出格式自动匹配）"""
        if not self._current_video_name:
            self.status_bar.showMessage("没有选中的文件，无法保存", 3000)
            return
        output_dir = self.output_combo.currentText().strip() or _DEFAULT_OUTPUT_DIR
        current_tab = self.result_tabs.currentIndex()
        if current_tab == 0:
            text = self.transcript_view.toPlainText()
            save_path = self._resolve_transcript_path(output_dir)
        else:
            text = self.summary_view.toPlainText()
            save_path = self._resolve_summary_path(output_dir)
        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(text, encoding="utf-8")
            self.status_bar.showMessage(f"已保存: {save_path}", 5000)
        except OSError as exc:
            self.status_bar.showMessage(f"保存失败: {exc}", 5000)

    def _resolve_transcript_path(self, output_dir: str) -> Path:
        """根据配置的转写格式，找到已存在的转写文件路径，或按首选格式生成新路径"""
        name = self._current_video_name
        formats = self.settings.get_list("output.transcript_format", ["txt"])
        formats = [f.lower().strip() for f in formats if f.lower().strip()]
        if not formats:
            formats = ["txt"]
        for fmt in formats:
            candidate = Path(output_dir) / f"{name}.{fmt}"
            if candidate.exists():
                return candidate
        return Path(output_dir) / f"{name}.{formats[0]}"

    def _resolve_summary_path(self, output_dir: str) -> Path:
        """根据配置的摘要格式，找到已存在的摘要文件路径，或按配置格式生成新路径"""
        name = self._current_video_name
        fmt = self.settings.get("output.summary_format", "txt").lower().strip()
        if fmt not in ("txt", "md"):
            fmt = "txt"
        preferred = Path(output_dir) / f"{name}_summary.{fmt}"
        if preferred.exists():
            return preferred
        found = FileWriter(output_dir).find_summary_file(name)
        if found:
            return found
        return preferred

    def _active_edit(self) -> QTextEdit:
        if self.result_tabs.currentIndex() == 1:
            return self.summary_view
        return self.transcript_view

    def _toggle_search(self) -> None:
        self._search_controller.toggle()

    def _clear_all_highlights(self) -> None:
        self.transcript_view.setExtraSelections([])
        self.summary_view.setExtraSelections([])

    def _on_replace_count(self, count: int) -> None:
        self.status_bar.showMessage(f"已替换 {count} 处文本", 5000)

    # ── 常用目录管理 ──

    def _fav_input_dir(self) -> None:
        self._fav_helper.fav_input_dir(self)

    def _fav_output_dir(self) -> None:
        self._fav_helper.fav_output_dir(self)

    def _fav_both_dirs(self) -> None:
        self._fav_helper.fav_both_dirs(self)

    def _clear_all_input_dirs(self) -> None:
        self._fav_helper.clear_all_input_dirs(self)

    def _clear_all_output_dirs(self) -> None:
        self._fav_helper.clear_all_output_dirs(self)

    def eventFilter(self, obj, event):
        return super().eventFilter(obj, event)

    # ── input selection slots ──

    def _get_media_filter_str(self) -> str:
        return (
            "媒体文件 ("
            + " ".join(f"*{ext}" for ext in sorted(self._media_exts))
            + ");;所有文件 (*.*)"
        )

    def _select_input_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择媒体文件",
            "",
            self._get_media_filter_str(),
        )
        if paths:
            self._input_folder = None
            self._mirror_subdirs = False
            self._mirror_depth = 1
            self._name_to_output_dir = {}
            if len(paths) == 1:
                self.input_combo.setCurrentText(paths[0])
            else:
                self.input_combo.setCurrentText(f"已选择 {len(paths)} 个文件")
            self._video_files = list(paths)
            last_dir = Path(paths[0]).parent.name
            self.output_combo.setCurrentText(str(_PROJECT_ROOT / "output" / last_dir))

    def _select_input_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择媒体文件夹")
        if folder:
            self._scan_context = {"mode": "folder_select", "folder": folder}
            self._start_scan(folder)

    def _on_input_combo_activated(self, index: int) -> None:
        """从下拉框选择常用目录时，自动执行对应的选择文件/文件夹逻辑"""
        text = self.input_combo.itemText(index).strip()
        if not text:
            return

        path = Path(FavoriteDirHelper._extract_dir_from_input(text))

        if path.is_file():
            self._input_folder = None
            self._mirror_subdirs = False
            self._mirror_depth = 1
            self._name_to_output_dir = {}
            self._video_files = [str(path)]
            self.input_combo.setCurrentText(str(path))
            last_dir = path.parent.name
            self.output_combo.setCurrentText(str(_PROJECT_ROOT / "output" / last_dir))
        elif path.is_dir():
            self._scan_context = {
                "mode": "combo_select",
                "folder": str(path),
                "index": index,
            }
            self._start_scan(str(path))

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

    def _start_scan(self, folder: str) -> None:
        """启动后台线程扫描文件夹中的媒体文件。"""
        self.status_bar.showMessage("正在扫描文件...")
        self.input_folder_btn.setEnabled(False)
        self._wait_async_thread("_scan_thread")
        thread = QThread()
        worker = ScanFilesWorker(folder, self._media_exts)
        worker.moveToThread(thread)

        def _cleanup():
            self._scan_thread = None
            self._scan_worker = None
            self.input_folder_btn.setEnabled(True)

        worker.result.connect(self._on_scan_result)
        thread.finished.connect(_cleanup)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(worker.deleteLater)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        thread.start()
        self._scan_thread = thread
        self._scan_worker = worker

    def _on_scan_result(self, file_metas: list[tuple[str, int]]) -> None:
        """扫描完成后，根据上下文弹出选择对话框或提示无文件。"""
        ctx = self._scan_context
        self._scan_context = None
        self.status_bar.showMessage("")

        if not file_metas:
            QMessageBox.information(
                self, "提示", "该文件夹及其子目录中未找到支持的媒体文件"
            )
            return

        folder = ctx["folder"]
        dialog = VideoSelectionDialog(file_metas, self, folder=folder)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        selected_files = dialog.get_selected_files()
        if not selected_files:
            QMessageBox.information(self, "提示", "未选择任何文件")
            return

        self._mirror_subdirs = dialog.get_mirror_subdirs()
        self._mirror_depth = dialog.get_mirror_depth()
        self._input_folder = dialog.get_input_folder() if self._mirror_subdirs else None
        self._name_to_output_dir = {}

        if ctx["mode"] == "folder_select":
            self.input_combo.setCurrentText(
                f"{folder} (已选择 {len(selected_files)} 个文件)"
            )
            last_dir = Path(folder).name
        else:
            index = ctx["index"]
            self.input_combo.setItemText(
                index,
                f"{folder} (已选择 {len(selected_files)} 个文件)",
            )
            last_dir = Path(folder).name

        self._video_files = selected_files
        self.output_combo.setCurrentText(str(_PROJECT_ROOT / "output" / last_dir))

    def _select_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if folder:
            self.output_combo.setCurrentText(folder)

    def _load_history_files(self) -> None:
        """从输出目录加载历史转写和总结文件，填充文件列表。"""
        output_dir = self.output_combo.currentText().strip() or _DEFAULT_OUTPUT_DIR
        output_path = Path(output_dir)

        if not output_path.exists():
            QMessageBox.warning(self, "提示", f"输出目录不存在: {output_dir}")
            return

        self._history_loaded = True
        self._name_to_output_dir = {}

        transcript_files: list[Path] = []
        if self._mirror_subdirs:
            for ext in ("txt", "srt", "vtt", "json"):
                try:
                    transcript_files.extend(output_path.rglob(f"*.{ext}"))
                except OSError:
                    pass
        else:
            for ext in ("txt", "srt", "vtt", "json"):
                transcript_files.extend(output_path.glob(f"*.{ext}"))
        transcript_files.sort(key=lambda p: p.name.lower())

        found_names: set[str] = set()
        for txt_file in transcript_files:
            if txt_file.name.endswith("_summary.txt") or txt_file.name.endswith(
                "_summary.md"
            ):
                continue
            if txt_file.name.endswith("_keywords.txt"):
                continue
            found_names.add(txt_file.stem)
            if self._mirror_subdirs:
                self._name_to_output_dir[txt_file.stem] = str(txt_file.parent)

        summary_files: list[Path] = []
        if self._mirror_subdirs:
            try:
                summary_files.extend(
                    p
                    for p in output_path.rglob("*_summary.*")
                    if p.suffix in (".txt", ".md")
                )
            except OSError:
                pass
        else:
            summary_files.extend(
                p
                for p in output_path.glob("*_summary.*")
                if p.suffix in (".txt", ".md")
            )
        for summary_file in summary_files:
            if summary_file.suffix not in (".txt", ".md"):
                continue
            video_name = summary_file.stem.removesuffix("_summary")
            if video_name:
                found_names.add(video_name)
                if self._mirror_subdirs and video_name not in self._name_to_output_dir:
                    self._name_to_output_dir[video_name] = str(summary_file.parent)

        if not found_names:
            QMessageBox.warning(
                self,
                "提示",
                f"未在输出目录中找到历史文件:\n{output_dir}\n\n"
                "请先完成转写，或更换包含历史文件的输出目录后重试。",
            )
            self.status_bar.showMessage("未找到历史文件")
            return

        self.file_list.clear()
        self._completed_names.clear()

        for video_name in sorted(found_names, key=str.lower):
            self._completed_names.add(video_name)
            item = QListWidgetItem(video_name)
            item.setData(Qt.ItemDataRole.UserRole, video_name)
            self.file_list.addItem(item)

        self.status_bar.showMessage(f"已加载 {len(found_names)} 个历史文件")
        self.file_list.setCurrentRow(0)

    # ── worker 生成 ──

    def _get_output_dir(self) -> str:
        """获取并规范化输出目录路径，不存在时自动创建。"""
        output_dir = self.output_combo.currentText().strip() or _DEFAULT_OUTPUT_DIR
        self.output_combo.setCurrentText(output_dir)
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        return output_dir

    def _get_stream_setting(self) -> bool:
        """根据当前配置决定是否使用流式输出"""
        provider = self.settings.get("summarization.provider", "ollama")
        if provider == "ollama":
            return True
        mode = self.settings.get(f"summarization.{provider}_mode", "single")
        if mode == "multi":
            return False
        return self.settings.get_bool(f"summarization.{provider}_stream", True)

    def _resolve_video_output_dir(self, video_name: str) -> str:
        base_dir = self.output_combo.currentText().strip() or _DEFAULT_OUTPUT_DIR
        if not self._mirror_subdirs:
            return base_dir
        if video_name in self._name_to_output_dir:
            return self._name_to_output_dir[video_name]
        if self._input_folder:
            for vf in self._video_files:
                if Path(vf).stem == video_name:
                    return TranscriptionService.get_file_output_dir(
                        vf, base_dir, self._input_folder, self._mirror_depth
                    )
        return base_dir

    def _update_multi_thread_flag(self) -> None:
        """更新多线程标志"""
        provider = self.settings.get("summarization.provider", "ollama")
        mode = self.settings.get(f"summarization.{provider}_mode", "single")
        self._is_multi_thread = provider in ("nvidia", "zhipu") and mode == "multi"

    def _start_worker(self, thread: QThread, worker) -> None:
        """启动 worker 线程并连接通用信号"""
        if self._worker_thread is not None and self._worker_thread.isRunning():
            try:
                self._worker_thread.finished.disconnect(self._on_thread_finished)
            except (RuntimeError, TypeError):
                pass
            if self._worker is not None:
                if hasattr(self._worker, "cancel"):
                    self._worker.cancel()
                if hasattr(self._worker, "unpause"):
                    self._worker.unpause()
            self._worker_thread.quit()
            self._worker_thread.wait(5000)
            if self._worker_thread.isRunning():
                self._worker_thread.terminate()
                self._worker_thread.wait(1000)
            if self._worker is not None:
                self._worker.deleteLater()
            self._worker = None
            self._worker_thread = None

        self._worker_thread = thread
        self._worker = worker

        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        thread.start()

    def _set_busy_state(self, busy: bool) -> None:
        """设置界面忙碌状态：禁用/启用各操作按钮，控制暂停按钮可见性。"""
        self.transcribe_btn.setEnabled(not busy)
        self.summarize_btn.setEnabled(not busy)
        self.combine_btn.setEnabled(not busy)
        self.input_file_btn.setEnabled(not busy)
        self.input_folder_btn.setEnabled(not busy)
        self.output_btn.setEnabled(not busy)
        self.load_history_btn.setEnabled(not busy)
        self._update_pause_button(busy)
        if not busy:
            self.pause_btn.setText("暂停")

    def _update_pause_button(self, busy: bool) -> None:
        """根据当前模式和阶段决定暂停按钮的启用状态。"""
        if not busy:
            self.pause_btn.setEnabled(False)
            return

        if self._current_mode == "transcribe":
            self.pause_btn.setEnabled(True)
            return

        if self._current_mode == "summarize":
            # 仅总结模式：仅 Ollama 可暂停
            provider = self.settings.get("summarization.provider", "ollama")
            self.pause_btn.setEnabled(provider == "ollama")
            return

        if self._current_mode == "pipeline":
            if self._current_phase == "transcribe":
                self.pause_btn.setEnabled(True)
            else:
                # 总结阶段：仅 Ollama 可暂停
                provider = self.settings.get("summarization.provider", "ollama")
                self.pause_btn.setEnabled(provider == "ollama")
            return

        self.pause_btn.setEnabled(False)

    def _reset_counters(self) -> None:
        """重置转写/总结的成功/失败计数器。"""
        self._tx_success = 0
        self._tx_fail = 0
        self._sum_success = 0
        self._sum_fail = 0
        self._fail_records = []

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

        # ── 管道模式：根据阶段分别处理 ──
        if self._current_mode == "pipeline" and hasattr(self._worker, "sum_pause"):
            if self._current_phase == "summarize":
                # 总结阶段暂停/继续
                if self._worker.is_sum_paused:
                    self._worker.sum_resume()
                    self.pause_btn.setText("暂停")
                    self.status_bar.showMessage("总结已继续")
                else:
                    self._worker.sum_pause()
                    self.pause_btn.setText("继续")
                    self.status_bar.showMessage("总结已暂停")
                return
            else:
                # 转写阶段暂停/继续
                if not hasattr(self._worker, "pause") or not hasattr(
                    self._worker, "resume"
                ):
                    return
                if self._worker.is_paused:
                    self._worker.resume()
                    self.pause_btn.setText("暂停")
                    self.status_bar.showMessage("转写已继续")
                else:
                    self._worker.pause()
                    self.pause_btn.setText("继续")
                    self.status_bar.showMessage("正在等待当前音频/切片转写完成后暂停…")
                return

        # ── 仅总结模式 ──
        if self._current_mode == "summarize" and hasattr(self._worker, "pause"):
            if self._worker.is_paused:
                self._worker.resume()
                self.pause_btn.setText("暂停")
                self.status_bar.showMessage("总结已继续")
            else:
                self._worker.pause()
                self.pause_btn.setText("继续")
                self.status_bar.showMessage("总结已暂停")
            return

        # ── 仅转写模式（原有逻辑） ──
        if not hasattr(self._worker, "pause") or not hasattr(self._worker, "resume"):
            return

        if self._worker.is_paused:
            self._worker.resume()
            self.pause_btn.setText("暂停")
            self.status_bar.showMessage("转写已继续")
        else:
            self._worker.pause()
            self.pause_btn.setText("继续")
            self.status_bar.showMessage("正在等待当前音频/切片转写完成后暂停…")

    # ── 仅转写 ──

    def _on_transcribe(self) -> None:
        """「仅转写」按钮点击处理：校验输入 → 清空结果 → 启动转写线程。"""
        if not self._video_files:
            QMessageBox.warning(self, "提示", "请先选择输入文件或文件夹。")
            return

        output_dir = self._get_output_dir()

        self.file_list.clear()
        self.transcript_view.clear()
        self.summary_view.clear()
        self._completed_names.clear()
        self.log_panel.clear()

        self._current_mode = "transcribe"
        self._reset_counters()

        total = len(self._video_files)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"0/{total}")

        self._set_busy_state(True)

        thread = QThread()
        worker = TranscribeWorker(
            self._video_files,
            output_dir,
            self.settings,
            input_folder=self._input_folder,
            mirror_depth=self._mirror_depth,
        )

        worker.video_done.connect(self._on_single_video_transcribed)
        worker.video_error.connect(self._on_transcribe_error)
        worker.progress.connect(self._on_progress)
        worker.error.connect(self._on_worker_error)
        worker.confirm_download.connect(self._on_confirm_download)

        self._start_worker(thread, worker)

    def _on_single_video_transcribed(
        self, video_name: str, segments_count: int, output_paths: list
    ) -> None:
        """单个文件转写完成 —— 立即更新 GUI"""
        self._tx_success += 1
        self._current_video_name = video_name
        if video_name not in self._completed_names:
            self._completed_names.add(video_name)
            item = QListWidgetItem(video_name)
            item.setData(Qt.ItemDataRole.UserRole, video_name)
            self.file_list.addItem(item)

        self.file_list.setCurrentItem(self.file_list.item(self.file_list.count() - 1))
        if output_paths:
            try:
                self.transcript_view.setPlainText(
                    Path(output_paths[0]).read_text(encoding="utf-8-sig")
                )
            except Exception:
                self._load_transcript_content(video_name)
        else:
            self._load_transcript_content(video_name)

        self.status_bar.showMessage(
            f"转写完成: {video_name} ({segments_count} 段)", 5000
        )

    def _load_transcript_content(self, video_name: str) -> None:
        """加载指定文件的转写文本到编辑区"""
        output_dir = self._resolve_video_output_dir(video_name)
        transcript_path = FileWriter(output_dir).find_transcript_file(video_name)
        if transcript_path is None:
            return
        try:
            self.transcript_view.setPlainText(
                transcript_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, UnicodeDecodeError) as exc:
            get_logger("video2text").warning(
                "读取转写文件失败: %s (%s)", transcript_path.name, exc
            )

    def _on_transcribe_error(self, video_name: str, error_msg: str) -> None:
        """单个文件转写失败"""
        self._tx_fail += 1
        self._fail_records.append((video_name, "转写", error_msg))
        self.status_bar.showMessage(f"转写失败: {video_name} — {error_msg}", 5000)

    # ── 仅总结 ──

    def _on_summarize(self) -> None:
        output_dir = self._get_output_dir()
        video_files: list[str] = list(self._video_files)

        if not video_files and self._completed_names:
            video_files = []
            for name in self._completed_names:
                resolved_dir = self._resolve_video_output_dir(name)
                transcript = FileWriter(resolved_dir).find_transcript_file(name)
                if transcript:
                    video_files.append(str(transcript))
                else:
                    video_files.append(name)

        if not video_files:
            QMessageBox.warning(
                self,
                "提示",
                "请先选择文件或文件夹，并完成转写后再进行总结。",
            )
            return

        self._current_mode = "summarize"
        self._reset_counters()
        self._update_multi_thread_flag()

        total = len(video_files)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"0/{total}")

        self._set_busy_state(True)

        custom_prompt = self.ollama_prompt_edit.toPlainText().strip()

        thread = QThread()
        worker = SummarizeWorker(
            video_files,
            output_dir,
            self.settings,
            custom_prompt,
            stream=self._get_stream_setting(),
            input_folder=self._input_folder,
            mirror_depth=self._mirror_depth,
        )

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
        self.summary_view.ensureCursorVisible()

    def _on_summarize_started(self, video_name: str) -> None:
        """开始总结新文件时清空摘要区（多线程模式下不清空）"""
        if not self._is_multi_thread:
            self.summary_view.clear()

    def _on_single_video_summarized(self, video_name: str, summary: str) -> None:
        """单个文件总结完成"""
        self._sum_success += 1
        if video_name not in self._completed_names:
            self._completed_names.add(video_name)
            item = QListWidgetItem(video_name)
            item.setData(Qt.ItemDataRole.UserRole, video_name)
            self.file_list.addItem(item)
        if not self._is_multi_thread:
            self.summary_view.setPlainText(summary)
        elif self._current_video_name == video_name:
            self.summary_view.setPlainText(summary)
        self.status_bar.showMessage(f"总结完成: {video_name}", 5000)

    def _on_summarize_error(self, video_name: str, error_msg: str) -> None:
        """单个文件总结失败"""
        self._sum_fail += 1
        self._fail_records.append((video_name, "总结", error_msg))
        self.status_bar.showMessage(f"总结失败: {video_name} — {error_msg}", 5000)

    # ── 转写总结管道 ──

    def _on_pipeline(self) -> None:
        """「转写总结」按钮点击处理：校验输入 → 启动管道线程（先转写后总结）。"""
        if not self._video_files:
            QMessageBox.warning(self, "提示", "请先选择输入文件或文件夹。")
            return

        output_dir = self._get_output_dir()

        self.file_list.clear()
        self.transcript_view.clear()
        self.summary_view.clear()
        self._completed_names.clear()
        self.log_panel.clear()

        self._current_mode = "pipeline"
        self._current_phase = "transcribe"
        self._reset_counters()
        self._update_multi_thread_flag()

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
            stream=self._get_stream_setting(),
            input_folder=self._input_folder,
            mirror_depth=self._mirror_depth,
        )

        worker.transcribe_done.connect(self._on_single_video_transcribed)
        worker.transcribe_error.connect(self._on_transcribe_error)
        worker.phase_changed.connect(self._on_phase_changed)
        worker.summarize_started.connect(self._on_summarize_started)
        worker.stream_token.connect(self._on_stream_token)
        worker.summarize_done.connect(self._on_single_video_summarized)
        worker.summarize_error.connect(self._on_summarize_error)
        worker.progress.connect(self._on_progress)
        worker.error.connect(self._on_worker_error)
        worker.confirm_download.connect(self._on_confirm_download)

        self._start_worker(thread, worker)

    # ── progress / completion ──

    def _on_progress(self, completed: int, total: int) -> None:
        self.progress_bar.setValue(completed)
        self.progress_label.setText(f"{completed}/{total}")

    def _on_phase_changed(self, phase: str) -> None:
        """管道阶段变更回调：更新暂停按钮状态。"""
        self._current_phase = phase
        if self._worker is not None:
            self._update_pause_button(True)
        if phase == "summarize":
            self.status_bar.showMessage("正在切换到总结阶段...")
        elif phase == "transcribe":
            self.status_bar.showMessage("正在转写阶段...")

    def _on_confirm_download(self) -> None:
        worker = self._worker
        if worker is None:
            return
        reply = QMessageBox.question(
            self,
            "模型下载确认",
            "建议使用网盘中已下载好的模型，HuggingFace 一般不可直连。\n\n"
            "如需使用其它模型，请把核心文件复制到 models 目录再到配置中更改模型路径。\n\n"
            "是否开始下载模型？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        worker.set_download_confirmed(reply == QMessageBox.StandardButton.Yes)

    def _on_thread_finished(self) -> None:
        sender = self.sender()
        if sender is not None and sender is not self._worker_thread:
            return

        self._set_busy_state(False)

        worker = self._worker
        thread = self._worker_thread
        self._worker = None
        self._worker_thread = None

        if thread is not None:
            try:
                thread.finished.disconnect(self._on_thread_finished)
            except (RuntimeError, TypeError):
                pass
            thread.wait(3000)
        if worker is not None:
            worker.deleteLater()

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
            "pipeline": "转写总结",
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
        if not self._completed_names and not self._history_loaded:
            QMessageBox.warning(self, "提示", "请先完成转写或加载历史文件")
            return

        output_dir = self.output_combo.currentText().strip() or _DEFAULT_OUTPUT_DIR
        video_files = list(self._completed_names)

        if self._result_viewer is None or not self._result_viewer.isVisible():
            self._result_viewer = ResultViewerWindow(self)

        self._result_viewer.load_files(
            video_files, output_dir, folder_mode=self._mirror_subdirs
        )
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
                f"未找到原始文件: {video_name}\n请先在主界面加载包含该文件的目录。",
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
        worker = TranscribeWorker(
            [video_path],
            output_dir,
            self.settings,
            input_folder=self._input_folder,
            mirror_depth=self._mirror_depth,
        )
        worker.video_done.connect(self._on_single_video_transcribed)
        worker.video_error.connect(self._on_transcribe_error)
        worker.progress.connect(self._on_progress)
        worker.error.connect(self._on_worker_error)
        worker.confirm_download.connect(self._on_confirm_download)
        self._start_worker(thread, worker)

    def _on_resummarize(self, video_name: str) -> None:
        if self._worker_thread is not None and self._worker_thread.isRunning():
            QMessageBox.warning(self, "提示", "当前有任务正在运行，请等待完成后再试。")
            return

        output_dir = self._get_output_dir()
        resolved_dir = self._resolve_video_output_dir(video_name)
        transcript_path = FileWriter(resolved_dir).find_transcript_file(video_name)
        if transcript_path is None:
            QMessageBox.warning(self, "提示", f"未找到转写文件: {video_name}")
            return

        self._current_mode = "summarize"
        self._reset_counters()
        self._update_multi_thread_flag()
        self.progress_bar.setMaximum(1)
        self.progress_bar.setValue(0)
        self.progress_label.setText("0/1")
        self._set_busy_state(True)
        self.summary_view.clear()

        custom_prompt = self.ollama_prompt_edit.toPlainText().strip()

        video_path = video_name
        for vf in self._video_files:
            if Path(vf).stem == video_name:
                video_path = vf
                break
        else:
            video_path = str(transcript_path)

        thread = QThread()
        worker = SummarizeWorker(
            [video_path],
            output_dir,
            self.settings,
            custom_prompt,
            stream=self._get_stream_setting(),
            input_folder=self._input_folder,
            mirror_depth=self._mirror_depth,
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
        """文件列表选中变更时，延迟加载对应的转写和摘要内容到编辑器。"""
        if current is None:
            return
        video_name = current.data(Qt.ItemDataRole.UserRole)
        self._current_video_name = video_name
        output_dir = self._resolve_video_output_dir(video_name)

        QTimer.singleShot(0, lambda: self._load_file_content(video_name, output_dir))

    def _load_file_content(self, video_name: str, output_dir: str) -> None:
        """在事件循环空闲时加载文件内容，避免阻塞 GUI 线程。"""
        transcript_path = FileWriter(output_dir).find_transcript_file(video_name)
        if transcript_path is not None:
            try:
                self.transcript_view.setPlainText(
                    transcript_path.read_text(encoding="utf-8-sig")
                )
            except (OSError, UnicodeDecodeError) as exc:
                self.transcript_view.setPlainText(f"读取失败: {exc}")
        else:
            self.transcript_view.setPlainText("(未找到转写文件)")

        summary_path = _find_summary_path(output_dir, video_name)
        if summary_path:
            try:
                self.summary_view.setPlainText(summary_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError) as exc:
                self.summary_view.setPlainText(f"读取失败: {exc}")
        else:
            self.summary_view.setPlainText("(未找到摘要文件)")

        self._search_controller.refresh_if_active()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self._scan_thread.quit()
            self._scan_thread.wait(3000)

        if self._worker_thread is not None and self._worker_thread.isRunning():
            if self._worker is not None and hasattr(self._worker, "cancel"):
                self._worker.cancel()
            if self._worker is not None and hasattr(self._worker, "unpause"):
                self._worker.unpause()
            self._worker_thread.quit()
            if not self._worker_thread.wait(3000):
                self._worker_thread.terminate()
                self._worker_thread.wait(1000)
            if self._worker is not None:
                self._worker.deleteLater()
            self._worker = None
            self._worker_thread = None

        self.log_panel.cleanup()

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
    """GUI 主入口：启用 faulthandler、设置线程异常钩子、启动 Qt 事件循环。"""
    import faulthandler
    import threading

    _settings = Settings()
    setup_logger(
        "video2text",
        log_dir=_settings.get("paths.logs_dir", "logs"),
        level=_settings.get("app.log_level", "INFO"),
        log_to_console=False,
    )

    _log_dir = Path("logs")
    _log_dir.mkdir(parents=True, exist_ok=True)
    _crash_log = open(_log_dir / "crash.log", "a", encoding="utf-8")
    try:
        faulthandler.enable(file=_crash_log, all_threads=True)

        def _thread_excepthook(args):
            import traceback

            msg = f"Unhandled exception in thread {args.thread}:\n"
            msg += "".join(
                traceback.format_exception(
                    args.exc_type, args.exc_value, args.exc_traceback
                )
            )
            logging.getLogger("video2text").error(msg)
            try:
                with open(_log_dir / "thread_error.log", "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.now()}] {msg}\n")
            except Exception:
                pass

        threading.excepthook = _thread_excepthook

        app = QApplication()
        app.setStyle("Fusion")
        window = MainWindow()
        window.show()
        app.exec()
    finally:
        _crash_log.close()


if __name__ == "__main__":
    main()
