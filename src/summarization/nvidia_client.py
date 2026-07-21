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

    def __init__(
        self,
        api_url: str = "https://integrate.api.nvidia.com/v1/chat/completions",
        api_key: Optional[str] = None,
        timeout: int = 60,
        model: str = "openai/gpt-oss-120b",
    ):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = 5
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
        """
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
            else:
                try:
                    error_detail = resp.json()
                    logger.error(
                        t("services.summarization.nvidia.check_fail_detail", code=resp.status_code, detail=error_detail),
                    )
                except Exception:
                    logger.error(
                        t("services.summarization.nvidia.check_fail", code=resp.status_code),
                    )
            return ok
        except Exception as e:
            logger.error(t("services.summarization.nvidia.check_error", error=e))
            return False

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
                        return self._handle_streaming(
                            response, on_token, cancel_check, pause_event
                        )
                    else:
                        data = response.json()
                        choices = data.get("choices", [])
                        if choices:
                            return choices[0].get("message", {}).get("content", "")
                        return ""

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
