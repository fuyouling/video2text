"""智谱 API 客户端 —— 使用 zai-sdk 调用 GLM 模型"""

import os
import threading
import time
from typing import Callable, Optional

from src.utils.env_loader import ensure_env_loaded
from src.utils.exceptions import SummarizationError
from src.utils.logger import get_logger

logger = get_logger(__name__)


class CancelledError(SummarizationError):
    """用户取消操作"""

    pass


class ZhipuClient:
    """智谱 API 客户端 —— 通过 zai-sdk 调用 GLM 模型"""

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
            logger.error("ZhipuClient: ✗ API Key 未配置")
            return False

        for attempt in range(1, self.max_retries + 1):
            try:
                messages = self._build_messages("你好，请回复OK")
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
                logger.debug("ZhipuClient: 连接检查 %s", "成功" if ok else "失败")
                return ok
            except Exception as e:
                if self._is_rate_limit(e):
                    wait = self._get_retry_after(e) or 2 ** (attempt + 1)
                    logger.warning(
                        "ZhipuClient: ⚠ 429 限流 (%d/%d)，等待 %ds",
                        attempt,
                        self.max_retries,
                        wait,
                    )
                    if attempt < self.max_retries:
                        time.sleep(wait)
                        continue
                logger.error("ZhipuClient: ✗ 连接失败 (%s)", e)
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
            "智谱 API 请求参数: model=%s, temperature=%s, max_tokens=%s, stream=%s",
            model,
            temperature,
            max_tokens,
            stream,
        )

        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            if cancel_check and cancel_check():
                raise CancelledError("用户取消")

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
                        f"智谱 API 限流 (429), {wait}s 后重试"
                    )
                    logger.warning(
                        "ZhipuClient: ⚠ 429 限流 (%d/%d)，等待 %ds",
                        attempt,
                        self.max_retries,
                        wait,
                    )
                    if attempt < self.max_retries:
                        for _ in range(wait):
                            if cancel_check and cancel_check():
                                raise CancelledError("用户取消")
                            time.sleep(1)
                        continue
                    raise last_exc
                last_exc = SummarizationError(f"智谱 API 请求失败: {e}")
                logger.error("ZhipuClient: ✗ 请求异常 (%s)", e)

            if attempt < self.max_retries:
                wait = 2**attempt
                logger.info("ZhipuClient: %ds 后重试...", wait)
                for _ in range(wait):
                    if cancel_check and cancel_check():
                        raise CancelledError("用户取消")
                    time.sleep(1)

        raise last_exc or SummarizationError("智谱 API 请求失败（未知错误）")

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
                    logger.info("ZhipuClient: 用户取消流式生成")
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
                    "ZhipuClient: ⚠ 流式中断，已接收 %d 字符", len(full_text)
                )
            else:
                raise SummarizationError(f"智谱 流式连接失败: {e}") from e
        return full_text
