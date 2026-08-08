"""停止/取消相关修复的单元测试

覆盖：
- Transcriber 段级取消：cancel_check 置位时抛出 TranscriptionCancelledError
- TranscriptionService.run 对取消静默中止（不调用 on_video_error）
- VideoProcessor ffmpeg 提取可中断：取消时终止子进程并抛出异常
- TranscriptionService 把 cancel_check 传给底层 transcriber
"""

from unittest.mock import MagicMock, patch

import pytest

from src.preprocessing.video_processor import VideoProcessor
from src.services.transcription_service import TranscriptionService
from src.transcription.transcriber import Transcriber
from src.utils.exceptions import TranscriptionCancelledError


@pytest.fixture
def service():
    """与 tests/test_transcription_service.py 等价的 mock 服务实例"""
    with patch("src.services.transcription_service.Settings") as mock_settings_cls:
        mock_settings = MagicMock()
        mock_settings.config_path = "/tmp/config.ini"
        mock_settings_cls.return_value = mock_settings

        transcriber = MagicMock()
        transcriber.model_path = "models/large-v3"
        transcriber.device = "cpu"
        transcriber.compute_type = "int8"

        video_processor = MagicMock()
        video_processor.validate_input.return_value = True
        video_processor.is_audio_file.return_value = False
        video_processor.get_video_info.return_value = MagicMock(
            duration=10.0, has_audio=True
        )
        video_processor.extract_audio.return_value = "/tmp/audio.wav"

        file_writer = MagicMock()
        file_writer.write_transcript.return_value = "/tmp/output.txt"

        svc = TranscriptionService(
            transcriber=transcriber,
            video_processor=video_processor,
            file_writer=file_writer,
            output_formats=["txt"],
        )
        svc.transcriber = transcriber
        svc.video_processor = video_processor
        svc.file_writer = file_writer
    return svc


class TestTranscriberSegmentCancel:
    def _make_transcriber(self, tmp_path) -> Transcriber:
        tr = Transcriber(model_path="dummy", device="cpu", compute_type="int8")
        tr._loaded = True
        tr.model = MagicMock()
        return tr

    def test_cancel_check_raises_cancelled(self, tmp_path):
        tr = self._make_transcriber(tmp_path)
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"\x00" * 100)

        def fake_segments():
            yield MagicMock(start=0.0, end=1.0, text="a", avg_logprob=-0.1)
            yield MagicMock(start=1.0, end=2.0, text="b", avg_logprob=-0.1)

        tr.model.transcribe.return_value = (
            fake_segments(),
            MagicMock(language="en", language_probability=0.99),
        )

        calls = {"n": 0}

        def cancel_check():
            calls["n"] += 1
            return calls["n"] >= 2  # 转写第二个段时用户取消

        with pytest.raises(TranscriptionCancelledError):
            tr.transcribe(str(audio), cancel_check=cancel_check)

    def test_no_cancel_check_transcribes_normally(self, tmp_path):
        tr = self._make_transcriber(tmp_path)
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"\x00" * 100)
        segs = [MagicMock(start=0.0, end=1.0, text="hello", avg_logprob=-0.1)]
        tr.model.transcribe.return_value = (
            iter(segs),
            MagicMock(language="en", language_probability=0.99),
        )
        result = tr.transcribe(str(audio))
        assert len(result) == 1


class TestServiceCancel:
    def test_run_stops_silently_on_cancel(self, service):
        """转写中途取消：不抛异常、不调用 on_video_error、结果为 []"""
        service.cancel_check = lambda: False
        service.transcriber.transcribe.side_effect = TranscriptionCancelledError(
            "user cancelled"
        )
        on_error = MagicMock()
        service.on_video_error = on_error

        results = service.run(["/tmp/video.mp4"], "/tmp/out")

        assert results == []
        on_error.assert_not_called()

    def test_cancel_before_first_file_breaks(self, service):
        """文件循环开始前取消：直接 break，不进入转写"""
        service.cancel_check = lambda: True
        on_error = MagicMock()
        service.on_video_error = on_error

        results = service.run(["/tmp/video.mp4"], "/tmp/out")

        assert results == []
        on_error.assert_not_called()
        service.transcriber.transcribe.assert_not_called()

    def test_timeout_passes_cancel_check_to_transcriber(self, service):
        """_transcribe_with_timeout 应把 cancel_check 传给 transcriber.transcribe"""
        service.cancel_check = lambda: True
        service.transcriber.transcribe.return_value = iter([])

        service._transcribe_with_timeout(
            "/tmp/audio.wav",
            timeout=30,
        )

        _, kwargs = service.transcriber.transcribe.call_args
        assert kwargs.get("cancel_check") is service.cancel_check


class TestVideoProcessorCancel:
    def test_cancel_kills_ffmpeg_subprocess(self):
        """取消标志置位时：终止子进程并抛 TranscriptionCancelledError"""
        vp = VideoProcessor.__new__(VideoProcessor)
        proc = MagicMock()
        proc.poll.return_value = None
        proc.kill.return_value = None
        proc.wait.return_value = None

        with patch(
            "src.preprocessing.video_processor.subprocess.Popen",
            return_value=proc,
        ):
            with pytest.raises(TranscriptionCancelledError):
                vp._run_ffmpeg_with_cancel(
                    ["ffmpeg", "-i", "in.mp4", "out.wav"],
                    timeout=60,
                    cancel_check=lambda: True,
                )

        proc.kill.assert_called_once()

    def test_no_cancel_normal_completion(self):
        """无取消时正常返回 CompletedProcess"""
        vp = VideoProcessor.__new__(VideoProcessor)
        proc = MagicMock()
        proc.communicate.return_value = ("", "")
        proc.returncode = 0
        proc.poll.return_value = None

        with patch(
            "src.preprocessing.video_processor.subprocess.Popen",
            return_value=proc,
        ):
            result = vp._run_ffmpeg_with_cancel(
                ["ffmpeg", "-i", "in.mp4", "out.wav"],
                timeout=60,
            )

        assert result.returncode == 0
