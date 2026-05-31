"""NVIDIA API 客户端 —— 使用 OpenAI 兼容的 chat/completions 接口"""

import json as _json
import os
import threading
import time
from typing import Callable, Optional

import requests

from src.utils.exceptions import SummarizationError
from src.utils.logger import get_logger
from src.utils.paths import get_base_dir

logger = get_logger(__name__)


def _ensure_env_loaded() -> None:
    """如果 NVIDIA_API_KEY 不在环境变量中，尝试从 .env 文件加载"""
    if os.environ.get("NVIDIA_API_KEY"):
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(get_base_dir() / ".env", override=False)
    except Exception:
        pass


class NvidiaClient:
    """NVIDIA API 客户端 —— 通过 OpenAI 兼容接口调用 NVIDIA 模型"""

    def __init__(
        self,
        api_url: str = "https://integrate.api.nvidia.com/v1/chat/completions",
        api_key: Optional[str] = None,
        timeout: int = 600,
        model: str = "openai/gpt-oss-120b",
    ):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = 5
        self._model = model

        if not api_key:
            _ensure_env_loaded()
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
            logger.error("NvidiaClient: ✗ API Key 未配置")
            env_path = get_base_dir() / ".env"
            if env_path.exists():
                try:
                    with open(env_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    if "NVIDIA_API_KEY" not in content:
                        logger.warning(
                            "NvidiaClient: .env 文件中未找到 NVIDIA_API_KEY，请添加: NVIDIA_API_KEY=your_api_key"
                        )
                except Exception as e:
                    logger.debug("NvidiaClient: 读取 .env 文件失败: %s", e)
            else:
                logger.warning(
                    "NvidiaClient: .env 文件不存在，请创建并配置 NVIDIA_API_KEY"
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
            resp = self._session.post(self.api_url, json=payload, timeout=30)
            ok = resp.status_code == 200
            if ok:
                logger.debug("NvidiaClient: 连接检查成功")
            else:
                try:
                    error_detail = resp.json()
                    logger.error(
                        "NvidiaClient: ✗ 连接检查失败 (状态码 %d): %s",
                        resp.status_code,
                        error_detail,
                    )
                except Exception:
                    logger.error(
                        "NvidiaClient: ✗ 连接检查失败 (状态码 %d): %s",
                        resp.status_code,
                        resp.text[:500] if resp.text else "(空响应)",
                    )
            return ok
        except Exception as e:
            logger.error("NvidiaClient: ✗ 连接失败 (%s)", e)
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
                    if response.status_code == 429:
                        retry_after = response.headers.get("Retry-After")
                        if retry_after:
                            try:
                                wait = int(retry_after)
                            except ValueError:
                                wait = 2 ** (attempt + 1)
                        else:
                            wait = 2 ** (attempt + 1)
                        last_exc = SummarizationError(
                            f"NVIDIA API 限流 (429), {wait} 秒后重试"
                        )
                        logger.warning(
                            "NvidiaClient: ⚠ 429 限流 (%d/%d)，等待 %ds",
                            attempt,
                            self.max_retries,
                            wait,
                        )
                        if attempt < self.max_retries:
                            time.sleep(wait)
                            continue
                        raise last_exc

                    if response.status_code != 200:
                        error_msg = f"NVIDIA API 错误: {response.status_code}"
                        try:
                            error_detail = response.json()
                            error_msg += f", 详情: {error_detail}"
                        except Exception:
                            error_msg += f", 响应: {response.text[:500]}"
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
                last_exc = SummarizationError("NVIDIA API 请求超时")
                logger.warning(
                    "NvidiaClient: ⚠ 超时 (%d/%d)", attempt, self.max_retries
                )
            except requests.exceptions.ConnectionError as e:
                last_exc = SummarizationError(f"NVIDIA API 连接失败: {e}")
                logger.warning(
                    "NvidiaClient: ⚠ 连接失败 (%d/%d): %s", attempt, self.max_retries, e
                )
            except SummarizationError:
                raise
            except Exception as e:
                last_exc = SummarizationError(f"NVIDIA API 请求失败: {e}")
                logger.error("NvidiaClient: ✗ 请求异常 (%s)", e)

            if attempt < self.max_retries:
                wait = 2**attempt
                logger.info("NvidiaClient: %ds 后重试...", wait)
                time.sleep(wait)

        raise last_exc or SummarizationError("NVIDIA API 请求失败（未知错误）")

    def _handle_streaming(
        self,
        response: requests.Response,
        on_token: Optional[Callable[[str], None]],
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_event: Optional[threading.Event] = None,
    ) -> str:
        """处理流式响应"""
        full_text = ""
        try:
            for line in response.iter_lines():
                if cancel_check and cancel_check():
                    raise SummarizationError("用户取消了摘要")
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
                        raise SummarizationError(f"NVIDIA 流式错误: {data['error']}")
                    choices = data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            full_text += content
                            if on_token:
                                on_token(content)
                except _json.JSONDecodeError:
                    logger.warning("NvidiaClient: ⚠ 流式 JSON 解析失败")
                    continue
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ) as e:
            if full_text:
                logger.warning(
                    "NvidiaClient: ⚠ 流式中断，已接收 %d 字符",
                    len(full_text),
                )
            else:
                raise SummarizationError(f"NVIDIA 流式连接失败: {e}") from e
        return full_text
