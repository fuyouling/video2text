"""FFmpeg 路径管理 —— 使用项目内置的 ffmpeg"""

import sys
from pathlib import Path
from typing import Optional

from src.utils.exceptions import VideoFileError
from src.utils.logger import get_logger

logger = get_logger(__name__)

_ffmpeg_path: Optional[str] = None
_ffprobe_path: Optional[str] = None


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent


def ensure_ffmpeg() -> str:
    global _ffmpeg_path
    if _ffmpeg_path is not None:
        return _ffmpeg_path
    name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    path = _get_base_dir() / "ffmpeg" / "bin" / name
    if not path.exists():
        raise VideoFileError(
            f"FFmpeg未找到: {path}\n"
            "请确保程序目录下的 ffmpeg/bin/ 目录存在且包含 ffmpeg.exe。"
        )
    _ffmpeg_path = str(path)
    logger.debug("FFmpeg: %s", _ffmpeg_path)
    return _ffmpeg_path


def ensure_ffprobe() -> str:
    global _ffprobe_path
    if _ffprobe_path is not None:
        return _ffprobe_path
    name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    path = _get_base_dir() / "ffmpeg" / "bin" / name
    if not path.exists():
        raise VideoFileError(
            f"ffprobe未找到: {path}\n"
            "请确保程序目录下的 ffmpeg/bin/ 目录存在且包含 ffprobe.exe。"
        )
    _ffprobe_path = str(path)
    logger.debug("ffprobe: %s", _ffprobe_path)
    return _ffprobe_path
