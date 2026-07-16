"""实时转写服务 —— 基于 faster-whisper 对音频片段异步转写，支持上下文提示"""

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
    所有配置从 [voice_to_text] 段独立读取。
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
        self._last_text = ""

    def _get_transcriber(self):
        if self._transcriber is not None:
            return self._transcriber
        model = self._settings.get(
            "voice_to_text.model_path", "large-v3",
        )
        device = self._settings.get(
            "voice_to_text.device", "auto",
        )
        compute_type = self._settings.get(
            "voice_to_text.compute_type", "float16",
        )
        num_workers = self._settings.get_int(
            "voice_to_text.num_workers", 1
        )
        with self._lock:
            self._transcriber = get_cached_transcriber(
                model_path=model,
                device=device,
                compute_type=compute_type,
                num_workers=num_workers,
            )
        return self._transcriber

    def _build_vad_parameters(self) -> Optional[dict]:
        """从 [voice_to_text] 段构建 VAD 参数"""
        if not self._settings.get_bool("voice_to_text.vad_filter", True):
            return None
        return {
            "threshold": self._settings.get_float(
                "voice_to_text.vad_threshold", 0.5
            ),
            "min_speech_duration_ms": self._settings.get_int(
                "voice_to_text.vad_min_silence_ms", 2000
            ),
            "min_silence_duration_ms": self._settings.get_int(
                "voice_to_text.vad_min_silence_ms", 2000
            ),
            "speech_pad_ms": self._settings.get_int(
                "voice_to_text.vad_speech_pad_ms", 400
            ),
            "max_speech_duration_s": self._settings.get_int(
                "voice_to_text.vad_max_speech_s", 0
            ),
        }

    def _build_context_prompt(self, previous_text: str = "") -> str:
        """构建上下文提示词，用于连续转写时保持语义连贯"""
        if not previous_text:
            return ""
        max_ctx_chars = self._settings.get_int(
            "voice_to_text.context_max_chars", 200
        )
        text = previous_text.strip()
        if len(text) > max_ctx_chars:
            text = text[-max_ctx_chars:]
        return text

    def preload_model(self) -> None:
        """预加载模型到内存（在进入 VoiceToText 界面时调用），避免第一次转写时卡顿"""
        transcriber = self._get_transcriber()
        transcriber.load_model()

    def transcribe_file(
        self,
        wav_path: str,
        previous_text: str = "",
    ) -> str:
        """转写单个 WAV 文件，返回文本

        Args:
            wav_path: WAV 文件路径
            previous_text: 上一段转写文本，用于上下文提示（提升连续转写准确率）
        """
        path = Path(wav_path)
        if not path.exists():
            raise Video2TextError(f"音频文件不存在: {wav_path}")

        try:
            transcriber = self._get_transcriber()
            language = self._settings.get(
                "voice_to_text.language", "zh",
            )
            vad_filter = self._settings.get_bool(
                "voice_to_text.vad_filter", True,
            )
            vad_params = self._build_vad_parameters() if vad_filter else None

            context_prompt = self._build_context_prompt(previous_text)
            initial_prompt = self._settings.get(
                "voice_to_text.initial_prompt", ""
            )
            if context_prompt:
                if initial_prompt:
                    initial_prompt = f"{initial_prompt}\n\n上文: {context_prompt}"
                else:
                    initial_prompt = f"上文: {context_prompt}"

            segments = transcriber.transcribe(
                str(path),
                language=language if language != "auto" else None,
                vad_filter=vad_filter,
                vad_parameters=vad_params,
                temperature=[0.0, 0.2, 0.4],
                condition_on_previous_text=True,
                initial_prompt=initial_prompt,
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
