"""NvidiaClient 连接状态复用（类级缓存）单元测试。

覆盖：check_connection 结果在类级缓存中复用、失败不缓存、
过期后重新探测、不同配置隔离、generate 成功后刷新缓存。
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.summarization.nvidia_client import NvidiaClient
from src.utils.exceptions import SummarizationError


class FakeResponse:
    """模拟 requests.Response 的最小对象（支持 with 语句）。"""

    def __init__(
        self,
        status_code: int = 200,
        json_data: dict | None = None,
        headers: dict | None = None,
        text: str = "",
    ):
        self.status_code = status_code
        self._json = (
            json_data
            if json_data is not None
            else {"choices": [{"message": {"content": "ok"}}]}
        )
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._json

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


@pytest.fixture(autouse=True)
def _clean_cache():
    """每个用例前后清理类级缓存并恢复 TTL。"""
    NvidiaClient._connection_cache.clear()
    ttl = NvidiaClient._connection_cache_ttl
    yield
    NvidiaClient._connection_cache.clear()
    NvidiaClient._connection_cache_ttl = ttl


def _make_client(
    session: MagicMock,
    api_url: str = "https://api.nvidia.com/v1/chat/completions",
    api_key: str = "test-key",
    model: str = "openai/gpt-oss-120b",
) -> NvidiaClient:
    with patch("requests.Session", return_value=session):
        return NvidiaClient(api_url=api_url, api_key=api_key, model=model)


def test_check_connection_cached_between_instances():
    """第一次检查后，新的 client 实例应复用缓存，不再发请求。"""
    session = MagicMock()
    session.post.return_value = FakeResponse(200)
    c1 = _make_client(session)
    c2 = _make_client(session)

    assert c1.check_connection() is True
    assert c2.check_connection() is True
    assert session.post.call_count == 1


def test_check_connection_failure_not_cached():
    """失败结果不缓存，下次调用会重新探测。"""
    session = MagicMock()
    session.post.return_value = FakeResponse(500)
    c1 = _make_client(session)
    c2 = _make_client(session)
    c1.check_retries = 1
    c2.check_retries = 1

    assert c1.check_connection() is False
    assert c2.check_connection() is False
    assert session.post.call_count == 2


def _expire_cache(client: NvidiaClient) -> None:
    """把某 client 对应的缓存记录时间戳改为 1000 秒前，模拟已过期。"""
    key = (client.api_url, client._api_key, client._model)
    with NvidiaClient._connection_cache_lock:
        NvidiaClient._connection_cache[key] = (True, time.monotonic() - 1000)


def test_cache_expires_and_rechecks():
    """缓存过期后应重新发起连接检查。"""
    session = MagicMock()
    session.post.return_value = FakeResponse(200)
    c1 = _make_client(session)
    c2 = _make_client(session)

    assert c1.check_connection() is True
    _expire_cache(c1)
    assert c2.check_connection() is True
    assert session.post.call_count == 2


def test_cache_isolated_by_config():
    """不同 api_key / api_url / model 的 client 不共享缓存。"""
    session = MagicMock()
    session.post.return_value = FakeResponse(200)
    c1 = _make_client(session, api_key="key-a")
    c2 = _make_client(session, api_key="key-b")

    assert c1.check_connection() is True
    assert c2.check_connection() is True
    assert session.post.call_count == 2


def test_generate_success_refreshes_cache():
    """generate 成功后刷新缓存，后续 check_connection 直接命中。"""
    session = MagicMock()
    session.post.return_value = FakeResponse(200)
    c1 = _make_client(session)
    c2 = _make_client(session)

    assert c1.check_connection() is True
    _expire_cache(c1)

    assert c1.generate(model="openai/gpt-oss-120b", prompt="hi") == "ok"
    # generate 成功后缓存被刷新（新时间戳），不再发探测请求
    assert c2.check_connection() is True
    assert session.post.call_count == 2  # 1 次 check + 1 次 generate


def test_generate_missing_api_key_raises():
    """API Key 缺失时在 generate 入口立即拦截，抛出 SummarizationError。"""
    session = MagicMock()
    c = _make_client(session, api_key="placeholder")
    c._api_key = ""  # 强制置空，不受环境变量影响
    with pytest.raises(SummarizationError, match="API Key"):
        c.generate(prompt="hi")


def test_generate_401_invalidates_cache():
    """调用 generate 遇到 401 鉴权失败时，主动失效类级连接缓存。"""
    session = MagicMock()
    session.post.return_value = FakeResponse(200)
    c1 = _make_client(session)
    # 先探测成功，写入缓存
    assert c1.check_connection() is True
    assert c1._cached_connection() is True

    # generate 遇到 401
    session.post.return_value = FakeResponse(401, json_data={"error": "unauthorized"})
    with pytest.raises(SummarizationError):
        c1.generate(prompt="hi")

    # 缓存应已被清除
    assert c1._cached_connection() is None


def test_generate_5xx_retries_and_recovers():
    """5xx 服务端错误参与退避重试，在重试中恢复。"""
    session = MagicMock()
    # 第一次 503，第二次 200 成功
    resp_503 = FakeResponse(503, json_data={"error": "service unavailable"})
    resp_200 = FakeResponse(200, json_data={"choices": [{"message": {"content": "recovered"}}]})
    session.post.side_effect = [resp_503, resp_200]

    c = _make_client(session)
    with patch("time.sleep") as mock_sleep:
        res = c.generate(prompt="hi")
        assert res == "recovered"
        assert session.post.call_count == 2
        mock_sleep.assert_called_once()


def test_generate_4xx_does_not_retry():
    """4xx 客户端错误（除 429 外）不可重试，直接抛出。"""
    session = MagicMock()
    session.post.return_value = FakeResponse(400, json_data={"error": "bad request"})
    c = _make_client(session)

    with pytest.raises(SummarizationError):
        c.generate(prompt="hi")
    assert session.post.call_count == 1


class StreamingResponse:
    def __init__(self, lines):
        self.status_code = 200
        self.headers = {}
        self._lines = lines

    def iter_lines(self):
        for line in self._lines:
            yield line

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


def test_streaming_reasoning_fallback():
    """推理模型（如 GPT-OSS）流式返回 reasoning_content 时正常提取。"""
    session = MagicMock()
    lines = [
        b'data: {"choices":[{"delta":{"reasoning_content":"reasoning summary"}}]}',
        b'data: [DONE]',
    ]
    session.post.return_value = StreamingResponse(lines)
    c = _make_client(session)

    tokens = []
    res = c.generate(prompt="hi", stream=True, on_token=tokens.append)
    assert res == "reasoning summary"
    assert tokens == ["reasoning summary"]


def test_streaming_sse_no_space_and_safe_utf8():
    """SSE 协议放宽前缀 data:（无空格），并安全解码截断 UTF-8。"""
    session = MagicMock()
    # 包含无空格的 data: 以及非法字节 \xff（测试 errors='replace' 不崩溃）
    lines = [
        b'data:{"choices":[{"delta":{"content":"\xe4\xbd\xa0\xe5\xa5\xbd"}}]}',  # 你好
        b'data:{"choices":[{"delta":{"content":"\xffabc"}}]}',  # 包含替换字符
        b'data:[DONE]',
    ]
    session.post.return_value = StreamingResponse(lines)
    c = _make_client(session)

    res = c.generate(prompt="hi", stream=True)
    assert "你好" in res
    assert "abc" in res


def test_streaming_network_interruption_raises():
    """流式中途网络中断时抛出明确异常，禁止将残缺文字作为成功结果返回。"""
    session = MagicMock()

    class ErrorStreamingResponse:
        def __init__(self):
            self.status_code = 200
            self.headers = {}

        def iter_lines(self):
            yield b'data: {"choices":[{"delta":{"content":"part1"}}]}'
            raise requests.exceptions.ChunkedEncodingError("Connection broken halfway")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    session.post.return_value = ErrorStreamingResponse()
    c = _make_client(session)

    with pytest.raises(SummarizationError, match="流式"):
        c.generate(prompt="hi", stream=True)


def test_streaming_pause_event_response():
    """流式生成循环中支持响应 pause_event。"""
    session = MagicMock()
    pause_evt = threading.Event()
    pause_evt.set()  # 初始运行

    lines = [
        b'data: {"choices":[{"delta":{"content":"token1"}}]}',
        b'data: {"choices":[{"delta":{"content":"token2"}}]}',
        b'data: [DONE]',
    ]

    def line_generator():
        yield lines[0]
        # 产生一个 token 后暂停
        pause_evt.clear()

        # 后台 0.1 秒后恢复运行
        def resume():
            time.sleep(0.05)
            pause_evt.set()

        t = threading.Thread(target=resume)
        t.start()
        yield lines[1]
        yield lines[2]

    class PauseStreamingResponse:
        status_code = 200
        headers = {}
        def iter_lines(self):
            return line_generator()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None

    session.post.return_value = PauseStreamingResponse()
    c = _make_client(session)

    res = c.generate(prompt="hi", stream=True, pause_event=pause_evt)
    assert res == "token1token2"
