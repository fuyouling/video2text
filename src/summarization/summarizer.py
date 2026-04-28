"""总结器"""

from typing import Optional
from src.summarization.ollama_client import OllamaClient
from src.utils.exceptions import SummarizationError
from src.utils.logger import get_logger

logger = get_logger(__name__)


class Summarizer:
    """总结器"""

    def __init__(
        self,
        model_name: str,
        ollama_url: str = "http://127.0.0.1:11434",
        temperature: float = 0.7,
        max_length: int = 5000,
    ):
        """初始化总结器

        Args:
            model_name: 模型名称
            ollama_url: Ollama服务地址
            temperature: 温度参数
            max_length: 最大长度
        """
        self.model_name = model_name
        self.ollama_url = ollama_url
        self.temperature = temperature
        self.max_length = max_length
        self.client = OllamaClient(ollama_url)

    def check_connection(self) -> bool:
        """检查Ollama连接

        Returns:
            是否连接成功
        """
        return self.client.check_connection()

    def check_model(self) -> bool:
        """检查模型是否存在

        Returns:
            模型是否存在
        """
        try:
            models = self.client.list_models()
            exists = self.model_name in models
            logger.info(f"模型 {self.model_name} {'存在' if exists else '不存在'}")
            if not exists:
                logger.warning(f"可用模型: {models}")
            return exists
        except Exception as e:
            logger.error(f"检查模型失败: {e}")
            return False

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

        max_len = max_length or self.max_length

        if custom_prompt and custom_prompt.strip():
            user_prompt = f"{custom_prompt.strip()}\n\n{self.get_markdown_prompt()}\n\n文本内容：\n{text}"
        else:
            default_prompt = "你是一个专业的文本总结助手，擅长提取关键信息并生成简洁准确的总结。"
            user_prompt = (
                f"{default_prompt}\n\n{self.get_markdown_prompt()}\n\n文本内容：\n{text}"
            )

        logger.info(f"开始总结文本，长度: {len(text)} 字符")
        logger.info(f"使用模型: {self.model_name}")
        logger.debug(f"用户提示词长度: {len(user_prompt)} 字符")

        try:
            summary = self.client.generate(
                model=self.model_name,
                prompt=user_prompt,
                temperature=self.temperature,
                max_tokens=max_len * 2,
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
        return '''
请将总结内容以Markdown格式输出，形式如下：
- **要点标题**
	- 内容
	- 内容
- **要点标题**
	- 内容
	- 内容

保持Markdown格式的正确性，确保输出可以直接渲染。
'''
