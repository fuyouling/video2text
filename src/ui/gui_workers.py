"""GUI Worker 线程 —— 使用服务层，支持流式输出、断点续传、单文件即时回调"""

import threading
import time
from pathlib import Path
from typing import Callable, Optional

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
from src.utils.env_loader import get_api_key
from src.utils.exceptions import DownloadCancelledError
from src.utils.logger import get_logger
from src.utils.rate_limit import RateLimiter

# ---------------------------------------------------------------------------
# 模块级辅助类与函数
# ---------------------------------------------------------------------------


class _ProgressTracker:
    """线程安全的进度跟踪器，支持偏移量（Phase 2 从 Phase 1 末尾开始）。"""

    def __init__(
        self, total: int, emit_fn: Callable[[int, int], None], offset: int = 0
    ):
        self._lock = threading.Lock()
        self._current = offset
        self._total = total
        self._emit = emit_fn

    def tick(self) -> None:
        with self._lock:
            self._current += 1
            self._emit(self._current, self._total)

    @property
    def current(self) -> int:
        return self._current


class PauseController:
    """暂停控制逻辑，可嵌入任何 Worker。"""

    def __init__(self):
        self._event = threading.Event()
        self._event.set()

    def pause(self) -> None:
        self._event.clear()

    def resume(self) -> None:
        self._event.set()

    def unpause(self) -> None:
        self._event.set()

    def wait_if_paused(self, cancel_check=None) -> None:
        if self._event.is_set():
            return
        _logger = get_logger("video2text")
        _logger.info("  ├─ ✅ 已暂停 — 等待恢复…")
        while not self._event.wait(timeout=0.5):
            if cancel_check and cancel_check():
                break
        if not (cancel_check and cancel_check()):
            _logger.info("  └─ ▶ 已继续")

    @property
    def is_paused(self) -> bool:
        return not self._event.is_set()

    def get_event(self) -> threading.Event:
        return self._event


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


def _check_summarization_connection(
    settings: Settings, logger, provider_name: str
) -> bool:
    """检查总结提供商连接状态。返回 True 表示可用。"""
    provider_label = _get_provider_label(provider_name)
    if provider_name == "ollama":
        ollama_url = settings.get("summarization.ollama_url", "http://127.0.0.1:11434")
        ollama_model = settings.get("summarization.ollama_model", "")
        ok = OllamaClient.full_check(ollama_url, ollama_model or "")
        if not ok:
            logger.warning("Ollama 服务不可用")
    else:
        provider = create_provider(settings)
        try:
            ok = provider.check_connection()
            if not ok:
                logger.warning("%s 连接: ✗ 失败", provider_label)
        finally:
            provider.close()
    status = "✓" if ok else "✗"
    logger.info("[总结] %s: %s", provider_label, status)
    return ok


def _build_transcription_service(
    settings: Settings,
    output_dir: str,
    input_folder: Optional[str],
    mirror_depth: int,
    on_video_done: Callable[[TranscribeResult], None],
    on_video_error: Callable[[str, str], None],
    cancel_check: Callable[[], bool],
    confirm_download_callback: Callable[[], bool],
) -> TranscriptionService:
    """创建配置完整的 TranscriptionService 并加载模型。"""
    logger = get_logger("video2text")
    cfg = _load_tx_config(settings)

    logger.info("正在加载转写模型...")
    transcriber = get_cached_transcriber(
        model_path=cfg.model_path,
        device=cfg.device,
        compute_type=cfg.compute_type,
        num_workers=settings.get_int("transcription.num_workers", 1),
    )
    transcriber.confirm_download_callback = confirm_download_callback
    transcriber.load_model()
    logger.info("转写模型加载完成")

    model_name = Path(cfg.model_path).name
    logger.info(
        "[转写] 模型: %s | 设备: %s (%s) ✓ ",
        model_name,
        transcriber.device,
        transcriber.compute_type,
    )

    return TranscriptionService(
        transcriber=transcriber,
        video_processor=VideoProcessor(),
        file_writer=FileWriter(output_dir),
        language=cfg.language,
        beam_size=cfg.beam_size,
        best_of=cfg.best_of,
        temperature=cfg.temperature,
        condition_on_previous_text=cfg.condition_on_previous_text,
        word_timestamps=cfg.word_timestamps,
        vad_filter=settings.get_bool("transcription.vad_filter", True),
        max_chunk_duration=cfg.max_chunk_duration,
        output_formats=cfg.output_formats,
        input_folder=input_folder,
        mirror_depth=mirror_depth,
        on_video_done=on_video_done,
        on_video_error=on_video_error,
        cancel_check=cancel_check,
    )


def prepare_transcript_text(
    transcript_path_or_result, segment_merger=None, text_cleaner=None
) -> str:
    """从转写路径或 TranscribeResult 中提取并预处理文本。"""
    if isinstance(transcript_path_or_result, Path):
        text = transcript_path_or_result.read_text(encoding="utf-8-sig")
        return text
    else:
        result = transcript_path_or_result
        if segment_merger and text_cleaner:
            merged = segment_merger.merge_segments(result.segments)
            processed = segment_merger.format_segments_as_text(
                merged, include_timestamps=False
            )
            return text_cleaner.clean(processed)
        return result.text


# ---------------------------------------------------------------------------
# TranscribeWorker
# ---------------------------------------------------------------------------


class TranscribeWorker(QObject):
    """后台转写线程 —— 每完成一个文件立即通过信号通知 GUI"""

    video_done = Signal(str, int, list)
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
        self._cancelled = True
        self._confirm_event.set()

    def pause(self) -> None:
        with self._service_lock:
            if self._service is not None:
                self._service.pause()

    def resume(self) -> None:
        with self._service_lock:
            if self._service is not None:
                self._service.resume()

    def unpause(self) -> None:
        with self._service_lock:
            if self._service is not None:
                self._service.resume()

    @property
    def is_paused(self) -> bool:
        with self._service_lock:
            return self._service.is_paused if self._service else False

    def _confirm_download_callback(self) -> bool:
        if self._cancelled:
            return False
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
        logger = get_logger("video2text")
        try:
            total = len(self.video_files)
            done_count = [0]

            def on_done(result: TranscribeResult):
                done_count[0] += 1
                self.video_done.emit(
                    result.video_name, len(result.segments), result.output_paths
                )
                self.progress.emit(done_count[0], total)

            def on_error(video_name: str, error_msg: str):
                done_count[0] += 1
                self.video_error.emit(video_name, error_msg)
                self.progress.emit(done_count[0], total)

            service = _build_transcription_service(
                self.settings,
                self.output_dir,
                self.input_folder,
                self.mirror_depth,
                on_video_done=on_done,
                on_video_error=on_error,
                cancel_check=lambda: self._cancelled,
                confirm_download_callback=self._confirm_download_callback,
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


# ---------------------------------------------------------------------------
# SummarizeWorker
# ---------------------------------------------------------------------------


class SummarizeWorker(QObject):
    """后台总结线程 —— 委托 SummarizationService，仅负责 Qt 信号适配"""

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
        self._active_provider = None
        self._pause_ctrl = PauseController()

    def cancel(self) -> None:
        self._cancelled = True
        provider = self._active_provider
        if provider is not None:
            try:
                provider.close()
            except Exception:
                pass

    def pause(self) -> None:
        self._pause_ctrl.pause()
        get_logger("video2text").info("  ├─ ⏸ 总结暂停请求已接收，等待当前任务完成…")

    def resume(self) -> None:
        self._pause_ctrl.resume()

    def unpause(self) -> None:
        self._pause_ctrl.unpause()

    def _wait_if_paused(self) -> None:
        self._pause_ctrl.wait_if_paused(lambda: self._cancelled)

    @property
    def is_paused(self) -> bool:
        return self._pause_ctrl.is_paused

    def run(self) -> None:
        logger = get_logger("video2text")
        try:
            file_writer = FileWriter(self.output_dir)
            provider_name = self.settings.get("summarization.provider", "ollama")

            if not _check_summarization_connection(
                self.settings, logger, provider_name
            ):
                provider_label = _get_provider_label(provider_name)
                self.error.emit(f"{provider_label} 服务不可用")
                return

            items = self._prepare_items(file_writer)
            if not items:
                return

            tracker = _ProgressTracker(len(items), self.progress.emit)

            valid_items = []
            for item in items:
                if item.get("_skip"):
                    tracker.tick()
                    self.video_error.emit(item["video_name"], "转写文件不可用")
                    continue
                valid_items.append(item)

            if not valid_items:
                return

            provider_inst = create_provider(self.settings)
            self._active_provider = provider_inst
            try:
                mode = _get_online_cfg(self.settings, "mode", "single")
                max_workers = (
                    _get_online_cfg(self.settings, "thread_count", 5)
                    if provider_name in ("nvidia", "zhipu") and mode == "multi"
                    else 1
                )
                stream = self.stream and max_workers <= 1

                service = SummarizationService(
                    settings=self.settings,
                    file_writer=file_writer,
                    provider=provider_inst,
                    custom_prompt=self.custom_prompt,
                    on_stream_token=lambda token: (
                        self.stream_token.emit(token) if stream else None
                    ),
                    cancel_check=lambda: self._cancelled,
                    pause_event=self._pause_ctrl.get_event(),
                    rate_limiter=RateLimiter(1.5) if max_workers > 1 else None,
                    on_item_started=lambda name: self.summarize_started.emit(name),
                    on_item_done=lambda name, summary: (
                        tracker.tick(),
                        self.video_done.emit(name, summary),
                    ),
                    on_item_error=lambda name, err: (
                        tracker.tick(),
                        self.video_error.emit(name, err),
                    ),
                )
                service.summarize_batch(
                    valid_items,
                    stream=stream,
                    max_workers=max_workers,
                )
            finally:
                self._active_provider = None
                provider_inst.close()

        except Exception as exc:
            logger.exception("总结线程异常")
            self.error.emit(str(exc))
        finally:
            self.finished.emit()

    def _prepare_items(self, file_writer: FileWriter) -> list[dict]:
        """准备 {video_name, text, file_writer} 列表，支持 input_folder 镜像目录。"""
        logger = get_logger("video2text")
        items = []
        for video_path in self.video_files:
            if self._cancelled:
                break
            video_name = Path(video_path).stem
            file_output_dir = TranscriptionService.get_file_output_dir(
                video_path, self.output_dir, self.input_folder, self.mirror_depth
            )
            per_fw = FileWriter(file_output_dir) if self.input_folder else file_writer
            transcript_path = per_fw.find_transcript_file(video_name)
            if transcript_path is None:
                logger.warning("未找到转写文件: %s", video_name)
                items.append(
                    {
                        "video_name": video_name,
                        "text": "",
                        "file_writer": per_fw,
                        "_skip": True,
                    }
                )
                continue
            text = transcript_path.read_text(encoding="utf-8-sig")
            if not text.strip():
                logger.warning("转写文件为空: %s", video_name)
                items.append(
                    {
                        "video_name": video_name,
                        "text": "",
                        "file_writer": per_fw,
                        "_skip": True,
                    }
                )
                continue
            items.append(
                {"video_name": video_name, "text": text, "file_writer": per_fw}
            )
        return items


# ---------------------------------------------------------------------------
# PipelineWorker
# ---------------------------------------------------------------------------


class PipelineWorker(QObject):
    """转写总结管道线程 —— Phase 2 复用 SummarizationService.summarize_batch()"""

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
    phase_changed = Signal(str)

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
        self._sum_pause_ctrl = PauseController()

    def cancel(self) -> None:
        self._cancelled = True
        self._confirm_event.set()
        self._sum_pause_ctrl.unpause()
        provider = self._active_provider
        if provider is not None:
            try:
                provider.close()
            except Exception:
                pass

    def pause(self) -> None:
        with self._tx_service_lock:
            if self._tx_service is not None:
                self._tx_service.pause()

    def resume(self) -> None:
        with self._tx_service_lock:
            if self._tx_service is not None:
                self._tx_service.resume()

    def unpause(self) -> None:
        with self._tx_service_lock:
            if self._tx_service is not None:
                self._tx_service.resume()
        self._sum_pause_ctrl.unpause()

    def sum_pause(self) -> None:
        self._sum_pause_ctrl.pause()
        get_logger("video2text").info("  ├─ ⏸ 总结暂停请求已接收，等待当前任务完成…")

    def sum_resume(self) -> None:
        self._sum_pause_ctrl.resume()

    def sum_unpause(self) -> None:
        self._sum_pause_ctrl.unpause()

    def _sum_wait_if_paused(self) -> None:
        self._sum_pause_ctrl.wait_if_paused(lambda: self._cancelled)

    @property
    def is_paused(self) -> bool:
        with self._tx_service_lock:
            return self._tx_service.is_paused if self._tx_service else False

    @property
    def is_sum_paused(self) -> bool:
        return self._sum_pause_ctrl.is_paused

    def _confirm_download_callback(self) -> bool:
        if self._cancelled:
            return False
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
        logger = get_logger("video2text")
        file_writer = FileWriter(self.output_dir)
        try:
            total = len(self.video_files)
            total_steps = total * 2
            done_count = [0]

            def on_tx_done(result: TranscribeResult):
                done_count[0] += 1
                self.transcribe_done.emit(
                    result.video_name, len(result.segments), result.output_paths
                )
                self.progress.emit(done_count[0], total_steps)

            def on_tx_error(video_name: str, error_msg: str):
                done_count[0] += 1
                self.transcribe_error.emit(video_name, error_msg)
                self.progress.emit(done_count[0], total_steps)

            service = _build_transcription_service(
                self.settings,
                self.output_dir,
                self.input_folder,
                self.mirror_depth,
                on_video_done=on_tx_done,
                on_video_error=on_tx_error,
                cancel_check=lambda: self._cancelled,
                confirm_download_callback=self._confirm_download_callback,
            )
            with self._tx_service_lock:
                self._tx_service = service

            results = service.run(self.video_files, self.output_dir)

            # ---- Phase 2: 总结 ----
            provider_name = self.settings.get("summarization.provider", "ollama")
            sum_available = _check_summarization_connection(
                self.settings, logger, provider_name
            )

            self.phase_changed.emit("summarize")

            if sum_available:
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

                items = []
                for result in results:
                    if self._cancelled:
                        break
                    processed_text = prepare_transcript_text(
                        result, segment_merger, text_cleaner
                    )
                    per_file_dir = (
                        str(Path(result.output_paths[0]).parent)
                        if result.output_paths and self.input_folder
                        else self.output_dir
                    )
                    per_fw = FileWriter(per_file_dir)
                    items.append(
                        {
                            "video_name": result.video_name,
                            "text": processed_text,
                            "file_writer": per_fw,
                        }
                    )

                if items and not self._cancelled:
                    mode = _get_online_cfg(self.settings, "mode", "single")
                    max_workers = (
                        _get_online_cfg(self.settings, "thread_count", 5)
                        if provider_name in ("nvidia", "zhipu") and mode == "multi"
                        else 1
                    )
                    stream = self.stream and max_workers <= 1
                    rate_limiter = RateLimiter(1.5) if max_workers > 1 else None

                    tracker = _ProgressTracker(
                        total_steps, self.progress.emit, offset=total
                    )

                    provider_inst = create_provider(self.settings)
                    self._active_provider = provider_inst
                    try:
                        sum_service = SummarizationService(
                            settings=self.settings,
                            file_writer=file_writer,
                            provider=provider_inst,
                            custom_prompt=self.custom_prompt,
                            on_stream_token=lambda token: (
                                self.stream_token.emit(token) if stream else None
                            ),
                            cancel_check=lambda: self._cancelled,
                            pause_event=self._sum_pause_ctrl.get_event(),
                            rate_limiter=rate_limiter,
                            on_item_started=lambda name: self.summarize_started.emit(
                                name
                            ),
                            on_item_done=lambda name, summary: (
                                tracker.tick(),
                                self.summarize_done.emit(name, summary),
                            ),
                            on_item_error=lambda name, err: (
                                tracker.tick(),
                                self.summarize_error.emit(name, err),
                            ),
                        )
                        sum_service.summarize_batch(
                            items, stream=stream, max_workers=max_workers
                        )
                    finally:
                        self._active_provider = None
                        provider_inst.close()
            else:
                provider_label = _get_provider_label(provider_name)
                logger.warning("%s 服务不可用，跳过总结", provider_label)

        except DownloadCancelledError:
            logger.info("用户取消了模型下载")
        except Exception as exc:
            logger.exception("管道线程异常")
            self.error.emit(str(exc))
        finally:
            with self._tx_service_lock:
                self._tx_service = None
            self.finished.emit()


# ---------------------------------------------------------------------------
# CheckWorker (统一)
# ---------------------------------------------------------------------------


class CheckWorker(QObject):
    """通用连接检查 Worker —— 根据 provider_type 检查对应 API 连通性。"""

    result = Signal(bool, float, str)
    finished = Signal()

    def __init__(self, provider_type: str, **kwargs):
        super().__init__()
        self.provider_type = provider_type
        self.kwargs = kwargs

    def run(self) -> None:
        t0 = time.monotonic()
        try:
            if self.provider_type == "ollama":
                ok, detail = self._check_ollama()
            elif self.provider_type == "nvidia":
                ok, detail = self._check_nvidia()
            elif self.provider_type == "zhipu":
                ok, detail = self._check_zhipu()
            else:
                ok, detail = False, "unknown_provider"
            latency_ms = (time.monotonic() - t0) * 1000
            self.result.emit(ok, latency_ms, detail)
        except Exception:
            self.result.emit(False, 0.0, "error")
        finally:
            self.finished.emit()

    def _check_ollama(self) -> tuple[bool, str]:
        client = OllamaClient(base_url=self.kwargs["url"])
        try:
            if not client.check_connection(quiet=True):
                return False, "connection_failed"
            model = self.kwargs.get("model", "")
            if model and not client.check_model(model, quiet=True):
                return False, "model_not_found"
            return True, ""
        finally:
            client.close()

    def _check_nvidia(self) -> tuple[bool, str]:
        from src.summarization.nvidia_client import NvidiaClient

        client = NvidiaClient(
            api_url=self.kwargs["api_url"],
            api_key=get_api_key("NVIDIA_API_KEY"),
            model=self.kwargs.get("model", ""),
        )
        try:
            return client.check_connection(), ""
        finally:
            client.close()

    def _check_zhipu(self) -> tuple[bool, str]:
        from src.summarization.zhipu_client import ZhipuClient

        client = ZhipuClient(
            api_key=get_api_key("ZHIPU_API_KEY"),
            model=self.kwargs.get("model", ""),
        )
        try:
            return client.check_connection(), ""
        finally:
            client.close()


# ---------------------------------------------------------------------------
# Ollama 辅助 Workers (保持不变)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# ScanFilesWorker (保持不变)
# ---------------------------------------------------------------------------


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
