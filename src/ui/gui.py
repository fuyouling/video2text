"""Video2Text GUI —— 基于 PySide6 的音视频转文本图形界面"""

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
    QPixmap,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
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
from src.ui.background_content import BackgroundContent
from src.ui.gui_dialogs import ConfigEditorDialog, VideoSelectionDialog
from src.ui.startup_confirm_dialog import StartupConfirmDialog
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
from src.transcription.transcription_prompt_manager import TranscriptionPromptManager
from src.ui.startup_dependency_worker import StartupDependencyWorker
from src.utils.logger import get_logger, setup_logger
from src.i18n import (
    install_qt_translator,
    resolve_language,
    set_lang,
    t,
)

logger = get_logger(__name__)

if getattr(sys, "frozen", False):
    _PROJECT_ROOT = Path(sys.executable).parent
else:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_OUTPUT_DIR = str(_PROJECT_ROOT / "output")

_BTN_MIN_WIDTH = 100


class MainWindow(QMainWindow):
    """Video2Text 主窗口 —— 音视频转文本工具的图形界面主入口。

    功能包括：音视频文件选择、转写、总结、结果查看、暂停/继续、历史加载等。
    """

    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings()
        self.prompt_manager = PromptManager()
        self._tx_prompt_manager = TranscriptionPromptManager()
        self._video_exts = set(
            ext.lower()
            for ext in self.settings.get_list("preprocessing.supported_video_formats")
        )
        self._audio_exts = set(
            ext.lower()
            for ext in self.settings.get_list("preprocessing.supported_audio_formats")
        )
        self._input_exts = self._video_exts | self._audio_exts
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
        self._startup_dependency_thread: Optional[QThread] = None
        self._startup_dependency_worker: Optional[StartupDependencyWorker] = None
        self._input_folder: Optional[str] = None
        self._mirror_subdirs: bool = False
        self._mirror_depth: int = 1
        self._name_to_output_dir: dict[str, str] = {}
        self._current_phase = "transcribe"  # 管道当前阶段

        self._dir_manager = DirectoryManager(_PROJECT_ROOT / "favorite_dirs.json")
        self._default_output_dir = self.settings.get(
            "output.output_dir", _DEFAULT_OUTPUT_DIR
        )

        # 提示词下拉框占位符（实例属性，确保在 set_lang 之后求值）
        self._TX_PLACEHOLDER_PROMPT = t("main.placeholder_new")
        self._PLACEHOLDER_PROMPT = t("main.placeholder_new")

        # 背景图片
        self._bg_pixmap: Optional[QPixmap] = None
        self._bg_opacity: float = 0.4
        self._bg_image_path: str = ""

        self._init_ui()

        self._fav_helper = FavoriteDirHelper(
            dir_manager=self._dir_manager,
            input_combo=self.input_combo,
            output_combo=self.output_combo,
            default_output_dir=self._default_output_dir,
            status_callback=lambda msg, t: self.status_bar.showMessage(msg, t),
            parent=self,
        )
        self._fav_helper.load()
        self._load_prompt_config()
        self._load_prompt_templates()

        # 界面完整加载并渲染后，再执行启动模型完整性检测。
        # 使用 300ms 延迟而非 0ms，确保窗口完全绘制后再弹出模态确认对话框，
        # 避免在窗口未完成渲染时弹出 QMessageBox 导致 Qt 内部状态异常。
        QTimer.singleShot(300, self._startup_dependency_check)

    def _startup_dependency_check(self) -> None:
        """在主线程执行启动依赖检测和用户确认，仅下载阶段在后台线程执行。

        Phase 1（主线程，同步）：
        - 读取 is_check_model_file / is_check_dll_file 配置
        - 快速检查模型和 DLL 文件是否存在（纯文件 stat，不阻塞）
        - 完整的项立即关闭检测标记
        - 有缺失则弹出分组确认对话框
        - **绝不**将缺失项的 is_check_model_file 设为 false（否则 Phase 2 中
          check_models_integrity 会跳过下载）

        Phase 2（后台线程，异步）：
        - 用户确认后启动统一的 StartupDependencyWorker 线程串行执行
        - progress_updated 信号 → progress_bar 展示进度
        - finished 信号 → 保存配置、清理线程引用
        """
        _log = get_logger("video2text")
        try:
            self._do_startup_dependency_check()
        except Exception:
            _log.exception(t("main.dep_check_skip_warn"))

    def _do_startup_dependency_check(self) -> None:
        """_startup_dependency_check 的实际实现，由 try/except 包裹调用。"""
        _log = get_logger("video2text")
        from src.utils.model_downloader import (
            DEFAULT_MODEL_NAME,
            MODEL_CONFIG,
            ModelDownloader,
        )
        from src.utils.dll_downloader import DllDownloader

        logger = get_logger("video2text")

        do_check_model = self.settings.get_bool("app.is_check_model_file", True)
        do_check_dll = self.settings.get_bool("app.is_check_dll_file", True)

        if not do_check_model and not do_check_dll:
            return

        logger.info(t("main.dep_check_start"))
        model_missing = False
        dll_missing = False

        # ── 检查模型（仅标记，不修改缺失项的 is_check_model_file） ──
        if do_check_model:
            model_name = self.settings.get(
                "transcription.model_path", DEFAULT_MODEL_NAME
            )
            if model_name in MODEL_CONFIG:
                try:
                    downloader = ModelDownloader(model_name)
                except ValueError:
                    downloader = None
                if downloader and downloader.is_model_exists():
                    logger.info(t("main.dep_model_complete", name=model_name))
                    self.settings.set("app.is_check_model_file", "false")
                else:
                    model_missing = True
                    logger.info(t("main.dep_model_missing", name=model_name))

        # ── 检查 DLL ──
        if do_check_dll:
            dll_downloader = DllDownloader()
            if dll_downloader.is_dlls_complete():
                logger.info(t("main.dep_dll_complete"))
                self.settings.set("app.is_check_dll_file", "false")
            else:
                dll_missing = True
                logger.info(t("main.dep_dll_missing"))

        # 两者都完整 → 保存并返回
        if not model_missing and not dll_missing:
            self.settings.save()
            logger.info(t("main.dep_all_ready"))
            return

        # ── Phase 1: 弹窗确认 ──
        dialog = StartupConfirmDialog(model_missing, dll_missing, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            # 用户取消 → 关闭所有标记，下次不再弹出
            self.settings.set("app.is_check_model_file", "false")
            self.settings.set("app.is_check_dll_file", "false")
            self.settings.save()
            logger.info(t("main.dep_user_cancel"))
            return

        # ── Phase 2: 确认下载 → 启动后台线程 ──
        logger.info(t("main.dep_user_confirmed"))
        result = dialog.get_result()
        self._start_dependency_download_thread(
            download_model=result["download_model"],
            download_dll=result["download_dll"],
            keep_archive=result["keep_archive"],
        )

    def _start_dependency_download_thread(
        self, download_model: bool, download_dll: bool, keep_archive: bool
    ) -> None:
        """启动统一的依赖下载后台线程（串行执行模型 → DLL）。"""
        logger = get_logger("video2text")
        logger.info(
            t("main.dep_download_start"),
            download_model, download_dll, keep_archive,
        )
        self._wait_async_thread("_startup_dependency_thread")
        thread = QThread()
        worker = StartupDependencyWorker(download_model, download_dll, keep_archive)
        worker.moveToThread(thread)

        worker.phase_changed.connect(self._on_dependency_phase_changed)
        worker.progress_updated.connect(self._on_dependency_progress)
        worker.finished.connect(self._on_dependency_finished)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_dependency_thread_finished)
        thread.start()

        self._startup_dependency_thread = thread
        self._startup_dependency_worker = worker

    def _on_dependency_thread_finished(self) -> None:
        """后台线程退出后的清理槽：调度 QThread 删除并清除 Python 引用。

        此槽通过 thread.finished 信号触发，保证底层线程已完全退出。
        此时安全地 deleteLater() 并清除引用，避免 QThread 析构时
        检测到线程仍在运行而触发 terminate/abort。
        """
        thread = self._startup_dependency_thread
        if thread is not None:
            thread.deleteLater()
        self._startup_dependency_thread = None
        self._startup_dependency_worker = None

    def _on_dependency_phase_changed(self, source: str) -> None:
        """阶段切换时重置进度条 maximum 和标签。"""
        label = t("main.dep_download_label_model") if source == "model" else t("main.dep_download_label_dll")
        self.progress_label.setText("0/0")
        self.status_bar.showMessage(t("main.dep_progress_prepare", label=label))
        self.progress_bar.setMaximum(0)
        self.progress_bar.setValue(0)

    def _on_dependency_progress(
        self,
        source: str,
        downloaded: int,
        total: int,
        file_percent: int = 0,
        current_item: int = 1,
        total_items: int = 1,
    ) -> None:
        """用 progress_bar 展示当前文件字节进度，label 显示「第 N/M 个 (X%)」。

        file_percent 表示该文件自身的下载完成度（downloaded / 该文件大小 = 100%），
        而非所有文件的总进度。
        """
        name = t("main.dep_download_phase_model") if source == "model" else t("main.dep_download_phase_dll")
        if total > 0:
            if self.progress_bar.maximum() != total:
                self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(downloaded)
        else:
            self.progress_bar.setMaximum(0)
            self.progress_bar.setValue(downloaded)
        # file_percent 为「当前文件」自身的实时下载完成度，
        # 例：第 2 个文件共 100M，已下 50M → 显示 50%。
        self.progress_label.setText(
            f"{current_item}/{total_items} "
        )
        remain = total - downloaded if total > 0 else 0
        self.status_bar.showMessage(
            t("main.dep_progress_downloading", name=name, current=current_item, total=total_items, downloaded=self._fmt_size(downloaded), size=self._fmt_size(total))
        )

    def _on_dependency_finished(self, ok: bool) -> None:
        """主线程槽：依赖检测完成，保存配置并输出结果日志。

        配置保存必须在主线程执行：configparser 的 write 不是线程安全的，
        后台线程中直接写 config.ini 可能导致主线程读取到损坏的配置数据，
        进而引发未处理异常 → Qt 事件循环崩溃 → 窗口自动关闭。

        注意：不在此处清除 _startup_dependency_thread / _startup_dependency_worker 引用。
        QThread 的 Python 包装必须保留引用直到底层线程完全退出（thread.finished），
        否则 GC 可能在线程运行中回收 QThread → "QThread: Destroyed while thread
        is still running" → terminate/abort → 进程崩溃。
        引用清除交由 _on_dependency_thread_finished 槽处理。
        """
        _log = get_logger("video2text")

        try:
            self.settings.set("app.is_check_model_file", "false")
            self.settings.set("app.is_check_dll_file", "false")
            self.settings.save()
            _log.info(t("main.dep_check_saved"))
        except Exception:
            _log.warning(t("main.dep_check_save_fail"))
        if ok:
            _log.info(t("main.dep_check_passed"))
            self.progress_bar.setMaximum(1)
            self.progress_bar.setValue(1)
            self.progress_label.setText(t("main.dep_check_label_done"))
            self.status_bar.showMessage(t("main.dep_check_status_passed"), 5000)
        else:
            _log.warning(t("main.dep_check_warn_fail"))
            self.progress_bar.setMaximum(1)
            self.progress_bar.setValue(0)
            self.progress_label.setText(t("main.dep_check_label_failed"))
            self.status_bar.showMessage(
                t("main.dep_check_warn_user"), 8000
            )

    @staticmethod
    def _fmt_size(size: int) -> str:
        """将字节数格式化为可读形式。"""
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024.0:
                return f"{size:.1f}{unit}"
            size /= 1024.0
        return f"{size:.1f}TB"

    def _init_ui(self) -> None:
        """初始化主窗口 UI 布局：菜单栏、输入输出行、进度条、日志面板、结果面板。"""
        self.setWindowTitle("Video2Text")
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

        self._bg_content = BackgroundContent()
        self.setCentralWidget(self._bg_content)
        root = QVBoxLayout(self._bg_content)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.main_panel = QWidget()
        main_layout = QVBoxLayout(self.main_panel)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)

        top_grid = QGridLayout()
        top_grid.setSpacing(8)
        self._setup_input_row(top_grid, 0)
        self._setup_output_row(top_grid, 1)
        self._setup_run_row(top_grid, 2)
        top_grid.setColumnStretch(1, 1)
        main_layout.addLayout(top_grid)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.log_panel = LogPanel()
        splitter.addWidget(self.log_panel)

        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.addWidget(self._create_results_panel())
        right_splitter.addWidget(self._create_prompt_panel())
        right_splitter.setStretchFactor(0, 3)
        right_splitter.setStretchFactor(1, 1)
        splitter.addWidget(right_splitter)

        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 5)
        main_layout.addWidget(splitter, 1)

        root.addWidget(self.main_panel, 1)

        self.voice_panel = QWidget()
        self._voice_layout = QVBoxLayout(self.voice_panel)
        self._voice_layout.setContentsMargins(0, 0, 0, 0)
        self._voice_layout.setSpacing(0)
        self._voice_widget = None  # 延迟创建，首次切换到 VoiceToText 时才实例化
        self.voice_panel.hide()
        root.addWidget(self.voice_panel, 1)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage(t("main.config_path_status", path=self.settings.config_path))

        # 在窗口显示前加载背景图片和透明样式，避免先显示默认样式再闪变
        self._load_bg_settings()

        # 在所有控件构造完成且样式就绪后才最大化显示
        self.showMaximized()

    def _setup_input_row(self, grid: QGridLayout, row: int) -> None:
        """在 grid 的第 row 行构建输入控件行。"""
        grid.addWidget(QLabel(t("main.input_label")), row, 0)
        self.input_combo = QComboBox()
        self.input_combo.setEditable(True)
        self.input_combo.setPlaceholderText(t("main.input_placeholder"))
        self.input_combo.lineEdit().setPlaceholderText(self.input_combo.placeholderText())
        self.input_combo.setMinimumWidth(300)
        self.input_combo.activated.connect(self._on_input_combo_activated)
        grid.addWidget(self.input_combo, row, 1)

        self.input_folder_btn = QPushButton(t("main.input_btn"))
        self.input_folder_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.input_folder_btn.clicked.connect(self._select_input_folder)
        grid.addWidget(self.input_folder_btn, row, 2)
        self.pause_btn = QPushButton(t("main.pause_btn"))
        self.pause_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.pause_btn.setToolTip(t("main.pause_tooltip"))
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._on_pause_resume)
        grid.addWidget(self.pause_btn, row, 3)
        self.stop_btn = QPushButton(t("main.stop_btn"))
        self.stop_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.stop_btn.setToolTip(t("main.stop_tooltip"))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        grid.addWidget(self.stop_btn, row, 4)

    def _setup_output_row(self, grid: QGridLayout, row: int) -> None:
        """在 grid 的第 row 行构建输出控件行。"""
        grid.addWidget(QLabel(t("main.output_label")), row, 0)
        self.output_combo = QComboBox()
        self.output_combo.setEditable(True)
        self.output_combo.setCurrentText(self._default_output_dir)
        self.output_combo.setMinimumWidth(300)
        grid.addWidget(self.output_combo, row, 1)
        self.output_btn = QPushButton(t("main.output_btn"))
        self.output_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.output_btn.clicked.connect(self._select_output_dir)
        grid.addWidget(self.output_btn, row, 2)
        self.load_history_btn = QPushButton(t("main.load_history_btn"))
        self.load_history_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.load_history_btn.setToolTip(t("main.load_history_tooltip"))
        self.load_history_btn.clicked.connect(self._load_history_files)
        grid.addWidget(self.load_history_btn, row, 3)
        self.open_viewer_btn = QPushButton(t("main.open_viewer_btn"))
        self.open_viewer_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.open_viewer_btn.setToolTip(t("main.open_viewer_tooltip"))
        self.open_viewer_btn.clicked.connect(self._open_result_viewer)
        grid.addWidget(self.open_viewer_btn, row, 4)

    def _setup_run_row(self, grid: QGridLayout, row: int) -> None:
        """在 grid 的第 row 行构建运行控件行（进度条 + 操作按钮）。"""
        self.progress_label = QLabel(t("main.progress_label"))
        grid.addWidget(self.progress_label, row, 0)
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        grid.addWidget(self.progress_bar, row, 1)
        self.transcribe_btn = QPushButton(t("main.transcribe_btn"))
        self.transcribe_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.transcribe_btn.setToolTip(t("main.transcribe_tooltip"))
        self.transcribe_btn.clicked.connect(self._on_transcribe)
        grid.addWidget(self.transcribe_btn, row, 2)
        self.summarize_btn = QPushButton(t("main.summarize_btn"))
        self.summarize_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.summarize_btn.setToolTip(t("main.summarize_tooltip"))
        self.summarize_btn.clicked.connect(self._on_summarize)
        grid.addWidget(self.summarize_btn, row, 3)
        self.combine_btn = QPushButton(t("main.combine_btn"))
        self.combine_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        self.combine_btn.setToolTip(t("main.combine_tooltip"))
        self.combine_btn.clicked.connect(self._on_pipeline)
        grid.addWidget(self.combine_btn, row, 4)

    def _create_results_panel(self) -> QWidget:
        results_group = QGroupBox(t("main.results_group"))
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
        self.transcript_view.setPlaceholderText(t("main.transcript_placeholder"))
        self.result_tabs.addTab(self.transcript_view, t("main.transcript_tab"))
        self.summary_view = QTextEdit()
        self.summary_view.setFont(QFont("Consolas", 9))
        self.summary_view.setPlaceholderText(t("main.summary_placeholder"))
        self.result_tabs.addTab(self.summary_view, t("main.summary_tab"))
        self.result_tabs.currentChanged.connect(self._on_tab_changed)
        content_layout.addWidget(self.result_tabs, 3)

        save_transcript_action = QAction(t("main.save_action"), self)
        save_transcript_action.setShortcut(QKeySequence("Ctrl+S"))
        save_transcript_action.triggered.connect(self._save_transcript)
        self.addAction(save_transcript_action)

        find_action = QAction(t("main.find_action"), self)
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
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        tx_prompt_group = QGroupBox(t("main.tx_prompt_group"))
        tx_prompt_layout = QVBoxLayout(tx_prompt_group)

        self.tx_prompt_template_combo = QComboBox()
        self.tx_prompt_template_combo.setEditable(True)
        self.tx_prompt_template_combo.setObjectName("TxPromptCombo")
        self.tx_prompt_template_combo.setMinimumWidth(150)
        self.tx_prompt_template_combo.setPlaceholderText(t("main.tx_prompt_template_placeholder"))
        self.tx_prompt_template_combo.lineEdit().setPlaceholderText(self.tx_prompt_template_combo.placeholderText())
        self.tx_prompt_template_combo.currentTextChanged.connect(
            self._on_tx_prompt_template_selected
        )

        self.initial_prompt_edit = QTextEdit()
        self.initial_prompt_edit.setMaximumHeight(60)
        self.initial_prompt_edit.setPlaceholderText(t("main.tx_prompt_placeholder"))
        tx_prompt_layout.addWidget(self.initial_prompt_edit)

        self.hotwords_edit = QTextEdit()
        self.hotwords_edit.setMaximumHeight(40)
        self.hotwords_edit.setPlaceholderText(t("main.hotwords_placeholder"))
        tx_prompt_layout.addWidget(self.hotwords_edit)

        tx_prompt_btn_row = QHBoxLayout()
        tx_prompt_btn_row.addWidget(self.tx_prompt_template_combo, 1)
        self.tx_prompt_save_btn = QPushButton(t("common.save"))
        self.tx_prompt_save_btn.clicked.connect(self._save_tx_prompt_template)
        tx_prompt_btn_row.addWidget(self.tx_prompt_save_btn)
        self.tx_prompt_delete_btn = QPushButton(t("common.delete"))
        self.tx_prompt_delete_btn.clicked.connect(self._delete_tx_prompt_template)
        tx_prompt_btn_row.addWidget(self.tx_prompt_delete_btn)
        tx_prompt_layout.addLayout(tx_prompt_btn_row)

        layout.addWidget(tx_prompt_group, 1)

        summary_prompt_group = QGroupBox(t("main.summary_prompt_group"))
        prompt_layout = QVBoxLayout(summary_prompt_group)

        self.ollama_prompt_edit = QTextEdit()
        self.ollama_prompt_edit.setMaximumHeight(100)
        self.ollama_prompt_edit.setPlaceholderText(t("main.summary_prompt_placeholder"))
        prompt_layout.addWidget(self.ollama_prompt_edit)

        prompt_btn_row = QHBoxLayout()
        self.prompt_template_combo = QComboBox()
        self.prompt_template_combo.setEditable(True)
        self.prompt_template_combo.setObjectName("SummaryPromptCombo")
        self.prompt_template_combo.setMinimumWidth(150)
        self.prompt_template_combo.setPlaceholderText(t("main.summary_prompt_template_placeholder"))
        self.prompt_template_combo.lineEdit().setPlaceholderText(self.prompt_template_combo.placeholderText())
        self.prompt_template_combo.currentTextChanged.connect(
            self._on_prompt_template_selected
        )
        prompt_btn_row.addWidget(self.prompt_template_combo, 1)
        self.prompt_save_btn = QPushButton(t("common.save"))
        self.prompt_save_btn.clicked.connect(self._save_prompt_template)
        prompt_btn_row.addWidget(self.prompt_save_btn)
        self.prompt_delete_btn = QPushButton(t("common.delete"))
        self.prompt_delete_btn.clicked.connect(self._delete_prompt_template)
        prompt_btn_row.addWidget(self.prompt_delete_btn)
        self.markdown_enabled_cb = QCheckBox(t("main.markdown_cb"))
        self.markdown_enabled_cb.setToolTip(t("main.markdown_tooltip"))
        self.markdown_enabled_cb.setChecked(self.prompt_manager.get_markdown_enabled())
        self.markdown_enabled_cb.toggled.connect(self._on_markdown_toggled)
        prompt_btn_row.addWidget(self.markdown_enabled_cb)
        prompt_layout.addLayout(prompt_btn_row)

        layout.addWidget(summary_prompt_group, 1)
        return container

    def _create_menu_bar(self) -> None:
        menu_bar = self.menuBar()

        settings_menu = menu_bar.addMenu(t("menu.settings"))
        edit_config_action = settings_menu.addAction(t("menu.settings_edit_config"))
        edit_config_action.triggered.connect(self._show_config_editor)


        # 背景图片子菜单
        bg_menu = settings_menu.addMenu(t("menu.settings_bg_image"))
        bg_change_action = bg_menu.addAction(t("menu.settings_bg_change"))
        bg_change_action.triggered.connect(self._change_bg_image)
        bg_clear_action = bg_menu.addAction(t("menu.settings_bg_clear"))
        bg_clear_action.triggered.connect(self._clear_bg_image)
        bg_transparency_action = bg_menu.addAction(t("menu.settings_bg_transparency"))
        bg_transparency_action.triggered.connect(self._adjust_bg_transparency)

        fav_menu = settings_menu.addMenu(t("menu.settings_fav"))
        fav_input_action = fav_menu.addAction(t("menu.settings_fav_input"))
        fav_input_action.triggered.connect(self._fav_input_dir)
        fav_output_action = fav_menu.addAction(t("menu.settings_fav_output"))
        fav_output_action.triggered.connect(self._fav_output_dir)
        fav_both_action = fav_menu.addAction(t("menu.settings_fav_both"))
        fav_both_action.triggered.connect(self._fav_both_dirs)
        fav_menu.addSeparator()
        clear_input_action = fav_menu.addAction(t("menu.settings_fav_clear_input"))
        clear_input_action.triggered.connect(self._clear_all_input_dirs)
        clear_output_action = fav_menu.addAction(t("menu.settings_fav_clear_output"))
        clear_output_action.triggered.connect(self._clear_all_output_dirs)

        tools_menu = menu_bar.addMenu(t("menu.tools"))
        voice_action = tools_menu.addAction(t("menu.tools_voice_to_text"))
        voice_action.triggered.connect(self._on_show_voice_to_text)

        help_menu = menu_bar.addMenu(t("menu.help"))
        donate_action = help_menu.addAction(t("menu.help_donate"))
        donate_action.triggered.connect(self._show_donate)
        about_action = help_menu.addAction(t("menu.help_about"))
        about_action.triggered.connect(self._show_about)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            t("about.title"),
            "<div style='min-width:420px'>"
            "<h2 style='margin-bottom:4px'>🎬 Video2Text</h2>"
            f"<p style='color:#666;margin-top:0'>{t('about.version', version=APP_VERSION)}</p>"
            "<hr>"
            f"<p>{t('about.desc')}</p>"
            "<hr>"
            "<table style='font-size:13px'>"
            f"<tr><td style='padding:2px 12px 2px 0;color:#888'>{t('about.author_label')}</td><td>{t('about.author_value')}</td></tr>"
            f"<tr><td style='padding:2px 12px 2px 0;color:#888'>{t('about.license_label')}</td><td>{t('about.license_value')}</td></tr>"
            f"<tr><td style='padding:2px 12px 2px 0;color:#888'>{t('about.tech_label')}</td><td>{t('about.tech_value')}</td></tr>"
            f"<tr><td style='padding:2px 12px 2px 0;color:#888'>{t('about.qq_label')}</td><td>{t('about.qq_value')}</td></tr>"
            "</table>"
            "<hr>"
            "<p>"
            f'<a href="https://github.com/fuyouling/video2text">{t("about.repo_link")}</a> · '
            f'<a href="https://github.com/fuyouling/video2text/wiki">{t("about.docs_link")}</a>'
            "</p>"
            f"<p style='color:#999;font-size:12px'>{t('about.copyright')}</p>"
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
            self._refresh_output_dir()
            self.status_bar.showMessage(t("status.config_saved"), 5000)


    def _refresh_output_dir(self) -> None:
        """配置保存后刷新输出目录：同步内存配置与界面控件，使其立即生效"""
        new_default = self.settings.get("output.output_dir", _DEFAULT_OUTPUT_DIR)
        if new_default == self._default_output_dir:
            return
        self._default_output_dir = new_default
        current = self.output_combo.currentText().strip()
        if not current or current == self._default_output_dir:
            self.output_combo.setCurrentText(new_default)

    def _load_prompt_config(self) -> None:
        prompt = self.settings.get("summarization.custom_prompt", "")
        self.ollama_prompt_edit.setPlainText(prompt)

        self._load_tx_prompt_templates()

    # ── 转写提示词模板管理 ──

    def _load_tx_prompt_templates(self) -> None:
        self.tx_prompt_template_combo.blockSignals(True)
        self.tx_prompt_template_combo.clear()
        self.tx_prompt_template_combo.addItem(self._TX_PLACEHOLDER_PROMPT)
        for name in self._tx_prompt_manager.get_names():
            self.tx_prompt_template_combo.addItem(name)
        last_used = self._tx_prompt_manager.get_last_used()
        if last_used and last_used in self._tx_prompt_manager.get_names():
            idx = self.tx_prompt_template_combo.findText(last_used)
            if idx >= 0:
                self.tx_prompt_template_combo.setCurrentIndex(idx)
                tmpl = self._tx_prompt_manager.get_template(last_used)
                self.initial_prompt_edit.setPlainText(
                    tmpl.get("initial_prompt", "")
                )
                self.hotwords_edit.setPlainText(tmpl.get("hotwords", ""))
        else:
            self.initial_prompt_edit.clear()
            self.hotwords_edit.clear()
            self.tx_prompt_template_combo.clearEditText()
            self.tx_prompt_template_combo.setCurrentIndex(-1)
        self.tx_prompt_template_combo.blockSignals(False)

    def _on_tx_prompt_template_selected(self, name: str) -> None:
        if not name or name == self._TX_PLACEHOLDER_PROMPT:
            self.initial_prompt_edit.clear()
            self.hotwords_edit.clear()
            return
        tmpl = self._tx_prompt_manager.get_template(name)
        if tmpl:
            self.initial_prompt_edit.setPlainText(tmpl.get("initial_prompt", ""))
            self.hotwords_edit.setPlainText(tmpl.get("hotwords", ""))
            self._tx_prompt_manager.set_last_used(name)

    def _save_tx_prompt_template(self) -> None:
        initial_prompt = self.initial_prompt_edit.toPlainText().strip()
        hotwords = self.hotwords_edit.toPlainText().strip()
        if not initial_prompt and not hotwords:
            QMessageBox.warning(self, t("common.hint"), t("main.tx_prompt_empty_warning"))
            return

        current_name = self.tx_prompt_template_combo.currentText()
        if current_name == self._TX_PLACEHOLDER_PROMPT:
            current_name = ""
        name, ok = QInputDialog.getText(
            self, t("main.tx_prompt_save_dialog"), t("main.tx_prompt_name_label"), text=current_name
        )
        if not ok or not name.strip():
            return

        name = name.strip()
        if name == self._TX_PLACEHOLDER_PROMPT:
            QMessageBox.warning(
                self,
                t("common.hint"),
                t("main.tx_prompt_reserved_warning", name=self._TX_PLACEHOLDER_PROMPT)
            )
            return
        self._tx_prompt_manager.set_template(name, initial_prompt, hotwords)
        self._tx_prompt_manager.set_last_used(name)

        self.tx_prompt_template_combo.blockSignals(True)
        if self.tx_prompt_template_combo.findText(name) < 0:
            self.tx_prompt_template_combo.addItem(name)
        self.tx_prompt_template_combo.setCurrentText(name)
        self.tx_prompt_template_combo.blockSignals(False)

        self.status_bar.showMessage(t("main.tx_prompt_saved", name=name), 5000)

    def _delete_tx_prompt_template(self) -> None:
        name = self.tx_prompt_template_combo.currentText()
        if not name or name == self._TX_PLACEHOLDER_PROMPT:
            QMessageBox.warning(self, t("common.hint"), t("main.tx_prompt_select_to_delete"))
            return

        reply = QMessageBox.question(
            self,
            t("main.tx_prompt_delete_confirm_title"),
            t("main.tx_prompt_delete_confirm_msg", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._tx_prompt_manager.delete_template(name)

        self.tx_prompt_template_combo.blockSignals(True)
        idx = self.tx_prompt_template_combo.currentIndex()
        self.tx_prompt_template_combo.removeItem(idx)
        self.tx_prompt_template_combo.setCurrentIndex(0)
        self.tx_prompt_template_combo.blockSignals(False)

        self.initial_prompt_edit.clear()
        self.hotwords_edit.clear()
        self.status_bar.showMessage(t("main.tx_prompt_deleted", name=name), 5000)

    # ── 提示词模板管理 ──

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
        else:
            self.prompt_template_combo.clearEditText()
            self.prompt_template_combo.setCurrentIndex(-1)
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
            QMessageBox.warning(self, t("common.hint"), t("main.prompt_empty_warning"))
            return

        current_name = self.prompt_template_combo.currentText()
        if current_name == self._PLACEHOLDER_PROMPT:
            current_name = ""
        name, ok = QInputDialog.getText(
            self, t("main.prompt_save_dialog"), t("main.prompt_name_label"), text=current_name
        )
        if not ok or not name.strip():
            return

        name = name.strip()
        if name == self._PLACEHOLDER_PROMPT:
            QMessageBox.warning(
                self,
                t("common.hint"),
                t("main.prompt_reserved_warning", name=self._PLACEHOLDER_PROMPT)
            )
            return
        self.prompt_manager.set_template(name, content)
        self.prompt_manager.set_last_used(name)

        self.prompt_template_combo.blockSignals(True)
        if self.prompt_template_combo.findText(name) < 0:
            self.prompt_template_combo.addItem(name)
        self.prompt_template_combo.setCurrentText(name)
        self.prompt_template_combo.blockSignals(False)

        self.status_bar.showMessage(t("main.prompt_saved", name=name), 5000)

    def _delete_prompt_template(self) -> None:
        name = self.prompt_template_combo.currentText()
        if not name or name == self._PLACEHOLDER_PROMPT:
            QMessageBox.warning(self, t("common.hint"), t("main.prompt_select_to_delete"))
            return

        reply = QMessageBox.question(
            self,
            t("main.prompt_delete_confirm_title"),
            t("main.prompt_delete_confirm_msg", name=name),
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
        self.status_bar.showMessage(t("main.prompt_deleted", name=name), 5000)

    def _on_markdown_toggled(self, checked: bool) -> None:
        self.prompt_manager.set_markdown_enabled(checked)

    def _on_tab_changed(self, index: int) -> None:
        if index == 0:
            self.status_bar.showMessage(
                t("main.tab_transcript_hint")
            )
        elif index == 1:
            self.status_bar.showMessage(
                t("main.tab_summary_hint")
            )
        self._search_controller.refresh_if_active()

    def _save_transcript(self) -> None:
        """保存当前活动标签页的内容到文件（根据配置的输出格式自动匹配）"""
        if not self._current_video_name:
            self.status_bar.showMessage(t("main.save_no_file"), 3000)
            return
        output_dir = self.output_combo.currentText().strip() or self._default_output_dir
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
            self.status_bar.showMessage(t("main.saved_path", path=save_path), 5000)
        except OSError as exc:
            self.status_bar.showMessage(t("main.save_fail", error=exc), 5000)

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
        self.status_bar.showMessage(t("main.replaced_count", count=count), 5000)

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

    def _get_input_filter_str(self) -> str:
        return t("main.input_filter", exts=" ".join(f"*{ext}" for ext in sorted(self._input_exts)))

    def _select_input_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, t("main.select_input_folder"))
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
            self.output_combo.setCurrentText(str(Path(self._default_output_dir) / last_dir))
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
        """启动后台线程扫描文件夹中的音视频文件。"""
        self.status_bar.showMessage(t("main.scanning"))
        self.input_folder_btn.setEnabled(False)
        self._wait_async_thread("_scan_thread")
        thread = QThread()
        worker = ScanFilesWorker(folder, self._input_exts)
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
                self, t("common.hint"), t("main.no_media_in_folder")
            )
            return

        folder = ctx["folder"]
        dialog = VideoSelectionDialog(file_metas, self, folder=folder)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        selected_files = dialog.get_selected_files()
        if not selected_files:
            QMessageBox.information(self, t("common.hint"), t("main.no_files_selected"))
            return

        self._mirror_subdirs = dialog.get_mirror_subdirs()
        self._mirror_depth = dialog.get_mirror_depth()
        self._input_folder = dialog.get_input_folder() if self._mirror_subdirs else None
        self._name_to_output_dir = {}

        if ctx["mode"] == "folder_select":
            self.input_combo.setCurrentText(
                t("main.files_selected", folder=folder, count=len(selected_files))
            )
            last_dir = Path(folder).name
        else:
            index = ctx["index"]
            self.input_combo.setItemText(
                index,
                t("main.files_selected", folder=folder, count=len(selected_files)),
            )
            last_dir = Path(folder).name

        self._video_files = selected_files
        self.output_combo.setCurrentText(str(Path(self._default_output_dir) / last_dir))

    def _select_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, t("main.select_output_dir"))
        if folder:
            self.output_combo.setCurrentText(folder)

    def _load_history_files(self) -> None:
        """从输出目录加载历史转写和总结文件，填充文件列表。"""
        output_dir = self.output_combo.currentText().strip() or self._default_output_dir
        output_path = Path(output_dir)

        if not output_path.exists():
            QMessageBox.warning(self, t("common.hint"), t("main.output_dir_not_exist", dir=output_dir))
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
                t("common.hint"),
                t("main.no_history_found_msg", dir=output_dir),
            )
            self.status_bar.showMessage(t("main.no_history_found_status"))
            return

        self.file_list.clear()
        self._completed_names.clear()

        for video_name in sorted(found_names, key=str.lower):
            self._completed_names.add(video_name)
            item = QListWidgetItem(video_name)
            item.setData(Qt.ItemDataRole.UserRole, video_name)
            self.file_list.addItem(item)

        self.status_bar.showMessage(t("main.loaded_history_count", count=len(found_names)))
        self.file_list.setCurrentRow(0)

    # ── worker 生成 ──

    def _get_output_dir(self) -> str:
        """获取并规范化输出目录路径，不存在时自动创建。"""
        output_dir = self.output_combo.currentText().strip() or self._default_output_dir
        self.output_combo.setCurrentText(output_dir)
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        return output_dir

    def _apply_incremental_mode(self, video_files: list[str], output_dir: str) -> list[str]:
        if not self.settings.get_bool("app.incremental_mode", False):
            return video_files

        keep = []
        skipped = 0
        for video_path in video_files:
            name = Path(video_path).stem
            resolved_dir = self._resolve_video_output_dir(name)
            if not Path(resolved_dir).exists():
                keep.append(video_path)
                continue

            writer = FileWriter(resolved_dir)
            has_tx = writer.find_transcript_file(name) is not None
            has_sum = writer.find_summary_file(name) is not None
            if has_tx and has_sum:
                logger.info(
                    t("main.incremental_skip_detail", name=name, dir=resolved_dir),
                )
                skipped += 1
            else:
                keep.append(video_path)

        if skipped > 0:
            self.status_bar.showMessage(t("main.incremental_skipped", count=skipped), 5000)

        return keep

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
        base_dir = self.output_combo.currentText().strip() or self._default_output_dir
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
        self.input_folder_btn.setEnabled(not busy)
        self.output_btn.setEnabled(not busy)
        self.load_history_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)
        self._update_pause_button(busy)
        if not busy:
            self.pause_btn.setText(t("main.pause"))

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
            "transcribe": t("main.mode_label_transcribe"),
            "summarize": t("main.mode_label_summarize"),
            "pipeline": t("main.mode_label_pipeline"),
        }
        label = mode_map.get(self._current_mode, t("main.mode_label_task"))
        self.status_bar.showMessage(t("main.mode_error", label=label, msg=msg), 5000)

    def _on_pause_resume(self) -> None:
        if self._worker is None:
            return

        # ── 管道模式：根据阶段分别处理 ──
        if self._current_mode == "pipeline" and hasattr(self._worker, "sum_pause"):
            if self._current_phase == "summarize":
                # 总结阶段暂停/继续
                if self._worker.is_sum_paused:
                    self._worker.sum_resume()
                    self.pause_btn.setText(t("main.pause"))
                    self.status_bar.showMessage(t("main.summary_resumed"))
                else:
                    self._worker.sum_pause()
                    self.pause_btn.setText(t("main.resume"))
                    self.status_bar.showMessage(t("main.summary_paused"))
                return
            else:
                # 转写阶段暂停/继续
                if not hasattr(self._worker, "pause") or not hasattr(
                    self._worker, "resume"
                ):
                    return
                if self._worker.is_paused:
                    self._worker.resume()
                    self.pause_btn.setText(t("main.pause"))
                    self.status_bar.showMessage(t("main.transcribe_resumed"))
                else:
                    self._worker.pause()
                    self.pause_btn.setText(t("main.resume"))
                    self.status_bar.showMessage(t("main.transcribe_pause_waiting"))
                return

        # ── 仅总结模式 ──
        if self._current_mode == "summarize" and hasattr(self._worker, "pause"):
            if self._worker.is_paused:
                self._worker.resume()
                self.pause_btn.setText(t("main.pause"))
                self.status_bar.showMessage(t("main.summary_resumed"))
            else:
                self._worker.pause()
                self.pause_btn.setText(t("main.resume"))
                self.status_bar.showMessage(t("main.summary_paused"))
            return

        # ── 仅转写模式（原有逻辑） ──
        if not hasattr(self._worker, "pause") or not hasattr(self._worker, "resume"):
            return

        if self._worker.is_paused:
            self._worker.resume()
            self.pause_btn.setText(t("main.pause"))
            self.status_bar.showMessage(t("main.transcribe_resumed"))
        else:
            self._worker.pause()
            self.pause_btn.setText(t("main.resume"))
            self.status_bar.showMessage(t("main.transcribe_pause_waiting"))

    def _on_stop(self) -> None:
        """立即停止当前的转写或总结任务：请求取消并退出工作线程。

        策略：
        1. 立即设置取消标志，让 Worker 在下次检查时退出
        2. 解除暂停状态（避免 Worker 卡在暂停循环中）
        3. 等待线程自然退出（超时 5 秒）
        4. 若仍卡死（faster-whisper 在 C 层挂起），使用 terminate() 强行终止
        """
        if self._worker is None or self._worker_thread is None:
            return
        get_logger("video2text").info(t("main.stop_requested"))
        self.status_bar.showMessage(t("main.stopping_task"))
        if hasattr(self._worker, "cancel"):
            self._worker.cancel()
        if hasattr(self._worker, "unpause"):
            self._worker.unpause()
        if self._worker_thread.isRunning():
            # 先等待 5 秒让 Worker 自然退出（检查取消标志后退出循环）
            self._worker_thread.quit()
            if not self._worker_thread.wait(5000):
                get_logger("video2text").warning(
                    t("main.stop_force_terminate")
                )
                self._worker_thread.terminate()
                self._worker_thread.wait(3000)
        self._worker = None
        self._worker_thread = None
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        # 确保界面从忙碌状态恢复
        self._set_busy_state(False)
        self.status_bar.showMessage(t("main.task_stopped"))

    # ── 仅转写 ──

    def _on_transcribe(self) -> None:
        """「仅转写」按钮点击处理：校验输入 → 清空结果 → 启动转写线程。"""
        if not self._video_files:
            QMessageBox.warning(self, t("common.hint"), t("main.no_input_dialog"))
            return

        output_dir = self._get_output_dir()

        self.file_list.clear()
        self.transcript_view.clear()
        self.summary_view.clear()
        self._completed_names.clear()
        self.log_panel.clear()

        self._current_mode = "transcribe"
        self._reset_counters()

        initial_prompt = self.initial_prompt_edit.toPlainText().strip()
        hotwords = self.hotwords_edit.toPlainText().strip()

        video_files = self._apply_incremental_mode(self._video_files, output_dir)
        if not video_files:
            QMessageBox.information(
                self, t("common.hint"), t("main.incremental_all_done")
            )
            return

        total = len(video_files)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"0/{total}")

        self._set_busy_state(True)

        thread = QThread()
        worker = TranscribeWorker(
            video_files,
            output_dir,
            self.settings,
            input_folder=self._input_folder,
            mirror_depth=self._mirror_depth,
            initial_prompt=initial_prompt,
            hotwords=hotwords,
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
            t("main.tx_done_count", name=video_name, segments=segments_count), 5000
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
                t("main.load_transcript_fail", name=transcript_path.name, err=exc)
            )

    def _on_transcribe_error(self, video_name: str, error_msg: str) -> None:
        """单个文件转写失败"""
        self._tx_fail += 1
        self._fail_records.append((video_name, t("main.fail_record_transcribe"), error_msg))
        self.status_bar.showMessage(t("main.tx_fail_count", name=video_name, msg=error_msg), 5000)

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
                t("common.hint"),
                t("main.no_summarize_dialog"),
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
        self.status_bar.showMessage(t("main.sum_done_count", name=video_name), 5000)

    def _on_summarize_error(self, video_name: str, error_msg: str) -> None:
        """单个文件总结失败"""
        self._sum_fail += 1
        self._fail_records.append((video_name, t("main.fail_record_summarize"), error_msg))
        self.status_bar.showMessage(t("main.sum_fail_count", name=video_name, msg=error_msg), 5000)

    # ── 转写总结管道 ──

    def _on_pipeline(self) -> None:
        """「转写总结」按钮点击处理：校验输入 → 启动管道线程（先转写后总结）。"""
        if not self._video_files:
            QMessageBox.warning(self, t("common.hint"), t("main.no_input_dialog"))
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

        initial_prompt = self.initial_prompt_edit.toPlainText().strip()
        hotwords = self.hotwords_edit.toPlainText().strip()

        video_files = self._apply_incremental_mode(self._video_files, output_dir)
        if not video_files:
            QMessageBox.information(
                self, t("common.hint"), t("main.incremental_all_done")
            )
            return

        total = len(video_files)
        self.progress_bar.setMaximum(total * 2)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"0/{total * 2}")

        self._set_busy_state(True)

        custom_prompt = self.ollama_prompt_edit.toPlainText().strip()

        thread = QThread()
        worker = PipelineWorker(
            video_files,
            output_dir,
            self.settings,
            custom_prompt,
            stream=self._get_stream_setting(),
            input_folder=self._input_folder,
            mirror_depth=self._mirror_depth,
            initial_prompt=initial_prompt,
            hotwords=hotwords,
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
            self.status_bar.showMessage(t("main.phase_switch_summarize"))
        elif phase == "transcribe":
            self.status_bar.showMessage(t("main.phase_switch_transcribe"))

    def _on_confirm_download(self) -> None:
        worker = self._worker
        if worker is None:
            return
        reply = QMessageBox.question(
            self,
            t("main.confirm_download_title"),
            t("main.confirm_download_msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        worker.set_download_confirmed(reply == QMessageBox.StandardButton.Yes)

    # ── VoiceToText 界面翻转 ──

    def _on_show_voice_to_text(self) -> None:
        if self._voice_widget is None:
            from src.ui.voice_to_text_widget import VoiceToTextWidget
            self._voice_widget = VoiceToTextWidget(self.settings, self)
            self._voice_layout.addWidget(self._voice_widget)
        self.main_panel.hide()
        self.voice_panel.show()
        self._voice_widget.load_model_async()

    def _on_back_to_main(self) -> None:
        self.voice_panel.hide()
        self.main_panel.show()

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
            msg = t("main.pipeline_done_tx", ok=self._tx_success, fail=self._tx_fail)
        elif self._current_mode == "summarize":
            msg = t("main.pipeline_done_sum", ok=self._sum_success, fail=self._sum_fail)
        elif self._current_mode == "pipeline":
            msg = t("main.pipeline_done_both", tx_ok=self._tx_success, tx_fail=self._tx_fail, sum_ok=self._sum_success, sum_fail=self._sum_fail)
        else:
            msg = t("main.pipeline_done_all")

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
            "transcribe": t("main.log_mode_transcribe"),
            "summarize": t("main.log_mode_summarize"),
            "pipeline": t("main.log_mode_pipeline"),
        }
        mode_label = mode_map.get(self._current_mode, t("main.mode_label_task"))
        try:
            with open(fail_path, "a", encoding="utf-8") as f:
                f.write(t("main.fail_log_mode", time=timestamp, mode=mode_label))
                for video_name, stage, error_msg in self._fail_records:
                    f.write(t("main.fail_log_line", stage=stage, name=video_name, msg=error_msg))
                f.write("\n")
        except OSError as exc:
            get_logger("video2text").warning(t("main.failed_log_write", err=exc))

    # ── result file viewer ──

    def _open_result_viewer(self) -> None:
        if not self._completed_names and not self._history_loaded:
            QMessageBox.warning(self, t("common.hint"), t("main.no_video_dialog"))
            return

        output_dir = self.output_combo.currentText().strip() or self._default_output_dir
        video_files = list(self._completed_names)

        if self._result_viewer is None or not self._result_viewer.isVisible():
            self._result_viewer = ResultViewerWindow()

        # 先强制创建原生 HWND（窗口仍隐藏），使 Windows 使用 resize(1400,900) 的几何信息
        # 而非 CW_USEDEFAULT 默认小尺寸；再加载内容；最后 showMaximized() 直接在已有 HWND
        # 上调用 ShowWindow(SW_SHOWMAXIMIZED)，窗口一出场即最大化，消除"小窗口先闪"。
        self._result_viewer.winId()
        self._result_viewer.load_files(
            video_files, output_dir, folder_mode=self._mirror_subdirs
        )
        self._result_viewer.showMaximized()
        self._result_viewer.raise_()
        self._result_viewer.activateWindow()

    def _show_file_context_menu(self, pos) -> None:
        item = self.file_list.itemAt(pos)
        if item is None:
            return
        video_name = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        retranscribe_action = menu.addAction(t("main.file_context_retranscribe"))
        resummarize_action = menu.addAction(t("main.file_context_resummarize"))
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
            QMessageBox.warning(self, t("common.hint"), t("main.task_running_warning"))
            return

        video_path = self._find_video_path_by_name(video_name)
        if video_path is None:
            QMessageBox.warning(
                self,
                t("common.hint"),
                t("main.remove_file_dialog", name=video_name),
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
            initial_prompt=self.initial_prompt_edit.toPlainText().strip(),
            hotwords=self.hotwords_edit.toPlainText().strip(),
        )
        worker.video_done.connect(self._on_single_video_transcribed)
        worker.video_error.connect(self._on_transcribe_error)
        worker.progress.connect(self._on_progress)
        worker.error.connect(self._on_worker_error)
        worker.confirm_download.connect(self._on_confirm_download)
        self._start_worker(thread, worker)

    def _on_resummarize(self, video_name: str) -> None:
        if self._worker_thread is not None and self._worker_thread.isRunning():
            QMessageBox.warning(self, t("common.hint"), t("main.task_running_warning"))
            return

        output_dir = self._get_output_dir()
        resolved_dir = self._resolve_video_output_dir(video_name)
        transcript_path = FileWriter(resolved_dir).find_transcript_file(video_name)
        if transcript_path is None:
            QMessageBox.warning(self, t("common.hint"), t("main.no_transcript_file", name=video_name))
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
                self.transcript_view.setPlainText(t("main.read_failure_inline", err=exc))
        else:
            self.transcript_view.setPlainText(t("main.transcript_not_found"))

        summary_path = _find_summary_path(output_dir, video_name)
        if summary_path:
            try:
                self.summary_view.setPlainText(summary_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError) as exc:
                self.summary_view.setPlainText(t("main.read_failure_inline", err=exc))
        else:
            self.summary_view.setPlainText(t("main.summary_not_found_inline"))

        self._search_controller.refresh_if_active()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self._scan_thread.quit()
            self._scan_thread.wait(3000)

        if self._startup_dependency_thread is not None and self._startup_dependency_thread.isRunning():
            if self._startup_dependency_worker is not None:
                self._startup_dependency_worker.cancel()
            self._startup_dependency_thread.quit()
            # 等待 5 秒（含安全余量），超时则强制终止，避免窗口关闭卡顿。
            if not self._startup_dependency_thread.wait(5000):
                self._startup_dependency_thread.terminate()
                self._startup_dependency_thread.wait(2000)
            self._startup_dependency_thread = None
            self._startup_dependency_worker = None

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

        # ⚠ 先清理 VoiceToText 再卸载模型，避免转写线程与模型卸载竞争
        if hasattr(self, "_voice_widget") and self._voice_widget is not None:
            self._voice_widget.cleanup()

        OllamaClient.stop_service()

        # 模型卸载（del WhisperModel）会触发 ctranslate2 的 CUDA 上下文同步释放，
        # 在打包环境下可能耗时数秒。放到 daemon 后台线程执行，避免阻塞窗口关闭，
        # 让界面立即退出；进程结束时操作系统会兜底回收剩余资源。
        cached = list(_transcriber_cache.values())
        _transcriber_cache.clear()

        def _async_unload():
            for cached_transcriber in cached:
                try:
                    cached_transcriber.unload_model()
                except Exception:
                    pass

        import threading

        unload_thread = threading.Thread(target=_async_unload, daemon=True)
        unload_thread.start()

        if self._result_viewer is not None:
            self._result_viewer.close()

        event.accept()

    # ─── 背景图片 ────────────────────────────────────────────

    def _load_bg_settings(self) -> None:
        """从配置加载背景图片设置"""
        try:
            path = self.settings.get("app.main_image_path", "")
            if path:
                p = Path(path)
                if not p.is_absolute():
                    from src.utils.paths import get_base_dir as _get_base_dir
                    p = _get_base_dir() / path
                if p.exists():
                    self._bg_pixmap = QPixmap(str(p))
                    self._bg_image_path = str(p)
                else:
                    self._bg_pixmap = None
                    self._bg_image_path = ""
            else:
                self._bg_pixmap = None
                self._bg_image_path = ""

            opacity_int = self.settings.get_int(
                "app.main_transparency", 100
            )
            self._bg_opacity = max(0.0, min(1.0, opacity_int / 255.0))
        except Exception:
            self._bg_pixmap = None
            self._bg_image_path = ""
            self._bg_opacity = 0.4

        if hasattr(self, "_bg_content"):
            self._bg_content.set_bg_pixmap(self._bg_pixmap)
            self._bg_content.set_bg_opacity(self._bg_opacity)
        self._apply_bg_transparency()

    def _save_bg_config(self) -> None:
        """保存背景图片配置到 config.ini"""
        try:
            if self._bg_image_path:
                p = Path(self._bg_image_path)
                from src.utils.paths import get_base_dir as _get_base_dir
                base = _get_base_dir()
                try:
                    rel = p.relative_to(base)
                    self.settings.set("app.main_image_path", str(rel))
                except ValueError:
                    self.settings.set("app.main_image_path", str(p))
            else:
                self.settings.set("app.main_image_path", "")

            opacity_int = round(self._bg_opacity * 255)
            self.settings.set("app.main_transparency", str(opacity_int))
            self.settings.save()
        except Exception as e:
            logger.warning(t("main.config_save_fail_warn", err=e))

    def _change_bg_image(self) -> None:
        """通过资源管理器选择并更换背景图片"""
        initial_dir = (
            self._bg_image_path if self._bg_image_path else str(Path.cwd())
        )
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            t("main.bg_pick_title"),
            initial_dir,
            t("main.bg_pick_filter"),
        )
        if not file_path:
            return

        pixmap = QPixmap(file_path)
        if pixmap.isNull():
            QMessageBox.warning(self, t("common.hint"), t("main.bg_load_fail"))
            return

        self._bg_pixmap = pixmap
        self._bg_image_path = file_path
        if hasattr(self, "_bg_content"):
            self._bg_content.set_bg_pixmap(self._bg_pixmap)
        self._apply_bg_transparency()
        self._save_bg_config()
        self.status_bar.showMessage(t("main.bg_changed", name=Path(file_path).name))

    def _clear_bg_image(self) -> None:
        """清除背景图片"""
        self._bg_pixmap = None
        self._bg_image_path = ""
        if hasattr(self, "_bg_content"):
            self._bg_content.set_bg_pixmap(None)
        self._apply_bg_transparency()
        self._save_bg_config()
        self.status_bar.showMessage(t("main.bg_cleared"))

    def _adjust_bg_transparency(self) -> None:
        """弹出输入框修改背景不透明度 (0~255)"""
        current_val = round(self._bg_opacity * 255)
        value, ok = QInputDialog.getInt(
            self,
            t("main.bg_transparency_title"),
            t("main.bg_transparency_label"),
            current_val,
            0,
            255,
            1,
        )
        if ok:
            self._bg_opacity = max(0.0, min(1.0, value / 255.0))
            if hasattr(self, "_bg_content"):
                self._bg_content.set_bg_opacity(self._bg_opacity)
            self._save_bg_config()
            self.status_bar.showMessage(t("main.bg_transparency_set", value=value))

    def _apply_bg_transparency(self) -> None:
        """有背景图片时设置面板透明，否则恢复默认样式"""
        has_bg = (
            self._bg_pixmap is not None
            and not self._bg_pixmap.isNull()
        )
        for w in (self.main_panel, self.voice_panel):
            if has_bg:
                w.setStyleSheet("""
                    QWidget { background: transparent; }
                    QGroupBox { border: 1px solid palette(mid); border-radius: 4px; margin-top: 8px; }
                    QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
                    QComboBox { background: transparent; color: palette(text); border: 1px solid palette(mid); border-radius: 3px; padding: 2px 4px; }
                    QComboBox::drop-down { border-left: 1px solid palette(mid); width: 24px; }
                    QComboBox::down-arrow {
                        image: url(assets/arrow_down.png);
                        width: 20px; height: 20px;
                    }
                    QComboBox QAbstractItemView {
                        background: palette(window);
                        color: palette(text);
                        border: 1px solid palette(mid);
                        selection-background-color: palette(highlight);
                        selection-color: palette(highlighted-text);
                        outline: none;
                    }
                    QMenu {
                        background: palette(window);
                        color: palette(text);
                        border: 1px solid palette(mid);
                    }
                    QMenu::item:selected {
                        background: palette(highlight);
                        color: palette(highlighted-text);
                    }
                    QLineEdit { background: transparent; border: 1px solid palette(mid); border-radius: 3px; padding: 2px 4px; }
                    QTextEdit { background: transparent; border: 1px solid palette(mid); border-radius: 3px; }
                    QListWidget { background: transparent; border: 1px solid palette(mid); border-radius: 3px; }
                    QPushButton { border: 1px solid palette(mid); border-radius: 3px; padding: 4px 12px; }
                    QPushButton:hover { background: rgba(128, 128, 128, 30); border-color: palette(highlight); }
                    QCheckBox { spacing: 6px; border: 1px solid palette(mid); border-radius: 3px; padding: 2px 4px; background: transparent; }
                    QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid palette(mid); border-radius: 2px; background: transparent; }
                    QCheckBox::indicator:checked { background: palette(highlight); }
                    QProgressBar { border: 1px solid palette(mid); border-radius: 3px; text-align: center; background: transparent; }
                    QProgressBar::chunk { background: palette(highlight); border-radius: 2px; }
                    QSplitter::handle { background: palette(mid); width: 1px; }
                """)
                self.result_tabs.setStyleSheet("""
                    QTabBar::tab {
                        border: 1px solid palette(mid);
                        padding: 4px 12px;
                        margin-right: 2px;
                        background: transparent;
                    }
                    QTabBar::tab:selected {
                        background: rgba(128, 128, 128, 30);
                        border-bottom-color: palette(highlight);
                    }
                    QTabBar::tab:!selected {
                        background: rgba(128, 128, 128, 20);
                    }
                """)
            else:
                w.setStyleSheet("")
                self.result_tabs.setStyleSheet("")


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

        # ── 国际化：解析并应用语言（须在构建窗口前完成）──
        import os

        cli_lang = None
        for i, a in enumerate(sys.argv[1:]):
            if a == "--lang" and i + 1 < len(sys.argv[1:]):
                cli_lang = sys.argv[1:][i + 1]
            elif a.startswith("--lang="):
                cli_lang = a.split("=", 1)[1]
        lang = resolve_language(cli_lang)
        set_lang(lang)
        install_qt_translator(app, lang)

        window = MainWindow()
        window.show()
        app.exec()
    finally:
        _crash_log.close()


if __name__ == "__main__":
    main()
