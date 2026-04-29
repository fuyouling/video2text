"""视频处理器"""

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Dict
from dataclasses import dataclass
from src.config.settings import Settings
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
        self.ffmpeg_path = ffmpeg_path
        self.supported_video_formats = [
            ext.lower()
            for ext in Settings().get_list(
                "preprocessing.supported_video_formats",
                default=[".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm"],
            )
        ]
        self._check_ffmpeg()

    def _check_ffmpeg(self) -> None:
        """检查FFmpeg是否可用，按 PATH / 给定路径 / 常见位置 依次查找"""
        resolved = shutil.which(self.ffmpeg_path)
        if not resolved:
            resolved = shutil.which("ffmpeg")
        if not resolved:
            common_paths = [
                Path.home() / "ffmpeg" / "bin" / "ffmpeg.exe",
                Path("C:/") / "ffmpeg" / "bin" / "ffmpeg.exe",
            ]
            for p in common_paths:
                if p.exists():
                    resolved = str(p)
                    break
        if not resolved:
            raise VideoFileError(
                "FFmpeg未找到。请安装FFmpeg并添加到系统PATH环境变量，"
                "或在config.ini的[preprocessing]节中设置ffmpeg_path为FFmpeg的完整路径。"
            )
        self.ffmpeg_path = resolved
        try:
            result = subprocess.run(
                [self.ffmpeg_path, "-version"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=CREATE_NO_WINDOW,
                encoding="utf-8",
                errors="ignore",
            )
            if result.returncode != 0:
                raise VideoFileError("FFmpeg不可用")
            logger.info("FFmpeg检查通过: %s", self.ffmpeg_path)
        except subprocess.TimeoutExpired:
            raise VideoFileError("FFmpeg检查超时")

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
                timeout=30,
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

        Args:
            video_path: 视频文件路径
            output_path: 输出音频文件路径
            sample_rate: 采样率
            channels: 声道数

        Returns:
            输出音频文件路径

        Raises:
            VideoFileError: 音频提取失败
        """
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

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
                raise VideoFileError(f"音频提取失败: {error_msg}")

            if not output_file.exists():
                raise VideoFileError("音频文件未生成")

            logger.info(f"音频提取成功: {output_file}")
            return str(output_file)

        except subprocess.TimeoutExpired:
            raise VideoFileError("音频提取超时")
        except Exception as e:
            raise VideoFileError(f"音频提取失败: {e}")

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
