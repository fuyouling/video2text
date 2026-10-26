"""FunASR 转写器及相关适配单元测试"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import threading
import numpy as np
import pytest

from src.transcription.transcriber import TranscriptSegment, get_cached_transcriber
from src.transcription.funasr_transcriber import FunASRTranscriber
from src.utils.model_downloader import PARAFORMER_FILES
from src.ui.startup_confirm_dialog import StartupConfirmDialog


def test_paraformer_files_definition():
    """验证 Paraformer 下载配置完整性"""
    assert len(PARAFORMER_FILES) == 10
    assert "asr/model_quant.onnx" in PARAFORMER_FILES
    assert "vad/model_quant.onnx" in PARAFORMER_FILES
    assert "punc/model_quant.onnx" in PARAFORMER_FILES
    for path, url in PARAFORMER_FILES.items():
        assert url.startswith("https://www.modelscope.cn/")


def test_transcriber_routing_whisper():
    """测试 Whisper 引擎路由"""
    with patch("src.transcription.transcriber.Transcriber") as mock_transcriber_cls:
        mock_instance = MagicMock()
        mock_transcriber_cls.return_value = mock_instance
        t = get_cached_transcriber(
            model_path="models/faster-whisper-large-v3-turbo-ct2",
            device="cpu",
            compute_type="int8",
            engine="faster-whisper",
        )
        assert t is mock_instance


def test_transcriber_routing_funasr():
    """测试 FunASR 引擎路由"""
    with patch("src.transcription.funasr_transcriber.FunASRTranscriber.__init__", return_value=None) as mock_init:
        t = get_cached_transcriber(
            model_path="models/paraformer-large-vad-punc",
            device="cpu",
            engine="funasr",
        )
        assert isinstance(t, FunASRTranscriber)
        mock_init.assert_called_once()


def test_funasr_transcribe_mock(tmp_path):
    """测试 FunASR 转写与段落切分逻辑"""
    with patch("src.transcription.funasr_transcriber.FunASRTranscriber.__init__", return_value=None):
        transcriber = FunASRTranscriber("models/paraformer", device="cpu")
        transcriber.model_path = "models/paraformer"
        transcriber.device = "cpu"
        transcriber.compute_type = "int8"
        transcriber.num_workers = 1
        transcriber.quantize = True
        transcriber.punc_filter = True
        transcriber.vad_max_end_sil = 800
        transcriber._loaded = True
        transcriber._model_lock = threading.Lock()
        transcriber.asr_model = MagicMock()
        transcriber.vad_model = MagicMock()
        transcriber.punc_model = MagicMock()

        # 创建一个真实的临时 WAV 文件 (3 秒 16kHz float32 音频)
        import soundfile as sf
        fake_wav = tmp_path / "test.wav"
        fake_audio = np.zeros(48000, dtype=np.float32)
        sf.write(str(fake_wav), fake_audio, 16000)

        # 模拟 VAD 输出：两个段落 [[0, 1500], [1600, 3000]] (ms)
        transcriber.vad_model.return_value = [[[0, 1500], [1600, 3000]]]

        # 模拟 ASR 输出
        transcriber.asr_model.return_value = [{"preds": "识别文本测试"}]

        # 模拟 Punc 输出
        transcriber.punc_model.return_value = ("识别文本测试。", None)

        segments = transcriber.transcribe(str(fake_wav), hotwords="测试")

        assert len(segments) == 2
        assert segments[0].text == "识别文本测试。"
        assert segments[0].start == 0.0
        assert segments[0].end == 1.5
        assert segments[1].start == 1.6
        assert segments[1].end == 3.0


def test_funasr_punc_filter_disabled(tmp_path):
    """测试关闭标点恢复时 punc_model 不被调用"""
    with patch("src.transcription.funasr_transcriber.FunASRTranscriber.__init__", return_value=None):
        transcriber = FunASRTranscriber("models/paraformer", device="cpu")
        transcriber.model_path = "models/paraformer"
        transcriber.device = "cpu"
        transcriber.compute_type = "int8"
        transcriber.num_workers = 1
        transcriber.quantize = True
        transcriber.punc_filter = False
        transcriber.vad_max_end_sil = 800
        transcriber._loaded = True
        transcriber._model_lock = threading.Lock()
        transcriber.asr_model = MagicMock()
        transcriber.vad_model = MagicMock()
        transcriber.punc_model = MagicMock()

        import soundfile as sf
        fake_wav = tmp_path / "test_nopunc.wav"
        fake_audio = np.zeros(16000, dtype=np.float32)
        sf.write(str(fake_wav), fake_audio, 16000)

        transcriber.vad_model.return_value = [[[0, 1000]]]
        transcriber.asr_model.return_value = [{"preds": "无标点文本"}]

        segments = transcriber.transcribe(str(fake_wav))

        assert len(segments) == 1
        assert segments[0].text == "无标点文本"
        transcriber.punc_model.assert_not_called()


def test_startup_confirm_dialog():
    """测试启动依赖确认对话框返回结构"""
    from PySide6.QtWidgets import QApplication
    import sys
    app = QApplication.instance() or QApplication(sys.argv)

    # 1. 均缺失且默认选中
    dialog = StartupConfirmDialog(model_missing=True, dll_missing=False, whisper_missing=True, funasr_missing=True)
    res = dialog.get_result()
    assert res["download_model"] is True
    assert "faster-whisper-large-v3-turbo-ct2" in res["target_models"]
    assert "paraformer-large-vad-punc" in res["target_models"]
    assert res["download_dll"] is False

    # 2. 仅 FunASR 缺失
    dialog2 = StartupConfirmDialog(model_missing=True, dll_missing=False, whisper_missing=False, funasr_missing=True)
    res2 = dialog2.get_result()
    assert res2["target_models"] == ["paraformer-large-vad-punc"]
    assert res2["download_whisper"] is False
    assert res2["download_funasr"] is True
