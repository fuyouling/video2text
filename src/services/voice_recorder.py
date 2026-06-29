"""麦克风录音服务 —— 基于 sounddevice 捕获系统麦克风音频"""

import os
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, Signal

from src.config.settings import Settings
from src.utils.exceptions import Video2TextError
from src.utils.logger import get_logger

logger = get_logger(__name__)


class VoiceRecorder(QObject):
    """麦克风录音器

    录音循环运行在 threading.Thread 中（不依赖 moveToThread），
    通过信号向主线程传递结果。extract_chunk 可从任意线程安全调用。

    支持噪声抑制（noise_suppression）：
      - 噪声门（noise gate）：丢弃低于阈值的静默/噪底帧
      - 频谱减法（spectral subtraction）：衰减稳态背景噪声
    可显著改善非真人发声（如音箱播放、电子音等）的录音质量。
    """

    started = Signal()
    finished = Signal(str)
    chunk_ready = Signal(str)
    volume_changed = Signal(float)
    error_occurred = Signal(str)

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        settings: Optional[Settings] = None,
        noise_suppression: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._settings = settings or Settings()
        self._sample_rate = self._settings.get_int(
            "preprocessing.audio_sample_rate", sample_rate
        )
        self._channels = self._settings.get_int(
            "preprocessing.audio_channels", channels
        )
        self._noise_suppression = noise_suppression
        self._noise_gate_threshold = self._settings.get_float(
            "voice_to_text.noise_gate_threshold", 0.015,
        )
        self._spectral_subtraction_factor = self._settings.get_float(
            "voice_to_text.spectral_subtraction_factor", 1.5,
        )
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stream = None
        self._frames = []
        self._lock = threading.Lock()
        self._temp_dir = Path.cwd() / "voice"
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._latest_volume = 0.0
        self._noise_profile = None
        self._noise_profile_frames = []
        self._noise_profile_ready = False
        self._noise_profile_frame_count = self._settings.get_int(
            "voice_to_text.noise_profile_frames", 10,
        )

    def start(self) -> None:
        """启动录音（非阻塞，立即返回）"""
        self._running = True
        self._frames = []
        self._noise_profile = None
        self._noise_profile_frames = []
        self._noise_profile_ready = False
        self._thread = threading.Thread(target=self._record_loop, daemon=True)
        self._thread.start()
        self.started.emit()

    def _record_loop(self) -> None:
        """录音主循环（在 threading.Thread 中运行）"""
        try:
            import sounddevice as sd
        except ImportError as exc:
            self.error_occurred.emit(f"缺少 sounddevice 库: {exc}")
            return

        try:
            with sd.InputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="float32",
                blocksize=1024,
                callback=self._audio_callback,
            ):
                while self._running:
                    time.sleep(0.05)
        except Exception as exc:
            self.error_occurred.emit(f"录音异常: {exc}")
            return
        finally:
            self._stream = None

        try:
            wav_path = self.save_to_wav()
            self.finished.emit(wav_path)
        except Exception as exc:
            self.error_occurred.emit(f"保存录音失败: {exc}")

    def _audio_callback(
        self, indata, frames, time_info, status
    ) -> None:
        """sounddevice 音频回调（在 PortAudio 线程中执行）"""
        if status:
            logger.warning("VoiceRecorder 回调状态: %s", status)
        if self._running:
            processed = indata.copy()
            if self._noise_suppression:
                processed = self._apply_noise_suppression(processed)
                rms = float(np.sqrt(np.mean(processed * processed)))
                if rms < self._noise_gate_threshold:
                    return
            with self._lock:
                self._frames.append(processed)
            volume = float(np.sqrt(np.mean(indata * indata)))
            self._latest_volume = volume
            self.volume_changed.emit(volume)

    def _apply_noise_suppression(self, frame: np.ndarray) -> np.ndarray:
        """对单帧音频应用频谱减法噪声抑制"""
        if not self._noise_profile_ready:
            self._noise_profile_frames.append(frame.copy())
            if len(self._noise_profile_frames) >= self._noise_profile_frame_count:
                self._build_noise_profile()
            return frame
        return self._spectral_subtract(frame)

    def _build_noise_profile(self) -> None:
        """从初始静默帧构建噪声频谱参考（与单帧 FFT 尺寸对齐）"""
        try:
            n = self._noise_profile_frames[0].shape[0]
            mag_sum = None
            count = 0
            for frm in self._noise_profile_frames:
                samples = frm.flatten()
                if len(samples) < n:
                    samples = np.pad(samples, (0, n - len(samples)))
                spectrum = np.fft.rfft(samples[:n])
                mag_sum = np.abs(spectrum) if mag_sum is None else mag_sum + np.abs(spectrum)
                count += 1
            self._noise_profile = mag_sum / max(count, 1)
            self._noise_profile_ready = True
        except Exception:
            logger.debug("构建噪声轮廓失败")
        self._noise_profile_frames = []

    def _spectral_subtract(self, frame: np.ndarray) -> np.ndarray:
        """频谱减法：用噪声轮廓衰减稳态噪声"""
        if self._noise_profile is None:
            return frame
        try:
            samples = frame.flatten()
            n = len(samples)
            if n == 0:
                return frame
            spectrum = np.fft.rfft(samples)
            magnitude = np.abs(spectrum)
            phase = np.angle(spectrum)
            noise_mag = self._noise_profile[:len(magnitude)]
            if len(noise_mag) < len(magnitude):
                noise_mag = np.pad(noise_mag, (0, len(magnitude) - len(noise_mag)))
            alpha = self._spectral_subtraction_factor
            enhanced = np.maximum(magnitude - alpha * noise_mag, 0.0)
            new_spectrum = enhanced * np.exp(1j * phase)
            cleaned = np.fft.irfft(new_spectrum, n=n)
            return cleaned.reshape(frame.shape).astype(np.float32)
        except Exception:
            return frame

    @property
    def noise_suppression(self) -> bool:
        return self._noise_suppression

    @noise_suppression.setter
    def noise_suppression(self, value: bool) -> None:
        self._noise_suppression = value

    def stop(self) -> None:
        """请求停止录音"""
        self._running = False

    def is_running(self) -> bool:
        return self._running

    def save_to_wav(self) -> str:
        """将当前缓冲区音频保存为 WAV 文件，返回路径"""
        with self._lock:
            if not self._frames:
                raise Video2TextError("没有录制到音频数据")
            audio = np.concatenate(self._frames, axis=0)
            self._frames = []

        audio_int16 = (audio * 32767).astype(np.int16)
        timestamp = time.strftime("%Y%d%m%H%M%S")
        wav_path = self._temp_dir / f"voice_{timestamp}.wav"

        try:
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(self._channels)
                wf.setsampwidth(2)
                wf.setframerate(self._sample_rate)
                wf.writeframes(audio_int16.tobytes())
        except Exception as exc:
            raise Video2TextError(f"保存 WAV 失败: {exc}") from exc

        return str(wav_path)

    def extract_chunk(self) -> Optional[str]:
        """提取当前缓冲区为 WAV（实时录入模式专用），返回路径或 None

        线程安全：内部有锁保护，可从任意线程调用。
        """
        try:
            return self.save_to_wav()
        except Video2TextError:
            return None
