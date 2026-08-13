"""NVIDIA API client — using OpenAI-compatible chat/completions interface"""

import json as _json
import os
import threading
import time
from typing import Callable, Optional

import requests

from src.i18n import t
from src.utils.env_loader import ensure_env_loaded
from src.utils.exceptions import SummarizationError
from src.utils.logger import get_logger
from src.utils.paths import get_base_dir

logger = get_logger(__name__)


class NvidiaClient:
    """NVIDIA API client — calling NVIDIA models via OpenAI-compatible interface"""

    # ── 连接状态复用（类级缓存） ──────────────────────────────────
    # GUI 每次总结前都会调用 check_connection()（每次新建 client 实例），
    # 若每次都发探测请求会白白多一次网络往返。这里把连接检查结果缓存在
    # 类级别（跨实例共享），成功结果在 TTL 内直接复用；失败不缓存，
    # 以便网络恢复后能重新探测。generate() 成功时也会刷新缓存。
    _connection_cache: dict = {}  # key -> (ok, checked_at_monotonic)
    _connection_cache_lock = threading.Lock()
    _connection_cache_ttl = 300.0  # 成功连接状态的缓存时长（秒）

    def _cache_key(self) -> tuple:
        return (self.api_url, self._api_key, self._model)

    def _cached_connection(self) -> Optional[bool]:
        """返回缓存中的有效连接状态；无缓存或已过期返回 None。"""
        with self._connection_cache_lock:
            entry = self._connection_cache.get(self._cache_key())
            if not entry:
                return None
            ok, checked_at = entry
            if time.monotonic() - checked_at > self._connection_cache_ttl:
                self._connection_cache.pop(self._cache_key(), None)
                return None
            return ok

    def _remember_connection(self, ok: bool) -> None:
        """记录连接状态：成功结果入缓存；失败结果使缓存失效。"""
        with self._connection_cache_lock:
            if ok:
                self._connection_cache[self._cache_key()] = (True, time.monotonic())
            else:
                self._connection_cache.pop(self._cache_key(), None)

    def __init__(
        self,
        api_url: str = "https://integrate.api.nvidia.com/v1/chat/completions",
        api_key: Optional[str] = None,
        timeout: int = 30,
        model: str = "openai/gpt-oss-120b",
        check_retries: int = 3,
    ):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = 3
        self.check_retries = check_retries
        self._model = model

        if not api_key:
            ensure_env_loaded()
        self._api_key = api_key or os.environ.get("NVIDIA_API_KEY") or ""
        self._session = requests.Session()
        if self._api_key:
            self._session.headers.update({"Authorization": f"Bearer {self._api_key}"})
        self._session.headers.update({"Accept": "application/json"})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self) -> None:
        self._session.close()

    def check_connection(self) -> bool:
        """Check if the NVIDIA API is available.

        Reference: test_nvidia.py, sends a minimal request to verify connectivity and API Key.
        连接状态在类级缓存中复用（默认 300 秒），避免每次总结前重复探测；
        仅缓存成功结果，失败不缓存以便下次重新检查。
        """
        cached = self._cached_connection()
        if cached is True:
            logger.debug(t("services.summarization.nvidia.check_cached"))
            return True

        logger.info(t("services.summarization.nvidia.check_start"))
        if not self._api_key:
            logger.error(t("services.summarization.nvidia.api_key_missing"))
            env_path = get_base_dir() / ".env"
            if env_path.exists():
                try:
                    with open(env_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    if "NVIDIA_API_KEY" not in content:
                        logger.warning(
                            t("services.summarization.nvidia.env_missing_key")
                        )
                except Exception as e:
                    logger.debug("NvidiaClient: read .env failed: %s", e)
            else:
                logger.warning(
                    t("services.summarization.nvidia.env_not_found")
                )
            return False

        ok = False
        for attempt in range(1, self.check_retries + 1):
            ok, retryable = self._check_once()
            if ok or not retryable:
                break
            if attempt < self.check_retries:
                wait = 2 ** attempt
                logger.warning(
                    t("services.summarization.nvidia.check_retry", attempt=attempt, max=self.check_retries, wait=wait),
                )
                time.sleep(wait)

        self._remember_connection(ok)
        return ok

    def _check_once(self) -> tuple[bool, bool]:
        """执行一次连接探测，返回 (是否成功, 是否可重试)。

        401（鉴权失败）视为不可重试；其余（429 限流 / 5xx / 网关错误 /
        网络异常）视为瞬时故障，可重试。
        """
        try:
            payload = {
                "model": self._model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
                "temperature": 1.0,
                "stream": False,
            }
            resp = self._session.post(self.api_url, json=payload, timeout=self.timeout)
            ok = resp.status_code == 200
            if ok:
                logger.debug(t("services.summarization.nvidia.check_ok"))
                return True, False
            status = resp.status_code
            # 401 鉴权问题重试无意义，直接判定失败
            if status == 401:
                try:
                    error_detail = resp.json()
                    logger.error(
                        t("services.summarization.nvidia.check_fail_detail", code=status, detail=error_detail),
                    )
                except Exception:
                    logger.error(
                        t("services.summarization.nvidia.check_fail", code=status),
                    )
                return False, False
            # 其余（含 429/5xx）作为可重试的瞬时故障
            try:
                error_detail = resp.json()
                logger.warning(
                    t("services.summarization.nvidia.check_fail_detail", code=status, detail=error_detail),
                )
            except Exception:
                logger.warning(
                    t("services.summarization.nvidia.check_fail", code=status),
                )
            return False, True
        except Exception as e:
            logger.error(t("services.summarization.nvidia.check_error", error=e))
            return False, True

    def generate(
        self,
        model: str = "openai/gpt-oss-120b",
        prompt: str = "",
        temperature: float = 1.0,
        max_tokens: int = 100000,
        top_p: float = 1.0,
        frequency_penalty: float = 0.0,
        presence_penalty: float = 0.0,
        stream: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_event: Optional[threading.Event] = None,
    ) -> str:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "frequency_penalty": frequency_penalty,
            "presence_penalty": presence_penalty,
            "stream": stream,
        }

        logger.debug(
            "NVIDIA API request params: %s",
            {k: v for k, v in payload.items() if k != "messages"},
        )

        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._session.post(
                    self.api_url,
                    json=payload,
                    timeout=self.timeout,
                    stream=stream,
                )

                with response:
                    from src.utils.rate_limit import is_rate_limit, get_retry_after

                    if is_rate_limit(response):
                        retry_after = get_retry_after(dict(response.headers))
                        wait = retry_after if retry_after else 2 ** (attempt + 1)
                        last_exc = SummarizationError(
                            t("services.summarization.nvidia.rate_limited", wait=wait)
                        )
                        logger.warning(
                            t("services.summarization.nvidia.rate_limited_log", attempt=attempt, max=self.max_retries, wait=wait),
                        )
                        if attempt < self.max_retries:
                            time.sleep(wait)
                            continue
                        raise last_exc

                    if response.status_code != 200:
                        error_msg = t("services.summarization.nvidia.api_error", code=response.status_code)
                        try:
                            error_detail = response.json()
                            error_msg += f", {error_detail}"
                        except Exception:
                            error_msg += f", {response.text[:500]}"
                        logger.error("%s", error_msg)
                        raise SummarizationError(error_msg)

                    if stream:
                        result = self._handle_streaming(
                            response, on_token, cancel_check, pause_event
                        )
                    else:
                        data = response.json()
                        choices = data.get("choices", [])
                        if choices:
                            message = choices[0].get("message", {}) or {}
                            result = message.get("content") or ""
                            if not result:
                                # 部分推理模型（如 openai/gpt-oss）把最终答案放在
                                # reasoning_content，而 message.content 为 null；
                                # 这里回退，避免把可用模型误判为「空响应」。
                                result = (
                                    message.get("reasoning_content")
                                    or message.get("reasoning")
                                    or ""
                                )
                        else:
                            result = ""
                    # 请求成功说明连接可用，刷新类级连接缓存
                    self._remember_connection(True)
                    return result

            except requests.exceptions.Timeout:
                last_exc = SummarizationError(t("services.summarization.nvidia.request_timeout"))
                logger.warning(
                    "NvidiaClient: timeout (%d/%d)", attempt, self.max_retries
                )
            except requests.exceptions.ConnectionError as e:
                last_exc = SummarizationError(t("services.summarization.nvidia.connection_failed", error=e))
                logger.warning(
                    "NvidiaClient: connection failed (%d/%d): %s", attempt, self.max_retries, e
                )
            except SummarizationError:
                raise
            except Exception as e:
                last_exc = SummarizationError(t("services.summarization.nvidia.request_failed", error=e))
                logger.error("NvidiaClient: request exception (%s)", e)

            if attempt < self.max_retries:
                wait = 2**attempt
                logger.info(t("services.summarization.nvidia.retry_log", wait=wait))
                time.sleep(wait)

        raise last_exc or SummarizationError(t("services.summarization.nvidia.unknown_error"))

    def _handle_streaming(
        self,
        response: requests.Response,
        on_token: Optional[Callable[[str], None]],
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_event: Optional[threading.Event] = None,
    ) -> str:
        """Handle streaming response"""
        full_text = ""
        try:
            for line in response.iter_lines():
                if cancel_check and cancel_check():
                    raise SummarizationError(t("services.summarization.nvidia.user_cancelled"))
                if not line:
                    continue
                decoded = line.decode("utf-8")
                if not decoded.startswith("data: "):
                    continue
                data_str = decoded[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    data = _json.loads(data_str)
                    if "error" in data:
                        raise SummarizationError(t("services.summarization.nvidia.stream_error", error=data["error"]))
                    choices = data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            full_text += content
                            if on_token:
                                on_token(content)
                except _json.JSONDecodeError:
                    logger.warning(t("services.summarization.nvidia.stream_json_error"))
                    continue
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ) as e:
            if full_text:
                logger.warning(
                    t("services.summarization.nvidia.stream_interrupted", count=len(full_text)),
                )
            else:
                raise SummarizationError(t("services.summarization.nvidia.stream_connection_failed", error=e)) from e
        return full_text
