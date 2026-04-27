"""音频提取器"""

import subprocess
import sys
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from src.utils.exceptions import VideoFileError
from src.utils.logger import get_logger

logger = get_logger(__name__)

if sys.platform == "win32":
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    CREATE_NO_WINDOW = 0


@dataclass
class AudioInfo:
    """音频信息"""

    duration: float
    sample_rate: int
    channels: int
    codec: str


class AudioExtractor:
    """音频提取器"""

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        """初始化音频提取器

        Args:
            ffmpeg_path: FFmpeg可执行文件路径
        """
        self.ffmpeg_path = ffmpeg_path
        self._check_ffmpeg()

    def _check_ffmpeg(self) -> None:
        """检查FFmpeg是否可用"""
        try:
            result = subprocess.run(
                [self.ffmpeg_path, "-version"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                raise VideoFileError("FFmpeg不可用")
            logger.info("FFmpeg检查通过")
        except FileNotFoundError:
            raise VideoFileError(f"FFmpeg未找到: {self.ffmpeg_path}")
        except subprocess.TimeoutExpired:
            raise VideoFileError("FFmpeg检查超时")

    def extract_audio(
        self,
        video_path: str,
        output_path: str,
        sample_rate: int = 16000,
        channels: int = 1,
        codec: str = "pcm_s16le",
    ) -> str:
        """从视频中提取音频

        Args:
            video_path: 视频文件路径
            output_path: 输出音频文件路径
            sample_rate: 采样率
            channels: 声道数
            codec: 音频编码

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
            codec,
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

    def get_audio_info(self, audio_path: str) -> AudioInfo:
        """获取音频信息

        Args:
            audio_path: 音频文件路径

        Returns:
            音频信息对象

        Raises:
            VideoFileError: 获取音频信息失败
        """
        cmd = [self.ffmpeg_path, "-i", audio_path, "-f", "null", "-"]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=CREATE_NO_WINDOW,
            )

            stderr = result.stderr

            duration = 0.0
            sample_rate = 16000
            channels = 1
            codec = "pcm_s16le"

            for line in stderr.split("\n"):
                if "Duration:" in line:
                    time_str = line.split("Duration:")[1].split(",")[0].strip()
                    h, m, s = time_str.split(":")
                    duration = float(h) * 3600 + float(m) * 60 + float(s)
                if "Sample Rate" in line or "Hz" in line:
                    for part in line.split(","):
                        if "Hz" in part:
                            try:
                                sample_rate = int(part.strip().split()[0])
                            except (ValueError, IndexError):
                                pass
                if "Audio:" in line:
                    for part in line.split(","):
                        if "stereo" in part:
                            channels = 2
                        elif "mono" in part:
                            channels = 1
                        if "pcm" in part.lower():
                            codec = part.strip()

            return AudioInfo(
                duration=duration,
                sample_rate=sample_rate,
                channels=channels,
                codec=codec,
            )

        except Exception as e:
            raise VideoFileError(f"获取音频信息失败: {e}")

    def convert_audio(
        self,
        input_path: str,
        output_path: str,
        sample_rate: int = 16000,
        channels: int = 1,
        codec: str = "pcm_s16le",
    ) -> str:
        """转换音频格式

        Args:
            input_path: 输入音频文件路径
            output_path: 输出音频文件路径
            sample_rate: 采样率
            channels: 声道数
            codec: 音频编码

        Returns:
            输出音频文件路径
        """
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.ffmpeg_path,
            "-i",
            input_path,
            "-acodec",
            codec,
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "-y",
            str(output_file),
        ]

        logger.info(f"开始转换音频: {input_path}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,
                creationflags=CREATE_NO_WINDOW,
            )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout
                raise VideoFileError(f"音频转换失败: {error_msg}")

            logger.info(f"音频转换成功: {output_file}")
            return str(output_file)

        except subprocess.TimeoutExpired:
            raise VideoFileError("音频转换超时")
        except Exception as e:
            raise VideoFileError(f"音频转换失败: {e}")
