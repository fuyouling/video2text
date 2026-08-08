"""NvidiaClient 连接状态复用（类级缓存）单元测试。

覆盖：check_connection 结果在类级缓存中复用、失败不缓存、
过期后重新探测、不同配置隔离、generate 成功后刷新缓存。
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from src.summarization.nvidia_client import NvidiaClient


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
