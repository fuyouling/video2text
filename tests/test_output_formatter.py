"""OutputFormatter 单元测试"""

import pytest
from src.storage.output_formatter import OutputFormatter, OutputData
from src.transcription.transcriber import TranscriptSegment
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

    def test_create_output_data(self):
        segments = [_make_segment(0.0, 1.5, "你好")]
        data = self.formatter.create_output_data(
            video_name="test",
            video_path="/test.mp4",
            duration=60.0,
            transcript_segments=segments,
            processed_text="你好",
            summary="摘要",
            processing_time=5.0,
        )
        assert data.video_name == "test"
        assert data.duration == 60.0
        assert len(data.transcript) == 1
        assert data.summary == "摘要"

    def test_to_json(self):
        segments = [_make_segment(0.0, 1.5, "你好")]
        data = self.formatter.create_output_data(
            video_name="test",
            video_path="/test.mp4",
            duration=60.0,
            transcript_segments=segments,
            processed_text="你好",
            summary="摘要",
            processing_time=5.0,
        )
        json_str = self.formatter.to_json(data)
        assert '"video_name": "test"' in json_str
        assert '"你好"' in json_str

    def test_empty_segments(self):
        result = self.formatter.format_transcript([], include_timestamps=True)
        assert result == ""
        result = self.formatter.format_srt([])
        assert result == ""
        result = self.formatter.format_vtt([])
        assert result.startswith("WEBVTT")
