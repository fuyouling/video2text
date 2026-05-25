"""Provider 抽象层 —— 统一 Ollama / NVIDIA 等在线总结提供商的调用接口"""

from typing import Callable, Optional, Protocol

from src.config.settings import Settings
from src.summarization.nvidia_client import NvidiaClient
from src.summarization.ollama_client import OllamaClient
from src.summarization.summarizer import Summarizer
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
    ) -> str:
        """将文本转为总结"""
        ...

    def close(self) -> None:
        """释放底层 HTTP 连接等资源"""
        ...


def _build_prompt(text: str, custom_prompt: str = "") -> str:
    """构建完整的用户提示词

    包含默认 system prompt、Markdown 格式指令、用户文本。
    如果 custom_prompt 非空则替换默认 system prompt。
    """
    md_prompt = Summarizer.get_markdown_prompt()
    if custom_prompt and custom_prompt.strip():
        return f"{custom_prompt.strip()}\n\n{md_prompt}\n\n文本内容：\n{text}"
    else:
        default_prompt = (
            "你是一个专业的文本总结助手，擅长提取关键信息并生成简洁准确的总结。"
        )
        return f"{default_prompt}\n\n{md_prompt}\n\n文本内容：\n{text}"


class OllamaProvider:
    """Ollama 提供商 —— 本地模型总结"""

    def __init__(self, settings: Settings) -> None:
        ollama_url = settings.get("summarization.ollama_url", "http://127.0.0.1:11434")
        ollama_timeout = settings.get_int("summarization.timeout", 300)
        self._model_name = settings.get(
            "summarization.model_name", "qwen2.5:7b-instruct-q4_K_M"
        )
        self._temperature = settings.get_float("summarization.temperature", 0.7)
        self._max_length = settings.get_int("summarization.max_length", 5000)

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
    ) -> str:
        prompt = _build_prompt(text, custom_prompt)
        return self._client.generate(
            model=self._model_name,
            prompt=prompt,
            temperature=self._temperature,
            max_tokens=self._max_length,
            stream=stream,
            on_token=on_token,
        )

    def close(self) -> None:
        self._client.close()


class NvidiaProvider:
    """NVIDIA 提供商 —— 在线 API 总结"""

    def __init__(self, settings: Settings) -> None:
        nvidia_timeout = settings.get_int("summarization.timeout", 600)
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
            timeout=nvidia_timeout,
        )

    def check_connection(self) -> bool:
        return self._client.check_connection()

    def summarize(
        self,
        text: str,
        custom_prompt: str = "",
        stream: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> str:
        prompt = _build_prompt(text, custom_prompt)
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
        )

    def close(self) -> None:
        self._client.close()


def create_provider(settings: Settings) -> SummarizationProvider:
    """工厂函数 —— 根据配置创建对应的 Provider 实例"""
    provider_name = settings.get("summarization.provider", "ollama")
    if provider_name == "nvidia":
        return NvidiaProvider(settings)
    return OllamaProvider(settings)
