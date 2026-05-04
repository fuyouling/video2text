"""NVIDIA API 客户端 —— 使用 OpenAI 兼容的 chat/completions 接口"""

import json as _json
import os
import time
from pathlib import Path
from typing import Callable, Optional

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from src.config.settings import (
    DEFAULT_NVIDIA_API_URL,
    DEFAULT_NVIDIA_MODEL,
    DEFAULT_NVIDIA_MAX_TOKENS,
    DEFAULT_NVIDIA_TEMPERATURE,
    DEFAULT_NVIDIA_TOP_P,
)
from src.utils.exceptions import SummarizationError
from src.utils.logger import get_logger

logger = get_logger(__name__)


class NvidiaClient:
    """NVIDIA API 客户端 —— 通过 OpenAI 兼容接口调用 NVIDIA 模型"""

    def __init__(
        self,
        api_url: str = DEFAULT_NVIDIA_API_URL,
        api_key: Optional[str] = None,
        timeout: int = 600,
    ):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = 3

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
        """检查 NVIDIA API 是否可用。

        参考 test_nvidia.py，发送一个最小请求验证连通性和 API Key。
        """
        if not self._api_key:
            logger.error("NVIDIA API Key 未配置")
            return False

        try:
            payload = {
                "model": DEFAULT_NVIDIA_MODEL,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
                "temperature": 1.0,
                "stream": False,
            }
            resp = self._session.post(self.api_url, json=payload, timeout=10)
            ok = resp.status_code == 200
            logger.info(
                "NVIDIA API 连接检查: %s",
                "成功" if ok else f"状态码 {resp.status_code}",
            )
            return ok
        except Exception as e:
            logger.error("NVIDIA API 连接检查失败: %s", e)
            return False

    def generate(
        self,
        model: str = DEFAULT_NVIDIA_MODEL,
        prompt: str = "",
        temperature: float = DEFAULT_NVIDIA_TEMPERATURE,
        max_tokens: int = DEFAULT_NVIDIA_MAX_TOKENS,
        top_p: float = DEFAULT_NVIDIA_TOP_P,
        frequency_penalty: float = 0.0,
        presence_penalty: float = 0.0,
        stream: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> str:
        """调用 NVIDIA chat/completions 生成文本

        Args:
            model: 模型名称
            prompt: 用户提示词
            temperature: 温度参数
            max_tokens: 最大生成 token 数
            top_p: 核采样参数
            frequency_penalty: 频率惩罚
            presence_penalty: 存在惩罚
            stream: 是否流式输出
            on_token: 流式输出时的 token 回调

        Returns:
            生成的文本
        """
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
            "NVIDIA API 请求参数: %s",
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
                    if response.status_code != 200:
                        error_msg = f"NVIDIA API 错误: {response.status_code}"
                        try:
                            error_detail = response.json()
                            error_msg += f", 详情: {error_detail}"
                        except Exception:
                            error_msg += f", 响应: {response.text[:500]}"
                        logger.error(error_msg)
                        raise SummarizationError(error_msg)

                    if stream:
                        return self._handle_streaming(response, on_token)
                    else:
                        data = response.json()
                        choices = data.get("choices", [])
                        if choices:
                            return choices[0].get("message", {}).get("content", "")
                        return ""

            except requests.exceptions.Timeout:
                last_exc = SummarizationError("NVIDIA API 请求超时")
                logger.warning(
                    "NVIDIA API 超时 (尝试 %d/%d)", attempt, self.max_retries
                )
            except requests.exceptions.ConnectionError as e:
                last_exc = SummarizationError(f"NVIDIA API 连接失败: {e}")
                logger.warning(
                    "NVIDIA API 连接失败 (尝试 %d/%d): %s", attempt, self.max_retries, e
                )
            except SummarizationError:
                raise
            except Exception as e:
                last_exc = SummarizationError(f"NVIDIA API 请求失败: {e}")
                logger.error("NVIDIA API 请求异常: %s", e)

            if attempt < self.max_retries:
                wait = 2**attempt
                logger.info("%d 秒后重试...", wait)
                time.sleep(wait)

        raise last_exc

    def _handle_streaming(
        self, response: requests.Response, on_token: Optional[Callable[[str], None]]
    ) -> str:
        """处理流式响应"""
        full_text = ""
        for line in response.iter_lines():
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
                choices = data.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        full_text += content
                        if on_token:
                            on_token(content)
            except _json.JSONDecodeError:
                logger.warning("NVIDIA 流式响应 JSON 解析失败: %s", data_str[:200])
                continue
        return full_text
