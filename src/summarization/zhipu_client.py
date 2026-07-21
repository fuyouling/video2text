"""Zhipu API client — calling GLM models via zai-sdk"""

import os
import threading
import time
from typing import Callable, Optional

from src.i18n import t
from src.utils.env_loader import ensure_env_loaded
from src.utils.exceptions import SummarizationError
from src.utils.logger import get_logger

logger = get_logger(__name__)


class CancelledError(SummarizationError):
    """User cancelled operation"""
    pass


class ZhipuClient:
    """Zhipu API client — calling GLM models via zai-sdk"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "glm-4.7",
        timeout: int = 60,
    ):
        from zai import ZhipuAiClient

        self._model = model
        self.timeout = timeout
        self.max_retries = 5

        if not api_key:
            ensure_env_loaded()
        self._api_key = api_key or os.environ.get("ZHIPU_API_KEY") or ""

        self._client = ZhipuAiClient(api_key=self._api_key, timeout=timeout)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def _is_thinking_model(self) -> bool:
        return self._model.lower() == "glm-4.7-flash"

    @staticmethod
    def _is_rate_limit(exc: BaseException) -> bool:
        try:
            from zai.core import APIReachLimitError, APIServerFlowExceedError

            return isinstance(exc, (APIReachLimitError, APIServerFlowExceedError))
        except ImportError:
            return "429" in str(exc)

    @staticmethod
    def _get_retry_after(exc: BaseException) -> Optional[int]:
        resp = getattr(exc, "response", None)
        if resp is None:
            return None
        val = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
        if val:
            try:
                return int(val)
            except ValueError:
                pass
        return None

    def check_connection(self) -> bool:
        if not self._api_key:
            logger.error(t("services.summarization.zhipu.api_key_missing"))
            return False

        for attempt in range(1, self.max_retries + 1):
            try:
                messages = self._build_messages("Hello, please reply OK")
                kwargs: dict = dict(
                    model=self._model,
                    messages=messages,
                    max_tokens=64,
                    stream=False,
                    timeout=self.timeout,
                )
                if self._is_thinking_model():
                    kwargs["temperature"] = 1.0
                    kwargs["thinking"] = {"type": "enabled"}
                response = self._client.chat.completions.create(**kwargs)
                ok = response is not None and len(response.choices) > 0
                logger.debug("ZhipuClient: check %s", t("services.summarization.zhipu.check_ok") if ok else "failed")
                return ok
            except Exception as e:
                if self._is_rate_limit(e):
                    wait = self._get_retry_after(e) or 2 ** (attempt + 1)
                    logger.warning(
                        t("services.summarization.zhipu.rate_limited_log", attempt=attempt, max=self.max_retries, wait=wait),
                    )
                    if attempt < self.max_retries:
                        time.sleep(wait)
                        continue
                logger.error(t("services.summarization.zhipu.check_fail", error=e))
                return False
        return False

    def generate(
        self,
        model: str = "glm-4.7",
        prompt: str = "",
        temperature: float = 1.0,
        max_tokens: int = 65536,
        stream: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_event: Optional[threading.Event] = None,
    ) -> str:
        logger.debug(
            "Zhipu API request: model=%s, temperature=%s, max_tokens=%s, stream=%s",
            model,
            temperature,
            max_tokens,
            stream,
        )

        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            if cancel_check and cancel_check():
                raise CancelledError(t("services.summarization.zhipu.user_cancelled"))

            try:
                if stream:
                    return self._generate_stream(
                        model,
                        prompt,
                        temperature,
                        max_tokens,
                        on_token,
                        cancel_check,
                        pause_event,
                    )
                else:
                    return self._generate_non_stream(
                        model, prompt, temperature, max_tokens
                    )
            except CancelledError:
                raise
            except Exception as e:
                from src.utils.rate_limit import exponential_backoff

                if self._is_rate_limit(e):
                    wait = self._get_retry_after(e) or 2 ** (attempt + 1)
                    last_exc = SummarizationError(
                        t("services.summarization.zhipu.rate_limited", wait=wait)
                    )
                    logger.warning(
                        t("services.summarization.zhipu.rate_limited_log", attempt=attempt, max=self.max_retries, wait=wait),
                    )
                    if attempt < self.max_retries:
                        for _ in range(wait):
                            if cancel_check and cancel_check():
                                raise CancelledError(t("services.summarization.zhipu.user_cancelled"))
                            time.sleep(1)
                        continue
                    raise last_exc
                last_exc = SummarizationError(t("services.summarization.zhipu.request_failed", error=e))
                logger.error("ZhipuClient: request exception (%s)", e)

            if attempt < self.max_retries:
                wait = 2**attempt
                logger.info(t("services.summarization.zhipu.retry_log", wait=wait))
                for _ in range(wait):
                    if cancel_check and cancel_check():
                        raise CancelledError(t("services.summarization.zhipu.user_cancelled"))
                    time.sleep(1)

        raise last_exc or SummarizationError(t("services.summarization.zhipu.unknown_error"))

    def _build_messages(self, prompt: str) -> list[dict]:
        if self._is_thinking_model():
            return [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": "好的，请提供需要总结的文本。"},
                {"role": "user", "content": "请根据以上系统提示词对文本进行总结。"},
            ]
        return [{"role": "user", "content": prompt}]

    def _generate_non_stream(
        self, model: str, prompt: str, temperature: float, max_tokens: int
    ) -> str:
        kwargs: dict = dict(
            model=model,
            messages=self._build_messages(prompt),
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            timeout=self.timeout,
        )
        if self._is_thinking_model():
            kwargs["thinking"] = {"type": "enabled"}
        response = self._client.chat.completions.create(**kwargs)
        choices = response.choices
        if choices:
            return choices[0].message.content or ""
        return ""

    def _generate_stream(
        self,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
        on_token: Optional[Callable[[str], None]],
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_event: Optional[threading.Event] = None,
    ) -> str:
        full_text = ""
        try:
            kwargs: dict = dict(
                model=model,
                messages=self._build_messages(prompt),
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                timeout=self.timeout,
            )
            if self._is_thinking_model():
                kwargs["thinking"] = {"type": "enabled"}
            response = self._client.chat.completions.create(**kwargs)
            for chunk in response:
                if cancel_check and cancel_check():
                    logger.info(t("services.summarization.zhipu.user_cancelled_stream"))
                    break
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    full_text += content
                    if on_token:
                        on_token(content)
        except CancelledError:
            raise
        except Exception as e:
            if full_text:
                logger.warning(
                    t("services.summarization.zhipu.stream_interrupted", count=len(full_text)),
                )
            else:
                raise SummarizationError(t("services.summarization.zhipu.stream_connection_failed", error=e)) from e
        return full_text
