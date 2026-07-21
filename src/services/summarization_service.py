"""总结服务 —— 统一 CLI / GUI 的总结逻辑，支持流式输出与多模型切换"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, List, Optional

from src.config.settings import Settings
from src.i18n import t
from src.storage.file_writer import FileWriter
from src.summarization.providers import SummarizationProvider, create_provider
from src.utils.exceptions import SummarizationError
from src.utils.logger import get_logger
from src.utils.rate_limit import RateLimiter

logger = get_logger(__name__)

OnItemStarted = Callable[[str], None]
OnItemDone = Callable[[str, str], None]
OnItemError = Callable[[str, str], None]


class SummarizationService:
    """总结服务

    主要职责：
    1. 统一 CLI / GUI 的总结逻辑
    2. 支持流式输出（streaming）—— GUI 实时显示生成过程
    3. 支持多后端（Ollama / NVIDIA）via Provider 抽象层
    4. 支持暂停/继续（仅 Ollama 本地模型）
    5. 支持回调/进度跟踪/RateLimiter
    """

    def __init__(
        self,
        settings: Settings,
        file_writer: FileWriter,
        provider: SummarizationProvider,
        *,
        custom_prompt: str = "",
        on_stream_token: Optional[Callable[[str], None]] = None,
        on_item_started: Optional[OnItemStarted] = None,
        on_item_done: Optional[OnItemDone] = None,
        on_item_error: Optional[OnItemError] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_event: Optional[threading.Event] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ):
        self.settings = settings
        self.file_writer = file_writer
        self.provider = provider
        self.custom_prompt = custom_prompt

        self.on_stream_token = on_stream_token
        self.on_item_started = on_item_started
        self.on_item_done = on_item_done
        self.on_item_error = on_item_error
        self.cancel_check = cancel_check
        self.rate_limiter = rate_limiter
        self.summary_format = (
            settings.get("output.summary_format", "txt").lower().strip()
        )

        self._summarize_log_lock = threading.Lock()

        if pause_event is not None:
            self._pause_event = pause_event
        else:
            self._pause_event = threading.Event()
            self._pause_event.set()

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def close(self) -> None:
        self.provider.close()

    def pause(self) -> None:
        self._pause_event.clear()
        logger.info("  ├─ ⏸ " + t("services.summarization.pause_requested"))

    def resume(self) -> None:
        self._pause_event.set()

    def _wait_if_paused(self) -> None:
        if self._pause_event.is_set():
            return
        logger.info("  ├─ ✅ " + t("services.summarization.paused_waiting"))
        while not self._pause_event.wait(timeout=0.5):
            if self.cancel_check and self.cancel_check():
                break
        if not (self.cancel_check and self.cancel_check()):
            logger.info("  └─ ▶ " + t("services.summarization.resumed"))

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def summarize(
        self,
        text: str,
        *,
        video_name: str = "",
        stream: bool = False,
        index: int = 0,
        total: int = 0,
        file_writer: Optional[FileWriter] = None,
    ) -> str:
        if not text or not text.strip():
            raise SummarizationError(t("services.summarization.input_empty"))

        label = video_name or t("services.summarization.unnamed")

        if total > 0:
            logger.info("[%d/%d] %s\n", index, total, label)
            # logger.info("  ├─ 文本总结开始")

        def _on_token(token: str):
            if self.on_stream_token:
                self.on_stream_token(token)

        self._wait_if_paused()
        summary = self.provider.summarize(
            text,
            custom_prompt=self.custom_prompt or "",
            stream=stream,
            on_token=_on_token if stream else None,
            cancel_check=self.cancel_check,
            pause_event=self._pause_event,
        )

        if not summary or not summary.strip():
            raise SummarizationError(t("services.summarization.model_returned_empty"))

        writer = file_writer or self.file_writer
        if video_name:
            writer.write_summary(summary, video_name, fmt=self.summary_format)

        if total > 0:
            logger.info("  └─ " + t("services.summarization.summary_done", format=self.summary_format))
        return summary

    def summarize_batch(
        self,
        items: List[dict],
        stream: bool = False,
        max_workers: int = 1,
    ) -> List[str]:
        total = len(items)

        if max_workers <= 1:
            return self._summarize_batch_serial(items, stream, total)

        return self._summarize_batch_concurrent(items, stream, total, max_workers)

    def _summarize_batch_serial(
        self, items: List[dict], stream: bool, total: int
    ) -> List[str]:
        results = []
        for idx, item in enumerate(items):
            if self.cancel_check and self.cancel_check():
                break

            video_name = item.get("video_name", f"item_{idx}")
            text = item.get("text", "")
            fw = item.get("file_writer") or self.file_writer

            if self.on_item_started:
                self.on_item_started(video_name)

            try:
                summary = self.summarize(
                    text,
                    video_name=video_name,
                    stream=stream,
                    index=idx + 1,
                    total=total,
                    file_writer=fw,
                )
                results.append(summary)
                if self.on_item_done:
                    self.on_item_done(video_name, summary)
            except Exception as e:
                logger.info(t("services.summarization.summary_failed", current=idx + 1, total=total, video_name=video_name, error=e))
                if self.on_item_error:
                    self.on_item_error(video_name, str(e))
                results.append("")

        return results

    def _summarize_batch_concurrent(
        self, items: List[dict], stream: bool, total: int, max_workers: int
    ) -> List[str]:
        if stream:
            logger.warning(t("services.summarization.concurrent_no_stream"))

        results: dict[int, str] = {}

        def _process_item(idx: int, item: dict) -> tuple[int, str]:
            if self.cancel_check and self.cancel_check():
                return idx, ""

            video_name = item.get("video_name", f"item_{idx}")
            text = item.get("text", "")
            fw = item.get("file_writer") or self.file_writer

            if self.rate_limiter:
                self.rate_limiter.acquire()

            if self.on_item_started:
                self.on_item_started(video_name)

            provider = create_provider(self.settings)
            try:
                if not text or not text.strip():
                    logger.warning(t("services.summarization.text_empty", name=video_name))
                    if self.on_item_error:
                        self.on_item_error(video_name, t("services.summarization.text_empty_short"))
                    return idx, ""

                prompt_text = text
                summary = provider.summarize(
                    prompt_text,
                    custom_prompt=self.custom_prompt or "",
                    stream=False,
                    on_token=None,
                    cancel_check=self.cancel_check,
                )

                if summary and summary.strip():
                    fw.write_summary(summary, video_name, fmt=self.summary_format)

                with self._summarize_log_lock:
                    logger.info("[%d/%d] %s", idx + 1, total, video_name)
                    logger.info("  └─ " + t("services.summarization.summary_done", format=self.summary_format))

                if self.on_item_done:
                    self.on_item_done(video_name, summary if summary else "")

                return idx, summary if summary else ""
            except Exception as e:
                logger.info(t("services.summarization.summary_failed", current=idx + 1, total=total, video_name=video_name, error=e))
                if self.on_item_error:
                    self.on_item_error(video_name, str(e))
                return idx, ""
            finally:
                provider.close()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for idx, item in enumerate(items):
                if self.cancel_check and self.cancel_check():
                    break
                future = executor.submit(_process_item, idx, item)
                futures[future] = idx

            for future in as_completed(futures):
                try:
                    idx, summary = future.result()
                    results[idx] = summary
                except Exception as e:
                    idx = futures[future]
                    logger.error(t("services.summarization.thread_exception", idx=idx, error=e))
                    results[idx] = ""

        return [results.get(i, "") for i in range(len(items))]
