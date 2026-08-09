"""Mistral API client — using the official mistralai SDK"""

import threading
import time
from typing import Callable, Optional

from mistralai.client import Mistral
from mistralai.client.errors.sdkerror import SDKError

from src.i18n import t
from src.utils.env_loader import ensure_env_loaded, get_api_key
from src.utils.exceptions import SummarizationError
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MistralClient:
    """Mistral API client — calling Mistral models via the official mistralai SDK"""

    # ── 连接状态复用（类级缓存，与 NvidiaClient 同策略） ───────────
    _connection_cache: dict = {}
    _connection_cache_lock = threading.Lock()
    _connection_cache_ttl = 300.0  # 成功连接状态的缓存时长（秒）

    def _cache_key(self) -> tuple:
        return (self._api_key, self._model, self._base_url)

    def _cached_connection(self) -> Optional[bool]:
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
        with self._connection_cache_lock:
            if ok:
                self._connection_cache[self._cache_key()] = (True, time.monotonic())
            else:
                self._connection_cache.pop(self._cache_key(), None)

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: int = 30,
        model: str = "mistral-large-latest",
        base_url: Optional[str] = None,
    ):
        self.timeout = timeout
        self.max_retries = 3
        self._model = model
        self._base_url = base_url

        if not api_key:
            ensure_env_loaded()
        self._api_key = api_key or get_api_key("MISTRAL_API_KEY") or ""

        init_kwargs = {
            "api_key": self._api_key,
            # timeout 配置（秒）必须显式传给 SDK，SDK 接收毫秒且默认为 None，
            # 若不传则在网络异常/慢响应时会无限期阻塞，表现为"重新总结"卡死。
            "timeout_ms": int(self.timeout * 1000),
        }
        if base_url:
            init_kwargs["server_url"] = base_url
        self._client = Mistral(**init_kwargs)

    def check_connection(self) -> bool:
        """Check if the Mistral API is available via a minimal request.

        连接状态在类级缓存中复用（默认 300 秒），仅缓存成功结果，失败不缓存。
        """
        cached = self._cached_connection()
        if cached is True:
            logger.debug(t("services.summarization.mistral.check_cached"))
            return True

        logger.info(t("services.summarization.mistral.check_start"))
        if not self._api_key:
            logger.error(t("services.summarization.mistral.api_key_missing"))
            return False

        try:
            try:
                resp = self._client.chat.complete(
                    model=self._model,
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=1,
                    timeout_ms=int(self.timeout * 1000),
                )
                ok = bool(resp and getattr(resp, "choices", None))
            except SDKError as e:
                msg = str(e)
                if "Unexpected response received" in msg and "text/event-stream" in msg:
                    logger.debug(
                        "MistralClient: model %s forces stream in check, using chat.stream", self._model
                    )
                    stream_resp = self._client.chat.stream(
                        model=self._model,
                        messages=[{"role": "user", "content": "hi"}],
                        max_tokens=1,
                        timeout_ms=int(self.timeout * 1000),
                    )
                    ok = False
                    for event in stream_resp:
                        chunk = getattr(event, "data", None)
                        if chunk and getattr(chunk, "choices", None):
                            ok = True
                            break
                else:
                    raise
            if ok:
                logger.debug(t("services.summarization.mistral.check_ok"))
            else:
                logger.error(t("services.summarization.mistral.check_fail_empty"))
        except SDKError as e:
            status = getattr(e, "status_code", None)
            if status == 401:
                logger.error(t("services.summarization.mistral.api_key_invalid"))
            else:
                logger.error(t("services.summarization.mistral.check_error", error=e))
            ok = False
        except Exception as e:
            logger.error(t("services.summarization.mistral.check_error", error=e))
            ok = False
        self._remember_connection(ok)
        return ok

    def generate(
        self,
        model: str = "mistral-large-latest",
        prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 10000,
        stream: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_event: Optional[threading.Event] = None,
    ) -> str:
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                if stream:
                    # 流式：使用 chat.stream(...)，返回 EventStream[CompletionEvent]
                    stream_resp = self._client.chat.stream(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    result = self._handle_streaming(
                        stream_resp, on_token, cancel_check, pause_event,
                    )
                else:
                    # 非流式：使用 chat.complete(...)，返回 ChatCompletionResponse
                    # 部分模型（如 codestral）强制流式返回，此时 SDK 会抛
                    # "Unexpected response received: text/event-stream"，捕获后
                    # 回退到 chat.stream(...) 消费整段流。
                    try:
                        response = self._client.chat.complete(
                            model=model,
                            messages=[{"role": "user", "content": prompt}],
                            temperature=temperature,
                            max_tokens=max_tokens,
                        )
                        message = getattr(getattr(response, "choices", [])[0], "message", None)
                        result = getattr(message, "content", None) or ""
                    except SDKError as e:
                        msg = str(e)
                        if "Unexpected response received" in msg and "text/event-stream" in msg:
                            logger.debug(
                                "MistralClient: model %s forces stream, falling back to chat.stream", model
                            )
                            stream_resp = self._client.chat.stream(
                                model=model,
                                messages=[{"role": "user", "content": prompt}],
                                temperature=temperature,
                                max_tokens=max_tokens,
                            )
                            result = self._handle_streaming(
                                stream_resp, None, cancel_check, pause_event,
                            )
                        else:
                            raise

                self._remember_connection(True)
                return result

            except SDKError as e:
                status = getattr(e, "status_code", None)
                if status == 429:
                    wait = 2 ** (attempt + 1)
                    last_exc = SummarizationError(
                        t("services.summarization.mistral.rate_limited", wait=wait)
                    )
                    logger.warning(
                        t("services.summarization.mistral.rate_limited_log", attempt=attempt, max=self.max_retries, wait=wait),
                    )
                    if attempt < self.max_retries:
                        time.sleep(wait)
                        continue
                    raise last_exc
                error_msg = t("services.summarization.mistral.api_error", error=e)
                logger.error("%s", error_msg)
                raise SummarizationError(error_msg)
            except SummarizationError:
                raise
            except Exception as e:
                last_exc = SummarizationError(t("services.summarization.mistral.request_failed", error=e))
                logger.error("MistralClient: request exception (%s)", e)

            if attempt < self.max_retries:
                wait = 2 ** attempt
                logger.info(t("services.summarization.mistral.retry_log", wait=wait))
                time.sleep(wait)

        raise last_exc or SummarizationError(t("services.summarization.mistral.unknown_error"))

    def _handle_streaming(
        self,
        stream_resp,
        on_token: Optional[Callable[[str], None]],
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_event: Optional[threading.Event] = None,
    ) -> str:
        """Handle streaming response from mistralai SDK (chat.stream)."""
        full_text = ""
        try:
            for event in stream_resp:
                if cancel_check and cancel_check():
                    raise SummarizationError(t("services.summarization.mistral.user_cancelled"))
                if pause_event is not None and not pause_event.is_set():
                    while not pause_event.is_set():
                        if cancel_check and cancel_check():
                            raise SummarizationError(t("services.summarization.mistral.user_cancelled"))
                        time.sleep(0.1)
                try:
                    chunk = getattr(event, "data", None)
                    if chunk is None:
                        continue
                    # API 可能以 SSE 事件返回错误（如模型不存在 / 401 / 429），
                    # 此时 chunk 形如 {"error": {"message": ..., "type": ..., "code": ...}}。
                    # 必须立即抛出，否则会继续阻塞读取直到超时，表现为"卡死"。
                    err = None
                    if isinstance(chunk, dict):
                        err = chunk.get("error")
                    else:
                        err = getattr(chunk, "error", None)
                    if err:
                        msg = err.get("message") if isinstance(err, dict) else str(err)
                        raise SummarizationError(
                            t("services.summarization.mistral.api_error", error=msg)
                        )
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    delta = getattr(choices[0], "delta", None) or {}
                    content = getattr(delta, "content", None)
                    if content:
                        full_text += content
                        if on_token:
                            on_token(content)
                except SummarizationError:
                    raise
                except Exception as e:
                    logger.warning(t("services.summarization.mistral.stream_chunk_error", error=e))
                    continue
        except (SummarizationError,):
            raise
        except Exception as e:
            if full_text:
                logger.warning(t("services.summarization.mistral.stream_interrupted", count=len(full_text)))
            else:
                raise SummarizationError(t("services.summarization.mistral.stream_connection_failed", error=e)) from e
        return full_text

    def close(self) -> None:
        """Release the underlying SDK client."""
        self._client = None
