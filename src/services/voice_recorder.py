"""麦克风录音服务 —— 基于 sounddevice 捕获系统麦克风音频，支持VAD端点检测"""

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

    支持实时VAD端点检测：
       - 自动校准噪底能量
       - 检测语音开始/结束
       - 语音结束后立即发出 speech_ended 信号
    """

    started = Signal()
    finished = Signal(str)
    chunk_ready = Signal(str)
    speech_ended = Signal(str)
    volume_changed = Signal(float)
    error_occurred = Signal(str)

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        settings: Optional[Settings] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._settings = settings or Settings()
        self._sample_rate = self._settings.get_int(
            "voice_to_text.audio_sample_rate", sample_rate
        )
        self._channels = self._settings.get_int(
            "voice_to_text.audio_channels", channels
        )
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stream = None
        self._frames = []
        self._lock = threading.Lock()
        self._temp_dir = Path.cwd() / "voice"
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._latest_volume = 0.0

        # VAD 端点检测参数
        self._vad_enabled = self._settings.get_bool(
            "voice_to_text.vad_endpoint_detection", True
        )
        self._vad_energy_threshold = self._settings.get_float(
            "voice_to_text.vad_energy_threshold", 0.0
        )
        self._vad_silence_frames = self._settings.get_int(
            "voice_to_text.vad_silence_frames", 30
        )
        self._vad_min_speech_frames = self._settings.get_int(
            "voice_to_text.vad_min_speech_frames", 10
        )
        self._vad_calibration_frames = self._settings.get_int(
            "voice_to_text.vad_calibration_frames", 30
        )
        self._blocksize = 512

        # VAD 运行时状态
        self._calibrated = False
        self._noise_floor = 0.0
        self._speech_active = False
        self._silence_frame_count = 0
        self._speech_frame_count = 0
        self._calibration_frames = []
        self._speech_start_idx = 0
        self._vad_initialized = False

    def _init_vad(self) -> None:
        if self._vad_initialized:
            return
        self._vad_initialized = True
        if self._vad_energy_threshold > 0:
            self._calibrated = True
            self._noise_floor = self._vad_energy_threshold

    def _calibrate_noise_floor(self, frame: np.ndarray) -> None:
        if self._calibrated:
            return
        self._calibration_frames.append(frame)
        if len(self._calibration_frames) >= self._vad_calibration_frames:
            energies = []
            for f in self._calibration_frames:
                rms = float(np.sqrt(np.mean(f * f)))
                energies.append(rms)
            if energies:
                self._noise_floor = float(np.mean(energies)) * 1.2
                if self._noise_floor < 0.005:
                    self._noise_floor = 0.005
            self._calibrated = True
            self._calibration_frames = []
            logger.info(
                "VoiceRecorder VAD 校准完成, noise_floor=%.6f", self._noise_floor
            )

    def _detect_speech_end(self, frame: np.ndarray) -> bool:
        if not self._vad_enabled or not self._calibrated:
            return False
        rms = float(np.sqrt(np.mean(frame * frame)))
        threshold = self._noise_floor * 2.0
        if rms > threshold:
            self._silence_frame_count = 0
            if not self._speech_active:
                self._speech_active = True
                self._speech_start_idx = max(0, len(self._frames) - 1)
            self._speech_frame_count += 1
        else:
            if self._speech_active:
                self._silence_frame_count += 1
                if self._silence_frame_count >= self._vad_silence_frames:
                    if self._speech_frame_count >= self._vad_min_speech_frames:
                        return True
                    self._speech_active = False
                    self._silence_frame_count = 0
                    self._speech_frame_count = 0
        return False

    def _extract_speech_chunk(self) -> Optional[str]:
        if not self._speech_active or self._speech_frame_count < self._vad_min_speech_frames:
            self._speech_active = False
            self._silence_frame_count = 0
            self._speech_frame_count = 0
            return None
        start_idx = max(0, self._speech_start_idx)
        end_idx = len(self._frames)
        if end_idx <= start_idx:
            self._speech_active = False
            self._silence_frame_count = 0
            self._speech_frame_count = 0
            return None
        speech_frames = self._frames[start_idx:end_idx]
        audio = np.concatenate(speech_frames, axis=0)
        self._frames = self._frames[end_idx:]
        self._speech_active = False
        self._silence_frame_count = 0
        self._speech_frame_count = 0
        audio_int16 = (audio * 32767).astype(np.int16)
        timestamp = time.strftime("%Y%d%m%H%M%S")
        wav_path = self._temp_dir / f"voice_vad_{timestamp}.wav"
        try:
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(self._channels)
                wf.setsampwidth(2)
                wf.setframerate(self._sample_rate)
                wf.writeframes(audio_int16.tobytes())
            return str(wav_path)
        except Exception as exc:
            logger.error("VAD chunk 保存失败: %s", exc)
            return None

    def start(self) -> None:
        """启动录音（非阻塞，立即返回）"""
        self._init_vad()
        self._running = True
        self._frames = []
        self._calibrated = False
        self._calibration_frames = []
        self._speech_active = False
        self._silence_frame_count = 0
        self._speech_frame_count = 0
        self._speech_start_idx = 0
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
                blocksize=self._blocksize,
                callback=self._audio_callback,
            ):
                while self._running:
                    time.sleep(0.05)
        except Exception as exc:
            # CallbackAbort 被 sd.InputStream 内部消化，不会到达此处
            self.error_occurred.emit(f"录音异常: {exc}")
            return
        finally:
            self._stream = None

        # stop() 设置了 _running=False 时，跳过 save_to_wav 和信号发射
        # （外部调用者已通过 extract_chunk() 保存了音频）
        if not self._running:
            return

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
        if not self._running:
            import sounddevice as sd
            raise sd.CallbackAbort()  # 立即停止 InputStream，避免音频流关闭延迟

        if self._vad_enabled:
            if not self._calibrated:
                self._calibrate_noise_floor(indata)
            else:
                if self._detect_speech_end(indata):
                    chunk_path = self._extract_speech_chunk()
                    if chunk_path:
                        self.speech_ended.emit(chunk_path)

        with self._lock:
            self._frames.append(indata.copy())
        volume = float(np.sqrt(np.mean(indata * indata)))
        self._latest_volume = volume
        self.volume_changed.emit(volume)

    def stop(self) -> None:
        """请求停止录音（立即关闭音频流）"""
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

    def get_buffer_rms(self) -> float:
        """获取当前缓冲区的 RMS 能量值，用于判断是否含有有效音频

        线程安全：内部有锁保护，可从任意线程调用。
        返回 0.0 表示缓冲区为空或能量为 0。
        """
        with self._lock:
            if not self._frames:
                return 0.0
            audio = np.concatenate(self._frames, axis=0)
            return float(np.sqrt(np.mean(audio * audio)))

    def get_vad_state(self) -> dict:
        """获取当前VAD检测状态（用于UI调试显示）"""
        return {
            "calibrated": self._calibrated,
            "noise_floor": self._noise_floor,
            "speech_active": self._speech_active,
            "silence_frames": self._silence_frame_count,
            "speech_frames": self._speech_frame_count,
            "buffer_frames": len(self._frames),
        }
