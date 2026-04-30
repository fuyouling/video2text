"""GUI Worker 线程 —— 使用服务层，支持流式输出、断点续传、单视频即时回调"""

import logging
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal, QThread

from src.config.settings import Settings
from src.preprocessing.video_processor import VideoProcessor
from src.services.transcription_service import TranscriptionService, TranscribeResult
from src.services.summarization_service import SummarizationService
from src.storage.file_writer import FileWriter
from src.text_processing.segment_merger import SegmentMerger
from src.transcription.transcriber import Transcriber
from src.utils.exceptions import Video2TextError
from src.utils.logger import get_logger, setup_logger
from src.utils.validators import validate_executable_path

SUPPORTED_TRANSCRIPT_FORMATS = {"txt", "srt", "vtt", "json"}

if sys.platform == "win32":
    import subprocess

    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    CREATE_NO_WINDOW = 0


def _get_output_formats(settings: Settings) -> list[str]:
    raw = settings.get_list("output.transcript_format", ["txt"])
    return [f.lower() for f in raw if f.lower() in SUPPORTED_TRANSCRIPT_FORMATS] or [
        "txt"
    ]


def _load_tx_config(settings: Settings):
    """从配置加载转写参数，返回 (language, model_path, device, compute_type,
    beam_size, temperature, max_chunk_duration, output_formats, ffmpeg_path)"""
    language = settings.get("transcription.language", "auto")
    model_name = settings.get("transcription.model_path", "large-v3")
    models_dir = settings.get("paths.models_dir", "models")
    model_path_obj = Path(models_dir) / model_name
    model_path = str(model_path_obj) if model_path_obj.exists() else model_name
    device = settings.get("transcription.device", "auto")
    compute_type = settings.get("transcription.compute_type", "float16")
    beam_size = settings.get_int("transcription.beam_size", 5)
    temperature = settings.get_float("transcription.temperature", 0.0)
    max_chunk_duration = settings.get_int("preprocessing.max_chunk_duration", 300)
    output_formats = _get_output_formats(settings)

    ffmpeg_path = settings.get("preprocessing.ffmpeg_path", "ffmpeg")
    try:
        ffmpeg_path = validate_executable_path(ffmpeg_path, "FFmpeg")
    except Exception:
        get_logger(__name__).warning(
            "FFmpeg 路径验证失败，使用原始路径: %s", ffmpeg_path
        )

    return (
        language,
        model_path,
        device,
        compute_type,
        beam_size,
        temperature,
        max_chunk_duration,
        output_formats,
        ffmpeg_path,
    )


class UiLogSignal(QObject):
    message = Signal(str)


class UiLogHandler(logging.Handler):
    def __init__(self, signal: UiLogSignal) -> None:
        super().__init__()
        self._signal = signal
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        self._signal.message.emit(self.format(record))


class TranscribeWorker(QObject):
    """后台转写线程 —— 每完成一个视频立即通过信号通知 GUI"""

    # (video_name, segments_count, output_paths)
    video_done = Signal(str, int, list)
    progress = Signal(int, int)
    finished = Signal()

    def __init__(
        self,
        video_files: list[str],
        output_dir: str,
        settings: Settings,
    ) -> None:
        super().__init__()
        self.video_files = video_files
        self.output_dir = output_dir
        self.settings = settings
        self._cancelled = False
        self._service: Optional[TranscriptionService] = None

    def cancel(self) -> None:
        self._cancelled = True

    def pause(self) -> None:
        if self._service is not None:
            self._service.pause()

    def resume(self) -> None:
        if self._service is not None:
            self._service.resume()

    @property
    def is_paused(self) -> bool:
        return self._service.is_paused if self._service else False

    def run(self) -> None:
        logger = setup_logger(
            "video2text",
            log_dir=self.settings.get("paths.logs_dir", "logs"),
            level=self.settings.get("app.log_level", "INFO"),
            log_to_console=False,
        )

        transcriber = None
        try:
            (
                language,
                model_path,
                device,
                compute_type,
                beam_size,
                temperature,
                max_chunk_duration,
                output_formats,
                ffmpeg_path,
            ) = _load_tx_config(self.settings)

            logger.info("正在加载转写模型...")
            transcriber = Transcriber(
                model_path=model_path,
                device=device,
                compute_type=compute_type,
                num_workers=self.settings.get_int("transcription.num_workers", 1),
            )
            transcriber.load_model()
            logger.info("转写模型加载完成")

            video_processor = VideoProcessor(ffmpeg_path=ffmpeg_path)
            file_writer = FileWriter(self.output_dir)

            total = len(self.video_files)
            done_count = 0

            def on_video_done(result: TranscribeResult):
                nonlocal done_count
                done_count += 1
                self.video_done.emit(
                    result.video_name, len(result.segments), result.output_paths
                )
                self.progress.emit(done_count, total)

            service = TranscriptionService(
                transcriber=transcriber,
                video_processor=video_processor,
                file_writer=file_writer,
                language=language,
                beam_size=beam_size,
                temperature=temperature,
                vad_filter=self.settings.get_bool("transcription.vad_filter", True),
                max_chunk_duration=max_chunk_duration,
                output_formats=output_formats,
                on_video_done=on_video_done,
                cancel_check=lambda: self._cancelled,
            )
            self._service = service

            service.run(self.video_files, self.output_dir)

        except Exception:
            logger.exception("转写线程异常")
        finally:
            self._service = None
            if transcriber is not None:
                transcriber.unload_model()
            self.finished.emit()


class SummarizeWorker(QObject):
    """后台总结线程 —— 支持流式输出"""

    # 流式 token 推送
    stream_token = Signal(str)
    # 总结完成 (video_name, summary)
    video_done = Signal(str, str)
    progress = Signal(int, int)
    finished = Signal()

    def __init__(
        self,
        video_files: list[str],
        output_dir: str,
        settings: Settings,
        custom_prompt: str = "",
        stream: bool = True,
    ) -> None:
        super().__init__()
        self.video_files = video_files
        self.output_dir = output_dir
        self.settings = settings
        self.custom_prompt = custom_prompt
        self.stream = stream
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

        file_writer = FileWriter(self.output_dir)

        service = SummarizationService(
            settings=self.settings,
            file_writer=file_writer,
            custom_prompt=self.custom_prompt,
            on_stream_token=lambda token: self.stream_token.emit(token),
            cancel_check=lambda: self._cancelled,
        )

        if not service.check_connection():
            logger.error("无法连接到 Ollama 服务")
            self.finished.emit()
            return

        if not service.check_model():
            logger.error("Ollama 模型 %s 不存在", service.model_name)
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
                summary = service.summarize(
                    text, video_name=video_name, stream=self.stream
                )
                if summary:
                    self.video_done.emit(video_name, summary)
            except Exception:
                logger.exception("总结失败: %s", video_name)

            self.progress.emit(idx + 1, total)

        self.finished.emit()


class PipelineWorker(QObject):
    """转写+总结管道线程 —— 每完成一个视频的转写就自动开始总结"""

    # 转写完成 (video_name, segments_count, output_paths)
    transcribe_done = Signal(str, int, list)
    # 总结完成 (video_name, summary)
    summarize_done = Signal(str, str)
    # 流式 token
    stream_token = Signal(str)
    progress = Signal(int, int)
    finished = Signal()

    def __init__(
        self,
        video_files: list[str],
        output_dir: str,
        settings: Settings,
        custom_prompt: str = "",
        stream: bool = True,
    ) -> None:
        super().__init__()
        self.video_files = video_files
        self.output_dir = output_dir
        self.settings = settings
        self.custom_prompt = custom_prompt
        self.stream = stream
        self._cancelled = False
        self._tx_service: Optional[TranscriptionService] = None

    def cancel(self) -> None:
        self._cancelled = True

    def pause(self) -> None:
        if self._tx_service is not None:
            self._tx_service.pause()

    def resume(self) -> None:
        if self._tx_service is not None:
            self._tx_service.resume()

    @property
    def is_paused(self) -> bool:
        return self._tx_service.is_paused if self._tx_service else False

    def run(self) -> None:
        logger = setup_logger(
            "video2text",
            log_dir=self.settings.get("paths.logs_dir", "logs"),
            level=self.settings.get("app.log_level", "INFO"),
            log_to_console=False,
        )

        transcriber = None
        try:
            (
                language,
                model_path,
                device,
                compute_type,
                beam_size,
                temperature,
                max_chunk_duration,
                output_formats,
                ffmpeg_path,
            ) = _load_tx_config(self.settings)

            logger.info("正在加载转写模型...")
            transcriber = Transcriber(
                model_path=model_path,
                device=device,
                compute_type=compute_type,
                num_workers=self.settings.get_int("transcription.num_workers", 1),
            )
            transcriber.load_model()
            logger.info("转写模型加载完成")

            video_processor = VideoProcessor(ffmpeg_path=ffmpeg_path)
            file_writer = FileWriter(self.output_dir)
            segment_merger = SegmentMerger(
                max_gap=self.settings.get_float("text_processing.max_gap", 2.0),
                min_length=self.settings.get_int("text_processing.min_length", 50),
            )

            sum_service = SummarizationService(
                settings=self.settings,
                file_writer=file_writer,
                custom_prompt=self.custom_prompt,
                on_stream_token=lambda token: self.stream_token.emit(token),
                cancel_check=lambda: self._cancelled,
            )
            sum_available = sum_service.check_connection() and sum_service.check_model()
            if not sum_available:
                logger.warning("总结服务不可用，将只执行转写")

            total = len(self.video_files)

            def on_tx_done(result: TranscribeResult):
                self.transcribe_done.emit(
                    result.video_name, len(result.segments), result.output_paths
                )

            tx_service = TranscriptionService(
                transcriber=transcriber,
                video_processor=video_processor,
                file_writer=file_writer,
                language=language,
                beam_size=beam_size,
                temperature=temperature,
                vad_filter=self.settings.get_bool("transcription.vad_filter", True),
                max_chunk_duration=max_chunk_duration,
                output_formats=output_formats,
                on_video_done=on_tx_done,
                cancel_check=lambda: self._cancelled,
            )
            self._tx_service = tx_service

            results = tx_service.run(self.video_files, self.output_dir)

            for idx, result in enumerate(results):
                if self._cancelled:
                    break

                if sum_available:
                    merged = segment_merger.merge_segments(result.segments)
                    processed_text = segment_merger.format_segments_as_text(
                        merged, include_timestamps=False
                    )
                    try:
                        summary = sum_service.summarize(
                            processed_text,
                            video_name=result.video_name,
                            stream=self.stream,
                        )
                        if summary:
                            self.summarize_done.emit(result.video_name, summary)
                    except Exception:
                        logger.exception("总结失败: %s", result.video_name)

                self.progress.emit(idx + 1, total)

        except Exception:
            logger.exception("管道线程异常")
        finally:
            self._tx_service = None
            if transcriber is not None:
                transcriber.unload_model()
            self.finished.emit()
