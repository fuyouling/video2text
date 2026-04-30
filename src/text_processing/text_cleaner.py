"""文本清理器"""

import re
from typing import List, Optional
from src.utils.logger import get_logger

logger = get_logger(__name__)


class TextCleaner:
    """文本清理器"""

    def __init__(self, config: Optional[dict] = None):
        """初始化文本清理器

        Args:
            config: 配置字典
                - filler_words: 填充词列表
                - normalize_punctuation: 是否将中文标点转换为英文标点（默认 False）
        """
        self.config = config or {}
        self.filler_words = self.config.get(
            "filler_words",
            ["嗯", "啊", "呃", "那个", "这个", "就是", "然后", "嗯嗯", "啊啊"],
        )
        self.normalize_punctuation = self.config.get("normalize_punctuation", False)

    def clean(self, text: str) -> str:
        """清理文本

        Args:
            text: 原始文本

        Returns:
            清理后的文本
        """
        if not text:
            return ""

        cleaned = text

        cleaned = self.remove_extra_whitespace(cleaned)
        cleaned = self.remove_fillers(cleaned)
        cleaned = self.fix_punctuation(cleaned)
        cleaned = self.normalize_quotes(cleaned)
        cleaned = self.remove_repeated_chars(cleaned)

        logger.debug(f"文本清理完成: {len(text)} -> {len(cleaned)} 字符")
        return cleaned.strip()

    def remove_fillers(self, text: str) -> str:
        """移除填充词

        Args:
            text: 原始文本

        Returns:
            移除填充词后的文本
        """
        for filler in self.filler_words:
            pattern = r"\b" + re.escape(filler) + r"\b"
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)

        return text

    def fix_punctuation(self, text: str) -> str:
        """修复标点符号

        Args:
            text: 原始文本

        Returns:
            修复标点后的文本
        """
        if self.normalize_punctuation:
            text = re.sub(
                r"[，。！？、；：" "''（）【】《》]",
                lambda m: {
                    "，": ",",
                    "。": ".",
                    "！": "!",
                    "？": "?",
                    "、": ",",
                    "；": ";",
                    "：": ":",
                    '"': '"',
                    "'": "'",
                    "（": "(",
                    "）": ")",
                    "【": "[",
                    "】": "]",
                    "《": "<",
                    "》": ">",
                }.get(m.group(), m.group()),
                text,
            )

        text = re.sub(r"\s+([,.!?;:])", r"\1", text)

        text = re.sub(r"([,.!?;:])\s+", r"\1 ", text)

        text = re.sub(r"([,.!?;:])\1+", r"\1", text)

        text = re.sub(r"\s+", " ", text)

        return text

    def remove_extra_whitespace(self, text: str) -> str:
        """移除多余空白

        Args:
            text: 原始文本

        Returns:
            移除多余空白后的文本
        """
        text = re.sub(r"\r\n", "\n", text)
        text = re.sub(r"\r", "\n", text)
        text = re.sub(r"\n\s*\n", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)

        return text

    def normalize_quotes(self, text: str) -> str:
        """规范化引号

        Args:
            text: 原始文本

        Returns:
            规范化引号后的文本
        """
        text = re.sub(r'[""\'`]', '"', text)
        return text

    def remove_repeated_chars(self, text: str) -> str:
        """移除重复字符

        Args:
            text: 原始文本

        Returns:
            移除重复字符后的文本
        """
        text = re.sub(r"([a-zA-Z])\1{2,}", r"\1\1", text)
        text = re.sub(r"([.,!?;:])\1{2,}", r"\1", text)

        return text

    def capitalize_sentences(self, text: str) -> str:
        """句子首字母大写

        Args:
            text: 原始文本

        Returns:
            首字母大写后的文本
        """
        sentences = re.split(r"([.!?]+)\s*", text)

        for i in range(0, len(sentences), 2):
            if sentences[i]:
                sentences[i] = sentences[i][0].upper() + sentences[i][1:]

        return "".join(sentences)

    def remove_empty_lines(self, text: str) -> str:
        """移除空行

        Args:
            text: 原始文本

        Returns:
            移除空行后的文本
        """
        lines = text.split("\n")
        non_empty_lines = [line for line in lines if line.strip()]
        return "\n".join(non_empty_lines)

    def truncate_text(self, text: str, max_length: int, ellipsis: str = "...") -> str:
        """截断文本

        Args:
            text: 原始文本
            max_length: 最大长度
            ellipsis: 省略号

        Returns:
            截断后的文本
        """
        if len(text) <= max_length:
            return text

        return text[: max_length - len(ellipsis)] + ellipsis
