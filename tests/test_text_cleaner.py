"""TextCleaner 单元测试"""

import pytest
from src.text_processing.text_cleaner import TextCleaner, remove_replacement_chars


class TestTextCleaner:
    def setup_method(self):
        self.cleaner = TextCleaner()

    def test_clean_empty_text(self):
        assert self.cleaner.clean("") == ""
        assert self.cleaner.clean(None) == ""

    def test_remove_extra_whitespace(self):
        assert self.cleaner.remove_extra_whitespace("hello  world") == "hello world"
        assert (
            self.cleaner.remove_extra_whitespace("hello\n\n\nworld") == "hello\n\nworld"
        )
        assert self.cleaner.remove_extra_whitespace("hello\r\nworld") == "hello\nworld"

    def test_remove_fillers(self):
        text = "嗯今天嗯嗯我们讨论一下"
        result = self.cleaner.remove_fillers(text)
        assert result == "今天我们讨论一下"

    def test_remove_fillers_preserves_semantic_words(self):
        """那个/这个/就是/然后 不应被删除（非默认填充词）"""
        text = "然后那个苹果就是很甜的"
        result = self.cleaner.remove_fillers(text)
        assert result == text

    def test_fix_punctuation_no_normalize(self):
        """默认不转换中文标点"""
        cleaner = TextCleaner({"normalize_punctuation": False})
        text = "你好，世界。"
        result = cleaner.fix_punctuation(text)
        assert "，" in result
        assert "。" in result

    def test_fix_punctuation_with_normalize(self):
        """启用中文标点转英文标点"""
        cleaner = TextCleaner({"normalize_punctuation": True})
        text = "你好，世界。"
        result = cleaner.fix_punctuation(text)
        assert "," in result
        assert "." in result
        assert "，" not in result
        assert "。" not in result

    def test_normalize_quotes(self):
        text = "他说\"你好\"和'再见'"
        result = self.cleaner.normalize_quotes(text)
        assert '"' in result

    def test_remove_repeated_chars(self):
        assert self.cleaner.remove_repeated_chars("aaa") == "aa"
        assert self.cleaner.remove_repeated_chars("!!!") == "!!!"
        assert self.cleaner.remove_repeated_chars("aa") == "aa"

    def test_remove_replacement_chars(self):
        """替换字符 U+FFFD 应被移除，且不影响正常中文/英文。"""
        rc = chr(0xFFFD)
        text = "轨道层级越高代表素" + rc + "材越重要"
        result = remove_replacement_chars(text)
        assert rc not in result
        assert result == "轨道层级越高代表素材越重要"

    def test_clean_removes_replacement_chars(self):
        """clean 全流程应清理掉 U+FFFD 损坏字符。"""
        rc = chr(0xFFFD)
        text = "素" + rc + "材放在 V2上"
        result = self.cleaner.clean(text)
        assert rc not in result
        assert result == "素材放在 V2上"

    def test_truncate_text(self):
        assert self.cleaner.truncate_text("hello", 10) == "hello"
        assert self.cleaner.truncate_text("hello world", 8) == "hello..."

    def test_clean_full_pipeline(self):
        text = "嗯  你好，世界！！！  嗯嗯"
        result = self.cleaner.clean(text)
        assert len(result) > 0
        assert "  " not in result  # no double spaces

    def test_fix_punctuation_preserves_chinese_ellipsis(self):
        """中文省略号。。。不应被压缩为单个句号"""
        cleaner = TextCleaner({"normalize_punctuation": False})
        text = "好吧。。。"
        result = cleaner.fix_punctuation(text)
        assert "。。。" in result
        assert result == "好吧。。。"

    def test_fix_punctuation_reduces_repeated_punctuation(self):
        """重复标点应被压缩（省略号除外）"""
        cleaner = TextCleaner({"normalize_punctuation": False})
        assert cleaner.fix_punctuation("你好！！！") == "你好！"
        assert cleaner.fix_punctuation("你好？？？") == "你好？"
        assert cleaner.fix_punctuation("你好，，，") == "你好，"
