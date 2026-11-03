"""SegmentMerger 单元测试"""

import pytest
from src.text_processing.segment_merger import SegmentMerger, MergedSegment
from src.transcription.transcriber import TranscriptSegment


def _make_segment(
    start: float, end: float, text: str, language: str = "zh"
) -> TranscriptSegment:
    return TranscriptSegment(
        start=start, end=end, text=text, confidence=90.0, language=language
    )


class TestSegmentMerger:
    def setup_method(self):
        self.merger = SegmentMerger(max_gap=2.0, min_length=50)

    def test_merge_empty(self):
        assert self.merger.merge_segments([]) == []

    def test_merge_single_segment(self):
        segments = [_make_segment(0.0, 1.0, "你好")]
        result = self.merger.merge_segments(segments)
        assert len(result) == 1
        assert result[0].text == "你好"
        assert result[0].start == 0.0
        assert result[0].end == 1.0

    def test_merge_close_segments(self):
        segments = [
            _make_segment(0.0, 1.0, "你好"),
            _make_segment(1.5, 2.5, "世界"),
        ]
        result = self.merger.merge_segments(segments)
        assert len(result) == 1
        assert "你好" in result[0].text
        assert "世界" in result[0].text

    def test_no_merge_far_segments(self):
        segments = [
            _make_segment(0.0, 1.0, "你好"),
            _make_segment(5.0, 6.0, "世界"),
        ]
        result = self.merger.merge_segments(segments)
        assert len(result) == 2

    def test_no_merge_different_language(self):
        segments = [
            _make_segment(0.0, 1.0, "你好", language="zh"),
            _make_segment(1.5, 2.5, "hello", language="en"),
        ]
        result = self.merger.merge_segments(segments)
        assert len(result) == 2

    def test_merge_by_length(self):
        segments = [
            _make_segment(0.0, 1.0, "短"),
            _make_segment(1.5, 2.5, "文本"),
            _make_segment(3.0, 4.0, "这是比较长的文本内容"),
        ]
        result = self.merger.merge_by_length(segments, target_length=10)
        assert len(result) >= 1

    def test_merge_by_time(self):
        segments = [
            _make_segment(0.0, 1.0, "你好"),
            _make_segment(2.0, 3.0, "世界"),
            _make_segment(35.0, 36.0, "再见"),
        ]
        result = self.merger.merge_by_time(segments, interval=30.0)
        assert len(result) == 2

    def test_filter_short_segments(self):
        segments = [
            MergedSegment(0.0, 1.0, "短", "zh"),
            MergedSegment(2.0, 3.0, "这是一个足够长的段落", "zh"),
        ]
        result = self.merger.filter_short_segments(segments, min_length=5)
        assert len(result) == 1
        assert "足够长" in result[0].text

    def test_format_segments_as_text(self):
        segments = [
            MergedSegment(0.0, 1.0, "你好", "zh"),
            MergedSegment(2.0, 3.0, "世界", "zh"),
        ]
        result = self.merger.format_segments_as_text(segments, include_timestamps=False)
        assert "你好" in result
        assert "世界" in result
        assert "\n\n" in result

    def test_format_segments_with_timestamps(self):
        segments = [
            MergedSegment(0.0, 65.0, "你好", "zh"),
        ]
        result = self.merger.format_segments_as_text(segments, include_timestamps=True)
        assert "[00:00:00" in result
        assert "你好" in result

    def test_cjk_no_space_merge(self):
        """中文/日文合并时不强制插入空格，英文正常加空格"""
        zh_segments = [
            _make_segment(0.0, 1.0, "你好", language="zh"),
            _make_segment(1.2, 2.0, "世界", language="zh"),
        ]
        zh_result = self.merger.merge_segments(zh_segments)
        assert len(zh_result) == 1
        assert zh_result[0].text == "你好世界"

        ja_segments = [
            _make_segment(0.0, 1.0, "こんにちは", language="ja"),
            _make_segment(1.2, 2.0, "世界", language="ja"),
        ]
        ja_result = self.merger.merge_segments(ja_segments)
        assert len(ja_result) == 1
        assert ja_result[0].text == "こんにちは世界"

        en_segments = [
            _make_segment(0.0, 1.0, "Hello", language="en"),
            _make_segment(1.2, 2.0, "World", language="en"),
        ]
        en_result = self.merger.merge_segments(en_segments)
        assert len(en_result) == 1
        assert en_result[0].text == "Hello World"

    def test_prevent_time_going_backwards(self):
        """current.end = max(current.end, segment.end) 防止时间倒流"""
        segments = [
            _make_segment(0.0, 5.0, "前一段", language="zh"),
            _make_segment(1.0, 3.0, "重叠段", language="zh"),
        ]
        result = self.merger.merge_segments(segments)
        assert len(result) == 1
        assert result[0].end == 5.0  # max(5.0, 3.0)

    def test_merge_by_length_respects_max_gap(self):
        """merge_by_length 在 gap > max_gap 时禁止合并"""
        segments = [
            _make_segment(0.0, 1.0, "短句一", language="zh"),
            # 间隔 10 秒，超过 self.merger.max_gap (2.0)
            _make_segment(11.0, 12.0, "短句二", language="zh"),
        ]
        result = self.merger.merge_by_length(segments, target_length=200)
        assert len(result) == 2
        assert result[0].text == "短句一"
        assert result[1].text == "短句二"
