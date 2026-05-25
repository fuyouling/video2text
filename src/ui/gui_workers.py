"""GUI Worker 线程 —— 使用服务层，支持流式输出、断点续传、单文件即时回调"""

import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

from src.config.settings import Settings
from src.preprocessing.video_processor import VideoProcessor
from src.services.transcription_service import TranscriptionService, TranscribeResult
from src.services.summarization_service import SummarizationService
from src.storage.file_writer import FileWriter
from src.summarization.ollama_client import OllamaClient
from src.summarization.providers import create_provider
from src.text_processing.segment_merger import SegmentMerger
from src.text_processing.text_cleaner import TextCleaner
from src.transcription.transcriber import get_cached_transcriber
from src.utils.exceptions import ConfigurationError
from src.utils.logger import get_logger, setup_logger
from src.utils.validators import validate_executable_path

SUPPORTED_TRANSCRIPT_FORMATS = {"txt", "srt", "vtt", "json"}

if sys.platform == "win32":
    import subprocess

    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    CREATE_NO_WINDOW = 0


class RateLimiter:
    """简单的速率限制器 —— 确保请求间隔不低于指定秒数"""

    def __init__(self, min_interval: float = 1.5):
        self._lock = threading.Lock()
        self._min_interval = min_interval
        self._last_time = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_time = time.monotonic()


def _get_output_formats(settings: Settings) -> list[str]:
    raw = settings.get_list("output.transcript_format", ["txt"])
    return [f.lower() for f in raw if f.lower() in SUPPORTED_TRANSCRIPT_FORMATS] or [
        "txt"
    ]


@dataclass
class TranscriptionConfig:
    language: str
    model_path: str
    device: str
    compute_type: str
    beam_size: int
    temperature: float
    max_chunk_duration: int
    output_formats: list[str]
    ffmpeg_path: str


def _load_tx_config(settings: Settings) -> TranscriptionConfig:
    """从配置加载转写参数"""
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
    except ConfigurationError as e:
        if "不安全字符" in str(e):
            raise
        get_logger(__name__).warning(
            "FFmpeg 路径验证失败，使用原始路径: %s", ffmpeg_path
        )

    return TranscriptionConfig(
        language=language,
        model_path=model_path,
        device=device,
        compute_type=compute_type,
        beam_size=beam_size,
        temperature=temperature,
        max_chunk_duration=max_chunk_duration,
        output_formats=output_formats,
        ffmpeg_path=ffmpeg_path,
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
    """后台转写线程 —— 每完成一个文件立即通过信号通知 GUI"""

    # (video_name, segments_count, output_paths)
    video_done = Signal(str, int, list)
    # (video_name, error_message)
    video_error = Signal(str, str)
    progress = Signal(int, int)
    error = Signal(str)
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
        self._service_lock = threading.Lock()

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
        with self._service_lock:
            service = self._service
        return service.is_paused if service else False

    def run(self) -> None:
        logger = setup_logger(
            "video2text",
            log_dir=self.settings.get("paths.logs_dir", "logs"),
            level=self.settings.get("app.log_level", "INFO"),
            log_to_console=False,
        )

        transcriber = None
        try:
            cfg = _load_tx_config(self.settings)

            logger.info("正在加载转写模型...")
            transcriber = get_cached_transcriber(
                model_path=cfg.model_path,
                device=cfg.device,
                compute_type=cfg.compute_type,
                num_workers=self.settings.get_int("transcription.num_workers", 1),
            )
            transcriber.load_model()
            logger.info("转写模型加载完成")

            video_processor = VideoProcessor(ffmpeg_path=cfg.ffmpeg_path)
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

            def on_video_error(video_name: str, error_msg: str):
                nonlocal done_count
                done_count += 1
                self.video_error.emit(video_name, error_msg)
                self.progress.emit(done_count, total)

            service = TranscriptionService(
                transcriber=transcriber,
                video_processor=video_processor,
                file_writer=file_writer,
                language=cfg.language,
                beam_size=cfg.beam_size,
                temperature=cfg.temperature,
                vad_filter=self.settings.get_bool("transcription.vad_filter", True),
                max_chunk_duration=cfg.max_chunk_duration,
                output_formats=cfg.output_formats,
                on_video_done=on_video_done,
                on_video_error=on_video_error,
                cancel_check=lambda: self._cancelled,
            )
            self._service = service

            service.run(self.video_files, self.output_dir)

        except Exception as exc:
            logger.exception("转写线程异常")
            self.error.emit(str(exc))
        finally:
            self._service = None
            self.finished.emit()


class SummarizeWorker(QObject):
    """后台总结线程 —— 支持流式输出和多线程并发"""

    summarize_started = Signal(str)
    stream_token = Signal(str)
    video_done = Signal(str, str)
    video_error = Signal(str, str)
    progress = Signal(int, int)
    error = Signal(str)
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
        self._standalone_text: str = ""

    def set_standalone_text(self, text: str) -> None:
        self._standalone_text = text

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        logger = setup_logger(
            "video2text",
            log_dir=self.settings.get("paths.logs_dir", "logs"),
            level=self.settings.get("app.log_level", "INFO"),
            log_to_console=False,
        )

        try:
            file_writer = FileWriter(self.output_dir)
            provider = self.settings.get("summarization.provider", "ollama")
            nvidia_mode = self.settings.get("summarization.nvidia_mode", "single")

            if provider == "nvidia" and nvidia_mode == "multi":
                self._run_multi_thread(logger, file_writer)
            else:
                self._run_single_thread(logger, file_writer, provider)

        except Exception as exc:
            logger.exception("总结线程异常")
            self.error.emit(str(exc))
        finally:
            self.finished.emit()

    def _run_single_thread(
        self, logger, file_writer: FileWriter, provider: str
    ) -> None:
        """单线程模式（Ollama 或 NVIDIA single）"""
        if provider == "ollama":
            ollama_url = self.settings.get(
                "summarization.ollama_url", "http://127.0.0.1:11434"
            )
            try:
                OllamaClient.ensure_service(ollama_url)
            except RuntimeError as e:
                msg = str(e)
                logger.error(msg)
                self.error.emit(msg)
                self._emit_all_errors(msg)
                return

        provider_inst = create_provider(self.settings)
        try:
            if not provider_inst.check_connection():
                msg = f"{provider} 连接失败"
                logger.error(msg)
                self.error.emit(msg)
                self._emit_all_errors(msg)
                return

            service = SummarizationService(
                settings=self.settings,
                file_writer=file_writer,
                provider=provider_inst,
                custom_prompt=self.custom_prompt,
                on_stream_token=lambda token: self.stream_token.emit(token),
                cancel_check=lambda: self._cancelled,
            )
            self._execute_summarization(logger, service)
        except Exception as e:
            logger.exception("总结流程异常")
            self.error.emit(str(e))
        finally:
            provider_inst.close()

    def _run_multi_thread(self, logger, file_writer: FileWriter) -> None:
        """多线程模式（NVIDIA multi）—— 强制非流式"""
        provider_inst = create_provider(self.settings)
        try:
            if not provider_inst.check_connection():
                msg = "NVIDIA API 连接失败，请检查 API Key 和网络"
                logger.error(msg)
                self.error.emit(msg)
                self._emit_all_errors(msg)
                return
        finally:
            provider_inst.close()

        if self._standalone_text and not self.video_files:
            self._execute_summarization_single_fallback(logger, file_writer)
            return

        self._execute_summarization_multi(logger, file_writer)

    def _execute_summarization_single_fallback(
        self, logger, file_writer: FileWriter
    ) -> None:
        """独立文本在 multi 模式下退化为单线程"""
        provider_inst = create_provider(self.settings)
        try:
            service = SummarizationService(
                settings=self.settings,
                file_writer=file_writer,
                provider=provider_inst,
                custom_prompt=self.custom_prompt,
                cancel_check=lambda: self._cancelled,
            )
            try:
                self.summarize_started.emit("(粘贴文本)")
                summary = service.summarize(
                    self._standalone_text,
                    video_name="",
                    stream=False,
                )
                if summary:
                    self.video_done.emit("(粘贴文本)", summary)
                else:
                    self.video_error.emit("(粘贴文本)", "总结结果为空")
            except Exception as e:
                logger.exception("独立文本总结失败")
                self.video_error.emit("(粘贴文本)", str(e))
            finally:
                service.close()
        except Exception as e:
            logger.exception("总结流程异常")
            self.video_error.emit("(粘贴文本)", str(e))

        self.progress.emit(1, 1)

    def _execute_summarization_multi(self, logger, file_writer: FileWriter) -> None:
        """多线程并发总结"""
        thread_count = self.settings.get_int("summarization.nvidia_thread_count", 3)
        total = len(self.video_files)
        progress_lock = threading.Lock()
        done_count = [0]

        tasks: list[tuple[str, str]] = []
        for video_path in self.video_files:
            video_name = Path(video_path).stem
            transcript_path = None
            for ext in ("txt", "srt", "vtt", "json"):
                candidate = Path(self.output_dir) / f"{video_name}.{ext}"
                if candidate.exists():
                    transcript_path = candidate
                    break

            if transcript_path is None:
                logger.warning("未找到转写文件: %s", video_name)
                self.video_error.emit(video_name, "未找到转写文件")
                with progress_lock:
                    done_count[0] += 1
                self.progress.emit(done_count[0], total)
                continue

            try:
                text = transcript_path.read_text(encoding="utf-8-sig")
                if not text.strip():
                    logger.warning("转写文件为空: %s", video_name)
                    self.video_error.emit(video_name, "转写文件为空")
                    with progress_lock:
                        done_count[0] += 1
                    self.progress.emit(done_count[0], total)
                    continue
                tasks.append((video_name, text))
            except Exception as e:
                logger.exception("读取转写文件失败: %s", video_name)
                self.video_error.emit(video_name, str(e))
                with progress_lock:
                    done_count[0] += 1
                self.progress.emit(done_count[0], total)

        if not tasks:
            return

        rate_limiter = RateLimiter(min_interval=1.5)

        def _process_video(video_name: str, text: str) -> tuple[str, str, str]:
            if self._cancelled:
                return video_name, "", "cancelled"

            rate_limiter.acquire()
            provider = create_provider(self.settings)
            try:
                service = SummarizationService(
                    settings=self.settings,
                    file_writer=file_writer,
                    provider=provider,
                    custom_prompt=self.custom_prompt,
                    cancel_check=lambda: self._cancelled,
                )
                try:
                    summary = service.summarize(
                        text, video_name=video_name, stream=False
                    )
                    return video_name, summary, ""
                finally:
                    service.close()
            except Exception as e:
                logger.exception("总结失败: %s", video_name)
                return video_name, "", str(e)
            finally:
                provider.close()

        with ThreadPoolExecutor(max_workers=thread_count) as executor:
            futures = {}
            for video_name, text in tasks:
                if self._cancelled:
                    break
                self.summarize_started.emit(video_name)
                future = executor.submit(_process_video, video_name, text)
                futures[future] = video_name

            for future in as_completed(futures):
                try:
                    video_name, summary, err = future.result()
                    if err == "cancelled":
                        continue
                    if summary:
                        self.video_done.emit(video_name, summary)
                    else:
                        self.video_error.emit(
                            video_name, err if err else "总结结果为空"
                        )
                except Exception as e:
                    video_name = futures[future]
                    logger.exception("线程异常: %s", video_name)
                    self.video_error.emit(video_name, str(e))

                with progress_lock:
                    done_count[0] += 1
                self.progress.emit(done_count[0], total)

    def _execute_summarization(self, logger, service: SummarizationService) -> None:
        """串行总结逻辑（single 模式和 Ollama）"""
        if self._standalone_text and not self.video_files:
            try:
                self.summarize_started.emit("(粘贴文本)")
                summary = service.summarize(
                    self._standalone_text,
                    video_name="",
                    stream=self.stream,
                )
                if summary:
                    self.video_done.emit("(粘贴文本)", summary)
                else:
                    self.video_error.emit("(粘贴文本)", "总结结果为空")
            except Exception as e:
                logger.exception("独立文本总结失败")
                self.video_error.emit("(粘贴文本)", str(e))

            self.progress.emit(1, 1)
            return

        total = len(self.video_files)

        for idx, video_path in enumerate(self.video_files):
            if self._cancelled:
                break

            video_name = Path(video_path).stem
            transcript_path = None
            for ext in ("txt", "srt", "vtt", "json"):
                candidate = Path(self.output_dir) / f"{video_name}.{ext}"
                if candidate.exists():
                    transcript_path = candidate
                    break

            if transcript_path is None:
                logger.warning("未找到转写文件: %s", video_name)
                self.video_error.emit(video_name, "未找到转写文件")
                self.progress.emit(idx + 1, total)
                continue

            try:
                text = transcript_path.read_text(encoding="utf-8-sig")
                if not text.strip():
                    logger.warning("转写文件为空: %s", video_name)
                    self.video_error.emit(video_name, "转写文件为空")
                    self.progress.emit(idx + 1, total)
                    continue

                logger.info("开始总结 (%d/%d): %s", idx + 1, total, video_name)
                self.summarize_started.emit(video_name)
                summary = service.summarize(
                    text, video_name=video_name, stream=self.stream
                )
                if summary:
                    self.video_done.emit(video_name, summary)
                else:
                    self.video_error.emit(video_name, "总结结果为空")
            except Exception as e:
                logger.exception("总结失败: %s", video_name)
                self.video_error.emit(video_name, str(e))

            self.progress.emit(idx + 1, total)

    def _emit_all_errors(self, msg: str) -> None:
        """连接失败时为所有文件发射错误信号"""
        if self._standalone_text:
            self.video_error.emit("(粘贴文本)", msg)
            self.progress.emit(1, 1)
        else:
            for vp in self.video_files:
                self.video_error.emit(Path(vp).stem, msg)
            self.progress.emit(len(self.video_files), len(self.video_files))


class PipelineWorker(QObject):
    """转写+总结管道线程 —— 每完成一个文件的转写就自动开始总结"""

    transcribe_done = Signal(str, int, list)
    transcribe_error = Signal(str, str)
    summarize_started = Signal(str)
    summarize_done = Signal(str, str)
    summarize_error = Signal(str, str)
    stream_token = Signal(str)
    progress = Signal(int, int)
    error = Signal(str)
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
        self._tx_service_lock = threading.Lock()

    def cancel(self) -> None:
        self._cancelled = True

    def pause(self) -> None:
        with self._tx_service_lock:
            service = self._tx_service
        if service is not None:
            service.pause()

    def resume(self) -> None:
        with self._tx_service_lock:
            service = self._tx_service
        if service is not None:
            service.resume()

    @property
    def is_paused(self) -> bool:
        with self._tx_service_lock:
            service = self._tx_service
        return service.is_paused if service else False

    def run(self) -> None:
        logger = setup_logger(
            "video2text",
            log_dir=self.settings.get("paths.logs_dir", "logs"),
            level=self.settings.get("app.log_level", "INFO"),
            log_to_console=False,
        )

        transcriber = None
        try:
            cfg = _load_tx_config(self.settings)

            logger.info("正在加载转写模型...")
            transcriber = get_cached_transcriber(
                model_path=cfg.model_path,
                device=cfg.device,
                compute_type=cfg.compute_type,
                num_workers=self.settings.get_int("transcription.num_workers", 1),
            )
            transcriber.load_model()
            logger.info("转写模型加载完成")

            video_processor = VideoProcessor(ffmpeg_path=cfg.ffmpeg_path)
            file_writer = FileWriter(self.output_dir)
            segment_merger = SegmentMerger(
                max_gap=self.settings.get_float("text_processing.max_gap", 2.0),
                min_length=self.settings.get_int("text_processing.min_length", 50),
            )
            text_cleaner = TextCleaner()

            provider_name = self.settings.get("summarization.provider", "ollama")
            nvidia_mode = self.settings.get("summarization.nvidia_mode", "single")
            sum_available = False

            if provider_name == "ollama":
                ollama_url = self.settings.get(
                    "summarization.ollama_url", "http://127.0.0.1:11434"
                )
                try:
                    OllamaClient.ensure_service(ollama_url)
                except RuntimeError as e:
                    logger.warning("%s，将只执行转写", e)

            check_provider = create_provider(self.settings)
            try:
                sum_available = check_provider.check_connection()
                if not sum_available:
                    logger.warning("总结服务不可用，将只执行转写")
            finally:
                check_provider.close()

            total = len(self.video_files)
            done_count = 0
            sum_done = 0
            total_steps = total * 2

            def on_tx_done(result: TranscribeResult):
                nonlocal done_count
                done_count += 1
                self.transcribe_done.emit(
                    result.video_name, len(result.segments), result.output_paths
                )
                self.progress.emit(done_count, total_steps)

            def on_tx_error(video_name: str, error_msg: str):
                nonlocal done_count
                done_count += 1
                self.transcribe_error.emit(video_name, error_msg)
                self.progress.emit(done_count, total_steps)

            tx_service = TranscriptionService(
                transcriber=transcriber,
                video_processor=video_processor,
                file_writer=file_writer,
                language=cfg.language,
                beam_size=cfg.beam_size,
                temperature=cfg.temperature,
                vad_filter=self.settings.get_bool("transcription.vad_filter", True),
                max_chunk_duration=cfg.max_chunk_duration,
                output_formats=cfg.output_formats,
                on_video_done=on_tx_done,
                on_video_error=on_tx_error,
                cancel_check=lambda: self._cancelled,
            )
            self._tx_service = tx_service

            results = tx_service.run(self.video_files, self.output_dir)

            if sum_available and provider_name == "nvidia" and nvidia_mode == "multi":
                sum_done = self._summarize_results_multi(
                    logger,
                    file_writer,
                    segment_merger,
                    text_cleaner,
                    results,
                    sum_done,
                    total,
                    total_steps,
                )
            else:
                sum_done = self._summarize_results_serial(
                    logger,
                    file_writer,
                    segment_merger,
                    text_cleaner,
                    results,
                    sum_done,
                    total,
                    total_steps,
                    sum_available,
                )

        except Exception as exc:
            logger.exception("管道线程异常")
            self.error.emit(str(exc))
        finally:
            self._tx_service = None
            self.finished.emit()

    def _prepare_text(
        self, result: TranscribeResult, segment_merger, text_cleaner
    ) -> str:
        merged = segment_merger.merge_segments(result.segments)
        processed_text = segment_merger.format_segments_as_text(
            merged, include_timestamps=False
        )
        return text_cleaner.clean(processed_text)

    def _summarize_results_serial(
        self,
        logger,
        file_writer,
        segment_merger,
        text_cleaner,
        results,
        sum_done,
        total,
        total_steps,
        sum_available,
    ) -> int:
        for result in results:
            if self._cancelled:
                break

            if sum_available:
                processed_text = self._prepare_text(
                    result, segment_merger, text_cleaner
                )
                provider_inst = create_provider(self.settings)
                try:
                    service = SummarizationService(
                        settings=self.settings,
                        file_writer=file_writer,
                        provider=provider_inst,
                        custom_prompt=self.custom_prompt,
                        on_stream_token=lambda token: self.stream_token.emit(token),
                        cancel_check=lambda: self._cancelled,
                    )
                    self.summarize_started.emit(result.video_name)
                    summary = service.summarize(
                        processed_text,
                        video_name=result.video_name,
                        stream=self.stream,
                    )
                    if summary:
                        self.summarize_done.emit(result.video_name, summary)
                    else:
                        self.summarize_error.emit(result.video_name, "总结结果为空")
                except Exception as e:
                    logger.exception("总结失败: %s", result.video_name)
                    self.summarize_error.emit(result.video_name, str(e))
                finally:
                    provider_inst.close()
            else:
                self.summarize_error.emit(result.video_name, "总结服务不可用，已跳过")

            sum_done += 1
            self.progress.emit(total + sum_done, total_steps)

        return sum_done

    def _summarize_results_multi(
        self,
        logger,
        file_writer,
        segment_merger,
        text_cleaner,
        results,
        sum_done,
        total,
        total_steps,
    ) -> int:
        thread_count = self.settings.get_int("summarization.nvidia_thread_count", 3)
        progress_lock = threading.Lock()
        tasks: list[tuple[str, str]] = []

        for result in results:
            if self._cancelled:
                break
            processed_text = self._prepare_text(result, segment_merger, text_cleaner)
            tasks.append((result.video_name, processed_text))

        if not tasks:
            return sum_done

        rate_limiter = RateLimiter(min_interval=1.5)

        def _process(video_name: str, text: str) -> tuple[str, str, str]:
            if self._cancelled:
                return video_name, "", "cancelled"
            rate_limiter.acquire()
            provider = create_provider(self.settings)
            try:
                service = SummarizationService(
                    settings=self.settings,
                    file_writer=file_writer,
                    provider=provider,
                    custom_prompt=self.custom_prompt,
                    cancel_check=lambda: self._cancelled,
                )
                summary = service.summarize(text, video_name=video_name, stream=False)
                return video_name, summary, ""
            except Exception as e:
                logger.exception("总结失败: %s", video_name)
                return video_name, "", str(e)
            finally:
                provider.close()

        with ThreadPoolExecutor(max_workers=thread_count) as executor:
            futures = {}
            for video_name, text in tasks:
                if self._cancelled:
                    break
                self.summarize_started.emit(video_name)
                future = executor.submit(_process, video_name, text)
                futures[future] = video_name

            for future in as_completed(futures):
                try:
                    video_name, summary, err = future.result()
                    if err == "cancelled":
                        continue
                    if summary:
                        self.summarize_done.emit(video_name, summary)
                    else:
                        self.summarize_error.emit(
                            video_name, err if err else "总结结果为空"
                        )
                except Exception as e:
                    video_name = futures[future]
                    logger.exception("线程异常: %s", video_name)
                    self.summarize_error.emit(video_name, str(e))

                with progress_lock:
                    sum_done += 1
                self.progress.emit(total + sum_done, total_steps)

        return sum_done


class OllamaCheckWorker(QObject):
    """异步检查 Ollama 连接状态"""

    result = Signal(bool, float)
    finished = Signal()

    def __init__(self, url: str, model: str = "") -> None:
        super().__init__()
        self.url = url
        self.model = model

    def run(self) -> None:
        try:
            client = OllamaClient(base_url=self.url)
            try:
                t0 = time.monotonic()
                if self.model:
                    ok = client.check_model(self.model)
                else:
                    ok = client.check_connection()
                latency_ms = (time.monotonic() - t0) * 1000
                self.result.emit(ok, latency_ms)
            finally:
                client.close()
        except Exception:
            self.result.emit(False, 0.0)
        finally:
            self.finished.emit()


class OllamaStartServiceWorker(QObject):
    """异步启动 Ollama 服务并等待就绪"""

    result = Signal(bool, str)
    finished = Signal()

    def __init__(
        self, url: str, max_wait: float = 10, poll_interval: float = 0.5
    ) -> None:
        super().__init__()
        self.url = url
        self.max_wait = max_wait
        self.poll_interval = poll_interval

    def run(self) -> None:
        try:
            if OllamaClient.is_service_running(self.url):
                self.result.emit(True, "already_running")
                return

            started = OllamaClient.start_service(self.url)
            if not started:
                self.result.emit(False, "not_found")
                return

            elapsed = 0.0
            while elapsed < self.max_wait:
                time.sleep(self.poll_interval)
                elapsed += self.poll_interval
                if OllamaClient.is_service_running(self.url):
                    self.result.emit(True, "started")
                    return

            self.result.emit(False, "timeout")
        except Exception:
            self.result.emit(False, "error")
        finally:
            self.finished.emit()


class OllamaListModelWorker(QObject):
    """异步获取 Ollama 模型列表"""

    result = Signal(object)
    finished = Signal()

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url

    def run(self) -> None:
        try:
            client = OllamaClient(base_url=self.url)
            try:
                models = client.list_models()
            finally:
                client.close()
            self.result.emit(models)
        except Exception:
            self.result.emit([])
        finally:
            self.finished.emit()


class NvidiaCheckWorker(QObject):
    """异步检查 NVIDIA API 连接状态"""

    result = Signal(bool, float)
    finished = Signal()

    def __init__(self, api_url: str) -> None:
        super().__init__()
        self.api_url = api_url

    def run(self) -> None:
        from src.summarization.nvidia_client import NvidiaClient

        try:
            client = NvidiaClient(api_url=self.api_url)
            try:
                t0 = time.monotonic()
                ok = client.check_connection()
                latency_ms = (time.monotonic() - t0) * 1000
                self.result.emit(ok, latency_ms)
            finally:
                client.close()
        except Exception:
            self.result.emit(False, 0.0)
        finally:
            self.finished.emit()
