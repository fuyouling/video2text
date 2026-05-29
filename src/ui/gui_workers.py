"""GUI Worker 线程 —— 使用服务层，支持流式输出、断点续传、单文件即时回调"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

from src.config.settings import Settings
from src.config.transcription_config import _load_tx_config
from src.preprocessing.video_processor import VideoProcessor
from src.services.transcription_service import TranscriptionService, TranscribeResult
from src.services.summarization_service import SummarizationService
from src.storage.file_writer import FileWriter
from src.summarization.ollama_client import OllamaClient
from src.summarization.providers import create_provider
from src.text_processing.segment_merger import SegmentMerger
from src.text_processing.text_cleaner import TextCleaner
from src.transcription.transcriber import get_cached_transcriber
from src.utils.exceptions import DownloadCancelledError
from src.utils.logger import get_logger, setup_logger


def _get_online_cfg(settings: Settings, suffix: str, default):
    """根据当前 provider 动态拼配置键: summarization.{provider}_{suffix}"""
    provider = settings.get("summarization.provider", "ollama")
    key = f"summarization.{provider}_{suffix}"
    if isinstance(default, bool):
        return settings.get_bool(key, default)
    if isinstance(default, int):
        return settings.get_int(key, default)
    return settings.get(key, default)


def _get_provider_label(provider: str) -> str:
    return {"ollama": "Ollama", "nvidia": "NVIDIA API", "zhipu": "智谱 API"}.get(
        provider, provider
    )


def _setup_worker_logger(settings: Settings):
    return setup_logger(
        "video2text",
        log_dir=settings.get("paths.logs_dir", "logs"),
        level=settings.get("app.log_level", "INFO"),
        log_to_console=False,
    )


class RateLimiter:
    """简单的速率限制器 —— 确保两次操作之间的间隔不低于指定秒数，用于 API 调用限流。"""

    def __init__(self, min_interval: float = 1.5):
        """初始化速率限制器。

        Args:
            min_interval: 最小间隔秒数
        """
        self._lock = threading.Lock()
        self._min_interval = min_interval
        self._last_time = 0.0

    def acquire(self) -> None:
        """获取操作许可，若距上次操作不足最小间隔则阻塞等待。"""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_time = time.monotonic()


class TranscribeWorker(QObject):
    """后台转写线程 —— 每完成一个文件立即通过信号通知 GUI"""

    # (video_name, segments_count, output_paths)
    video_done = Signal(str, int, list)
    # (video_name, error_message)
    video_error = Signal(str, str)
    progress = Signal(int, int)
    error = Signal(str)
    finished = Signal()
    confirm_download = Signal()

    def __init__(
        self,
        video_files: list[str],
        output_dir: str,
        settings: Settings,
        input_folder: Optional[str] = None,
        mirror_depth: int = 1,
    ) -> None:
        super().__init__()
        self.video_files = video_files
        self.output_dir = output_dir
        self.settings = settings
        self.input_folder = input_folder
        self.mirror_depth = mirror_depth
        self._cancelled = False
        self._service: Optional[TranscriptionService] = None
        self._service_lock = threading.Lock()
        self._confirm_event = threading.Event()
        self._confirm_result = [False]

    def cancel(self) -> None:
        """标记取消，终止后续文件转写。"""
        self._cancelled = True
        self._confirm_event.set()

    def pause(self) -> None:
        """暂停当前转写任务。"""
        with self._service_lock:
            if self._service is not None:
                self._service.pause()

    def resume(self) -> None:
        """恢复被暂停的转写任务。"""
        with self._service_lock:
            if self._service is not None:
                self._service.resume()

    def unpause(self) -> None:
        """强制解除暂停状态（用于关闭窗口时确保线程不阻塞）。"""
        with self._service_lock:
            if self._service is not None:
                self._service.resume()

    @property
    def is_paused(self) -> bool:
        with self._service_lock:
            return self._service.is_paused if self._service else False

    def _confirm_download_callback(self) -> bool:
        self._confirm_event.clear()
        self._confirm_result[0] = False
        self.confirm_download.emit()
        self._confirm_event.wait()
        if self._cancelled:
            return False
        return self._confirm_result[0]

    def set_download_confirmed(self, confirmed: bool) -> None:
        self._confirm_result[0] = confirmed
        self._confirm_event.set()

    def run(self) -> None:
        logger = _setup_worker_logger(self.settings)

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
            transcriber.confirm_download_callback = self._confirm_download_callback
            transcriber.load_model()
            logger.info("转写模型加载完成")

            model_name = Path(cfg.model_path).name
            logger.info(
                "[转写] 模型: %s | 设备: %s (%s) ✓ ",
                model_name,
                transcriber.device,
                transcriber.compute_type,
            )

            video_processor = VideoProcessor()
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
                best_of=cfg.best_of,
                temperature=cfg.temperature,
                condition_on_previous_text=cfg.condition_on_previous_text,
                word_timestamps=cfg.word_timestamps,
                vad_filter=self.settings.get_bool("transcription.vad_filter", True),
                max_chunk_duration=cfg.max_chunk_duration,
                output_formats=cfg.output_formats,
                input_folder=self.input_folder,
                mirror_depth=self.mirror_depth,
                on_video_done=on_video_done,
                on_video_error=on_video_error,
                cancel_check=lambda: self._cancelled,
            )
            with self._service_lock:
                self._service = service

            service.run(self.video_files, self.output_dir)

        except DownloadCancelledError:
            logger.info("用户取消了模型下载")
        except Exception as exc:
            logger.exception("转写线程异常")
            self.error.emit(str(exc))
        finally:
            with self._service_lock:
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
        input_folder: Optional[str] = None,
        mirror_depth: int = 1,
    ) -> None:
        super().__init__()
        self.video_files = video_files
        self.output_dir = output_dir
        self.settings = settings
        self.custom_prompt = custom_prompt
        self.stream = stream
        self.input_folder = input_folder
        self.mirror_depth = mirror_depth
        self._cancelled = False
        self._standalone_text: str = ""
        self._active_provider = None

    def set_standalone_text(self, text: str) -> None:
        """设置独立文本模式的内容（仅总结指定文本，不从文件读取）。"""
        self._standalone_text = text

    def cancel(self) -> None:
        """标记取消，终止后续总结任务。"""
        self._cancelled = True
        provider = self._active_provider
        if provider is not None:
            try:
                provider.close()
            except Exception:
                pass

    def run(self) -> None:
        """执行总结任务：根据配置选择单线程或多线程模式。"""
        logger = _setup_worker_logger(self.settings)

        try:
            file_writer = FileWriter(self.output_dir)
            provider = self.settings.get("summarization.provider", "ollama")
            mode = _get_online_cfg(self.settings, "mode", "single")

            if provider in ("nvidia", "zhipu") and mode == "multi":
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
        """单线程模式（Ollama / NVIDIA single / 智谱 single）"""
        provider_label = _get_provider_label(provider)
        if provider == "ollama":
            ollama_url = self.settings.get(
                "summarization.ollama_url", "http://127.0.0.1:11434"
            )
            ollama_model = self.settings.get("summarization.ollama_model", "")
            sum_available = OllamaClient.full_check(ollama_url, ollama_model or "")
        else:
            provider_inst = create_provider(self.settings)
            try:
                sum_available = provider_inst.check_connection()
            finally:
                provider_inst.close()

        sum_status = "✓" if sum_available else "✗"
        logger.info("[总结] %s: %s", provider_label, sum_status)

        if not sum_available:
            msg = f"{provider_label} 服务不可用"
            logger.error(msg)
            self.error.emit(msg)
            self._emit_all_errors(msg)
            return

        provider_inst = create_provider(self.settings)
        self._active_provider = provider_inst
        try:
            service = SummarizationService(
                settings=self.settings,
                file_writer=file_writer,
                provider=provider_inst,
                custom_prompt=self.custom_prompt,
                on_stream_token=lambda token: self.stream_token.emit(token),
                cancel_check=lambda: self._cancelled,
            )
            self._execute_summarization(logger, service, file_writer)
        except Exception as e:
            logger.exception("总结流程异常")
            self.error.emit(str(e))
        finally:
            self._active_provider = None
            provider_inst.close()

    def _run_multi_thread(self, logger, file_writer: FileWriter) -> None:
        """多线程模式（NVIDIA/智谱 multi）—— 强制非流式"""
        provider = self.settings.get("summarization.provider", "ollama")
        provider_label = _get_provider_label(provider)
        provider_inst = create_provider(self.settings)
        try:
            sum_available = provider_inst.check_connection()
        finally:
            provider_inst.close()

        sum_status = "✓" if sum_available else "✗"
        logger.info("[总结] %s: %s", provider_label, sum_status)

        if not sum_available:
            msg = f"{provider_label} 服务不可用"
            logger.error(msg)
            self.error.emit(msg)
            self._emit_all_errors(msg)
            return

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
                logger.info("[1/1] (粘贴文本)")
                summary = service.summarize(
                    self._standalone_text,
                    video_name="",
                    stream=False,
                    index=1,
                    total=1,
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
        thread_count = _get_online_cfg(self.settings, "thread_count", 5)
        total = len(self.video_files)
        progress_lock = threading.Lock()
        done_count = [0]

        tasks: list[tuple[str, str, FileWriter]] = []
        for video_path in self.video_files:
            video_name = Path(video_path).stem
            file_output_dir = TranscriptionService.get_file_output_dir(
                video_path, self.output_dir, self.input_folder, self.mirror_depth
            )
            per_file_writer = (
                FileWriter(file_output_dir) if self.input_folder else file_writer
            )
            transcript_path = per_file_writer.find_transcript_file(video_name)

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
                tasks.append((video_name, text, per_file_writer))
            except Exception as e:
                logger.exception("读取转写文件失败: %s", video_name)
                self.video_error.emit(video_name, str(e))
                with progress_lock:
                    done_count[0] += 1
                self.progress.emit(done_count[0], total)

        if not tasks:
            return

        task_total = len(tasks)
        rate_limiter = RateLimiter(min_interval=1.5)
        summary_format = (
            self.settings.get("output.summary_format", "txt").lower().strip()
        )

        def _process_video(
            idx: int, video_name: str, text: str, fw: FileWriter
        ) -> tuple[str, str, str]:
            if self._cancelled:
                return video_name, "", "cancelled"

            rate_limiter.acquire()
            provider = create_provider(self.settings)
            try:
                service = SummarizationService(
                    settings=self.settings,
                    file_writer=fw,
                    provider=provider,
                    custom_prompt=self.custom_prompt,
                    cancel_check=lambda: self._cancelled,
                )
                try:
                    summary = service.summarize(
                        text,
                        video_name=video_name,
                        stream=False,
                    )
                    return video_name, summary, ""
                finally:
                    service.close()
            except Exception as e:
                return video_name, "", str(e)
            finally:
                provider.close()

        with ThreadPoolExecutor(max_workers=thread_count) as executor:
            futures = {}
            for idx, (video_name, text, fw) in enumerate(tasks):
                if self._cancelled:
                    break
                self.summarize_started.emit(video_name)
                future = executor.submit(_process_video, idx, video_name, text, fw)
                futures[future] = (video_name, idx)

            for future in as_completed(futures):
                try:
                    video_name, summary, err = future.result()
                    if err == "cancelled":
                        continue

                    _, idx = futures[future]
                    with progress_lock:
                        done_count[0] += 1

                    if summary:
                        logger.info("[%d/%d] %s", idx + 1, task_total, video_name)
                        logger.info("  └─ 文本总结完成 ✓ (.%s)", summary_format)
                        self.video_done.emit(video_name, summary)
                    else:
                        err_msg = err if err else "总结结果为空"
                        logger.info("[%d/%d] %s", idx + 1, task_total, video_name)
                        logger.info("  └─ 文本总结失败 ✗ %s", err_msg)
                        self.video_error.emit(video_name, err_msg)
                except Exception as e:
                    video_name, idx = futures[future]
                    with progress_lock:
                        done_count[0] += 1
                    logger.info("[%d/%d] %s", idx + 1, task_total, video_name)
                    logger.info("  └─ 文本总结失败 ✗ %s", e)
                    self.video_error.emit(video_name, str(e))

                self.progress.emit(done_count[0], total)

    def _execute_summarization(
        self, logger, service: SummarizationService, file_writer: FileWriter
    ) -> None:
        """串行总结逻辑（single 模式和 Ollama）"""
        if self._standalone_text and not self.video_files:
            try:
                self.summarize_started.emit("(粘贴文本)")
                logger.info("[1/1] (粘贴文本)")
                summary = service.summarize(
                    self._standalone_text,
                    video_name="",
                    stream=self.stream,
                    index=1,
                    total=1,
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
            file_output_dir = TranscriptionService.get_file_output_dir(
                video_path, self.output_dir, self.input_folder, self.mirror_depth
            )
            per_file_writer = (
                FileWriter(file_output_dir) if self.input_folder else file_writer
            )
            transcript_path = per_file_writer.find_transcript_file(video_name)

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

                self.summarize_started.emit(video_name)
                logger.info("[%d/%d] %s", idx + 1, total, video_name)
                if self.input_folder:
                    per_provider = create_provider(self.settings)
                    try:
                        per_service = SummarizationService(
                            settings=self.settings,
                            file_writer=per_file_writer,
                            provider=per_provider,
                            custom_prompt=self.custom_prompt,
                            on_stream_token=lambda token: self.stream_token.emit(token),
                            cancel_check=lambda: self._cancelled,
                        )
                        summary = per_service.summarize(
                            text,
                            video_name=video_name,
                            stream=self.stream,
                            index=idx + 1,
                            total=total,
                        )
                    finally:
                        per_provider.close()
                else:
                    summary = service.summarize(
                        text,
                        video_name=video_name,
                        stream=self.stream,
                        index=idx + 1,
                        total=total,
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
    """转写总结管道线程 —— 每完成一个文件的转写就自动开始总结"""

    transcribe_done = Signal(str, int, list)
    transcribe_error = Signal(str, str)
    summarize_started = Signal(str)
    summarize_done = Signal(str, str)
    summarize_error = Signal(str, str)
    stream_token = Signal(str)
    progress = Signal(int, int)
    error = Signal(str)
    finished = Signal()
    confirm_download = Signal()

    def __init__(
        self,
        video_files: list[str],
        output_dir: str,
        settings: Settings,
        custom_prompt: str = "",
        stream: bool = True,
        input_folder: Optional[str] = None,
        mirror_depth: int = 1,
    ) -> None:
        super().__init__()
        self.video_files = video_files
        self.output_dir = output_dir
        self.settings = settings
        self.custom_prompt = custom_prompt
        self.stream = stream
        self.input_folder = input_folder
        self.mirror_depth = mirror_depth
        self._cancelled = False
        self._active_provider = None
        self._tx_service: Optional[TranscriptionService] = None
        self._tx_service_lock = threading.Lock()
        self._confirm_event = threading.Event()
        self._confirm_result = [False]

    def cancel(self) -> None:
        """标记取消，终止后续转写和总结。"""
        self._cancelled = True
        self._confirm_event.set()
        provider = self._active_provider
        if provider is not None:
            try:
                provider.close()
            except Exception:
                pass

    def pause(self) -> None:
        """暂停当前转写任务。"""
        with self._tx_service_lock:
            if self._tx_service is not None:
                self._tx_service.pause()

    def resume(self) -> None:
        """恢复被暂停的转写任务。"""
        with self._tx_service_lock:
            if self._tx_service is not None:
                self._tx_service.resume()

    def unpause(self) -> None:
        """强制解除暂停状态（用于关闭窗口时确保线程不阻塞）。"""
        with self._tx_service_lock:
            if self._tx_service is not None:
                self._tx_service.resume()

    @property
    def is_paused(self) -> bool:
        """是否处于暂停状态。"""
        with self._tx_service_lock:
            return self._tx_service.is_paused if self._tx_service else False

    def _confirm_download_callback(self) -> bool:
        self._confirm_event.clear()
        self._confirm_result[0] = False
        self.confirm_download.emit()
        self._confirm_event.wait()
        if self._cancelled:
            return False
        return self._confirm_result[0]

    def set_download_confirmed(self, confirmed: bool) -> None:
        self._confirm_result[0] = confirmed
        self._confirm_event.set()

    def run(self) -> None:
        """执行管道任务：加载模型 → 逐文件转写 → 文本清理 → 总结。"""
        logger = _setup_worker_logger(self.settings)

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
            transcriber.confirm_download_callback = self._confirm_download_callback
            transcriber.load_model()
            logger.info("转写模型加载完成")

            video_processor = VideoProcessor()
            file_writer = FileWriter(self.output_dir)
            segment_merger = SegmentMerger(
                max_gap=self.settings.get_float("text_processing.max_gap", 2.0),
                min_length=self.settings.get_int("text_processing.min_length", 50),
            )
            text_cleaner = TextCleaner(
                {
                    "filler_words": self.settings.get_list(
                        "text_processing.filler_words"
                    ),
                }
            )

            model_name = Path(cfg.model_path).name
            logger.info(
                "[转写] 模型: %s | 设备: %s (%s) ✓ ",
                model_name,
                transcriber.device,
                transcriber.compute_type,
            )

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
                best_of=cfg.best_of,
                temperature=cfg.temperature,
                condition_on_previous_text=cfg.condition_on_previous_text,
                word_timestamps=cfg.word_timestamps,
                vad_filter=self.settings.get_bool("transcription.vad_filter", True),
                max_chunk_duration=cfg.max_chunk_duration,
                output_formats=cfg.output_formats,
                input_folder=self.input_folder,
                mirror_depth=self.mirror_depth,
                on_video_done=on_tx_done,
                on_video_error=on_tx_error,
                cancel_check=lambda: self._cancelled,
            )
            with self._tx_service_lock:
                self._tx_service = tx_service

            results = tx_service.run(self.video_files, self.output_dir)

            provider_name = self.settings.get("summarization.provider", "ollama")
            mode = _get_online_cfg(self.settings, "mode", "single")
            sum_available = False
            provider_label = _get_provider_label(provider_name)

            if provider_name == "ollama":
                ollama_url = self.settings.get(
                    "summarization.ollama_url", "http://127.0.0.1:11434"
                )
                ollama_model = self.settings.get("summarization.ollama_model", "")
                sum_available = OllamaClient.full_check(ollama_url, ollama_model or "")
                if not sum_available:
                    logger.warning("Ollama 服务不可用，将只执行转写")
            else:
                check_provider = create_provider(self.settings)
                try:
                    sum_available = check_provider.check_connection()
                    if not sum_available:
                        logger.warning("%s 连接: ✗ 失败，将只执行转写", provider_label)
                finally:
                    check_provider.close()

            sum_status = "✓" if sum_available else "✗"
            logger.info("[总结] %s: %s", provider_label, sum_status)

            if (
                sum_available
                and provider_name in ("nvidia", "zhipu")
                and mode == "multi"
            ):
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

        except DownloadCancelledError:
            logger.info("用户取消了模型下载")
        except Exception as exc:
            logger.exception("管道线程异常")
            self.error.emit(str(exc))
        finally:
            with self._tx_service_lock:
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
        for idx, result in enumerate(results):
            if self._cancelled:
                break

            if sum_available:
                processed_text = self._prepare_text(
                    result, segment_merger, text_cleaner
                )
                per_file_dir = (
                    str(Path(result.output_paths[0]).parent)
                    if result.output_paths and self.input_folder
                    else self.output_dir
                )
                per_fw = FileWriter(per_file_dir)
                provider_inst = create_provider(self.settings)
                self._active_provider = provider_inst
                try:
                    service = SummarizationService(
                        settings=self.settings,
                        file_writer=per_fw,
                        provider=provider_inst,
                        custom_prompt=self.custom_prompt,
                        on_stream_token=lambda token: self.stream_token.emit(token),
                        cancel_check=lambda: self._cancelled,
                    )
                    self.summarize_started.emit(result.video_name)
                    logger.info("[%d/%d] %s", idx + 1, len(results), result.video_name)
                    summary = service.summarize(
                        processed_text,
                        video_name=result.video_name,
                        stream=self.stream,
                        index=idx + 1,
                        total=len(results),
                    )
                    if summary:
                        self.summarize_done.emit(result.video_name, summary)
                    else:
                        self.summarize_error.emit(result.video_name, "总结结果为空")
                except Exception as e:
                    logger.exception("总结失败: %s", result.video_name)
                    self.summarize_error.emit(result.video_name, str(e))
                finally:
                    self._active_provider = None
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
        thread_count = _get_online_cfg(self.settings, "thread_count", 5)
        progress_lock = threading.Lock()
        tasks: list[tuple[str, str, FileWriter]] = []

        for result in results:
            if self._cancelled:
                break
            processed_text = self._prepare_text(result, segment_merger, text_cleaner)
            per_file_dir = (
                str(Path(result.output_paths[0]).parent)
                if result.output_paths and self.input_folder
                else self.output_dir
            )
            per_fw = FileWriter(per_file_dir)
            tasks.append((result.video_name, processed_text, per_fw))

        if not tasks:
            return sum_done

        rate_limiter = RateLimiter(min_interval=1.5)
        task_total = len(tasks)
        summary_format = (
            self.settings.get("output.summary_format", "txt").lower().strip()
        )

        def _process(
            idx: int, video_name: str, text: str, fw: FileWriter
        ) -> tuple[str, str, str]:
            if self._cancelled:
                return video_name, "", "cancelled"
            rate_limiter.acquire()
            provider = create_provider(self.settings)
            try:
                service = SummarizationService(
                    settings=self.settings,
                    file_writer=fw,
                    provider=provider,
                    custom_prompt=self.custom_prompt,
                    cancel_check=lambda: self._cancelled,
                )
                summary = service.summarize(
                    text,
                    video_name=video_name,
                    stream=False,
                )
                return video_name, summary, ""
            except Exception as e:
                return video_name, "", str(e)
            finally:
                provider.close()

        with ThreadPoolExecutor(max_workers=thread_count) as executor:
            futures = {}
            for idx, (video_name, text, fw) in enumerate(tasks):
                if self._cancelled:
                    break
                self.summarize_started.emit(video_name)
                future = executor.submit(_process, idx, video_name, text, fw)
                futures[future] = (video_name, idx)

            for future in as_completed(futures):
                try:
                    video_name, summary, err = future.result()
                    if err == "cancelled":
                        continue
                    _, idx = futures[future]
                    if summary:
                        logger.info("[%d/%d] %s", idx + 1, task_total, video_name)
                        logger.info("  └─ 文本总结完成 ✓ (.%s)", summary_format)
                        self.summarize_done.emit(video_name, summary)
                    else:
                        err_msg = err if err else "总结结果为空"
                        logger.info("[%d/%d] %s", idx + 1, task_total, video_name)
                        logger.info("  └─ 文本总结失败 ✗ %s", err_msg)
                        self.summarize_error.emit(video_name, err_msg)
                except Exception as e:
                    video_name, idx = futures[future]
                    logger.info("[%d/%d] %s", idx + 1, task_total, video_name)
                    logger.info("  └─ 文本总结失败 ✗ %s", e)
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
        except Exception as exc:
            get_logger(__name__).warning("Ollama 连接: ✗ 异常 %s", exc)
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


class OllamaStopServiceWorker(QObject):
    """异步停止 Ollama 服务并确认关闭"""

    result = Signal(bool, str)
    finished = Signal()

    def __init__(self, url: str, is_external: bool) -> None:
        super().__init__()
        self.url = url
        self.is_external = is_external

    def run(self) -> None:
        try:
            if self.is_external:
                self.result.emit(False, "external")
                return

            OllamaClient.stop_service()
            if OllamaClient.is_service_running(self.url):
                self.result.emit(False, "still_running")
            else:
                self.result.emit(True, "stopped")
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
        except Exception as exc:
            get_logger(__name__).warning("Ollama 模型列表: ✗ 获取失败 %s", exc)
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
            client = NvidiaClient(
                api_url=self.api_url, api_key=os.environ.get("NVIDIA_API_KEY", "")
            )
            try:
                t0 = time.monotonic()
                ok = client.check_connection()
                latency_ms = (time.monotonic() - t0) * 1000
                self.result.emit(ok, latency_ms)
            finally:
                client.close()
        except Exception as exc:
            get_logger(__name__).warning("NVIDIA API 连接: ✗ 异常 %s", exc)
            self.result.emit(False, 0.0)
        finally:
            self.finished.emit()


class ZhipuCheckWorker(QObject):
    """异步检查智谱 API 连接状态"""

    result = Signal(bool, float)
    finished = Signal()

    def __init__(self) -> None:
        super().__init__()

    def run(self) -> None:
        from src.summarization.zhipu_client import ZhipuClient

        try:
            client = ZhipuClient(api_key=os.environ.get("ZHIPU_API_KEY", ""))
            try:
                t0 = time.monotonic()
                ok = client.check_connection()
                latency_ms = (time.monotonic() - t0) * 1000
                self.result.emit(ok, latency_ms)
            finally:
                client.close()
        except Exception as exc:
            get_logger(__name__).warning("智谱 API 连接: ✗ 异常 %s", exc)
            self.result.emit(False, 0.0)
        finally:
            self.finished.emit()


class ScanFilesWorker(QObject):
    """异步递归扫描文件夹中的媒体文件"""

    result = Signal(list)
    finished = Signal()

    def __init__(self, folder: str, media_exts: set[str]) -> None:
        super().__init__()
        self.folder = folder
        self.media_exts = media_exts

    def run(self) -> None:
        try:
            folder_path = Path(self.folder)
            files: list[str] = []
            seen: set[str] = set()
            for ext in self.media_exts:
                try:
                    for f in folder_path.rglob(f"*{ext}"):
                        try:
                            if f.is_file():
                                normalized = str(f).lower()
                                if normalized not in seen:
                                    seen.add(normalized)
                                    files.append(str(f))
                        except PermissionError:
                            get_logger(__name__).warning("无权访问文件，已跳过: %s", f)
                except PermissionError:
                    get_logger(__name__).warning(
                        "无权遍历目录，已跳过: %s", folder_path
                    )
            self.result.emit(sorted(files))
        except Exception:
            self.result.emit([])
        finally:
            self.finished.emit()
