"""OutputFormatter 单元测试"""

import pytest
from src.storage.output_formatter import OutputFormatter
from src.transcription.transcriber import TranscriptSegment
from src.utils.output_validator import validate_srt_content, validate_vtt_content
from src.utils.time_format import format_time_hms, format_time_srt, format_time_vtt


def _make_segment(start: float, end: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(
        start=start, end=end, text=text, confidence=95.0, language="zh"
    )


class TestOutputFormatter:
    def setup_method(self):
        self.formatter = OutputFormatter()

    def test_format_transcript_with_timestamps(self):
        segments = [_make_segment(0.0, 1.5, "你好世界")]
        result = self.formatter.format_transcript(segments, include_timestamps=True)
        assert "[00:00:00 - 00:00:01]" in result
        assert "你好世界" in result

    def test_format_transcript_without_timestamps(self):
        segments = [_make_segment(0.0, 1.5, "你好世界")]
        result = self.formatter.format_transcript(segments, include_timestamps=False)
        assert result == "你好世界"

    def test_format_srt(self):
        segments = [
            _make_segment(0.0, 1.5, "你好"),
            _make_segment(1.5, 3.0, "世界"),
        ]
        result = self.formatter.format_srt(segments)
        assert "1\n" in result
        assert "2\n" in result
        assert "00:00:00,000 --> 00:00:01,500" in result
        assert "你好" in result
        assert "世界" in result

    def test_format_vtt(self):
        segments = [_make_segment(0.0, 1.5, "你好")]
        result = self.formatter.format_vtt(segments)
        assert result.startswith("WEBVTT")
        assert "00:00:00.000 --> 00:00:01.500" in result
        assert "你好" in result

    def test_format_summary(self):
        result = self.formatter.format_summary("这是摘要")
        assert result == "这是摘要"

    def test_format_time(self):
        assert format_time_hms(0) == "00:00:00"
        assert format_time_hms(61) == "00:01:01"
        assert format_time_hms(3661) == "01:01:01"

    def test_format_srt_time(self):
        assert format_time_srt(1.5) == "00:00:01,500"

    def test_format_vtt_time(self):
        assert format_time_vtt(1.5) == "00:00:01.500"

    def test_empty_segments(self):
        result = self.formatter.format_transcript([], include_timestamps=True)
        assert result == ""
        result = self.formatter.format_srt([])
        assert result == ""
        result = self.formatter.format_vtt([])
        assert result.startswith("WEBVTT")

    def test_srt_and_vtt_inverted_timestamp_compensation(self):
        # start > end: 防倒挂与最小持续时长补偿保证 end >= start + 0.05s
        segments = [
            _make_segment(5.0, 2.0, "时间倒挂测试"),
            _make_segment(10.0, 10.0, "零持续时长测试"),
            _make_segment(20.0, 20.01, "极短持续时长测试"),
        ]
        srt = self.formatter.format_srt(segments)
        # 应无异常通过校验器
        blocks = validate_srt_content(srt)
        assert len(blocks) == 3
        assert "00:00:05,000 --> 00:00:05,050" in srt
        assert "00:00:10,000 --> 00:00:10,050" in srt
        assert "00:00:20,000 --> 00:00:20,050" in srt

        vtt = self.formatter.format_vtt(segments)
        cues = validate_vtt_content(vtt)
        assert len(cues) == 3
        assert "00:00:05.000 --> 00:00:05.050" in vtt
        assert "00:00:10.000 --> 00:00:10.050" in vtt
        assert "00:00:20.000 --> 00:00:20.050" in vtt

    def test_srt_and_vtt_filter_cue_empty_lines(self):
        # cue 内部夹带多余空行，应当被过滤，不影响分块解析
        segments = [
            _make_segment(0.0, 1.5, "第一行\n\n\n第二行\r\n\r\n第三行\n"),
            _make_segment(1.5, 3.0, "   \n\n  "),  # 纯空白文本
        ]
        srt = self.formatter.format_srt(segments)
        blocks = validate_srt_content(srt)
        assert len(blocks) == 2
        assert "第一行\n第二行\n第三行" in srt
        assert "..." in blocks[1]

        vtt = self.formatter.format_vtt(segments)
        cues = validate_vtt_content(vtt)
        assert len(cues) == 2
        assert "第一行\n第二行\n第三行" in vtt
