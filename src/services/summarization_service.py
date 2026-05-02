"""总结服务 —— 统一 CLI / GUI 的总结逻辑，支持流式输出与多模型切换"""

from typing import Callable, List, Optional

from src.config.settings import Settings
from src.storage.file_writer import FileWriter
from src.summarization.ollama_client import OllamaClient
from src.summarization.summarizer import Summarizer
from src.utils.exceptions import SummarizationError
from src.utils.logger import get_logger, log_step

logger = get_logger(__name__)


class SummarizationService:
    """总结服务

    主要职责：
    1. 统一 CLI / GUI 的总结逻辑
    2. 支持流式输出（streaming）—— GUI 实时显示生成过程
    3. 支持多模型切换 —— 从 Ollama 获取可用模型列表
    """

    def __init__(
        self,
        settings: Settings,
        file_writer: FileWriter,
        *,
        model_name: Optional[str] = None,
        ollama_url: Optional[str] = None,
        temperature: Optional[float] = None,
        max_length: Optional[int] = None,
        custom_prompt: str = "",
        # 回调
        on_stream_token: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ):
        self.settings = settings
        self.file_writer = file_writer
        self.model_name = model_name or settings.get(
            "summarization.model_name", "qwen2.5:7b-instruct-q4_K_M"
        )
        self.ollama_url = ollama_url or settings.get(
            "summarization.ollama_url", "http://127.0.0.1:11434"
        )
        self.temperature = (
            temperature
            if temperature is not None
            else settings.get_float("summarization.temperature", 0.7)
        )
        self.max_length = (
            max_length
            if max_length is not None
            else settings.get_int("summarization.max_length", 5000)
        )
        self.custom_prompt = custom_prompt

        self.on_stream_token = on_stream_token
        self.on_progress = on_progress
        self.cancel_check = cancel_check

        ollama_timeout = settings.get_int("summarization.timeout", 300)
        self._client = OllamaClient(self.ollama_url, timeout=ollama_timeout)
        try:
            self._summarizer = Summarizer(
                model_name=self.model_name,
                ollama_url=self.ollama_url,
                temperature=self.temperature,
                max_length=self.max_length,
                client=self._client,
            )
        except Exception:
            self._client.close()
            raise

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def close(self) -> None:
        """关闭底层 HTTP 连接。"""
        self._client.close()

    def check_connection(self) -> bool:
        """检查 Ollama 连接。"""
        with log_step("Ollama 连接检查"):
            ok = self._client.check_connection()
            if not ok:
                logger.warning("Ollama 连接失败: %s", self.ollama_url)
            return ok

    def check_model(self) -> bool:
        """检查模型是否存在。"""
        with log_step(f"模型检查 ({self.model_name})"):
            try:
                models = self._client.list_models()
                exists = self.model_name in models
                if not exists:
                    logger.warning(
                        "模型 %s 不存在，可用模型: %s", self.model_name, models
                    )
                return exists
            except Exception as e:
                logger.error("检查模型失败: %s", e)
                return False

    def list_models(self) -> List[str]:
        """获取 Ollama 可用模型列表。"""
        return self._client.list_models()

    def summarize(
        self,
        text: str,
        *,
        video_name: str = "",
        stream: bool = False,
    ) -> str:
        """总结文本。

        Args:
            text: 待总结文本
            video_name: 视频名称（用于保存结果文件）
            stream: 是否流式输出

        Returns:
            总结文本
        """
        if not text or not text.strip():
            raise SummarizationError("输入文本为空")

        label = video_name or "(未命名)"

        with log_step(f"Ollama 生成总结 ({label})"):
            if stream:
                summary = self._summarize_streaming(text)
            else:
                summary = self._summarizer.summarize(
                    text,
                    max_length=self.max_length,
                    custom_prompt=self.custom_prompt or None,
                )

        if not summary or not summary.strip():
            raise SummarizationError("模型返回空总结")

        if video_name:
            with log_step(f"保存摘要 ({video_name})"):
                self.file_writer.write_summary(summary, video_name)

        self._log(f"✔ 总结完成: {label}")
        return summary

    def summarize_batch(
        self,
        items: List[dict],
        stream: bool = False,
    ) -> List[str]:
        """批量总结多个文本。

        Args:
            items: 列表，每项包含 {"text": str, "video_name": str}
            stream: 是否流式输出

        Returns:
            总结文本列表
        """
        results = []
        total = len(items)

        for idx, item in enumerate(items):
            if self.cancel_check and self.cancel_check():
                break

            video_name = item.get("video_name", f"item_{idx}")
            text = item.get("text", "")

            self._log(f"[{idx + 1}/{total}] 开始总结: {video_name}")

            try:
                summary = self.summarize(text, video_name=video_name, stream=stream)
                results.append(summary)
            except Exception as e:
                logger.error("总结失败 %s: %s", video_name, e)
                self._log(f"[{idx + 1}/{total}] 总结失败: {video_name} - {e}")
                results.append("")

        return results

    # ------------------------------------------------------------------
    # 流式总结
    # ------------------------------------------------------------------

    def _summarize_streaming(self, text: str) -> str:
        """流式总结文本，通过回调实时推送每个 token。"""
        prompt = self._summarizer.build_prompt(
            text, custom_prompt=self.custom_prompt or None
        )

        full_response = ""

        def on_token(token: str):
            nonlocal full_response
            full_response += token
            if self.on_stream_token:
                self.on_stream_token(token)

        try:
            self._client.generate(
                model=self.model_name,
                prompt=prompt,
                temperature=self.temperature,
                max_tokens=self.max_length,
                stream=True,
                on_token=on_token,
            )
        except Exception as e:
            raise SummarizationError(f"流式总结失败: {e}")

        return full_response.strip()

    def _log(self, message: str):
        logger.info(message)
        if self.on_progress:
            self.on_progress(message)
