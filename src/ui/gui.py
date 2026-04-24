"""Video2Text GUI —— 基于 PySide6 的视频转文本图形界面"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
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
    QSplitter,
    QTabWidget,
    QTextEdit,
    QToolButton,
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
from src.utils.exceptions import Video2TextError
from src.utils.logger import get_logger, setup_logger

SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm"}
SUPPORTED_TRANSCRIPT_FORMATS = {"txt", "srt", "vtt", "json"}

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_OUTPUT_DIR = str(_PROJECT_ROOT / "output")


class UiLogSignal(QObject):
    message = Signal(str)


class UiLogHandler(logging.Handler):
    """将日志记录转发到 GUI QTextEdit 的自定义 Handler"""

    def __init__(self, signal: UiLogSignal) -> None:
        super().__init__()
        self._signal = signal
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(levelname)s - %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        self._signal.message.emit(self.format(record))


class PipelineWorker(QObject):
    """后台工作线程，遍历选中视频并依次执行 转写→文本处理→总结 全流程"""

    progress = Signal(int, int)
    file_completed = Signal(str)
    all_done = Signal()
    model_loaded = Signal()

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

    def run(self) -> None:  # pylint: disable=too-many-locals
        logger = setup_logger(
            "video2text",
            log_dir=self.settings.get("paths.logs_dir", "logs"),
            level=self.settings.get("app.log_level", "INFO"),
            log_to_console=False,
        )
        logger.addHandler(self.ui_handler)

        language = self.settings.get("transcription.language", "zh")
        model_name = self.settings.get("transcription.model_path", "large-v3")
        models_dir = self.settings.get("paths.models_dir", "models")
        model_path_obj = Path(models_dir) / model_name
        model_path = str(model_path_obj) if model_path_obj.exists() else model_name

        device = self.settings.get("transcription.device", "auto")
        compute_type = self.settings.get("transcription.compute_type", "float16")
        num_workers = self.settings.get_int("transcription.num_workers", 1)
        beam_size = self.settings.get_int("transcription.beam_size", 5)
        temperature = self.settings.get_float("transcription.temperature", 0.0)
        max_chunk_duration = self.settings.get_int(
            "preprocessing.max_chunk_duration", 300
        )

        summarization_model = self.settings.get(
            "summarization.model_name", "qwen2.5:7b-instruct-q4_K_M"
        )
        ollama_url = self.settings.get(
            "summarization.ollama_url", "http://127.0.0.1:11434"
        )
        summary_max_length = self.settings.get_int("summarization.max_length", 500)
        summary_temperature_value = self.settings.get_float(
            "summarization.temperature", 0.7
        )

        output_formats = self._get_transcript_output_formats()

        try:
            logger.info("正在加载转写模型...")
            transcriber = Transcriber(
                model_path=model_path,
                device=device,
                compute_type=compute_type,
                num_workers=num_workers,
            )
            transcriber.load_model()
            self.model_loaded.emit()
            logger.info("转写模型加载完成")
        except Exception:
            logger.exception("加载转写模型失败")
            self.all_done.emit()
            return

        total = len(self.video_files)
        for idx, video_path in enumerate(self.video_files):
            if self._cancelled:
                logger.info("用户取消了处理")
                break

            video_name = Path(video_path).stem
            logger.info("开始处理 (%d/%d): %s", idx + 1, total, video_name)

            temp_audio = Path(self.output_dir) / f"temp_{video_name}.wav"
            try:
                video_processor = VideoProcessor(
                    ffmpeg_path=self.settings.get("preprocessing.ffmpeg_path", "ffmpeg")
                )
                video_processor.validate_video(video_path)
                video_info = video_processor.get_video_info(video_path)
                logger.info("视频时长: %.2f 秒", video_info.duration)

                video_processor.extract_audio(
                    video_path,
                    str(temp_audio),
                    sample_rate=self.settings.get_int(
                        "preprocessing.audio_sample_rate", 16000
                    ),
                    channels=self.settings.get_int("preprocessing.audio_channels", 1),
                )

                if video_info.duration > max_chunk_duration:
                    segments = self._transcribe_chunked(
                        transcriber=transcriber,
                        video_processor=video_processor,
                        audio_path=temp_audio,
                        language=language,
                        beam_size=beam_size,
                        temperature=temperature,
                        max_chunk_duration=max_chunk_duration,
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

                segment_merger = SegmentMerger(
                    max_gap=self.settings.get_float("text_processing.max_gap", 2.0),
                    min_length=self.settings.get_int("text_processing.min_length", 50),
                )
                merged_segments = segment_merger.merge_segments(segments)
                processed_text = segment_merger.format_segments_as_text(
                    merged_segments, include_timestamps=False
                )

                summarizer = Summarizer(
                    model_name=summarization_model,
                    ollama_url=ollama_url,
                    temperature=summary_temperature_value,
                    max_length=summary_max_length,
                )
                if summarizer.check_connection() and summarizer.check_model():
                    summary = summarizer.summarize(
                        processed_text, max_length=summary_max_length
                    )
                else:
                    logger.warning("Ollama 服务不可用，跳过总结")
                    summary = "总结不可用"

                file_writer = FileWriter(self.output_dir)
                for fmt in output_formats:
                    file_writer.write_transcript(segments, video_name, format=fmt)
                file_writer.write_summary(summary, video_name)

                if self.settings.get_bool("output.json_output", False):
                    formatter = OutputFormatter()
                    output_data = formatter.create_output_data(
                        video_name=video_name,
                        video_path=video_path,
                        duration=video_info.duration,
                        transcript_segments=segments,
                        processed_text=processed_text,
                        summary=summary,
                        processing_time=0.0,
                    )
                    file_writer.write_output_data(output_data, video_name)

                self.file_completed.emit(video_name)
                logger.info("处理完成: %s", video_name)

            except Video2TextError:
                logger.exception("处理失败 %s", video_path)
            except Exception:
                logger.exception("未知错误 %s", video_path)
            finally:
                temp_audio.unlink(missing_ok=True)

            self.progress.emit(idx + 1, total)

        self.all_done.emit()

    # ── internal helpers ────────────────────────────────────────────

    def _get_transcript_output_formats(self) -> list[str]:
        raw = self.settings.get_list("output.transcript_format", ["txt"])
        formats = [
            fmt.lower() for fmt in raw if fmt.lower() in SUPPORTED_TRANSCRIPT_FORMATS
        ]
        return formats or ["txt"]

    def _transcribe_chunked(
        self,
        transcriber: Transcriber,
        video_processor: VideoProcessor,
        audio_path: Path,
        language: str,
        beam_size: int,
        temperature: float,
        max_chunk_duration: int,
    ) -> list:
        logger = get_logger("video2text")
        chunk_dir = Path(tempfile.mkdtemp(prefix="audio_chunks_", dir=self.output_dir))
        try:
            split_cmd = [
                video_processor.ffmpeg_path,
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
            subprocess.run(split_cmd, capture_output=True, text=True, check=True)
            chunk_files = sorted(chunk_dir.glob("chunk_*.wav"))
            all_segments: list = []
            cumulative_offset = 0.0
            for i, chunk_path in enumerate(chunk_files):
                if self._cancelled:
                    break
                chunk_segments = transcriber.transcribe(
                    str(chunk_path),
                    language=language,
                    beam_size=beam_size,
                    temperature=temperature,
                    vad_filter=self.settings.get_bool("transcription.vad_filter", True),
                )
                for seg in chunk_segments:
                    seg.start += cumulative_offset
                    seg.end += cumulative_offset
                if chunk_segments:
                    cumulative_offset += max(s.end for s in chunk_segments)
                all_segments.extend(chunk_segments)
                logger.info("转写块 %d/%d", i + 1, len(chunk_files))
            return all_segments
        finally:
            shutil.rmtree(chunk_dir, ignore_errors=True)


class MainWindow(QMainWindow):
    """Video2Text 主窗口"""

    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings()
        self._video_files: list[str] = []
        self._completed_names: set[str] = set()
        self._worker_thread: QThread | None = None
        self._worker: PipelineWorker | None = None

        self._setup_logging()
        self._init_ui()

    # ── logging bridge ──────────────────────────────────────────────

    def _setup_logging(self) -> None:
        self._log_signal = UiLogSignal()
        self._log_signal.message.connect(self._append_log)
        self._ui_handler = UiLogHandler(self._log_signal)

    def _append_log(self, msg: str) -> None:
        self.log_text.append(msg)
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_text.setTextCursor(cursor)

    # ── UI construction ─────────────────────────────────────────────

    def _init_ui(self) -> None:
        self.setWindowTitle("Video2Text - 视频转文本工具")
        self.resize(1200, 800)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # -- input row --
        input_row = QHBoxLayout()
        input_row.addWidget(QLabel("输入:"))
        self.input_edit = QLineEdit()
        self.input_edit.setReadOnly(True)
        self.input_edit.setPlaceholderText("请选择视频文件或文件夹…")
        input_row.addWidget(self.input_edit, 1)

        self.input_btn = QToolButton()
        self.input_btn.setText("浏览")
        self.input_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.input_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        input_menu = QMenu(self.input_btn)
        input_menu.addAction("选择文件", self._select_input_files)
        input_menu.addAction("选择多个文件", self._select_input_multiple_files)
        input_menu.addAction("选择文件夹", self._select_input_folder)
        self.input_btn.setMenu(input_menu)
        self.input_btn.clicked.connect(self._select_input_files)
        input_row.addWidget(self.input_btn)
        root.addLayout(input_row)

        # -- output row --
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("输出:"))
        self.output_edit = QLineEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setText(_DEFAULT_OUTPUT_DIR)
        output_row.addWidget(self.output_edit, 1)
        self.output_btn = QPushButton("浏览")
        self.output_btn.clicked.connect(self._select_output_dir)
        output_row.addWidget(self.output_btn)
        root.addLayout(output_row)

        # -- run + progress row --
        run_row = QHBoxLayout()
        self.progress_label = QLabel("0/0")
        self.progress_label.setMinimumWidth(60)
        run_row.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        run_row.addWidget(self.progress_bar, 1)
        self.run_btn = QPushButton("运行")
        self.run_btn.setMinimumWidth(80)
        self.run_btn.clicked.connect(self._on_run)
        run_row.addWidget(self.run_btn)
        root.addLayout(run_row)

        # -- splitter: logs + results --
        splitter = QSplitter(Qt.Orientation.Vertical)

        # log panel
        log_group = QGroupBox("日志输出")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        log_layout.addWidget(self.log_text)
        splitter.addWidget(log_group)

        # results panel
        results_group = QGroupBox("结果查看")
        results_layout = QHBoxLayout(results_group)
        self.file_list = QListWidget()
        self.file_list.setMinimumWidth(200)
        self.file_list.currentItemChanged.connect(self._on_file_selected)
        results_layout.addWidget(self.file_list, 1)

        self.result_tabs = QTabWidget()
        self.transcript_view = QTextEdit()
        self.transcript_view.setReadOnly(True)
        self.transcript_view.setFont(QFont("Consolas", 9))
        self.summary_view = QTextEdit()
        self.summary_view.setReadOnly(True)
        self.summary_view.setFont(QFont("Consolas", 9))
        self.result_tabs.addTab(self.transcript_view, "转写文本 (.txt)")
        self.result_tabs.addTab(self.summary_view, "摘要 (_summary.txt)")
        results_layout.addWidget(self.result_tabs, 3)
        splitter.addWidget(results_group)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

    # ── slot: input selection ───────────────────────────────────────

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
            self.input_edit.setText(folder)
            self._video_files = self._scan_video_files(folder)

    @staticmethod
    def _scan_video_files(folder: str) -> list[str]:
        folder_path = Path(folder)
        video_files: list[str] = []
        for ext in SUPPORTED_VIDEO_EXTENSIONS:
            for f in folder_path.rglob(f"*{ext}"):
                video_files.append(str(f))
        return sorted(video_files)

    # ── slot: output selection ──────────────────────────────────────

    def _select_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if folder:
            self.output_edit.setText(folder)

    # ── slot: run / cancel ──────────────────────────────────────────

    def _on_run(self) -> None:
        if self._worker_thread is not None and self._worker_thread.isRunning():
            if self._worker is not None:
                self._worker.cancel()
            self.run_btn.setEnabled(False)
            return

        if not self._video_files:
            QMessageBox.warning(self, "提示", "请先选择输入文件或文件夹。")
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

        self.run_btn.setText("取消")

        self._worker_thread = QThread()
        self._worker = PipelineWorker(
            self._video_files, output_dir, self.settings, self._ui_handler
        )
        self._worker.moveToThread(self._worker_thread)

        self._worker.progress.connect(self._on_progress)
        self._worker.file_completed.connect(self._on_file_completed)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.all_done.connect(self._worker_thread.quit)
        self._worker_thread.started.connect(self._worker.run)
        self._worker_thread.finished.connect(self._worker.deleteLater)
        self._worker_thread.finished.connect(self._on_thread_finished)
        self._worker_thread.start()

    # ── slot: progress updates ──────────────────────────────────────

    def _on_progress(self, completed: int, total: int) -> None:
        self.progress_bar.setValue(completed)
        self.progress_label.setText(f"{completed}/{total}")

    def _on_file_completed(self, video_name: str) -> None:
        if video_name not in self._completed_names:
            self._completed_names.add(video_name)
            item = QListWidgetItem(video_name)
            item.setData(Qt.ItemDataRole.UserRole, video_name)
            self.file_list.addItem(item)

    def _on_all_done(self) -> None:
        count = len(self._completed_names)
        total = len(self._video_files)
        self.progress_label.setText(f"{count}/{total} 完成")

    def _on_thread_finished(self) -> None:
        self.run_btn.setText("运行")
        self.run_btn.setEnabled(True)
        self._worker_thread = None
        self._worker = None

    # ── slot: result file viewer ────────────────────────────────────

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

    # ── close event ─────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        if self._worker_thread is not None and self._worker_thread.isRunning():
            if self._worker is not None:
                self._worker.cancel()
            self._worker_thread.quit()
            self._worker_thread.wait(3000)
        event.accept()


def main() -> None:
    app = QApplication()
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
