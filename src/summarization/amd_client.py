"""AMD GPU Cloud (Radeon Cloud) API client — using OpenAI-compatible chat/completions interface"""

import json as _json
import os
import threading
import time
from typing import Callable, Optional

import requests

from src.i18n import t
from src.utils.env_loader import ensure_env_loaded, get_api_key
from src.utils.exceptions import SummarizationError
from src.utils.logger import get_logger
from src.utils.paths import get_base_dir

logger = get_logger(__name__)


def _normalize_api_url(url: str) -> str:
    """规范化 API URL：若传入的是 base_url 则自动追加 /chat/completions。"""
    cleaned = (url or "").strip().rstrip("/")
    if not cleaned:
        cleaned = "https://developer.amd.com.cn/radeon/api/v1/chat/completions"
    elif not cleaned.endswith("/chat/completions"):
        cleaned = f"{cleaned}/chat/completions"
    return cleaned


class AmdClient:
    """AMD API client — calling AMD Radeon Cloud models via OpenAI-compatible interface"""

    # ── 连接状态复用（类级缓存） ──────────────────────────────────
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
        api_url: str = "https://developer.amd.com.cn/radeon/api/v1/chat/completions",
        api_key: Optional[str] = None,
        timeout: int = 60,
        model: str = "Qwen3.8-Flash-Next",
        check_retries: int = 2,
    ):
        self.api_url = _normalize_api_url(api_url)
        self.timeout = timeout
        self.max_retries = 3
        self.check_retries = check_retries
        self._model = model

        if not api_key:
            ensure_env_loaded()
        self._api_key = api_key or get_api_key("AMD_API_KEY") or os.environ.get("AMD_API_KEY", "")
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
        """Check if the AMD API is available via a minimal request.

        连接状态在类级缓存中复用（默认 300 秒），仅缓存成功结果，失败不缓存。
        """
        cached = self._cached_connection()
        if cached is True:
            logger.debug(t("services.summarization.amd.check_cached"))
            return True

        logger.info(t("services.summarization.amd.check_start"))
        if not self._api_key:
            logger.error(t("services.summarization.amd.api_key_missing"))
            env_path = get_base_dir() / ".env"
            if env_path.exists():
                try:
                    with open(env_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    if "AMD_API_KEY" not in content:
                        logger.warning(
                            t("services.summarization.amd.env_missing_key")
                        )
                except Exception as e:
                    logger.debug("AmdClient: read .env failed: %s", e)
            else:
                logger.warning(
                    t("services.summarization.amd.env_not_found")
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
                    t("services.summarization.amd.check_retry", attempt=attempt, max=self.check_retries, wait=wait),
                )
                time.sleep(wait)

        self._remember_connection(ok)
        return ok

    def _check_once(self) -> tuple[bool, bool]:
        """执行一次连接探测，返回 (是否成功, 是否可重试)。"""
        try:
            payload = {
                "model": self._model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
                "temperature": 1.0,
                "stream": False,
            }
            resp = self._session.post(self.api_url, json=payload, timeout=min(self.timeout, 10))
            ok = resp.status_code == 200
            if ok:
                logger.debug(t("services.summarization.amd.check_ok"))
                return True, False
            status = resp.status_code
            if status == 401:
                try:
                    error_detail = resp.json()
                    logger.error(
                        t("services.summarization.amd.check_fail_detail", code=status, detail=error_detail),
                    )
                except Exception:
                    logger.error(
                        t("services.summarization.amd.check_fail", code=status),
                    )
                return False, False
            try:
                error_detail = resp.json()
                logger.error(
                    t("services.summarization.amd.check_fail_detail", code=status, detail=error_detail),
                )
            except Exception:
                logger.error(
                    t("services.summarization.amd.check_fail", code=status),
                )
            return False, status >= 500
        except Exception as e:
            logger.error(
                t("services.summarization.amd.check_exception", error=e),
            )
            return False, True

    def generate(
        self,
        model: str = "Qwen3.8-Flash-Next",
        prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 8192,
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
            "stream": stream,
        }

        logger.debug(
            "AMD API request params: %s",
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
                            t("services.summarization.amd.rate_limited", wait=wait)
                        )
                        logger.warning(
                            t("services.summarization.amd.rate_limited_log", attempt=attempt, max=self.max_retries, wait=wait),
                        )
                        if attempt < self.max_retries:
                            time.sleep(wait)
                            continue
                        raise last_exc

                    if response.status_code != 200:
                        error_msg = t("services.summarization.amd.api_error", code=response.status_code)
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
                                result = (
                                    message.get("reasoning_content")
                                    or message.get("reasoning")
                                    or ""
                                )
                        else:
                            result = ""
                    self._remember_connection(True)
                    return result

            except requests.exceptions.Timeout:
                last_exc = SummarizationError(t("services.summarization.amd.request_timeout"))
                logger.warning(
                    "AmdClient: timeout (%d/%d)", attempt, self.max_retries
                )
            except requests.exceptions.ConnectionError as e:
                last_exc = SummarizationError(t("services.summarization.amd.connection_failed", error=e))
                logger.warning(
                    "AmdClient: connection failed (%d/%d): %s", attempt, self.max_retries, e
                )
            except SummarizationError:
                raise
            except Exception as e:
                last_exc = SummarizationError(t("services.summarization.amd.request_failed", error=e))
                logger.error("AmdClient: request exception (%s)", e)

            if attempt < self.max_retries:
                wait = 2**attempt
                logger.info(t("services.summarization.amd.retry_log", wait=wait))
                time.sleep(wait)

        raise last_exc or SummarizationError(t("services.summarization.amd.unknown_error"))

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
                    raise SummarizationError(t("services.summarization.amd.user_cancelled"))
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
                        raise SummarizationError(t("services.summarization.amd.stream_error", error=data["error"]))
                    choices = data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if not content and "reasoning_content" in delta:
                            content = delta.get("reasoning_content") or ""
                        if content:
                            full_text += content
                            if on_token:
                                on_token(content)
                except _json.JSONDecodeError:
                    logger.warning(t("services.summarization.amd.stream_json_error"))
                    continue
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ) as e:
            if full_text:
                logger.warning(
                    t("services.summarization.amd.stream_interrupted", count=len(full_text)),
                )
            else:
                raise SummarizationError(t("services.summarization.amd.stream_connection_failed", error=e)) from e
        return full_text
