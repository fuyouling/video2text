"""TranscriptionService 单元测试"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.transcription_service import TranscriptionService, TranscribeResult
from src.transcription.transcriber import TranscriptSegment


@pytest.fixture
def mock_transcriber():
    t = MagicMock()
    t.model_path = "models/large-v3"
    t.device = "cpu"
    t.compute_type = "int8"
    t.transcribe.return_value = (
        [
            TranscriptSegment(
                start=0.0, end=2.0, text="hello", confidence=95.0, language="en"
            )
        ],
        MagicMock(language="en", language_probability=0.99),
    )
    return t


@pytest.fixture
def mock_video_processor():
    vp = MagicMock()
    vp.validate_input.return_value = True
    vp.is_audio_file.return_value = False
    vp.get_video_info.return_value = MagicMock(
        duration=10.0,
        has_audio=True,
        width=1920,
        height=1080,
        fps=30.0,
        codec="h264",
        audio_codec="aac",
        audio_sample_rate=44100,
    )
    vp.extract_audio.return_value = "/tmp/audio.wav"
    return vp


@pytest.fixture
def mock_file_writer():
    fw = MagicMock()
    fw.write_transcript.return_value = "/tmp/output.txt"
    return fw


@pytest.fixture
def service(mock_transcriber, mock_video_processor, mock_file_writer):
    with patch("src.services.transcription_service.Settings") as mock_settings_cls:
        mock_settings = MagicMock()
        mock_settings.config_path = "/tmp/config.ini"
        mock_settings_cls.return_value = mock_settings
        svc = TranscriptionService(
            transcriber=mock_transcriber,
            video_processor=mock_video_processor,
            file_writer=mock_file_writer,
            output_formats=["txt"],
        )
    return svc


class TestTranscriptionService:
    def test_pause_resume(self, service):
        assert not service.is_paused
        service.pause()
        assert service.is_paused
        service.resume()
        assert not service.is_paused

    def test_get_chunk_duration_from_ffprobe(self, service, tmp_path):
        chunk = tmp_path / "chunk.wav"
        chunk.write_bytes(b"\x00" * 100)
        service.video_processor.ffprobe_path = "ffprobe"

        with patch("src.services.transcription_service.subprocess") as mock_sub:
            mock_sub.run.return_value = MagicMock(stdout="5.0\n", returncode=0)
            mock_sub.CREATE_NO_WINDOW = 0
            duration = service._get_chunk_duration(chunk)
        assert duration == 5.0

    def test_get_chunk_duration_fallback_to_segments(self, service, tmp_path):
        chunk = tmp_path / "chunk.wav"
        chunk.write_bytes(b"\x00" * 100)
        service.video_processor.ffprobe_path = "ffprobe"

        seg = MagicMock()
        seg.end = 3.5

        with patch("src.services.transcription_service.subprocess") as mock_sub:
            mock_sub.run.side_effect = Exception("ffprobe not found")
            mock_sub.CREATE_NO_WINDOW = 0
            duration = service._get_chunk_duration(chunk, segments=[seg])
        assert duration == 3.5

    def test_get_chunk_duration_fallback_to_file_size(self, service, tmp_path):
        chunk = tmp_path / "chunk.wav"
        # 44 byte header + 32000 bytes data = 32000 / (16000*1*2) = 1.0 sec
        chunk.write_bytes(b"\x00" * (44 + 32000))
        service.video_processor.ffprobe_path = "ffprobe"

        with patch("src.services.transcription_service.subprocess") as mock_sub:
            mock_sub.run.side_effect = Exception("ffprobe not found")
            mock_sub.CREATE_NO_WINDOW = 0
            duration = service._get_chunk_duration(chunk)
        assert duration == pytest.approx(1.0)

    def test_save_history_record_error_handling(self, service, tmp_path):
        service._history_file = tmp_path / "history.json"

        with patch(
            "src.services.transcription_service.atomic_write_json",
            side_effect=OSError("disk full"),
        ):
            service._save_history_record(10.0, 5.0)

    def test_load_history_empty(self, service, tmp_path):
        service._history_file = tmp_path / "nonexistent.json"
        assert service._load_history() == []

    def test_load_history_valid(self, service, tmp_path):
        import json

        service._history_file = tmp_path / "history.json"
        data = {"records": [{"model": "large-v3", "device": "cpu"}]}
        service._history_file.write_text(json.dumps(data), encoding="utf-8")
        records = service._load_history()
        assert len(records) == 1
        assert records[0]["model"] == "large-v3"

    def test_estimate_returns_none_when_no_history(self, service, tmp_path):
        service._history_file = tmp_path / "history.json"
        assert service._estimate_transcribe_time(10.0) is None

    def test_estimate_returns_none_when_below_threshold(self, service, tmp_path):
        import json, time

        service._history_file = tmp_path / "history.json"
        now = time.time()
        records = [
            {
                "model": "large-v3",
                "device": "cpu",
                "compute_type": "int8",
                "audio_duration": 10.0,
                "transcribe_time": 5.0,
                "timestamp": now,
            },
            {
                "model": "large-v3",
                "device": "cpu",
                "compute_type": "int8",
                "audio_duration": 20.0,
                "transcribe_time": 8.0,
                "timestamp": now,
            },
        ]
        service._history_file.write_text(
            json.dumps({"records": records}), encoding="utf-8"
        )
        assert service._estimate_transcribe_time(10.0) is None

    def test_estimate_returns_value_when_enough_samples(self, service, tmp_path):
        import json, time

        service._history_file = tmp_path / "history.json"
        now = time.time()
        records = [
            {
                "model": "large-v3",
                "device": "cpu",
                "compute_type": "int8",
                "audio_duration": 10.0,
                "transcribe_time": 5.0,
                "timestamp": now,
            },
            {
                "model": "large-v3",
                "device": "cpu",
                "compute_type": "int8",
                "audio_duration": 20.0,
                "transcribe_time": 10.0,
                "timestamp": now,
            },
            {
                "model": "large-v3",
                "device": "cpu",
                "compute_type": "int8",
                "audio_duration": 30.0,
                "transcribe_time": 15.0,
                "timestamp": now,
            },
        ]
        service._history_file.write_text(
            json.dumps({"records": records}), encoding="utf-8"
        )
        result = service._estimate_transcribe_time(10.0)
        assert result is not None
        assert result == pytest.approx(5.0, rel=0.1)

    def test_estimate_recent_records_have_higher_weight(self, service, tmp_path):
        import json, time

        service._history_file = tmp_path / "history.json"
        now = time.time()
        old_ts = now - 60 * 86400  # 60 days ago
        records = [
            {
                "model": "large-v3",
                "device": "cpu",
                "compute_type": "int8",
                "audio_duration": 10.0,
                "transcribe_time": 5.0,
                "timestamp": old_ts,  # speed 0.5
            },
            {
                "model": "large-v3",
                "device": "cpu",
                "compute_type": "int8",
                "audio_duration": 10.0,
                "transcribe_time": 5.0,
                "timestamp": old_ts,  # speed 0.5
            },
            {
                "model": "large-v3",
                "device": "cpu",
                "compute_type": "int8",
                "audio_duration": 10.0,
                "transcribe_time": 3.0,
                "timestamp": now,  # speed 0.3
            },
        ]
        service._history_file.write_text(
            json.dumps({"records": records}), encoding="utf-8"
        )
        result = service._estimate_transcribe_time(10.0)
        assert result is not None
        # Recent record (speed 0.3) should dominate due to higher weight
        assert result < 4.0

    def test_estimate_no_timestamp_fallback(self, service, tmp_path):
        import json

        service._history_file = tmp_path / "history.json"
        records = [
            {
                "model": "large-v3",
                "device": "cpu",
                "compute_type": "int8",
                "audio_duration": 10.0,
                "transcribe_time": 5.0,  # no timestamp, speed 0.5
            },
            {
                "model": "large-v3",
                "device": "cpu",
                "compute_type": "int8",
                "audio_duration": 20.0,
                "transcribe_time": 10.0,  # no timestamp, speed 0.5
            },
            {
                "model": "large-v3",
                "device": "cpu",
                "compute_type": "int8",
                "audio_duration": 30.0,
                "transcribe_time": 15.0,  # no timestamp, speed 0.5
            },
        ]
        service._history_file.write_text(
            json.dumps({"records": records}), encoding="utf-8"
        )
        result = service._estimate_transcribe_time(10.0)
        assert result is not None
        assert result == pytest.approx(5.0, rel=0.01)

    def test_save_history_record_includes_timestamp(self, service, tmp_path):
        import json, time

        service._history_file = tmp_path / "history.json"
        before = time.time()
        service._save_history_record(10.0, 5.0)
        after = time.time()

        data = json.loads(service._history_file.read_text(encoding="utf-8"))
        records = data["records"]
        assert len(records) == 1
        ts = records[0].get("timestamp")
        assert ts is not None
        assert before <= ts <= after
