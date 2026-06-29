"""实时转写服务 —— 基于 faster-whisper 对音频片段异步转写"""

import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

from src.config.settings import Settings
from src.transcription.transcriber import get_cached_transcriber
from src.utils.exceptions import TranscriptionError, Video2TextError
from src.utils.logger import get_logger

logger = get_logger(__name__)

_converter = None


def _to_simplified(text: str) -> str:
    global _converter
    if _converter is None:
        try:
            from opencc import OpenCC
            _converter = OpenCC("t2s")
        except ImportError:
            _converter = False
    if _converter:
        try:
            return _converter.convert(text)
        except Exception:
            pass
    return text


class VoiceTranscriptionService(QObject):
    """语音转写服务

    复用现有 Transcriber 模型缓存，对临时 WAV 文件执行转写。
    运行在独立 QThread 中，通过信号回传结果。
    """

    text_ready = Signal(str)
    error_occurred = Signal(str)

    def __init__(
        self, settings: Optional[Settings] = None, parent=None
    ):
        super().__init__(parent)
        self._settings = settings or Settings()
        self._transcriber = None
        self._lock = threading.Lock()

    def _get_transcriber(self):
        if self._transcriber is not None:
            return self._transcriber
        model = self._settings.get(
            "transcription.model_path", "large-v3",
        )
        device = self._settings.get(
            "transcription.device", "auto",
        )
        compute_type = self._settings.get(
            "transcription.compute_type", "float16",
        )
        num_workers = self._settings.get_int(
            "transcription.num_workers", 1
        )
        with self._lock:
            self._transcriber = get_cached_transcriber(
                model_path=model,
                device=device,
                compute_type=compute_type,
                num_workers=num_workers,
            )
        return self._transcriber

    def transcribe_file(self, wav_path: str, noise_suppression: bool = False) -> str:
        """转写单个 WAV 文件，返回文本

        Args:
            wav_path: WAV 文件路径
            noise_suppression: 是否启用增强 VAD 过滤（对非真人音频更严格的语音检测）
        """
        path = Path(wav_path)
        if not path.exists():
            raise Video2TextError(f"音频文件不存在: {wav_path}")

        try:
            transcriber = self._get_transcriber()
            language = self._settings.get(
                "transcription.language", "zh",
            )
            vad_filter = self._settings.get_bool(
                "transcription.vad_filter", True,
            )

            vad_params = {}
            if vad_filter:
                vad_params = {
                    "threshold": self._settings.get_float(
                        "transcription.vad_onset", 0.500,
                    ),
                }
                if noise_suppression:
                    vad_params["threshold"] = self._settings.get_float(
                        "voice_to_text.vad_onset_enhanced", 0.300,
                    )

            temperature = self._settings.get_float(
                "transcription.temperature", 0.0,
            )
            if noise_suppression:
                temperatures = [0.0, 0.2, 0.4]
            else:
                temperatures = [temperature] if temperature > 0 else [0.0]

            segments = transcriber.transcribe(
                str(path),
                language=language if language != "auto" else None,
                vad_filter=vad_filter,
                vad_parameters=vad_params if vad_params else None,
                temperature=temperatures,
            )

            parts = []
            for seg in segments:
                parts.append(seg.text.strip())
            text = " ".join(parts).strip()
            if not text:
                text = "(未检测到语音内容)"
            text = _to_simplified(text)
            return text

        except Exception as exc:
            logger.error("VoiceTranscriptionService 转写失败: %s", exc)
            raise TranscriptionError(f"转写失败: {exc}") from exc

    def unload(self) -> None:
        """卸载模型（返回主界面时调用）"""
        with self._lock:
            if self._transcriber is not None:
                try:
                    self._transcriber.unload_model()
                except Exception:
                    pass
                self._transcriber = None