"""Ollama客户端"""

import requests
import json as _json
from typing import Callable, Dict, List, Optional, Any
from src.utils.exceptions import ExternalServiceError, SummarizationError
from src.utils.logger import get_logger, log_step

logger = get_logger(__name__)


class OllamaClient:
    """Ollama客户端"""

    def __init__(self, base_url: str = "http://127.0.0.1:11434"):
        """初始化Ollama客户端

        Args:
            base_url: Ollama服务地址
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = 300

    def check_connection(self) -> bool:
        """检查Ollama连接

        Returns:
            是否连接成功
        """
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=10)
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
            response = requests.get(f"{self.base_url}/api/tags", timeout=10)
            response.raise_for_status()

            data = response.json()
            models = [model["name"] for model in data.get("models", [])]

            logger.info(f"可用模型: {models}")
            return models
        except Exception as e:
            logger.error(f"获取模型列表失败: {e}")
            return []

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

        if max_tokens:
            if "options" not in payload:
                payload["options"] = {}
            payload["options"]["num_predict"] = max_tokens

        logger.debug(f"Ollama请求参数: {payload}")

        try:
            response = requests.post(
                f"{self.base_url}/api/generate", json=payload, timeout=self.timeout
            )

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
                        data = _json.loads(line)
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

    def chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> str:
        """对话生成

        Args:
            model: 模型名称
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大token数
            stream: 是否流式输出

        Returns:
            生成的文本
        """
        payload = {"model": model, "messages": messages, "stream": stream}

        if temperature is not None:
            payload["options"] = {"temperature": temperature}

        if max_tokens:
            if "options" not in payload:
                payload["options"] = {}
            payload["options"]["num_predict"] = max_tokens

        logger.debug(f"Ollama Chat请求参数: {payload}")

        try:
            response = requests.post(
                f"{self.base_url}/api/chat", json=payload, timeout=self.timeout
            )

            logger.debug(f"Ollama Chat响应状态: {response.status_code}")

            if response.status_code != 200:
                error_msg = f"Ollama Chat API错误: {response.status_code}"
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
                        data = _json.loads(line)
                        if "message" in data and "content" in data["message"]:
                            result += data["message"]["content"]
                        if data.get("done", False):
                            break
                return result
            else:
                data = response.json()
                return data.get("message", {}).get("content", "")

        except requests.exceptions.Timeout:
            raise SummarizationError("Ollama请求超时")
        except requests.exceptions.RequestException as e:
            raise SummarizationError(f"Ollama请求失败: {e}")
        except Exception as e:
            raise SummarizationError(f"对话生成失败: {e}")

    def pull_model(self, model: str) -> bool:
        """拉取模型

        Args:
            model: 模型名称

        Returns:
            是否成功
        """
        try:
            payload = {"name": model, "stream": False}
            response = requests.post(
                f"{self.base_url}/api/pull", json=payload, timeout=600
            )
            response.raise_for_status()

            logger.info(f"模型拉取成功: {model}")
            return True
        except Exception as e:
            logger.error(f"模型拉取失败: {e}")
            return False

    def model_info(self, model: str) -> Optional[Dict[str, Any]]:
        """获取模型信息

        Args:
            model: 模型名称

        Returns:
            模型信息
        """
        try:
            response = requests.post(
                f"{self.base_url}/api/show", json={"name": model}, timeout=30
            )
            response.raise_for_status()

            return response.json()
        except Exception as e:
            logger.error(f"获取模型信息失败: {e}")
            return None
