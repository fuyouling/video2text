"""总结服务 —— 统一 CLI / GUI 的总结逻辑，支持流式输出与多模型切换"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, List, Optional

from src.config.settings import Settings
from src.storage.file_writer import FileWriter
from src.summarization.providers import SummarizationProvider, create_provider
from src.utils.exceptions import SummarizationError
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SummarizationService:
    """总结服务

    主要职责：
    1. 统一 CLI / GUI 的总结逻辑
    2. 支持流式输出（streaming）—— GUI 实时显示生成过程
    3. 支持多后端（Ollama / NVIDIA）via Provider 抽象层
    """

    def __init__(
        self,
        settings: Settings,
        file_writer: FileWriter,
        provider: SummarizationProvider,
        *,
        custom_prompt: str = "",
        on_stream_token: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ):
        self.settings = settings
        self.file_writer = file_writer
        self.provider = provider
        self.custom_prompt = custom_prompt

        self.on_stream_token = on_stream_token
        self.on_progress = on_progress
        self.cancel_check = cancel_check
        self.summary_format = (
            settings.get("output.summary_format", "txt").lower().strip()
        )

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def close(self) -> None:
        """关闭底层 Provider 连接。"""
        self.provider.close()

    def summarize(
        self,
        text: str,
        *,
        video_name: str = "",
        stream: bool = False,
        index: int = 0,
        total: int = 0,
    ) -> str:
        """总结文本。

        Args:
            text: 待总结文本
            video_name: 文件名称（用于保存结果文件）
            stream: 是否流式输出

        Returns:
            总结文本
        """
        if not text or not text.strip():
            raise SummarizationError("输入文本为空")

        label = video_name or "(未命名)"

        if total > 0:
            self._log(f"  ├─ 文本总结开始")

        def _on_token(token: str):
            if self.on_stream_token:
                self.on_stream_token(token)

        summary = self.provider.summarize(
            text,
            custom_prompt=self.custom_prompt or "",
            stream=stream,
            on_token=_on_token if stream else None,
            cancel_check=self.cancel_check,
        )

        if not summary or not summary.strip():
            raise SummarizationError("模型返回空总结")

        if video_name:
            self.file_writer.write_summary(summary, video_name, fmt=self.summary_format)

        if total > 0:
            self._log(f"  └─ 文本总结完成 ✓ (.{self.summary_format})")
        return summary

    def summarize_batch(
        self,
        items: List[dict],
        stream: bool = False,
        max_workers: int = 1,
    ) -> List[str]:
        """批量总结多个文本。

        Args:
            items: 列表，每项包含 {"text": str, "video_name": str}
            stream: 是否流式输出
            max_workers: 并发线程数，1 = 串行（兼容现有调用）

        Returns:
            总结文本列表（与输入顺序一致）
        """
        total = len(items)

        if max_workers <= 1:
            return self._summarize_batch_serial(items, stream, total)

        return self._summarize_batch_concurrent(items, stream, total, max_workers)

    def _summarize_batch_serial(
        self, items: List[dict], stream: bool, total: int
    ) -> List[str]:
        """串行批量总结，逐个调用 summarize() 并支持取消检查。"""
        results = []
        for idx, item in enumerate(items):
            if self.cancel_check and self.cancel_check():
                break

            video_name = item.get("video_name", f"item_{idx}")
            text = item.get("text", "")

            try:
                summary = self.summarize(
                    text,
                    video_name=video_name,
                    stream=stream,
                    index=idx + 1,
                    total=total,
                )
                results.append(summary)
            except Exception as e:
                logger.error("总结失败 %s: %s", video_name, e)
                self._log(f"[{idx + 1}/{total}] 总结失败: {video_name} - {e}")
                results.append("")

        return results

    def _summarize_batch_concurrent(
        self, items: List[dict], stream: bool, total: int, max_workers: int
    ) -> List[str]:
        """并发批量总结，每个线程创建独立的 Provider 实例，不支持流式输出。"""
        if stream:
            logger.warning("并发模式不支持流式输出，自动切换为非流式")

        results: dict[int, str] = {}
        progress_lock = threading.Lock()
        done_count = [0]

        def _process_item(idx: int, item: dict) -> tuple[int, str]:
            if self.cancel_check and self.cancel_check():
                return idx, ""

            video_name = item.get("video_name", f"item_{idx}")
            text = item.get("text", "")

            provider = create_provider(self.settings)
            try:
                if not text or not text.strip():
                    logger.warning("文本为空: %s", video_name)
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
                    self.file_writer.write_summary(
                        summary, video_name, fmt=self.summary_format
                    )

                with progress_lock:
                    done_count[0] += 1
                    current = done_count[0]
                self._log(
                    f"[{current}/{total}] 总结完成: {video_name} (.{self.summary_format})"
                )

                return idx, summary if summary else ""
            except Exception as e:
                logger.error("总结失败 %s: %s", video_name, e)
                with progress_lock:
                    done_count[0] += 1
                    current = done_count[0]
                self._log(f"[{current}/{total}] 总结失败: {video_name} - {e}")
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
                    logger.error("线程异常 item_%d: %s", idx, e)
                    results[idx] = ""

        return [results.get(i, "") for i in range(len(items))]

    def _log(self, message: str):
        """记录日志并通过回调通知调用方。"""
        logger.info(message)
        if self.on_progress:
            self.on_progress(message)
