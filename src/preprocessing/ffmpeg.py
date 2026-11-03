"""FFmpeg 路径管理 —— 使用项目内置的 ffmpeg，支持系统 PATH 自动回退探测"""

import shutil
import sys
from pathlib import Path
from typing import Optional

from src.i18n import t
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
    if _ffmpeg_path is not None and Path(_ffmpeg_path).is_file():
        return _ffmpeg_path

    name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    path = _get_base_dir() / "ffmpeg" / "bin" / name
    if path.is_file():
        _ffmpeg_path = str(path)
        logger.debug("FFmpeg (builtin): %s", _ffmpeg_path)
        return _ffmpeg_path

    # 回退探测系统 PATH 中的 ffmpeg
    sys_ffmpeg = shutil.which(name) or shutil.which("ffmpeg")
    if sys_ffmpeg:
        sys_path = Path(sys_ffmpeg).resolve()
        if sys_path.is_file():
            _ffmpeg_path = str(sys_path)
            logger.info("FFmpeg (system PATH): %s", _ffmpeg_path)
            return _ffmpeg_path

    raise VideoFileError(
        t("errors.ffmpeg_not_found", path=path)
    )


def ensure_ffprobe() -> str:
    global _ffprobe_path
    if _ffprobe_path is not None and Path(_ffprobe_path).is_file():
        return _ffprobe_path

    name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    path = _get_base_dir() / "ffmpeg" / "bin" / name
    if path.is_file():
        _ffprobe_path = str(path)
        logger.debug("ffprobe (builtin): %s", _ffprobe_path)
        return _ffprobe_path

    # 回退探测系统 PATH 中的 ffprobe
    sys_ffprobe = shutil.which(name) or shutil.which("ffprobe")
    if sys_ffprobe:
        sys_path = Path(sys_ffprobe).resolve()
        if sys_path.is_file():
            _ffprobe_path = str(sys_path)
            logger.info("ffprobe (system PATH): %s", _ffprobe_path)
            return _ffprobe_path

    raise VideoFileError(
        t("errors.ffprobe_not_found", path=path)
    )
