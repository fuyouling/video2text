"""Provider 层单元测试（mock HTTP 响应）"""

from unittest.mock import MagicMock, patch

import pytest

from src.config.settings import PromptManager
from src.summarization.providers import (
    OllamaProvider,
    NvidiaProvider,
    create_provider,
)


class TestBuildPrompt:
    def test_default_prompt(self):
        pm = PromptManager()
        pm._markdown_enabled = True
        result = pm.build_prompt("测试文本")
        assert "专业的文本总结助手" in result
        assert "Markdown" in result
        assert "测试文本" in result

    def test_custom_prompt(self):
        pm = PromptManager()
        pm._markdown_enabled = True
        result = pm.build_prompt("测试文本", custom_prompt="自定义指令")
        assert "自定义指令" in result
        assert "测试文本" in result
        assert "专业的文本总结助手" not in result

    def test_empty_custom_prompt_uses_default(self):
        pm = PromptManager()
        pm._markdown_enabled = True
        result = pm.build_prompt("测试文本", custom_prompt="  ")
        assert "专业的文本总结助手" in result

    def test_markdown_prompt_contains_format(self):
        prompt = PromptManager().get_markdown_prompt()
        assert "Markdown" in prompt
        assert "**要点标题**" in prompt

    def test_build_prompt_markdown_disabled(self):
        pm = PromptManager()
        pm._markdown_enabled = False
        result = pm.build_prompt("测试文本")
        assert "专业的文本总结助手" in result
        assert "Markdown" not in result
        assert "测试文本" in result

    def test_build_prompt_empty_markdown_prompt(self):
        pm = PromptManager()
        pm._markdown_enabled = True
        pm._markdown_prompt = ""
        result = pm.build_prompt("测试文本")
        assert "专业的文本总结助手" in result
        assert "Markdown" not in result
        assert "测试文本" in result

    def test_build_prompt_custom_markdown(self):
        pm = PromptManager()
        pm._markdown_enabled = True
        pm._markdown_prompt = "自定义markdown指令"
        result = pm.build_prompt("测试文本")
        assert "自定义markdown指令" in result
        assert "测试文本" in result


class TestOllamaProvider:
    @patch("src.summarization.providers.OllamaClient")
    def test_check_connection_success(self, MockClient):
        settings = MagicMock()
        settings.get.side_effect = lambda key, default="": {
            "summarization.ollama_url": "http://localhost:11434",
            "summarization.model_name": "qwen2.5:7b",
        }.get(key, default)
        settings.get_int.return_value = 600
        settings.get_float.return_value = 0.7

        provider = OllamaProvider(settings)
        provider._client.check_connection.return_value = True
        provider._client.check_model.return_value = True
        assert provider.check_connection() is True

    @patch("src.summarization.providers.OllamaClient")
    def test_check_connection_failure(self, MockClient):
        settings = MagicMock()
        settings.get.side_effect = lambda key, default="": {
            "summarization.ollama_url": "http://localhost:11434",
            "summarization.model_name": "qwen2.5:7b",
        }.get(key, default)
        settings.get_int.return_value = 600
        settings.get_float.return_value = 0.7

        provider = OllamaProvider(settings)
        provider._client.check_connection.return_value = False
        assert provider.check_connection() is False

    @patch("src.summarization.providers.OllamaClient")
    def test_summarize(self, MockClient):
        settings = MagicMock()
        settings.get.side_effect = lambda key, default="": {
            "summarization.ollama_url": "http://localhost:11434",
            "summarization.model_name": "qwen2.5:7b",
        }.get(key, default)
        settings.get_int.return_value = 600
        settings.get_float.return_value = 0.7

        provider = OllamaProvider(settings)
        provider._client.generate.return_value = "总结结果"
        result = provider.summarize("长文本")
        assert result == "总结结果"


class TestNvidiaProvider:
    @patch("src.summarization.providers.NvidiaClient")
    def test_check_connection(self, MockClient):
        settings = MagicMock()
        settings.get.side_effect = lambda key, default="": {
            "summarization.nvidia_model": "openai/gpt-oss-120b",
            "summarization.nvidia_api_url": "https://api.nvidia.com",
        }.get(key, default)
        settings.get_int.return_value = 600
        settings.get_float.return_value = 1.0

        provider = NvidiaProvider(settings)
        provider._client.check_connection.return_value = True
        assert provider.check_connection() is True

    @patch("src.summarization.providers.NvidiaClient")
    def test_summarize(self, MockClient):
        settings = MagicMock()
        settings.get.side_effect = lambda key, default="": {
            "summarization.nvidia_model": "openai/gpt-oss-120b",
            "summarization.nvidia_api_url": "https://api.nvidia.com",
        }.get(key, default)
        settings.get_int.return_value = 600
        settings.get_float.return_value = 1.0

        provider = NvidiaProvider(settings)
        provider._client.generate.return_value = "NVIDIA总结"
        result = provider.summarize("文本")
        assert result == "NVIDIA总结"


class TestCreateProvider:
    def test_create_ollama_provider(self):
        settings = MagicMock()
        settings.get.side_effect = lambda key, default="": {
            "summarization.provider": "ollama",
            "summarization.ollama_url": "http://localhost:11434",
            "summarization.model_name": "qwen2.5:7b",
        }.get(key, default)
        settings.get_int.return_value = 600
        settings.get_float.return_value = 0.7
        provider = create_provider(settings)
        assert isinstance(provider, OllamaProvider)

    def test_create_nvidia_provider(self):
        settings = MagicMock()
        settings.get.side_effect = lambda key, default="": {
            "summarization.provider": "nvidia",
            "summarization.nvidia_model": "openai/gpt-oss-120b",
            "summarization.nvidia_api_url": "https://api.nvidia.com",
        }.get(key, default)
        settings.get_int.return_value = 600
        settings.get_float.return_value = 1.0
        provider = create_provider(settings)
        assert isinstance(provider, NvidiaProvider)

    def test_unknown_provider_falls_back_to_ollama(self):
        settings = MagicMock()
        settings.get.side_effect = lambda key, default="": {
            "summarization.provider": "unknown",
            "summarization.ollama_url": "http://localhost:11434",
            "summarization.model_name": "qwen2.5:7b",
        }.get(key, default)
        settings.get_int.return_value = 600
        settings.get_float.return_value = 0.7
        provider = create_provider(settings)
        assert isinstance(provider, OllamaProvider)
