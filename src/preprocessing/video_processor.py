"""音视频处理器"""

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from src.config.settings import Settings
from src.i18n import t
from src.preprocessing.ffmpeg import ensure_ffmpeg, ensure_ffprobe
from src.utils.exceptions import TranscriptionCancelledError, VideoFileError
from src.utils.logger import get_logger
from src.utils.subprocess_compat import CREATE_NO_WINDOW

logger = get_logger(__name__)


def _safe_float(val: Any, default: float = 0.0) -> float:
    """容错解析 float，安全处理 'N/A'、'null'、None 及溢出"""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        val_clean = val.strip()
        if not val_clean or val_clean.upper() in ("N/A", "NONE", "NULL"):
            return default
        try:
            return float(val_clean)
        except (ValueError, OverflowError):
            return default
    return default


def _safe_int(val: Any, default: int = 0) -> int:
    """容错解析 int，安全处理 'N/A'、'null'、None 及溢出"""
    if val is None:
        return default
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        try:
            return int(val)
        except (ValueError, OverflowError):
            return default
    if isinstance(val, str):
        val_clean = val.strip()
        if not val_clean or val_clean.upper() in ("N/A", "NONE", "NULL"):
            return default
        try:
            return int(float(val_clean))
        except (ValueError, OverflowError):
            return default
    return default


@dataclass
class VideoInfo:
    """音视频信息"""

    duration: float
    width: int
    height: int
    fps: float
    codec: str
    audio_codec: str
    audio_sample_rate: int
    has_audio: bool


class VideoProcessor:
    """音视频处理器"""

    def __init__(self):
        """初始化音视频处理器"""
        self.ffmpeg_path = ensure_ffmpeg()
        self.ffprobe_path = ensure_ffprobe()
        self.supported_video_formats = [
            ext.lower()
            for ext in Settings().get_list(
                "preprocessing.supported_video_formats",
                default=[".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm"],
            )
        ]
        self.supported_audio_formats = [
            ext.lower()
            for ext in Settings().get_list(
                "preprocessing.supported_audio_formats",
                default=[".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"],
            )
        ]
        self.supported_input_formats = (
            self.supported_video_formats + self.supported_audio_formats
        )

    def is_audio_file(self, file_path: str) -> bool:
        """判断文件是否为支持的音频格式"""
        return Path(file_path).suffix.lower() in self.supported_audio_formats

    def is_supported_input(self, file_path: str) -> bool:
        """判断文件是否为支持的音视频格式（视频或音频）"""
        return Path(file_path).suffix.lower() in self.supported_input_formats

    def validate_input(self, video_path: str) -> bool:
        """验证音视频文件

        Args:
            video_path: 音视频文件路径

        Returns:
            是否为有效的音视频文件

        Raises:
            VideoFileError: 音视频文件无效
        """
        path = Path(video_path)

        if not path.exists():
            raise VideoFileError(
                t("preprocessing.errors.file_not_found", path=video_path)
            )

        if not path.is_file():
            raise VideoFileError(
                t("preprocessing.errors.not_a_file", path=video_path)
            )

        if path.suffix.lower() not in self.supported_input_formats:
            raise VideoFileError(
                t(
                    "preprocessing.errors.unsupported_format",
                    suffix=path.suffix,
                    formats=", ".join(self.supported_input_formats),
                )
            )

        cmd = [
            self.ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=format_name",
            "-of",
            "csv=p=0",
            video_path,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=CREATE_NO_WINDOW,
                encoding="utf-8",
                errors="ignore",
            )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout
                raise VideoFileError(
                    t("preprocessing.errors.file_corrupt", error=error_msg)
                )

            logger.debug(
                t("preprocessing.log.validation_passed", name=path.name)
            )
            return True

        except subprocess.TimeoutExpired:
            raise VideoFileError(t("preprocessing.errors.timeout"))
        except VideoFileError:
            raise
        except Exception as e:
            raise VideoFileError(
                t("preprocessing.errors.validation_failed", error=e)
            )

    def get_video_info(self, video_path: str) -> VideoInfo:
        """获取音视频信息

        Args:
            video_path: 音视频文件路径

        Returns:
            音视频信息对象

        Raises:
            VideoFileError: 获取音视频信息失败
        """
        cmd = [
            self.ffprobe_path,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            video_path,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=CREATE_NO_WINDOW,
                encoding="utf-8",
                errors="ignore",
            )

            if result.returncode != 0:
                raise VideoFileError(
                    t(
                        "preprocessing.errors.info_failed",
                        error=result.stderr or result.stdout,
                    )
                )

            data = json.loads(result.stdout)

            video_stream = None
            audio_stream = None
            for stream in data.get("streams", []):
                codec_type = stream.get("codec_type", "")
                if codec_type == "video" and video_stream is None:
                    video_stream = stream
                elif codec_type == "audio" and audio_stream is None:
                    audio_stream = stream

            duration = _safe_float(data.get("format", {}).get("duration"), 0.0)

            width = _safe_int(video_stream.get("width"), 0) if video_stream else 0
            height = _safe_int(video_stream.get("height"), 0) if video_stream else 0
            codec = str(video_stream.get("codec_name") or "") if video_stream else ""

            fps = 0.0
            if video_stream:
                r_frame_rate = str(video_stream.get("r_frame_rate") or "0/1")
                try:
                    if "/" in r_frame_rate:
                        num_str, den_str = r_frame_rate.split("/", 1)
                        num = _safe_float(num_str, 0.0)
                        den = _safe_float(den_str, 1.0)
                        if den != 0.0:
                            fps = num / den
                    else:
                        fps = _safe_float(r_frame_rate, 0.0)
                except Exception:
                    logger.warning(
                        t("preprocessing.log.frame_rate_parse_failed"),
                        r_frame_rate,
                    )
                    fps = 0.0

            has_audio = audio_stream is not None
            audio_codec = str(audio_stream.get("codec_name") or "") if audio_stream else ""
            audio_sample_rate = (
                _safe_int(audio_stream.get("sample_rate"), 0) if audio_stream else 0
            )

            return VideoInfo(
                duration=duration,
                width=width,
                height=height,
                fps=fps,
                codec=codec,
                audio_codec=audio_codec,
                audio_sample_rate=audio_sample_rate,
                has_audio=has_audio,
            )

        except json.JSONDecodeError as e:
            raise VideoFileError(
                t("preprocessing.errors.parse_failed", error=e)
            )
        except VideoFileError:
            raise
        except Exception as e:
            raise VideoFileError(
                t("preprocessing.errors.info_failed", error=e)
            )

    def _run_ffmpeg_with_cancel(
        self,
        cmd: list,
        timeout: int,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> "subprocess.CompletedProcess[str]":
        """运行 ffmpeg 命令，支持用户取消。

        使用 Popen + communicate(timeout) 轮询：取消标志置位时立即终止
        子进程并抛 TranscriptionCancelledError（由转写服务层捕获后静默中止），
        避免用户点停止后 ffmpeg 仍在后台长时间运行、线程迟迟无法退出。
        """
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=CREATE_NO_WINDOW,
        )
        deadline = time.monotonic() + timeout
        try:
            while True:
                if cancel_check and cancel_check():
                    raise TranscriptionCancelledError(
                        "Audio extraction cancelled by user"
                    )
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(cmd, timeout)
                try:
                    stdout, stderr = proc.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except Exception:
            # 兜底：确保子进程被终止后原样抛出异常（kill 幂等）
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            raise
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

    def extract_audio(
        self,
        video_path: str,
        output_path: str,
        sample_rate: int = 16000,
        channels: int = 1,
        video_info: Optional[VideoInfo] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> str:
        """提取音频

        流程：
        1. 检查输入路径与输出路径防自覆盖
        2. 如果输入是音频文件，直接转换为 WAV（跳过视频信息探测）
        3. 检测视频是否包含音轨，无音轨则直接报错
        4. 优先使用 pcm_s16le 编码提取（无损 WAV）
        5. 若失败，自动回退为 mp3 编码再转 WAV
        6. 中断或失败时强制清理磁盘损坏/残留/0字节产物

        Args:
            video_path: 音视频文件路径
            output_path: 输出音频文件路径
            sample_rate: 采样率
            channels: 声道数
            video_info: 可选的已缓存 VideoInfo，避免重复调用 ffprobe
            cancel_check: 取消检查回调

        Returns:
            输出音频文件路径

        Raises:
            VideoFileError: 音频提取失败或视频无音轨
        """
        input_file = Path(video_path).resolve()
        output_file = Path(output_path).resolve()

        if input_file == output_file:
            raise VideoFileError(
                f"输入音视频文件路径与输出音频文件路径不能相同: {video_path}"
            )

        output_file.parent.mkdir(parents=True, exist_ok=True)

        if not self.is_audio_file(video_path):
            if video_info is None:
                video_info = self.get_video_info(video_path)
            if not video_info.has_audio:
                raise VideoFileError(
                    t("preprocessing.errors.no_audio", path=video_path)
                )

        label = (
            t("preprocessing.log.convert_audio")
            if self.is_audio_file(video_path)
            else t("preprocessing.log.extract_audio")
        )
        logger.debug(
            t("preprocessing.log.extract_start", label=label, name=Path(video_path).name)
        )

        try:
            res_path = self._run_ffmpeg_pcm_extract(
                video_path, str(output_file), sample_rate, channels, label,
                cancel_check=cancel_check,
            )
            if not output_file.exists() or output_file.stat().st_size == 0:
                if output_file.exists():
                    output_file.unlink(missing_ok=True)
                raise VideoFileError(
                    t("preprocessing.errors.extract_failed", label=label, error="提取产物为空或0字节损坏")
                )
            return res_path
        except (TranscriptionCancelledError, Exception):
            if output_file.exists():
                try:
                    output_file.unlink(missing_ok=True)
                except Exception as clean_err:
                    logger.debug("Failed to clean up output file %s: %s", output_file, clean_err)
            raise

    def _run_ffmpeg_pcm_extract(
        self,
        input_path: str,
        output_path: str,
        sample_rate: int,
        channels: int,
        label: str,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> str:
        """使用 pcm_s16le 编码提取/转换音频，失败时自动回退。"""
        output_file = Path(output_path)
        cmd = [
            self.ffmpeg_path,
            "-i",
            input_path,
            "-vn",
            "-map",
            "0:a:0?",
            "-sn",
            "-dn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "-y",
            str(output_file),
        ]
        logger.debug(
            t("preprocessing.log.ffmpeg_command"), " ".join(cmd)
        )

        try:
            result = self._run_ffmpeg_with_cancel(cmd, timeout=3600, cancel_check=cancel_check)

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout
                logger.warning(t("preprocessing.log.fallback_pcm_failed"))
                if output_file.exists():
                    output_file.unlink(missing_ok=True)
                return self._extract_audio_fallback(
                    input_path, str(output_file), sample_rate, channels,
                    cancel_check=cancel_check,
                )

            if not output_file.exists() or output_file.stat().st_size == 0:
                logger.warning(t("preprocessing.log.fallback_audio_not_generated"))
                if output_file.exists():
                    output_file.unlink(missing_ok=True)
                return self._extract_audio_fallback(
                    input_path, str(output_file), sample_rate, channels,
                    cancel_check=cancel_check,
                )

            logger.debug(
                t("preprocessing.log.extract_success", label=label, name=output_file.name)
            )
            return str(output_file)

        except subprocess.TimeoutExpired:
            if output_file.exists():
                output_file.unlink(missing_ok=True)
            raise VideoFileError(
                t("preprocessing.errors.extract_timeout", label=label)
            )
        except TranscriptionCancelledError:
            if output_file.exists():
                output_file.unlink(missing_ok=True)
            raise
        except VideoFileError:
            raise
        except Exception as e:
            logger.warning(
                t("preprocessing.log.fallback_exception", label=label, error=e)
            )
            if output_file.exists():
                output_file.unlink(missing_ok=True)
            try:
                return self._extract_audio_fallback(
                    input_path, str(output_file), sample_rate, channels,
                    cancel_check=cancel_check,
                )
            except VideoFileError as fallback_err:
                raise VideoFileError(
                    t("preprocessing.errors.extract_failed", label=label, error=e)
                ) from fallback_err

    def _extract_audio_fallback(
        self,
        video_path: str,
        output_path: str,
        sample_rate: int = 16000,
        channels: int = 1,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> str:
        """音频提取回退方案：先用 mp3 编码提取，再转为 WAV。

        用于 pcm_s16le 直接提取失败的情况（如某些编码不支持的容器格式）。
        临时 MP3 文件使用独立临时目录加唯一随机文件名，杜绝同名冲突与自读自写自删。
        """
        output_file = Path(output_path)
        stem = Path(video_path).stem
        pid = os.getpid()
        uid = uuid.uuid4().hex[:8]
        temp_mp3 = Path(tempfile.gettempdir()) / f"v2t_fallback_{stem}_{pid}_{uid}.mp3"

        cmd = [
            self.ffmpeg_path,
            "-i",
            video_path,
            "-vn",
            "-map",
            "0:a:0?",
            "-sn",
            "-dn",
            "-acodec",
            "libmp3lame",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "-q:a",
            "2",
            "-y",
            str(temp_mp3),
        ]

        logger.info(t("preprocessing.log.fallback_pcm_failed"))
        logger.debug(
            t("preprocessing.log.ffmpeg_command"), " ".join(cmd)
        )

        try:
            result = self._run_ffmpeg_with_cancel(cmd, timeout=3600, cancel_check=cancel_check)

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout
                raise VideoFileError(
                    t("preprocessing.errors.fallback_also_failed", error=error_msg)
                )

            if not temp_mp3.exists() or temp_mp3.stat().st_size == 0:
                raise VideoFileError(
                    t("preprocessing.errors.mp3_not_generated")
                )

            convert_cmd = [
                self.ffmpeg_path,
                "-i",
                str(temp_mp3),
                "-acodec",
                "pcm_s16le",
                "-ar",
                str(sample_rate),
                "-ac",
                str(channels),
                "-y",
                str(output_file),
            ]
            convert_result = self._run_ffmpeg_with_cancel(convert_cmd, timeout=600, cancel_check=cancel_check)

            if convert_result.returncode != 0:
                raise VideoFileError(
                    t(
                        "preprocessing.errors.mp3_to_wav_failed",
                        error=convert_result.stderr,
                    )
                )

            if not output_file.exists() or output_file.stat().st_size == 0:
                raise VideoFileError(
                    t("preprocessing.errors.fallback_also_failed", error="Converted WAV file is empty or missing")
                )

            logger.info(
                t("preprocessing.log.fallback_success", name=output_file.name)
            )
            return str(output_file)

        except VideoFileError:
            if output_file.exists():
                output_file.unlink(missing_ok=True)
            raise
        except TranscriptionCancelledError:
            if output_file.exists():
                output_file.unlink(missing_ok=True)
            raise
        except Exception as e:
            if output_file.exists():
                output_file.unlink(missing_ok=True)
            raise VideoFileError(
                t("preprocessing.errors.fallback_failed", error=e)
            )
        finally:
            temp_mp3.unlink(missing_ok=True)

    def get_thumbnail(
        self, video_path: str, output_path: str, timestamp: str = "00:00:01"
    ) -> str:
        """获取音视频缩略图

        Args:
            video_path: 音视频文件路径
            output_path: 输出图片路径
            timestamp: 时间戳

        Returns:
            输出图片路径

        Raises:
            VideoFileError: 音频文件不支持生成缩略图
        """
        if self.is_audio_file(video_path):
            raise VideoFileError(
                t("preprocessing.errors.audio_thumbnail_unsupported")
            )
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            parts = timestamp.split(":")
            ts_seconds = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        except (ValueError, IndexError):
            raise VideoFileError(
                t(
                    "preprocessing.errors.invalid_timestamp",
                    timestamp=timestamp,
                )
            )

        video_info = self.get_video_info(video_path)
        if video_info.duration > 0 and ts_seconds > video_info.duration:
            logger.warning(
                t("preprocessing.log.timestamp_exceeds_duration"),
                ts_seconds,
                video_info.duration,
            )
            timestamp = "00:00:00"

        cmd = [
            self.ffmpeg_path,
            "-i",
            video_path,
            "-ss",
            timestamp,
            "-vframes",
            "1",
            "-y",
            str(output_file),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=CREATE_NO_WINDOW,
                encoding="utf-8",
                errors="ignore",
            )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout
                raise VideoFileError(
                    t("preprocessing.errors.thumbnail_failed", error=error_msg)
                )

            logger.info(
                t("preprocessing.log.thumbnail_success", name=output_file.name)
            )
            return str(output_file)

        except VideoFileError:
            raise
        except Exception as e:
            raise VideoFileError(
                t("preprocessing.errors.thumbnail_failed", error=e)
            )
