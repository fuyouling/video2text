"""Track E 专属单元测试：验证音视频预处理与转写引擎修复"""

import math
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from src.preprocessing.ffmpeg import ensure_ffmpeg, ensure_ffprobe
from src.preprocessing.video_processor import (
    VideoInfo,
    VideoProcessor,
    _safe_float,
    _safe_int,
)
from src.transcription.transcriber import (
    Transcriber,
    _clean_gpu_memory,
    _logprob_to_confidence,
)
from src.utils.exceptions import TranscriptionCancelledError, VideoFileError


class TestVideoProcessorSafeParsing:
    """测试 video_processor 中的安全数值解析与 ffprobe 容错"""

    def test_safe_float(self):
        assert _safe_float(None) == 0.0
        assert _safe_float(12.34) == 12.34
        assert _safe_float("12.34") == 12.34
        assert _safe_float("N/A") == 0.0
        assert _safe_float("null") == 0.0
        assert _safe_float("NONE") == 0.0
        assert _safe_float("") == 0.0
        assert _safe_float("invalid_str", 5.0) == 5.0

    def test_safe_int(self):
        assert _safe_int(None) == 0
        assert _safe_int(123) == 123
        assert _safe_int(123.8) == 123
        assert _safe_int("123") == 123
        assert _safe_int("123.0") == 123
        assert _safe_int("N/A") == 0
        assert _safe_int("null") == 0
        assert _safe_int("", 10) == 10
        assert _safe_int("invalid", 42) == 42

    def test_get_video_info_handles_na_and_null(self):
        """测试 ffprobe 输出包含 N/A 与 null 时的容错解析"""
        vp = VideoProcessor.__new__(VideoProcessor)
        vp.ffprobe_path = "ffprobe"

        mock_json = """{
            "streams": [
                {
                    "codec_type": "video",
                    "width": "N/A",
                    "height": null,
                    "codec_name": "h264",
                    "r_frame_rate": "N/A"
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "N/A"
                }
            ],
            "format": {
                "duration": "N/A"
            }
        }"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = mock_json

        with patch("src.preprocessing.video_processor.subprocess.run", return_value=mock_result):
            info = vp.get_video_info("dummy.mp4")
            assert info.duration == 0.0
            assert info.width == 0
            assert info.height == 0
            assert info.fps == 0.0
            assert info.audio_sample_rate == 0
            assert info.has_audio is True


class TestVideoProcessorAudioExtraction:
    """测试音频提取的路径校验、防同名覆盖与异常清理"""

    def test_extract_audio_same_path_rejected(self, tmp_path):
        vp = VideoProcessor.__new__(VideoProcessor)
        vp.supported_audio_formats = [".mp3", ".wav"]
        vp.supported_input_formats = [".mp3", ".wav", ".mp4"]
        same_file = tmp_path / "audio.wav"
        same_file.write_text("dummy")

        with pytest.raises(VideoFileError, match="不能相同"):
            vp.extract_audio(str(same_file), str(same_file))

    def test_extract_audio_cleans_corrupted_zero_byte_file(self, tmp_path):
        vp = VideoProcessor.__new__(VideoProcessor)
        vp.supported_audio_formats = [".mp3", ".wav"]
        vp.supported_input_formats = [".mp3", ".wav", ".mp4"]
        vp.ffmpeg_path = "ffmpeg"
        vp.ffprobe_path = "ffprobe"

        in_file = tmp_path / "in.mp4"
        in_file.write_text("fake video")
        out_file = tmp_path / "out.wav"

        # 模拟 PCM 提取失败并留下 0 字节文件，同时回退也失败
        def mock_pcm(*args, **kwargs):
            out_file.write_bytes(b"")  # 留下 0 字节损坏文件
            raise VideoFileError("PCM failed")

        vp._run_ffmpeg_pcm_extract = mock_pcm

        fake_info = VideoInfo(
            duration=10.0,
            width=1920,
            height=1080,
            fps=30.0,
            codec="h264",
            audio_codec="aac",
            audio_sample_rate=44100,
            has_audio=True,
        )

        with pytest.raises(VideoFileError):
            vp.extract_audio(str(in_file), str(out_file), video_info=fake_info)

        # 确保损坏的 0 字节文件被强制删除清理
        assert not out_file.exists()

    def test_extract_audio_fallback_temp_mp3_naming_and_cleanup(self, tmp_path):
        """测试回退方案采用独立临时目录加唯一随机文件名，并在结束后清理"""
        vp = VideoProcessor.__new__(VideoProcessor)
        vp.ffmpeg_path = "ffmpeg"

        in_file = tmp_path / "test_video.mp4"
        in_file.write_text("fake")
        out_file = tmp_path / "output.wav"

        created_temp_files = []

        def mock_run_ffmpeg(cmd, timeout, cancel_check=None):
            # cmd 格式中最后一个参数为输出文件
            target_path = Path(cmd[-1])
            if target_path.suffix == ".mp3":
                # 验证临时文件名符合 v2t_fallback_{stem}_{pid}_{uuid}.mp3 规范
                assert target_path.parent == Path(tempfile.gettempdir())
                assert target_path.name.startswith("v2t_fallback_test_video_")
                created_temp_files.append(target_path)
                target_path.write_bytes(b"fake mp3 content")
            elif target_path.suffix == ".wav":
                target_path.write_bytes(b"RIFF fake wav content")
            res = MagicMock()
            res.returncode = 0
            return res

        vp._run_ffmpeg_with_cancel = mock_run_ffmpeg

        res = vp._extract_audio_fallback(str(in_file), str(out_file))
        assert res == str(out_file)
        assert out_file.exists()

        # 验证临时 MP3 在 finally 中已被清理
        assert len(created_temp_files) == 1
        assert not created_temp_files[0].exists()


class TestFFmpegPathFallback:
    """测试 FFmpeg / FFprobe PATH 自动回退探测"""

    def test_ensure_ffmpeg_path_fallback(self, tmp_path):
        import src.preprocessing.ffmpeg as ffmpeg_mod
        ffmpeg_mod._ffmpeg_path = None

        fake_ffmpeg = tmp_path / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
        fake_ffmpeg.write_text("fake")

        with patch("src.preprocessing.ffmpeg._get_base_dir", return_value=tmp_path / "non_existent"), \
             patch("shutil.which", return_value=str(fake_ffmpeg)):
            found = ensure_ffmpeg()
            assert found == str(fake_ffmpeg.resolve())

    def test_ensure_ffprobe_path_fallback(self, tmp_path):
        import src.preprocessing.ffmpeg as ffmpeg_mod
        ffmpeg_mod._ffprobe_path = None

        fake_ffprobe = tmp_path / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
        fake_ffprobe.write_text("fake")

        with patch("src.preprocessing.ffmpeg._get_base_dir", return_value=tmp_path / "non_existent"), \
             patch("shutil.which", return_value=str(fake_ffprobe)):
            found = ensure_ffprobe()
            assert found == str(fake_ffprobe.resolve())


class TestTranscriberConfidenceAndLock:
    """测试 Transcriber 置信度安全防护与模型锁迭代周期保护"""

    def test_logprob_to_confidence_edge_cases(self):
        assert _logprob_to_confidence(None) == 0.0
        assert _logprob_to_confidence(float("nan")) == 0.0
        assert _logprob_to_confidence(float("inf")) == 0.0
        assert _logprob_to_confidence(float("-inf")) == 0.0
        assert _logprob_to_confidence(1000.0) == 100.0  # Overflow 安全保护
        assert _logprob_to_confidence(-1000.0) == 0.0
        assert _logprob_to_confidence(0.0) == 100.0
        assert _logprob_to_confidence(-0.5) == 60.65

    def test_model_lock_held_during_generator_iteration(self, tmp_path):
        """测试 _model_lock 在生成器迭代消费期间保持持有，杜绝迭代中卸载"""
        t = Transcriber.__new__(Transcriber)
        t._loaded = True
        t.device = "cpu"
        t._model_lock = threading.Lock()

        fake_audio = tmp_path / "test.wav"
        fake_audio.write_text("fake")

        lock_held_during_yield = []

        def generator():
            for i in range(3):
                # 检查在生成器迭代过程中，锁是否已被锁定（尝试非阻塞获取）
                acquired = t._model_lock.acquire(blocking=False)
                if acquired:
                    t._model_lock.release()
                    lock_held_during_yield.append(False)
                else:
                    # 锁正在被持有，符合预期
                    lock_held_during_yield.append(True)

                seg = MagicMock()
                seg.start = i * 1.0
                seg.end = (i + 1) * 1.0
                seg.text = f"segment {i}"
                seg.avg_logprob = -0.1
                yield seg

        mock_info = MagicMock()
        mock_info.language = "zh"
        mock_info.language_probability = 0.99

        mock_model = MagicMock()
        mock_model.transcribe.return_value = (generator(), mock_info)
        t.model = mock_model

        segments = t.transcribe(str(fake_audio))
        assert len(segments) == 3
        # 验证每次迭代时，_model_lock 均处于锁定保护状态
        assert all(lock_held_during_yield)
