"""总结器"""

from typing import Optional
from src.summarization.ollama_client import OllamaClient
from src.utils.exceptions import SummarizationError
from src.utils.logger import get_logger, log_step

logger = get_logger(__name__)


class Summarizer:
    """总结器"""

    def __init__(
        self,
        model_name: str,
        client: OllamaClient,
        temperature: float = 0.7,
        max_length: int = 5000,
    ):
        """初始化总结器

        Args:
            model_name: 模型名称
            client: OllamaClient 实例
            temperature: 温度参数
            max_length: 最大长度
        """
        self.model_name = model_name
        self.client = client
        self.temperature = temperature
        self.max_length = max_length

    def build_prompt(
        self,
        text: str,
        custom_prompt: Optional[str] = None,
        include_markdown_prompt: bool = True,
    ) -> str:
        """构建完整的用户提示词

        Args:
            text: 输入文本
            custom_prompt: 自定义提示词
            include_markdown_prompt: 是否追加 Markdown 格式指令（默认 True）

        Returns:
            完整的用户提示词
        """
        md_part = f"\n\n{self.get_markdown_prompt()}" if include_markdown_prompt else ""
        if custom_prompt and custom_prompt.strip():
            return f"{custom_prompt.strip()}{md_part}\n\n文本内容：\n{text}"
        else:
            default_prompt = (
                "你是一个专业的文本总结助手，擅长提取关键信息并生成简洁准确的总结。"
            )
            return f"{default_prompt}{md_part}\n\n文本内容：\n{text}"

    def summarize(
        self,
        text: str,
        max_length: Optional[int] = None,
        custom_prompt: Optional[str] = None,
    ) -> str:
        """总结文本

        Args:
            text: 输入文本
            max_length: 最大长度
            custom_prompt: 自定义提示词，为空则使用默认提示词

        Returns:
            总结文本
        """
        if not text or not text.strip():
            raise SummarizationError("输入文本为空")

        max_len = max_length if max_length is not None else self.max_length
        user_prompt = self.build_prompt(text, custom_prompt)

        logger.info(f"开始总结文本，长度: {len(text)} 字符")
        logger.info(f"使用模型: {self.model_name}")
        logger.debug(f"用户提示词长度: {len(user_prompt)} 字符")

        try:
            with log_step(f"Ollama API 调用 ({self.model_name})"):
                summary = self.client.generate(
                    model=self.model_name,
                    prompt=user_prompt,
                    temperature=self.temperature,
                    max_tokens=max_len,
                )

            logger.info(f"总结完成，长度: {len(summary)} 字符")
            return summary.strip()

        except Exception as e:
            logger.error(f"总结失败: {e}")
            raise SummarizationError(f"总结失败: {e}")

    def get_markdown_prompt(self) -> str:
        """Markdown提示词

        Returns:
            str: Markdown提示词
        """
        return """
请将总结内容以Markdown格式输出，形式如下：
- **要点标题**
	- 内容
	- 内容
- **要点标题**
	- 内容
	- 内容

保持Markdown格式的正确性，确保输出可以直接渲染。
"""
