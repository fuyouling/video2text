"""Video2Text GUI —— 基于 PySide6 的视频转文本图形界面"""

import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer
from PySide6.QtGui import QFont, QIcon, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
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

from src.config.settings import Settings
from src.preprocessing.video_processor import VideoProcessor
from src.storage.file_writer import FileWriter
from src.storage.output_formatter import OutputFormatter
from src.summarization.summarizer import Summarizer
from src.text_processing.segment_merger import SegmentMerger
from src.text_processing.text_cleaner import TextCleaner
from src.transcription.transcriber import Transcriber
from src.ui.result_viewer import ResultViewerWindow
from src.utils.exceptions import Video2TextError
from src.utils.logger import get_logger, setup_logger

SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm"}
SUPPORTED_TRANSCRIPT_FORMATS = {"txt", "srt", "vtt", "json"}

if getattr(sys, "frozen", False):
    _PROJECT_ROOT = Path(sys.executable).parent
else:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_OUTPUT_DIR = str(_PROJECT_ROOT / "output")

_BTN_MIN_WIDTH = 100

if sys.platform == "win32":
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    CREATE_NO_WINDOW = 0


class UiLogSignal(QObject):
    message = Signal(str)


class UiLogHandler(logging.Handler):
    def __init__(self, signal: UiLogSignal) -> None:
        super().__init__()
        self._signal = signal
        self.setFormatter(
            logging.Formatter(
                # "%(asctime)s - %(levelname)s - %(message)s",
                "%(message)s",
                # datefmt="%H:%M:%S",
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        self._signal.message.emit(self.format(record))


class TranscribeWorker(QObject):
    """后台转写线程"""

    progress = Signal(int, int)
    finished = Signal(list, str)

    def __init__(
        self,
        video_files: list[str],
        output_dir: str,
        settings: Settings,
        ui_handler: UiLogHandler,
    ) -> None:
        super().__init__()
        self.video_files = video_files
        self.output_dir = output_dir
        self.settings = settings
        self.ui_handler = ui_handler
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _download_progress(self, downloaded: int, total: int) -> None:
        if total > 0:
            pct = (downloaded / total) * 100
            logger = get_logger("video2text")
            logger.info(
                f"模型下载进度: {pct:.1f}% ({downloaded // (1024 * 1024)}MB / {total // (1024 * 1024)}MB)"
            )

    def run(self) -> None:
        logger = setup_logger(
            "video2text",
            log_dir=self.settings.get("paths.logs_dir", "logs"),
            level=self.settings.get("app.log_level", "INFO"),
            log_to_console=False,
        )
        # logger.addHandler(self.ui_handler)

        language = self.settings.get("transcription.language", "zh")
        model_name = self.settings.get("transcription.model_path", "large-v3")
        models_dir = self.settings.get("paths.models_dir", "models")
        model_path_obj = Path(models_dir) / model_name
        model_path = str(model_path_obj) if model_path_obj.exists() else model_name
        device = self.settings.get("transcription.device", "auto")
        compute_type = self.settings.get("transcription.compute_type", "float16")
        beam_size = self.settings.get_int("transcription.beam_size", 5)
        temperature = self.settings.get_float("transcription.temperature", 0.0)
        max_chunk_duration = self.settings.get_int(
            "preprocessing.max_chunk_duration", 300
        )
        output_formats = self._get_output_formats()

        try:
            logger.info("正在加载转写模型...")
            transcriber = Transcriber(
                model_path=model_path,
                device=device,
                compute_type=compute_type,
                num_workers=self.settings.get_int("transcription.num_workers", 1),
            )
            transcriber.load_model(progress_callback=self._download_progress)
            logger.info("转写模型加载完成")
        except Exception:
            logger.exception("加载转写模型失败")
            self.finished.emit([], "")
            return

        total = len(self.video_files)
        ffmpeg_path = self.settings.get("preprocessing.ffmpeg_path", "ffmpeg")

        for idx, video_path in enumerate(self.video_files):
            if self._cancelled:
                break

            video_name = Path(video_path).stem
            logger.info("开始转写 (%d/%d): %s", idx + 1, total, video_name)

            temp_audio = Path(self.output_dir) / f"temp_{video_name}.wav"
            try:
                vp = VideoProcessor(ffmpeg_path=ffmpeg_path)
                vp.validate_video(video_path)
                video_info = vp.get_video_info(video_path)
                logger.info("视频时长: %.2f 秒", video_info.duration)

                vp.extract_audio(
                    video_path,
                    str(temp_audio),
                    sample_rate=self.settings.get_int(
                        "preprocessing.audio_sample_rate", 16000
                    ),
                    channels=self.settings.get_int("preprocessing.audio_channels", 1),
                )

                if video_info.duration > max_chunk_duration:
                    segments = self._transcribe_chunked(
                        transcriber,
                        vp,
                        temp_audio,
                        language,
                        beam_size,
                        temperature,
                        max_chunk_duration,
                    )
                else:
                    segments = transcriber.transcribe(
                        str(temp_audio),
                        language=language,
                        beam_size=beam_size,
                        temperature=temperature,
                        vad_filter=self.settings.get_bool(
                            "transcription.vad_filter", True
                        ),
                    )

                fw = FileWriter(self.output_dir)
                for fmt in output_formats:
                    fw.write_transcript(segments, video_name, format=fmt)

                logger.info("转写完成: %s (%d 段落)", video_name, len(segments))

            except Video2TextError:
                logger.exception("转写失败 %s", video_path)
            except Exception:
                logger.exception("未知错误 %s", video_path)
            finally:
                temp_audio.unlink(missing_ok=True)

            self.progress.emit(idx + 1, total)

        self.finished.emit([], "")

    def _get_output_formats(self) -> list[str]:
        raw = self.settings.get_list("output.transcript_format", ["txt"])
        return [
            f.lower() for f in raw if f.lower() in SUPPORTED_TRANSCRIPT_FORMATS
        ] or ["txt"]

    def _transcribe_chunked(
        self,
        transcriber,
        vp,
        audio_path,
        language,
        beam_size,
        temperature,
        max_chunk_duration,
    ) -> list:
        logger = get_logger("video2text")
        chunk_dir = Path(tempfile.mkdtemp(prefix="audio_chunks_", dir=self.output_dir))
        try:
            split_cmd = [
                vp.ffmpeg_path,
                "-i",
                str(audio_path),
                "-f",
                "segment",
                "-segment_time",
                str(max_chunk_duration),
                "-acodec",
                "pcm_s16le",
                "-reset_timestamps",
                "1",
                str(chunk_dir / "chunk_%03d.wav"),
            ]
            subprocess.run(
                split_cmd,
                capture_output=True,
                text=True,
                check=True,
                creationflags=CREATE_NO_WINDOW,
                encoding="utf-8",
                errors="ignore",
            )
            chunk_files = sorted(chunk_dir.glob("chunk_*.wav"))
            all_segments: list = []
            offset = 0.0
            for i, cp in enumerate(chunk_files):
                if self._cancelled:
                    break
                cs = transcriber.transcribe(
                    str(cp),
                    language=language,
                    beam_size=beam_size,
                    temperature=temperature,
                    vad_filter=self.settings.get_bool("transcription.vad_filter", True),
                )
                for seg in cs:
                    seg.start += offset
                    seg.end += offset
                if cs:
                    offset += max(s.end for s in cs)
                all_segments.extend(cs)
                logger.info("转写块 %d/%d", i + 1, len(chunk_files))
            return all_segments
        finally:
            shutil.rmtree(chunk_dir, ignore_errors=True)


class SummarizeWorker(QObject):
    """后台总结线程 —— 对传入文本调用 Ollama 进行摘要"""

    finished = Signal(str)
    progress = Signal(str)

    def __init__(
        self,
        input_text: str,
        output_dir: str,
        output_name: str,
        settings: Settings,
        ui_handler: UiLogHandler,
        custom_prompt: str = "",
    ) -> None:
        super().__init__()
        self.input_text = input_text
        self.output_dir = output_dir
        self.output_name = output_name
        self.settings = settings
        self.ui_handler = ui_handler
        self.custom_prompt = custom_prompt
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        logger = setup_logger(
            "video2text",
            log_dir=self.settings.get("paths.logs_dir", "logs"),
            level=self.settings.get("app.log_level", "INFO"),
            log_to_console=False,
        )
        # logger.addHandler(self.ui_handler)

        model = self.settings.get(
            "summarization.model_name", "qwen2.5:7b-instruct-q4_K_M"
        )
        url = self.settings.get("summarization.ollama_url", "http://127.0.0.1:11434")
        max_len = self.settings.get_int("summarization.max_length", 500)
        temp = self.settings.get_float("summarization.temperature", 0.7)

        try:
            summarizer = Summarizer(
                model_name=model,
                ollama_url=url,
                temperature=temp,
                max_length=max_len,
            )
            if not summarizer.check_connection():
                logger.error("无法连接到 Ollama 服务 (%s)", url)
                self.finished.emit("")
                return
            if not summarizer.check_model():
                logger.error("Ollama 模型 %s 不存在", model)
                self.finished.emit("")
                return

            self.progress.emit("正在生成摘要...")
            summary = summarizer.summarize(
                self.input_text,
                max_length=max_len,
                custom_prompt=self.custom_prompt,
            )
            if summary:
                fw = FileWriter(self.output_dir)
                fw.write_summary(summary, self.output_name)
                logger.info("总结完成: %s", self.output_name)
            self.finished.emit(summary)
        except Exception:
            logger.exception("总结失败")
            self.finished.emit("")


class BatchSummarizeWorker(QObject):
    """批量总结线程 —— 对多个视频的转写文本分别生成摘要"""

    progress = Signal(int, int)
    finished = Signal()

    def __init__(
        self,
        video_files: list[str],
        output_dir: str,
        settings: Settings,
        ui_handler: UiLogHandler,
        custom_prompt: str = "",
    ) -> None:
        super().__init__()
        self.video_files = video_files
        self.output_dir = output_dir
        self.settings = settings
        self.ui_handler = ui_handler
        self.custom_prompt = custom_prompt
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        logger = setup_logger(
            "video2text",
            log_dir=self.settings.get("paths.logs_dir", "logs"),
            level=self.settings.get("app.log_level", "INFO"),
            log_to_console=False,
        )
        # logger.addHandler(self.ui_handler)

        model = self.settings.get(
            "summarization.model_name", "qwen2.5:7b-instruct-q4_K_M"
        )
        url = self.settings.get("summarization.ollama_url", "http://127.0.0.1:11434")
        max_len = self.settings.get_int("summarization.max_length", 500)
        temp = self.settings.get_float("summarization.temperature", 0.7)

        try:
            summarizer = Summarizer(
                model_name=model,
                ollama_url=url,
                temperature=temp,
                max_length=max_len,
            )
            if not summarizer.check_connection():
                logger.error("无法连接到 Ollama 服务 (%s)", url)
                self.finished.emit()
                return
            if not summarizer.check_model():
                logger.error("Ollama 模型 %s 不存在", model)
                self.finished.emit()
                return
        except Exception:
            logger.exception("初始化总结器失败")
            self.finished.emit()
            return

        total = len(self.video_files)
        for idx, video_path in enumerate(self.video_files):
            if self._cancelled:
                break

            video_name = Path(video_path).stem
            transcript_path = Path(self.output_dir) / f"{video_name}.txt"

            if not transcript_path.exists():
                logger.warning("未找到转写文件: %s", video_name)
                self.progress.emit(idx + 1, total)
                continue

            try:
                text = transcript_path.read_text(encoding="utf-8")
                if not text.strip():
                    logger.warning("转写文件为空: %s", video_name)
                    self.progress.emit(idx + 1, total)
                    continue

                logger.info("开始总结 (%d/%d): %s", idx + 1, total, video_name)
                summary = summarizer.summarize(
                    text,
                    max_length=max_len,
                    custom_prompt=self.custom_prompt,
                )
                if summary:
                    fw = FileWriter(self.output_dir)
                    fw.write_summary(summary, video_name)
                    logger.info("总结完成: %s", video_name)
                else:
                    logger.warning("总结失败: %s", video_name)
            except Exception:
                logger.exception("总结失败: %s", video_name)

            self.progress.emit(idx + 1, total)

        self.finished.emit()


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
        self.file_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
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


class MainWindow(QMainWindow):
    """Video2Text 主窗口"""

    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings()
        self._video_files: list[str] = []
        self._completed_names: set[str] = set()
        self._worker_thread: QThread | None = None
        self._worker: QObject | None = None
        self._combined = False
        self._result_viewer: ResultViewerWindow | None = None

        self._setup_logging()
        self._init_ui()
        self._load_ollama_config()

    def _setup_logging(self) -> None:
        self._log_signal = UiLogSignal()
        self._log_signal.message.connect(self._append_log)
        self._ui_handler = UiLogHandler(self._log_signal)

        # 全局安装 GUI 日志输出
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)  # 根据需要调整级别
        # 避免重复添加（例如窗口重建时）
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
        self.combine_btn.clicked.connect(self._on_transcribe_combine)
        run_row.addWidget(self.combine_btn)
        # self.cancel_btn = QPushButton("取消")
        # self.cancel_btn.setMinimumWidth(_BTN_MIN_WIDTH)
        # self.cancel_btn.setEnabled(False)
        # self.cancel_btn.clicked.connect(self._on_cancel)
        # run_row.addWidget(self.cancel_btn)
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

        # 文件列表和内容区域
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
        self.ollama_model_edit = QLineEdit()
        self.ollama_model_edit.setPlaceholderText("qwen2.5:7b-instruct-q4_K_M")
        ollama_layout.addRow("模型名称:", self.ollama_model_edit)
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
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, 1)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage(f"配置: {self.settings.config_path}")

    def _load_ollama_config(self) -> None:
        url = self.settings.get("summarization.ollama_url", "http://127.0.0.1:11434")
        model = self.settings.get(
            "summarization.model_name", "qwen2.5:7b-instruct-q4_K_M"
        )
        temp = self.settings.get_float("summarization.temperature", 0.7)
        max_len = self.settings.get_int("summarization.max_length", 500)
        prompt = self.settings.get("summarization.custom_prompt", "")
        self.ollama_url_edit.setText(url)
        self.ollama_model_edit.setText(model)
        self.ollama_temp_spin.setValue(temp)
        self.ollama_maxlen_spin.setValue(max_len)
        self.ollama_prompt_edit.setPlainText(prompt)

    def _save_ollama_config(self) -> None:
        self.settings.set("summarization.ollama_url", self.ollama_url_edit.text())
        self.settings.set("summarization.model_name", self.ollama_model_edit.text())
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

    def _test_ollama(self) -> None:
        url = self.ollama_url_edit.text() or "http://127.0.0.1:11434"
        model = self.ollama_model_edit.text() or "qwen2.5:7b-instruct-q4_K_M"
        from src.summarization.ollama_client import OllamaClient

        client = OllamaClient(base_url=url)
        if client.check_connection():
            self.ollama_status_label.setText("连接成功")
            self.ollama_status_label.setStyleSheet("color: green")
        else:
            self.ollama_status_label.setText("连接失败")
            self.ollama_status_label.setStyleSheet("color: red")

    def _start_ollama_service(self) -> None:
        import shutil

        logger = get_logger("video2text")

        ollama_path = shutil.which("ollama")
        if not ollama_path:
            self.ollama_status_label.setText("未找到ollama命令")
            self.ollama_status_label.setStyleSheet("color: red")
            logger.error("未找到ollama命令，请确保已安装Ollama")
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
            self.ollama_status_label.setText("服务启动中...")
            self.ollama_status_label.setStyleSheet("color: orange")

            QTimer.singleShot(2000, self._check_ollama_after_start)

        except Exception as e:
            logger.error(f"启动Ollama服务失败: {e}")
            self.ollama_status_label.setText(f"启动失败: {e}")
            self.ollama_status_label.setStyleSheet("color: red")
            QMessageBox.critical(self, "错误", f"启动Ollama服务失败: {e}")

    def _check_ollama_after_start(self) -> None:
        from src.summarization.ollama_client import OllamaClient

        logger = get_logger("video2text")

        url = self.ollama_url_edit.text() or "http://127.0.0.1:11434"
        client = OllamaClient(base_url=url)

        if client.check_connection():
            self.ollama_status_label.setText("服务已启动")
            self.ollama_status_label.setStyleSheet("color: green")
            logger.info("Ollama服务启动成功")
        else:
            self.ollama_status_label.setText("服务启动中，请稍后测试")
            self.ollama_status_label.setStyleSheet("color: orange")
            logger.warning("Ollama服务可能需要更多时间启动")

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
            "视频文件 (*.mp4 *.avi *.mov *.mkv *.flv *.wmv *.webm);;所有文件 (*.*)",
        )
        if path:
            self.input_edit.setText(path)
            self._video_files = [path]

    def _select_input_multiple_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择视频文件",
            "",
            "视频文件 (*.mp4 *.avi *.mov *.mkv *.flv *.wmv *.webm);;所有文件 (*.*)",
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
            if dialog.exec() == QDialog.DialogCode.Accepted:
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
        summary_files = sorted(output_path.glob("*_summary.txt"))

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
            QMessageBox.information(self, "提示", "输出目录中未找到历史转写文件")

    # ── transcribe / summarize / cancel ──

    def _on_transcribe_combine(self) -> None:
        if not self._video_files:
            QMessageBox.warning(self, "提示", "请先选择输入视频文件或文件夹。")
            return
        self._combined = True
        self._on_transcribe()

    def _on_transcribe(self) -> None:
        if not self._video_files:
            QMessageBox.warning(self, "提示", "请先选择输入视频文件或文件夹。")
            return

        output_dir = self.output_edit.text().strip() or _DEFAULT_OUTPUT_DIR
        self.output_edit.setText(output_dir)
        Path(output_dir).mkdir(parents=True, exist_ok=True)

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

        self._worker_thread = QThread()
        self._worker = TranscribeWorker(
            self._video_files, output_dir, self.settings, self._ui_handler
        )
        self._worker.moveToThread(self._worker_thread)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_transcribe_done)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker_thread.started.connect(self._worker.run)
        self._worker_thread.finished.connect(self._worker.deleteLater)
        self._worker_thread.finished.connect(self._on_thread_finished)
        self._worker_thread.start()

    def _on_summarize(self) -> None:
        if not self._video_files:
            QMessageBox.warning(
                self,
                "提示",
                "请先选择视频文件或文件夹，并完成转写后再进行总结。",
            )
            return

        output_dir = self.output_edit.text().strip() or _DEFAULT_OUTPUT_DIR
        self.output_edit.setText(output_dir)
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        total = len(self._video_files)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"0/{total}")

        self._set_busy_state(True)

        custom_prompt = self.ollama_prompt_edit.toPlainText().strip()
        self._worker_thread = QThread()
        self._worker = BatchSummarizeWorker(
            self._video_files,
            output_dir,
            self.settings,
            self._ui_handler,
            custom_prompt,
        )
        self._worker.moveToThread(self._worker_thread)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_batch_summarize_done)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker_thread.started.connect(self._worker.run)
        self._worker_thread.finished.connect(self._worker.deleteLater)
        self._worker_thread.finished.connect(self._on_thread_finished)
        self._worker_thread.start()

    # def _on_cancel(self) -> None:
    #     if self._worker is not None and hasattr(self._worker, "cancel"):
    #         self._worker.cancel()
    #     self.cancel_btn.setEnabled(False)

    def _set_busy_state(self, busy: bool) -> None:
        self.transcribe_btn.setEnabled(not busy)
        self.summarize_btn.setEnabled(not busy)
        self.combine_btn.setEnabled(not busy)
        # self.cancel_btn.setEnabled(busy)
        self.input_file_btn.setEnabled(not busy)
        self.input_multi_btn.setEnabled(not busy)
        self.input_folder_btn.setEnabled(not busy)
        self.output_btn.setEnabled(not busy)

    # ── progress / completion ──

    def _on_progress(self, completed: int, total: int) -> None:
        self.progress_bar.setValue(completed)
        self.progress_label.setText(f"{completed}/{total}")

    def _on_transcribe_done(self, _segments, _name) -> None:
        output_dir = self.output_edit.text().strip() or _DEFAULT_OUTPUT_DIR
        total = len(self._video_files)
        completed = 0
        for vf in self._video_files:
            vn = Path(vf).stem
            if (Path(output_dir) / f"{vn}.txt").exists():
                completed += 1
                if vn not in self._completed_names:
                    self._completed_names.add(vn)
                    item = QListWidgetItem(vn)
                    item.setData(Qt.ItemDataRole.UserRole, vn)
                    self.file_list.addItem(item)
        self.progress_label.setText(f"{completed}/{total} 完成")

        if self._video_files:
            first_name = Path(self._video_files[0]).stem
            transcript_path = Path(output_dir) / f"{first_name}.txt"
            if transcript_path.exists():
                try:
                    self.transcript_view.setPlainText(
                        transcript_path.read_text(encoding="utf-8")
                    )
                    self.status_bar.showMessage(
                        "转写完成，文本已加载到「文本内容」标签页，可编辑后点击「仅总结」"
                        if not self._combined
                        else "转写完成，即将自动开始总结..."
                    )
                except (OSError, UnicodeDecodeError):
                    pass

    def _on_summarize_done(self, summary: str) -> None:
        if summary:
            self.summary_view.setPlainText(summary)
            self.ollama_status_label.setText("总结完成")
            self.ollama_status_label.setStyleSheet("color: green")
            self.status_bar.showMessage("总结完成，结果在「摘要」标签页")
        else:
            self.ollama_status_label.setText("总结失败，查看日志")
            self.ollama_status_label.setStyleSheet("color: red")
        self.progress_label.setText("就绪")
        self.progress_bar.setMaximum(1)
        self.progress_bar.setValue(1 if summary else 0)

    def _on_batch_summarize_done(self) -> None:
        output_dir = self.output_edit.text().strip() or _DEFAULT_OUTPUT_DIR
        total = len(self._video_files)
        completed = 0
        for vf in self._video_files:
            vn = Path(vf).stem
            summary_path = Path(output_dir) / f"{vn}_summary.txt"
            if summary_path.exists():
                completed += 1
        self.progress_label.setText(f"{completed}/{total} 总结完成")
        self.ollama_status_label.setText(f"总结完成: {completed}/{total}")
        self.ollama_status_label.setStyleSheet("color: green")
        self.status_bar.showMessage("批量总结完成，点击列表中的视频名称可查看摘要")

    def _on_thread_finished(self) -> None:
        self._worker_thread = None
        self._worker = None
        if self._combined:
            self._combined = False
            output_dir = self.output_edit.text().strip() or _DEFAULT_OUTPUT_DIR
            has_transcripts = False
            for vf in self._video_files:
                vn = Path(vf).stem
                transcript_path = Path(output_dir) / f"{vn}.txt"
                if transcript_path.exists():
                    has_transcripts = True
                    break
            if has_transcripts:
                self.status_bar.showMessage("转写完成，自动开始总结...")
                self._on_summarize()
            else:
                self._set_busy_state(False)
                self.status_bar.showMessage("转写完成，但未生成文本内容，无法自动总结")
        else:
            self._set_busy_state(False)

    # ── result file viewer ──

    def _open_result_viewer(self):
        """打开独立结果查看窗口"""
        if not self._completed_names:
            QMessageBox.warning(self, "提示", "请先完成转写或加载历史文件")
            return

        output_dir = self.output_edit.text().strip() or _DEFAULT_OUTPUT_DIR
        video_names = list(self._completed_names)

        # 如果窗口已存在，直接更新内容
        if self._result_viewer is None or not self._result_viewer.isVisible():
            self._result_viewer = ResultViewerWindow(self)

        self._result_viewer.load_files(video_names, output_dir)
        self._result_viewer.show()
        self._result_viewer.raise_()
        self._result_viewer.activateWindow()

    def _on_file_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
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
