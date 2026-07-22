"""Provider abstraction layer — unified interface for Ollama / NVIDIA summarization providers"""

import os
import threading
from typing import Callable, Optional, Protocol

from src.config.settings import Settings
from src.i18n import t
from src.summarization.prompt_manager import PromptManager
from src.summarization.nvidia_client import NvidiaClient
from src.summarization.ollama_client import OllamaClient
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SummarizationProvider(Protocol):
    """Provider protocol — all providers must implement these three methods"""

    def check_connection(self) -> bool:
        """Check if the provider API is available"""

    def summarize(
        self,
        text: str,
        custom_prompt: str = "",
        stream: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_event: Optional[threading.Event] = None,
        is_use_gui_markdown_flag: bool = True
    ) -> str:
        """Summarize the given text"""

    def close(self) -> None:
        """Release underlying HTTP connections etc."""


class OllamaProvider:
    """Ollama provider — local model summarization"""

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
        is_use_gui_markdown_flag: bool = True
    ) -> str:
        prompt = PromptManager().build_prompt(text, custom_prompt, is_use_gui_markdown_flag=is_use_gui_markdown_flag)
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
    """NVIDIA provider — online API summarization"""

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
        is_use_gui_markdown_flag: bool = True
    ) -> str:
        prompt = PromptManager().build_prompt(text, custom_prompt, is_use_gui_markdown_flag=is_use_gui_markdown_flag)
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


def create_provider(settings: Settings) -> SummarizationProvider:
    """Factory function — creates the appropriate provider based on config"""
    provider_name = settings.get("summarization.provider", "ollama")
    if provider_name == "nvidia":
        return NvidiaProvider(settings)
    if provider_name != "ollama":
        logger.warning(t("services.summarization.unknown_provider", provider=provider_name))
    return OllamaProvider(settings)
