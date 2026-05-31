"""Provider 抽象层 —— 统一 Ollama / NVIDIA / 智谱等在线总结提供商的调用接口"""

import os
import threading
from typing import Callable, Optional, Protocol

from src.config.settings import Settings
from src.summarization.prompt_manager import PromptManager
from src.summarization.nvidia_client import NvidiaClient
from src.summarization.ollama_client import OllamaClient
from src.summarization.zhipu_client import ZhipuClient
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SummarizationProvider(Protocol):
    """总结提供商协议 —— 所有 Provider 必须实现这三个方法"""

    def check_connection(self) -> bool:
        """检查提供商 API 是否可用"""
        ...

    def summarize(
        self,
        text: str,
        custom_prompt: str = "",
        stream: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_event: Optional[threading.Event] = None,
    ) -> str:
        """将文本转为总结"""
        ...

    def close(self) -> None:
        """释放底层 HTTP 连接等资源"""
        ...


class OllamaProvider:
    """Ollama 提供商 —— 本地模型总结"""

    def __init__(self, settings: Settings) -> None:
        ollama_url = settings.get("summarization.ollama_url", "http://127.0.0.1:11434")
        ollama_timeout = settings.get_int("summarization.timeout", 600)
        self._model_name = settings.get(
            "summarization.model_name", "qwen2.5:7b-instruct-q4_K_M"
        )
        self._temperature = settings.get_float("summarization.temperature", 0.7)
        self._max_length = settings.get_int("summarization.max_length", 10000)

        self._client = OllamaClient(ollama_url, timeout=ollama_timeout)

    def check_connection(self) -> bool:
        if not self._client.check_connection():
            return False
        return self._client.check_model(self._model_name)

    def summarize(
        self,
        text: str,
        custom_prompt: str = "",
        stream: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_event: Optional[threading.Event] = None,
    ) -> str:
        prompt = PromptManager().build_prompt(text, custom_prompt)
        return self._client.generate(
            model=self._model_name,
            prompt=prompt,
            temperature=self._temperature,
            max_tokens=self._max_length,
            stream=stream,
            on_token=on_token,
            cancel_check=cancel_check,
            pause_event=pause_event,
        )

    def close(self) -> None:
        self._client.close()


class NvidiaProvider:
    """NVIDIA 提供商 —— 在线 API 总结"""

    def __init__(self, settings: Settings) -> None:
        nvidia_timeout = settings.get_int("summarization.nvidia_timeout", 600)
        self._model = settings.get("summarization.nvidia_model", "openai/gpt-oss-120b")
        self._max_tokens = settings.get_int("summarization.nvidia_max_tokens", 100000)
        self._temperature = settings.get_float("summarization.nvidia_temperature", 1.0)
        self._top_p = settings.get_float("summarization.nvidia_top_p", 1.0)
        self._frequency_penalty = settings.get_float(
            "summarization.nvidia_frequency_penalty", 0.0
        )
        self._presence_penalty = settings.get_float(
            "summarization.nvidia_presence_penalty", 0.0
        )

        self._client = NvidiaClient(
            api_url=settings.get(
                "summarization.nvidia_api_url",
                "https://integrate.api.nvidia.com/v1/chat/completions",
            ),
            api_key=os.environ.get("NVIDIA_API_KEY", ""),
            timeout=nvidia_timeout,
            model=self._model,
        )

    def check_connection(self) -> bool:
        return self._client.check_connection()

    def summarize(
        self,
        text: str,
        custom_prompt: str = "",
        stream: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_event: Optional[threading.Event] = None,
    ) -> str:
        prompt = PromptManager().build_prompt(text, custom_prompt)
        return self._client.generate(
            model=self._model,
            prompt=prompt,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            top_p=self._top_p,
            frequency_penalty=self._frequency_penalty,
            presence_penalty=self._presence_penalty,
            stream=stream,
            on_token=on_token,
            cancel_check=cancel_check,
            pause_event=pause_event,
        )

    def close(self) -> None:
        self._client.close()


class ZhipuProvider:
    """智谱提供商 —— 在线 API 总结"""

    def __init__(self, settings: Settings) -> None:
        zhipu_timeout = settings.get_int("summarization.zhipu_timeout", 600)
        self._model = settings.get("summarization.zhipu_model", "glm-4.7")
        self._max_tokens = settings.get_int("summarization.zhipu_max_tokens", 65536)
        self._temperature = settings.get_float("summarization.zhipu_temperature", 1.0)

        self._client = ZhipuClient(
            api_key=os.environ.get("ZHIPU_API_KEY", ""),
            model=self._model,
            timeout=zhipu_timeout,
        )

    def check_connection(self) -> bool:
        return self._client.check_connection()

    def summarize(
        self,
        text: str,
        custom_prompt: str = "",
        stream: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_event: Optional[threading.Event] = None,
    ) -> str:
        prompt = PromptManager().build_prompt(text, custom_prompt)
        return self._client.generate(
            model=self._model,
            prompt=prompt,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            stream=stream,
            on_token=on_token,
            cancel_check=cancel_check,
            pause_event=pause_event,
        )

    def close(self) -> None:
        self._client.close()


def create_provider(settings: Settings) -> SummarizationProvider:
    """工厂函数 —— 根据配置创建对应的 Provider 实例"""
    provider_name = settings.get("summarization.provider", "ollama")
    if provider_name == "nvidia":
        return NvidiaProvider(settings)
    if provider_name == "zhipu":
        return ZhipuProvider(settings)
    if provider_name != "ollama":
        logger.warning("未知的总结提供商 '%s'，回退到 Ollama", provider_name)
    return OllamaProvider(settings)
