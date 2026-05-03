"""Ollama客户端"""

import os
import shutil
import subprocess
import sys
import requests
import json as _json
import time
from typing import Callable, List, Optional
from src.config.settings import DEFAULT_OLLAMA_URL, DEFAULT_OLLAMA_TIMEOUT
from src.utils.exceptions import SummarizationError
from src.utils.logger import get_logger

logger = get_logger(__name__)


class OllamaClient:
    """Ollama客户端 —— 统一管理 Ollama HTTP 通信与服务进程生命周期"""

    # 类级别进程引用，所有实例共享同一个 Ollama 服务进程
    _service_process: Optional[subprocess.Popen] = None

    def __init__(
        self, base_url: str = DEFAULT_OLLAMA_URL, timeout: int = DEFAULT_OLLAMA_TIMEOUT
    ):
        """初始化Ollama客户端

        Args:
            base_url: Ollama服务地址
            timeout: 请求超时时间（秒）
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = 3
        self._session = requests.Session()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self) -> None:
        """关闭底层 HTTP Session，释放连接池。"""
        self._session.close()

    # ------------------------------------------------------------------
    # 服务进程管理
    # ------------------------------------------------------------------

    @classmethod
    def start_service(cls, url: str = DEFAULT_OLLAMA_URL) -> bool:
        """启动 Ollama 服务进程（如果尚未运行）。

        如果 Ollama 已在外部启动（例如用户手动运行了 ``ollama serve``），
        本方法会通过 HTTP 探测发现它并直接返回 ``True``，**不会**记录进程引用，
        因此后续调用 ``stop_service()`` 也不会终止外部进程。

        Args:
            url: Ollama 服务地址

        Returns:
            服务是否已就绪（已在运行或成功启动）
        """
        # 已经在运行？（可能由外部启动，不归本客户端管理）
        try:
            resp = requests.get(f"{url.rstrip('/')}/api/tags", timeout=2)
            if resp.status_code == 200:
                logger.info("Ollama 服务已在运行中")
                return True
        except Exception:
            pass

        # 已由本客户端启动过且进程仍存活？
        if cls._service_process is not None and cls._service_process.poll() is None:
            logger.info("Ollama 服务进程已存在")
            return True

        ollama_path = shutil.which("ollama")
        if not ollama_path:
            logger.error("未找到 ollama 命令，请确保已安装 Ollama")
            return False

        try:
            logger.info("正在启动 Ollama 服务...")
            creation_flags = (
                subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            cls._service_process = subprocess.Popen(
                [ollama_path, "serve"],
                creationflags=creation_flags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={**os.environ},
            )
            logger.info("Ollama 服务启动命令已执行 (PID: %s)", cls._service_process.pid)
            return True
        except Exception as e:
            logger.error("启动 Ollama 服务失败: %s", e)
            cls._service_process = None
            return False

    @classmethod
    def stop_service(cls) -> None:
        """停止由本客户端启动的 Ollama 服务进程（含全部子进程）。"""
        proc = cls._service_process
        if proc is None:
            return
        cls._service_process = None
        if proc.poll() is not None:
            return
        pid = proc.pid
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
            else:
                proc.terminate()
                proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass
        except Exception:
            pass
        logger.info("Ollama 服务进程已停止 (PID: %s)", pid)

    @classmethod
    def is_service_running(cls, url: str = DEFAULT_OLLAMA_URL) -> bool:
        """检查 Ollama 服务是否正在运行（通过 HTTP 探测）。

        Args:
            url: Ollama 服务地址

        Returns:
            服务是否可达
        """
        try:
            resp = requests.get(f"{url.rstrip('/')}/api/tags", timeout=2)
            return resp.status_code == 200
        except Exception:
            return False

    @classmethod
    def ensure_service(
        cls,
        url: str = DEFAULT_OLLAMA_URL,
        max_retries: int = 3,
        wait_seconds: float = 5,
    ) -> bool:
        """确保 Ollama 服务可用：未运行时自动启动，最多重试指定次数。

        Args:
            url: Ollama 服务地址
            max_retries: 最大重试次数
            wait_seconds: 每次重试等待秒数

        Returns:
            服务是否最终可用

        Raises:
            RuntimeError: 所有重试后服务仍不可用
        """
        if cls.is_service_running(url):
            return True

        logger.info("Ollama 未运行，尝试自动启动...")
        if not cls.start_service(url):
            raise RuntimeError("无法启动 Ollama 服务：未找到 ollama 命令")

        for attempt in range(1, max_retries + 1):
            logger.info(
                "等待 Ollama 就绪... (%d/%d，等待 %.0f 秒)",
                attempt,
                max_retries,
                wait_seconds,
            )
            time.sleep(wait_seconds)
            if cls.is_service_running(url):
                logger.info("Ollama 服务就绪 (第 %d 次检测)", attempt)
                return True

        raise RuntimeError(
            f"Ollama 服务已启动但 {max_retries} 次检测均无法连接 ({url})"
        )

    def _post_with_retry(
        self, url: str, json: dict, timeout: int, stream: bool = False
    ) -> requests.Response:
        """带重试的 POST 请求（仅对非流式请求重试）。"""
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._session.post(
                    url, json=json, timeout=timeout, stream=stream
                )
                return response
            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ) as e:
                last_exc = e
                if stream or attempt == self.max_retries:
                    raise
                wait = 2**attempt
                logger.warning(
                    "Ollama 请求失败 (尝试 %d/%d): %s，%d 秒后重试...",
                    attempt,
                    self.max_retries,
                    e,
                    wait,
                )
                time.sleep(wait)
        raise last_exc

    def check_connection(self) -> bool:
        """检查Ollama连接

        Returns:
            是否连接成功
        """
        try:
            response = self._session.get(f"{self.base_url}/api/tags", timeout=10)
            success = response.status_code == 200
            logger.info(f"Ollama连接检查: {'成功' if success else '失败'}")
            return success
        except Exception as e:
            logger.error(f"Ollama连接检查失败: {e}")
            return False

    def list_models(self) -> List[str]:
        """列出可用模型

        Returns:
            模型名称列表
        """
        try:
            response = self._session.get(f"{self.base_url}/api/tags", timeout=10)
            response.raise_for_status()

            data = response.json()
            models = [model["name"] for model in data.get("models", [])]

            logger.info(f"可用模型: {models}")
            return models
        except Exception as e:
            logger.error(f"获取模型列表失败: {e}")
            return []

    def check_model(self, model_name: str) -> bool:
        """检查指定模型是否存在

        Args:
            model_name: 模型名称

        Returns:
            模型是否存在
        """
        try:
            models = self.list_models()
            exists = model_name in models
            logger.info(f"模型 {model_name} {'存在' if exists else '不存在'}")
            if not exists:
                logger.warning(f"可用模型: {models}")
            return exists
        except Exception as e:
            logger.error(f"检查模型失败: {e}")
            return False

    def generate(
        self,
        model: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> str:
        """生成文本

        Args:
            model: 模型名称
            prompt: 提示词
            system_prompt: 系统提示词
            temperature: 温度参数
            max_tokens: 最大token数
            stream: 是否流式输出
            on_token: 流式输出时的 token 回调函数

        Returns:
            生成的文本
        """
        payload = {"model": model, "prompt": prompt, "stream": stream}

        if system_prompt:
            payload["system"] = system_prompt

        if temperature is not None:
            payload["options"] = {"temperature": temperature}

        if max_tokens is not None:
            if "options" not in payload:
                payload["options"] = {}
            payload["options"]["num_predict"] = max_tokens

        logger.debug(f"Ollama请求参数: {payload}")

        try:
            response = self._post_with_retry(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
                stream=stream,
            )

            with response:
                logger.debug(f"Ollama响应状态: {response.status_code}")

                if response.status_code != 200:
                    error_msg = f"Ollama API错误: {response.status_code}"
                    try:
                        error_detail = response.json()
                        error_msg += f", 详情: {error_detail}"
                    except Exception:
                        error_msg += f", 响应: {response.text[:200]}"
                    logger.error(error_msg)
                    raise SummarizationError(error_msg)

                if stream:
                    result = ""
                    for line in response.iter_lines():
                        if line:
                            try:
                                data = _json.loads(line)
                            except _json.JSONDecodeError:
                                logger.warning(
                                    "Ollama 流式响应 JSON 解析失败: %s", line[:200]
                                )
                                continue
                            if "response" in data:
                                token = data["response"]
                                result += token
                                if on_token:
                                    on_token(token)
                            if data.get("done", False):
                                break
                    return result
                else:
                    data = response.json()
                    return data.get("response", "")

        except requests.exceptions.Timeout:
            raise SummarizationError("Ollama请求超时")
        except requests.exceptions.RequestException as e:
            raise SummarizationError(f"Ollama请求失败: {e}")
        except SummarizationError:
            raise
        except Exception as e:
            raise SummarizationError(f"生成文本失败: {e}")
