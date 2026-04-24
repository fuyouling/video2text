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
        language: str = "zh",
        custom_prompt: Optional[str] = None,
    ) -> str:
        """总结文本

        Args:
            text: 输入文本
            max_length: 最大长度
            language: 语言
            custom_prompt: 自定义提示词，为空则使用默认提示词

        Returns:
            总结文本
        """
        if not text or not text.strip():
            raise SummarizationError("输入文本为空")

        max_len = max_length or self.max_length

        system_prompt = self._get_system_prompt(language)
        if custom_prompt and custom_prompt.strip():
            user_prompt = f"{custom_prompt.strip()}\n\n文本内容：\n{text}"
        else:
            user_prompt = self._get_user_prompt(text, max_len, language)

        logger.info(f"开始总结文本，长度: {len(text)} 字符")
        logger.info(f"使用模型: {self.model_name}")
        logger.debug(f"系统提示词: {system_prompt[:100]}...")
        logger.debug(f"用户提示词长度: {len(user_prompt)} 字符")

        try:
            summary = self.client.generate(
                model=self.model_name,
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=self.temperature,
                max_tokens=max_len * 2,
            )

            logger.info(f"总结完成，长度: {len(summary)} 字符")
            return summary.strip()

        except Exception as e:
            logger.error(f"总结失败: {e}")
            raise SummarizationError(f"总结失败: {e}")

    def summarize_with_points(
        self,
        text: str,
        num_points: int = 5,
        language: str = "zh",
        custom_prompt: Optional[str] = None,
    ) -> str:
        """生成要点总结

        Args:
            text: 输入文本
            num_points: 要点数量
            language: 语言
            custom_prompt: 自定义提示词，为空则使用默认提示词

        Returns:
            要点总结
        """
        if not text or not text.strip():
            raise SummarizationError("输入文本为空")

        system_prompt = self._get_system_prompt(language)
        if custom_prompt and custom_prompt.strip():
            user_prompt = f"{custom_prompt.strip()}\n\n文本内容：\n{text}"
        else:
            user_prompt = self._get_points_prompt(text, num_points, language)

        logger.info(f"开始生成要点总结，要点数: {num_points}")

        try:
            summary = self.client.generate(
                model=self.model_name,
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=self.temperature,
            )

            logger.info(f"要点总结完成")
            return summary.strip()

        except Exception as e:
            raise SummarizationError(f"要点总结失败: {e}")

    def extract_keywords(
        self,
        text: str,
        num_keywords: int = 10,
        language: str = "zh",
        custom_prompt: Optional[str] = None,
    ) -> list:
        """提取关键词

        Args:
            text: 输入文本
            num_keywords: 关键词数量
            language: 语言
            custom_prompt: 自定义提示词，为空则使用默认提示词

        Returns:
            关键词列表
        """
        if not text or not text.strip():
            raise SummarizationError("输入文本为空")

        system_prompt = self._get_system_prompt(language)
        if custom_prompt and custom_prompt.strip():
            user_prompt = f"{custom_prompt.strip()}\n\n文本内容：\n{text}"
        else:
            user_prompt = self._get_keywords_prompt(text, num_keywords, language)

        logger.info(f"开始提取关键词，数量: {num_keywords}")

        try:
            result = self.client.generate(
                model=self.model_name,
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.3,
            )

            keywords = [kw.strip() for kw in result.split(",")]
            keywords = [kw for kw in keywords if kw]

            logger.info(f"关键词提取完成: {keywords}")
            return keywords[:num_keywords]

        except Exception as e:
            raise SummarizationError(f"关键词提取失败: {e}")

    def _get_system_prompt(self, language: str) -> str:
        """获取系统提示词

        Args:
            language: 语言

        Returns:
            系统提示词
        """
        if language == "zh":
            return "你是一个专业的文本总结助手，擅长提取关键信息并生成简洁准确的总结。"
        else:
            return "You are a professional text summarization assistant, skilled at extracting key information and generating concise and accurate summaries."

    def _get_user_prompt(self, text: str, max_length: int, language: str) -> str:
        """获取用户提示词

        Args:
            text: 输入文本
            max_length: 最大长度
            language: 语言

        Returns:
            用户提示词
        """
        if language == "zh":
            return f"""请对以下文本进行总结，要求：
1. 提取主要内容和关键信息
2. 语言简洁明了
3. 总结长度控制在{max_length}字以内
4. 保持原文的核心意思

文本内容：
{text}"""
        else:
            return f"""Please summarize the following text with the following requirements:
1. Extract main content and key information
2. Use concise and clear language
3. Keep the summary within {max_length} words
4. Maintain the core meaning of the original text

Text content:
{text}"""

    def _get_points_prompt(self, text: str, num_points: int, language: str) -> str:
        """获取要点提示词

        Args:
            text: 输入文本
            num_points: 要点数量
            language: 语言

        Returns:
            要点提示词
        """
        if language == "zh":
            return f"""请从以下文本中提取{num_points}个要点，每个要点用数字编号：

文本内容：
{text}"""
        else:
            return f"""Please extract {num_points} key points from the following text, numbered with digits:

Text content:
{text}"""

    def _get_keywords_prompt(self, text: str, num_keywords: int, language: str) -> str:
        """获取关键词提示词

        Args:
            text: 输入文本
            num_keywords: 关键词数量
            language: 语言

        Returns:
            关键词提示词
        """
        if language == "zh":
            return f"""请从以下文本中提取{num_keywords}个关键词，用逗号分隔：

文本内容：
{text}"""
        else:
            return f"""Please extract {num_keywords} keywords from the following text, separated by commas:

Text content:
{text}"""
