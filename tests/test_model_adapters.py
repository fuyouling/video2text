"""Unit tests for Wave 2 Track F: Model adapter fixes (Mistral, Ollama, AMD, NVIDIA, Providers)"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.summarization.amd_client import AmdClient
from src.summarization.mistral_client import MistralClient, SDKError
from src.summarization.ollama_client import OllamaClient
from src.summarization.providers import (
    AmdProvider,
    MistralProvider,
    NvidiaProvider,
    OllamaProvider,
)
from src.utils.exceptions import SummarizationError


def _make_sdk_error(message: str, status_code: int = 500, raw_response=None) -> SDKError:
    """创建跨版本兼容的 SDKError 异常实例，规避 mistralai 构造函数参数要求。"""
    try:
        err = SDKError.__new__(SDKError)
        Exception.__init__(err, message)
    except Exception:
        err = Exception(message)
    err.message = message
    err.body = message
    err.status_code = status_code
    err.raw_response = raw_response
    if raw_response and hasattr(raw_response, "headers"):
        err.headers = raw_response.headers
    return err


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {"choices": [{"message": {"content": "ok"}}]}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._json

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


# ──────────────────────────────────────────────────────────────────
# 1. MistralClient Tests
# ──────────────────────────────────────────────────────────────────

class TestMistralClientFixes:
    def test_mistral_none_raises_summarization_error(self):
        """Mistral is None 时抛出友好的 SummarizationError，杜绝原生 TypeError。"""
        with patch("src.summarization.mistral_client.Mistral", None):
            with pytest.raises(SummarizationError, match="mistralai SDK"):
                MistralClient(api_key="test-key")

    def test_generate_missing_api_key(self):
        """API Key 缺失时在 generate 入口拦截。"""
        with patch("src.summarization.mistral_client.Mistral"):
            client = MistralClient(api_key="placeholder")
            client._api_key = ""
            with pytest.raises(SummarizationError, match="API Key"):
                client.generate(prompt="hi")

    def test_rate_limit_retry_after_and_backoff(self):
        """429 限流解析 Retry-After 并引入指数退避。"""
        mock_sdk = MagicMock()
        mock_client = MagicMock()
        mock_sdk.return_value = mock_client

        # from mistralai SDKError
        mock_resp = MagicMock()
        mock_resp.headers = {"Retry-After": "0.1"}
        err = _make_sdk_error("Rate limited", status_code=429, raw_response=mock_resp)

        # 第一次抛 429，第二次成功
        success_resp = MagicMock()
        success_resp.choices = [MagicMock(message=MagicMock(content="mistral summary"))]
        mock_client.chat.complete.side_effect = [err, success_resp]

        with patch("src.summarization.mistral_client.Mistral", mock_sdk):
            client = MistralClient(api_key="valid-key")
            client.max_retries = 3
            with patch("time.sleep") as mock_sleep:
                res = client.generate(prompt="hi")
                assert res == "mistral summary"
                mock_sleep.assert_called_once()
                # 确认等待时间解析了 Retry-After (0.1s)
                assert mock_sleep.call_args[0][0] == 0.1

    def test_fallback_streaming_preserves_chat_messages(self):
        """模型强制流式回退时，保持完整的 chat_messages（含系统提示词）。"""
        mock_sdk = MagicMock()
        mock_client = MagicMock()
        mock_sdk.return_value = mock_client

        stream_force_err = _make_sdk_error("Unexpected response received: text/event-stream")
        mock_client.chat.complete.side_effect = stream_force_err

        # 模拟流式事件
        chunk_event = MagicMock()
        chunk_data = MagicMock()
        chunk_data.error = None
        choice = MagicMock()
        choice.delta = MagicMock(content="fallback chunk")
        chunk_data.choices = [choice]
        chunk_event.data = chunk_data
        mock_client.chat.stream.return_value = [chunk_event]

        with patch("src.summarization.mistral_client.Mistral", mock_sdk):
            client = MistralClient(api_key="valid-key")
            messages = [
                {"role": "system", "content": "You are a summarizer"},
                {"role": "user", "content": "The main content"},
            ]
            res = client.generate(messages=messages, stream=False)
            assert res == "fallback chunk"

            # 验证 stream 收到的是完整的 chat_messages，而非单纯用户 prompt
            call_kwargs = mock_client.chat.stream.call_args[1]
            assert call_kwargs["messages"] == messages


# ──────────────────────────────────────────────────────────────────
# 2. OllamaClient Tests
# ──────────────────────────────────────────────────────────────────

class TestOllamaClientFixes:
    def test_dynamic_num_ctx_calculation(self):
        """动态计算 options["num_ctx"] (max(4096, min(len(prompt) + 2048, 32768)))。"""
        client = OllamaClient()

        recorded_payloads = []

        def fake_post_with_retry(url, json, timeout, stream=False):
            recorded_payloads.append(json)
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"response": "ollama summary"}
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = None
            return resp

        client._post_with_retry = fake_post_with_retry

        # Case 1: 短 prompt (10 字符) -> max(4096, 2058) = 4096
        client.generate(model="qwen2.5:7b", prompt="hello")
        assert recorded_payloads[-1]["options"]["num_ctx"] == 4096

        # Case 2: 中长 prompt (5000 字符) -> max(4096, min(7048, 32768)) = 7048
        client.generate(model="qwen2.5:7b", prompt="a" * 5000)
        assert recorded_payloads[-1]["options"]["num_ctx"] == 7048

        # Case 3: 超长 prompt (40000 字符) -> 上限 32768
        client.generate(model="qwen2.5:7b", prompt="a" * 40000)
        assert recorded_payloads[-1]["options"]["num_ctx"] == 32768

    def test_post_with_retry_streaming_connection_retry(self):
        """_post_with_retry 允许流式请求在连接建立阶段重试。"""
        client = OllamaClient()
        client.max_retries = 2

        mock_session = MagicMock()
        client._session = mock_session

        # 模拟第一次连接异常，第二次成功
        success_resp = MagicMock(status_code=200)
        mock_session.post.side_effect = [
            requests.exceptions.ConnectionError("Connection refused"),
            success_resp,
        ]

        with patch("time.sleep") as mock_sleep:
            resp = client._post_with_retry("http://localhost:11434/api/generate", {}, timeout=10, stream=True)
            assert resp == success_resp
            assert mock_session.post.call_count == 2
            mock_sleep.assert_called_once()

    def test_ollama_streaming_pause_event(self):
        """Ollama 流式输出循环中支持响应 pause_event。"""
        client = OllamaClient()
        pause_evt = threading.Event()
        pause_evt.set()

        lines = [
            b'{"response":"part1","done":false}',
            b'{"response":"part2","done":true}',
        ]

        def line_iter():
            yield lines[0]
            pause_evt.clear()

            def resume():
                time.sleep(0.05)
                pause_evt.set()

            threading.Thread(target=resume).start()
            yield lines[1]

        mock_resp = MagicMock(status_code=200)
        mock_resp.iter_lines.return_value = line_iter()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None
        client._post_with_retry = MagicMock(return_value=mock_resp)

        res = client.generate(model="test", prompt="hi", stream=True, pause_event=pause_evt)
        assert res == "part1part2"


# ──────────────────────────────────────────────────────────────────
# 3. AmdClient Tests
# ──────────────────────────────────────────────────────────────────

class TestAmdClientFixes:
    def test_generate_missing_api_key(self):
        """AMD API Key 缺失在 generate 入口抛出 SummarizationError。"""
        client = AmdClient(api_key="placeholder")
        client._api_key = ""
        with pytest.raises(SummarizationError, match="API Key"):
            client.generate(prompt="hi")

    def test_amd_401_invalidates_cache(self):
        """AMD generate 发生 401 致命鉴权错误时，主动失效类级连接缓存。"""
        session = MagicMock()
        session.post.return_value = FakeResponse(200)
        with patch("requests.Session", return_value=session):
            client = AmdClient(api_key="valid-key")
            assert client.check_connection() is True
            assert client._cached_connection() is True

            session.post.return_value = FakeResponse(401, json_data={"error": "unauthorized"})
            with pytest.raises(SummarizationError):
                client.generate(prompt="hi")

            assert client._cached_connection() is None

    def test_amd_5xx_retries_and_recovers(self):
        """AMD 服务端 502 错误参与退避重试，随后恢复成功。"""
        session = MagicMock()
        resp_502 = FakeResponse(502, text="Bad Gateway")
        resp_200 = FakeResponse(200, json_data={"choices": [{"message": {"content": "amd recovered"}}]})
        session.post.side_effect = [resp_502, resp_200]

        with patch("requests.Session", return_value=session):
            client = AmdClient(api_key="valid-key")
            with patch("time.sleep"):
                res = client.generate(prompt="hi")
                assert res == "amd recovered"
                assert session.post.call_count == 2

    def test_amd_streaming_reasoning_and_safe_utf8(self):
        """AMD 流式解析提取 reasoning_content 回退，并安全处理 SSE data: 前缀。"""
        session = MagicMock()
        lines = [
            b'data:{"choices":[{"delta":{"reasoning_content":"amd reason "}}]}',
            b'data:{"choices":[{"delta":{"content":"amd content"}}]}',
            b'data:[DONE]',
        ]

        class StreamResp:
            status_code = 200
            headers = {}
            def iter_lines(self):
                for l in lines:
                    yield l
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return None

        session.post.return_value = StreamResp()
        with patch("requests.Session", return_value=session):
            client = AmdClient(api_key="valid-key")
            res = client.generate(prompt="hi", stream=True)
            assert res == "amd reason amd content"


# ──────────────────────────────────────────────────────────────────
# 4. Providers Layer Tests
# ──────────────────────────────────────────────────────────────────

class TestProvidersConsistency:
    def test_is_use_gui_markdown_flag_forwarded(self):
        """所有 Provider 均一致接收并传递 is_use_gui_markdown_flag 参数。"""
        settings = MagicMock()
        settings.get.return_value = "http://localhost:11434"
        settings.get_int.return_value = 30
        settings.get_float.return_value = 0.7

        with patch("src.summarization.prompt_manager.PromptManager.build_messages") as mock_build:
            mock_build.return_value = [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "usr"},
            ]
            ollama_prov = OllamaProvider(settings)
            ollama_prov._client = MagicMock()
            ollama_prov._client.generate.return_value = "res"

            ollama_prov.summarize("test text", is_use_gui_markdown_flag=False)
            mock_build.assert_called_with(
                "test text",
                custom_prompt="",
                is_use_gui_markdown_flag=False,
            )
