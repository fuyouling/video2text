"""transcription_config / TranscriptionService 参数透传测试"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.config.settings import Settings
from src.config.transcription_config import (
    TranscriptionConfig,
    _build_vad_parameters,
    _load_tx_config,
    _parse_temperature,
)
from src.services.transcription_service import TranscriptionService
from src.transcription.transcriber import TranscriptSegment


@pytest.fixture(autouse=True)
def _reset_settings():
    Settings._reset()
    yield
    Settings._reset()


def _settings_with(tmp_path, body: str) -> Settings:
    cfg = tmp_path / "config.ini"
    cfg.write_text(body, encoding="utf-8")
    return Settings(config_path=str(cfg))


class TestParseTemperature:
    def test_list(self, tmp_path):
        s = _settings_with(
            tmp_path, "[transcription]\ntemperature = 0.0,0.2,0.4\n"
        )
        assert _parse_temperature(s) == [0.0, 0.2, 0.4]

    def test_single(self, tmp_path):
        s = _settings_with(tmp_path, "[transcription]\ntemperature = 0.0\n")
        assert _parse_temperature(s) == [0.0]

    def test_invalid_fallback(self, tmp_path):
        s = _settings_with(tmp_path, "[transcription]\ntemperature = abc\n")
        assert _parse_temperature(s) == [0.0]

    def test_empty_fallback(self, tmp_path):
        s = _settings_with(tmp_path, "[transcription]\nlanguage = zh\n")
        assert _parse_temperature(s) == [0.0]


class TestBuildVadParameters:
    def test_no_max_speech(self, tmp_path):
        s = _settings_with(
            tmp_path,
            "[transcription]\nvad_threshold = 0.5\nvad_min_silence_ms = 2000\n"
            "vad_speech_pad_ms = 400\nvad_max_speech_s = 0\n",
        )
        params = _build_vad_parameters(s)
        assert params == {
            "threshold": 0.5,
            "min_silence_duration_ms": 2000,
            "speech_pad_ms": 400,
        }
        assert "max_speech_duration_s" not in params

    def test_with_max_speech(self, tmp_path):
        s = _settings_with(
            tmp_path,
            "[transcription]\nvad_threshold = 0.6\nvad_min_silence_ms = 800\n"
            "vad_speech_pad_ms = 300\nvad_max_speech_s = 30\n",
        )
        params = _build_vad_parameters(s)
        assert params["max_speech_duration_s"] == 30.0


class TestLoadTxConfig:
    def test_new_fields(self, tmp_path):
        s = _settings_with(
            tmp_path,
            "[transcription]\n"
            "temperature = 0.0,0.2,0.4\n"
            "condition_on_previous_text = False\n"
            "vad_filter = True\n"
            "initial_prompt = 专有名词提示\n"
            "hotwords = 张三 李四\n"
            "compression_ratio_threshold = 2.4\n"
            "log_prob_threshold = -1.0\n"
            "no_speech_threshold = 0.6\n"
            "repetition_penalty = 1.0\n"
            "no_repeat_ngram_size = 0\n"
            "vad_threshold = 0.5\n"
            "vad_min_silence_ms = 2000\n"
            "vad_speech_pad_ms = 400\n"
            "vad_max_speech_s = 0\n",
        )
        tc = _load_tx_config(s)
        assert isinstance(tc, TranscriptionConfig)
        assert tc.temperature == [0.0, 0.2, 0.4]
        assert tc.initial_prompt == "专有名词提示"
        assert tc.hotwords == "张三 李四"
        assert tc.vad_parameters == {
            "threshold": 0.5,
            "min_silence_duration_ms": 2000,
            "speech_pad_ms": 400,
        }
        assert tc.compression_ratio_threshold == 2.4
        assert tc.log_prob_threshold == -1.0
        assert tc.no_speech_threshold == 0.6
        assert tc.repetition_penalty == 1.0
        assert tc.no_repeat_ngram_size == 0
        assert tc.vad_filter is True

    def test_vad_filter_false_disables_vad_params(self, tmp_path):
        s = _settings_with(tmp_path, "[transcription]\nvad_filter = False\n")
        tc = _load_tx_config(s)
        assert tc.vad_parameters is None


class TestTranscriptionServicePassthrough:
    @pytest.fixture
    def mock_transcriber(self):
        t = MagicMock()
        t.model_path = "models/large-v3"
        t.device = "cpu"
        t.compute_type = "int8"
        return t

    @pytest.fixture
    def mock_video_processor(self):
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
    def mock_file_writer(self):
        fw = MagicMock()
        fw.write_transcript.return_value = "/tmp/output.txt"
        return fw

    def test_single_file_passthrough(
        self, mock_transcriber, mock_video_processor, mock_file_writer
    ):
        with patch("src.services.transcription_service.Settings") as ms:
            ms.return_value = MagicMock(config_path="/tmp/config.ini")
            svc = TranscriptionService(
                transcriber=mock_transcriber,
                video_processor=mock_video_processor,
                file_writer=mock_file_writer,
                output_formats=["txt"],
                vad_parameters={"threshold": 0.5},
                initial_prompt="专有名词",
                hotwords="张三 李四",
                compression_ratio_threshold=2.0,
                log_prob_threshold=-1.5,
                no_speech_threshold=0.7,
                repetition_penalty=1.2,
                no_repeat_ngram_size=3,
            )
        svc.transcriber.transcribe.return_value = [
            TranscriptSegment(
                start=0.0, end=2.0, text="hello", confidence=95.0, language="en"
            )
        ]
        mock_video_processor.get_video_info.return_value.duration = 10.0
        svc._transcribe_single("/tmp/video.mp4", "/tmp/out")

        kwargs = mock_transcriber.transcribe.call_args.kwargs
        assert kwargs["initial_prompt"] == "专有名词"
        assert kwargs["hotwords"] == "张三 李四"
        assert kwargs["compression_ratio_threshold"] == 2.0
        assert kwargs["log_prob_threshold"] == -1.5
        assert kwargs["no_speech_threshold"] == 0.7
        assert kwargs["repetition_penalty"] == 1.2
        assert kwargs["no_repeat_ngram_size"] == 3
        assert kwargs["vad_parameters"] == {"threshold": 0.5}

    def test_chunked_passthrough(
        self, mock_transcriber, mock_video_processor, mock_file_writer, tmp_path
    ):
        with patch("src.services.transcription_service.Settings") as ms:
            ms.return_value = MagicMock(config_path="/tmp/config.ini")
            svc = TranscriptionService(
                transcriber=mock_transcriber,
                video_processor=mock_video_processor,
                file_writer=mock_file_writer,
                output_formats=["txt"],
                vad_parameters={"threshold": 0.5},
                initial_prompt="专有名词",
                hotwords="张三 李四",
                compression_ratio_threshold=2.0,
                log_prob_threshold=-1.5,
                no_speech_threshold=0.7,
                repetition_penalty=1.2,
                no_repeat_ngram_size=3,
                max_chunk_duration=300,
            )
        svc._checkpoint_dir = tmp_path / ".checkpoint"
        svc._checkpoint_dir.mkdir(parents=True, exist_ok=True)
        svc.transcriber.transcribe.return_value = [
            TranscriptSegment(
                start=0.0, end=2.0, text="hello", confidence=95.0, language="en"
            )
        ]
        mock_video_processor.get_video_info.return_value.duration = 400.0
        chunk_dir = tmp_path / "chunks"
        chunk_dir.mkdir()
        chunk = chunk_dir / "chunk_000.wav"
        chunk.write_bytes(b"\x00" * (44 + 32000))
        mock_video_processor.ffmpeg_path = "ffmpeg"
        mock_video_processor.ffprobe_path = "ffprobe"

        with patch("src.services.transcription_service.subprocess") as mock_sub:

            def _fake_split(split_cmd, **kwargs):
                out_dir = Path(split_cmd[-1]).parent
                (out_dir / "chunk_000.wav").write_bytes(b"\x00" * (44 + 32000))
                return MagicMock(returncode=0, stderr="", stdout="")

            mock_sub.run.side_effect = _fake_split
            mock_sub.CREATE_NO_WINDOW = 0
            svc._transcribe_chunked(chunk, "video", "/tmp/video.mp4", str(tmp_path))

        kwargs = mock_transcriber.transcribe.call_args.kwargs
        assert kwargs["initial_prompt"] == "专有名词"
        assert kwargs["no_repeat_ngram_size"] == 3
        assert kwargs["vad_parameters"] == {"threshold": 0.5}
