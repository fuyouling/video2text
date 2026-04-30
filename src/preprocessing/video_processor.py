"""视频处理器"""

import subprocess
import sys
from pathlib import Path
from typing import Optional, Dict
from dataclasses import dataclass
from src.config.settings import Settings
from src.preprocessing.ffmpeg import ensure_ffmpeg
from src.utils.exceptions import VideoFileError
from src.utils.logger import get_logger

logger = get_logger(__name__)

if sys.platform == "win32":
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    CREATE_NO_WINDOW = 0


@dataclass
class VideoInfo:
    """视频信息"""

    duration: float
    width: int
    height: int
    fps: float
    codec: str
    audio_codec: str
    audio_sample_rate: int
    has_audio: bool


class VideoProcessor:
    """视频处理器"""

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        """初始化视频处理器

        Args:
            ffmpeg_path: FFmpeg可执行文件路径
        """
        self.ffmpeg_path = ensure_ffmpeg(ffmpeg_path)
        self.supported_video_formats = [
            ext.lower()
            for ext in Settings().get_list(
                "preprocessing.supported_video_formats",
                default=[".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm"],
            )
        ]

    def validate_video(self, video_path: str) -> bool:
        """验证视频文件

        Args:
            video_path: 视频文件路径

        Returns:
            是否为有效的视频文件

        Raises:
            VideoFileError: 视频文件无效
        """
        path = Path(video_path)

        if not path.exists():
            raise VideoFileError(f"视频文件不存在: {video_path}")

        if not path.is_file():
            raise VideoFileError(f"路径不是文件: {video_path}")

        if path.suffix.lower() not in self.supported_video_formats:
            raise VideoFileError(
                f"不支持的视频格式: {path.suffix}. "
                f"支持的格式: {', '.join(self.supported_video_formats)}"
            )

        cmd = [self.ffmpeg_path, "-v", "error", "-i", video_path, "-f", "null", "-"]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                creationflags=CREATE_NO_WINDOW,
                encoding="utf-8",
                errors="ignore",
            )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout
                raise VideoFileError(f"视频文件损坏: {error_msg}")

            logger.info(f"视频文件验证通过: {video_path}")
            return True

        except subprocess.TimeoutExpired:
            raise VideoFileError("视频验证超时")
        except Exception as e:
            raise VideoFileError(f"视频验证失败: {e}")

    def get_video_info(self, video_path: str) -> VideoInfo:
        """获取视频信息

        Args:
            video_path: 视频文件路径

        Returns:
            视频信息对象

        Raises:
            VideoFileError: 获取视频信息失败
        """
        cmd = [self.ffmpeg_path, "-i", video_path, "-f", "null", "-"]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                errors="ignore",
                creationflags=CREATE_NO_WINDOW,
            )

            stderr = result.stderr or ""

            duration = 0.0
            width = 0
            height = 0
            fps = 0.0
            codec = ""
            audio_codec = ""
            audio_sample_rate = 0
            has_audio = False

            for line in stderr.split("\n"):
                if "Duration:" in line:
                    time_str = line.split("Duration:")[1].split(",")[0].strip()
                    h, m, s = time_str.split(":")
                    duration = float(h) * 3600 + float(m) * 60 + float(s)
                if "Video:" in line:
                    for part in line.split(","):
                        if "x" in part and "fps" not in part:
                            try:
                                resolution = part.strip().split()[0]
                                width, height = map(int, resolution.split("x"))
                            except (ValueError, IndexError):
                                pass
                        if "fps" in part:
                            try:
                                fps_str = part.strip().split()[0]
                                fps = float(fps_str)
                            except (ValueError, IndexError):
                                pass
                        if "Video:" in part:
                            try:
                                codec = part.split("Video:")[1].strip().split()[0]
                            except (ValueError, IndexError):
                                pass
                if "Audio:" in line:
                    has_audio = True
                    for part in line.split(","):
                        if "Hz" in part:
                            try:
                                audio_sample_rate = int(part.strip().split()[0])
                            except (ValueError, IndexError):
                                pass
                        if "Audio:" in part:
                            try:
                                audio_codec = part.split("Audio:")[1].strip().split()[0]
                            except (ValueError, IndexError):
                                pass

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

        except Exception as e:
            raise VideoFileError(f"获取视频信息失败: {e}")

    def extract_audio(
        self,
        video_path: str,
        output_path: str,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> str:
        """提取音频

        流程：
        1. 检测视频是否包含音轨，无音轨则直接报错
        2. 优先使用 pcm_s16le 编码提取（无损 WAV）
        3. 若失败，自动回退为 mp3lib 编码再转 WAV

        Args:
            video_path: 视频文件路径
            output_path: 输出音频文件路径
            sample_rate: 采样率
            channels: 声道数

        Returns:
            输出音频文件路径

        Raises:
            VideoFileError: 音频提取失败或视频无音轨
        """
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        video_info = self.get_video_info(video_path)
        if not video_info.has_audio:
            raise VideoFileError(f"视频文件没有音轨，无法提取音频: {video_path}")

        cmd = [
            self.ffmpeg_path,
            "-i",
            video_path,
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

        logger.info(f"开始提取音频: {video_path}")
        logger.debug(f"FFmpeg命令: {' '.join(cmd)}")

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
                    "pcm_s16le 提取失败，尝试自动转码回退: %s", error_msg[:200]
                )
                return self._extract_audio_fallback(
                    video_path, str(output_file), sample_rate, channels
                )

            if not output_file.exists():
                logger.warning("音频文件未生成，尝试自动转码回退")
                return self._extract_audio_fallback(
                    video_path, str(output_file), sample_rate, channels
                )

            logger.info(f"音频提取成功: {output_file}")
            return str(output_file)

        except subprocess.TimeoutExpired:
            raise VideoFileError("音频提取超时")
        except VideoFileError:
            raise
        except Exception as e:
            logger.warning("音频提取异常，尝试自动转码回退: %s", e)
            try:
                return self._extract_audio_fallback(
                    video_path, str(output_file), sample_rate, channels
                )
            except VideoFileError:
                raise VideoFileError(f"音频提取失败（含回退）: {e}")

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
        logger.debug(f"FFmpeg命令: {' '.join(cmd)}")

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

            logger.info(f"回退方案提取成功: {output_file}")
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
        """获取视频缩略图

        Args:
            video_path: 视频文件路径
            output_path: 输出图片路径
            timestamp: 时间戳

        Returns:
            输出图片路径
        """
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

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

            logger.info(f"缩略图生成成功: {output_file}")
            return str(output_file)

        except Exception as e:
            raise VideoFileError(f"缩略图生成失败: {e}")
