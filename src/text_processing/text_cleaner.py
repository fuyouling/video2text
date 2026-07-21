"""文本清理器"""

import re
from typing import Optional
from src.utils.logger import get_logger
from src.i18n import t

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
        self.filler_words = sorted(
            self.config.get(
                "filler_words",
                [],
            ),
            key=len,
            reverse=True,
        )
        self._filler_patterns = []
        for filler in self.filler_words:
            escaped = re.escape(filler)
            if re.search(r"[a-zA-Z]", filler):
                self._filler_patterns.append(
                    (re.compile(r"\b" + escaped + r"\b", re.IGNORECASE), "")
                )
            else:
                self._filler_patterns.append((re.compile(escaped), ""))
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

        logger.debug(t("text_processing.text_cleaner.clean_done", before=len(text), after=len(cleaned)))
        return cleaned.strip()

    def remove_fillers(self, text: str) -> str:
        """移除填充词

        Args:
            text: 原始文本

        Returns:
            移除填充词后的文本
        """
        for pattern, replacement in self._filler_patterns:
            text = pattern.sub(replacement, text)

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
                "[，。！？、；：\u201c\u201d\u2018\u2019（）【】《》]",
                lambda m: {
                    "，": ",",
                    "。": ".",
                    "！": "!",
                    "？": "?",
                    "、": ",",
                    "；": ";",
                    "：": ":",
                    "\u201c": '"',
                    "\u201d": '"',
                    "\u2018": "'",
                    "\u2019": "'",
                    "（": "(",
                    "）": ")",
                    "【": "[",
                    "】": "]",
                    "《": "<",
                    "》": ">",
                }.get(m.group(), m.group()),
                text,
            )

        text = re.sub(r"[ \t]+([,.!?;:])", r"\1", text)

        text = re.sub(r"([,.!?;:])[ \t]+", r"\1 ", text)

        text = re.sub(r"\.{4,}", "...", text)
        text = re.sub(r"([!?])\1+", r"\1", text)

        text = re.sub(r"[ \t]+([，。！？、；：])", r"\1", text)

        text = re.sub(r"([，。！？、；：])[ \t]+", r"\1", text)

        text = re.sub(r"([，！？、；：])\1+", r"\1", text)
        text = re.sub(r"。{4,}", "。。。", text)

        text = re.sub(r"[ \t]+", " ", text)

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
        text = re.sub(r"\n(\s*\n)+", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)

        return text

    def normalize_quotes(self, text: str) -> str:
        """规范化引号

        Args:
            text: 原始文本

        Returns:
            规范化引号后的文本
        """
        text = re.sub(r'["""]', '"', text)
        text = re.sub(r"[''']", "'", text)
        return text

    def remove_repeated_chars(self, text: str) -> str:
        """移除重复字符

        Args:
            text: 原始文本

        Returns:
            移除重复字符后的文本
        """
        text = re.sub(r"([a-zA-Z])\1{2,}", r"\1\1", text)
        text = re.sub(r"([\u4e00-\u9fff])\1{4,}", r"\1\1\1", text)

        return text

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

        if max_length <= len(ellipsis):
            return ellipsis[:max_length]

        return text[: max_length - len(ellipsis)] + ellipsis
