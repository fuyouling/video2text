"""媒体处理器"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from src.config.settings import Settings
from src.preprocessing.ffmpeg import ensure_ffmpeg, ensure_ffprobe
from src.utils.exceptions import VideoFileError
from src.utils.logger import get_logger
from src.utils.subprocess_compat import CREATE_NO_WINDOW

logger = get_logger(__name__)


@dataclass
class VideoInfo:
    """媒体信息"""

    duration: float
    width: int
    height: int
    fps: float
    codec: str
    audio_codec: str
    audio_sample_rate: int
    has_audio: bool


class VideoProcessor:
    """媒体处理器"""

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        """初始化媒体处理器

        Args:
            ffmpeg_path: FFmpeg可执行文件路径
        """
        self.ffmpeg_path = ensure_ffmpeg(ffmpeg_path)
        self.ffprobe_path = ensure_ffprobe(ffmpeg_path)
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
        self.supported_media_formats = (
            self.supported_video_formats + self.supported_audio_formats
        )

    def is_audio_file(self, file_path: str) -> bool:
        """判断文件是否为支持的音频格式"""
        return Path(file_path).suffix.lower() in self.supported_audio_formats

    def is_supported_media(self, file_path: str) -> bool:
        """判断文件是否为支持的媒体格式（视频或音频）"""
        return Path(file_path).suffix.lower() in self.supported_media_formats

    def validate_media(self, video_path: str) -> bool:
        """验证媒体文件

        Args:
            video_path: 媒体文件路径

        Returns:
            是否为有效的媒体文件

        Raises:
            VideoFileError: 媒体文件无效
        """
        path = Path(video_path)

        if not path.exists():
            raise VideoFileError(f"媒体文件不存在: {video_path}")

        if not path.is_file():
            raise VideoFileError(f"路径不是文件: {video_path}")

        if path.suffix.lower() not in self.supported_media_formats:
            raise VideoFileError(
                f"不支持的媒体格式: {path.suffix}. "
                f"支持的格式: {', '.join(self.supported_media_formats)}"
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
                raise VideoFileError(f"媒体文件损坏: {error_msg}")

            logger.debug("媒体文件验证通过: %s", video_path)
            return True

        except subprocess.TimeoutExpired:
            raise VideoFileError("媒体验证超时")
        except VideoFileError:
            raise
        except Exception as e:
            raise VideoFileError(f"媒体验证失败: {e}")

    def get_video_info(self, video_path: str) -> VideoInfo:
        """获取媒体信息

        Args:
            video_path: 媒体文件路径

        Returns:
            媒体信息对象

        Raises:
            VideoFileError: 获取媒体信息失败
        """
        cmd = [
            self.ffprobe_path,
            "-v",
            "quiet",
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
                timeout=30,
                creationflags=CREATE_NO_WINDOW,
                encoding="utf-8",
                errors="ignore",
            )

            if result.returncode != 0:
                raise VideoFileError(
                    f"获取媒体信息失败: {result.stderr or result.stdout}"
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

            duration = float(data.get("format", {}).get("duration") or 0)

            width = int(video_stream.get("width", 0)) if video_stream else 0
            height = int(video_stream.get("height", 0)) if video_stream else 0
            codec = video_stream.get("codec_name", "") if video_stream else ""

            fps = 0.0
            if video_stream:
                r_frame_rate = video_stream.get("r_frame_rate", "0/1")
                try:
                    if "/" in r_frame_rate:
                        num, den = r_frame_rate.split("/")
                        if int(den) != 0:
                            fps = int(num) / int(den)
                    else:
                        fps = float(r_frame_rate)
                except (ValueError, ZeroDivisionError):
                    logger.warning("无法解析帧率: %s，使用默认值 0", r_frame_rate)
                    fps = 0.0

            has_audio = audio_stream is not None
            audio_codec = audio_stream.get("codec_name", "") if audio_stream else ""
            audio_sample_rate = (
                int(audio_stream.get("sample_rate", 0)) if audio_stream else 0
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
            raise VideoFileError(f"解析媒体信息失败: {e}")
        except VideoFileError:
            raise
        except Exception as e:
            raise VideoFileError(f"获取媒体信息失败: {e}")

    def extract_audio(
        self,
        video_path: str,
        output_path: str,
        sample_rate: int = 16000,
        channels: int = 1,
        video_info: Optional[VideoInfo] = None,
    ) -> str:
        """提取音频

        流程：
        1. 如果输入是音频文件，直接转换为 WAV（跳过视频信息探测）
        2. 检测视频是否包含音轨，无音轨则直接报错
        3. 优先使用 pcm_s16le 编码提取（无损 WAV）
        4. 若失败，自动回退为 mp3lib 编码再转 WAV

        Args:
            video_path: 媒体文件路径
            output_path: 输出音频文件路径
            sample_rate: 采样率
            channels: 声道数
            video_info: 可选的已缓存 VideoInfo，避免重复调用 ffprobe

        Returns:
            输出音频文件路径

        Raises:
            VideoFileError: 音频提取失败或视频无音轨
        """
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        if not self.is_audio_file(video_path):
            if video_info is None:
                video_info = self.get_video_info(video_path)
            if not video_info.has_audio:
                raise VideoFileError(f"媒体文件没有音轨，无法提取音频: {video_path}")

        label = "转换音频" if self.is_audio_file(video_path) else "提取音频"
        logger.debug("开始%s: %s", label, video_path)

        return self._run_ffmpeg_pcm_extract(
            video_path, str(output_file), sample_rate, channels, label
        )

    def _run_ffmpeg_pcm_extract(
        self,
        input_path: str,
        output_path: str,
        sample_rate: int,
        channels: int,
        label: str,
    ) -> str:
        """使用 pcm_s16le 编码提取/转换音频，失败时自动回退。"""
        output_file = Path(output_path)
        cmd = [
            self.ffmpeg_path,
            "-i",
            input_path,
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "-y",
            str(output_file),
        ]
        logger.debug("FFmpeg命令: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,
                creationflags=CREATE_NO_WINDOW,
                encoding="utf-8",
                errors="ignore",
            )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout
                logger.warning(
                    "pcm_s16le %s失败，尝试自动转码回退: %s", label, error_msg[:200]
                )
                return self._extract_audio_fallback(
                    input_path, str(output_file), sample_rate, channels
                )

            if not output_file.exists():
                logger.warning("音频文件未生成，尝试自动转码回退")
                return self._extract_audio_fallback(
                    input_path, str(output_file), sample_rate, channels
                )

            logger.debug("音频%s成功: %s", label, output_file)
            return str(output_file)

        except subprocess.TimeoutExpired:
            raise VideoFileError(f"音频{label}超时")
        except VideoFileError:
            raise
        except Exception as e:
            logger.warning("音频%s异常，尝试自动转码回退: %s", label, e)
            try:
                return self._extract_audio_fallback(
                    input_path, str(output_file), sample_rate, channels
                )
            except VideoFileError as fallback_err:
                raise VideoFileError(
                    f"音频{label}失败（含回退）: {e}"
                ) from fallback_err

    def _extract_audio_fallback(
        self,
        video_path: str,
        output_path: str,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> str:
        """音频提取回退方案：先用 mp3 编码提取，再转为 WAV。

        用于 pcm_s16le 直接提取失败的情况（如某些编码不支持的容器格式）。
        """
        output_file = Path(output_path)
        temp_mp3 = output_file.with_suffix(".mp3")

        cmd = [
            self.ffmpeg_path,
            "-i",
            video_path,
            "-vn",
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

        logger.info("回退方案: 使用 libmp3lame 提取音频")
        logger.debug("FFmpeg命令: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,
                creationflags=CREATE_NO_WINDOW,
                encoding="utf-8",
                errors="ignore",
            )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout
                raise VideoFileError(f"音频提取回退方案也失败: {error_msg}")

            if not temp_mp3.exists():
                raise VideoFileError("回退方案: MP3 文件未生成")

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
            convert_result = subprocess.run(
                convert_cmd,
                capture_output=True,
                text=True,
                timeout=600,
                creationflags=CREATE_NO_WINDOW,
                encoding="utf-8",
                errors="ignore",
            )

            if convert_result.returncode != 0:
                raise VideoFileError(f"MP3 转 WAV 失败: {convert_result.stderr}")

            logger.info("回退方案提取成功: %s", output_file)
            return str(output_file)

        except VideoFileError:
            raise
        except Exception as e:
            raise VideoFileError(f"音频提取回退方案失败: {e}")
        finally:
            temp_mp3.unlink(missing_ok=True)

    def get_thumbnail(
        self, video_path: str, output_path: str, timestamp: str = "00:00:01"
    ) -> str:
        """获取媒体缩略图

        Args:
            video_path: 媒体文件路径
            output_path: 输出图片路径
            timestamp: 时间戳

        Returns:
            输出图片路径

        Raises:
            VideoFileError: 音频文件不支持生成缩略图
        """
        if self.is_audio_file(video_path):
            raise VideoFileError("不支持对音频文件生成缩略图")
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            parts = timestamp.split(":")
            ts_seconds = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        except (ValueError, IndexError):
            raise VideoFileError(f"无效的时间戳格式: {timestamp}，应为 HH:MM:SS")

        video_info = self.get_video_info(video_path)
        if video_info.duration > 0 and ts_seconds > video_info.duration:
            logger.warning(
                "时间戳 %s (%.1fs) 超过媒体时长 %.1fs，使用第一帧",
                timestamp,
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
                raise VideoFileError(f"缩略图生成失败: {error_msg}")

            logger.info("缩略图生成成功: %s", output_file)
            return str(output_file)

        except VideoFileError:
            raise
        except Exception as e:
            raise VideoFileError(f"缩略图生成失败: {e}")
