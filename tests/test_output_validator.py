"""OutputValidator 单元测试"""

import json
import pytest
import tempfile
from pathlib import Path

from src.utils.output_validator import (
    OutputValidationError,
    validate_output_file,
    validate_srt_content,
    validate_vtt_content,
    validate_json_content,
    validate_transcript_segments,
    validate_output_content,
    _parse_srt_timestamp,
    _parse_vtt_timestamp,
)
from src.transcription.transcriber import TranscriptSegment


def _make_segment(start, end, text, confidence=95.0, language="zh"):
    return TranscriptSegment(
        start=start, end=end, text=text, confidence=confidence, language=language
    )


class TestValidateOutputFile:
    def test_file_exists_and_nonempty(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello", encoding="utf-8")
        validate_output_file(str(f))

    def test_file_not_exists(self, tmp_path):
        f = tmp_path / "missing.txt"
        with pytest.raises(OutputValidationError, match="未生成"):
            validate_output_file(str(f))

    def test_file_empty(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        with pytest.raises(OutputValidationError, match="为空或过小"):
            validate_output_file(str(f))

    def test_file_min_size(self, tmp_path):
        f = tmp_path / "small.txt"
        f.write_text("ab", encoding="utf-8")
        with pytest.raises(OutputValidationError, match="为空或过小"):
            validate_output_file(str(f), min_size=10)

    def test_file_encoding_ok(self, tmp_path):
        f = tmp_path / "utf8.txt"
        f.write_text("你好世界", encoding="utf-8")
        validate_output_file(str(f), encoding="utf-8")


class TestValidateSrtContent:
    def test_valid_srt(self):
        srt = (
            "1\n"
            "00:00:00,000 --> 00:00:01,500\n"
            "你好\n"
            "\n"
            "2\n"
            "00:00:01,500 --> 00:00:03,000\n"
            "世界\n"
        )
        blocks = validate_srt_content(srt)
        assert len(blocks) == 2

    def test_srt_empty(self):
        with pytest.raises(OutputValidationError, match="为空"):
            validate_srt_content("")

    def test_srt_wrong_sequence(self):
        srt = (
            "1\n"
            "00:00:00,000 --> 00:00:01,000\n"
            "你好\n"
            "\n"
            "3\n"
            "00:00:01,000 --> 00:00:02,000\n"
            "世界\n"
        )
        with pytest.raises(OutputValidationError, match="序号不连续"):
            validate_srt_content(srt)

    def test_srt_bad_timestamp(self):
        srt = "1\n00:00:00 --> 00:00:01\n你好\n"
        with pytest.raises(OutputValidationError, match="时间戳格式错误"):
            validate_srt_content(srt)

    def test_srt_start_after_end(self):
        srt = "1\n00:00:05,000 --> 00:00:01,000\n你好\n"
        with pytest.raises(OutputValidationError, match="start.*>=.*end"):
            validate_srt_content(srt)

    def test_srt_comma_separator(self):
        srt = "1\n00:00:00,000 --> 00:00:01,500\n你好\n"
        validate_srt_content(srt)


class TestValidateVttContent:
    def test_valid_vtt(self):
        vtt = (
            "WEBVTT\n"
            "\n"
            "00:00:00.000 --> 00:00:01.500\n"
            "你好\n"
            "\n"
            "00:00:01.500 --> 00:00:03.000\n"
            "世界\n"
        )
        cues = validate_vtt_content(vtt)
        assert len(cues) == 2

    def test_vtt_no_header(self):
        vtt = "00:00:00.000 --> 00:00:01.000\n你好\n"
        with pytest.raises(OutputValidationError, match="WEBVTT"):
            validate_vtt_content(vtt)

    def test_vtt_empty(self):
        with pytest.raises(OutputValidationError, match="为空"):
            validate_vtt_content("")


class TestValidateJsonContent:
    def test_valid_json_array(self):
        data = [{"start": 0.0, "end": 1.5, "text": "你好"}]
        result = validate_json_content(json.dumps(data))
        assert len(result) == 1

    def test_json_empty(self):
        with pytest.raises(OutputValidationError, match="为空"):
            validate_json_content("")

    def test_json_invalid(self):
        with pytest.raises(OutputValidationError, match="解析失败"):
            validate_json_content("{invalid}")

    def test_json_missing_field(self):
        data = [{"start": 0.0, "end": 1.5}]
        with pytest.raises(OutputValidationError, match="缺少字段"):
            validate_json_content(json.dumps(data))


class TestValidateTranscriptSegments:
    def test_valid_segments(self):
        segs = [_make_segment(0.0, 1.0, "你好")]
        warnings = validate_transcript_segments(segs)
        assert len(warnings) == 0

    def test_start_after_end(self):
        segs = [_make_segment(5.0, 1.0, "你好")]
        warnings = validate_transcript_segments(segs)
        assert len(warnings) == 1
        assert "start" in warnings[0]

    def test_empty_text(self):
        segs = [_make_segment(0.0, 1.0, "")]
        warnings = validate_transcript_segments(segs)
        assert len(warnings) == 1
        assert "文本为空" in warnings[0]


class TestTimestampParsing:
    def test_parse_srt_timestamp(self):
        assert _parse_srt_timestamp("00:00:01,500") == 1.5
        assert _parse_srt_timestamp("01:30:00,000") == 5400.0

    def test_parse_vtt_timestamp(self):
        assert _parse_vtt_timestamp("00:00:01.500") == 1.5
        assert _parse_vtt_timestamp("01:30:00.000") == 5400.0


class TestValidateOutputContent:
    def test_txt_content(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("你好世界", encoding="utf-8")
        validate_output_content(str(f), "txt")

    def test_txt_empty_content(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("", encoding="utf-8")
        with pytest.raises(OutputValidationError, match="内容为空"):
            validate_output_content(str(f), "txt")

    def test_srt_content_file(self, tmp_path):
        f = tmp_path / "test.srt"
        f.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n你好\n",
            encoding="utf-8",
        )
        validate_output_content(str(f), "srt")

    def test_json_content_file(self, tmp_path):
        f = tmp_path / "test.json"
        f.write_text(
            json.dumps([{"start": 0, "end": 1, "text": "你好"}]),
            encoding="utf-8",
        )
        validate_output_content(str(f), "json")
